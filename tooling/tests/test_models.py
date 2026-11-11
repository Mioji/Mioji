# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Snapshot schema, invariants and summary aggregation."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from profilegen import paths
from profilegen.errors import SnapshotError
from profilegen.models import (
    MAX_EVENTS,
    SUMMARY_PLACEHOLDERS,
    Event,
    Repository,
    Snapshot,
    Summary,
)
from profilegen.validation import parse_json
from tests import factories


def test_committed_snapshot_round_trips_byte_for_byte() -> None:
    text = (paths.REPOSITORY_ROOT / paths.SNAPSHOT).read_text(encoding="utf-8")
    snapshot = Snapshot.from_json(factories.read(paths.REPOSITORY_ROOT / paths.SNAPSHOT))
    assert snapshot.dumps() == text


def _set(path: str, value: object) -> Any:
    def mutate(document: dict[str, Any]) -> None:
        target: Any = document
        *parents, last = path.split(".")
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        if value is KeyError:
            del target[int(last) if last.isdigit() else last]
        else:
            target[int(last) if last.isdigit() else last] = value

    return mutate


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(_set("followers", None), id="null-count"),
        pytest.param(_set("followers", True), id="boolean-count"),
        pytest.param(_set("followers", -1), id="negative-count"),
        pytest.param(_set("followers", "0"), id="string-count"),
        pytest.param(_set("followers", 0.0), id="float-count"),
        pytest.param(_set("followers", KeyError), id="missing-key"),
        pytest.param(_set("extra", 1), id="unknown-key"),
        pytest.param(_set("schema_version", 3), id="schema-version"),
        pytest.param(_set("created_at", "bad"), id="bad-timestamp"),
        pytest.param(_set("created_at", "2026-7-30T03:50:27Z"), id="lax-timestamp"),
        pytest.param(_set("created_at", "٢٠٢٦-١١-١٠T٠٩:٠٠:٠٠Z"), id="unicode-digits-timestamp"),
        pytest.param(_set("created_at", "2026-02-30T00:00:00Z"), id="impossible-date"),
        pytest.param(_set("created_at", "2026-11-11T00:00:00Z"), id="created-after-fetch"),
        pytest.param(_set("collection_started_at", "2026-11-10T10:01:00Z"), id="start-after-end"),
        pytest.param(_set("collection_started_at", "2026-11-10T09:00:00Z"), id="slow-collection"),
        pytest.param(_set("username", "bad name"), id="bad-username"),
        pytest.param(_set("repositories.0.name", "Other/Mioji"), id="foreign-owner"),
        pytest.param(_set("repositories.0.name", "Mioji/.."), id="traversal-slug"),
        pytest.param(_set("repositories.0.name", "Mioji/a\nb"), id="control-character"),
        pytest.param(_set("repositories.0.language", "Rust\u2028X"), id="line-separator"),
        pytest.param(_set("repositories.0.language", "Rust\u202eX"), id="bidi-override"),
        pytest.param(_set("repositories.0.language", "Rust\u2066X"), id="bidi-isolate"),
        pytest.param(_set("repositories.0.language", KeyError), id="missing-language"),
        pytest.param(_set("repositories.0.language", ""), id="empty-language"),
        pytest.param(_set("repositories.0.fork", 0), id="integer-flag"),
        pytest.param(_set("repositories.0.pushed_at", "2026-12-01T00:00:00Z"), id="future-push"),
        pytest.param(_set("events", [factories.event(), factories.event()]), id="duplicate-event"),
        pytest.param(_set("events", [factories.event("x")]), id="non-numeric-event-id"),
        pytest.param(
            _set("events", [factories.event(created_at="2026-11-11T00:00:00Z")]), id="future-event"
        ),
        pytest.param(
            _set("repositories", [factories.repository(), factories.repository()]),
            id="duplicate-repository",
        ),
        pytest.param(
            _set("project_repositories", [factories.repository(name="Mioji/Owned")]),
            id="owned-catalog-repository",
        ),
    ],
)
def test_invalid_snapshots_are_rejected(mutation: Any) -> None:
    document = factories.snapshot()
    mutation(document)
    with pytest.raises(SnapshotError):
        Snapshot.from_json(document)


def test_summary_uses_utc_calendar_days() -> None:
    document = factories.snapshot(
        events=[
            factories.event("3", "2026-11-10T09:00:00Z"),
            factories.event("2", "2026-10-12T00:00:00Z"),
            factories.event("1", "2026-10-11T23:59:59Z"),
        ]
    )
    summary = Summary.from_snapshot(Snapshot.from_json(document))
    assert summary.window[0] == date(2026, 10, 12)
    assert summary.window[-1] == date(2026, 11, 10)
    assert len(summary.daily_events) == 30
    assert (summary.daily_events[0], summary.daily_events[-1]) == (1, 1)
    assert (summary.events, summary.active_days, summary.event_repositories) == (2, 2, 1)


def test_summary_separates_owned_and_catalog_totals() -> None:
    document = factories.snapshot(
        repositories=[
            factories.repository(stars=3, open_items=2, language="Python"),
            factories.repository(name="Mioji/fork", fork=True, archived=True, language="Rust"),
        ],
        project_repositories=[factories.repository(name="Example/Library", stars=5)],
    )
    summary = Summary.from_snapshot(Snapshot.from_json(document))
    assert (summary.stars, summary.project_stars) == (3, 8)
    assert (summary.originals, summary.forked, summary.archived) == (1, 1, 1)
    assert summary.languages == (("Python", 1), ("Rust", 1))
    assert summary.repository_stars["example/library"] == 5


def test_empty_account_produces_zero_summary() -> None:
    summary = Summary.from_snapshot(Snapshot.from_json(factories.snapshot(repositories=[])))
    assert (summary.repositories, summary.stars, summary.events) == (0, 0, 0)
    assert summary.last_push is None


def test_declared_placeholders_match_summary_values() -> None:
    summary = Summary.from_snapshot(Snapshot.from_json(factories.snapshot()))
    assert set(summary.placeholders()) == SUMMARY_PLACEHOLDERS


def test_snapshot_is_immutable() -> None:
    snapshot = Snapshot.from_json(factories.snapshot())
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.followers = 1  # type: ignore[misc]


def test_snapshot_enforces_event_limit_and_aware_timestamps() -> None:
    base = Snapshot.from_json(factories.snapshot())
    instant = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    too_many = tuple(
        Event(id=str(index + 1), type="PushEvent", repository="Mioji/Mioji", created_at=instant)
        for index in range(MAX_EVENTS + 1)
    )
    with pytest.raises(SnapshotError, match=str(MAX_EVENTS)):
        dataclasses.replace(base, events=too_many)

    with pytest.raises(SnapshotError, match="timezone-aware"):
        dataclasses.replace(base, fetched_at=base.fetched_at.replace(tzinfo=None))


def test_summary_normalizes_non_utc_offsets_to_utc_calendar_day() -> None:
    base = Snapshot.from_json(factories.snapshot())
    plus_five = timezone(timedelta(hours=5))
    # 2026-11-10 02:00+05:00 equals 2026-11-09 21:00Z (previous UTC day).
    offset_time = datetime(2026, 11, 10, 2, 0, tzinfo=plus_five)
    snapshot = dataclasses.replace(
        base,
        collection_started_at=offset_time - timedelta(seconds=10),
        fetched_at=offset_time,
        created_at=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
        events=(),
    )
    summary = Summary.from_snapshot(snapshot)
    assert summary.window[-1] == date(2026, 11, 9)


def test_repository_event_and_snapshot_enforce_invariants_directly() -> None:
    now = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    naive = now.replace(tzinfo=None)
    with pytest.raises(SnapshotError):
        Repository("../bad", 0, 0, 0, False, False, None, None)
    with pytest.raises(SnapshotError):
        Repository("Mioji/repo", -1, 0, 0, False, False, None, None)
    with pytest.raises(SnapshotError):
        Repository("Mioji/repo", 0, -1, 0, False, False, None, None)
    with pytest.raises(SnapshotError):
        Repository("Mioji/repo", 0, 0, -1, False, False, None, None)
    with pytest.raises(SnapshotError):
        Repository("Mioji/repo", 0, 0, 0, False, False, None, naive)

    with pytest.raises(SnapshotError):
        Event("abc", "PushEvent", "Mioji/repo", now)
    with pytest.raises(SnapshotError):
        Event("1", "bad", "Mioji/repo", now)
    with pytest.raises(SnapshotError):
        Event("1", "PushEvent", "not-a-slug", now)
    with pytest.raises(SnapshotError):
        Event("1", "PushEvent", "Mioji/repo", naive)

    base = Snapshot.from_json(factories.snapshot())
    with pytest.raises(SnapshotError, match="followers"):
        dataclasses.replace(base, followers=-1)
    with pytest.raises(SnapshotError, match="following"):
        dataclasses.replace(base, following=-1)
    with pytest.raises(SnapshotError, match="gists"):
        dataclasses.replace(base, gists=-1)


def test_parse_json_wraps_excessive_nesting() -> None:
    deep = "[" * 250_000 + "]" * 250_000
    with pytest.raises(SnapshotError, match="nesting"):
        parse_json(deep, source="deep", error=SnapshotError)


def test_summary_handles_leap_day_in_window() -> None:
    leap_next = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    snapshot = Snapshot(
        username="Mioji",
        collection_started_at=leap_next - timedelta(seconds=5),
        fetched_at=leap_next,
        followers=0,
        following=0,
        gists=0,
        created_at=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        repositories=(),
        project_repositories=(),
        events=(Event("1", "PushEvent", "Mioji/repo", datetime(2024, 2, 29, 23, 59, tzinfo=UTC)),),
    )
    summary = Summary.from_snapshot(snapshot)
    assert summary.window[0] == date(2024, 2, 1)
    assert date(2024, 2, 29) in summary.window
    assert summary.daily_events[summary.window.index(date(2024, 2, 29))] == 1
