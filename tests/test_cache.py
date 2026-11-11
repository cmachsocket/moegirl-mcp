# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Tests for the local page cache."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, cast

from moegirl_mcp.cache import Cache


class _Cursor:
    """A cursor answering with a single canned row."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def fetchone(self) -> tuple[Any, ...]:
        return (self._value,)


class _RefusingConnection:
    """A connection whose journal mode switch is refused by a lock."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> _Cursor:
        self.statements.append(sql)
        if sql.strip().lower() == "pragma journal_mode":
            return _Cursor("delete")
        if "journal_mode=WAL" in sql:
            raise sqlite3.OperationalError("database is locked")
        return _Cursor(None)


def test_put_and_get_round_trip(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    cache.put("明日方舟", "《明日方舟》是由鹰角网络开发的游戏。", pageid=1, lastrevid=42)

    entry = cache.get("明日方舟")

    assert entry is not None
    assert entry.extract == "《明日方舟》是由鹰角网络开发的游戏。"
    assert entry.pageid == 1
    assert entry.lastrevid == 42
    assert entry.canonical == "明日方舟"
    assert entry.hits == 1


def test_get_counts_hits(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    cache.put("A", "a")

    first = cache.get("A")
    second = cache.get("A")

    assert first is not None
    assert second is not None
    assert first.hits == 1
    assert second.hits == 2


def test_get_returns_none_for_an_unknown_title(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    assert cache.get("不存在") is None


def test_put_replaces_an_earlier_entry(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    cache.put("A", "old")
    cache.put("A", "new")

    entry = cache.get("A")

    assert entry is not None
    assert entry.extract == "new"
    assert cache.stats()["pages"] == 1


def test_entries_go_stale_after_the_ttl(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db", ttl=0.0)
    cache.put("A", "a")

    entry = cache.get("A")

    assert entry is not None
    assert not entry.is_fresh(0.0)
    assert entry.is_fresh(3600)


def test_stats_report_the_cache_content(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db", ttl=3600)
    cache.put("A", "abc")
    cache.put("B", "de")

    stats = cache.stats()

    assert stats["pages"] == 2
    assert stats["fresh_pages"] == 2
    assert stats["characters"] == 5
    assert stats["ttl_seconds"] == 3600
    assert stats["path"].endswith("cache.db")


def test_clear_drops_every_entry(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    cache.put("A", "a")
    cache.put("B", "b")

    assert cache.clear() == 2
    assert cache.stats()["pages"] == 0


def test_purge_removes_only_expired_entries(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db", ttl=3600)
    cache.put("fresh", "a")
    cache.put("stale", "b")

    connection = cache._connect()  # noqa: SLF001
    assert connection is not None
    connection.execute(
        "UPDATE pages SET fetched_at = ? WHERE title = 'stale'", (time.time() - 7200,)
    )
    connection.commit()

    assert cache.purge_expired() == 1
    assert cache.stats()["pages"] == 1


def test_the_cache_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    Cache(path).put("A", "a")

    entry = Cache(path).get("A")

    assert entry is not None
    assert entry.extract == "a"


def test_a_refused_journal_switch_is_not_fatal(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    connection = _RefusingConnection()

    cache._configure(cast(sqlite3.Connection, connection))  # noqa: SLF001

    assert "PRAGMA journal_mode=WAL" in connection.statements
    assert "PRAGMA synchronous=NORMAL" in connection.statements


def test_a_switch_to_wal_is_skipped_when_the_database_is_already_in_it(
    tmp_path: Path,
) -> None:
    cache = Cache(tmp_path / "cache.db")
    connection = _RefusingConnection()

    def execute(sql: str) -> _Cursor:
        connection.statements.append(sql)
        if sql.strip().lower() == "pragma journal_mode":
            return _Cursor("wal")
        return _Cursor(None)

    connection.execute = execute  # type: ignore[method-assign]
    cache._configure(cast(sqlite3.Connection, connection))  # noqa: SLF001

    assert "PRAGMA journal_mode=WAL" not in connection.statements


def test_a_broken_cache_degrades_to_a_no_op(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    cache = Cache(blocker / "cache.db")

    assert not cache.available
    assert cache.get("A") is None
    cache.put("A", "a")
    assert cache.clear() == 0
    assert cache.purge_expired() == 0

    stats = cache.stats()

    assert stats["pages"] == 0
    assert stats["available"] is False
    assert stats["error"]


def test_stats_report_availability(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")

    assert cache.available
    assert cache.stats()["available"] is True
    assert cache.stats()["error"] is None


def test_a_thread_that_cannot_open_the_database_reports_no_entry(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    cache = Cache(blocker / "cache.db")

    assert cache._connect() is None  # noqa: SLF001
    assert cache.get("A") is None
    assert cache.stats()["available"] is False
