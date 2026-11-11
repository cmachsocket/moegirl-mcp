# Copyright (C) 2026 cmachsocket
# SPDX-License-Identifier: AGPL-3.0-or-later -- see LICENSE for the full terms.
"""Command line interface of moegirl-mcp."""

from __future__ import annotations

import argparse
import json
import sys

from .cache import Cache
from .config import (
    DEFAULT_EXTRACT_CHARS,
    DEFAULT_LOOKUP_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    cache_path,
    cache_ttl,
    rate_limit,
)
from .mediawiki import MediaWikiClient, RateLimiter
from .server import create_server
from .service import DictionaryService


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser of the command line interface."""
    parser = argparse.ArgumentParser(
        prog="moegirl-mcp",
        description="MCP server exposing Moegirlpedia entries to language models.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the MCP server on stdio.")
    _add_cache_options(serve)
    serve.add_argument(
        "--rate-limit",
        type=float,
        default=None,
        help="Maximum number of wiki requests per second.",
    )
    serve.add_argument(
        "--extract-chars",
        type=int,
        default=DEFAULT_EXTRACT_CHARS,
        help="Default number of characters of a lead section handed to the model.",
    )

    lookup = subparsers.add_parser("lookup", help="Look a term up and print the answer.")
    lookup.add_argument("term")
    lookup.add_argument("--limit", type=int, default=DEFAULT_LOOKUP_LIMIT)
    _add_cache_options(lookup)
    lookup.add_argument("--include-subpages", action="store_true")

    search = subparsers.add_parser("search", help="Search the wiki and print the answers.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=DEFAULT_SEARCH_LIMIT)
    _add_cache_options(search)
    search.add_argument("--include-subpages", action="store_true")

    stats = subparsers.add_parser("stats", help="Report the state of the local cache.")
    _add_cache_options(stats)

    cache = subparsers.add_parser("cache", help="Maintain the local cache.")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_clear = cache_sub.add_parser("clear", help="Drop every cached page.")
    _add_cache_options(cache_clear)
    cache_purge = cache_sub.add_parser("purge", help="Drop expired pages only.")
    _add_cache_options(cache_purge)

    return parser


def _add_cache_options(parser: argparse.ArgumentParser) -> None:
    """Add the options shared by every command touching the cache."""
    parser.add_argument(
        "--db",
        default=None,
        help="Path of the SQLite cache (default: the configured cache file).",
    )
    parser.add_argument(
        "--ttl",
        type=float,
        default=None,
        help="Number of seconds a cached page stays fresh.",
    )


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        return _serve(args)
    if args.command == "lookup":
        return _run_query(args, "lookup")
    if args.command == "search":
        return _run_query(args, "search")
    if args.command == "stats":
        return _stats(args)
    if args.command == "cache":
        return _cache(args)
    return 1


def _serve(args: argparse.Namespace) -> int:
    """Start the MCP server on stdio."""
    ttl = args.ttl if args.ttl is not None else cache_ttl()
    service = DictionaryService(
        client=MediaWikiClient(limiter=RateLimiter(args.rate_limit or rate_limit())),
        cache=Cache(args.db or cache_path(), ttl=ttl),
        ttl=ttl,
        extract_chars=args.extract_chars,
    )
    create_server(service).run("stdio")
    return 0


def _run_query(args: argparse.Namespace, mode: str) -> int:
    """Run a single lookup or search and print the answer as JSON."""
    ttl = args.ttl if args.ttl is not None else cache_ttl()
    service = DictionaryService(
        client=MediaWikiClient(limiter=RateLimiter(rate_limit())),
        cache=Cache(args.db or cache_path(), ttl=ttl),
        ttl=ttl,
    )
    if mode == "lookup":
        payload = service.lookup(
            args.term, limit=args.limit, include_subpages=args.include_subpages
        )
    else:
        payload = service.search(
            args.query, limit=args.limit, include_subpages=args.include_subpages
        )
    _print(payload)
    return 0


def _stats(args: argparse.Namespace) -> int:
    """Print the state of the local cache."""
    ttl = args.ttl if args.ttl is not None else cache_ttl()
    cache = Cache(args.db or cache_path(), ttl=ttl)
    _print(
        {
            "ttl_seconds": ttl,
            "cache": cache.stats(),
        }
    )
    return 0


def _cache(args: argparse.Namespace) -> int:
    """Clear or purge the local cache."""
    ttl = getattr(args, "ttl", None)
    if ttl is None:
        ttl = cache_ttl()
    cache = Cache(args.db or cache_path(), ttl=ttl)
    if args.cache_command == "clear":
        removed = cache.clear()
    else:
        removed = cache.purge_expired()
    _print({"removed": removed, "cache": cache.stats()})
    return 0


def _print(payload: dict) -> None:
    """Print a payload as indented JSON."""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
