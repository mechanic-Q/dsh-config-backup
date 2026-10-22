---
name: crawl4ai-crawl
description: 抓取网页为 LLM 友好 markdown,支持 JS 渲染页面、批量/深度爬整站、结构化抽取(本机 crawl4ai Docker 服务)。当 WebFetch 拿不到内容、需要页面全文、要把文档站爬成语料库、或要批量抓同构页面字段时使用。
---

# crawl4ai 网页抓取

补 WebFetch 的三个盲区:JS 渲染、批量、结构化抽取。搜索入口是 searxng-search skill——搜到 URL 后到这里抓全文。

## 服务

- 地址 `http://localhost:11235`,认证 `Authorization: Bearer <token>`
- token 与 `CRAWL4AI_SECRET_KEY` 以 `D:\SynologyDrive\LMR\Docker\crawl4ai\.env` 为唯一事实源;`~/.zcode/cli/config.json` 里 MCP 的 Bearer header 必须与它一致,改 token 两处同步

## REST API(主路径:单页、批量、深度爬取都走这里)

**单页 → markdown**,90% 的场景:

```bash
TOKEN=$(grep -oP '(?<=CRAWL4AI_API_TOKEN=).*' "/d/SynologyDrive/LMR/Docker/crawl4ai/.env")
curl -s -X POST "http://localhost:11235/md" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://目标页"}'
```

取返回 JSON 的 `markdown` 字段(已去导航/广告噪声)。加 `"f":"llm","q":"提取什么"` 走 LLM 精炼(需服务端配 LLM key,当前未配)。

**批量/深度爬取 → `POST /crawl`**:体 `{"urls":[...], "browser_config":{...}, "crawler_config":{...}}`;深度爬取在 `crawler_config` 配 BFS/DFS/best-first 策略,流式进度用 `POST /crawl/stream`(NDJSON)。参数全集查 `references/complete-sdk-reference.md`(官方 SDK 参考,0.7.4 版;CrawlerRunConfig 字段同样适用于请求体,认证与部署以本文档的 0.9.x 规则为准)。

**其他端点**:`/screenshot`、`/pdf`、`/execute_js`、`GET /health`(免认证)。

## MCP 工具(轻量场景)

会话里有 `crawl4ai` MCP 服务器时,`md`/`crawl`/`screenshot` 可反射式调用;它是 REST 的子集,复杂配置走 curl。

## 排障

- **Connection refused / 401** → Docker Desktop 没起或 token 不对:`docker ps` 看 crawl4ai 状态,token 以 `.env` 为准。
- **`URL blocked (SSRF protection)`** → 本机代理是 fake-IP 模式,域名解析到 198.18.x.x,被 Python 判为非全局地址,官方 SSRF 防护误拦一切外站。解法是重建容器时带官方逃生门 `-e CRAWL4AI_ALLOW_INTERNAL_URLS=true`(容器只绑 127.0.0.1 + token,单机自用风险可控),一键脚本:`bash /d/SynologyDrive/LMR/Docker/crawl4ai/rebuild.sh`。
- **超时** → 目标站慢或被墙,重试或换镜像站。

token 明文在 `.env`,轮换时改 `.env` 后跑 rebuild.sh 并同步 config.json。
