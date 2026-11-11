# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Snapshot collection and cross-checks of API responses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from profilegen.collect import collect
from profilegen.config import Project
from profilegen.errors import SnapshotError
from tests import factories


class FakeClient:
    """Serves canned API responses and records requested paths."""

    def __init__(
        self,
        user: object,
        repositories: list[object],
        events: list[object],
        resources: dict[str, object] | None = None,
    ) -> None:
        self.responses = {"/users/Mioji": user, **(resources or {})}
        self.collections = {"repos": repositories, "events": events}
        self.paths: list[str] = []

    def get(self, path: str) -> object:
        self.paths.append(path)
        return self.responses[path]

    def get_pages(self, path: str, *, max_pages: int, allow_truncation: bool) -> list[object]:
        self.paths.append(path)
        return self.collections["events" if "/events/" in path else "repos"]


def _project(repository: str, visibility: str) -> Project:
    return Project(
        repository=repository,
        description_key="pattsys",
        status="active",
        visibility="public" if visibility == "public" else "private",
        background_key="pattsys",
    )


def _now() -> datetime:
    return datetime(2026, 11, 10, 10, 0, tzinfo=UTC)


def test_collects_owned_data_and_public_catalog_projects() -> None:
    library = factories.api_repository(
        full_name="Example/Library", owner={"login": "Example"}, stargazers_count=7
    )
    fake = FakeClient(
        factories.api_user(),
        [factories.api_repository(stargazers_count=3, language="Python")],
        [factories.api_event()],
        {"/repos/Example/Library": library},
    )
    projects = [
        _project("Example/Closed", "private"),
        _project("Example/Library", "public"),
        _project("Mioji/Mioji", "public"),
    ]
    snapshot = collect(fake, "Mioji", projects, now=_now)

    assert "/repos/Example/Closed" not in fake.paths
    assert "/repos/Mioji/Mioji" not in fake.paths
    assert [repo.name for repo in snapshot.project_repositories] == ["Example/Library"]
    assert (snapshot.followers, snapshot.following, snapshot.gists) == (2, 3, 4)
    assert snapshot.repositories[0].stars == 3
    assert snapshot.events[0].repository == "Mioji/Mioji"


def test_duplicate_events_across_pagination_shifts_are_deduplicated() -> None:
    event_1 = factories.api_event(event_id="101")
    event_2 = factories.api_event(event_id="102")
    fake = FakeClient(
        factories.api_user(), [factories.api_repository()], [event_1, event_2, event_1]
    )
    snapshot = collect(fake, "Mioji", [], now=_now)
    assert [event.id for event in snapshot.events] == ["101", "102"]


@pytest.mark.parametrize(
    ("user", "repositories", "events", "message"),
    [
        (factories.api_user(login=None), [], [], "login"),
        (factories.api_user(login="Other"), [], [], "does not belong"),
        (factories.api_user(public_repos=2), [factories.api_repository()], [], "retry later"),
        (factories.api_user(), [factories.api_repository(private=True)], [], "public"),
        (
            factories.api_user(),
            [factories.api_repository(owner={"login": "Other"})],
            [],
            "not owned",
        ),
        (factories.api_user(), [factories.api_repository()], [{"public": False}], "public"),
        (
            factories.api_user(),
            [factories.api_repository()],
            [factories.api_event(actor={"login": "Other"})],
            "not performed",
        ),
    ],
)
def test_inconsistent_responses_are_rejected(
    user: object, repositories: list[object], events: list[object], message: str
) -> None:
    with pytest.raises(SnapshotError, match=message):
        collect(FakeClient(user, repositories, events), "Mioji", [], now=_now)


def test_renamed_catalog_repository_is_reported() -> None:
    renamed = factories.api_repository(full_name="Example/New", owner={"login": "Example"})
    fake = FakeClient(
        factories.api_user(),
        [factories.api_repository()],
        [],
        {"/repos/Example/Old": renamed},
    )
    with pytest.raises(SnapshotError, match="renamed"):
        collect(fake, "Mioji", [_project("Example/Old", "public")], now=_now)


def test_fails_fast_when_user_payload_exceeds_repo_limit_or_is_invalid() -> None:
    over_limit = FakeClient(factories.api_user(public_repos=1001), [], [])
    with pytest.raises(SnapshotError, match="exceeding"):
        collect(over_limit, "Mioji", [], now=_now)
    assert over_limit.paths == ["/users/Mioji"]

    bad_user = FakeClient(factories.api_user(followers=-1), [], [])
    with pytest.raises(SnapshotError, match="followers"):
        collect(bad_user, "Mioji", [], now=_now)
    assert bad_user.paths == ["/users/Mioji"]


def test_missing_language_or_pushed_at_in_api_repository_fails() -> None:
    repo = factories.api_repository()
    del repo["language"]
    with pytest.raises(SnapshotError, match="language"):
        collect(FakeClient(factories.api_user(), [repo], []), "Mioji", [], now=_now)


def test_absorbs_small_clock_skew_and_backward_ntp_step() -> None:
    base = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)
    ahead = (base + timedelta(seconds=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = FakeClient(
        factories.api_user(),
        [factories.api_repository(pushed_at=ahead)],
        [factories.api_event(created_at=ahead)],
    )
    ticks = iter([base + timedelta(seconds=5), base + timedelta(seconds=2)])
    snapshot = collect(fake, "Mioji", [], now=lambda: next(ticks))
    assert snapshot.fetched_at == base + timedelta(seconds=15)


def test_rejects_large_clock_skew_beyond_tolerance() -> None:
    base = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)
    far_ahead = (base + timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = FakeClient(
        factories.api_user(),
        [factories.api_repository(pushed_at=far_ahead)],
        [],
    )
    with pytest.raises(SnapshotError, match="pushed after"):
        collect(fake, "Mioji", [], now=_now)
