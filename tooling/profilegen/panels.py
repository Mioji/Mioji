# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Renderers for the individual SVG panels.

Each panel comes in two variants: a 960-unit-wide desktop layout and a
480-unit-wide compact layout that the README serves on narrow screens.
Coordinates are tuned by hand for those two widths, which is why widths
are constants here rather than theme tokens. Heights grow with wrapped
text wherever the copy is variable, so long translations extend a panel
instead of overflowing it.

Every renderer is a pure function of its arguments. The module also
declares which locale strings and lists the panels need and which
placeholders each message may use; :mod:`profilegen.render` passes both
to the locale loader so that catalogues are validated up front.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from profilegen.config import Project
from profilegen.locales import Locale
from profilegen.models import SUMMARY_PLACEHOLDERS, Summary
from profilegen.svg import (
    MISSING,
    MONO_ADVANCE,
    Canvas,
    fitted_size,
    units,
    wrap,
    wrap_budget,
)
from profilegen.theme import Theme

DESKTOP_WIDTH: Final = 960
COMPACT_WIDTH: Final = 480

STATISTICS: Final = (
    "repositories",
    "stars",
    "forks",
    "followers",
    "following",
    "gists",
    "originals",
    "open_items",
)
"""Counters on the statistics panel; ``<name>_label`` is the caption."""

REQUIRED_STRINGS: Final = frozenset(
    (
        "dashboard_title",
        "dashboard_alt",
        "quick_alt",
        "profile_heading",
        "knowledge_heading",
        *(f"{name}_label" for name in STATISTICS),
        "language_stats_label",
        "no_language_data",
        "active_label",
        "development_label",
        "project_stars_label",
        "public_total",
        "public_label",
        "private_label",
        "active_status",
        "development_status",
        "archived_status",
        "project_stars_caption",
        "private_stars_caption",
        "more_projects",
        "updated_label",
        "public_scope",
        "refresh_note",
        "activity_scope",
        "observed_events_label",
        "active_days_label",
        "event_repositories_label",
        "joined_label",
        "last_push_label",
        "archived_label",
        "forked_label",
        "softskills_label",
        "interests_label",
    )
)
"""Message keys used by the renderers, independent of configuration."""

REQUIRED_LISTS: Final = frozenset(("softskills_items", "interests_items"))

PLACEHOLDERS: Final[Mapping[str, frozenset[str]]] = {
    "quick_alt": frozenset(("active", "development", "stars")),
    "dashboard_alt": SUMMARY_PLACEHOLDERS,
    "more_projects": frozenset(("start", "end")),
}
"""Placeholders each message may use; all other messages take none."""


@dataclass(frozen=True, slots=True)
class Card:
    """Display data of one project card.

    Attributes:
        project: Catalog entry.
        index: One-based position in the catalog.
        stars: Live star count, or ``None`` for private projects.
    """

    project: Project
    index: int
    stars: int | None


def _width(compact: bool) -> int:
    return COMPACT_WIDTH if compact else DESKTOP_WIDTH


def project_card(
    card: Card, locale: Locale, theme: Theme, background: str, *, compact: bool
) -> str:
    """Render one catalog card.

    Private cards get a pink frame and the em dash instead of a count,
    so they read as intentionally hidden rather than as zero stars.
    """
    project = card.project
    width = _width(compact)
    description = locale.text(project.description_key)
    status = status_label(project, locale)
    desc_size = 22 if compact else 17
    desc_avail = (width - 14 if compact else width - 184) - 24
    lines = wrap(description, wrap_budget(desc_avail, desc_size))
    top, line_height = (119, 29) if compact else (92, 24)
    height = top + len(lines) * line_height + 20

    canvas = Canvas(width, height, f"{project.title}. {status}. {description}", theme)
    canvas.background(background, theme.overlays["project"])
    canvas.frame("pink" if card.stars is None else "violet")
    canvas.text(22, 34, f"{card.index:02}", 14, "violet", mono=True)
    title_size = fitted_size(project.title, 26, width - 115, min_size=6)
    canvas.text(65, 38, project.title, title_size, weight=600)
    canvas.arrow(width - 38, 24)

    stars = MISSING if card.stars is None else locale.format_count(card.stars)
    stars_units = units(stars)
    x, y = (width - 84, 78) if compact else (width - 102, 82)
    star_size = 28
    text_x = x - 10
    if stars_units > 4:
        avail = 124.0 if compact else 114.0
        star_size = fitted_size(stars, 28, avail, mono=True, min_size=8)
        right_edge = width - (22 if compact else 20)
        min_text_x = 315 if compact else width - 134
        shifted = round(right_edge - stars_units * star_size * MONO_ADVANCE)
        text_x = max(min_text_x, min(text_x, shifted))
        x = text_x + 10
    status_avail = (text_x - 42 if compact else width - 180) - 24
    status_size = fitted_size(status, 18 if compact else 16, max(status_avail, 40), min_size=6)
    canvas.text(24, 80 if compact else 63, status, status_size, "violet")
    canvas.star(x - 31, y - 9, 11)
    canvas.text(text_x, y, stars, star_size, "pink", 600, mono=True)
    if not compact:
        caption_key = "private_stars_caption" if card.stars is None else "project_stars_caption"
        caption = locale.text(caption_key)
        caption_size = fitted_size(caption, 14, 130, min_size=10)
        canvas.text(width - 145, 110, caption, caption_size, "muted")
        canvas.path(f"M{width - 168} 52V{height - 23}", "border")

    for row, line in enumerate(lines):
        canvas.text(24, top + row * line_height, line, desc_size, "muted")
    return canvas.finish()


def status_label(project: Project, locale: Locale) -> str:
    """Return the localised ``visibility / status`` line of a card."""
    return (
        f"{locale.text(project.visibility + '_label')} / {locale.text(project.status + '_status')}"
    )


def summary_strip(
    cards: tuple[Card, ...],
    summary: Summary,
    locale: Locale,
    theme: Theme,
    background: str,
    *,
    compact: bool,
) -> str:
    """Render the strip with active, in-development and star totals."""
    width = _width(compact)
    values = (
        (_count_status(cards, "active"), "active_label"),
        (_count_status(cards, "development"), "development_label"),
        (summary.project_stars, "project_stars_label"),
    )
    label_size = 17
    col_step = width / 3
    col_avails = [
        (width - 14 if index == 2 else (index + 1) * col_step - 5) - (22 + index * col_step)
        for index in range(3)
    ]
    wrapped = [
        wrap(locale.text(label), wrap_budget(avail, label_size))
        for (_, label), avail in zip(values, col_avails, strict=True)
    ]
    max_rows = max((len(lines) for lines in wrapped), default=1)
    height = max(145, 78 + max_rows * 21 + 25) if compact else max(116, 78 + max_rows * 21 + 17)
    canvas = Canvas(width, height, summary_alt(cards, summary, locale), theme)
    canvas.background(background, theme.overlays["summary"])
    canvas.frame("pink")

    for index, ((value, _), lines) in enumerate(zip(values, wrapped, strict=True)):
        x = 22 + index * col_step
        if index:
            canvas.path(f"M{x - 13:g} 24V{height - 24}", "border")
        if index == 2:
            canvas.star(x + 10, 37, 10)
        count_x = x + (29 if index == 2 else 0)
        col_right = width - 14 if index == 2 else (index + 1) * col_step - 18
        formatted = locale.format_count(value)
        avail_count = max(col_right - count_x, 40)
        count_size = fitted_size(formatted, 31, avail_count, mono=True, min_size=10)
        canvas.text(count_x, 48, formatted, count_size, "pink", 600, mono=True)
        for row, line in enumerate(lines):
            canvas.text(x, 78 + row * 21, line, label_size, "muted")
        if index == 2:
            total_note = locale.text("public_total")
            total_size = fitted_size(total_note, 13, width - 14 - x, min_size=9)
            canvas.text(x, height - 14, total_note, total_size, "violet")
    return canvas.finish()


def _count_status(cards: tuple[Card, ...], status: str) -> int:
    return sum(card.project.status == status for card in cards)


def summary_alt(cards: tuple[Card, ...], summary: Summary, locale: Locale) -> str:
    """Return the accessible description of the summary strip."""
    return locale.text(
        "quick_alt",
        active=_count_status(cards, "active"),
        development=_count_status(cards, "development"),
        stars=summary.project_stars,
    )


def activity(
    summary: Summary, locale: Locale, theme: Theme, background: str, *, compact: bool
) -> str:
    """Render the daily event histogram of the activity window.

    Bars are scaled to the busiest day, so the chart shows the shape of
    activity rather than absolute volume; exact totals are printed above
    it. Days without events get a flat tick, so gaps read as zero
    rather than as missing data.
    """
    width = _width(compact)
    metrics = (
        (summary.events, "observed_events_label"),
        (summary.active_days, "active_days_label"),
        (summary.event_repositories, "event_repositories_label"),
    )
    label_size = 16 if compact else 17
    col_step = width / 3
    col_avails = [
        (width - 14 if index == 2 else (index + 1) * col_step + 8) - (24 + index * col_step)
        for index in range(3)
    ]
    metric_wraps = [
        wrap(locale.text(label), wrap_budget(avail, label_size))
        for (_, label), avail in zip(metrics, col_avails, strict=True)
    ]
    extra_top = max(0, (max((len(lines) for lines in metric_wraps), default=1) - 2) * 20)
    footer = wrap(locale.text("activity_scope"), wrap_budget(width - 38, 16))
    footer_top = 310 + extra_top
    height = max((362 if compact else 330) + extra_top, footer_top + (len(footer) - 1) * 22 + 20)
    title = f"{locale.text('dashboard_title')}. {activity_alt(summary, locale)}"
    canvas = Canvas(width, height, title, theme)
    canvas.background(background, theme.overlays["activity"])
    canvas.frame("pink")

    for index, ((value, _), lines, avail) in enumerate(
        zip(metrics, metric_wraps, col_avails, strict=True)
    ):
        x = 24 + index * col_step
        value_str = str(value)
        value_size = fitted_size(value_str, 32, avail, mono=True, min_size=12)
        canvas.text(x, 53, value_str, value_size, "pink", 600, mono=True)
        for row, line in enumerate(lines):
            canvas.text(x, 80 + row * 20, line, label_size, "muted")

    step, baseline = (width - 48) / len(summary.window), 245 + extra_top
    peak = max(summary.daily_events, default=0)
    for y in (135 + extra_top, 190 + extra_top, baseline):
        canvas.path(f"M24 {y}H{width - 24}", "grid")
    for index, count in enumerate(summary.daily_events):
        x = 24 + index * step
        if count:
            bar = count / peak * 105
            canvas.path(f"M{x:.2f} {baseline}v{-bar:.2f}h{step - 4:.2f}v{bar:.2f}Z", "pink", "pink")
        else:
            canvas.path(f"M{x:.2f} {baseline}h{step - 4:.2f}", "border", width=2.0)

    date_avail = (width - 60) / 2
    start_date = locale.format_date(summary.window[0])
    end_date = locale.format_date(summary.window[-1])
    canvas.text(
        24,
        274 + extra_top,
        start_date,
        fitted_size(start_date, 15, date_avail, mono=True, min_size=9),
        "muted",
        mono=True,
    )
    canvas.text(
        width - 24,
        274 + extra_top,
        end_date,
        fitted_size(end_date, 15, date_avail, mono=True, min_size=9),
        "muted",
        mono=True,
        anchor="end",
    )
    for row, line in enumerate(footer):
        canvas.text(24, footer_top + row * 22, line, 16, "violet")
    return canvas.finish()


def activity_alt(summary: Summary, locale: Locale) -> str:
    """Return the accessible description of the activity panel."""
    return locale.text("dashboard_alt", **summary.placeholders())


def statistics(
    summary: Summary, locale: Locale, theme: Theme, background: str, *, compact: bool
) -> str:
    """Render account counters, metadata, languages and freshness."""
    width = _width(compact)
    columns = 2 if compact else 4
    col_step = width / columns
    label_size = 17
    col_avails = [
        (width - 14 - (24 + col * col_step) if col == columns - 1 else col_step - 16)
        for col in (index % columns for index in range(len(STATISTICS)))
    ]
    stat_wraps = [
        wrap(locale.text(f"{name}_label"), wrap_budget(avail, label_size))
        for name, avail in zip(STATISTICS, col_avails, strict=True)
    ]
    row_extra = max(0, (max((len(lines) for lines in stat_wraps), default=1) - 2) * 23)
    grid_rows = len(STATISTICS) // columns
    grid_extra = grid_rows * row_extra

    limit = 3 if compact else 4
    shown = summary.languages[:limit]
    languages = (
        " / ".join(f"{name[:14]} ({count})" for name, count in shown)
        if shown
        else locale.text("no_language_data")
    )
    if len(summary.languages) > limit:
        languages += f" / +{len(summary.languages) - limit}"
    lang_lines = wrap(languages, wrap_budget(width - 38, 17))
    lang_extra = max(0, (len(lang_lines) - (2 if compact else 1)) * 22)

    scope = f"{locale.text('public_scope')} / {locale.text('refresh_note')}"
    scope_lines = wrap(scope, wrap_budget(width - 38, 14))
    scope_extra = max(0, (len(scope_lines) - 2) * 18)

    height = (770 if compact else 458) + grid_extra + lang_extra + scope_extra
    divider_y = height - 78 - scope_extra

    canvas = Canvas(width, height, locale.text("profile_heading"), theme)
    canvas.background(background, theme.overlays["statistics"])
    canvas.frame()

    for index, (name, lines, avail) in enumerate(
        zip(STATISTICS, stat_wraps, col_avails, strict=True)
    ):
        x = 24 + index % columns * col_step
        y = 64 + index // columns * (104 + row_extra)
        value = locale.format_count(getattr(summary, name))
        value_size = fitted_size(value, 40, avail, mono=True, min_size=14)
        canvas.text(x, y, value, value_size, "pink", 600, mono=True)
        for row, line in enumerate(lines):
            canvas.text(x, y + 28 + row * 23, line, label_size, "muted")

    metadata = (
        ("joined_label", locale.format_date(summary.joined)),
        (
            "last_push_label",
            MISSING if summary.last_push is None else locale.format_date(summary.last_push),
        ),
        ("archived_label", str(summary.archived)),
        ("forked_label", str(summary.forked)),
    )
    value_offset = 290 if compact else 280
    for index, (label, value) in enumerate(metadata):
        x = 24 if compact else 24 + index // 2 * 480
        y = (475 + index * 32 if compact else 265 + index % 2 * 34) + grid_extra
        meta_label = locale.text(label)
        meta_size = fitted_size(meta_label, 17, value_offset - 12, min_size=9)
        value_right = width - 14 if compact or index >= 2 else 504 - 16
        value_size = fitted_size(value, 17, value_right - (x + value_offset), mono=True, min_size=9)
        canvas.text(x, y, meta_label, meta_size, "muted")
        canvas.text(x + value_offset, y, value, value_size, "violet", mono=True)

    top = (619 if compact else 343) + grid_extra
    lang_heading = locale.text("language_stats_label")
    lang_head_size = fitted_size(lang_heading, 18, width - 38, min_size=10)
    canvas.text(24, top, lang_heading, lang_head_size, weight=600)
    for row, line in enumerate(lang_lines):
        canvas.text(24, top + 27 + row * 22, line, 17, "muted")

    canvas.path(f"M24 {divider_y}H{width - 24}", "border")
    updated = f"{locale.text('updated_label')} {locale.format_timestamp(summary.fetched_at)}"
    updated_size = fitted_size(updated, 15, width - 38, min_size=10)
    canvas.text(24, divider_y + 26, updated, updated_size, "violet")
    for row, line in enumerate(scope_lines):
        canvas.text(24, divider_y + 50 + row * 18, line, 14, "muted")
    return canvas.finish()


def statistics_alt(summary: Summary, locale: Locale) -> str:
    """Return the accessible description of the statistics panel."""
    values = "; ".join(
        f"{locale.text(f'{name}_label')}: {getattr(summary, name)}" for name in STATISTICS
    )
    timestamp = locale.format_timestamp(summary.fetched_at)
    return f"{locale.text('profile_heading')}. {values}. {timestamp}"


def knowledge(locale: Locale, theme: Theme, background: str, *, compact: bool) -> str:
    """Render the engineering principles and interests lists."""
    width = _width(compact)
    avail = width - 38
    item_size = 22 if compact else 19
    budget = wrap_budget(avail, item_size)
    strengths = wrap(" / ".join(locale.items("softskills_items")), budget)
    interests = wrap(" / ".join(locale.items("interests_items")), budget)
    line_height = 29 if compact else 27
    second_top = 62 + len(strengths) * line_height + 39
    height = second_top + 30 + len(interests) * line_height + 19

    canvas = Canvas(width, height, knowledge_alt(locale), theme)
    canvas.background(background, theme.overlays["knowledge"])
    canvas.frame("pink")
    softskills_head = locale.text("softskills_label")
    canvas.text(
        24, 37, softskills_head, fitted_size(softskills_head, 22, avail, min_size=10), "violet", 600
    )
    for index, line in enumerate(strengths):
        canvas.text(24, 72 + index * line_height, line, item_size)
    canvas.path(f"M24 {second_top - 26}H{width - 24}", "border")
    interests_head = locale.text("interests_label")
    canvas.text(
        24,
        second_top + 3,
        interests_head,
        fitted_size(interests_head, 22, avail, min_size=10),
        "violet",
        600,
    )
    for index, line in enumerate(interests):
        canvas.text(24, second_top + 39 + index * line_height, line, item_size)
    return canvas.finish()


def knowledge_alt(locale: Locale) -> str:
    """Return the accessible description of the knowledge panel."""
    return (
        f"{locale.text('knowledge_heading')}. "
        f"{', '.join(locale.items('softskills_items'))}. "
        f"{', '.join(locale.items('interests_items'))}"
    )


def skill_group_header(index: int, title: str, count: int, theme: Theme, *, compact: bool) -> str:
    """Render a category sub-header bar for a group of skill badges."""
    width = _width(compact)
    canvas = Canvas(width, 42, title, theme)
    canvas.frame("violet")
    canvas.path("M3 3H45V39H3Z", "grid", fill="grid")
    canvas.path("M48 10v22", "border")
    canvas.text(16, 26, f"{index:02}", 13, "pink", 600, mono=True)
    title_size = fitted_size(title, 16, max(width - 140, 60), min_size=10)
    canvas.text(62, 26, title, title_size, weight=600)
    canvas.path(f"M{width - 66} 10v22", "border")
    canvas.text(width - 25, 26, f"{count:02}", 13, "violet", 600, mono=True, anchor="end")
    return canvas.finish()


def section_header(number: int, title: str, theme: Theme, *, compact: bool) -> str:
    """Render a numbered section heading with an anchor arrow."""
    width = _width(compact)
    canvas = Canvas(width, 68, title, theme)
    canvas.path(f"M0 67H{width}", "border")
    canvas.path("M0 67H112", "pink", width=2.0)
    canvas.text(0, 43, f"{number:02}", 17, "pink", mono=True)
    canvas.text(48, 44, title, fitted_size(title, 26, width - 83, min_size=10), weight=600)
    canvas.arrow(width - 23, 31)
    return canvas.finish()
