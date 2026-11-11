# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""MCP server exposing Moegirlpedia entries to language models."""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

from .config import (
    DEFAULT_EXTRACT_CHARS,
    DEFAULT_LOOKUP_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    PACKAGE_VERSION,
)
from .service import DictionaryService

INSTRUCTIONS = """\
Look up entries of Moegirlpedia (萌娘百科, zh.moegirl.org.cn), the Chinese
encyclopedia of ACGN culture.

Use `moegirl_lookup` when the user names a specific term and wants to know
what it is. Use `moegirl_search` when the term is unknown, misspelled, or the
user asks about a topic rather than a single entry.

The answers are the lead sections of the wiki articles, fetched live and
cached locally for a week. Subpages such as `明日方舟/剧情` are skipped by
default; pass `include_subpages=true` to see them.

The content comes from Moegirlpedia and is licensed CC BY-NC-SA 3.0 CN.\
"""


def create_server(service: DictionaryService | None = None) -> MCPServer:
    """Build the MCP server exposing the dictionary tools."""
    dictionary = service or DictionaryService()
    server = MCPServer(
        name="moegirl-mcp",
        version=PACKAGE_VERSION,
        instructions=INSTRUCTIONS,
    )

    @server.tool(
        description=(
            "Look up a Moegirlpedia entry by name and return its lead section. "
            "Falls back to a full text search when no page carries that exact "
            "title."
        )
    )
    async def moegirl_lookup(
        term: str,
        limit: int = DEFAULT_LOOKUP_LIMIT,
        extract_chars: int = DEFAULT_EXTRACT_CHARS,
        include_subpages: bool = False,
    ) -> dict:
        """Look a term up in Moegirlpedia.

        Args:
            term: The entry to look up, for example `明日方舟`.
            limit: Maximum number of results, at most 50.
            extract_chars: Maximum number of characters of the lead section.
            include_subpages: Whether pages below another page are included.

        Returns:
            The matching entries with their title, URL and lead section.
        """
        return await asyncio.to_thread(
            dictionary.lookup, term, limit, extract_chars, include_subpages
        )

    @server.tool(
        description=(
            "Search Moegirlpedia for entries matching a query and return the "
            "lead section of every hit, best match first."
        )
    )
    async def moegirl_search(
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        extract_chars: int = DEFAULT_EXTRACT_CHARS,
        include_subpages: bool = False,
    ) -> dict:
        """Search Moegirlpedia.

        Args:
            query: The search query, for example `舰娘`.
            limit: Maximum number of results, at most 50.
            extract_chars: Maximum number of characters of the lead section.
            include_subpages: Whether pages below another page are included.

        Returns:
            The matching entries with their title, URL and lead section.
        """
        return await asyncio.to_thread(
            dictionary.search, query, limit, extract_chars, include_subpages
        )

    @server.tool(description="Report the state of the local page cache.")
    async def moegirl_stats() -> dict:
        """Report how many pages are cached and how long they stay fresh."""
        return await asyncio.to_thread(dictionary.stats)

    return server
