# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Tests for the local page cache."""

from __future__ import annotations

import time
from pathlib import Path

from moegirl_mcp.cache import Cache


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

    assert cache.get("A").hits == 1
    assert cache.get("A").hits == 2


def test_get_returns_none_for_an_unknown_title(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    assert cache.get("不存在") is None


def test_put_replaces_an_earlier_entry(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    cache.put("A", "old")
    cache.put("A", "new")

    assert cache.get("A").extract == "new"
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
    connection.execute(
        "UPDATE pages SET fetched_at = ? WHERE title = 'stale'", (time.time() - 7200,)
    )
    connection.commit()

    assert cache.purge_expired() == 1
    assert cache.stats()["pages"] == 1


def test_the_cache_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    Cache(path).put("A", "a")

    assert Cache(path).get("A").extract == "a"
