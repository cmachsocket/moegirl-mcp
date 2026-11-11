# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Minimal MediaWiki API client for Moegirlpedia."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

import requests

from .config import (
    API_URL,
    REQUEST_TIMEOUT,
    SEARCH_PAGE_LIMIT,
    USER_AGENT,
)


class MediaWikiError(RuntimeError):
    """Raised when the wiki API reports an error."""


class RateLimitedError(MediaWikiError):
    """Raised when the wiki answers with HTTP 429."""


@dataclass(frozen=True)
class Page:
    """A wiki page together with the lead section of its article."""

    title: str
    extract: str
    pageid: int | None = None
    lastrevid: int | None = None
    canonical: str | None = None
    missing: bool = False


class RateLimiter:
    """Limit the number of requests per second across all threads.

    A cooldown raised by one thread pauses every other thread as well, so a
    burst of rate limiting responses stops the whole client at once.
    """

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0
        self._cooldown_until = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now < self._cooldown_until:
                    delay = self._cooldown_until - now
                else:
                    start = max(now, self._next)
                    if start <= now:
                        # Jitter keeps concurrent requests from forming a burst.
                        self._next = now + self._interval * (1.0 + random.uniform(0, 0.25))
                        return
                    delay = start - now
            time.sleep(min(delay, 1.0))

    def cooldown(self, seconds: float) -> None:
        """Block every thread for at least ``seconds`` before the next request."""
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)


class MediaWikiClient:
    """Read page lead sections and run searches through the MediaWiki API."""

    def __init__(
        self,
        api_url: str = API_URL,
        user_agent: str = USER_AGENT,
        timeout: int = REQUEST_TIMEOUT,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.api_url = api_url
        self.user_agent = user_agent
        self.timeout = timeout
        self.limiter = limiter
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": self.user_agent})
            self._local.session = session
        return session

    def fetch_pages(self, titles: list[str]) -> dict[str, Page]:
        """Fetch the lead section of every requested title.

        The result is keyed by the requested title, so a caller can look up
        its own input directly. Redirected and normalised titles carry the
        canonical page title in ``Page.canonical``.
        """
        if not titles:
            return {}

        params = {
            "action": "query",
            "format": "json",
            "prop": "extracts|info",
            "exintro": "1",
            "explaintext": "1",
            "exlimit": "max",
            "redirects": "1",
            "titles": "|".join(titles),
        }

        aliases: dict[str, str] = {}
        pages: dict[str, Page] = {}

        while True:
            data = self._request(params)
            query = data.get("query") or {}

            # Titles requested by the user may be redirected or normalised to a
            # different title before the pages are looked up.
            for section in ("normalized", "redirects"):
                for mapping in query.get(section) or []:
                    aliases[mapping["from"]] = mapping["to"]

            for page in (query.get("pages") or {}).values():
                title = page.get("title", "")
                if not title:
                    continue
                pages[title] = _page_from_payload(page)

            # `prop=extracts` only extracts 20 pages per request, no matter how
            # many titles were asked for. The remaining ones arrive through the
            # continuation block.
            continuation = data.get("continue") or {}
            if "excontinue" not in continuation:
                break
            params = dict(params)
            params["excontinue"] = continuation["excontinue"]

        result: dict[str, Page] = {}
        for title in titles:
            target = _resolve_alias(title, aliases)
            page = pages.get(target)
            if page is None:
                # The API answered neither with a page nor with a "missing"
                # entry, so the title is treated as absent.
                result[title] = Page(title=target, extract="", missing=True)
            else:
                result[title] = Page(
                    title=page.title,
                    extract=page.extract,
                    pageid=page.pageid,
                    lastrevid=page.lastrevid,
                    canonical=page.title,
                    missing=page.missing,
                )
        return result

    def search_titles(self, query: str, limit: int = 10) -> list[str]:
        """Return the titles matching ``query``, best match first."""
        query = query.strip()
        if not query or limit <= 0:
            return []

        titles: list[str] = []
        offset = 0
        while len(titles) < limit:
            chunk = min(limit - len(titles), SEARCH_PAGE_LIMIT)
            params = {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": query,
                "gsrlimit": chunk,
                "prop": "info",
                "redirects": "1",
            }
            if offset:
                params["gsroffset"] = offset

            data = self._request(params)
            payload = data.get("query") or {}
            found = [
                page["title"] for page in (payload.get("pages") or {}).values() if page.get("title")
            ]
            for title in found:
                if title not in titles:
                    titles.append(title)

            continuation = data.get("continue") or {}
            if not found or "gsroffset" not in continuation:
                break
            offset = int(continuation["gsroffset"])

        return titles[:limit]

    def _request(self, params: dict[str, str]) -> dict:
        """Perform one API call and return the decoded payload."""
        if self.limiter is not None:
            self.limiter.wait()

        try:
            response = self._session().get(self.api_url, params=params, timeout=self.timeout)
        except requests.RequestException as error:
            raise MediaWikiError(str(error)) from error

        if response.status_code == 429:
            raise RateLimitedError("429 Too Many Requests")

        response.raise_for_status()
        data = response.json()

        if "error" in data:
            error = data["error"]
            raise MediaWikiError(f"{error.get('code', '?')}: {error.get('info', error)}")

        return data


def _page_from_payload(payload: dict) -> Page:
    """Build a :class:`Page` from one entry of the API ``pages`` mapping."""
    pageid = payload.get("pageid")
    lastrevid = payload.get("lastrevid")
    return Page(
        title=payload.get("title", ""),
        extract=(payload.get("extract") or "").strip(),
        pageid=int(pageid) if pageid is not None else None,
        lastrevid=int(lastrevid) if lastrevid is not None else None,
        missing="missing" in payload,
    )


def _resolve_alias(title: str, aliases: dict[str, str]) -> str:
    """Follow the normalisation and redirect chain of a title."""
    target = title
    seen = {target}
    while target in aliases and aliases[target] not in seen:
        target = aliases[target]
        seen.add(target)
    return target
