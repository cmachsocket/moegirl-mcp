# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Static configuration for moegirl-mcp."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent

PACKAGE_NAME = "moegirl-mcp"
PACKAGE_VERSION = "0.2.0"

#: MediaWiki API endpoint of Moegirlpedia.
API_URL = "https://zh.moegirl.org.cn/api.php"

#: Identifier sent to the wiki API. The MediaWiki API etiquette asks for a
#: descriptive agent string with contact information, so keep it stable.
USER_AGENT = (
    f"{PACKAGE_NAME}/{PACKAGE_VERSION} "
    "(Moegirlpedia dictionary MCP server; https://github.com/outloudvi/mw2fcitx)"
)

#: Per-request timeout in seconds.
REQUEST_TIMEOUT = 30

#: Upper bound of API requests per second. Moegirlpedia sits behind
#: Cloudflare and answers bursts with HTTP 429, so the limit is deliberately
#: conservative. Online lookups are sparse, therefore this is far below the
#: throughput the wiki would tolerate.
RATE_LIMIT = 3.0

#: How long a cached page stays fresh, in seconds. Seven days keeps the
#: answer accurate enough for a dictionary while avoiding repeated requests.
CACHE_TTL = 7 * 24 * 3600

#: Largest number of search hits one `generator=search` request may carry.
SEARCH_PAGE_LIMIT = 20

#: Default number of results returned by the lookup and search tools.
DEFAULT_LOOKUP_LIMIT = 5
DEFAULT_SEARCH_LIMIT = 10

#: Largest number of results a caller may ask for.
MAX_LIMIT = 50

#: Default number of characters of the lead section handed to the model.
DEFAULT_EXTRACT_CHARS = 600


def data_dir() -> Path:
    """Directory holding the local page cache."""
    override = os.environ.get("MOEGIRL_MCP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if (PROJECT_DIR / "pyproject.toml").is_file():
        return PROJECT_DIR / "data"
    return _xdg_data_home() / PACKAGE_NAME


def cache_path() -> Path:
    """Path of the SQLite page cache."""
    override = os.environ.get("MOEGIRL_MCP_DB")
    if override:
        return Path(override).expanduser()
    return data_dir() / "cache.db"


def cache_ttl() -> float:
    """Lifetime of a cache entry in seconds."""
    override = os.environ.get("MOEGIRL_MCP_TTL")
    if override:
        return float(override)
    return CACHE_TTL


def rate_limit() -> float:
    """Maximum number of API requests per second."""
    override = os.environ.get("MOEGIRL_MCP_RATE_LIMIT")
    if override:
        return float(override)
    return RATE_LIMIT


def _xdg_data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".local" / "share"
