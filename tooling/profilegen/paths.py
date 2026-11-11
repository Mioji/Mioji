# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Repository-relative locations of every input and output.

Paths are :class:`~pathlib.PurePosixPath` values relative to the
repository root. Keeping them relative and POSIX-shaped serves two
purposes: generated Markdown embeds them as URLs, which always use
forward slashes, and :mod:`profilegen.publish` can recognise managed
outputs by pattern without touching the file system.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
"""Checkout root, derived from this file so the CLI works from any CWD."""

PROFILE_CONFIG: Final = PurePosixPath("configs/profile.json")
SKILLS_CONFIG: Final = PurePosixPath("configs/skills.json")
THEME_CONFIG: Final = PurePosixPath("configs/theme.json")
SNAPSHOT: Final = PurePosixPath("configs/data/snapshot.json")

LOCALES_DIR: Final = PurePosixPath("locales/readme")
TEMPLATE: Final = LOCALES_DIR / "template.md"
STRINGS_FILE: Final = "strings.json"

BACKGROUNDS_DIR: Final = PurePosixPath("assets/profile/backgrounds")
PROJECT_BACKGROUNDS_DIR: Final = BACKGROUNDS_DIR / "projects"
PROMPTS_DIR: Final = BACKGROUNDS_DIR / "prompts"
PROJECT_PROMPTS_DIR: Final = PROMPTS_DIR / "projects"

GENERATED_DIR: Final = PurePosixPath("assets/generated")
SHARED_ASSETS_DIR: Final = GENERATED_DIR / "shared"
ROOT_README: Final = PurePosixPath("README.md")


def locale_assets_dir(code: str) -> PurePosixPath:
    """Return the directory holding SVG panels of one locale.

    Args:
        code: Locale code such as ``ru``.

    Returns:
        A path of the form ``assets/generated/readme/<code>``.
    """
    return GENERATED_DIR / "readme" / code


def locale_readme(code: str) -> PurePosixPath:
    """Return the generated README path of a non-default locale.

    Args:
        code: Locale code such as ``ru``.

    Returns:
        A path of the form ``locales/readme/<code>/README.md``.
    """
    return LOCALES_DIR / code / "README.md"
