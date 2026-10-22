---
name: daily-china-news
description: 每日新中国新闻管道：7 信源→LLM 分类去重→正文→摘要→报纸（HTML+PNG）。Use when 用户要跑或补跑当日/指定日期的报纸、管道某一步失败要排障、要改信源或 LLM provider、需要产物喂给 video-pipeline、或要查 9:00 定时任务为什么没产出。
---

# daily-china-news（每日新中国）

自动采集、筛选、分类、摘要、渲染全流程的 Python 新闻管道。每天从 7 个中国信源采集当日新闻，经 LLM 分类 + 摘要，渲染为手机壁纸风格报纸（HTML + PNG）。

本文件是**该技能的规范源**（随仓库分发）。本机安装位 `~/.agents/skills/daily-china-news/SKILL.md` 是本文件的副本，改完仓库这份要同步过去。

## 项目位置

| 项 | 值 |
|---|---|
| 本机检出 | `E:/Daily/daily-china-news-hardcoded` |
| 运行宿主 | Windows 原生（Git for Windows 的 MSYS bash + 项目 venv） |
| venv | `.venv/Scripts/python.exe` |
| 产物根 | `E:/每日新中国/<YYYY-MM-DD>/` |
| 文档真源 | 项目内 `AGENTS.md`、`CONTEXT.md`、`docs/adr/` |

> **WSL 路径已退役**：`//wsl.localhost/Ubuntu/home/lmr/projects/Daily` 那份检出停在 #67，没有本文件，且从 Windows 侧 git 操作报 dubious ownership。**一律走 E: 检出**。`AGENTS.md` 里仍写着 UNC 路径（历史遗留），以本节为准。

## 跑起来

### 整条管道（推荐入口）

~~~powershell
# 无人值守 / 定时任务一律走这里：run_all.ps1 自动甄别 Git 的 MSYS bash，
# 显式排除 System32 的 WSL bash（后者不容忍 CRLF，且缺 taskkill/nohup/netstat 语义）
powershell -NoProfile -ExecutionPolicy Bypass -File E:/Daily/daily-china-news-hardcoded/run_all.ps1 --date 2026-10-14
~~~

省略 `--date` 即跑今天；加 `--dry-run` 干跑。参数原样透传给 `run_all.sh`（逻辑单一真源是 bash），前一步失败即终止。

已在本机 Git Bash 时等价：

~~~bash
cd /e/Daily/daily-china-news-hardcoded
./run_all.sh --date 2026-10-14
~~~

### 单步续跑（用项目 venv）

~~~bash
P=E:/Daily/daily-china-news-hardcoded
$P/.venv/Scripts/python.exe step1_3.py --date 2026-10-14   # 采集 + HTTP-200 校验
$P/.venv/Scripts/python.exe step4.py   --date 2026-10-14   # 分类 + 过滤，选 top10
$P/.venv/Scripts/python.exe step6.py   --date 2026-10-14   # 正文提取
$P/.venv/Scripts/python.exe step7.py   --date 2026-10-14   # 摘要生成
$P/.venv/Scripts/python.exe step8.py   --date 2026-10-14   # 渲染成报（HTML + PNG）
~~~

步骤之间只靠文件接力，失败后从断点那一步续跑即可。

### 测试

~~~bash
cd /e/Daily/daily-china-news-hardcoded
.venv/Scripts/python.exe -m unittest discover -s tests
~~~

当前基线：**292 tests OK**（约 9s）。`tests/manual/` 下 5 个是带 CLI 开关的手工验收脚本，`unittest discover` 不收它们，属正常。

> **本机 venv 没装 pytest**。`python -m pytest` 会报 No module named pytest。CI 用 pytest 是它自己的事（见下），本地照上面这条跑。

### 独立工具（不在主链路上）

~~~bash
P=.venv/Scripts/python.exe
$P archive_news.py   --date 2026-10-14            # 归档补跑（不重算分类）
$P archive_enrich.py --date 2026-10-14            # 归档正文/首图补全（best-effort）
$P perf_profile.py   --date 2026-10-14            # 性能量化，出 JSON + Markdown
$P monthly_report.py --month 2026-10            # 月报（--no-llm 可离线）
~~~

## 管道架构

~~~
step1_3.py  采集 + 校验   ->  0新闻_粗筛.md
step4.py    分类 + 过滤   ->  1新闻_链接.md      <- LLM x4
step6.py    正文提取     ->  2新闻_已审核.md
step7.py    摘要生成     ->  3新闻_概述.md      <- LLM x1
step8.py    渲染成报     ->  HTML + PNG
~~~

全部落在 `E:/每日新中国/<YYYY-MM-DD>/`；步骤间无内存共享，纯文件接口，可独立运行。

### 产物文件名

~~~
0新闻_粗筛.md
1新闻_链接.md
2新闻_已审核.md
3新闻_概述.md
<日期>_每日新中国_第<期号>.html
<日期>_每日新中国_第<期号>.png
~~~

期号自 2026-04-19 起算：`(target_date - 2026-04-19).days + 1`，中文序数（第 184 期这种）。见 `step8.py:_compute_issue()`。

## 7 个信源

| 信源 | 采集方式 |
|---|---|
| 新华社 news.cn | urllib 首页 + chromium 降级 |
| 参考消息 cankaoxiaoxi.com | JSON API（`list.json` × 9 频道） |
| 央视新闻 news.cctv.com | urllib 首页 + chromium 降级 |
| 央视军事 military.cctv.com | urllib 首页 + chromium 降级 |
| 中科院 cas.cn | urllib 静态 |
| 中核集团 cnnc.com.cn -> cnnpn.cn | 三级降级链 |
| 人民日报 paper.people.com.cn | urllib 版面索引 `node_NN.html` 1-9 |

采集走 static-first：`step1_3.py:fetch_home_html()` 先静态抓，`_is_static_sufficient()` 判不通过才 fallback 到 `chromium_dom()`。信源列表与 URL 硬编码在 `step1_3.py:SOURCES` 与各 `fetch_*()` 里，无外部配置。

## LLM 后端

- **唯一配置** `llm.yaml`。切 provider/model 只改顶层 `provider:` + `model:`，不改代码。当前 `provider: strata` / `model: qwen3.8-flash-next-iq3_s`。
- **抽象层** `llm_client.py`：`call_llm(call_site_id, messages)` 一站式调用；温度/max_tokens/timeout 以 `llm.yaml:call_sites` 为唯一真源。
- **6 个调用点**：

| call_site_id | 位置 | 用途 |
|---|---|---|
| `china-relevance` | `step4.py:llm_is_china_related_batch()` | 涉华判定兜底 |
| `column-classify` | `step4.py:llm_classify_single()` | 栏目分类仲裁 |
| `column-score` | `step4.py:score_signals_batch()` | 9 栏目新闻价值评分 |
| `event-dedup` | `step4.py:llm_review_duplicate_candidates()` | 跨信源同事件去重 |
| `summarize` | `step7.py:llm_summarize()` | 摘要生成 |
| `monthly-overview` | `monthly_report.py` | 月报综述 |

### 本地后端 Strata

- `bash start-llm.sh` 后台拉起 + 健康自检（端口 8080，`STRATA_HOME=E:/AI/LLM/Strata`）。`run_all.sh` 会在 step4 前自动启停它。
- **GPU 红线**：加载后显存 15.7/16.3GB，空闲基线约 600MB。`start-llm.sh` 用 `VRAM_LIMIT_MB=2000` 阈值把关；GPU 被占时**拒绝启动**，不要替用户杀进程。凡视频侧 Krea2 / ComfyUI 在跑就必须先停。
- **CoT 坑**：引擎默认开思维链，小 `max_tokens` 会拿到空 content（实测 `content=null`）。`llm.yaml` 的 `strata.reasoning: off` 与调用侧 `reasoning_effort=none` 都不可删除。
- 端口改了要同步改 `llm.yaml` 的 `strata.base_url`。
- 冷加载最长约 5 分钟；`run_all.sh:start_llm_server()` 轮询 150×2s。

### 凭据

`.env`：`STRATA_API_KEY`（本地，值不校验）+ 注释掉的 `ZHIPU_API_KEY` / `MINIMAX_API_KEY`（云应急回滚，切 provider 时才需要真 key）。

## 红线（写死，不可违反）

1. **敏感词全链路剔除**：标题或正文命中 `daily/common.py:BLOCKED_NEWS_TERMS`（习近平 / 习主席 / 习总书记）的条目整体剔除，报纸与视频均不得出现。三道闸门：step4（标题级，进 LLM 前剔除省开销）、step6（标题 + 抓取后正文级）、step7（组装概述前最后一道，防重跑旧数据绕过）。日志不回显敏感标题原文。`习主席`/`习总书记` 用负向前瞻 `(?<![学练复预补温])` 排除「学习主席台」这类误切，「习近平」直接子串匹配。
2. **产物不手改**：`3新闻_概述.md` 是下游契约。唯一允许的人工改写路径是摘要质量闸门修复——改写后**必须重跑** `step8.py --date <日期>`，其余情况不要手改任何产物。
3. **GPU 独占**：Strata 与一切其它 GPU 负载互斥，见上。
4. **依赖外部 Chrome**：`daily/http.py:CHROMIUM` 单点硬编码 `C:/Program Files/Google/Chrome/Application/chrome.exe`，缺装则采集降级、渲染直接失败。

## 摘要质量闸门

`run_all.sh` 在 step7 之后扫 `3新闻_概述.md`，命中占位/空转摘要就**不生成报纸**并 exit 1。命中模式（`run_all.sh:228`）：

~~~
请提供 | 我才能根据 | 无法概括 | 正文仅包含 | 仅表明 | 无法生成 | 请补充 | 未包含具体新闻事实
~~~

同一组模式也在 `step7.py:PLACEHOLDER_PATTERNS`（多一个「不包含具体」），用于逐条摘要校验与定向重试。撞上闸门时：改写概述 -> 重跑 step8，不要重跑全链。

## 正文提取现状（别照旧文档写）

`AGENTS.md` 与新文档都说 step6 是「5 层 regex 策略链（TRS_Editor -> article -> class 匹配 -> p 回退）」。**代码已经不是了**：`step6.py:21` 引入 `trafilatura.extract`，`extract_body()` 先走 trafilatura，ckxx 系页面回退到 `_extract_ckxx_content_txt()`，站点噪声清理收敛到 `SITE_POSTPROCESS` 注册表。并发 `STEP6_MAX_WORKERS = 4`。正文提取失败的条目会在 step7 被检测到并直接终止管道（不是降级跳过）。

## 渲染与截图（最近一次修复的坑）

chromium 的**进程退出与 `--screenshot` 落盘不是原子的**：实测进程 0.5s 返回、文件 4s 后才写完。旧代码在 `subprocess.run` 返回瞬间只 `exists()` 查一次，查不到就判失败并跳过裁剪，迟到的未裁剪整窗图（4320x20000）被下游当成品。

现在的判据是「文件存在 + size > 0 + size 在 settle 窗口内不再变化」的轮询（`step8.py:_wait_for_screenshot`）。相关常量：`SCREENSHOT_WIDTH=2160`、`SCREENSHOT_HEIGHT=10000`（整窗 4320x20000）、`SCREENSHOT_SCALE=2`、`SCREENSHOT_WAIT_SECONDS=30.0`、`SCREENSHOT_SETTLE_SECONDS=0.3`。裁剪未生效或裁剪后高度仍等于整窗高度时会删掉半成品并返回失败——**不要把这种产物当成品**。

## 9:00 定时任务怎么接的（别按 AGENTS.md 找）

仓库文档写的「9:00 报纸定时任务」在 **Windows 任务计划程序和 WSL crontab 里都不存在**（实测两边都没有对应条目）。真正接的是 **DSH 任务板卡**：

- 卡片「每日新中国晨报（daily-china-news）」，cron `0 9 * * *`，prompt 是「加载并执行 daily-china-news 技能…」，即**按名字加载本技能**。
- 所以本技能的安装副本就是生产入口，写错就等于每天 9:00 跑错。
- 历史故障：旧卡三次执行全 failed，后两次是 `workspace not found`（工作区 id 失效），不是管道本身的问题。新卡已重建为 todo 状态。卡片失败先查工作区绑定，别一头扎进管道代码。

## 仓库约定（提交 / 文档流）

- **docs 改动也要走 PR**。`AGENTS.md` 写的「docs 直推 main」在 2026-10-13 起就不成立了：main 有分支保护（`enforce_admins: true`、required check `test (3.12)`、`strict` + `required_linear_history`），**owner 直推一样被拒**。实际路径：`docs/*` 或 `feat/*` 分支 -> PR -> CI 绿 -> merge。历史 PR #64 / #67 / #68 都是这么合的。
- **实现票走 feat + PR**，PR 正文用**中文详记踩坑**。
- **README 只在里程碑动**。
- `.gitattributes` 锁 `*.sh text eol=lf`——别用会写 CRLF 的工具改 shell 脚本。
- 仓库 `core.autocrlf=true`，用脚本写回文本文件时让 git 自己处理，别手搓换行。
- 开工前可查能力图谱：`node .opencode/get-shit-done/bin/gsd-tools.cjs graphify status`（当前 disabled）。

## 下游契约

`E:/每日新中国/<日期>/3新闻_概述.md` 是 **video-pipeline**（B 站读报视频）直接读报模式的每日输入。9:15 的视频任务（同样挂在 DSH 任务板上）会读它。改动这一步的产物格式前，先确认 video-pipeline 的读取约定（见 `video-pipeline` skill）。

## 排障

| 现象 | 先查这里 | 处置 |
|---|---|---|
| `项目虚拟环境不存在` | venv 是否在 | `py -3 -m venv .venv && .venv/Scripts/pip install -r requirements.txt` |
| `未找到 Git for Windows 的 bash.exe` | `run_all.ps1` 的候选路径 | 确认 Git 安装位；它按设计排除 System32 的 WSL bash |
| `GPU 已占用 xxxx MiB` | `nvidia-smi --query-gpu=memory.used` | 停对方负载（视频侧 Krea2/ComfyUI），不要杀用户进程 |
| `LLM empty content` | `llm.yaml` 的 `reasoning: off` | CoT 没关，小 max_tokens 会被思维链吃满 |
| `摘要质量闸门` 命中 | 概述里的占位摘要 | 改写概述 -> 只重跑 step8 |
| 截图失败 / 产物是 4320x20000 | 是否最新代码（#70） | 更新后重跑 step8；旧版会漏裁剪 |
| 某信源 0 条 | `E:/每日新中国/logs/daily.log` + step1_3 的 health banner | 单源失败不阻断其他源；连续 3 天低于 5 条会告警 |

日志：`E:/每日新中国/logs/daily.log`（`daily_logging.py` 统一入口），历史定时任务日志在 `logs/cron-<日期>.log`。

## 沙箱注意

`E:/Daily/...` 与 `E:/每日新中国/...` 在 DSH 的 workspace-write 策略下**只读**；`danger-full-access` 下可直接写。要改源码或写产物而沙箱回落时，先提权或由用户执行。

[![powered by dsh](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
