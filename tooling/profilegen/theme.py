# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Design tokens loaded from ``configs/theme.json``.

The theme file is the single source of colours, font stacks, overlay
strengths and badge geometry. There are no fallback values in code: a
missing or misspelt token is a configuration error, because a silent
default would render a subtly wrong profile that nobody notices.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from profilegen.errors import ConfigError
from profilegen.validation import Validator, child, display_units, load_json

REQUIRED_COLORS: Final = frozenset(
    ("background", "border", "grid", "text", "muted", "pink", "violet")
)
"""Palette tokens referenced directly by the panel renderers."""

PANELS: Final = ("summary", "project", "activity", "statistics", "knowledge")
"""Panels that draw artwork and therefore need an overlay strength."""

_COLOR: Final = re.compile(r"#[0-9a-fA-F]{6}")
_TOKEN: Final = re.compile(r"[a-z][a-z0-9_]*")
# Font stacks are embedded verbatim in an XML attribute. Restricting the
# alphabet excludes quotes and ampersands, so no escaping can go wrong.
_FONT_STACK: Final = re.compile(r"[A-Za-z0-9 ,-]+")


@dataclass(frozen=True, slots=True)
class BadgeSize:
    """Geometry of a fixed-width badge.

    Attributes:
        canvas_width: Width of the SVG coordinate system.
        display_height: Height of the ``<img>`` element in the README;
            GitHub scales the badge proportionally from it.
    """

    canvas_width: int
    display_height: int


@dataclass(frozen=True, slots=True)
class SkillBadgeSize:
    """Geometry of skill badges, whose width follows the label.

    Width is ``max(min_width, base_width + display_units * char_width)``,
    measuring East Asian wide characters as two columns so multi-byte
    labels never clip past the badge border.

    Attributes:
        display_height: Height of the ``<img>`` element in the README.
        min_width: Smallest canvas width.
        base_width: Width taken by the frame, divider and mark.
        char_width: Average advance of one label character.
    """

    display_height: int
    min_width: int
    base_width: int
    char_width: int

    def width_for(self, label: str) -> int:
        """Return the canvas width for a badge with ``label``."""
        return max(self.min_width, self.base_width + display_units(label) * self.char_width)


@dataclass(frozen=True, slots=True)
class Theme:
    """Validated design tokens.

    Attributes:
        name: Display name of the theme.
        palette: Hex colours keyed by token name.
        font_family: Proportional font stack.
        mono_family: Monospaced font stack.
        overlays: Opacity of the dark layer drawn over artwork, keyed by
            panel; higher values trade artwork visibility for contrast.
        corner_chamfer: Size of the cut top-right corner of frames.
        corner_bracket: Length of the accent brackets on frame corners.
        navigation_badge: Language switcher badge geometry.
        contact_badge: Contact badge geometry.
        skill_badge: Skill badge geometry.
        compact_max_width: Viewport width up to which the README serves
            the compact variant of each panel.
    """

    name: str
    palette: Mapping[str, str]
    font_family: str
    mono_family: str
    overlays: Mapping[str, float]
    corner_chamfer: float
    corner_bracket: float
    navigation_badge: BadgeSize
    contact_badge: BadgeSize
    skill_badge: SkillBadgeSize
    compact_max_width: int

    def color(self, token: str) -> str:
        """Resolve a palette token to its hex colour.

        Args:
            token: Palette token such as ``pink``.

        Returns:
            The colour, for example ``#f28acb``.

        Raises:
            ConfigError: If the palette does not define ``token``.
        """
        try:
            return self.palette[token]
        except KeyError:
            raise ConfigError(f"theme: unknown colour token {token!r}") from None

    def font(self, *, mono: bool) -> str:
        """Return the monospaced or proportional font stack."""
        return self.mono_family if mono else self.font_family


def _badge(v: Validator, value: object, where: str) -> BadgeSize:
    data = v.record(value, where, required=("canvas_width", "display_height"))
    return BadgeSize(
        canvas_width=v.integer(data["canvas_width"], child(where, "canvas_width"), minimum=80),
        display_height=v.integer(
            data["display_height"], child(where, "display_height"), minimum=12
        ),
    )


def load_theme(path: Path, *, source: str) -> Theme:
    """Load and validate the theme file.

    Args:
        path: Location of ``theme.json``.
        source: Repository-relative name used in error messages.

    Returns:
        The validated theme.

    Raises:
        ConfigError: If the file is unreadable or violates the schema.
    """
    v = Validator(source, ConfigError)
    data = v.record(
        load_json(path, source=source, error=ConfigError),
        "",
        required=("name", "palette", "typography", "overlays", "geometry", "badges", "breakpoints"),
    )

    palette: dict[str, str] = {}
    for token, color in v.mapping(data["palette"], "palette").items():
        if _TOKEN.fullmatch(token) is None:
            v.fail(child("palette", token), "invalid token name")
        palette[token] = v.string(color, child("palette", token), pattern=_COLOR).lower()
    missing = sorted(REQUIRED_COLORS - palette.keys())
    if missing:
        v.fail("palette", "missing tokens: " + ", ".join(missing))

    typography = v.record(data["typography"], "typography", required=("font_family", "mono_family"))
    overlays_data = v.record(data["overlays"], "overlays", required=PANELS)
    geometry = v.record(data["geometry"], "geometry", required=("corner_chamfer", "corner_bracket"))
    badges = v.record(data["badges"], "badges", required=("navigation", "contact", "skill"))
    skill = v.record(
        badges["skill"],
        "badges.skill",
        required=("display_height", "min_width", "base_width", "char_width"),
    )
    breakpoints = v.record(data["breakpoints"], "breakpoints", required=("compact_max_width",))

    return Theme(
        name=v.string(data["name"], "name", max_length=80),
        palette=palette,
        font_family=v.string(
            typography["font_family"], "typography.font_family", pattern=_FONT_STACK
        ),
        mono_family=v.string(
            typography["mono_family"], "typography.mono_family", pattern=_FONT_STACK
        ),
        overlays={
            panel: v.number(overlays_data[panel], child("overlays", panel), minimum=0, maximum=1)
            for panel in PANELS
        },
        corner_chamfer=v.number(
            geometry["corner_chamfer"], "geometry.corner_chamfer", minimum=0, maximum=40
        ),
        corner_bracket=v.number(
            geometry["corner_bracket"], "geometry.corner_bracket", minimum=0, maximum=80
        ),
        navigation_badge=_badge(v, badges["navigation"], "badges.navigation"),
        contact_badge=_badge(v, badges["contact"], "badges.contact"),
        skill_badge=SkillBadgeSize(
            display_height=v.integer(
                skill["display_height"], "badges.skill.display_height", minimum=12
            ),
            min_width=v.integer(skill["min_width"], "badges.skill.min_width", minimum=80),
            base_width=v.integer(skill["base_width"], "badges.skill.base_width", minimum=56),
            char_width=v.integer(skill["char_width"], "badges.skill.char_width", minimum=1),
        ),
        compact_max_width=v.integer(
            breakpoints["compact_max_width"],
            "breakpoints.compact_max_width",
            minimum=320,
            maximum=1280,
        ),
    )
