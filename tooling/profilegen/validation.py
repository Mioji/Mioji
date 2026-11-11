# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Strict readers for JSON documents that come from outside the code.

Configuration files, locale catalogues, the snapshot and GitHub API
responses all pass through this module before any other code sees them.
Centralising the checks gives the rest of the package three guarantees:

* values have exactly the type their annotation promises. In particular
  ``bool`` is never accepted where ``int`` is expected, which a plain
  ``isinstance`` check would allow because ``bool`` subclasses ``int``;
* objects carry no unknown keys, so a typo in hand-edited JSON is an
  immediate error rather than a silently ignored setting;
* every error names the source and the JSON path of the offending
  value, for example ``configs/profile.json: catalog.projects[1].status``.

The JSON decoder is stricter than :func:`json.loads` defaults as well:
duplicate keys and the non-standard ``NaN`` and ``Infinity`` literals
are rejected, because both silently change meaning between parsers.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Collection, Mapping
from pathlib import Path
from string import Formatter
from typing import Final, NoReturn

from profilegen.errors import ProfileError

_DISALLOWED_CATEGORIES: Final = frozenset(("Cc", "Zl", "Zp"))
# Non-spacing and enclosing combining marks overlap the base glyph without
# advancing the cursor; treating them as zero-width avoids over-counting NFD
# strings and prevents line wraps from detaching a mark from its base character.
_COMBINING_CATEGORIES: Final = frozenset(("Mn", "Me"))
# Bidi overrides and isolates (CVE-2021-42574) can silently reorder rendered
# text in SVGs and Markdown; disallow them alongside control characters.
_BIDI_OVERRIDES: Final = frozenset(
    ("\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069")
)


def char_units(char: str) -> int:
    """Return the estimated display width of ``char`` in half-width columns.

    Args:
        char: Single Unicode code point.

    Returns:
        ``0`` for combining marks, ``2`` for East Asian wide or full-width
        characters and ``1`` for all other characters.
    """
    if unicodedata.category(char) in _COMBINING_CATEGORIES:
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def display_units(text: str) -> int:
    """Return the estimated display width of ``text`` in half-width columns."""
    return sum(char_units(char) for char in text)


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-standard number literal {name}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def parse_json(text: str, *, source: str, error: type[ProfileError]) -> object:
    """Decode a JSON document with duplicate-key and literal checks.

    Args:
        text: Document text.
        source: Human-readable origin used as the message prefix.
        error: Exception class to raise, which lets the caller classify
            the failure as configuration, snapshot or network related.

    Returns:
        The decoded value.

    Raises:
        ProfileError: An instance of ``error`` if the text is not valid
            JSON or uses a construct rejected by this decoder.
    """
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise error(
            f"{source}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except RecursionError as exc:
        raise error(f"{source}: invalid JSON: nesting depth exceeded") from exc
    except ValueError as exc:
        raise error(f"{source}: invalid JSON: {exc}") from exc


def load_json(path: Path, *, source: str, error: type[ProfileError]) -> object:
    """Read a UTF-8 file and decode it with :func:`parse_json`.

    Args:
        path: File to read.
        source: Repository-relative name used in error messages.
        error: Exception class to raise on any failure.

    Returns:
        The decoded value.

    Raises:
        ProfileError: An instance of ``error`` if the file is missing,
            unreadable, not UTF-8 or not valid JSON.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise error(f"{source}: file not found") from exc
    except (OSError, UnicodeError) as exc:
        raise error(f"{source}: cannot read file: {exc}") from exc
    return parse_json(text, source=source, error=error)


def child(where: str, key: str | int) -> str:
    """Return the JSON path of ``key`` inside the value at ``where``.

    Args:
        where: Path of the containing value; empty for the document root.
        key: Object key or array index.

    Returns:
        A dotted path such as ``catalog.projects[0]``.
    """
    if isinstance(key, int):
        return f"{where}[{key}]"
    return f"{where}.{key}" if where else key


class Validator:
    """Type-checked accessors that report failures with a JSON path.

    One instance is bound to one document. Methods take the raw value and
    its path, and either return the value narrowed to the expected type
    or raise the configured exception class.
    """

    __slots__ = ("_error", "_source")

    def __init__(self, source: str, error: type[ProfileError]) -> None:
        """Bind the validator to a document.

        Args:
            source: Name of the document used as the message prefix.
            error: Exception class raised by every failed check.
        """
        self._source = source
        self._error = error

    def fail(self, where: str, message: str) -> NoReturn:
        """Raise the configured exception for the value at ``where``.

        Args:
            where: JSON path of the offending value; empty for the root.
            message: Description of the violated rule.

        Raises:
            ProfileError: Always, as an instance of the bound class.
        """
        location = f"{self._source}: {where}" if where else self._source
        raise self._error(f"{location}: {message}")

    def record(
        self,
        value: object,
        where: str,
        *,
        required: Collection[str],
        optional: Collection[str] = (),
    ) -> Mapping[str, object]:
        """Validate an object with a closed set of keys.

        Args:
            value: Raw decoded value.
            where: JSON path of the value.
            required: Keys that must be present.
            optional: Keys that may be present.

        Returns:
            The object as a read-only mapping.

        Raises:
            ProfileError: If the value is not an object, lacks a required
                key or contains a key outside both collections.
        """
        mapping = self.mapping(value, where)
        missing = sorted(set(required) - mapping.keys())
        if missing:
            self.fail(where, "missing keys: " + ", ".join(missing))
        unknown = sorted(mapping.keys() - set(required) - set(optional))
        if unknown:
            self.fail(where, "unknown keys: " + ", ".join(unknown))
        return mapping

    def mapping(self, value: object, where: str) -> Mapping[str, object]:
        """Validate an object whose keys are data rather than a schema.

        Args:
            value: Raw decoded value.
            where: JSON path of the value.

        Returns:
            The object as a read-only mapping.

        Raises:
            ProfileError: If the value is not a JSON object.
        """
        if not isinstance(value, dict):
            self.fail(where, "expected an object")
        return value

    def array(
        self,
        value: object,
        where: str,
        *,
        min_items: int = 0,
        max_items: int | None = None,
    ) -> list[object]:
        """Validate an array and its length.

        Args:
            value: Raw decoded value.
            where: JSON path of the value.
            min_items: Smallest accepted length.
            max_items: Largest accepted length, or ``None`` for no limit.

        Returns:
            The array.

        Raises:
            ProfileError: If the value is not an array or its length is
                out of bounds.
        """
        if not isinstance(value, list):
            self.fail(where, "expected an array")
        if len(value) < min_items:
            self.fail(where, f"expected at least {min_items} items")
        if max_items is not None and len(value) > max_items:
            self.fail(where, f"expected at most {max_items} items")
        return value

    def string(
        self,
        value: object,
        where: str,
        *,
        pattern: re.Pattern[str] | None = None,
        max_length: int = 1000,
    ) -> str:
        """Validate a non-blank, single-line string.

        Control characters are rejected because every string eventually
        lands in SVG or HTML, where most of them are illegal or change
        the layout.

        Args:
            value: Raw decoded value.
            where: JSON path of the value.
            pattern: Regular expression the whole string must match.
            max_length: Largest accepted length in code points.

        Returns:
            The string.

        Raises:
            ProfileError: If the value is not a string, is blank, is too
                long, contains control characters or does not match.
        """
        if not isinstance(value, str) or not value.strip():
            self.fail(where, "expected a non-empty string")
        if len(value) > max_length:
            self.fail(where, f"longer than {max_length} characters")
        if any(
            unicodedata.category(char) in _DISALLOWED_CATEGORIES or char in _BIDI_OVERRIDES
            for char in value
        ):
            self.fail(where, "contains disallowed control characters")
        if pattern is not None and pattern.fullmatch(value) is None:
            self.fail(where, f"{value!r} does not match {pattern.pattern!r}")
        return value

    def integer(
        self,
        value: object,
        where: str,
        *,
        minimum: int = 0,
        maximum: int | None = None,
    ) -> int:
        """Validate an integer within bounds, rejecting booleans.

        Args:
            value: Raw decoded value.
            where: JSON path of the value.
            minimum: Smallest accepted value.
            maximum: Largest accepted value, or ``None`` for no limit.

        Returns:
            The integer.

        Raises:
            ProfileError: If the value is not an integer or is out of
                bounds.
        """
        if type(value) is not int:
            self.fail(where, "expected an integer")
        if value < minimum or (maximum is not None and value > maximum):
            upper = "" if maximum is None else f" and at most {maximum}"
            self.fail(where, f"expected at least {minimum}{upper}")
        return value

    def number(self, value: object, where: str, *, minimum: float, maximum: float) -> float:
        """Validate a finite number within a closed interval.

        Args:
            value: Raw decoded value; integers are accepted as well.
            where: JSON path of the value.
            minimum: Smallest accepted value.
            maximum: Largest accepted value.

        Returns:
            The value as ``float``.

        Raises:
            ProfileError: If the value is not a number or is outside the
                interval.
        """
        if isinstance(value, bool) or not isinstance(value, int | float):
            self.fail(where, "expected a number")
        number = float(value)
        if not (math.isfinite(number) and minimum <= number <= maximum):
            self.fail(where, f"expected a number between {minimum} and {maximum}")
        return number

    def boolean(self, value: object, where: str) -> bool:
        """Validate a boolean.

        Args:
            value: Raw decoded value.
            where: JSON path of the value.

        Returns:
            The boolean.

        Raises:
            ProfileError: If the value is not ``true`` or ``false``.
        """
        if not isinstance(value, bool):
            self.fail(where, "expected a boolean")
        return value

    def choice[T: str](self, value: object, where: str, choices: tuple[T, ...]) -> T:
        """Validate that a string is one of a fixed set of literals.

        Args:
            value: Raw decoded value.
            where: JSON path of the value.
            choices: Accepted values, typically ``get_args`` of a
                ``Literal`` type so the result keeps that type.

        Returns:
            The matching member of ``choices``.

        Raises:
            ProfileError: If the value is not one of ``choices``.
        """
        matches = [candidate for candidate in choices if candidate == value]
        if not matches:
            self.fail(where, "expected one of: " + ", ".join(choices))
        return matches[0]


def parse_placeholders(validator: Validator, template: str, where: str) -> frozenset[str]:
    """Extract plain ``{name}`` placeholders from a format template.

    Args:
        validator: Validator bound to the containing document.
        template: Format string or template text.
        where: JSON path or field name of the template.

    Returns:
        Distinct placeholder names used in ``template``.

    Raises:
        ProfileError: If brace syntax is invalid or uses format specs or
            conversions.
    """
    names: set[str] = set()
    try:
        fields = list(Formatter().parse(template))
    except ValueError as exc:
        validator.fail(where, f"malformed placeholder: {exc}")
    for _literal, name, spec, conversion in fields:
        if name is None:
            continue
        if not name.isidentifier() or spec or conversion:
            validator.fail(where, f"unsupported placeholder {{{name}}}; use a plain {{name}}")
        names.add(name)
    return frozenset(names)
