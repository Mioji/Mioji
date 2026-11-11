# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Collection of a new snapshot from the GitHub REST API.

Collection is all-or-nothing: any surprise in the responses raises
before the snapshot file is touched, so the committed snapshot is only
ever replaced by a complete and consistent one. Beyond schema checks,
the responses are cross-checked against each other, because the API is
eventually consistent and a partially paginated listing would otherwise
render plausible but wrong totals.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from profilegen.client import PAGE_SIZE
from profilegen.config import Project
from profilegen.errors import SnapshotError
from profilegen.models import Event, Repository, Snapshot, parse_timestamp
from profilegen.validation import Validator, child

MAX_REPOSITORY_PAGES: Final = 10
"""Up to 1000 repositories; beyond that the listing is rejected rather
than silently truncated, because totals are shown on the dashboard."""
EVENT_PAGES: Final = 3
"""GitHub serves at most three pages of 100 events per feed."""
MAX_CLOCK_SKEW: Final = timedelta(seconds=30)
"""Small tolerance for runner clock lag or minor backward NTP steps."""


class Client(Protocol):
    """Subset of :class:`profilegen.client.GitHubClient` used here."""

    def get(self, path: str) -> object:
        """Fetch one JSON resource."""
        ...

    def get_pages(self, path: str, *, max_pages: int, allow_truncation: bool) -> list[object]:
        """Fetch a paginated JSON collection."""
        ...


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _login(v: Validator, value: object, where: str) -> str:
    data = v.mapping(value, where)
    return v.string(data.get("login"), child(where, "login"), max_length=39)


def _repository(v: Validator, value: object, where: str) -> Repository:
    """Convert a REST repository object into a snapshot record."""
    data = v.mapping(value, where)
    if data.get("private") is not False:
        v.fail(child(where, "private"), "only public repositories may be collected")
    language = data.get("language")
    pushed_at = data.get("pushed_at")
    if "language" not in data or "pushed_at" not in data:
        v.fail(where, "language and pushed_at must be present, even if null")
    return Repository.from_json(
        v,
        {
            "name": data.get("full_name"),
            "stars": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "open_items": data.get("open_issues_count"),
            "fork": data.get("fork"),
            "archived": data.get("archived"),
            "language": language,
            "pushed_at": pushed_at,
        },
        where,
    )


def collect(
    client: Client,
    username: str,
    projects: Sequence[Project],
    *,
    now: Callable[[], datetime] = _now,
) -> Snapshot:
    """Fetch public data for ``username`` and the catalog projects.

    Private catalog entries are never requested, so the token needs no
    access to them and their existence is not probed.

    Args:
        client: API client.
        username: Login from the profile configuration.
        projects: Catalog entries; public ones not owned by
            ``username`` are fetched individually.
        now: Clock returning the current UTC time, injectable for tests.

    Returns:
        The validated snapshot.

    Raises:
        SnapshotError: If responses are inconsistent or malformed.
        NetworkError: If a request fails.
    """
    v = Validator("GitHub API", SnapshotError)
    expected = username.casefold()
    started_at = now()

    user = v.mapping(client.get(f"/users/{username}"), "user")
    if _login(v, user, "user").casefold() != expected:
        v.fail("user.login", f"response does not belong to {username}")
    # Validate user fields and repository cap before fetching pages so
    # an oversized or malformed account fails without wasted requests.
    public_repos = v.integer(user.get("public_repos"), "user.public_repos")
    repo_limit = MAX_REPOSITORY_PAGES * PAGE_SIZE
    if public_repos > repo_limit:
        v.fail(
            "user.public_repos",
            f"account has {public_repos} public repositories, exceeding the {repo_limit} limit",
        )
    followers = v.integer(user.get("followers"), "user.followers")
    following = v.integer(user.get("following"), "user.following")
    gists = v.integer(user.get("public_gists"), "user.public_gists")
    created_at = parse_timestamp(v, user.get("created_at"), "user.created_at")

    raw_repositories = client.get_pages(
        f"/users/{username}/repos?type=owner&sort=full_name",
        max_pages=MAX_REPOSITORY_PAGES,
        allow_truncation=False,
    )
    raw_events = client.get_pages(
        f"/users/{username}/events/public", max_pages=EVENT_PAGES, allow_truncation=True
    )

    # public_repos and the listing come from different requests. A
    # mismatch means a repository was created, deleted or had its
    # visibility changed mid-collection; the next run will be consistent.
    if len(raw_repositories) != public_repos:
        v.fail(
            "repositories",
            f"listing has {len(raw_repositories)} entries but the account reports "
            f"{public_repos}; the data changed during collection, retry later",
        )

    repositories: list[Repository] = []
    for index, item in enumerate(raw_repositories):
        where = child("repositories", index)
        owner = _login(v, v.mapping(item, where).get("owner"), child(where, "owner"))
        if owner.casefold() != expected:
            v.fail(child(where, "owner"), f"repository is not owned by {username}")
        repositories.append(_repository(v, item, where))

    events: list[Event] = []
    seen_events: set[str] = set()
    for index, item in enumerate(raw_events):
        where = child("events", index)
        data = v.mapping(item, where)
        if data.get("public") is not True:
            v.fail(child(where, "public"), "only public events may be collected")
        if _login(v, data.get("actor"), child(where, "actor")).casefold() != expected:
            v.fail(child(where, "actor"), f"event was not performed by {username}")
        repo = v.mapping(data.get("repo"), child(where, "repo"))
        parsed_event = Event.from_json(
            v,
            {
                "id": data.get("id"),
                "type": data.get("type"),
                "repository": repo.get("name"),
                "created_at": data.get("created_at"),
            },
            where,
        )
        if parsed_event.id not in seen_events:
            seen_events.add(parsed_event.id)
            events.append(parsed_event)

    owned = {repository.name.casefold() for repository in repositories}
    external: list[Repository] = []
    for project in projects:
        key = project.repository.casefold()
        if project.visibility != "public" or key in owned:
            continue
        record = _repository(v, client.get(f"/repos/{project.repository}"), project.repository)
        if record.name.casefold() != key:
            v.fail(
                project.repository,
                f"GitHub returned {record.name}; the repository was renamed, "
                "update configs/profile.json",
            )
        external.append(record)
        owned.add(key)

    finished_at = now()
    observed = [started_at, created_at]
    observed.extend(repo.pushed_at for repo in (*repositories, *external) if repo.pushed_at)
    observed.extend(event.created_at for event in events)
    latest_observed = max(observed)
    skew = latest_observed - finished_at
    fetched_at = latest_observed if timedelta(0) < skew <= MAX_CLOCK_SKEW else finished_at
    return Snapshot(
        username=username,
        collection_started_at=started_at,
        fetched_at=fetched_at,
        followers=followers,
        following=following,
        gists=gists,
        created_at=created_at,
        repositories=tuple(repositories),
        project_repositories=tuple(external),
        events=tuple(events),
    )
