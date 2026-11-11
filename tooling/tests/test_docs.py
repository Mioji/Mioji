# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Consistency checks between repository documentation, config and code."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

from profilegen import paths
from profilegen.cli import main
from profilegen.client import MAX_WAIT_SECONDS
from profilegen.collect import EVENT_PAGES
from profilegen.panels import COMPACT_WIDTH, DESKTOP_WIDTH
from tests import factories

ROOT = paths.REPOSITORY_ROOT


def _webp_dimensions(payload: bytes) -> tuple[int, int]:
    """Read width and height from a WebP VP8/VP8L/VP8X bitstream header."""
    chunk = payload[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(payload[24:27], "little") + 1
        height = int.from_bytes(payload[27:30], "little") + 1
        return width, height
    if chunk == b"VP8L":
        bits = int.from_bytes(payload[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 ":
        width = struct.unpack("<H", payload[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", payload[28:30])[0] & 0x3FFF
        return width, height
    raise AssertionError(f"unexpected WebP chunk {chunk!r}")


def test_design_doc_matches_panel_widths_and_banner_dimensions() -> None:
    design = (ROOT / "docs/design.md").read_text(encoding="utf-8")
    assert f"desktop variant is {DESKTOP_WIDTH}" in design
    assert f"compact variant {COMPACT_WIDTH}" in design

    banner_path = ROOT / "assets/profile/banner.webp"
    width, height = _webp_dimensions(banner_path.read_bytes())
    assert f"({width} by {height} pixels)" in design


def test_maintenance_doc_matches_code_constants_and_paths() -> None:
    maintenance = (ROOT / "docs/maintenance.md").read_text(encoding="utf-8")
    documented_paths = (
        "tooling/generate.py",
        "configs/profile.json",
        "configs/skills.json",
        "configs/theme.json",
        "configs/data/snapshot.json",
        "locales/readme/template.md",
        "tooling/requirements-dev.txt",
        ".github/workflows/profile.yml",
        "REUSE.toml",
        "LICENSE",
    )
    for rel in documented_paths:
        assert rel in maintenance
        assert (ROOT / rel).exists(), rel

    assert f"{int(MAX_WAIT_SECONDS)} seconds" in maintenance
    assert f"three pages of {100 if EVENT_PAGES == 3 else '?'}" in maintenance
    assert "`date_format`) accept `%Y %m %d %%`" in maintenance
    assert "`timestamp_format`) accept `%Y %m %d %H %M %S %%`" in maintenance


def test_workflow_pin_comments_and_python_versions_stay_in_sync() -> None:
    workflow = (ROOT / ".github/workflows/profile.yml").read_text(encoding="utf-8")
    maintenance = (ROOT / "docs/maintenance.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in pyproject
    assert "Python 3.12" in maintenance
    assert '"3.12"' in workflow
    assert "fetch-depth: 0" in workflow


def test_workflow_git_add_and_lockfile_stay_in_sync() -> None:
    workflow = (ROOT / ".github/workflows/profile.yml").read_text(encoding="utf-8")
    for target in (paths.ROOT_README, paths.LOCALES_DIR, paths.GENERATED_DIR, paths.SNAPSHOT):
        assert str(target) in workflow

    reqs_in = (ROOT / "tooling/requirements-dev.in").read_text(encoding="utf-8").splitlines()
    reqs_txt = (ROOT / "tooling/requirements-dev.txt").read_text(encoding="utf-8")
    pinned = [
        line.strip() for line in reqs_in if line.strip() and not line.lstrip().startswith("#")
    ]
    assert pinned
    for requirement in pinned:
        assert requirement in reqs_txt


def test_documented_common_changes_work_end_to_end(tmp_path: Path) -> None:
    work = factories.copy_inputs(tmp_path)
    assert main([], root=work) == 0

    # 1. Add a private catalog project + translations + artwork + prompt.
    shutil.copy2(
        work / paths.PROJECT_BACKGROUNDS_DIR / "pattsys.webp",
        work / paths.PROJECT_BACKGROUNDS_DIR / "demo.webp",
    )
    shutil.copy2(
        work / paths.PROJECT_PROMPTS_DIR / "pattsys.md",
        work / paths.PROJECT_PROMPTS_DIR / "demo.md",
    )
    profile = factories.read(work / paths.PROFILE_CONFIG)
    profile["catalog"]["projects"].append(
        {
            "repository": "Example/docs-demo",
            "description_key": "docs_demo_desc",
            "status": "active",
            "visibility": "private",
            "background_key": "demo",
        }
    )
    # 2. Add a contact.
    profile["contacts"].append(
        {
            "id": "docs",
            "label": "Docs",
            "mark": "DOC",
            "href": "https://example.org/",
            "accent": "violet",
            "enabled": True,
        }
    )
    # 4. Reorder and hide a section (hide skills, swap first two sections).
    for section in profile["layout"]["sections"]:
        if section["component"] == "skills":
            section["enabled"] = False
    profile["layout"]["sections"][0], profile["layout"]["sections"][1] = (
        profile["layout"]["sections"][1],
        profile["layout"]["sections"][0],
    )
    factories.write(work / paths.PROFILE_CONFIG, profile)

    # 3. Add plain and custom-id skills.
    skills = factories.read(work / paths.SKILLS_CONFIG)
    skills["languages_label"].extend(
        [
            {"label": "Nim", "mark": "NIM", "url": "https://nim-lang.org/"},
            {"label": "C++ & SIMD", "id": "cpp-simd", "mark": "C++", "url": "https://isocpp.org/"},
        ]
    )
    factories.write(work / paths.SKILLS_CONFIG, skills)

    # Add the project key to existing locales, then add a new language.
    for code in ("en", "ru", "zh-CN"):
        loc_path = work / paths.LOCALES_DIR / code / paths.STRINGS_FILE
        loc_data = factories.read(loc_path)
        loc_data["content"]["docs_demo_desc"] = "Demo catalog item."
        factories.write(loc_path, loc_data)

    es_dir = work / paths.LOCALES_DIR / "es"
    es_dir.mkdir()
    es_data = factories.read(work / paths.LOCALES_DIR / "en" / paths.STRINGS_FILE)
    es_data["locale_code"] = "es"
    es_data["language"] = "Español"
    es_data["locale_format"]["date"]["date_format"] = "%d/%m/%Y"
    factories.write(es_dir / paths.STRINGS_FILE, es_data)

    assert main([], root=work) == 0
    assert main(["--check"], root=work) == 0
    assert "Example/docs-demo" in (work / paths.ROOT_README).read_text(encoding="utf-8")
    assert (work / paths.SHARED_ASSETS_DIR / "contacts" / "docs.svg").is_file()
    assert (work / paths.SHARED_ASSETS_DIR / "languages" / "es.svg").is_file()
    assert (work / paths.locale_readme("es")).is_file()
    assert not (work / paths.SHARED_ASSETS_DIR / "skills").exists()
