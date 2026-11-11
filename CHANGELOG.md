# Changelog

## Unreleased

Keep the server alive when the cache cannot be opened.

- Switch the database to the WAL journal mode only when it is not in it
  already, and ignore a refused switch. The upgrade needs an exclusive lock on
  the database file that is not taken through the busy handler, so it failed
  with "database is locked" when a second instance of the server started at the
  same time.
- Degrade to a cache free lookup when the database cannot be opened, reporting
  the reason on stderr. The wiki lookup never fails because of the cache.
- Add `moegirl-mcp doctor`, which probes the cache location for write access,
  locking and journal mode support.

## 0.2.0

Switch from an offline corpus to an online lookup service.

- Query Moegirlpedia live through the MediaWiki API instead of building a
  224k entry dictionary ahead of time.
- Keep the lead sections in a local SQLite cache, fresh for seven days by
  default, so repeated lookups are answered without touching the network.
- Resolve redirects to the canonical page and cache both the alias and the
  canonical title.
- Look a term up by title first and fall back to a full text search when the
  wiki has no page under that name.
- Cache the lead sections of search hits, so a repeated search only pays for
  the title lookup.
- Serve the requests at three requests per second and cool the whole client
  down when the wiki answers with HTTP 429.
- Drop the mw2fcitx, pypinyin and opencc dependencies along with the bulk
  build, the pinyin index and the FTS5 index.
- License the code under AGPL-3.0-or-later. The wiki content served by the tools
  stays under CC BY-NC-SA 3.0 CN and is not covered by the AGPL.

## 0.1.0

Initial release.

- Build a Moegirlpedia dictionary of about 224k entries: title, lead section
  and pinyin, stored in SQLite with an FTS5 trigram index.
- Fetch the lead sections through the MediaWiki API in batches of 20 with
  global rate limiting and resumable progress.
- Derive candidate words and pinyin with the mw2fcitx moegirl tweaks, the
  pypinyin exporter and the upstream `fixfile.json` correction table.
- Expose the dictionary as an MCP server over stdio with the tools
  `moegirl_lookup`, `moegirl_search` and `moegirl_stats`.
