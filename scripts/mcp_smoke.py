# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Smoke test the MCP server over stdio.

.venv/bin/python scripts/mcp_smoke.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

SERVER = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "moegirl-mcp"


async def run(db: Path) -> int:
    params = StdioServerParameters(
        command=str(SERVER),
        args=["serve", "--db", str(db)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print("server:", info.server_info.name, info.server_info.version)

            tools = await session.list_tools()
            print("tools:", ", ".join(tool.name for tool in tools.tools))

            for call in (
                {"name": "moegirl_stats", "arguments": {}},
                {"name": "moegirl_lookup", "arguments": {"term": "明日方舟"}},
                {"name": "moegirl_search", "arguments": {"query": "舰娘", "limit": 3}},
            ):
                result = await session.call_tool(call["name"], call["arguments"])
                text = "".join(
                    block.text for block in result.content if isinstance(block, TextContent)
                )
                print(f"\n--- {call['name']} ---")
                print(json.dumps(json.loads(text), ensure_ascii=False, indent=2)[:1200])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/cache.db")
    args = parser.parse_args()

    if not SERVER.is_file():
        print(f"server not found: {SERVER}", file=sys.stderr)
        return 1
    return asyncio.run(run(Path(args.db)))


if __name__ == "__main__":
    raise SystemExit(main())
