# moegirl-mcp

把[萌娘百科](https://zh.moegirl.org.cn/)的词条释义做成 MCP server，让 LLM 可以直接查词。

用时在线抓取萌娘百科的词条首段，缓存在本地 SQLite 里，对外暴露三个 MCP 工具：
`moegirl_lookup`、`moegirl_search`、`moegirl_stats`。

## 特点

- **在线供给**：不需要预先构建离线语料，查询时直接问萌娘百科的 MediaWiki API
- **本地缓存**：抓回来的词条存进 SQLite，默认保鲜 7 天，重复查询不再走网络
- **重定向感知**：`EVA` 这类跳转词条会自动解析到真正的页面，别名和正名都进缓存
- **限速保护**：萌娘百科在 Cloudflare 之后，突发请求会收到 HTTP 429，
  客户端默认限制在 3 req/s，并在遇到 429 时全局冷却
- **子页面可过滤**：`明日方舟/剧情` 这类下级页面默认不返回，需要时用 `include_subpages=true`

## 安装

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。

```sh
uv venv --python 3.12
uv pip install -e '.[dev]'
```

本项目用项目内的 `.venv`，**不要装进共享的 `~/uvenv`**：它需要 `mcp>=2`，
而 mcp 2.x 删掉了 `mcp.server.fastmcp`，会让共享环境里的 `langchain-mcp-adapters`
和 `mcp-server` 直接 import 失败。

## 接入 OpenCode

在 `opencode.json` 里挂载：

```json
{
  "mcp": {
    "moegirl": {
      "type": "local",
      "command": ["/home/cmach_socket/projects/moegirl-mcp/.venv/bin/moegirl-mcp", "serve"],
      "enabled": true,
      "timeout": 60000
    }
  }
}
```

`timeout` 单位是毫秒，OpenCode 默认只有 5000 ms，而一次实时检索可能耗时 10 秒以上，
因此要放宽。改完配置后用 `opencode mcp list` 确认状态是 `connected`。

任何支持 stdio 的 MCP 客户端都可以用同样的方式接入。

## 命令行

```sh
moegirl-mcp serve                       # 启动 MCP server（stdio）
moegirl-mcp lookup "明日方舟"            # 查词，输出 JSON
moegirl-mcp search "舰娘" --limit 3      # 搜索，输出 JSON
moegirl-mcp stats                       # 查看缓存状态
moegirl-mcp cache clear                 # 清空缓存
moegirl-mcp cache purge                 # 只清理过期条目
moegirl-mcp doctor                      # 体检缓存所在位置能否放下 SQLite
```

## 工具

### `moegirl_lookup(term, limit=5, extract_chars=600, include_subpages=false)`

按标题查词条。先按标题精确请求 MediaWiki API；如果没有同名页面，就退化成一次全文搜索。

```json
{
  "term": "明日方舟",
  "count": 1,
  "source": "live",
  "results": [
    {
      "title": "明日方舟",
      "url": "https://zh.moegirl.org.cn/明日方舟",
      "pageid": 262182,
      "matched_by": "title",
      "extract": "《明日方舟》（英文：Arknights；日文：アークナイツ <明日方舟>；韩文：명일방주）是由鹰角网络开发的策略塔防及收集养成类游戏。"
    }
  ]
}
```

`source` 说明答案从哪来：`live` 表示刚从 wiki 抓到，`cache` 表示命中本地缓存，
`live-search` 表示走了搜索兜底，`stale-cache` 表示 wiki 不可用、返回了过期缓存。

### `moegirl_search(query, limit=10, extract_chars=600, include_subpages=false)`

用 `generator=search` 搜索萌娘百科，按相关度返回命中词条的首段。

### `moegirl_stats()`

返回缓存条目数、命中次数、保鲜期和缓存文件路径。

## 数据模型

缓存是一个 SQLite 文件，默认在 `data/cache.db`：

| 表 | 内容 |
| --- | --- |
| `pages` | `title` / `extract` / `pageid` / `lastrevid` / `canonical` / `fetched_at` / `hits` |

一条词条会按两种方式入缓存：正名（`canonical`）一条，请求时用的别名（例如 `EVA`）
再单独存一条，两条都指向同一份释义。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MOEGIRL_MCP_DATA_DIR` | 源码目录下 `data/`，否则 `$XDG_DATA_HOME/moegirl-mcp` | 缓存目录 |
| `MOEGIRL_MCP_DB` | `<缓存目录>/cache.db` | 缓存文件路径 |
| `MOEGIRL_MCP_TTL` | `604800`（7 天） | 缓存保鲜秒数 |
| `MOEGIRL_MCP_RATE_LIMIT` | `3.0` | 每秒最多发起的 wiki 请求数 |

`serve` 子命令还支持 `--db`、`--ttl`、`--rate-limit`、`--extract-chars` 覆盖对应默认值。

### 缓存放不下时

缓存只是加速手段，放不下也不影响查词：缓存打不开时 server 照常工作，只是每次都走在线请求，
并在 stderr 上打印一行原因。

有些文件系统（网络盘、容器里的 proot 根文件系统等）不支持 SQLite 需要的文件锁，
`doctor` 可以分辨是缓存坏了还是 wiki 坏了：

```sh
moegirl-mcp doctor --db /path/to/cache.db
```

输出里 `healthy` 为 `false` 时，把缓存挪到本地盘即可：

```sh
export MOEGIRL_MCP_DB=~/.local/share/moegirl-mcp/cache.db
```

## 与 mw2fcitx 的关系

[mw2fcitx](https://github.com/outloudvi/mw2fcitx) 从萌娘百科抓取词条标题，清洗后转成拼音，
生成 fcitx5 / RIME 输入法的候选词库。它本身只有 `词 → 拼音`，**没有释义**。

本项目走的是另一条路：直接问 MediaWiki API 要词条首段，把释义这一层补上，
再包装成 MCP 工具。两者互不依赖，可以各自使用。

早期版本曾基于 mw2fcitx 的 `pkg/moegirl` 分支预置词表做全量离线抓取，
0.2.0 起改为纯在线供给，离线词表与拼音索引已移除。

## 限制

- 萌娘百科禁用了 `list=search` / `list=allpages`，搜索走的是 `generator=search`
- MediaWiki 搜索不认拼音，`mingrifangzhou` 这类查询会搜不到东西
- 词条首段来自 wiki 的 `exintro`，没有首段的页面会返回空字符串

## 许可

代码以 **AGPL-3.0-or-later** 发布（SPDX-License-Identifier: `AGPL-3.0-or-later`），
见 [LICENSE](LICENSE)；版权与依赖、数据边界见 [NOTICE](NOTICE)。

词条释义来自萌娘百科，遵循 CC BY-NC-SA 3.0 CN，**不在 AGPL 范围内**。本项目不附带
任何词条数据，运行时按需在线抓取并缓存在本机。
