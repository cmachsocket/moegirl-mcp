# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Tests for the MCP server surface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.types import CallToolResult, InputRequiredResult

from moegirl_mcp.cache import Cache
from moegirl_mcp.mediawiki import Page
from moegirl_mcp.server import create_server
from moegirl_mcp.service import DictionaryService

from .test_service import FakeClient


def _payload(result: CallToolResult | InputRequiredResult) -> dict:
    """Read the JSON payload out of a tool result."""
    if not isinstance(result, CallToolResult):
        raise AssertionError("the tool asked for input instead of answering")
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError("the tool returned no text content")


@pytest.fixture()
def service(tmp_path: Path) -> DictionaryService:
    client = FakeClient(
        pages={"明日方舟": Page(title="明日方舟", extract="《明日方舟》是由鹰角网络开发的游戏。")},
        titles=["明日方舟"],
    )
    return DictionaryService(
        client=client,
        cache=Cache(tmp_path / "cache.db", ttl=3600),
        ttl=3600,
    )


def test_server_registers_the_tools(service: DictionaryService) -> None:
    server = create_server(service)

    assert server.name == "moegirl-mcp"
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {
        "moegirl_lookup",
        "moegirl_search",
        "moegirl_stats",
    }


def test_lookup_tool_answers_with_the_entry(service: DictionaryService) -> None:
    server = create_server(service)

    result = asyncio.run(server.call_tool("moegirl_lookup", {"term": "明日方舟"}))

    payload = _payload(result)
    assert payload["results"][0]["title"] == "明日方舟"
    assert payload["results"][0]["extract"] == "《明日方舟》是由鹰角网络开发的游戏。"


def test_search_tool_answers_with_the_matches(service: DictionaryService) -> None:
    server = create_server(service)

    result = asyncio.run(server.call_tool("moegirl_search", {"query": "明日方舟"}))

    payload = _payload(result)
    assert payload["count"] == 1
    assert payload["results"][0]["title"] == "明日方舟"


def test_stats_tool_reports_the_cache(service: DictionaryService) -> None:
    server = create_server(service)

    result = asyncio.run(server.call_tool("moegirl_stats", {}))

    payload = _payload(result)
    assert payload["cache"]["pages"] == 0


def test_tools_carry_a_description(service: DictionaryService) -> None:
    server = create_server(service)

    tools = asyncio.run(server.list_tools())

    assert all(tool.description for tool in tools)
