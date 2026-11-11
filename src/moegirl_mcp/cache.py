# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""SQLite backed cache of wiki pages."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import cache_ttl

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    title      TEXT PRIMARY KEY,
    extract    TEXT NOT NULL DEFAULT '',
    pageid     INTEGER,
    lastrevid  INTEGER,
    canonical  TEXT,
    fetched_at REAL NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pages_fetched_at ON pages(fetched_at);
"""


@dataclass(frozen=True)
class CacheEntry:
    """One cached page."""

    title: str
    extract: str
    pageid: int | None = None
    lastrevid: int | None = None
    canonical: str | None = None
    fetched_at: float = 0.0
    hits: int = 0

    def age(self, now: float | None = None) -> float:
        """Seconds since the entry was stored."""
        return (now if now is not None else time.time()) - self.fetched_at

    def is_fresh(self, ttl: float, now: float | None = None) -> bool:
        """Whether the entry may still be served without asking the wiki."""
        return self.age(now) < ttl


class Cache:
    """A small key-value store keeping the lead section of wiki pages.

    The cache is shared by every thread of the server, therefore each thread
    keeps its own SQLite connection.
    """

    def __init__(self, path: str | Path, ttl: float | None = None) -> None:
        self.path = Path(path)
        self.ttl = cache_ttl() if ttl is None else ttl
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def get(self, title: str) -> CacheEntry | None:
        """Return the cached page for ``title`` and count the hit."""
        conn = self._connect()
        row = conn.execute(
            "SELECT title, extract, pageid, lastrevid, canonical, fetched_at, hits"
            " FROM pages WHERE title = ?",
            (title,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE pages SET hits = hits + 1 WHERE title = ?", (title,))
        conn.commit()
        return CacheEntry(
            title=row["title"],
            extract=row["extract"],
            pageid=row["pageid"],
            lastrevid=row["lastrevid"],
            canonical=row["canonical"],
            fetched_at=row["fetched_at"],
            hits=row["hits"] + 1,
        )

    def put(
        self,
        title: str,
        extract: str,
        pageid: int | None = None,
        lastrevid: int | None = None,
        canonical: str | None = None,
    ) -> None:
        """Store a page, replacing any earlier entry with the same title."""
        conn = self._connect()
        conn.execute(
            "INSERT INTO pages (title, extract, pageid, lastrevid, canonical, fetched_at, hits)"
            " VALUES (?, ?, ?, ?, ?, ?, 0)"
            " ON CONFLICT(title) DO UPDATE SET"
            " extract = excluded.extract, pageid = excluded.pageid,"
            " lastrevid = excluded.lastrevid, canonical = excluded.canonical,"
            " fetched_at = excluded.fetched_at",
            (title, extract, pageid, lastrevid, canonical or title, time.time()),
        )
        conn.commit()

    def stats(self) -> dict:
        """Summarise the content of the cache."""
        conn = self._connect()
        row = conn.execute(
            "SELECT COUNT(*) AS pages, COALESCE(SUM(length(extract)), 0) AS chars,"
            " COALESCE(SUM(hits), 0) AS hits, MIN(fetched_at) AS oldest, MAX(fetched_at) AS newest"
            " FROM pages"
        ).fetchone()
        fresh = conn.execute(
            "SELECT COUNT(*) FROM pages WHERE fetched_at > ?",
            (time.time() - self.ttl,),
        ).fetchone()[0]
        return {
            "pages": row["pages"],
            "fresh_pages": fresh,
            "characters": row["chars"],
            "hits": row["hits"],
            "oldest": row["oldest"],
            "newest": row["newest"],
            "ttl_seconds": self.ttl,
            "path": str(self.path),
        }

    def clear(self) -> int:
        """Drop every cached page and return how many were removed."""
        conn = self._connect()
        count = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        conn.execute("DELETE FROM pages")
        conn.commit()
        return count

    def purge_expired(self, ttl: float | None = None) -> int:
        """Drop entries older than ``ttl`` and return how many were removed."""
        conn = self._connect()
        cutoff = time.time() - (self.ttl if ttl is None else ttl)
        count = conn.execute(
            "SELECT COUNT(*) FROM pages WHERE fetched_at <= ?", (cutoff,)
        ).fetchone()[0]
        conn.execute("DELETE FROM pages WHERE fetched_at <= ?", (cutoff,))
        conn.commit()
        return count
