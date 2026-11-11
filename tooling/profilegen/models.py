# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Self-validating model of the public GitHub snapshot and its summary.

``configs/data/snapshot.json`` is the only renderer input that changes
over time. ``--fetch`` writes it and the repository commits it, which is
what makes every generated file reproducible offline. This module owns
its schema in both directions:

* :meth:`Snapshot.from_json` accepts only the documented shape, so a
  hand-edited or truncated snapshot fails loudly instead of rendering
  wrong numbers;
* :meth:`Snapshot.dumps` emits the canonical text. Key order,
  indentation and timestamp format are fixed, which keeps refresh diffs
  minimal and lets ``--check`` compare bytes.

Invariants spanning several fields (timestamp ordering, ownership,
uniqueness) are enforced in ``__post_init__`` so they also hold for
snapshots assembled directly from API responses by
:mod:`profilegen.collect`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

from profilegen.errors import SnapshotError
from profilegen.validation import Validator, child

SCHEMA_VERSION: Final = 2
ACTIVITY_WINDOW_DAYS: Final = 30
MAX_EVENTS: Final = 300
"""GitHub serves at most 300 events per feed; more indicates corruption."""
MAX_COLLECTION_TIME: Final = timedelta(minutes=10)
"""Upper bound on one fetch; a longer span means the data is not a
consistent point-in-time view and should be collected again."""

TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
# strptime accepts single-digit fields such as "2026-1-5", which would
# not survive a round trip through TIMESTAMP_FORMAT, so the exact shape
# is enforced with a regular expression first.
_TIMESTAMP_SHAPE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

LOGIN_PATTERN: Final = re.compile(r"[A-Za-z0-9-]{1,39}")
SLUG_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
_EVENT_ID: Final = re.compile(r"[0-9]{1,20}")
_EVENT_TYPE: Final = re.compile(r"[A-Z][A-Za-z]{0,62}Event")

_REPOSITORY_KEYS: Final = (
    "name",
    "stars",
    "forks",
    "open_items",
    "fork",
    "archived",
    "language",
    "pushed_at",
)
_EVENT_KEYS: Final = ("id", "type", "repository", "created_at")
_SNAPSHOT_KEYS: Final = (
    "schema_version",
    "username",
    "collection_started_at",
    "fetched_at",
    "followers",
    "following",
    "gists",
    "created_at",
    "repositories",
    "project_repositories",
    "events",
)


def is_slug(value: str) -> bool:
    """Return whether ``value`` is a safe ``owner/name`` repository slug.

    The character set matches what GitHub allows. The ``.`` and ``..``
    components are excluded explicitly because slugs are interpolated
    into API paths, where they would act as path traversal.

    Args:
        value: Candidate slug.

    Returns:
        ``True`` if the slug is well formed.
    """
    if SLUG_PATTERN.fullmatch(value) is None:
        return False
    return not any(part in {".", ".."} for part in value.split("/"))


def parse_timestamp(validator: Validator, value: object, where: str) -> datetime:
    """Parse a second-precision UTC timestamp in GitHub's format.

    Args:
        validator: Validator bound to the containing document.
        value: Raw decoded value, for example ``"2026-11-10T17:35:50Z"``.
        where: JSON path of the value.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Raises:
        ProfileError: If the value is not a timestamp of that exact
            shape or names an impossible date.
    """
    text = validator.string(value, where, pattern=_TIMESTAMP_SHAPE)
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        validator.fail(where, f"{text!r} is not a valid UTC timestamp")


def format_timestamp(value: datetime) -> str:
    """Serialise a UTC ``datetime`` in the snapshot timestamp format."""
    return value.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


@dataclass(frozen=True, slots=True)
class Repository:
    """Public metadata of one repository.

    Attributes:
        name: ``owner/name`` slug with GitHub's capitalisation.
        stars: Stargazer count.
        forks: Fork count.
        open_items: Open issues plus open pull requests, which GitHub
            reports as a single ``open_issues_count`` value.
        fork: Whether the repository is itself a fork.
        archived: Whether the repository is archived.
        language: Primary language, or ``None`` if GitHub detected none.
        pushed_at: Time of the last push, or ``None`` for an empty
            repository.
    """

    name: str
    stars: int
    forks: int
    open_items: int
    fork: bool
    archived: bool
    language: str | None
    pushed_at: datetime | None

    def __post_init__(self) -> None:
        """Enforce field invariants regardless of how the record was built."""
        problems: list[str] = []
        if not is_slug(self.name):
            problems.append(f"invalid slug {self.name!r}")
        for label, value in (
            ("stars", self.stars),
            ("forks", self.forks),
            ("open_items", self.open_items),
        ):
            if value < 0:
                problems.append(f"{label} must be non-negative")
        if self.pushed_at is not None and self.pushed_at.tzinfo is None:
            problems.append("pushed_at must be timezone-aware")
        if problems:
            raise SnapshotError("repository: " + "; ".join(problems))

    @property
    def owner(self) -> str:
        """Owner login part of :attr:`name`."""
        return self.name.split("/", 1)[0]

    @classmethod
    def from_json(cls, v: Validator, value: object, where: str) -> Repository:
        """Parse a snapshot repository record.

        Args:
            v: Validator bound to the snapshot document.
            value: Raw decoded record.
            where: JSON path of the record.

        Returns:
            The parsed repository.

        Raises:
            ProfileError: If any field is missing, unknown or invalid.
        """
        data = v.record(value, where, required=_REPOSITORY_KEYS)
        name = v.string(data["name"], child(where, "name"), max_length=201)
        if not is_slug(name):
            v.fail(child(where, "name"), f"{name!r} is not a repository slug")
        language = data["language"]
        pushed_at = data["pushed_at"]
        return cls(
            name=name,
            stars=v.integer(data["stars"], child(where, "stars")),
            forks=v.integer(data["forks"], child(where, "forks")),
            open_items=v.integer(data["open_items"], child(where, "open_items")),
            fork=v.boolean(data["fork"], child(where, "fork")),
            archived=v.boolean(data["archived"], child(where, "archived")),
            language=None
            if language is None
            else v.string(language, child(where, "language"), max_length=80),
            pushed_at=None
            if pushed_at is None
            else parse_timestamp(v, pushed_at, child(where, "pushed_at")),
        )

    def to_json(self) -> dict[str, object]:
        """Return the canonical snapshot record with a fixed key order."""
        return {
            "name": self.name,
            "stars": self.stars,
            "forks": self.forks,
            "open_items": self.open_items,
            "fork": self.fork,
            "archived": self.archived,
            "language": self.language,
            "pushed_at": None if self.pushed_at is None else format_timestamp(self.pushed_at),
        }


@dataclass(frozen=True, slots=True)
class Event:
    """One entry of the public events feed.

    Attributes:
        id: Numeric identifier, kept as a string because GitHub returns
            it as one and the values exceed what JSON consumers can
            represent exactly as numbers.
        type: Event type such as ``PushEvent``.
        repository: Slug of the repository the event belongs to.
        created_at: Time the event was recorded.
    """

    id: str
    type: str
    repository: str
    created_at: datetime

    def __post_init__(self) -> None:
        """Enforce field invariants regardless of how the event was built."""
        problems: list[str] = []
        if _EVENT_ID.fullmatch(self.id) is None:
            problems.append(f"invalid id {self.id!r}")
        if _EVENT_TYPE.fullmatch(self.type) is None:
            problems.append(f"invalid type {self.type!r}")
        if not is_slug(self.repository):
            problems.append(f"invalid repository {self.repository!r}")
        if self.created_at.tzinfo is None:
            problems.append("created_at must be timezone-aware")
        if problems:
            raise SnapshotError("event: " + "; ".join(problems))

    @classmethod
    def from_json(cls, v: Validator, value: object, where: str) -> Event:
        """Parse a snapshot event record.

        Args:
            v: Validator bound to the snapshot document.
            value: Raw decoded record.
            where: JSON path of the record.

        Returns:
            The parsed event.

        Raises:
            ProfileError: If any field is missing, unknown or invalid.
        """
        data = v.record(value, where, required=_EVENT_KEYS)
        repository = v.string(data["repository"], child(where, "repository"), max_length=201)
        if not is_slug(repository):
            v.fail(child(where, "repository"), f"{repository!r} is not a slug")
        return cls(
            id=v.string(data["id"], child(where, "id"), pattern=_EVENT_ID),
            type=v.string(data["type"], child(where, "type"), pattern=_EVENT_TYPE),
            repository=repository,
            created_at=parse_timestamp(v, data["created_at"], child(where, "created_at")),
        )

    def to_json(self) -> dict[str, object]:
        """Return the canonical snapshot record with a fixed key order."""
        return {
            "id": self.id,
            "type": self.type,
            "repository": self.repository,
            "created_at": format_timestamp(self.created_at),
        }


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Point-in-time view of the public data of one GitHub account.

    Attributes:
        username: Account login as written in the profile configuration.
        collection_started_at: Time the fetch began.
        fetched_at: Time the fetch completed. All derived dates,
            including the activity window, are relative to this value
            rather than to the wall clock, which keeps rendering
            deterministic.
        followers: Follower count.
        following: Count of followed accounts.
        gists: Public gist count.
        created_at: Account creation time.
        repositories: Public repositories owned by the account.
        project_repositories: Public catalog repositories owned by
            someone else, fetched individually for their star counts.
        events: Public events feed, newest first, at most
            :data:`MAX_EVENTS` entries.

    Raises:
        SnapshotError: From ``__post_init__`` if a cross-field invariant
            does not hold.
    """

    username: str
    collection_started_at: datetime
    fetched_at: datetime
    followers: int
    following: int
    gists: int
    created_at: datetime
    repositories: tuple[Repository, ...]
    project_repositories: tuple[Repository, ...]
    events: tuple[Event, ...]

    def __post_init__(self) -> None:
        """Enforce invariants that no single field can express."""
        problems: list[str] = []
        if LOGIN_PATTERN.fullmatch(self.username) is None:
            problems.append(f"invalid username {self.username!r}")
        for label, value in (
            ("followers", self.followers),
            ("following", self.following),
            ("gists", self.gists),
        ):
            if value < 0:
                problems.append(f"{label} must be non-negative")
        naive = [
            label
            for label, moment in (
                ("collection_started_at", self.collection_started_at),
                ("fetched_at", self.fetched_at),
                ("created_at", self.created_at),
            )
            if moment.tzinfo is None
        ]
        if naive:
            problems.append(" and ".join(naive) + " must be timezone-aware")
        else:
            elapsed = self.fetched_at - self.collection_started_at
            if not timedelta(0) <= elapsed <= MAX_COLLECTION_TIME:
                problems.append("collection interval is negative or too long")
            if self.created_at > self.fetched_at:
                problems.append("account creation date is after the snapshot")
        if len(self.events) > MAX_EVENTS:
            problems.append(f"more than {MAX_EVENTS} events")

        owner = self.username.casefold()
        names: set[str] = set()
        problems.extend(
            f"{repository.name} is not owned by {self.username}"
            for repository in self.repositories
            if repository.owner.casefold() != owner
        )
        problems.extend(
            f"catalog repository {repository.name} is owned by {self.username}"
            for repository in self.project_repositories
            if repository.owner.casefold() == owner
        )
        fetched_aware = self.fetched_at.tzinfo is not None
        for repository in (*self.repositories, *self.project_repositories):
            key = repository.name.casefold()
            if key in names:
                problems.append(f"duplicate repository {repository.name}")
            names.add(key)
            if repository.pushed_at is not None:
                if repository.pushed_at.tzinfo is None:
                    problems.append(f"{repository.name} pushed_at must be timezone-aware")
                elif fetched_aware and repository.pushed_at > self.fetched_at:
                    problems.append(f"{repository.name} was pushed after the snapshot")

        event_ids: set[str] = set()
        for event in self.events:
            if event.id in event_ids:
                problems.append(f"duplicate event {event.id}")
            event_ids.add(event.id)
            if event.created_at.tzinfo is None:
                problems.append(f"event {event.id} created_at must be timezone-aware")
            elif fetched_aware and event.created_at > self.fetched_at:
                problems.append(f"event {event.id} is newer than the snapshot")

        if problems:
            raise SnapshotError("snapshot: " + "; ".join(problems))

    @classmethod
    def from_json(cls, value: object, *, source: str = "snapshot") -> Snapshot:
        """Parse and validate a decoded snapshot document.

        Args:
            value: Decoded JSON document.
            source: Name used as the prefix of error messages.

        Returns:
            The validated snapshot.

        Raises:
            SnapshotError: If the document violates the schema or any
                invariant.
        """
        v = Validator(source, SnapshotError)
        data = v.record(value, "", required=_SNAPSHOT_KEYS)
        version = v.integer(data["schema_version"], "schema_version")
        if version != SCHEMA_VERSION:
            v.fail("schema_version", f"unsupported version {version}")
        return cls(
            username=v.string(data["username"], "username", pattern=LOGIN_PATTERN),
            collection_started_at=parse_timestamp(
                v, data["collection_started_at"], "collection_started_at"
            ),
            fetched_at=parse_timestamp(v, data["fetched_at"], "fetched_at"),
            followers=v.integer(data["followers"], "followers"),
            following=v.integer(data["following"], "following"),
            gists=v.integer(data["gists"], "gists"),
            created_at=parse_timestamp(v, data["created_at"], "created_at"),
            repositories=tuple(
                Repository.from_json(v, item, child("repositories", index))
                for index, item in enumerate(v.array(data["repositories"], "repositories"))
            ),
            project_repositories=tuple(
                Repository.from_json(v, item, child("project_repositories", index))
                for index, item in enumerate(
                    v.array(data["project_repositories"], "project_repositories")
                )
            ),
            events=tuple(
                Event.from_json(v, item, child("events", index))
                for index, item in enumerate(
                    v.array(data["events"], "events", max_items=MAX_EVENTS)
                )
            ),
        )

    def to_json(self) -> dict[str, object]:
        """Return the canonical document with a fixed key order."""
        return {
            "schema_version": SCHEMA_VERSION,
            "username": self.username,
            "collection_started_at": format_timestamp(self.collection_started_at),
            "fetched_at": format_timestamp(self.fetched_at),
            "followers": self.followers,
            "following": self.following,
            "gists": self.gists,
            "created_at": format_timestamp(self.created_at),
            "repositories": [item.to_json() for item in self.repositories],
            "project_repositories": [item.to_json() for item in self.project_repositories],
            "events": [item.to_json() for item in self.events],
        }

    def dumps(self) -> str:
        """Return the canonical file content, newline-terminated.

        ``ensure_ascii=False`` keeps non-Latin language names readable
        in diffs; the file is always written as UTF-8.
        """
        return json.dumps(self.to_json(), ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True, slots=True)
class Summary:
    """Aggregates displayed by the dashboards, derived from a snapshot.

    Attributes:
        repositories: Count of owned public repositories.
        stars: Stars across owned repositories.
        forks: Forks across owned repositories.
        followers: Follower count.
        following: Count of followed accounts.
        gists: Public gist count.
        originals: Owned repositories that are not forks.
        forked: Owned repositories that are forks.
        archived: Owned repositories that are archived.
        open_items: Open issues and pull requests across owned
            repositories.
        joined: Account creation date.
        last_push: Date of the most recent push, or ``None``.
        languages: ``(language, repository count)`` pairs, most common
            first and alphabetical within a tie, so output is stable.
        window: The :data:`ACTIVITY_WINDOW_DAYS` UTC calendar dates that
            end on the snapshot date, oldest first.
        daily_events: Event count for each date of :attr:`window`.
        events: Events inside the window.
        active_days: Dates of the window with at least one event.
        event_repositories: Distinct repositories among those events.
        project_stars: Stars across owned and catalog repositories.
        fetched_at: Completion time of the snapshot.
        repository_stars: Star counts keyed by case-folded slug, for
            owned and catalog repositories alike.
    """

    repositories: int
    stars: int
    forks: int
    followers: int
    following: int
    gists: int
    originals: int
    forked: int
    archived: int
    open_items: int
    joined: date
    last_push: date | None
    languages: tuple[tuple[str, int], ...]
    window: tuple[date, ...]
    daily_events: tuple[int, ...]
    events: int
    active_days: int
    event_repositories: int
    project_stars: int
    fetched_at: datetime
    repository_stars: Mapping[str, int]

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> Summary:
        """Compute every aggregate in a single pass over the snapshot.

        The window uses UTC calendar dates, not a rolling 720-hour span,
        because the dashboard labels individual days and GitHub reports
        event times in UTC.

        Args:
            snapshot: Validated snapshot.

        Returns:
            The summary.
        """
        owned = snapshot.repositories
        end = snapshot.fetched_at.astimezone(UTC).date()
        window = tuple(
            end - timedelta(days=offset) for offset in range(ACTIVITY_WINDOW_DAYS - 1, -1, -1)
        )
        in_window = [
            (event, day)
            for event in snapshot.events
            if (day := event.created_at.astimezone(UTC).date()) >= window[0]
        ]
        per_day = Counter(day for _, day in in_window)
        languages = Counter(repo.language for repo in owned if repo.language)
        pushes = [repo.pushed_at for repo in owned if repo.pushed_at is not None]
        catalog = (*owned, *snapshot.project_repositories)
        return cls(
            repositories=len(owned),
            stars=sum(repo.stars for repo in owned),
            forks=sum(repo.forks for repo in owned),
            followers=snapshot.followers,
            following=snapshot.following,
            gists=snapshot.gists,
            originals=sum(not repo.fork for repo in owned),
            forked=sum(repo.fork for repo in owned),
            archived=sum(repo.archived for repo in owned),
            open_items=sum(repo.open_items for repo in owned),
            joined=snapshot.created_at.astimezone(UTC).date(),
            last_push=max(pushes).astimezone(UTC).date() if pushes else None,
            languages=tuple(sorted(languages.items(), key=lambda item: (-item[1], item[0]))),
            window=window,
            daily_events=tuple(per_day[day] for day in window),
            events=len(in_window),
            active_days=len(per_day),
            event_repositories=len({event.repository.casefold() for event, _ in in_window}),
            project_stars=sum(repo.stars for repo in catalog),
            fetched_at=snapshot.fetched_at,
            repository_stars={repo.name.casefold(): repo.stars for repo in catalog},
        )

    def placeholders(self) -> dict[str, int]:
        """Return the values available to locale message templates."""
        return {
            "repositories": self.repositories,
            "stars": self.stars,
            "forks": self.forks,
            "followers": self.followers,
            "following": self.following,
            "gists": self.gists,
            "events": self.events,
            "active_days": self.active_days,
            "event_repositories": self.event_repositories,
        }


SUMMARY_PLACEHOLDERS: Final = frozenset(
    (
        "repositories",
        "stars",
        "forks",
        "followers",
        "following",
        "gists",
        "events",
        "active_days",
        "event_repositories",
    )
)
"""Names accepted by templates that are formatted with
:meth:`Summary.placeholders`; kept in sync by a unit test."""
