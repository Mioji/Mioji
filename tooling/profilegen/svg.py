# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""SVG drawing primitives, text measurement and line wrapping.

SVG has no text layout engine, so line breaks are computed here from an
estimate of rendered width. :func:`units` counts East Asian wide and
fullwidth characters as two units and everything else as one, which is
accurate enough for the proportional fonts in the theme. Panels then
pick a per-line unit budget for each font size.

Output formatting is part of the contract: numbers use ``:g`` so that
``15.0`` prints as ``15``, attributes appear in a fixed order and lines
are joined with newlines. Identical inputs must yield identical bytes, or
``--check`` would report spurious differences.
"""

from __future__ import annotations

import math
from html import escape
from typing import Final, Literal

from profilegen.theme import Theme
from profilegen.validation import char_units, display_units

MISSING: Final = "—"
"""Em dash shown in place of a value that is unknown or not public."""

PROPORTIONAL_ADVANCE: Final = 0.54
MONO_ADVANCE: Final = 0.6
CLOSING_PUNCT_SLACK: Final = 2

STROKE_INSET: Final = 0.5
"""Half of a 1px stroke. Offsetting outlines by it centres the stroke
on the pixel grid, so hairlines stay crisp instead of anti-aliased."""

BADGE_HEIGHT: Final = 42
"""Canvas height shared by all badges; display size comes from the
theme."""

# Closing punctuation that typographic convention forbids at the start
# of a line in Chinese and Japanese. Such a character stays on the
# current line even if it overflows the budget by one wide character.
_NO_LINE_START: Final = frozenset(
    "\uff0c\u3002\u3001\uff1a\uff1b\uff01\uff1f"  # Comma, full stop, enumeration
    # comma, colon, semicolon, exclamation and question marks.
    "\uff09\u300d\u300f\u3011\u300b\u3009\uff05"  # Closing brackets and percent.
)


def is_wide(char: str) -> bool:
    """Return whether ``char`` occupies two columns in East Asian text."""
    return char_units(char) == 2


def units(text: str) -> int:
    """Return the estimated display width of ``text`` in half-width units."""
    return display_units(text)


def fitted_size(
    text: str,
    base_size: int,
    max_width: float,
    *,
    mono: bool = False,
    min_size: int = 12,
) -> int:
    """Scale ``base_size`` down when ``text`` would exceed ``max_width``."""
    width_units = max(units(text), 1)
    advance = MONO_ADVANCE if mono else PROPORTIONAL_ADVANCE
    if width_units * base_size * advance <= max_width:
        return base_size
    return max(min_size, int(max_width / (width_units * advance)))


def wrap_budget(max_width: float, font_size: int, *, mono: bool = False) -> int:
    """Return a :func:`wrap` limit whose worst-case line fits ``max_width``."""
    advance = MONO_ADVANCE if mono else PROPORTIONAL_ADVANCE
    return max(4, int(max_width / (font_size * advance)) - CLOSING_PUNCT_SLACK)


def _tokens(text: str) -> list[tuple[bool, str]]:
    """Split text into ``(preceded_by_space, token)`` pairs.

    A token is either a single wide character (with any trailing
    combining marks attached), or a maximal run of other non-space
    characters, which is kept whole. Recording whether whitespace
    preceded each token lets :func:`wrap` reproduce the original spacing
    exactly instead of inventing spaces between scripts.
    """
    tokens: list[tuple[bool, str]] = []
    word: list[str] = []
    word_spaced = spaced = False
    for char in text:
        if char.isspace():
            if word:
                tokens.append((word_spaced, "".join(word)))
                word = []
            spaced = True
        elif char_units(char) == 0 and not word and tokens:
            prev_spaced, prev_token = tokens[-1]
            tokens[-1] = (prev_spaced, prev_token + char)
        elif is_wide(char):
            if word:
                tokens.append((word_spaced, "".join(word)))
                word = []
            tokens.append((spaced, char))
            spaced = False
        else:
            if not word:
                word_spaced, spaced = spaced, False
            word.append(char)
    if word:
        tokens.append((word_spaced, "".join(word)))
    return tokens


def _pieces(token: str, limit: int) -> list[str]:
    """Hard-split a token wider than ``limit`` at character boundaries.

    Breaking inside a word is ugly, but text running past the panel edge
    is worse and cannot be fixed by the reader.
    """
    if units(token) <= limit:
        return [token]
    pieces: list[str] = []
    current = ""
    current_width = 0
    for char in token:
        char_width = char_units(char)
        if current and current_width + char_width > limit:
            pieces.append(current)
            current = char
            current_width = char_width
        else:
            current += char
            current_width += char_width
    pieces.append(current)
    return pieces


def wrap(text: str, limit: int) -> list[str]:
    """Break ``text`` into lines of at most ``limit`` width units.

    Whitespace between tokens collapses to one space and disappears at
    line breaks. Wide characters break anywhere, other words only at
    whitespace unless they alone exceed the budget.

    Args:
        text: Text to wrap.
        limit: Line budget in :func:`units`, at least 1.

    Returns:
        The lines; empty if ``text`` contains no visible characters.

    Raises:
        ValueError: If ``limit`` is less than 1.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    lines: list[str] = []
    current = ""
    current_width = 0
    for spaced, token in _tokens(text):
        for index, piece in enumerate(_pieces(token, limit)):
            joiner = " " if spaced and index == 0 and current else ""
            piece_width = units(piece)
            candidate_width = current_width + len(joiner) + piece_width
            if (
                current
                and candidate_width > limit
                and (piece not in _NO_LINE_START or current_width > limit)
            ):
                lines.append(current)
                current = piece
                current_width = piece_width
            else:
                current += joiner + piece
                current_width = candidate_width
    if current:
        lines.append(current)
    return lines


def _check_finite(*values: float) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError("SVG coordinates must be finite numbers")


class Canvas:
    """Append-only builder for one SVG document.

    Colours are given as theme palette tokens and resolved on use, so a
    misspelt token fails during rendering rather than producing an
    invisible element.
    """

    __slots__ = ("_has_background", "_parts", "height", "theme", "width")

    def __init__(self, width: int, height: int, title: str, theme: Theme) -> None:
        """Start a document.

        Args:
            width: Width of the coordinate system.
            height: Height of the coordinate system.
            title: Accessible name exposed through ``<title>``.
            theme: Design tokens.
        """
        self.width = width
        self.height = height
        self.theme = theme
        self._has_background = False
        self._parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            'role="img" aria-labelledby="title">',
            f'<title id="title">{escape(title)}</title>',
        ]

    def text(
        self,
        x: float,
        y: float,
        value: object,
        size: int = 18,
        color: str = "text",
        weight: int = 400,
        *,
        mono: bool = False,
        anchor: Literal["start", "end"] = "start",
    ) -> None:
        """Draw one line of text with its baseline at ``y``."""
        _check_finite(x, y)
        alignment = ' text-anchor="end"' if anchor == "end" else ""
        self._parts.append(
            f'<text x="{x:g}" y="{y:g}"{alignment} font-size="{size}" '
            f'font-family="{self.theme.font(mono=mono)}" font-weight="{weight}" '
            f'fill="{self.theme.color(color)}">{escape(str(value))}</text>'
        )

    def path(
        self, data: str, color: str = "border", fill: str = "none", width: float = 1.0
    ) -> None:
        """Draw a path; ``fill`` is a palette token or ``"none"``."""
        _check_finite(width)
        fill_value = "none" if fill == "none" else self.theme.color(fill)
        self._parts.append(
            f'<path d="{data}" stroke="{self.theme.color(color)}" fill="{fill_value}" '
            f'stroke-width="{width:g}" opacity="1"/>'
        )

    def _outline(self) -> str:
        """Return the chamfered outline shared by frame and clip path."""
        w, h = self.width, self.height
        c, s = self.theme.corner_chamfer, STROKE_INSET
        if c <= s:
            return f"M{s:g} {s:g}H{w - s:g}V{h - s:g}H{s:g}Z"
        return f"M{s:g} {s:g}H{w - c:g}L{w - s:g} {c:g}V{h - s:g}H{s:g}Z"

    def frame(self, accent: str = "violet") -> None:
        """Draw the outline and the two accent corner brackets.

        Without artwork the outline is filled with the background colour;
        with artwork it stays transparent so the image shows through.
        """
        w, h = self.width, self.height
        b, s = self.theme.corner_bracket, STROKE_INSET
        self.path(self._outline(), fill="none" if self._has_background else "background")
        length = min(b, w - s, h - s)
        if length <= s + 1:
            return
        self.path(
            f"M{s:g} {length - 1:g}V{s:g}H{length:g}"
            f"M{w - length:g} {h - s:g}H{w - s:g}V{h - length:g}",
            color=accent,
        )

    def background(self, data_uri: str, shade: float) -> None:
        """Draw artwork clipped to the outline under a dark overlay.

        ``slice`` scaling fills the panel at any aspect ratio, cropping
        the artwork instead of letterboxing it. The overlay opacity
        ``shade`` keeps text contrast independent of the artwork.
        """
        clip = self._outline()
        self._parts.extend(
            (
                f'<defs><clipPath id="panel-background"><path d="{clip}"/></clipPath></defs>',
                f'<image href="{data_uri}" x="0" y="0" width="{self.width}" '
                f'height="{self.height}" preserveAspectRatio="xMidYMid slice" '
                'clip-path="url(#panel-background)"/>',
                f'<rect x="0" y="0" width="{self.width}" height="{self.height}" '
                f'fill="{self.theme.color("background")}" opacity="{shade:g}" '
                'clip-path="url(#panel-background)"/>',
            )
        )
        self._has_background = True

    def arrow(self, x: float, y: float, color: str = "violet") -> None:
        """Draw the north-east arrow that marks a clickable panel."""
        _check_finite(x, y)
        self.path(f"M{x:g} {y + 10:g}l12-12m-10 0h10v10", color=color, width=1.5)

    def star(self, x: float, y: float, size: float) -> None:
        """Draw an outlined five-pointed star with its top at ``y - size``."""
        _check_finite(x, y, size)
        s = size
        self.path(
            f"M{x:g} {y - s:g}l{s * 0.3:.2f} {s * 0.63:.2f} {s * 0.7:.2f} {s * 0.1:.2f}"
            f" {-s * 0.5:.2f} {s * 0.49:.2f} {s * 0.12:.2f} {s * 0.69:.2f}"
            f" {-s * 0.62:.2f} {-s * 0.33:.2f} {-s * 0.62:.2f} {s * 0.33:.2f}"
            f" {s * 0.12:.2f} {-s * 0.69:.2f} {-s * 0.5:.2f} {-s * 0.49:.2f}"
            f" {s * 0.7:.2f} {-s * 0.1:.2f}Z",
            "pink",
        )

    def finish(self) -> str:
        """Return the complete, newline-terminated document."""
        return "\n".join([*self._parts, "</svg>"]) + "\n"


def badge(label: str, mark: str, accent: str, width: int, theme: Theme) -> str:
    """Render a badge: a mark cell, a divider and the label.

    Args:
        label: Text of the badge.
        mark: One to three characters for the left cell.
        accent: Palette token of the mark and brackets.
        width: Canvas width.
        theme: Design tokens.

    Returns:
        The SVG document.
    """
    canvas = Canvas(width, BADGE_HEIGHT, label, theme)
    canvas.frame(accent)
    canvas.path("M3 3H38V39H3Z", "grid", fill="grid")
    canvas.path("M41 8v26", "border")
    mark_width = units(mark) * 13 * MONO_ADVANCE
    mark_x = max(5.0, round(20.5 - mark_width / 2, 1))
    canvas.text(mark_x, 26, mark, 13, accent, 600, mono=True)
    size = fitted_size(label, 15, max(width - 62, 16), min_size=6)
    canvas.text(52, 26, label, size, weight=600)
    return canvas.finish()


def picture(
    asset_base: str, alt: str, href: str, *, asset_prefix: str, compact_max_width: int
) -> str:
    """Return a linked ``<picture>`` that switches to the compact asset.

    GitHub's Markdown sanitiser keeps ``<picture>`` with ``<source
    media>``, which is the only way to serve a different image on narrow
    screens; CSS is stripped.

    Args:
        asset_base: Repository-relative path without ``.svg`` suffix.
        alt: Alternative text.
        href: Link target.
        asset_prefix: Relative path from the README to the repository
            root, empty for the root README.
        compact_max_width: Viewport width up to which the compact
            variant is served.

    Returns:
        The HTML fragment.
    """
    return (
        f'<a href="{escape(href)}"><picture>'
        f'<source media="(max-width: {compact_max_width}px)" '
        f'srcset="{escape(asset_prefix + asset_base)}-compact.svg">'
        f'<img src="{escape(asset_prefix + asset_base)}.svg" alt="{escape(alt)}" width="100%">'
        "</picture></a>"
    )
