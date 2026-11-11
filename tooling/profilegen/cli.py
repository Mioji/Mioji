# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Command-line interface: render, refresh or verify the profile.

Exit status is 0 on success, 1 for any :class:`ProfileError` or file
system error, and 2 for invalid arguments. Expected failures print one
line to standard error; unexpected exceptions keep their traceback,
because hiding a programming error behind a friendly message only makes
it harder to fix.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from profilegen import paths, publish
from profilegen.client import GitHubClient
from profilegen.collect import Client, collect
from profilegen.errors import ProfileError
from profilegen.models import format_timestamp
from profilegen.render import load_inputs, load_snapshot, render

# Bandit's S105 matches the word "token" in the name; this is the name of
# an environment variable, not a credential.
TOKEN_VARIABLE = "GH_TOKEN"  # noqa: S105
"""Environment variable with an optional API token; GitHub Actions sets
it from ``github.token``, and the GitHub CLI uses the same name."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tooling/generate.py",
        description="Render the multilingual GitHub profile from committed inputs.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--fetch",
        action="store_true",
        help=f"collect a new public snapshot from GitHub first (reads ${TOKEN_VARIABLE})",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify that generated files are up to date without writing anything",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = paths.REPOSITORY_ROOT,
    client_factory: Callable[[str | None], Client] = GitHubClient,
) -> int:
    """Run the generator.

    Args:
        argv: Arguments without the program name; ``None`` reads
            :data:`sys.argv`.
        root: Repository root, overridable for tests.
        client_factory: Builds the API client from an optional token,
            overridable for tests.

    Returns:
        The process exit status.
    """
    args = _parser().parse_args(argv)
    try:
        inputs = load_inputs(root)
        if args.fetch:
            snapshot = collect(
                client_factory(os.environ.get(TOKEN_VARIABLE) or None),
                inputs.profile.username,
                inputs.profile.projects,
            )
        else:
            snapshot = load_snapshot(root)
        outputs = render(inputs, snapshot)

        if args.check:
            stale = publish.stale_files(root, outputs)
            if stale:
                print(
                    "Generated files are out of date; run python tooling/generate.py:\n  "
                    + "\n  ".join(stale),
                    file=sys.stderr,
                )
                return 1
            print(f"Verified {len(outputs)} generated files.")
            return 0

        change = publish.plan(root, outputs)
        publish.apply(root, change)
    except (ProfileError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Rendered {len(outputs)} files from the snapshot of "
        f"{format_timestamp(snapshot.fetched_at)}: {len(change.writes)} written, "
        f"{len(change.deletions)} removed, {change.unchanged} unchanged."
    )
    return 0
