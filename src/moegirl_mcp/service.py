# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Lookup and search orchestration on top of the wiki client and the cache."""

from __future__ import annotations

from typing import Protocol

from .cache import Cache, CacheEntry
from .config import (
    API_URL,
    DEFAULT_EXTRACT_CHARS,
    DEFAULT_LOOKUP_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    MAX_LIMIT,
    cache_path,
    cache_ttl,
)
from .mediawiki import MediaWikiClient, MediaWikiError, Page

WIKI_BASE_URL = "https://zh.moegirl.org.cn"


class WikiClient(Protocol):
    """The part of the wiki client the service depends on."""

    def fetch_pages(self, titles: list[str]) -> dict[str, Page]:
        """Return one page per requested title, keyed by the requested title."""
        ...

    def search_titles(self, query: str, limit: int = 10) -> list[str]:
        """Return the titles matching a search query, best match first."""
        ...


class DictionaryService:
    """Answer dictionary queries, using the wiki online and a local cache."""

    def __init__(
        self,
        client: WikiClient | None = None,
        cache: Cache | None = None,
        ttl: float | None = None,
        extract_chars: int = DEFAULT_EXTRACT_CHARS,
    ) -> None:
        self.ttl = cache_ttl() if ttl is None else ttl
        self.client = client or MediaWikiClient()
        self.cache = cache or Cache(cache_path(), ttl=self.ttl)
        self.extract_chars = extract_chars

    def lookup(
        self,
        term: str,
        limit: int = DEFAULT_LOOKUP_LIMIT,
        extract_chars: int | None = None,
        include_subpages: bool = False,
    ) -> dict:
        """Look a term up, falling back to a full text search.

        The page is fetched by its exact title first. When the wiki has no
        page under that title, the term is used as a search query instead.
        """
        term = (term or "").strip()
        if not term:
            raise ValueError("term must not be empty")
        limit = _clamp_limit(limit)
        chars = self.extract_chars if extract_chars is None else extract_chars

        cached = self.cache.get(term)
        if cached is not None and cached.is_fresh(self.ttl):
            return _payload(
                term,
                [_result(cached.title, cached.extract, "title", cached.pageid, chars)],
                source="cache",
            )

        try:
            pages = self.client.fetch_pages([term])
        except MediaWikiError as error:
            if cached is not None:
                # The wiki is unreachable, but an older answer is still
                # better than no answer at all.
                return _payload(
                    term,
                    [_result(cached.title, cached.extract, "title", cached.pageid, chars)],
                    source="stale-cache",
                    warning=str(error),
                )
            raise

        page = pages.get(term)
        if page is not None and not page.missing:
            self._store(term, page)
            return _payload(
                term,
                [_result(page.title, page.extract, "title", page.pageid, chars)],
                source="live",
            )

        results = self._search_live(term, limit, include_subpages, chars)
        return _payload(term, results, source="live-search")

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        extract_chars: int | None = None,
        include_subpages: bool = False,
    ) -> dict:
        """Search the wiki and return the lead section of every hit."""
        query = (query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        limit = _clamp_limit(limit)
        chars = self.extract_chars if extract_chars is None else extract_chars

        results = self._search_live(query, limit, include_subpages, chars)
        return _payload(query, results, source="live-search")

    def stats(self) -> dict:
        """Report the state of the local cache."""
        cache_stats = self.cache.stats()
        return {
            "api": API_URL,
            "ttl_seconds": self.ttl,
            "cache": cache_stats,
        }

    def _search_live(
        self,
        query: str,
        limit: int,
        include_subpages: bool,
        extract_chars: int | None,
    ) -> list[dict]:
        """Run a search and resolve the lead section of every hit."""
        titles = self.client.search_titles(query, limit)
        if not include_subpages:
            titles = [title for title in titles if "/" not in title]

        results: list[dict] = []
        pending: list[str] = []
        cached_entries: dict[str, CacheEntry] = {}
        stale_entries: dict[str, CacheEntry] = {}

        for title in titles:
            cached = self.cache.get(title)
            if cached is None:
                pending.append(title)
            elif cached.is_fresh(self.ttl):
                cached_entries[title] = cached
            else:
                # A stale entry is refetched, but kept as a fallback in case
                # the wiki stays unreachable.
                stale_entries[title] = cached
                pending.append(title)

        if pending:
            try:
                pages = self.client.fetch_pages(pending)
            except MediaWikiError:
                # The wiki is unreachable. Older answers are still better than
                # no answer at all.
                pages = {}
            for title in pending:
                page = pages.get(title)
                if page is None or page.missing:
                    continue
                self._store(title, page)
                cached_entries[title] = CacheEntry(
                    title=page.title,
                    extract=page.extract,
                    pageid=page.pageid,
                    canonical=page.canonical,
                )

        for title, entry in stale_entries.items():
            cached_entries.setdefault(title, entry)

        for title in titles:
            entry = cached_entries.get(title)
            if entry is None:
                continue
            results.append(
                _result(entry.title, entry.extract, "search", entry.pageid, extract_chars)
            )
        return results

    def _store(self, requested: str, page: Page) -> None:
        """Store a page under its canonical title and under the alias used."""
        canonical = page.canonical or page.title
        self.cache.put(
            canonical,
            page.extract,
            pageid=page.pageid,
            lastrevid=page.lastrevid,
            canonical=canonical,
        )
        if requested != canonical:
            self.cache.put(
                requested,
                page.extract,
                pageid=page.pageid,
                lastrevid=page.lastrevid,
                canonical=canonical,
            )


def _clamp_limit(limit: int) -> int:
    """Keep the requested number of results inside the supported range."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LOOKUP_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _result(
    title: str,
    extract: str,
    matched_by: str,
    pageid: int | None,
    extract_chars: int | None,
) -> dict:
    """Render one entry for the tool output."""
    chars = extract_chars if extract_chars is not None else DEFAULT_EXTRACT_CHARS
    return {
        "title": title,
        "url": f"{WIKI_BASE_URL}/{title.replace(' ', '_')}",
        "pageid": pageid,
        "matched_by": matched_by,
        "extract": _condense(_truncate(extract, chars)),
    }


def _payload(
    term: str,
    results: list[dict],
    source: str,
    warning: str | None = None,
) -> dict:
    """Wrap the results of a query for the tool output."""
    payload = {"term": term, "count": len(results), "source": source, "results": results}
    if warning:
        payload["warning"] = warning
    return payload


def _condense(text: str) -> str:
    """Fold line breaks and runs of whitespace into single spaces."""
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    """Shorten a lead section to ``limit`` characters."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
