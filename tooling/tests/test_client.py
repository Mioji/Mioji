# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""GitHub client: path policy, retries, rate limits and pagination."""

from __future__ import annotations

import io
import json
import ssl
from email.message import Message
from http.client import HTTPMessage
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from profilegen.client import (
    MAX_ATTEMPTS,
    GitHubClient,
    _GitHubRedirectHandler,
    check_path,
    next_page,
)
from profilegen.errors import NetworkError, RateLimitError, SecurityPolicyError


def _headers(values: dict[str, str] | list[tuple[str, str]] | None = None) -> HTTPMessage:
    message = HTTPMessage()
    pairs = values.items() if isinstance(values, dict) else (values or ())
    for key, value in pairs:
        message[key] = value
    return message


class FakeResponse:
    """In-memory stand-in for an HTTP response."""

    def __init__(
        self,
        body: object,
        headers: dict[str, str] | list[tuple[str, str]] | None = None,
    ) -> None:
        self.headers: Message = _headers(headers)
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, amt: int | None = None, /) -> bytes:
        return self._body if amt is None else self._body[:amt]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def http_error(code: int, headers: dict[str, str] | None = None, body: bytes = b"") -> HTTPError:
    return HTTPError("https://api.github.com/x", code, "error", _headers(headers), io.BytesIO(body))


class FakeOpener:
    """Replays queued responses or exceptions and records requests."""

    def __init__(self, *results: FakeResponse | Exception) -> None:
        self.results = list(results)
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def client(opener: FakeOpener, sleeps: list[float], token: str | None = None) -> GitHubClient:
    return GitHubClient(
        token, opener=opener, sleep=sleeps.append, clock=lambda: 1_000.0, jitter=lambda: 0.5
    )


def test_sends_versioned_headers_and_bearer_token() -> None:
    opener = FakeOpener(FakeResponse({"login": "Mioji"}))
    assert client(opener, [], "test-token").get("/users/Mioji") == {"login": "Mioji"}
    request = opener.requests[0]
    assert request.full_url == "https://api.github.com/users/Mioji"
    assert request.get_header("Authorization") == "Bearer test-token"
    assert request.get_header("X-github-api-version") == "2022-11-28"


@pytest.mark.parametrize(
    "path",
    ["/users/../admin", "/orgs/github", "/repos/a", "/users/Mioji/x?y=<z>", "https://x/users/a"],
)
def test_path_policy_rejects_unexpected_paths(path: str) -> None:
    with pytest.raises(SecurityPolicyError):
        check_path(path)


@pytest.mark.parametrize(
    "path",
    ["/users/Mioji", "/user/42/repos?per_page=100&page=2", "/repos/a/b", "/repositories/7"],
)
def test_path_policy_accepts_api_paths(path: str) -> None:
    assert check_path(path) == path


def test_retry_after_is_honoured() -> None:
    sleeps: list[float] = []
    opener = FakeOpener(http_error(429, {"Retry-After": "2"}), FakeResponse({"ok": True}))
    assert client(opener, sleeps).get("/users/Mioji") == {"ok": True}
    assert sleeps == [2.0]


def test_rate_limit_reset_is_honoured() -> None:
    sleeps: list[float] = []
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1010"}
    opener = FakeOpener(http_error(403, headers), FakeResponse([]))
    client(opener, sleeps).get("/users/Mioji")
    assert sleeps == [11.0]


@pytest.mark.parametrize("retry_after", ["600", "Wed, 21 Oct 2099 07:28:00 GMT", "soon"])
def test_excessive_or_unparseable_wait_aborts_without_sleeping(retry_after: str) -> None:
    sleeps: list[float] = []
    opener = FakeOpener(http_error(403, {"Retry-After": retry_after}))
    with pytest.raises(RateLimitError):
        client(opener, sleeps).get("/users/Mioji")
    assert sleeps == []


def test_permission_error_is_not_retried_and_reports_message() -> None:
    opener = FakeOpener(http_error(403, body=b'{"message": "Resource not accessible"}'))
    with pytest.raises(NetworkError, match="HTTP 403: Resource not accessible"):
        client(opener, []).get("/users/Mioji")
    assert len(opener.requests) == 1


def test_error_message_with_escaped_quotes_is_reported() -> None:
    opener = FakeOpener(http_error(404, body=b'{"message": "Repository \\"foo\\" not found"}'))
    with pytest.raises(NetworkError, match='HTTP 404: Repository "foo" not found'):
        client(opener, []).get("/users/Mioji")
    assert len(opener.requests) == 1


def test_transient_errors_back_off_with_jitter() -> None:
    sleeps: list[float] = []
    opener = FakeOpener(http_error(502), URLError("reset"), FakeResponse([]))
    assert client(opener, sleeps).get("/users/Mioji") == []
    assert sleeps == [0.75, 1.5]


def test_gives_up_after_the_last_attempt() -> None:
    sleeps: list[float] = []
    opener = FakeOpener(*(TimeoutError("slow") for _ in range(MAX_ATTEMPTS)))
    with pytest.raises(NetworkError, match="failed"):
        client(opener, sleeps).get("/users/Mioji")
    assert len(sleeps) == MAX_ATTEMPTS - 1


def test_error_responses_are_closed_after_a_retry_and_after_the_final_failure() -> None:
    retried = http_error(502)
    final = http_error(404, body=b'{"message": "Not Found"}')
    with pytest.raises(NetworkError, match="HTTP 404: Not Found"):
        client(FakeOpener(retried, final), []).get("/users/Mioji")
    assert retried.fp.closed
    assert final.fp.closed


def test_error_response_is_closed_when_the_rate_limit_aborts() -> None:
    error = http_error(429, {"Retry-After": "86400"})
    with pytest.raises(RateLimitError):
        client(FakeOpener(error), []).get("/users/Mioji")
    assert error.fp.closed


@pytest.mark.parametrize("body", [b"\xff", b"{", b"NaN", b'{"a": 1, "a": 2}'])
def test_malformed_bodies_are_rejected(body: bytes) -> None:
    with pytest.raises(NetworkError):
        client(FakeOpener(FakeResponse(body)), []).get("/users/Mioji")


def test_oversized_body_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("profilegen.client.MAX_RESPONSE_BYTES", 4)
    with pytest.raises(NetworkError, match="size limit"):
        client(FakeOpener(FakeResponse(b"[1, 2]")), []).get("/users/Mioji")


def test_pagination_follows_link_headers() -> None:
    link = '<https://api.github.com/user/42/repos?per_page=100&page=2>; rel="next", <x>; rel="last"'
    opener = FakeOpener(FakeResponse([1] * 100, {"Link": link}), FakeResponse([2]))
    items = client(opener, []).get_pages("/users/Mioji/repos", max_pages=5, allow_truncation=False)
    assert items == [1] * 100 + [2]
    assert opener.requests[1].full_url.endswith("/user/42/repos?per_page=100&page=2")


def test_pagination_limit() -> None:
    link = {"Link": '<https://api.github.com/users/Mioji/events?page=2>; rel="next"'}
    pages = [FakeResponse([index], link) for index in range(2)]
    truncated = client(FakeOpener(*pages), []).get_pages(
        "/users/Mioji/events", max_pages=2, allow_truncation=True
    )
    assert truncated == [0, 1]
    pages = [FakeResponse([index], link) for index in range(2)]
    with pytest.raises(NetworkError, match="more than 2 pages"):
        client(FakeOpener(*pages), []).get_pages(
            "/users/Mioji/repos", max_pages=2, allow_truncation=False
        )


def test_pagination_rejects_non_list_pages() -> None:
    with pytest.raises(NetworkError, match="invalid page"):
        client(FakeOpener(FakeResponse({"message": "x"})), []).get_pages(
            "/users/Mioji/repos", max_pages=1, allow_truncation=False
        )


def test_next_link_must_stay_on_the_api_host() -> None:
    assert next_page(None) is None
    with pytest.raises(SecurityPolicyError):
        next_page('<https://example.org/users/Mioji?page=2>; rel="next"')


def test_redirects_leaving_the_api_host_are_refused() -> None:
    request = Request("https://api.github.com/users/Mioji", headers={"Authorization": "x"})
    body = io.BytesIO()
    with pytest.raises(SecurityPolicyError):
        _GitHubRedirectHandler().redirect_request(
            request, body, 302, "Found", _headers(), "https://example.org/users/Mioji"
        )
    assert body.closed


def test_redirects_on_the_api_host_are_allowed() -> None:
    request = Request("https://api.github.com/repos/Mioji/old", headers={"Authorization": "x"})
    body = io.BytesIO()
    redirected = _GitHubRedirectHandler().redirect_request(
        request, body, 301, "Moved", _headers(), "https://api.github.com/repositories/7"
    )
    assert redirected is not None
    assert redirected.full_url == "https://api.github.com/repositories/7"


@pytest.mark.parametrize(
    "header",
    [
        '<https://api.github.com/users/Mioji/repos?page=2>; title="a, b"; REL="NEXT"',
        '<https://api.github.com/users/Mioji/repos?page=3>; rel="next-page", '
        '<https://api.github.com/users/Mioji/repos?page=2>; rel="collection next"',
    ],
)
def test_next_page_follows_rfc8288_link_parameters(header: str) -> None:
    assert next_page(header) == "/users/Mioji/repos?page=2"


def test_next_page_ignores_rel_next_page_when_no_next_exists() -> None:
    assert next_page('<https://api.github.com/users/Mioji/repos?page=2>; rel="next-page"') is None


def test_pagination_handles_lowercase_and_duplicate_link_headers() -> None:
    first_link = '<https://api.github.com/users/Mioji/repos?page=2>; rel="next"'
    single = FakeOpener(FakeResponse([1] * 100, [("link", first_link)]), FakeResponse([2]))
    single_items = client(single, []).get_pages(
        "/users/Mioji/repos", max_pages=5, allow_truncation=False
    )
    assert single_items == [1] * 100 + [2]

    headers = [
        ("link", '<https://api.github.com/users/Mioji/repos?page=1>; rel="prev"'),
        ("Link", '<https://api.github.com/user/42/repos?per_page=100&page=2+extra>; rel="next"'),
    ]
    opener = FakeOpener(FakeResponse([1] * 100, headers), FakeResponse([2]))
    items = client(opener, []).get_pages("/users/Mioji/repos", max_pages=5, allow_truncation=False)
    assert items == [1] * 100 + [2]
    assert opener.requests[1].full_url.endswith("/user/42/repos?per_page=100&page=2+extra")


@pytest.mark.parametrize(
    "headers",
    [
        {"Retry-After": "²"},
        {"Retry-After": "9" * 5000},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "²"},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9" * 5000},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "bad"},
    ],
)
def test_non_ascii_or_huge_rate_limit_headers_abort_cleanly(headers: dict[str, str]) -> None:
    sleeps: list[float] = []
    opener = FakeOpener(http_error(403, headers))
    with pytest.raises(RateLimitError):
        client(opener, sleeps).get("/users/Mioji")
    assert sleeps == []


def test_naive_http_date_retry_after_is_treated_as_utc() -> None:
    sleeps: list[float] = []
    # Clock is fixed at 1000.0; 1970-01-01 00:16:45 GMT is 1005.0 POSIX seconds.
    opener = FakeOpener(
        http_error(429, {"Retry-After": "Thu, 01 Jan 1970 00:16:45"}),
        FakeResponse({"ok": True}),
    )
    assert client(opener, sleeps).get("/users/Mioji") == {"ok": True}
    assert sleeps == [5.0]


def test_rate_limit_on_final_attempt_raises_network_error() -> None:
    opener = FakeOpener(*(http_error(429, {"Retry-After": "0"}) for _ in range(MAX_ATTEMPTS)))
    with pytest.raises(NetworkError, match="HTTP 429"):
        client(opener, []).get("/users/Mioji")


def test_ssl_error_retries_and_wraps_as_network_error() -> None:
    sleeps: list[float] = []
    opener = FakeOpener(ssl.SSLError("EOF"), FakeResponse({"ok": True}))
    assert client(opener, sleeps).get("/users/Mioji") == {"ok": True}
    assert len(sleeps) == 1

    failing = FakeOpener(*(ssl.SSLError("EOF") for _ in range(MAX_ATTEMPTS)))
    with pytest.raises(NetworkError, match="failed"):
        client(failing, []).get("/users/Mioji")


def test_truncated_error_json_falls_back_to_regex_detail() -> None:
    opener = FakeOpener(http_error(403, body=b'{"message": "Bad \\"token\\" denied", "doc":'))
    with pytest.raises(NetworkError, match='HTTP 403: Bad "token" denied'):
        client(opener, []).get("/users/Mioji")


@pytest.mark.parametrize(
    ("token", "user_agent"),
    [
        ("bad\ntoken", "profilegen"),
        ("bad token", "profilegen"),
        (None, "bad\ragent"),
        (None, " "),
    ],
)
def test_header_injection_in_token_or_user_agent_is_rejected(
    token: str | None, user_agent: str
) -> None:
    with pytest.raises(SecurityPolicyError):
        GitHubClient(token, user_agent=user_agent, opener=FakeOpener())


def test_pagination_detects_loop_and_rejects_nonpositive_limit() -> None:
    loop_headers = {"Link": '<https://api.github.com/users/Mioji/repos?page=1>; rel="next"'}
    opener = FakeOpener(*(FakeResponse([index], loop_headers) for index in range(5)))
    with pytest.raises(NetworkError, match="loop"):
        client(opener, []).get_pages("/users/Mioji/repos", max_pages=5, allow_truncation=True)
    assert len(opener.requests) == 2

    with pytest.raises(ValueError, match="positive"):
        client(FakeOpener(), []).get_pages(
            "/users/Mioji/repos", max_pages=0, allow_truncation=False
        )


def test_429_without_rate_limit_headers_retries_with_backoff() -> None:
    sleeps: list[float] = []
    opener = FakeOpener(http_error(429), FakeResponse({"ok": True}))
    assert client(opener, sleeps).get("/users/Mioji") == {"ok": True}
    assert sleeps == [0.75]


def test_next_page_ignores_quotes_inside_link_uri() -> None:
    header = (
        '<https://example.org/x?a=">; rel="prev", '
        '<https://api.github.com/users/Mioji/repos?page=2>; rel="next"'
    )
    assert next_page(header) == "/users/Mioji/repos?page=2"


@pytest.mark.parametrize(
    "target",
    [
        "http://api.github.com/users/Mioji",
        "https://api.github.com:443/users/Mioji",
        "https://user@api.github.com/users/Mioji",
        "https://api.github.com/users/Mioji#frag",
    ],
)
def test_redirects_reject_scheme_port_userinfo_and_fragment(target: str) -> None:
    request = Request("https://api.github.com/users/Mioji", headers={"Authorization": "x"})
    body = io.BytesIO()
    with pytest.raises(SecurityPolicyError):
        _GitHubRedirectHandler().redirect_request(request, body, 302, "Found", _headers(), target)
    assert body.closed


def test_describe_strips_bidi_and_c1_controls() -> None:
    body = b'{"message": "Denied\\u0085\\u202ehidden"}'
    opener = FakeOpener(http_error(403, body=body))
    with pytest.raises(NetworkError) as exc_info:
        client(opener, []).get("/users/Mioji")
    message = str(exc_info.value)
    assert "\x85" not in message
    assert "\u202e" not in message


def test_token_does_not_leak_in_errors() -> None:
    bearer = "ghp_example_value_123"
    gh = client(FakeOpener(http_error(401, body=b'{"message": "Bad credentials"}')), [], bearer)
    assert bearer not in repr(gh)
    with pytest.raises(NetworkError) as exc_info:
        gh.get("/users/Mioji")
    assert bearer not in str(exc_info.value)
