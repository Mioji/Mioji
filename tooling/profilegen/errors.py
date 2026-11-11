# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Exception hierarchy shared by every stage of the generator.

Every anticipated failure derives from :class:`ProfileError`. The
command-line entry point reports those as a single readable line and
exits with status 1. Programming errors such as ``KeyError`` or
``TypeError`` are deliberately left outside the hierarchy so that they
still surface with a full traceback instead of being disguised as bad
input.
"""

from __future__ import annotations


class ProfileError(Exception):
    """Base class for expected failures that the user can act upon."""


class ConfigError(ProfileError):
    """A configuration, theme, locale or template file is invalid."""


class SnapshotError(ProfileError):
    """A snapshot or GitHub API payload violates the data contract."""


class NetworkError(ProfileError):
    """The GitHub API could not be reached or answered with an error."""


class RateLimitError(NetworkError):
    """GitHub requested a pause longer than the client may wait."""


class SecurityPolicyError(ProfileError):
    """A URL, redirect, output path or asset violates security policy."""
