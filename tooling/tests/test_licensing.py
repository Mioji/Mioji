# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Consistency of the split-license setup (MIT code, CC BY-NC-ND art).

The assignment exists in three places that must not drift apart: the
prose of ``LICENSE``, the machine-readable ``REUSE.toml`` together with
SPDX headers in source files, and the license texts under ``LICENSES/``.
These tests resolve the license of every file in the working tree the way
the REUSE specification does and compare the result with expectations.
"""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import PurePosixPath

import pytest

from profilegen import paths

ROOT = paths.REPOSITORY_ROOT
MIT = "MIT"
CC = "CC-BY-NC-ND-4.0"
SKIPPED_DIRECTORIES = {
    ".git",
    "__pycache__",
    ".venv",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
# The tag is assembled so that this file does not contain a second, bogus
# license declaration for REUSE tooling to find.
TAG = "SPDX-License-" + "Identifier"
COPYRIGHT_TAG = "SPDX-FileCopyright" + "Text"
SPDX_HEADER = re.compile(rf"^# {TAG}: (\S+)$", re.MULTILINE)
SPDX_COPYRIGHT = re.compile(rf"^# {COPYRIGHT_TAG}: .+$", re.MULTILINE)


def _files() -> list[PurePosixPath]:
    result: list[PurePosixPath] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if (
            path.is_file()
            and not SKIPPED_DIRECTORIES & set(relative.parts)
            and relative.parts[0] != "LICENSES"
            and not relative.parts[0].startswith(".profile-")
        ):
            result.append(PurePosixPath(relative.as_posix()))
    return sorted(result)


def _glob(pattern: str) -> re.Pattern[str]:
    """Translate a REUSE glob; ``*`` stays in a directory, ``**`` does not."""
    regex = ""
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            regex += "(?:.*/)?"
            index += 3
        elif pattern.startswith("**", index):
            regex += ".*"
            index += 2
        elif pattern[index] == "*":
            regex += "[^/]*"
            index += 1
        else:
            regex += re.escape(pattern[index])
            index += 1
    return re.compile(regex)


@cache
def _annotations() -> tuple[tuple[tuple[re.Pattern[str], ...], str, str, bool], ...]:
    data = tomllib.loads((ROOT / "REUSE.toml").read_text(encoding="utf-8"))
    result = []
    for entry in data["annotations"]:
        patterns = entry["path"] if isinstance(entry["path"], list) else [entry["path"]]
        precedence = str(entry.get("precedence", "aggregate"))
        has_copyright = bool(entry.get(COPYRIGHT_TAG))
        result.append(
            (
                tuple(_glob(pattern) for pattern in patterns),
                entry["SPDX-License-Identifier"],
                precedence,
                has_copyright,
            )
        )
    return tuple(result)


def licenses_for(path: PurePosixPath) -> tuple[set[str], bool]:
    """Return license IDs and copyright status under REUSE 3.3 rules."""
    licenses: set[str] = set()
    has_copyright = False
    overridden = False
    for patterns, license_id, precedence, ann_copyright in _annotations():
        if not any(pattern.fullmatch(str(path)) for pattern in patterns):
            continue
        if precedence == "override":
            licenses = {license_id}
            has_copyright = ann_copyright
            overridden = True
        else:
            licenses.add(license_id)
            has_copyright = has_copyright or ann_copyright
            overridden = False

    if not overridden and path.suffix != ".webp":
        try:
            head = (ROOT / path).read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            return licenses, has_copyright
        header = SPDX_HEADER.search(head)
        if header:
            licenses.add(header.group(1))
        if SPDX_COPYRIGHT.search(head):
            has_copyright = True
    return licenses, has_copyright


def resolve(path: PurePosixPath) -> str | None:
    """Return the unique license of ``path``, or ``None`` if ambiguous."""
    licenses, _ = licenses_for(path)
    return next(iter(licenses)) if len(licenses) == 1 else None


def test_every_file_has_exactly_one_license_and_copyright() -> None:
    issues: list[str] = []
    for path in _files():
        licenses, has_copyright = licenses_for(path)
        if len(licenses) != 1 or not has_copyright:
            issues.append(f"{path}: licenses={sorted(licenses)}, copyright={has_copyright}")
    assert issues == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tooling/profilegen/render.py", MIT),
        ("tooling/tests/test_licensing.py", MIT),
        (".github/workflows/profile.yml", MIT),
        ("locales/readme/template.md", MIT),
        ("configs/theme.json", MIT),
        ("docs/design.md", MIT),
        ("assets/profile/banner.webp", CC),
        ("assets/profile/backgrounds/summary.webp", CC),
        ("assets/profile/backgrounds/projects/pattsys.webp", CC),
        ("assets/generated/readme/en/dashboard/summary.svg", CC),
        ("assets/generated/shared/skills/rust.svg", CC),
        ("locales/readme/ru/strings.json", CC),
        ("locales/readme/ru/README.md", CC),
        ("README.md", CC),
        ("configs/profile.json", CC),
        ("configs/data/snapshot.json", CC),
        ("assets/profile/backgrounds/prompts/summary.md", MIT),
        ("assets/profile/backgrounds/prompts/projects/pattsys.md", MIT),
    ],
)
def test_license_assignment(path: str, expected: str) -> None:
    assert resolve(PurePosixPath(path)) == expected


def test_all_python_and_workflow_files_carry_an_spdx_header() -> None:
    sources = [
        *ROOT.joinpath("tooling").rglob("*.py"),
        *ROOT.joinpath(".github").rglob("*.yml"),
        *ROOT.joinpath(".github").rglob("*.yaml"),
    ]
    missing = [
        str(path.relative_to(ROOT))
        for path in sources
        if "__pycache__" not in path.parts
        and not (
            SPDX_HEADER.search(head := path.read_text(encoding="utf-8")[:300])
            and SPDX_COPYRIGHT.search(head)
        )
    ]
    assert missing == []
    assert all(resolve(PurePosixPath(path.relative_to(ROOT).as_posix())) == MIT for path in sources)


def test_license_texts_are_shipped_and_identified() -> None:
    assert "MIT License" in (ROOT / "LICENSES/MIT.txt").read_text(encoding="utf-8")
    cc_text = (ROOT / "LICENSES/CC-BY-NC-ND-4.0.txt").read_text(encoding="utf-8")
    assert cc_text.startswith("Creative Commons Attribution-NonCommercial-NoDerivatives 4.0")
    notice = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert f"{TAG}: {MIT}" in notice
    assert f"{TAG}: {CC}" in notice
    assert "Template Usage Clause" in notice


def test_no_trace_of_the_previous_license_remains() -> None:
    offenders = []
    for path in _files():
        if path.suffix in {".webp", ".svg"} or path.name == "test_licensing.py":
            continue
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace").casefold()
        if "unlicense" in text or "public domain" in text:
            offenders.append(str(path))
    assert offenders == []


def test_pyproject_declares_both_licenses() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == f"{MIT} AND {CC}"
    for pattern in project["license-files"]:
        assert list(ROOT.glob(pattern)), f"missing license-files match for {pattern}"


def test_reuse_toml_patterns_all_match_existing_files() -> None:
    files = _files()
    for patterns, _, _, _ in _annotations():
        assert any(pattern.fullmatch(str(path)) for pattern in patterns for path in files)


def test_every_readme_states_both_licenses() -> None:
    readmes = [
        ROOT / "README.md",
        *sorted((ROOT / "locales/readme").glob("*/README.md")),
    ]
    assert len(readmes) >= 3
    for readme in readmes:
        text = readme.read_text(encoding="utf-8")
        assert "LICENSES/MIT.txt" in text, str(readme)
        assert "creativecommons.org/licenses/by-nc-nd/4.0/" in text, str(readme)
        assert 'LICENSE">LICENSE</a>' in text, str(readme)
