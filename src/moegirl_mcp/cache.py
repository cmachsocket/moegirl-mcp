# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""SQLite backed cache of wiki pages."""

from __future__ import annotations

import sqlite3
import sys
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

#: Seconds a locked database is waited for before giving up.
BUSY_TIMEOUT = 30.0

#: Seconds before a failed attempt to open the database is repeated.
RETRY_INTERVAL = 5.0


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
    keeps its own SQLite connection. A cache that cannot be opened is not
    fatal, the wiki lookup works without it, so failures are reported and
    the store degrades to a no-op.
    """

    def __init__(self, path: str | Path, ttl: float | None = None) -> None:
        self.path = Path(path)
        self.ttl = cache_ttl() if ttl is None else ttl
        self._local = threading.local()
        self._state_lock = threading.Lock()
        self._ready = False
        self._error: str | None = None
        self._retry_at = 0.0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._error = f"{type(error).__name__}: {error}"
        if not self._ensure_ready():
            _warn(f"cache disabled ({self.path}): {self._error}")

    @property
    def available(self) -> bool:
        """Whether the cache database is usable."""
        return self._ready

    def get(self, title: str) -> CacheEntry | None:
        """Return the cached page for ``title`` and count the hit."""
        if not self._ensure_ready():
            return None
        conn = self._connect()
        if conn is None:
            return None
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
        if not self._ensure_ready():
            return
        conn = self._connect()
        if conn is None:
            return
        try:
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
        except sqlite3.Error as error:
            self._error = f"{type(error).__name__}: {error}"

    def stats(self) -> dict:
        """Summarise the content of the cache."""
        base = {
            "path": str(self.path),
            "ttl_seconds": self.ttl,
            "available": self.available,
            "error": self._error,
        }
        conn = self._connect() if self._ensure_ready() else None
        if conn is None:
            return base | {
                "pages": 0,
                "fresh_pages": 0,
                "characters": 0,
                "hits": 0,
                "oldest": None,
                "newest": None,
            }
        row = conn.execute(
            "SELECT COUNT(*) AS pages, COALESCE(SUM(length(extract)), 0) AS chars,"
            " COALESCE(SUM(hits), 0) AS hits, MIN(fetched_at) AS oldest, MAX(fetched_at) AS newest"
            " FROM pages"
        ).fetchone()
        fresh = conn.execute(
            "SELECT COUNT(*) FROM pages WHERE fetched_at > ?",
            (time.time() - self.ttl,),
        ).fetchone()[0]
        return base | {
            "pages": row["pages"],
            "fresh_pages": fresh,
            "characters": row["chars"],
            "hits": row["hits"],
            "oldest": row["oldest"],
            "newest": row["newest"],
        }

    def clear(self) -> int:
        """Drop every cached page and return how many were removed."""
        if not self._ensure_ready():
            return 0
        conn = self._connect()
        if conn is None:
            return 0
        count = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        conn.execute("DELETE FROM pages")
        conn.commit()
        return count

    def purge_expired(self, ttl: float | None = None) -> int:
        """Drop entries older than ``ttl`` and return how many were removed."""
        if not self._ensure_ready():
            return 0
        conn = self._connect()
        if conn is None:
            return 0
        cutoff = time.time() - (self.ttl if ttl is None else ttl)
        count = conn.execute(
            "SELECT COUNT(*) FROM pages WHERE fetched_at <= ?", (cutoff,)
        ).fetchone()[0]
        conn.execute("DELETE FROM pages WHERE fetched_at <= ?", (cutoff,))
        conn.commit()
        return count

    def _ensure_ready(self) -> bool:
        """Open the database and create the schema, retrying after a failure."""
        if self._ready:
            return True
        with self._state_lock:
            if self._ready:
                return True
            now = time.time()
            if now < self._retry_at:
                return False
            self._retry_at = now + RETRY_INTERVAL
            conn = self._connect()
            if conn is None:
                return False
            try:
                with conn:
                    conn.executescript(SCHEMA)
            except sqlite3.Error as error:
                self._error = f"{type(error).__name__}: {error}"
                return False
            self._ready = True
            self._error = None
            return True

    def _connect(self) -> sqlite3.Connection | None:
        """Return the connection of the calling thread, opening it if needed."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        try:
            conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._configure(conn)
        except sqlite3.Error as error:
            self._error = f"{type(error).__name__}: {error}"
            if conn is not None:
                conn.close()
            return None
        self._local.conn = conn
        return conn

    def _configure(self, conn: sqlite3.Connection) -> None:
        """Prepare a connection for use.

        Switching the journal mode to WAL needs an exclusive lock on the
        database file. That lock is not taken through the busy handler, so
        the switch fails at once whenever another process holds the file,
        for instance a second instance of the server started at the same
        time. The default journal mode is slower but works in every case,
        therefore a refused switch is not an error.
        """
        conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT * 1000)}")
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if mode != "wal":
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        conn.execute("PRAGMA synchronous=NORMAL")


def _warn(message: str) -> None:
    """Report a problem on stderr, which the MCP protocol does not use."""
    print(f"moegirl-mcp: {message}", file=sys.stderr, flush=True)
