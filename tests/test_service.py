# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Tests for the dictionary service."""

from __future__ import annotations

from pathlib import Path

import pytest

from moegirl_mcp.cache import Cache
from moegirl_mcp.mediawiki import MediaWikiError, Page
from moegirl_mcp.service import DictionaryService


class FakeClient:
    """A wiki client answering from a prepared table."""

    def __init__(
        self,
        pages: dict[str, Page] | None = None,
        titles: list[str] | None = None,
        error: Exception | None = None,
        search_error: Exception | None = None,
    ) -> None:
        self.pages = pages or {}
        self.titles = titles or []
        self.error = error
        self.search_error = search_error
        self.calls: list[tuple] = []

    def fetch_pages(self, titles: list[str]) -> dict[str, Page]:
        self.calls.append(("fetch", list(titles)))
        if self.error is not None:
            raise self.error
        return {
            title: self.pages.get(title, Page(title=title, extract="", missing=True))
            for title in titles
        }

    def search_titles(self, query: str, limit: int = 10) -> list[str]:
        self.calls.append(("search", query, limit))
        if self.search_error is not None:
            raise self.search_error
        return self.titles[:limit]


@pytest.fixture()
def cache(tmp_path: Path) -> Cache:
    return Cache(tmp_path / "cache.db", ttl=3600)


def make_service(client: FakeClient, cache: Cache) -> DictionaryService:
    return DictionaryService(client=client, cache=cache, ttl=3600)


def test_lookup_fetches_the_page_and_caches_it(cache: Cache) -> None:
    client = FakeClient(pages={"明日方舟": Page(title="明日方舟", extract="《明日方舟》是游戏。")})
    service = make_service(client, cache)

    payload = service.lookup("明日方舟")

    assert payload["source"] == "live"
    assert payload["count"] == 1
    assert payload["results"][0]["title"] == "明日方舟"
    assert payload["results"][0]["matched_by"] == "title"
    assert payload["results"][0]["url"].endswith("明日方舟")

    second = service.lookup("明日方舟")
    assert second["source"] == "cache"
    assert len(client.calls) == 1


def test_lookup_falls_back_to_a_search(cache: Cache) -> None:
    client = FakeClient(
        pages={"舰娘": Page(title="舰娘", extract="“舰娘”是…")},
        titles=["舰娘", "兰州舰娘"],
    )
    service = make_service(client, cache)

    payload = service.lookup("舰娘们")

    assert payload["source"] == "live-search"
    assert payload["results"][0]["matched_by"] == "search"
    assert ("search", "舰娘们", 5) in client.calls


def test_lookup_stores_the_alias_of_a_redirect(cache: Cache) -> None:
    client = FakeClient(
        pages={
            "EVA": Page(
                title="新世纪福音战士",
                extract="《新世纪福音战士》…",
                canonical="新世纪福音战士",
            )
        }
    )
    service = make_service(client, cache)

    payload = service.lookup("EVA")

    assert payload["results"][0]["title"] == "新世纪福音战士"
    assert cache.get("EVA") is not None
    assert cache.get("新世纪福音战士") is not None


def test_lookup_serves_the_stale_cache_when_the_wiki_is_down(cache: Cache) -> None:
    cache.put("明日方舟", "旧的释义")
    client = FakeClient(error=MediaWikiError("boom"))
    service = DictionaryService(client=client, cache=cache, ttl=0)

    payload = service.lookup("明日方舟")

    assert payload["source"] == "stale-cache"
    assert payload["results"][0]["extract"] == "旧的释义"
    assert "boom" in payload["warning"]


def test_lookup_reports_a_broken_wiki(cache: Cache) -> None:
    client = FakeClient(error=MediaWikiError("boom"))
    service = make_service(client, cache)

    with pytest.raises(MediaWikiError):
        service.lookup("明日方舟")


def test_lookup_rejects_an_empty_term(cache: Cache) -> None:
    service = make_service(FakeClient(), cache)

    with pytest.raises(ValueError):
        service.lookup("   ")


def test_lookup_truncates_and_condenses_the_extract(cache: Cache) -> None:
    client = FakeClient(pages={"A": Page(title="A", extract="第一行\n第二行  第三行")})
    service = make_service(client, cache)

    payload = service.lookup("A", extract_chars=6)

    assert payload["results"][0]["extract"] == "第一行 第二…"


def test_search_filters_subpages(cache: Cache) -> None:
    client = FakeClient(
        pages={
            "明日方舟": Page(title="明日方舟", extract="《明日方舟》是游戏。"),
            "明日方舟/剧情": Page(title="明日方舟/剧情", extract="剧情简介。"),
        },
        titles=["明日方舟", "明日方舟/剧情"],
    )
    service = make_service(client, cache)

    payload = service.search("明日方舟")

    assert [result["title"] for result in payload["results"]] == ["明日方舟"]


def test_search_can_include_subpages(cache: Cache) -> None:
    client = FakeClient(
        pages={
            "明日方舟": Page(title="明日方舟", extract="《明日方舟》是游戏。"),
            "明日方舟/剧情": Page(title="明日方舟/剧情", extract="剧情简介。"),
        },
        titles=["明日方舟", "明日方舟/剧情"],
    )
    service = make_service(client, cache)

    payload = service.search("明日方舟", include_subpages=True)

    assert [result["title"] for result in payload["results"]] == [
        "明日方舟",
        "明日方舟/剧情",
    ]


def test_search_reuses_cached_extracts(cache: Cache) -> None:
    client = FakeClient(
        pages={"舰娘": Page(title="舰娘", extract="“舰娘”是…")},
        titles=["舰娘"],
    )
    service = make_service(client, cache)

    service.search("舰娘")
    service.search("舰娘")

    # The second search only asks for the titles, the extract is already
    # cached.
    assert client.calls.count(("fetch", ["舰娘"])) == 1
    assert client.calls.count(("search", "舰娘", 10)) == 2


def test_search_rejects_an_empty_query(cache: Cache) -> None:
    service = make_service(FakeClient(), cache)

    with pytest.raises(ValueError):
        service.search("  ")


def test_search_refetches_a_stale_entry(cache: Cache) -> None:
    cache.put("舰娘", "旧的释义")
    client = FakeClient(pages={"舰娘": Page(title="舰娘", extract="新的释义")}, titles=["舰娘"])
    service = DictionaryService(client=client, cache=cache, ttl=0)

    payload = service.search("舰娘")

    assert payload["results"][0]["extract"] == "新的释义"


def test_search_falls_back_to_the_stale_cache_when_the_wiki_is_down(cache: Cache) -> None:
    cache.put("舰娘", "旧的释义")
    client = FakeClient(error=MediaWikiError("boom"), titles=["舰娘"])
    service = DictionaryService(client=client, cache=cache, ttl=0)

    payload = service.search("舰娘")

    assert payload["results"][0]["extract"] == "旧的释义"


def test_stats_report_the_cache(cache: Cache) -> None:
    client = FakeClient(pages={"A": Page(title="A", extract="a")})
    service = make_service(client, cache)
    service.lookup("A")

    stats = service.stats()

    assert stats["cache"]["pages"] == 1
    assert stats["ttl_seconds"] == 3600
    assert stats["api"].endswith("/api.php")


def test_the_lookup_survives_a_broken_cache(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    client = FakeClient(pages={"明日方舟": Page(title="明日方舟", extract="《明日方舟》是游戏。")})
    service = DictionaryService(client=client, cache=Cache(blocker / "cache.db"), ttl=3600)

    payload = service.lookup("明日方舟")

    assert payload["source"] == "live"
    assert payload["results"][0]["extract"] == "《明日方舟》是游戏。"
