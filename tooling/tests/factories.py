# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Builders for valid test documents; tests mutate copies of them."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from profilegen import paths

INPUT_DIRECTORIES = ("configs", "locales/readme", "assets/profile")


def copy_inputs(target: Path) -> Path:
    """Copy every committed generator input into ``target``."""
    for name in INPUT_DIRECTORIES:
        shutil.copytree(paths.REPOSITORY_ROOT / name, target / name)
    return target


def read(path: Path) -> Any:
    """Decode a JSON file for in-test modification."""
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: object) -> None:
    """Encode ``value`` as JSON into ``path``."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def repository(**overrides: object) -> dict[str, object]:
    """Return a snapshot repository record."""
    record: dict[str, object] = {
        "name": "Mioji/Mioji",
        "stars": 0,
        "forks": 0,
        "open_items": 0,
        "fork": False,
        "archived": False,
        "language": None,
        "pushed_at": "2026-10-27T19:33:38Z",
    }
    record.update(overrides)
    return record


def event(event_id: str = "1", created_at: str = "2026-11-10T09:00:00Z") -> dict[str, object]:
    """Return a snapshot event record."""
    return {
        "id": event_id,
        "type": "CreateEvent",
        "repository": "Mioji/Mioji",
        "created_at": created_at,
    }


def snapshot(**overrides: object) -> dict[str, Any]:
    """Return a snapshot document with one owned repository."""
    document: dict[str, Any] = {
        "schema_version": 2,
        "username": "Mioji",
        "collection_started_at": "2026-11-10T10:00:00Z",
        "fetched_at": "2026-11-10T10:00:04Z",
        "followers": 0,
        "following": 0,
        "gists": 0,
        "created_at": "2026-07-30T03:50:27Z",
        "repositories": [repository()],
        "project_repositories": [],
        "events": [],
    }
    document.update(overrides)
    return document


def api_repository(**overrides: object) -> dict[str, object]:
    """Return a REST API repository object."""
    record: dict[str, object] = {
        "full_name": "Mioji/Mioji",
        "private": False,
        "owner": {"login": "Mioji"},
        "stargazers_count": 0,
        "forks_count": 0,
        "open_issues_count": 0,
        "fork": False,
        "archived": False,
        "language": None,
        "pushed_at": "2026-10-27T19:33:38Z",
    }
    record.update(overrides)
    return record


def api_user(**overrides: object) -> dict[str, object]:
    """Return a REST API user object."""
    record: dict[str, object] = {
        "login": "Mioji",
        "public_repos": 1,
        "followers": 2,
        "following": 3,
        "public_gists": 4,
        "created_at": "2026-07-30T03:50:27Z",
    }
    record.update(overrides)
    return record


def api_event(event_id: str = "7", **overrides: object) -> dict[str, object]:
    """Return a REST API event object."""
    record: dict[str, object] = {
        "id": event_id,
        "type": "PushEvent",
        "public": True,
        "actor": {"login": "Mioji"},
        "repo": {"name": "Mioji/Mioji"},
        "created_at": "2026-11-10T09:00:00Z",
    }
    record.update(overrides)
    return record
