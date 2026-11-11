# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Line wrapping, width estimation and locale-aware formatting."""

from __future__ import annotations

import dataclasses
import math
import re
from datetime import UTC, date, datetime

import pytest

from profilegen import paths
from profilegen.locales import Locale, NumberFormat
from profilegen.render import load_inputs
from profilegen.svg import Canvas, units, wrap
from profilegen.theme import load_theme


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        pytest.param("Alpha / Beta / Gamma", 80, ["Alpha / Beta / Gamma"], id="slash-spacing"),
        pytest.param("Alpha  Beta", 80, ["Alpha Beta"], id="collapses-whitespace"),
        pytest.param("one two three", 7, ["one two", "three"], id="breaks-at-spaces"),
        pytest.param("abcdefghij", 4, ["abcd", "efgh", "ij"], id="splits-long-word"),
        pytest.param(
            "自动化 GitHub 主页：SVG", 80, ["自动化 GitHub 主页：SVG"], id="mixed-scripts"
        ),
        pytest.param("基于Rust的", 80, ["基于Rust的"], id="no-invented-spaces"),
        pytest.param("一二三四", 4, ["一二", "三四"], id="cjk-breaks-anywhere"),
        pytest.param("一二，三", 4, ["一二，", "三"], id="no-comma-at-line-start"),
        pytest.param("  ", 10, [], id="blank"),
    ],
)
def test_wrap(text: str, limit: int, expected: list[str]) -> None:
    assert wrap(text, limit) == expected


def test_units_counts_wide_characters_twice() -> None:
    assert units("ab中文") == 6


def _locale(separator: str = ".", suffixes: tuple[str, str, str] = ("k", "M", "B")) -> Locale:
    return Locale(
        code="xx",
        language="Test",
        strings={},
        lists={},
        number=NumberFormat(decimal_separator=separator, suffixes=suffixes),
        date_format="%d.%m.%Y",
        timestamp_format="%Y-%m-%d %H:%M UTC",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (999, "999"),
        (1_000, "1k"),
        (1_049, "1k"),
        (1_050, "1.1k"),
        (1_250, "1.3k"),
        (999_949, "999.9k"),
        (999_950, "1M"),
        (1_500_000, "1.5M"),
        (999_950_000, "1B"),
        (2_000_000_000_000, "2000B"),
    ],
)
def test_format_count(value: int, expected: str) -> None:
    assert _locale().format_count(value) == expected


def test_format_count_uses_locale_separator_and_suffix() -> None:
    russian = _locale(",", (" тыс.", " млн", " млрд"))
    assert russian.format_count(1_234) == "1,2 тыс."


def test_dates_use_locale_patterns() -> None:
    locale = _locale()
    assert locale.format_date(date(2026, 7, 30)) == "30.07.2026"
    moment = datetime(2026, 11, 10, 17, 35, 50, tzinfo=UTC)
    assert locale.format_timestamp(moment) == "2026-11-10 17:35 UTC"


def test_committed_locales_render_russian_dates() -> None:
    russian = load_inputs(paths.REPOSITORY_ROOT).locales["ru"]
    assert russian.format_date(date(2026, 7, 30)) == "30.07.2026"
    assert russian.format_count(1_500) == "1,5 тыс."


def test_wrap_rejects_non_positive_limit_and_bounds_closing_punctuation() -> None:
    with pytest.raises(ValueError, match="limit"):
        wrap("abc", 0)
    lines = wrap("一二。。。。。", 4)
    assert len(lines) >= 2
    assert units(lines[0]) <= 6


def test_wrap_splits_large_token_linearly() -> None:
    pieces = wrap("a" * 20_000, 50)
    assert len(pieces) == 400
    assert all(len(piece) == 50 for piece in pieces)


def test_canvas_rejects_non_finite_coordinates() -> None:
    theme = load_theme(paths.REPOSITORY_ROOT / paths.THEME_CONFIG, source=str(paths.THEME_CONFIG))
    canvas = Canvas(100, 100, "test", theme)
    with pytest.raises(ValueError, match="finite"):
        canvas.text(math.nan, 10, "x")
    with pytest.raises(ValueError, match="finite"):
        canvas.arrow(math.inf, 10)


def test_units_and_wrap_treat_combining_marks_as_zero_width() -> None:
    assert units("e\u0301и\u0306") == 2
    assert units("漢\u0301") == 2
    assert wrap("a\u0301b\u0301c\u0301d\u0301", 2) == ["a\u0301b\u0301", "c\u0301d\u0301"]
    assert wrap("漢字\u0301", 2) == ["漢", "字\u0301"]


def test_canvas_outline_and_brackets_stay_in_bounds_at_theme_extremes() -> None:
    base = load_theme(paths.REPOSITORY_ROOT / paths.THEME_CONFIG, source=str(paths.THEME_CONFIG))
    zero = dataclasses.replace(base, corner_chamfer=0.0, corner_bracket=0.0)
    zero_canvas = Canvas(124, 42, "test", zero)
    zero_canvas.frame()
    zero_svg = zero_canvas.finish()
    assert "H124L123.5" not in zero_svg
    assert "-1" not in zero_svg

    huge = dataclasses.replace(base, corner_bracket=80.0)
    huge_canvas = Canvas(124, 42, "test", huge)
    huge_canvas.frame()
    huge_svg = huge_canvas.finish()
    for path_data in re.findall(r' d="([^"]+)"', huge_svg):
        x = y = 0.0
        for cmd, arg_str in re.findall(r"([MLHV])([^MLHVZ]*)", path_data):
            nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", arg_str)]
            if cmd in {"M", "L"}:
                x, y = nums[0], nums[1]
            elif cmd == "H":
                x = nums[0]
            elif cmd == "V":
                y = nums[0]
            assert 0.0 <= x <= 124.0
            assert 0.0 <= y <= 42.0
