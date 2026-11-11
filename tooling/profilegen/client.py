# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Minimal GitHub REST API client built on the standard library.

The generator needs four read-only endpoints, so a third-party HTTP
stack would add supply-chain surface without benefit. The client covers
what ``urllib`` does not:

* **Path policy.** Only ``/users/...``, ``/user/<id>/...``,
  ``/repos/...`` and ``/repositories/<id>`` paths are requested. The two
  numeric forms are not reachable from configuration; GitHub uses them
  in pagination ``Link`` headers and in redirects for renamed
  repositories, so rejecting them would break legitimate traffic.
* **Credential safety.** Redirects are followed only to
  ``https://api.github.com`` under the same path policy, so the bearer
  token, which ``urllib`` copies onto redirected requests, never leaves
  GitHub.
* **Retries.** Rate-limit responses honour ``Retry-After`` and
  ``X-RateLimit-Reset``; transient 5xx responses and connection
  failures back off exponentially with jitter. A requested wait above
  :data:`MAX_WAIT_SECONDS` aborts immediately: the next scheduled run is
  a better retry than an idle CI job.
* **Pagination.** ``Link: rel="next"`` is followed exactly as GitHub
  recommends, instead of guessing ``page=N`` URLs.
* **Bounded reads.** Bodies above :data:`MAX_RESPONSE_BYTES` are
  rejected before decoding.
"""

from __future__ import annotations

import http.client
import json
import math
import random
import re
import ssl
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from http.client import HTTPMessage
from typing import IO, Final, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from profilegen.errors import NetworkError, RateLimitError, SecurityPolicyError
from profilegen.validation import parse_json

API_HOST: Final = "api.github.com"
API_VERSION: Final = "2022-11-28"
PAGE_SIZE: Final = 100
MAX_RESPONSE_BYTES: Final = 8_000_000
MAX_ATTEMPTS: Final = 3
MAX_WAIT_SECONDS: Final = 30.0
TIMEOUT_SECONDS: Final = 20.0
BACKOFF_BASE_SECONDS: Final = 1.0
DEFAULT_USER_AGENT: Final = "profilegen (+https://github.com/Mioji/Mioji)"

_TRANSIENT_STATUS: Final = frozenset((500, 502, 503, 504))
_SEGMENT: Final = r"[A-Za-z0-9_.-]+"
_API_PATH: Final = re.compile(
    rf"/(?:users/{_SEGMENT}|user/[0-9]+|repos/{_SEGMENT}/{_SEGMENT}|repositories/[0-9]+)"
    rf"(?:/{_SEGMENT})*(?:\?[A-Za-z0-9_.=&%+-]*)?"
)
# Bounded to 12 ASCII digits so neither Unicode digits (e.g. U+00B2) nor
# thousands-of-digits headers can trigger ValueError or OverflowError.
_ASCII_DIGITS: Final = re.compile(r"[0-9]{1,12}")
_TRUNCATED_MESSAGE: Final = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)+)')
_CONTROL_CHARS: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]+")


class Response(Protocol):
    """Subset of :class:`http.client.HTTPResponse` used by the client."""

    headers: Message

    def read(self, amt: int | None = None, /) -> bytes:
        """Read at most ``amt`` bytes of the body."""
        ...

    def __enter__(self) -> Response:
        """Enter the response context."""
        ...

    def __exit__(self, *args: object) -> None:
        """Close the response."""
        ...


type Opener = Callable[[Request, float], Response]
"""Callable performing one HTTP request, injectable for tests."""


def check_path(path: str) -> str:
    """Return ``path`` if it satisfies the API path policy.

    Args:
        path: Absolute path, optionally with a query string.

    Returns:
        The unchanged path.

    Raises:
        SecurityPolicyError: If the path is outside the allowed
            endpoints or contains a ``.`` or ``..`` segment.
    """
    route = path.split("?", 1)[0]
    if _API_PATH.fullmatch(path) is None or any(
        segment in {".", ".."} for segment in route.split("/")
    ):
        raise SecurityPolicyError(f"GitHub API path rejected by policy: {path!r}")
    return path


def _api_path_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != API_HOST or parts.fragment:
        raise SecurityPolicyError(f"URL outside https://{API_HOST} rejected: {url!r}")
    return check_path(parts.path + (f"?{parts.query}" if parts.query else ""))


def _split_outside_quotes(text: str, delimiter: str) -> list[str]:
    """Split an RFC 8288 header on ``delimiter`` outside quotes and URLs."""
    parts: list[str] = []
    start = 0
    in_quotes = False
    in_angles = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if in_quotes:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_quotes = False
        elif char == '"' and not in_angles:
            in_quotes = True
        elif char == "<":
            in_angles = True
        elif char == ">":
            in_angles = False
        elif char == delimiter and not in_angles:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _unquote_param(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def _next_link_target(header: str) -> str | None:
    for entry in _split_outside_quotes(header, ","):
        segments = _split_outside_quotes(entry, ";")
        target = segments[0].strip()
        if not (target.startswith("<") and target.endswith(">")):
            continue
        for param in segments[1:]:
            name, sep, raw_value = param.partition("=")
            if not sep or name.strip().lower() != "rel":
                continue
            if "next" in _unquote_param(raw_value).lower().split():
                return target[1:-1].strip()
    return None


def next_page(link_header: str | Sequence[str] | None) -> str | None:
    """Extract the ``rel="next"`` target from RFC 8288 ``Link`` headers.

    Args:
        link_header: Raw header value, list of header values, or ``None``.

    Returns:
        The API path of the next page, or ``None`` on the last page.

    Raises:
        SecurityPolicyError: If the next link leaves the API host or
            violates the path policy.
    """
    if not link_header:
        return None
    headers = (link_header,) if isinstance(link_header, str) else link_header
    for header in headers:
        target = _next_link_target(header)
        if target is not None:
            return _api_path_of(target)
    return None


def _check_header_value(name: str, value: str, *, allow_space: bool) -> str:
    if not value.strip():
        raise SecurityPolicyError(f"{name} must not be empty")
    min_code = 0x20 if allow_space else 0x21
    if any(ord(char) < min_code or ord(char) >= 0x7F for char in value):
        raise SecurityPolicyError(f"{name} contains disallowed characters")
    return value


class _GitHubRedirectHandler(HTTPRedirectHandler):
    """Redirect handler that keeps every hop on the GitHub API host."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        """Validate the target before delegating to the default logic."""
        try:
            _api_path_of(newurl)
        except SecurityPolicyError:
            # Close the rejected redirect body immediately because the
            # caller in urllib only closes it after redirect_request
            # returns normally.
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener() -> Opener:
    open_url = build_opener(_GitHubRedirectHandler()).open

    def opener(request: Request, timeout: float) -> Response:
        response: Response = open_url(request, timeout=timeout)
        return response

    return opener


class GitHubClient:
    """Read-only GitHub REST API client with retries and pagination.

    Args:
        token: Optional bearer token. Anonymous requests work but share a
            limit of 60 requests per hour per IP address.
        user_agent: ``User-Agent`` header, which GitHub requires.
        opener: Request executor; defaults to ``urllib`` with the
            redirect policy described in the module docstring.
        sleep: Delay function, injectable so tests run instantly.
        clock: Wall clock returning POSIX seconds, used to interpret
            ``X-RateLimit-Reset`` and HTTP-date ``Retry-After`` values.
        jitter: Random source in ``[0, 1)`` for backoff jitter.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        opener: Opener | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        """Create a client; see the class docstring for arguments."""
        self._headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": _check_header_value("User-Agent", user_agent, allow_space=True),
            "X-GitHub-Api-Version": API_VERSION,
        }
        if token:
            stripped = token.strip()
            if stripped:
                checked = _check_header_value("Authorization token", stripped, allow_space=False)
                self._headers["Authorization"] = f"Bearer {checked}"
        self._open = opener or _default_opener()
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter

    def get(self, path: str) -> object:
        """Fetch one JSON resource.

        Args:
            path: API path such as ``/users/Mioji``.

        Returns:
            The decoded body.

        Raises:
            NetworkError: If the request ultimately fails.
            SecurityPolicyError: If the path violates the policy.
        """
        body, _ = self._request(path)
        return body

    def get_pages(self, path: str, *, max_pages: int, allow_truncation: bool) -> list[object]:
        """Fetch a paginated collection by following ``Link`` headers.

        Args:
            path: API path of the first page, without ``per_page``.
            max_pages: Largest number of pages to request.
            allow_truncation: Whether stopping at ``max_pages`` while a
                next page exists is acceptable. Feeds that are capped by
                GitHub anyway, like events, allow it; collections whose
                totals are displayed, like repositories, do not.

        Returns:
            Items of all requested pages in API order.

        Raises:
            NetworkError: If a page is not a JSON array, exceeds the page
                size, a request fails, or the collection is longer than
                ``max_pages`` without ``allow_truncation``.
            SecurityPolicyError: If a path violates the policy.
        """
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        separator = "&" if "?" in path else "?"
        target = f"{path}{separator}per_page={PAGE_SIZE}"
        items: list[object] = []
        seen_targets: set[str] = set()
        for _ in range(max_pages):
            if target in seen_targets:
                raise NetworkError(f"GitHub pagination loop detected at {target}")
            seen_targets.add(target)
            body, headers = self._request(target)
            if not isinstance(body, list) or len(body) > PAGE_SIZE:
                raise NetworkError(f"GitHub returned an invalid page for {target}")
            items.extend(body)
            following = next_page(headers.get_all("Link"))
            if following is None:
                return items
            target = following
        if not allow_truncation:
            raise NetworkError(f"{path} has more than {max_pages} pages")
        return items

    def _request(self, path: str) -> tuple[object, Message]:
        url = f"https://{API_HOST}{check_path(path)}"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = Request(url, headers=self._headers)
            try:
                with self._open(request, TIMEOUT_SECONDS) as response:
                    payload = response.read(MAX_RESPONSE_BYTES + 1)
                    headers = response.headers
            except HTTPError as exc:
                # The error owns the live response. Closing it here frees
                # the connection before the retry or the raise instead of
                # whenever the garbage collector reaches it.
                with exc:
                    delay = self._retry_delay(exc, attempt)
                    if delay is None:
                        raise NetworkError(_describe(exc, path)) from exc
            except (
                URLError,
                TimeoutError,
                ConnectionError,
                ssl.SSLError,
                http.client.HTTPException,
            ) as exc:
                if attempt == MAX_ATTEMPTS:
                    raise NetworkError(f"GitHub request for {path} failed: {exc}") from exc
                delay = self._backoff(attempt)
            else:
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise NetworkError(f"GitHub response for {path} exceeds the size limit")
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise NetworkError(f"GitHub response for {path} is not UTF-8") from exc
                return parse_json(text, source=f"GitHub {path}", error=NetworkError), headers
            self._sleep(delay)
        raise AssertionError("unreachable: the final attempt returns or raises")

    def _retry_delay(self, error: HTTPError, attempt: int) -> float | None:
        """Return the wait before retrying ``error``, or ``None`` to stop.

        GitHub signals both primary and secondary rate limits with 403
        or 429 plus either ``Retry-After`` or ``X-RateLimit-Remaining:
        0``. A 403 without those headers is a permission problem and is
        not retried; a 429 without them backs off as a transient limit.
        """
        if attempt == MAX_ATTEMPTS:
            return None
        # HTTPError built without headers carries None despite the stubs.
        headers: Message = error.headers or Message()
        if error.code in (403, 429):
            wait = self._rate_limit_wait(headers)
            if wait is None:
                return self._backoff(attempt) if error.code == 429 else None
            if wait > MAX_WAIT_SECONDS:
                raise RateLimitError(
                    f"GitHub rate limit requires waiting {wait:.0f}s; retry later"
                ) from error
            return wait
        return self._backoff(attempt) if error.code in _TRANSIENT_STATUS else None

    def _rate_limit_wait(self, headers: Message) -> float | None:
        retry_after = headers.get("Retry-After")
        if retry_after is not None:
            return max(0.0, self._parse_retry_after(retry_after.strip()))
        if headers.get("X-RateLimit-Remaining", "").strip() == "0":
            reset = headers.get("X-RateLimit-Reset", "").strip()
            if _ASCII_DIGITS.fullmatch(reset) is None:
                return MAX_WAIT_SECONDS + 1
            # One extra second absorbs clock skew between us and GitHub.
            return max(0.0, int(reset) - self._clock() + 1.0)
        return None

    def _parse_retry_after(self, value: str) -> float:
        if _ASCII_DIGITS.fullmatch(value) is not None:
            return float(value)
        try:
            moment = parsedate_to_datetime(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            return float((moment - datetime.fromtimestamp(self._clock(), UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            # An unparseable value cannot be honoured safely; treating it
            # as an excessive wait aborts instead of hammering the API.
            return MAX_WAIT_SECONDS + 1

    def _backoff(self, attempt: int) -> float:
        """Return an exponential delay with "equal jitter".

        Half of the exponential step is fixed and half is random. Unlike
        full jitter this guarantees a minimum pause, which matters for a
        client that makes only a handful of attempts.
        """
        step = math.ldexp(BACKOFF_BASE_SECONDS, attempt - 1)
        return step / 2 + self._jitter() * step / 2


def _describe(error: HTTPError, path: str) -> str:
    """Build a message that includes GitHub's ``message`` field if any."""
    detail = ""
    try:
        body = error.read(4096).decode("utf-8", "replace")
        raw_message = _extract_error_message(body)
        if raw_message:
            cleaned = _CONTROL_CHARS.sub(" ", raw_message).strip()[:200]
            if cleaned:
                detail = f": {cleaned}"
    except (OSError, ValueError, AttributeError):
        detail = ""
    return f"GitHub request for {path} failed with HTTP {error.code}{detail}"


def _extract_error_message(body: str) -> str | None:
    try:
        payload = json.loads(body)
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str):
                return message
    except ValueError:
        match = _TRUNCATED_MESSAGE.search(body)
        if match is not None:
            raw = match.group(1)
            try:
                decoded = json.loads(f'"{raw}"')
            except ValueError:
                return raw
            return decoded if isinstance(decoded, str) else raw
    return None
