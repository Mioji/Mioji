# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Transactional publication of rendered files.

The generator owns a closed set of paths: the snapshot, the root README,
``locales/readme/<code>/README.md`` and everything under
``assets/generated/``. Within that set the rendered mapping is the
complete desired state, so a managed file absent from it is an orphan,
for example a card left over after a project was removed. Orphans are
deleted on publish and reported by :func:`stale_files`, which is what
``--check`` uses.

Publication is all-or-nothing. New content is first staged in a
temporary directory on the same file system, so a failure while writing
cannot corrupt the tree; each file is then moved into place with
:meth:`pathlib.Path.replace`, which is atomic per file. If any step of
that second phase fails, the files already replaced or deleted are
restored from in-memory copies before the error propagates.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from profilegen import paths
from profilegen.errors import SecurityPolicyError


def is_managed(path: PurePosixPath) -> bool:
    """Return whether the generator owns ``path``.

    Args:
        path: Repository-relative path.

    Returns:
        ``True`` for the snapshot, the root README, localised READMEs and
        SVG files under the generated assets directory.
    """
    if path in {paths.SNAPSHOT, paths.ROOT_README}:
        return True
    parts = path.parts
    locale_dir = paths.LOCALES_DIR.parts
    if len(parts) == len(locale_dir) + 2 and parts[: len(locale_dir)] == locale_dir:
        return parts[-1] == "README.md"
    return path.is_relative_to(paths.GENERATED_DIR) and path.suffix == ".svg"


def _exists_on_disk(path: Path) -> bool:
    return path.is_file() or path.is_symlink()


def _managed_on_disk(root: Path) -> Iterator[PurePosixPath]:
    for path in (paths.SNAPSHOT, paths.ROOT_README):
        if _exists_on_disk(root / path):
            yield path
    for readme in sorted((root / paths.LOCALES_DIR).glob("*/README.md")):
        if _exists_on_disk(readme):
            yield PurePosixPath(readme.relative_to(root).as_posix())
    generated = root / paths.GENERATED_DIR
    if generated.is_dir():
        for file in sorted(generated.rglob("*")):
            relative = PurePosixPath(file.relative_to(root).as_posix())
            if _exists_on_disk(file) and is_managed(relative):
                yield relative


def _validated(outputs: Mapping[str, str]) -> dict[PurePosixPath, bytes]:
    encoded: dict[PurePosixPath, bytes] = {}
    for name, content in outputs.items():
        if "\\" in name or "\x00" in name:
            raise SecurityPolicyError(f"refusing to write unmanaged path {name!r}")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not is_managed(path):
            raise SecurityPolicyError(f"refusing to write unmanaged path {name!r}")
        encoded[path] = content.encode("utf-8")
    return encoded


@dataclass(frozen=True, slots=True)
class Plan:
    """Difference between rendered outputs and the working tree.

    Attributes:
        writes: Files whose content is new or changed.
        deletions: Managed files that are no longer rendered.
        unchanged: Number of files already up to date.
    """

    writes: Mapping[PurePosixPath, bytes]
    deletions: tuple[PurePosixPath, ...]
    unchanged: int

    @property
    def is_empty(self) -> bool:
        """Whether the working tree already matches the outputs."""
        return not self.writes and not self.deletions


def plan(root: Path, outputs: Mapping[str, str]) -> Plan:
    """Compare rendered outputs with the files below ``root``.

    Args:
        root: Repository root.
        outputs: Rendered content keyed by repository-relative path.

    Returns:
        The plan.

    Raises:
        SecurityPolicyError: If an output path is outside the managed
            set, which would let a renderer bug overwrite inputs.
    """
    desired = _validated(outputs)
    writes: dict[PurePosixPath, bytes] = {}
    unchanged = 0
    for path, content in desired.items():
        target = root / path
        if target.is_file() and not target.is_symlink() and target.read_bytes() == content:
            unchanged += 1
        else:
            writes[path] = content
    deletions = tuple(path for path in _managed_on_disk(root) if path not in desired)
    return Plan(writes=writes, deletions=deletions, unchanged=unchanged)


def stale_files(root: Path, outputs: Mapping[str, str]) -> list[str]:
    """Return a sorted description of every out-of-date managed file.

    Args:
        root: Repository root.
        outputs: Rendered content keyed by repository-relative path.

    Returns:
        Paths that would change, each suffixed with ``(orphaned)`` if it
        would be deleted. Empty when the tree is up to date.
    """
    result = plan(root, outputs)
    return sorted(
        [str(path) for path in result.writes] + [f"{path} (orphaned)" for path in result.deletions]
    )


def apply(root: Path, change: Plan) -> None:
    """Apply a plan atomically with respect to failures.

    Args:
        root: Repository root.
        change: Plan computed by :func:`plan` for the same root.

    Raises:
        OSError: If staging or moving files fails. The working tree is
            restored to its previous state before the error propagates.
    """
    if change.is_empty:
        return
    # The staging directory lives inside the root so that Path.replace
    # never crosses a file system boundary, which would make it a copy.
    # Its ".profile-" prefix is listed in .gitignore in case the process
    # is killed before cleanup.
    with tempfile.TemporaryDirectory(prefix=".profile-", dir=root) as staging_dir:
        staging = Path(staging_dir)
        for path, content in change.writes.items():
            staged = staging / path
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)

        previous: dict[PurePosixPath, bytes | None] = {}
        try:
            for path in change.writes:
                target = root / path
                backup = (
                    target.read_bytes() if target.is_file() and not target.is_symlink() else None
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                (staging / path).replace(target)
                previous[path] = backup
            for path in change.deletions:
                target = root / path
                backup = (
                    target.read_bytes() if target.is_file() and not target.is_symlink() else None
                )
                target.unlink(missing_ok=True)
                previous[path] = backup
        except OSError:
            _restore(root, previous)
            _prune_empty_directories(root / paths.GENERATED_DIR)
            raise
    _prune_empty_directories(root / paths.GENERATED_DIR)


def _restore(root: Path, previous: Mapping[PurePosixPath, bytes | None]) -> None:
    """Undo partially applied changes in reverse order."""
    for path, content in reversed(list(previous.items())):
        target = root / path
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def _prune_empty_directories(directory: Path) -> None:
    """Remove directories left empty by deletions, deepest first."""
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_symlink() and path.is_dir() and not any(path.iterdir()):
            path.rmdir()
