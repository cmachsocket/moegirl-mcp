# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Tests for the cache diagnostics."""

from __future__ import annotations

from pathlib import Path

from moegirl_mcp.diagnostics import diagnose, filesystem_type


def test_diagnose_reports_a_healthy_location(tmp_path: Path) -> None:
    report = diagnose(tmp_path / "cache.db")

    assert report["healthy"]
    assert report["failed"] == []
    assert report["path"].endswith("cache.db")
    assert report["sqlite_version"]
    assert report["filesystem"]


def test_diagnose_reports_an_unusable_location(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")

    report = diagnose(blocker / "cache.db")

    assert not report["healthy"]
    assert report["failed"]
    assert report["hint"]
    assert not report["directory_writable"]


def test_diagnose_describes_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    path.touch()

    report = diagnose(path)

    assert report["exists"]
    assert report["database"]["ok"]
    assert "database does not exist yet" not in report["database"]["detail"]


def test_filesystem_type_is_known(tmp_path: Path) -> None:
    assert filesystem_type(tmp_path) != ""
