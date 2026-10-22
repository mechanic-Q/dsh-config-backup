---
name: bili-book
description: 每日处理 B 站收藏夹「AI绘图」：检查新增视频 → 转文字稿 → 按 nashsu llm-wiki 规范（wiki/purpose.md + wiki/schema.md）编译与评审 → 判断卡。当用户说"处理收藏夹/每日评审/跑一遍 bili-book"时使用。
---

# BiLI_book 每日流程

目标：把用户新收藏的 B 站视频变成**知识**和**判断**。用户没时间逐个看视频——你**代替他做前置的思考、判断和了解**，他根据判断卡决定要不要亲自试。

## 记录的铁律：nashsu llm-wiki 规范

**每一条 wiki 记录都必须遵守 `wiki/purpose.md` 和 `wiki/schema.md`（upstream 原版，不得添加自定义规则）。** 关键约定：

- 六类页面，type 必须与目录一致：

| type | 目录 | 在本项目的用法 |
|---|---|---|
| source | `wiki/sources/` | 每视频精读页，命名 `<up>-<year>-<slug>.md`；frontmatter 含 `authors`（UP主）/`year`/`url`（B站链接）/`venue: "Bilibili"`，`sources:` 引用 `raw/sources/<bvid>/transcript.md` |
| entity | `wiki/entities/` | 工具/模型/公司（comfyui、flux…），命名用官方名 |
| concept | `wiki/concepts/` | 技术方法概念 |
| comparison | `wiki/comparisons/` | 替代关系的对比页（评审发现"新的比旧的好"时写） |
| query | `wiki/queries/` | 开放问题（矛盾悬而未决时挂起） |
| synthesis | `wiki/synthesis/` | 每日评审 `daily-review-<yyyymmdd>.md`（判断卡）与跨源结论 |

- frontmatter 一律：`type/title/tags/related/created/updated`（source 另加 authors/year/url/venue）；日期 `YYYY-MM-DD`；**禁止** status/confidence/volatility 等自造字段。
- 正文 wikilink 一律裸 `[[stem]]`（带 `.md` 是断链，带 `dir/` 前缀不产生图谱边）；`related:` 列表同样不带 `.md`。
- 每个 entity/concept（以及新增的 source/synthesis）必须注册进 `wiki/index.md`：`- [[slug]] — 一句话摘要`。
- `wiki/log.md` **逆时间序**追加（永不改写旧行），格式 `## YYYY-MM-DD 记录 | 标题` + 要点 bullets（含 [[wikilink]]），末尾一行索引统计。写入 index/log 前先做 `.bak-before-<动作>-<日期>` 备份（项目惯例）。
- 矛盾处理走 schema 的 Contradiction Handling：相关 concept/entity 页记录矛盾 → 挂或更新 query 页并链接双方来源 → 证据足够后在 synthesis 页裁决。**不偷偷二选一，不删除被替代的内容**。

## 环境与工具

- 项目根：`E:\zcode\BiLI_book`，所有命令在此执行。wiki 根（purpose/schema/raw/wiki 的宿主）由 `config.toml [wiki] root` 指向 **llm-wiki 桌面端项目 `E:/Agent_memory/wiki_nashsu/wiki_nashsu`**（2026-09-19 归位后直写该库；转写稿落 `raw/sources/` 即被桌面端 source watch 自动摄取）。
- CLI：`python -m bilibook <doctor|fetch|diff|transcribe|sync|pending|lint>`，产物落盘 `data/<日期>/`，幂等可重跑。收尾 `lint --today` 必须通过（只查今天写入的条目；它检查 type/dir 匹配、必备字段、链接卫生、index 注册。不带 --today 的全量 lint 用于审计，会报出库内存量页面的历史问题）
- 评审对接基准（`config.toml [profile]`）：ComfyUI + SD WebUI；RTX 5080 16GB / 7950X / 128GB RAM / Windows 10。
- 交叉评审时优先直接读库内文件；语义检索/图谱用 LLM Wiki 桌面应用 API `http://127.0.0.1:19828`（本项目 = wiki_nashsu 项目，已注册；鉴权 token 见 WSL 的 tmp 下 `llm-wiki-env.*.env` 文件，遇 401 先取新 token；中文短语检索命中率低，优先英文 token/标题词。旧 HTTP 用法手册（llm-wiki skill）已于 2026-09-21 删除，日常走 ZCode MCP 工具。）。

## 流程

1. **自检**：`python -m bilibook doctor`。失败先修环境，不硬跑。
2. **拉取与差分**：`fetch` → `diff`。若无新增：跑一次 `lint --today`，向用户报告"今日无新增"后结束。
3. **转写**：`transcribe`（**每批 ≤10 条**，失败记录在 `results.json` 不中断）→ `sync`。
4. **精读（Stage 4）**：`python -m bilibook pending` 看待办，对每篇 `wiki/raw/sources/<bvid>/transcript.md` 写 source 页 `wiki/wiki/sources/<up>-<year>-<slug>.md`（模板见下）→ 更新 `wiki/index.md` → `log.md` 追加。已存在（state 里 note_pages 有映射）则跳过。
5. **三层评审（Stage 5）**：写 `wiki/wiki/synthesis/daily-review-<yyyymmdd>.md`（判断卡模板见下），同时落地交叉评审的产出：新建/更新 entity/concept/comparison 页、矛盾挂 query 页、全部注册 index、log 追加。
6. **收尾**：`python -m bilibook sync` → `python -m bilibook lint --today` 通过 → 向用户汇报判断卡摘要 + 建议亲自处理的事项。

首次/存量回填：diff 把所有未见过的视频列为新增，按批处理、每批后 `sync`；不要一次跑几百条。

## Stage 4：source 页模板（wiki/wiki/sources/<up>-<year>-<slug>.md）

```markdown
---
type: source
title: <视频标题>
tags: [从内容提炼, 3-6 个]
related: [引用到的其他页面 stem]
created: YYYY-MM-DD
updated: YYYY-MM-DD
authors:
  - <UP主名>
year: YYYY
url: "https://www.bilibili.com/video/<bvid>"
venue: "Bilibili"
sources:
  - raw/sources/<bvid>/transcript.md
---

# <视频标题>

## 它声称什么
一段话：核心主张/方法/结论。

## 方法与要点
- 要点（带文字稿时间戳 [12:30]，主张必须可回溯）

## 证据与可信度
实测演示 / 数据对比 / 只有口述？

## 可复用的东西
具体步骤、参数、工作流、外部链接。

## 评审结论
一句话 + 指向当日 synthesis：[[daily-review-yyyymmdd]]

## Related
- [[相关页面]] — 关系（一致/矛盾/替代）
```

## Stage 5：三层评审协议

**第一层·精读**：读文字稿，这个视频声称什么、方法是什么、证据是什么。

**第二层·交叉评审**：读 `wiki/index.md` 与同主题页面，回答：与已有条目**一致 / 矛盾 / 互补**？谁的证据更硬？是否构成替代（写 `wiki/comparisons/` 对比页，说清新方法好在哪、旧条目是否过时）？矛盾悬而未决 → 挂 query 页。

**第三层·联网调研**（WebSearch/WebFetch 核实，不臆断）：
1. **对接可能性**（高/中/低）：能否接进 ComfyUI（现成节点/工作流）或 SD WebUI（插件）？16GB 显存跑不跑得动？
2. **实操性**（高/中/低）：上手成本、依赖复杂度、别人照做能不能成功。
3. **成熟度**（纯理论 / 有早期实践 / 已广泛验证）：GitHub 活跃度与真实 issue、社区复现帖、文档完整度。附证据链接。

### 判断卡（synthesis/daily-review-<yyyymmdd>.md 里每个新条目一块）

```markdown
### <bvid> <标题>（source 页：[[<slug>]]）
- 定性：有价值 / 炒冷饭 / 纯资讯 —— 一句话为什么
- 对接性：高/中/低 —— 依据（接哪个工具、装什么）
- 实操性：高/中/低 —— 依据（门槛、成本、显存够不够）
- 成熟度：理论 / 早期 / 成熟 —— 证据链接
- 交叉结论：与书内哪些页面一致/矛盾/替代（点名）
- 行动建议：值得试 / 再等等 / 不值得 —— 理由
- 若值得试：装什么、第一步做什么（只做探测结论，不自动安装）
```

synthesis 页 frontmatter 的 `related:` 引用当日所有 source 页与受影响的 concept/comparison 页。

## 边界

- 不自动安装/运行任何被推荐的工具；只做探测并给出安装与验证路径。
- 文字稿与评审仅本地存档，个人学习用途。
- 评审要讲出道理（为什么好/为什么不如旧的），拒绝空泛形容词；没有证据就明说"证据不足"。
- 本 wiki 的知识晋升进主数字孪生 wiki（wiki_nashsu）必须经用户明确确认，属策展行为，不在自动流程内。
