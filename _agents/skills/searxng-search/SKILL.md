---
name: searxng-search
description: 网页搜索(本机 SearXNG JSON API,免费无 key)。当用户要求搜索、查资料,或任务本身需要联网信息——确认最新版本、查现状、进入陌生领域先摸底——时使用。
---

# SearXNG 网页搜索

本机 Docker 容器聚合 google/bing/baidu/duckduckgo 等引擎,免费无 API key。需要全文时接 crawl4ai-crawl skill。

## 主路径:MCP 工具

会话里有 `searxng` MCP 服务器时直接调:

- `searxng_web_search(query, language, pageno, ...)` — 常规搜索
- `searxng_search_suggestions(query)` — 查询补全,不确定怎么搜时先要建议
- `web_url_read(url)` — 读单个 URL 的文本

中文资料传 `language: "zh-CN"`。

## 备用路径:Python 脚本

MCP 不可用或需要精确控制时:

```bash
python "C:\Users\LMR\.agents\skills\searxng-search\scripts\searxng_search.py" -q "查询词" --lang zh-CN --max-results 8
```

参数:`--page 2` 翻页 / `--time-range day|month|year` / `--engines google,bing` / `--categories it` / `--raw` 原始 JSON。

输出含 Suggestions(改写查询的线索)和 Unresponsive engines(诊断线索)。

## 排障

- **Connection refused** → Docker Desktop 没起:启动后 `docker compose up -d`(目录 `D:\SynologyDrive\LMR\Docker\searxng`),等 10 秒重试。
- **403** → `settings.yml` 的 `search.formats` 丢了 `json` 行,修复后 `docker compose restart`。
- **结果全空且 Unresponsive engines 一片** → 上游引擎验证码/限流,隔几分钟重试。
- **结果质量差** → 加 `--lang`、`--engines`,或用 Suggestions 改写查询词。

高频自动化查询会触发上游引擎限流;服务只绑 127.0.0.1:8888。
