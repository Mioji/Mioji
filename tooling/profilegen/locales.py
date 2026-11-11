# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Locale catalogues under ``locales/readme/<code>/strings.json``.

Each locale directory holds one catalogue with display strings, list
values and number and date formats. Loading is strict by design:

* every locale must define exactly the keys the renderer and the
  configuration reference. Missing keys fail the build instead of
  falling back to English, and unused keys fail it too, so stale copy
  cannot accumulate unnoticed;
* every string is a :meth:`str.format` template. Its placeholders are
  checked against the names the renderer supplies for that key, which
  turns a translator's typo into a load-time error instead of a crash
  in the middle of rendering;
* date patterns may only use numeric ``strftime`` directives. Names of
  months or weekdays depend on the C locale of the machine, which would
  make output differ between a laptop and CI.

The default locale is ordered first; the rest follow by code, which
fixes the order of the language switcher.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

from profilegen.errors import ConfigError
from profilegen.paths import STRINGS_FILE
from profilegen.validation import Validator, child, load_json, parse_placeholders

LOCALE_CODE: Final = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_DIRECTIVE: Final = re.compile(r"%(.?)")
_DATE_DIRECTIVES: Final = frozenset("Ymd%")
_TIMESTAMP_DIRECTIVES: Final = frozenset("YmdHMS%")
_SCALES: Final = ("thousand", "million", "billion")
_DIVISORS: Final = (1_000, 1_000_000, 1_000_000_000)


@dataclass(frozen=True, slots=True)
class NumberFormat:
    """Compact number notation of one locale.

    Attributes:
        decimal_separator: Separator between whole and tenth digits.
        suffixes: Suffix for thousands, millions and billions, in that
            order. A suffix may start with a space, as in ``" тыс."``.
    """

    decimal_separator: str
    suffixes: tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class Locale:
    """Validated catalogue of one locale.

    Attributes:
        code: Locale code, equal to the directory name.
        language: Endonym shown in the language switcher.
        strings: Message templates keyed by name.
        lists: Ordered string lists keyed by name.
        number: Compact number notation.
        date_format: ``strftime`` pattern for calendar dates.
        timestamp_format: ``strftime`` pattern for UTC timestamps.
    """

    code: str
    language: str
    strings: Mapping[str, str]
    lists: Mapping[str, tuple[str, ...]]
    number: NumberFormat
    date_format: str
    timestamp_format: str

    def text(self, key: str, **values: object) -> str:
        """Format the message ``key`` with the given placeholder values.

        Placeholder names were validated at load time, so a
        ``KeyError`` here indicates a renderer bug, not bad input.

        Args:
            key: Message name.
            **values: Values for the template placeholders.

        Returns:
            The formatted message.
        """
        return self.strings[key].format_map(values)

    def items(self, key: str) -> tuple[str, ...]:
        """Return the list value ``key``."""
        return self.lists[key]

    def format_count(self, value: int) -> str:
        """Format a non-negative count in compact notation.

        Values below 1000 are printed in full. Larger values keep one
        decimal digit, rounded half up with integer arithmetic so the
        result never depends on binary floating point. When rounding
        reaches 1000 of a unit the next unit is used, so 999 950 becomes
        ``1M`` rather than ``1000k``.

        Args:
            value: Count to format.

        Returns:
            The formatted count, for example ``1.2k`` or ``1,2 тыс.``.
        """
        if value < 1000:
            return str(value)
        scale = 0
        while scale < len(_SCALES) - 1 and value >= _DIVISORS[scale + 1]:
            scale += 1
        tenths = _tenths(value, scale)
        if tenths >= 10_000 and scale < len(_SCALES) - 1:
            scale += 1
            tenths = _tenths(value, scale)
        whole, fraction = divmod(tenths, 10)
        mantissa = f"{whole}{self.number.decimal_separator}{fraction}" if fraction else str(whole)
        return mantissa + self.number.suffixes[scale]

    def format_date(self, value: date) -> str:
        """Format a calendar date with the locale's numeric pattern."""
        return value.strftime(self.date_format)

    def format_timestamp(self, value: datetime) -> str:
        """Format a UTC timestamp with the locale's numeric pattern."""
        return value.strftime(self.timestamp_format)


def _tenths(value: int, scale: int) -> int:
    """Return ``value`` in tenths of the unit at ``scale``, half up."""
    divisor = _DIVISORS[scale]
    return (value * 10 + divisor // 2) // divisor


def _date_pattern(v: Validator, value: object, where: str, *, allow_time: bool) -> str:
    pattern = v.string(value, where, max_length=40)
    allowed = _TIMESTAMP_DIRECTIVES if allow_time else _DATE_DIRECTIVES
    accepted = "%Y %m %d %H %M %S %%" if allow_time else "%Y %m %d %%"
    for directive in _DIRECTIVE.findall(pattern):
        if directive not in allowed:
            v.fail(where, f"directive %{directive} is not one of {accepted}")
    return pattern


def _load_locale(
    path: Path,
    *,
    code: str,
    source: str,
    strings: Collection[str],
    lists: Collection[str],
    placeholders: Mapping[str, frozenset[str]],
) -> Locale:
    v = Validator(source, ConfigError)
    data = v.record(
        load_json(path, source=source, error=ConfigError),
        "",
        required=("language", "locale_code", "locale_format", "content", "structured_lists"),
    )
    if data["locale_code"] != code:
        v.fail("locale_code", f"must equal the directory name {code!r}")

    fmt = v.record(data["locale_format"], "locale_format", required=("number", "date"))
    number = v.record(
        fmt["number"], "locale_format.number", required=("decimal_separator", "compact_suffixes")
    )
    suffixes = v.record(
        number["compact_suffixes"], "locale_format.number.compact_suffixes", required=_SCALES
    )
    dates = v.record(
        fmt["date"], "locale_format.date", required=("date_format", "timestamp_format")
    )

    content = v.record(data["content"], "content", required=strings)
    catalogue: dict[str, str] = {}
    for key in strings:
        where = child("content", key)
        template = v.string(content[key], where, max_length=400)
        used = parse_placeholders(v, template, where)
        unknown = sorted(used - placeholders.get(key, frozenset()))
        if unknown:
            v.fail(where, "unknown placeholders: " + ", ".join(unknown))
        catalogue[key] = template

    list_data = v.record(data["structured_lists"], "structured_lists", required=lists)
    list_values = {
        key: tuple(
            v.string(item, child(child("structured_lists", key), index), max_length=80)
            for index, item in enumerate(
                v.array(list_data[key], child("structured_lists", key), min_items=1, max_items=20)
            )
        )
        for key in lists
    }

    thousand, million, billion = (
        v.string(suffixes[scale], f"locale_format.number.compact_suffixes.{scale}", max_length=8)
        for scale in _SCALES
    )
    return Locale(
        code=code,
        language=v.string(data["language"], "language", max_length=40),
        strings=catalogue,
        lists=list_values,
        number=NumberFormat(
            decimal_separator=v.string(
                number["decimal_separator"],
                "locale_format.number.decimal_separator",
                max_length=1,
            ),
            suffixes=(thousand, million, billion),
        ),
        date_format=_date_pattern(
            v, dates["date_format"], "locale_format.date.date_format", allow_time=False
        ),
        timestamp_format=_date_pattern(
            v,
            dates["timestamp_format"],
            "locale_format.date.timestamp_format",
            allow_time=True,
        ),
    )


def load_locales(
    directory: Path,
    *,
    source: str,
    default: str,
    strings: Collection[str],
    lists: Collection[str],
    placeholders: Mapping[str, frozenset[str]],
) -> dict[str, Locale]:
    """Discover and validate every locale catalogue.

    Args:
        directory: Directory containing one subdirectory per locale.
        source: Repository-relative name of ``directory`` for messages.
        default: Code of the locale rendered into the root README.
        strings: Exact set of message keys each catalogue must define.
        lists: Exact set of list keys each catalogue must define.
        placeholders: Placeholder names permitted per message key; keys
            absent from the mapping may not use placeholders at all.

    Returns:
        Locales keyed by code, the default locale first and the rest in
        code order.

    Raises:
        ConfigError: If the default locale is missing, a directory name
            is not a locale code or any catalogue is invalid.
    """
    codes = sorted(path.parent.name for path in directory.glob(f"*/{STRINGS_FILE}"))
    if default not in codes:
        raise ConfigError(f"{source}: default locale {default!r} is missing")
    ordered = [default, *(code for code in codes if code != default)]
    locales: dict[str, Locale] = {}
    for code in ordered:
        if LOCALE_CODE.fullmatch(code) is None:
            raise ConfigError(f"{source}/{code}: directory name is not a locale code")
        locales[code] = _load_locale(
            directory / code / STRINGS_FILE,
            code=code,
            source=f"{source}/{code}/{STRINGS_FILE}",
            strings=strings,
            lists=lists,
            placeholders=placeholders,
        )
    return locales
