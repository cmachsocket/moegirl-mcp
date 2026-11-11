# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Tests for the MediaWiki API client."""

from __future__ import annotations

import json
import time
from typing import Any

from moegirl_mcp.mediawiki import (
    MediaWikiClient,
    MediaWikiError,
    RateLimitedError,
    RateLimiter,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = json.dumps(payload).encode("utf-8")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: int | None = None):
        self.requests.append({"url": url, "params": params or {}})
        return FakeResponse(self.payload, self.status_code)


class ScriptedSession:
    """Answer successive requests with a prepared list of payloads."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.headers: dict[str, str] = {}
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: int | None = None):
        self.requests.append({"url": url, "params": params or {}})
        index = min(len(self.requests) - 1, len(self.payloads) - 1)
        return FakeResponse(self.payloads[index])


def make_client(
    payload: dict[str, Any], status_code: int = 200
) -> tuple[MediaWikiClient, FakeSession]:
    client = MediaWikiClient()
    session = FakeSession(payload, status_code)
    client._session = lambda: session  # type: ignore[method-assign]
    return client, session


def test_fetch_pages_reads_pages() -> None:
    payload = {
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "lastrevid": 8741370,
                    "title": "明日方舟",
                    "extract": "  《明日方舟》是由鹰角网络开发的游戏。  ",
                },
            }
        }
    }
    client, session = make_client(payload)
    pages = client.fetch_pages(["明日方舟"])

    page = pages["明日方舟"]
    assert page.extract == "《明日方舟》是由鹰角网络开发的游戏。"
    assert page.pageid == 1
    assert page.lastrevid == 8741370
    assert page.canonical == "明日方舟"
    assert not page.missing
    assert session.requests[0]["params"]["titles"] == "明日方舟"
    assert session.requests[0]["params"]["prop"] == "extracts|info"
    assert session.requests[0]["params"]["exintro"] == "1"


def test_fetch_pages_resolves_redirects() -> None:
    payload = {
        "query": {
            "normalized": [{"from": "eva", "to": "EVA"}],
            "redirects": [{"from": "EVA", "to": "新世纪福音战士"}],
            "pages": {
                "1": {
                    "pageid": 1,
                    "title": "新世纪福音战士",
                    "extract": "《新世福音战士》",
                },
            },
        }
    }
    client, _ = make_client(payload)
    pages = client.fetch_pages(["EVA", "eva"])

    assert pages["EVA"].title == "新世纪福音战士"
    assert pages["EVA"].canonical == "新世纪福音战士"
    assert pages["eva"].canonical == "新世纪福音战士"


def test_fetch_pages_reports_missing_pages() -> None:
    payload = {"query": {"pages": {"-1": {"title": "不存在", "missing": True}}}}
    client, _ = make_client(payload)
    pages = client.fetch_pages(["不存在"])

    assert pages["不存在"].missing
    assert pages["不存在"].extract == ""


def test_fetch_pages_raises_on_api_errors() -> None:
    payload = {"error": {"code": "action-notallowed", "info": "not allowed"}}
    client, _ = make_client(payload)

    try:
        client.fetch_pages(["明日方舟"])
    except MediaWikiError as error:
        assert "action-notallowed" in str(error)
    else:
        raise AssertionError("expected a MediaWikiError")


def test_fetch_pages_without_titles_is_a_noop() -> None:
    client, session = make_client({})
    assert client.fetch_pages([]) == {}
    assert session.requests == []


def test_fetch_pages_reports_rate_limiting() -> None:
    client, _ = make_client({}, status_code=429)
    try:
        client.fetch_pages(["明日方舟"])
    except RateLimitedError:
        pass
    else:
        raise AssertionError("expected a RateLimitedError")


def test_fetch_pages_follows_the_continuation_block() -> None:
    session = ScriptedSession(
        [
            {
                "query": {"pages": {"1": {"pageid": 1, "title": "A", "extract": "a"}}},
                "continue": {"excontinue": "20", "continue": "||"},
            },
            {"query": {"pages": {"2": {"pageid": 2, "title": "B", "extract": "b"}}}},
        ]
    )
    client = MediaWikiClient()
    client._session = lambda: session  # type: ignore[method-assign]

    pages = client.fetch_pages(["A", "B"])

    assert pages["A"].extract == "a"
    assert pages["B"].extract == "b"
    assert len(session.requests) == 2
    assert session.requests[1]["params"]["excontinue"] == "20"


def test_search_titles_returns_titles() -> None:
    payload = {
        "query": {
            "pages": {
                "1": {"pageid": 1, "title": "兰州舰娘"},
                "2": {"pageid": 2, "title": "舰娘"},
            }
        }
    }
    client, session = make_client(payload)
    titles = client.search_titles("舰娘")

    assert titles == ["兰州舰娘", "舰娘"]
    assert session.requests[0]["params"]["generator"] == "search"
    assert session.requests[0]["params"]["gsrsearch"] == "舰娘"
    assert session.requests[0]["params"]["prop"] == "info"


def test_search_titles_paginates() -> None:
    session = ScriptedSession(
        [
            {
                "query": {"pages": {"1": {"pageid": 1, "title": "A"}}},
                "continue": {"gsroffset": 1, "continue": "gsroffset||"},
            },
            {"query": {"pages": {"2": {"pageid": 2, "title": "B"}}}},
        ]
    )
    client = MediaWikiClient()
    client._session = lambda: session  # type: ignore[method-assign]

    titles = client.search_titles("x", limit=2)

    assert titles == ["A", "B"]
    assert len(session.requests) == 2
    assert session.requests[1]["params"]["gsroffset"] == 1


def test_search_titles_ignores_an_empty_query() -> None:
    client, session = make_client({})
    assert client.search_titles("  ") == []
    assert session.requests == []


def test_rate_limiter_paces_requests() -> None:
    limiter = RateLimiter(rate=20.0)
    started = time.monotonic()
    for _ in range(4):
        limiter.wait()
    assert time.monotonic() - started >= 0.1


def test_rate_limiter_cooldown_blocks_other_threads() -> None:
    limiter = RateLimiter(rate=100.0)
    limiter.cooldown(0.3)
    started = time.monotonic()
    limiter.wait()
    assert time.monotonic() - started >= 0.25
