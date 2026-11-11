# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Diagnostics for the local cache database.

The cache lives on a file system the user picked, and not every file system
can serve a SQLite database. This module probes the chosen location and
reports what it found, so a broken cache can be told apart from a broken
wiki.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from .config import cache_path


def filesystem_type(path: Path) -> str:
    """Return the type of the file system holding ``path``."""
    target = str(path.resolve())
    best_point = ""
    best_type = "unknown"
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return best_type
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        point = parts[1].replace("\\040", " ")
        if target == point or target.startswith(point.rstrip("/") + "/"):
            if len(point) > len(best_point):
                best_point = point
                best_type = parts[2]
    return best_type


def _probe(action) -> dict:
    """Run one probe and describe the outcome."""
    try:
        return {"ok": True, "detail": action()}
    except (sqlite3.Error, OSError) as error:
        return {"ok": False, "detail": f"{type(error).__name__}: {error}"}


def _write_probe(directory: Path) -> str:
    """Write a row into a throwaway database and read it back."""
    with tempfile.TemporaryDirectory(dir=directory) as workspace:
        probe = Path(workspace) / "probe.db"
        conn = sqlite3.connect(probe, timeout=10)
        try:
            conn.execute("CREATE TABLE probe (value INTEGER)")
            conn.execute("INSERT INTO probe VALUES (1)")
            conn.commit()
            value = conn.execute("SELECT value FROM probe").fetchone()[0]
        finally:
            conn.close()
        return f"wrote and read back {value}"


def _journal_probe(directory: Path, mode: str) -> str:
    """Try to run a throwaway database in the given journal mode."""
    with tempfile.TemporaryDirectory(dir=directory) as workspace:
        probe = Path(workspace) / f"probe-{mode}.db"
        conn = sqlite3.connect(probe, timeout=10)
        try:
            current = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            if current != mode:
                current = str(conn.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]).lower()
            conn.execute("CREATE TABLE probe (value INTEGER)")
            conn.execute("INSERT INTO probe VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        return f"journal mode {current}"


def _lock_probe(path: Path) -> str:
    """Take a write lock on the real database and give it back."""
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    finally:
        conn.close()
    return "write lock acquired"


def diagnose(path: str | Path | None = None) -> dict:
    """Describe the cache location and every probe of it."""
    target = Path(path) if path else cache_path()
    report: dict = {
        "path": str(target),
        "exists": target.exists(),
        "size_bytes": target.stat().st_size if target.exists() else 0,
        "filesystem": filesystem_type(target.parent),
        "sqlite_version": sqlite3.sqlite_version,
        "directory_writable": target.parent.is_dir() and os.access(target.parent, os.W_OK),
    }
    if target.exists():
        report["database"] = _probe(lambda: _describe_database(target))
    else:
        report["database"] = {"ok": True, "detail": "database does not exist yet"}

    if report["directory_writable"]:
        report["write"] = _probe(lambda: _write_probe(target.parent))
        report["journal_wal"] = _probe(lambda: _journal_probe(target.parent, "WAL"))
        report["journal_delete"] = _probe(lambda: _journal_probe(target.parent, "DELETE"))
    else:
        message = "directory is not writable"
        report["write"] = {"ok": False, "detail": message}
        report["journal_wal"] = {"ok": False, "detail": message}
        report["journal_delete"] = {"ok": False, "detail": message}

    if target.exists():
        report["lock"] = _probe(lambda: _lock_probe(target))
    else:
        report["lock"] = {"ok": True, "detail": "database does not exist yet"}

    failed = [
        name
        for name in ("database", "write", "journal_wal", "journal_delete", "lock")
        if not report[name]["ok"]
    ]
    report["failed"] = failed
    report["healthy"] = not failed
    if failed:
        report["hint"] = (
            "The cache cannot be used here. The wiki lookup still works without it. "
            "Point the cache at a local file system with MOEGIRL_MCP_DB=/path/to/cache.db."
        )
    return report


def _describe_database(path: Path) -> str:
    """Report the journal mode of the database."""
    conn = sqlite3.connect(path, timeout=10)
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        try:
            pages = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        except sqlite3.OperationalError:
            pages = 0
    finally:
        conn.close()
    return f"journal mode {mode}, {pages} cached pages"
