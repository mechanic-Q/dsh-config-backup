---
name: video-pipeline
description: B站读报视频全自动生产 driver（run_pipeline.py 一条命令：口播稿→TTS→分镜→H3→字幕→封面→确认门→发布）。Use when 用户要跑读报单/排障/单阶段旁路；agent 仅在确认门与异常回到现场。
---

# video-pipeline

从选题到三平台发布的完整视频生产流水线。driver 编排阶段 s01-s13（**目录号 = 阶段号配对**，00 为输入锚点，详见「文件管理」），每阶段有产出、自检门、确认门。Skill 为主，渐进披露（references 按需加载），阶段间只传文件。身份分层：编剧→导演→摄影师→翻译官→生产商（各环节 persona 见 `references/video-craft/personas.md`）。

> **v3（2026-09-05 起）：读报模式默认走 driver**——`scripts/run_pipeline.py` 全自动编排 s01-s13（分段→发布），agent 仅在确认门/异常/发布核验回到现场。见下方「运行方式」；逐阶段人工协作路径保留为 **agent/full 模式遗留**。

## 硬约束（写死，不可违反）

1. **严禁并行生成**：一次只能生成一个视频（5080 16GB 显存；H3 权重 ~21GB，offload 后运行峰值 ~30GB——单任务已靠显存↔内存倒腾才装下，双任务必爆。Strata 常驻 94% VRAM / Index-TTS 同卡同理互斥——生图窗口内 LLM 服务必须已停，见「Strata 窗口」）
2. **段时长 ≤15 秒**（H3 上限）
3. **中文口播语速 ≈6.4 字/秒**（E2 配方实测）：段时长 = 字数 ÷ 6.4 + 0.3-1s 余量。**预估仅供分段初算，最终时长以 TTS 实测为准**（driver 音频先行：TTS 实测在导演设计**前**回填 raw.json，无回填重跑循环；「TTS 回填后重跑阶段6」是 agent/full 模式遗留路径）
4. **整片时长覆盖**：Σ(段时长) ≥ 台词朗读总时长（按 6.4字/s 实测语速算）；≤15s 是单段上限，不是覆盖充分条件
5. **人物政策（2026-09-05 更新）**：禁**真实/可辨识人物**（无 I2V、无真人参考）；**虚构仙侠角色允许作为画面主体，中近景/特写可**——是否用人物、用多近由每段台词内容决定（信息/情绪落在人身上→人物镜头；宏观叙事→空镜或人物锚点；见导演设计规则 6/7）。人物画面内不说话；POV 段 = 第一人称手部视角（无脸不变）
6. **H3 画面内无人声**：prompt 必写 `the visuals play as off-screen voiceover narration while lips remain completely closed`
7. **BGM 后期叠加**：H3 prompt 的 `non_diegetic_music` 写 `N/A`
8. **发布前闸门不可跳过**（auto 模式也停）：产出成片 + 验收报告 → 等用户确认 → 才发布
9. **前 2-3 秒生死线**：口播稿第一句必须是钩子，禁铺垫式开场
10. **导演设计（内容/表演层）必须在分镜增强（技术执行层）之前**：先定"要表达什么情绪/哪个信息是重点" → 才能决定"用什么镜头表达"。先分镜后导演 = 先有答案再出题目。

## 运行方式：driver 模式（v3 默认）与 agent 模式（遗留）

- **driver 模式（v3 默认，读报模式）**：`scripts/run_pipeline.py` 全自动编排 s01-s13（s01 分段 → s13 发布），一条命令到发布，中间只有两处飞书确认门
- **agent 模式（full 模式遗留）**：agent 逐阶段人工协作，`mode=review`（每阶段确认门问用户）/ `mode=auto`（校验通过自动继续；失败自动重试限 2-3 次；同段连败 3 次停下等用户；发布前闸门不可跳过）。用于非读报模式、单阶段旁路、driver 异常后的人工接管

### driver 流程图（音频先行双车道，#30/#31 定稿）

```
口播稿（定稿——确认门①在 driver 之前，--script 输入即定稿）
  │
  ▼
[s01] 分段 segment_script ────────► 02/分镜清单_raw.json + 02/口播台词/shotXX.md
  │
  ▼
[s02] TTS 音频先行（Index-TTS GPU）─► 02_TTS语音/shotXX.wav + 欢迎词（当日日期动态生成）
  │
  ▼
[s03] TTS 实测时长回填 raw.json（duration_est——设计前回填，无回填重跑循环）
  │
  ▼
[s04] 设计+H3 prompt（Strata 压缩车道，#57-60 定稿；#84 起后端 Strata）：
  │      driver 全自动托管 Strata 窗口（#85）——s04 入口 ensure_online（探活 → powershell
  │      跨界拉起 → 300s 加载窗口 → 预热放行），s08 生图前自动停（taskkill 树杀+显存
  │      回落双确认）、s11 前再拉、s12 确认门前停（conflict 纪律，单视频常态两次加载）；
  │      窗口内异常走自愈阶梯不叫人（清残留→修复重启 ≤2 轮，耗尽才硬失败叫人）；
  │      单次调用直出 06_H3prompt/shotXX.json（s05/s06/s07 写虚拟 stamp）
  │      legacy 回退：--lane legacy = v3 四阶段链（#86 起四阶段同指 Strata，结构不动）
  │
  ▼
  ├── GPU 车道：[s08] H3 逐段生成（submit_accel 串行）→ 抽帧 → vision 竖屏检查
  │             （翻转段消毒重生成 ≤2 轮：判官复判为准名单，不修交人）──► 07_成片/1080P/
  └── CPU 车道：whisper 词级时间戳（并行）──► _wsout/
  │
  ▼
[s09] 读报装配 compose_final（片头三合一 + 翻页过渡 + BGM 0.15）
[s10] 字幕 gen_subs + 烧录（顶部标题 drawtext + 底部口播 subtitles）→ 压缩 CRF28
  │
  ▼
[s11] Krea2 封面横竖版（LLM 读稿提炼参考文本；conflict 纪律此处再拉 Strata）──► 09_封面/
  │
  ▼
[s12] 🔔 确认门④+⑤ 成片+封面合并审核（2026-09-05 拍板：审一次）
  │   飞书一次发：审核版成片 + 横竖封面；【通过】即发布
  │   【重生成】= 重做封面再送审；成片要改 = 修改意见 → 叫 agent
  ▼
[s13] 发布 B站(tid=21) → 45s → 抖音(headless+AI声明) → 视频号自动发（v5，含封面+原创声明）；
      视频号失败才降级待发文件夹
      + 核验报告 ──► 10_发布/发布核验报告.md
```

### driver 用法

```bash
# 标准跑法（口播稿定稿后一条命令到发布）
python3 scripts/run_pipeline.py --script <slug>/00_口播稿/口播稿.md

# 后台跑（脱离终端，会话退出不打断；日志与退出码可追，#195）
python3 scripts/run_driver_bg.py --script <slug>/00_口播稿/口播稿.md   # driver 参数原样透传
python3 scripts/run_driver_bg.py --status --workdir <slug目录>        # 查存活/退出码

# 变体
--platforms bilibili          # 只发 B站（默认 bilibili,douyin,kuaishou,toutiao 四平台自动发；视频号由 s13 完成后 driver 钩子自动发——sph_publish_v5.py，含封面+原创声明，失败才降级待发文件夹；发布器从自身位置解析邻居，与 driver 同仓，见「脚本一览」）
--lane compressed|legacy      # 设计车道（默认 compressed=Strata 单次调用直出 06_H3prompt；legacy=v3 四阶段链结构回退，#86 起同指 Strata）；切换自动清 s04-s07 stamps（pitfalls #45）；env DRIVER_LANE 同效，CLI 优先
--bgm ""                      # 禁用 BGM（默认 Documentary Discovery，音量 0.15）
--style <名>                  # 风格块（默认 chinese-celestial-palace 天宫）
--res 1080P                   # 分辨率（默认 1080P）
--date YYYY-MM-DD             # 日期（默认从 slug 目录名推导）
--workdir <slug目录>          # 项目目录（默认从 --script 推导）
--fresh                       # 清 stamps 全量重跑
--dry-run                     # 只打印执行计划（每阶段 RUN/SKIP）
--stop-after s07              # 跑到指定阶段（含）后退出（s01-s13；测评/半程验收用，该阶段照常写 stamp，续跑接得上）
--gate-timeout <秒>           # 确认门等待上限（默认 3600）
--gate-approved               # 确认门已由用户在 agent 会话明确批准（跳过飞书轮询、不重发包，直接进发布；何时用见「确认门说明」）
--status                      # 只读查状态（运行中/卡住/已崩/已完成）后退出，见下
--stall-after <秒>            # 「卡住」阈值（默认 300；env DRIVER_STATUS_STALL_SEC 同效，CLI 优先）
```

- **查状态（#146）**：`python3 scripts/run_pipeline.py --status --script <slug>/00_口播稿/口播稿.md`（也可 `--workdir <slug目录>`）。
  一屏给四态之一 + **判定依据**：`已完成`（`_driver/stage_s13.json` 在）/ `运行中`（进程在 + 心跳新鲜）/ `卡住`（进程在 + 心跳超过阈值没更新）/ `已崩`（进程不在 + 无完成标记，附上次退出码）；另有 `未开始` 表示还没起跑。
  同时显示当前阶段与阶段耗时、心跳年龄、进度 N/13、日志尾三行。
  **只读**：不写文件、不发通知、不重启，也不跑预检——确认门轮询 6 小时期间照样能查。
  心跳来自 `_driver/heartbeat.json`（driver 每 15s 推进；无此文件的旧运行回退用日志 mtime）。
  注意 `report.json` 的 `finished_at` **不是**完成标记（连败/确认门硬退出/finally 都会写它），完成标记只认 s13 stamp。
  跨宿主探活按 `docs/host-contract.md` 的声明走；探不准就明说「进程存活未知」，不猜。

- **后台启动（#195）**：`python3 scripts/run_driver_bg.py --script <口播稿>` 一条命令把 driver 脱到终端之外（Windows 走 WMI `Win32_Process.Create`，POSIX 走 `start_new_session`），**会话退出不影响**。落点固定：日志 `<slug>/_driver/bg-<run_id>.log`（每轮独立文件）、记录 `<slug>/_driver/bg_run.json`（run_id / 看守与 driver 的 pid / 状态 / 退出码 / 起止）。
  启动命令的**退出码只代表「启没启动起来」**（0 已起 / 1 守卫拒绝或启动失败）——driver 的真实退出码（0/3/4）去 `--status` 或 `bg_run.json` 查；**不要**接到 `| tail` 上判结果（#194：管道吞退出码）。`run_pipeline.py --status` 也已认这份后台记录（优先显示它记的日志、补上「预检就退场、没来得及写心跳」的退出码）。
  单实例守卫（AC4，**严禁并行跑两期**——s13 收尾 `shutdown_comfyui()` 清卡会杀掉另一期正在烧段的 ComfyUI）：① 同一 slug 的 `_driver/heartbeat.json` 或 `bg_run.json` 里进程还活着 → 拒绝；② 机器级锁 `~/.video-pipeline/driver_bg.lock`（`DRIVER_BG_LOCK` 可覆盖）原子占锁，别的读报单在跑 → 拒绝，锁主已死 → 接管并告警，跨宿主探不准 → 拒绝。**明确文档化**：锁只覆盖经本工具启动的运行；手工前台 `run_pipeline.py` 不持锁，跨期并行仍靠入口自查，本工具不提供 `--allow-parallel`，也无意提供。
- **断点续跑**：重跑同命令即可。`_driver/stage_*.json` stamps 自动跳过已完成阶段；口播稿 hash 变了自动清 stamps 全量重跑；H3 段级产物存在即跳过
- **失败策略**：driver 层每阶段自动重试 1 次；同阶段连败 2 次 → 飞书叫人 + 优雅退出（stamps 留现场，处理后重启即续）

### 每日无人值守（定时任务入口，2026-09-25 定案；2026-10-14 随报纸管道原生化更新）

每日两条定时任务（一会话限挂一个）：

1. **9:00 报纸**：Windows 原生任务。入口、自动启停 Strata @8080（~15.7GB，nvidia-smi 互斥把关）与全链 15-20 分钟的宿主归属，见 `docs/host-contract.md` 的宿主与目录契约（迁移历史见 pitfalls 与 ADR 0005）。产物 = `E:\每日新中国\<日期>\3新闻_概述.md` + 报纸 PNG；占位摘要 = 质量闸门命中，人工改写后 `.venv/Scripts/python.exe step8.py --date <日期>` 重跑；日志 `E:\每日新中国\logs\cron-<日期>.log`
2. **9:15 视频**：前置检查（报纸已结束——按 `docs/host-contract.md` 的定时链路一节查进程与 GPU 水位：`nvidia-smi --query-gpu=memory.used` >2000MiB 即 GPU 忙）→ 输入就绪（3新闻_概述.md 存在且无占位摘要）→ ComfyUI 无头直启（在线判据见 `docs/host-contract.md` 的定时链路一节）→ `new_daily.py` 建读报单 → 脱离启动 driver（`--gate-timeout 28800`）→ 飞书确认门等用户。入口层不代用户做任何确认门决策：绝不传 `--gate-approved`，失败只通知不代批，绝不并行第二条流水线

### 模型分配表（#29 实测定稿；2026-10-12 全切 Strata #82-#87；2026-10-14 vision 本地化 #101-#107，**云端例外退役、全链无云**）

| 环节 | 模型 | 端点 | 说明 |
|---|---|---|---|
| s04 设计+H3 prompt（压缩车道默认） | strata（Qwen3.8-Flash-Next IQ3_S）**请求级关思维链**（`chat_template_kwargs.enable_thinking=false`，唯一有效写法） | 本地 Strata（端点与跨侧访问见 `docs/host-contract.md` 服务表；driver 托管窗口） | 单次调用直出 06_H3prompt；s05/s06/s07 写虚拟 stamp；热态生成 148-159 tok/s（#83 实测）；就绪后首请求 ~70s 属预热非故障 |
| s08 翻转重写 + s11 封面参考文本 + 平台标题 | strata（同上；#86 起文案链全本地） | 本地 Strata（同上） | 原云端 9router `low` 调用随 #86 切本地；窗口纪律 s08 前停/s11 前再拉 |
| s08 抽帧竖屏判读 | qwen3-vl-4b（盲比定档，ADR 0007） | 本地 llama-server（端点见 `docs/host-contract.md` 服务表，CPU 窗口化——判读窗口用完即关，ADR 0007，`DRIVER_VISION_API` 缺省指本地） | 10-14 起本地化：CPU 与烧卡零显存交互、s08 边烧边判时序不变；判读面零改动；9router 留显式 env 回退；CLIP 线性探针平行兜底（reconcile 合成，层缺席=单路 VLM）；翻转段消毒重生成 ≤2 轮（判官复判为准重烧名单，T16 #128） |
| H3 视频 | MiniMax H3 | Windows ComfyUI（端点与访问方式见 `docs/host-contract.md` 服务表） | GPU 串行；烧卡窗口内 Strata 必须已停（94% VRAM 互斥） |
| TTS | Index-TTS-2.5 | 本地 GPU | E2 模仿配方默认 |
| whisper 字幕 | turbo 词级 | 本地 CPU | 与 H3 GPU 并行 |
| legacy 车道 s04 导演 / s05 摄影师 / s06 美术 / s07 H3 prompt | strata（同上，`LLM_MODEL`/`LLM_API` 缺省即 Strata） | 本地 Strata（同上） | v3 四阶段链结构回退（#86 端点同切 Strata）；8888/9router 文案端点已退役 |

### 确认门说明（#30 定稿 + 2026-09-05 拍板：④⑤合并审一次）

| 门 | 内容 | 形式 |
|---|---|---|
| ①稿件审核 | driver **之前**（`--script` 输入即定稿） | 人工 |
| ②视觉方向 | 自动选读报固定方向（方向卡推荐项） | 无门 |
| ③增强表审核 | 自动旁路（七项自检闸门把守） | 无门 |
| **④+⑤成片+封面合并审核** | 发布前唯一闸门，不可跳过；封面 vision 复检并入党人审 | 飞书**一次发**：审核版成片 + 横竖封面 |

**回复协议**（feishu_gate.py 分类）：「通过/ok/确认/👍」类 → 直接发布；「重生成/重来/换一」类 → 带段号（`shot02`/`3、5-8`/`1-4`）即镜头级重做，无段号重做封面，均重做后重新送审；**人工指认翻转**（含指认词「翻转/横躺/横着/竖着/旋转/倒了/倒着」：`shot03 翻转了`、`第3段翻转了`，不带段号也认）→ flag：先存被定罪帧（who/when/verdict 落台账 human_flags）+人工真值回填 user_flagged，再指名段重烧+复判，仍翻转继续存证+换法重烧直到通过或人喊停，不停机（T17 #129；CLI `--flag-shots "shot03=翻转,shot05=正常"`）；**裸段号点名**（`shot02`、`shot02 不行`——不带重做动词与指认词）同样直接进镜头级重做——`shot` 前缀即镜头指称（#69 收紧），**裸数字仍不算**（`2`、`第 3 集不错` 归修改意见）；其它文字（修改意见）或超时 → 飞书留言后 driver 退出叫 agent，处理完重启续跑（stamps 保留，确认门重进）。越界/倒序区间（`0-5`、`5-0`、`1-199`）整段丢弃不拾端点，段号全废时按无段号处理。轮询走群历史消息 REST（只读）。确认门是**群**不是私聊：IM 助理是 websocket 事件推送（只推 @ 机器人的消息），私聊里人的回复会被助理当活跃会话消费，driver 再轮询同一会话会把一条「通过」判两次；群里不 @ 机器人 ⇒ 助理侧收不到、两边不抢（T13 #125 / pitfalls #56）。

**agent 批准路径（2026-09-22 实证，勿绕路）**：轮询按 `sender_type=app` 过滤 bot 自发消息，所以用 `feishu_gate.py send` 代发「通过」**无效**——bot 发的消息轮询自己读不到（当天正是绕了这一步才翻 driver 源码找到参数）。用户已在 agent 会话明确批准发布时，正确路径 = 停掉 driver，重启加 `--gate-approved` 续跑：stamps 保留（s01-s11 跳过）、不重发审核包、直接进 s13 发布。这不是跳过门——④+⑤ 合并审一次、发布前唯一闸门的语义不变，只是批准场景从飞书群换到 agent 会话，前提必须是**用户明确批准**（agent 不得代用户决定发布）。

### driver 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DRIVER_LANE` | compressed | 设计车道（CLI `--lane` 优先）：compressed=Strata 单次调用 / legacy=v3 四阶段链（同指 Strata）；车道变化自动清 s04-s07 stamps（首次记录 lane 的旧现场不清，pitfalls #45） |
| `STRATA_URL` | 见 `docs/host-contract.md` 服务表（本机 127.0.0.1） | Strata 端点（单一事实源 strata_h3_lane；勿写死 NAT 子网 IP） |
| `STRATA_MODEL` | strata | Strata 模型名（请求显式携带，缺 model 服务端虽容忍但红线要求可断言） |
| `STRATA_START_CMD` | powershell Start-Process 串（#83 spike §5 定案） | Strata 跨界拉起命令（--host 0.0.0.0；变更经此 env 承载） |
| `STRATA_LOAD_TIMEOUT` / `STRATA_WARMUP_TIMEOUT` | 300 / 180 | 加载窗口（秒）/ 预热首请求长超时（秒，首补全 ~70s 属常态） |
| `STRATA_MAX_ROUNDS` / `STRATA_STALL_SECS` | 2 / 600 | 自愈修复重启硬边界（冷启动不占预算）/ 单次 LLM 调用挂死阈值（秒） |
| `STRATA_WINDOW_MODE` | conflict | 显存窗口纪律：conflict=生图前停/s11 前再拉/尾部停（#83 实测 94% VRAM 冲突）；coexist=常驻（显存充裕的机器） |
| `DRIVER_BGM` | Documentary Discovery（pixabay） | BGM 文件；`--bgm ""` 禁用 |
| `DRIVER_VOICE_REF` | expressive-reference.wav | TTS 音色参考 |
| `DRIVER_VISION_MODEL` | qwen3-vl-4b（盲比定档 alias） | 抽帧判读模型（本地 llama-server；云端回退=同时设 `DRIVER_VISION_API`） |
| `DRIVER_VISION_API` | `vision_common.default_api()`（见 `docs/host-contract.md` 服务表） | 判读端点；设 9router 20128 即一条切回云端 |
| `DRIVER_VISION_MAX_TOKENS` | 200（10-14 实测 verdict JSON 8-15 tok，10 倍余量） | 判读 max_tokens |
| `DRIVER_VISION_IMG_MAX_SIDE` | 896 | 送判图长边上限（缩图预算，pitfalls #55；0/负=原图直送） |
| `DRIVER_VISION_READY_TIMEOUT` | 300 | 本地判读服务拉起就绪等待秒数（超时降级留痕） |
| `VISION_JUDGE_HOME` / `VISION_JUDGE_PORT` | E:\AI\LLMision-judge / 8100 | 判读服务供给目录（bin+models）/ 端口 |
| `DRIVER_JUDGE` | 开 | 判官层开关（=0/false/no 关闭；关闭时复判直接 fail-open 采信本地）。T14 #126 |
| `DRIVER_JUDGE_PROFILE` | `judgeprobe` | 判官 DSH profile 名（profile 文件在仓 `dsh-profile/judgeprobe/`，装机见其 README） |
| `DRIVER_JUDGE_TIMEOUT` | 900 | 判官单次判读超时秒（超时/故障一律 fail-open，不抛异常） |
| `DRIVER_JUDGE_MODEL` | `glm-5.3-flash`（显示回落：status 优先读 profile 实配，读不到才用本值） | 台账记录的判官模型名 |
| `FEISHU_CHANNELS_JSON` | `~/.dsh/dsh-im-connect/channels.json`（双端候选见 `docs/host-contract.md` 的 `dsh-creds` 服务） | 飞书凭证文件路径；不设时按双端候选自动定位（T13 #125） |
| `FEISHU_CHANNEL` | `feishu_439061570ff1` | channels.json 里取哪条渠道的 config.appId/appSecret |
| `FEISHU_GATE_CHAT_ID` | `GATE_CHAT_ID`（读报确认门群常量） | 确认门目标群，覆盖常量 |
| `FEISHU_GATE_SILENT` | 未设 | =1 时静音确认门进展日志（凭证源留痕等），测试用 |

飞书凭证默认取 DSH 侧 `~/.dsh/dsh-im-connect/channels.json` 里飞书渠道的 `config.appId/appSecret`（与 IM 助理同一套应用，T13 #125 起；跨侧候选见 `docs/host-contract.md` 的 `dsh-creds` 服务），环境变量 `FEISHU_APP_ID`/`FEISHU_APP_SECRET` 覆盖一切，`~/.hermes/.env` 只在 DSH 源不可用时兜底（会打一行「⚠️ 旧口径回退」留痕）；确认门群 = `GATE_CHAT_ID`（常量，`FEISHU_GATE_CHAT_ID` 可覆盖）。收发实现在 `scripts/feishu_gate.py`，可独立 CLI 自检（`send`/`poll`/`gate`）。

## 旁路模式

- 只要口播稿：阶段1
- 只要整体视觉方案：阶段1 + 2
- 只要逐段设计/分镜：阶段1 + 2 + 3 + 4
- 只要 prompt：阶段1 + 2 + 3 + 4 + 5 + 6 + 7
- 只要成片：完整十个阶段
- **直接读报模式**：跳过阶段1（编剧不加工），稿子直接放 `00_口播稿/口播稿.md` → 从阶段2 起。适用场景：每日新中国 `3新闻_概述.md` 等已定稿的新闻概括，分段时自然按新闻条目断句，下游全流程不变（**driver 模式即此路径的全自动化**）。`3新闻_概述.md` 由 9:00 报纸任务生成，入口与产物口径见 `docs/host-contract.md`，产物落 `E:\每日新中国\<日期>\`。

## 文件管理

每稿一个自包含文件夹（**按稿子标题/关键词命名 slug**），目录编号 = 流程执行顺序。**每个阶段只从上一阶段落盘的文件取输入，分析完落盘，下一阶段再读文件——中间产物不进 agent 上下文（交接即文件）。**机制由 `pipeline_contract.py` 强制（check_input 上游校验 / stamp_output 完成标记）。

> **🚨 项目位置铁律（2026-08-29 用户纠正）**：项目文件夹必须建在**稿子文件旁边**（稿子所在目录的同级/同父目录），**严禁建到 WSL 的 /home/lmr 下**。例：稿子 `/mnt/e/每日新中国/2026-08-28/3新闻_概述.md` → 项目建 `/mnt/e/每日新中国/2026-08-28/video/每日新中国b站/流水线/<slug>/`。拿到任何稿子先问/确认稿子位置，再在旁边建项目。旧项目已从 `/home/lmr/每日新中国b站/流水线/daily-china-20260828/` 迁移到 E 盘稿子旁。

```
<稿子目录>/
├── <slug>-口播稿.md              ← 稿子原件
└── <slug>/                        ← 每稿自包含文件夹（目录号 = 阶段号 s01-s13 配对）
    ├── 00_口播稿/                  ← 输入（driver 之前定稿）：口播稿 + 来源（锚点，永不改）
    ├── 01_分段/                    ← s01：分镜清单_raw.json + 口播台词/（s03 回填 raw 的 duration_est）
    ├── 02_TTS语音/                 ← s02：shotXX.wav + _welcome/shot01.wav（欢迎词）
    ├── 03_导演设计/                ← s04：创意方向卡 + 整体视觉方案 + 交接摘要 + shotXX.json/md + 交接卡/
    ├── 04_分镜增强/                ← s05：分镜清单_enhanced.json（执行字段）+ _自检报告.md
    ├── 05_美术画质/                ← s06：画质设计.json（光质/材质/景深/负面词/风格锚定）
    ├── 06_H3prompt/                ← s07：shotXX.json（prompt 正文在 `prompt` 字段内嵌三段字段头）
    ├── 07_成片/                    ← s08-s10 共用：1080P/（H3 原始段）+ 装配中间产物 + 最终成片.mp4 + verify报告
    ├── 08_字幕/                    ← s10：字幕.srt
    ├── 09_封面/                    ← s11：海报 + 平台标题.json
    ├── 10_发布/                    ← s13：发布核验报告.md
    └── _driver/                    ← stamps（stage_sXX.json）+ report.json 台账
                                       + heartbeat.json（15s 心跳，--status 判四态用）
```

slug 从稿子文件名提取（去日期前缀和"-口播稿"后缀）。H3 原始输出在 `07_成片/<清晰度>/`（如 1080P/，自包含），不在稿子同级建独立 480P/ 文件夹。

> **目录说明（2026-09-07 配对重整）**：**目录号 = driver 阶段号**（s01-s13 顺序编号；00 = driver 前的输入锚点）。分段（s01）与导演设计（s04）拆开各自落盘：`01_分段/` 只放 raw.json + 口播台词，`03_导演设计/` 放方向卡/整体方案/交接摘要/**shotXX.json** + 交接卡/。交接上下文 = 短摘要卡（`00_交接摘要.md`）+ 逐镜交接卡（`交接卡/shotNN.json`，含情绪/镜头/信息点/衔接/风格锁/竖屏，s07 读此替代全文，token -60%）。`04_分镜增强/`（s05 摄影师）落地执行字段；`05_美术画质/`（s06）是美术层：每段 光质/材质/景深/负面词，s07 融入 H3 prompt。07_成片 由 s08（H3 原始段）/s09（装配）/s10（字幕烧录）共用——同一"视频产物"类别。存量旧布局项目用 `scripts/migrate_project_dirs.py <slug>` 一次性迁移。

## 十一阶段流程

> 阶段的输入/产出/自检门语义对两种运行方式都成立；agent 模式独有的确认门②③在 driver 模式自动处理（见「确认门说明」表），driver 新增动作（TTS 提前、时长回填时机、抽帧 vision 检查）见「driver 流程图」。driver 默认发布 B站+抖音+快手+头条（`--platforms` 可收窄）；视频号自 2026-09-29 起由完成钩子自动发（v5，含封面+原创声明），失败才降级待发文件夹。
>
> **车道注记（2026-10-12 更新）**：阶段2-6（s04-s07）四阶段语义适用于 agent 模式与 driver legacy 车道；driver 默认的**压缩车道**把四阶段压成对本地 Strata 的单次调用直出 06_H3prompt，前置防线随中间稿失效、质量兜底转移至生成期约束门+后置防线（ADR 0004 车道设计 + ADR 0006 后端选型；术语见 CONTEXT.md「压缩车道/Strata 窗口/自愈阶梯」）。

### 阶段1：口播稿创作

输入：选题 / 新闻原文 / 参考视频
动作：按 5 幕结构写稿（钩子→冲突→原理→类比→落点）
方法论：角色提示词见 `references/video-craft/personas.md` 阶段1 + 书源决策表 `references/video-craft/{mckee-story-structure,truby-anatomy-of-story}.md`（钩子/转折/激励事件）
硬约束：前 2-3 秒生死线（钩子第一句，禁铺垫式开场）
产出：`<slug>/口播稿.md`（含来源+URL+日期头）
自检门①：黄金三秒 / 信息点密度 / 落点落情绪不落知识
确认门①：稿件审核（用户改稿后才进流水线）

### 阶段2：整体视觉方案（全篇方向，内容/表演层）

> 本阶段 = 全篇视觉把控，先定"整片讲什么情绪/什么风格方向"，产出落盘后供逐段设计读取。

输入：`00_口播稿/口播稿.md`（定稿）
动作：
- 步骤A1：LLM 通读全文 → 出 2-3 个整体视觉方案方向（创意方向卡）
- 确认门②：用户选方向（2-3 选 1，推荐项第一）
- 步骤A2：按选定方向 → 全篇整体视觉方案（视觉主线 / 场景系统 / 重复母题 / 色彩节奏 / 节奏曲线 / 段间衔接）
- 产出 `00_交接摘要.md`：只含方向名 / 视觉主线 / 风格锁 / 色彩基线 / 禁忌（短，防污染）
方法论：导演角色提示词见 `references/video-craft/personas.md` 阶段2 + 书源决策表 `references/video-craft/{proferes-directing-fundamentals,mamet-on-directing-film,lumet-making-movies}.md`（镜头动机/一个镜头一个想法/主题统一）+ `shot-grammar.md`
角色投射：system prompt "你是专业导演/美术指导" + 方法论叠加
工具：`director_llm.py --directions`（创意方向卡 + 整体方案）
产出：`03_导演设计/{00_创意方向卡,00_整体视觉方案,00_交接摘要}.md` 【落盘】
自检门②：①方向卡有 2-3 个可选 ②整体方案六要素齐全（主线/场景/母题/色彩/节奏/衔接）③交接摘要 ≤15 行（短）

### 阶段3：逐段视觉设计（读 01 → 分段 + 每段设计）

> 本阶段**只读 `03_导演设计/` 的落盘文件（含交接摘要）**，不靠对话记忆。分段 + 每段设计一次完成，产出落盘。

输入：`03_导演设计/00_交接摘要.md` + `00_口播稿/口播稿.md`
动作：
- 按口播节奏切段：≤70字/15s 硬约束、语义断句（句号/感叹/问号）、专有名词不拆、短句并入前段
- 逐段设计：情绪基调（内容/表演层）+ 视觉重点 + 信息点 + POV 手部动作细节（严格对齐整体方案）
- **导演输出 JSON（S2 结构化）**：`shotXX.json`，经 `schema.DIRECTOR_SHOT_SCHEMA` 强校验（节奏首词 6 枚举强制，不合格反馈重试）；`shotXX.md` 旧版兼容
- 分段产物落盘：`01_分段/分镜清单_raw.json`（`segment_script.py`）
- 纯台词提取落盘：`01_分段/口播台词/shotXX.md`（给 TTS，纯原文）
工具：`segment_script.py` + `director_llm.py`（逐段设计，用 `llm_json` + schema）+ `llm_json.py`
校验：事实保真（数字/专有名词锚点）+ **整片时长覆盖**（Σ段时长 ≥ 台词朗读时长，4.5字/s）
产出：`01_分段/分镜清单_raw.json` + `03_导演设计/shotXX.json` + `01_分段/口播台词/shotXX.md` 【落盘】
自检门③（时长覆盖）：①每段 ≤15s ②Σ(段时长) ≥ 台词总时长/4.5 ③每段有情绪基调（JSON 首词=6枚举）④每段有信息点 ⑤专有名词未拆开 ⑥逐段设计对齐整体方案（无风格漂移）

### 阶段4：分镜增强（摄影师：怎么拍，后置）

> 分镜增强 = **摄影师环节**。导演（阶段3）已定好"这段讲什么/什么情绪/什么镜头决策"，摄影师负责"怎么拍"的执行细节——**导演做过的摄影师不重做**（不重定景别/节奏/Hook/信息点），只做执行：秒级指令/光线基线/空间锚点/段间交接。输出执行字段（S3 删 4 决策字段）。

输入：`01_分段/分镜清单_raw.json` + `03_导演设计/shotXX.json`（导演设计，`shotXX.md` 旧版兼容）+ `03_导演设计/00_交接摘要.md`（风格锁）
动作：
- LLM 摄影师逐段输出执行字段（秒级指令/光线基线/空间锚点/交接），注入摄影知识 reference
- `storyboard_photographer.py`：读导演设计 JSON + 交接摘要 → 生成 enhanced.json（`llm_json` + `schema.PHOTOGRAPHER_SCHEMA` 强校验）
- `storyboard_enhancer.py`：七项自检闸门（校验 LLM 产出，枚举收敛到 schema）
方法论：读 `references/video-craft/{camera-language,camera-movement,shot-grammar,continuity-editing,camera-angle-depth,katz-shot-by-shot}.md`（摄影知识：拉毕格4章 + 卡茨9域决策表 + 机位角度/景深专项）
角色投射：system prompt "你是资深电影摄影师"（见 personas.md 阶段4）
工具：`storyboard_photographer.py` + `storyboard_enhancer.py`（自检）+ `llm_json.py` + `schema.py`
产出：`04_分镜增强/分镜清单_enhanced.json` 【落盘】
自检门④（七项）：①时长≤15s ②节奏意图合法（读导演设计）③连续性链成环 ④Hook分布（首尾强+每3段1强；**导演平铺节奏判据：全片弱节奏[铺垫/建立/预备]时④PASS，尊重导演读报节奏选择**）⑤有信息点 ⑥镜头类型+秒级指令合法 ⑦**景别交替**（上一镜末景别 vs 下一镜首景别不重复；中近景↔中景同级放行，特写→特写内容不同放行）
确认门③：增强表审核（可旁路）

### 阶段5：美术画质（美术画室，怎么精致，后置）

> 美术画室 = **美术/画质指导**。导演（阶段3）定了"拍什么"、摄影师（阶段4）定了"怎么拍"，美术层负责"画面质感有多精致"——为每段配画质词（光质/材质/景深/负面词），锚定内容、尊重风格块。**风格化管"是什么风格"，美术层管"质感多精致"，维度独立不重叠。**

输入：
- `04_分镜增强/分镜清单_enhanced.json`（摄影师：机位/光线基线 → 光质依据）
- `03_导演设计/shotXX.json`（导演：场景/主体 → 材质/景深依据）
- `03_导演设计/00_交接摘要.md`（风格锁）
- `--style`（风格块 → 风格分流选词，勿混）
动作：LLM 美术指导逐段配画质词（光质/材质/景深/负面词），`schema.ART_SCHEMA` 强校验（3 词非空 + 风格锚定）
工具：`art_director.py`（llm_json + ART_SCHEMA + 并行）
方法论：`references/video-craft/quality-modifiers.md`（决策规则表）+ personas.md 美术身份层
产出：`05_美术画质/画质设计.json`（每段 {光质, 材质, 景深, 负面词, 风格锚定}）【落盘】
自检门：①每段 光质/材质/景深 非空 ②风格锚定非空 ③词符合风格块负向约束（勿混）

### 阶段6：H3 画面 prompt

输入（**汇聚到每个镜头**）：
- `03_导演设计/shotXX.json`（导演 JSON：场景/主体/镜头/情绪——S2 结构化，`shotXX.md` 旧版兼容）
- `03_导演设计/交接卡/shotNN.json`（S4：本镜交接卡，含情绪/镜头/信息点/衔接/风格锁/竖屏——**替代整体方案全文，token -60%**）
- `04_分镜增强/分镜清单_enhanced.json`（摄影师：秒级指令/锚点/交接——**全字段注入，非仅时长**）
- `05_美术画质/画质设计.json`（美术层：光质/材质/景深/负面词——**质感以此为准，融入画面描述**）
- `03_导演设计/00_交接摘要.md`（无交接卡时 fallback）
- `--style`（风格块）
动作：LLM 综合以上信息转译三字段（`integrated_multimodal_description` / `overall_soundscape` / `non_diegetic_music:N/A`），优先级：镜头语言以导演【镜头】为准（唯一镜头权威）、秒级执行以摄影师为准、风格母题以整体方案为准。**任何一路上游落盘的信息都必须进入最终提示词，不允许"落了文件但没汇进 prompt"。**
方法论：`h3-prompt-writing` skill（MiniMax 官方提示词规则）+ `references/video-craft/personas.md` 阶段5
工具：`director_to_h3_llm.py`（llm_json 流式+强校验+并行 + **`_imd_ok` 内容校验：desc 空壳/占位符(...)/过短(<200)/指令模板复制 自动重试至3次**）+ `pov_scene_llm.py` + `merge_pov_scenes.py`
**官方语法（S6b 启用）**：
- `[Shot N]` 分段（首镜无时间戳，后续 `At 00:SS.SSS` 递增，切点来自摄影师秒级指令）
- 首镜风格声明（Cinematic/3D CG/live-action 等）
- 相机运动三要素（类型+幅度+速度）
- 叙述者 `(S1)` + off-screen voiceover + lips closed
- 画面内文字英文双引号
**模式适配（PR#8）**：`--reference image:first:xx` → I2VA；`--reference image:last:xx` → L2VA；首尾帧 → FL2VA；多资产 → Ref2VA 六段式；无参考 → T2VA（默认）
校验：`llm_json` + `schema.H3_SCHEMA` 强校验（缺字段自动重试）+ `enforce_constraints`（off-screen + lips closed + music N/A + POV 标记 + 风格锁）+ **竖屏方向约束**（`PORTRAIT_CONSTRAINT` 自动注入：9:16/天空正上/地面正下/不旋转/镜头正立；风险词命中即告警需人工复核）
产出：`06_H3prompt/shotXX.json`（prompt/video_seconds/mode/camera_sanitized/portrait_risk）【落盘】
自检门③b（竖屏方向，P0）：`scripts/check_portrait_orientation.py <06_H3prompt>` 全段含竖屏约束句（vertical 9:16 / sky at the very top / not rotated / forbidden），风险词命中段生成后必须抽帧 vision 复核画面竖直（天空在上地面在下、无旋转90°、无俯视鸟瞰）

### 阶段7：TTS 配音

输入：`01_分段/口播台词/`（纯台词）+ 用户音色参考
动作：Index-TTS-2.5 批量配音（每段 audio_XX.mp3）
工具：`tts_batch.py`
产出：`02_TTS语音/audio_XX.mp3` 【落盘】

**阶段7 收尾步骤：时长回填（TTS 实测 → 消除语速预估偏差）【agent/full 模式遗留——driver 音频先行时 TTS 在设计前回填 raw.json，无此步骤无重跑循环】**
> 语速预估与实际 TTS 时长有偏差 → 若不回填，H3 视频比音频长 3-5s/段，compose_final -shortest 裁掉视频尾部（丢动作节拍第3拍）。实测偏差：预估 4.5字/s 时 H3 总长 138s vs 音频 90.7s。
- 工具：`sync_tts_durations.py <enhanced.json> <02_TTS语音> [--prompts 06_H3prompt] --write`
- 动作：ffprobe 实测每段音频时长 → 回填 enhanced.duration_est（+0.3s 余量，封顶15s）+ per_second_directives 时间按比例缩放 + prompts video_seconds 同步（默认 dry-run）
- **回填后必须重跑阶段6（At 时间码随新时长重算）再进阶段8**

### 阶段8：生成 + 合成（GPU 硬串行）

输入：`06_H3prompt/shotXX.json` + `02_TTS语音/audio_XX.mp3`
动作：
1. H3 生成 直出 1080P ≤15s（串行严禁并行）→ `07_成片/1080P/`（⚠️ 工作流 2026-10-13 改版：出片链 394 DLSSNR 2x → 395 VHS_VideoCombine（193 SaveVideo 弃用）；ui2api/fix_models 含 **bypass 直通重接**（UI bypass 节点 API 化后无 mode 概念会被当真执行，多套一层 attention patch）+ 剪枝后主链完整性校验；**工作流每次改版先干跑 `fix_models(ui2api.convert(wf), wf)`** 目检关键接线再上真跑，pitfalls #52。遗留：395.audio 未接——H3 原始段无自带音轨，读报装配不受影响（人声合成显式 `-map 0:v -map 1:a` 取 TTS），但勿依赖 H3 原始段音轨）
2. **抽帧竖屏检查（P0，防 90° 翻转）**：`extract_h3_frames.py <1080P> <帧目录>` 抽帧 → vision 逐帧复核"画面是否 90° 翻转"——翻转段必须重生成。竖屏约束句已强制在 desc 开头（`director_to_h3_llm.enforce_constraints`），但 H3 对横构图场景（太空俯瞰/平台横排）仍会偶发旋转，**必须生成后抽帧拦截**
3. **片头 + 报纸段装配（读报模式必守）**：`compose_final.py` 传 `--intro 每日新中国片头.mp4 --newspaper 当日报纸图.png --welcome-audio 欢迎词.wav` → 片头视频段 + 报纸配音段 + 主体新闻三段拼接
4. 合成：`compose_final.py`（切点落句间停顿 + 全局调色 + BGM 铺底）→ `07_成片/最终成片.mp4`
方法论：`references/pitfalls.md`（GPU 掉线/480P 污染/合成坑）
工具：`submit_accel.py` + `extract_h3_frames.py` + `compose_final.py`
产出：`07_成片/最终成片.mp4` + verify 报告 【落盘】
自检门⑤（`verify_output.py` 九项）【agent/手工模式旁路工具——driver 不调用，质量兜底由抽帧 vision 检查与确认门④+⑤人审承担】：文件/时长（**读 enhanced.duration_est TTS 回填**）/分辨率（480/720/768/1088/1440）/三字段/风格锁（**风格关键词跨段共享**，非前缀比对）/切点不在句中/相邻段画面连贯/每段传达info_point/音频对齐（**容忍 video≥audio**，H3 生成余量；合成后 compose 已保证 merged=音频长）。用法带 `--enhanced`
确认门④：成片审核（发布前唯一闸门，auto 模式也停，不可跳过；随门附发九宫格——全段中帧拼图供人眼终审旋转，是旋转质量主防线，机器判读只是辅助信号）
**数据集入库（#148 收口，2026-10-18）**：s08 末 `run_pipeline.py` 自动把当期镜头行追加进 `references/rotation-dataset.jsonl`（经 `rotation_dataset.write_rows` 单一写口）——**追加只是落盘，不算入库**。当班 agent 在收尾前必须把这批行**提交进 git**（commit 说明写期次与判读结论；判读服务不可用就如实写「未判」，不得为干净工作树丢弃诚实记录）。校验口径：`git status references/rotation-dataset.jsonl` 无未提交改动，且当期新增行数 == 当期镜头数。测试永不读写这份活文件——夹具是 `scripts/fixtures/` 的期次冻结快照，跑器给测试子进程注入 `ROTATION_DATASET_READONLY=1`。

### 阶段9：字幕

输入：成片（`07_成片/`）+ `01_分段/口播台词/`（时间轴来自 TTS/whisper）
动作：SRT 字幕（480×864：字号 9px 描边 1.5 阴影 0.3 白字黑边，底部居中 MarginV=40，每行≤20 字语义断行，最多 5 行块高≤140px）
方法论：`references/pitfalls.md` 字幕章节
标准：禁 ass 滤镜（偏移 bug）用 `subtitles=srt`；TTS 尾静音先裁；拼接重编码
**字幕时间轴根治（2026-08-31 定稿，替代旧 whisper 猜词）**：
- 旧方案（`gen_precise_subs.py`）对 TTS wav 跑 whisper 词级时间戳 + 按字数比例摊派——但 IndexTTS 句间停顿<0.2s，whisper 无法切句边界，句子起止靠"猜"→ 字幕对不上人声（用户多次反馈）
- 新方案（`scripts/gen_subs_from_timeline.py`）：whisper 词级时间戳是**连续的、按时间顺序的**（每字发音时刻准确）→ 把**原文每个字按位置比例映射到 [t0,t1] 连续时间轴** → 句子起止 = 句首字 start、句末字 end。**时间 100% 来自 whisper 实测、文本 100% 来自原文**，零 ASR 错字零猜测
- 用法：`gen_subs_from_timeline.py <01_分段/口播台词> <02_TTS语音> <whisper缓存/wsout> <输出srt> <片头偏移s> <新闻标题映射json>`
- 断行规则：句号/逗号/顿号断，>20 字在 14-20 字范围找标点断（保护词不被拆），纯标点碎片并入前段
- 标题去重：句子==新闻标题（rstrip 句号后比对）跳过，顶部标题不重复显示
- 检测：SRT 无异常 cue（孤标点/单字符）、时间轴单调、末条≈成片时长-0.5s
产出：`08_字幕/字幕.srt` + 烧录版（可选） 【落盘】
自检门⑥：行宽≤180px / 块高≤140px / 时间轴 3+ 采样点精确 seek 抽帧比对 / 音频对齐

### 阶段10：封面（Krea2 数字迷幻流，**禁止降级为抽帧+PIL**）

> ⚠️ 用户红线（2026-09-02）：封面必须用 Krea2 数字迷幻流生成底图。抽帧+PIL 叠字（video-cover-generation）**只允许用于 Krea2 底图上的标题叠加**，禁止直接当封面底图。

> 🆕 平台定制封面（2026-09-05 用户拍板）：底图仍是横竖两张 Krea2（B站横版 / 抖音·视频号竖版，流程不变）；每平台另出一张定制封面——`gen_platform_titles.py` 按 `references/platform-title-playbook.md` 为 B站（信息摘要+搜索词，≤80字）/ 抖音（前13字钩子，≤55字，禁通报腔）/ 视频号（转发理由，≤30字）各拟标题+封面大字（6-10字，与标题互补不重复）+标签 → `overlay_cover_text.py` 按各平台 UI 安全区把大字印上底图（PIL 白字黑描边+投影）→ `封面_bilibili/douyin/shipinhao.png`。产物：`09_封面/平台标题.json`（发布阶段消费）。确认门「重生成」=重拟标题与封面字（底图不重画）。

输入：`00_口播稿/口播稿.md`（核心信息点）+ 标题/副标题
动作：
- 读稿提炼参考文本（150-280 字中文，含主体+场景+材质+色调+构图）
- 填入 Krea2 工作流 198 节点（手动输入参考入口）
- 调分辨率：B站 16:9 = SDXL_1344x768 + EmptyLatent 1536×768 / 抖音视频号 9:16 = SDXL_768x1344 + EmptyLatent 768×1344
- 转 API 图：`krea2_to_api.py`（过滤静音节点 + SetNode/GetNode 展开 + VAE 补连）
- 提交 ComfyUI：comfy-mcp `run_workflow`（裸 API 图，wait=False → job wait）
- vision 复检：色调/主体/元素/瑕疵（`scripts/cover_recheck.py --image <png> --expect "<参考文本核心句>"`，结论落盘 封面复检.json；本地判读端点，ADR 0007）
方法论：读 `references/07-cover.md`
工具：`scripts/gen_krea2_cover.py`（一键生成横竖版：自动填 198 参考文本 + 修 UNET/LoRA/loras/模式切换/GetNode 指向 + 绕开 LLM 扩写 429）或 `scripts/krea2_to_api.py` + comfy-mcp（`run_workflow` / `job`）+ 飞书发送
产出：`09_封面/海报_krea2数字迷幻_<尺寸>.png` + `海报_提示词_krea2数字迷幻.md` 【落盘】
自检门⑦：vision 复检色调符合预期 + 主体清晰 + 无变形乱码 + 元素到位
确认门⑤：封面审核（横竖版都要确认，发飞书）

**连接链路**（已持久化，见 `references/07-cover.md` 连接配置表）：
- ComfyUI `--listen 0.0.0.0`（绘世启动器自定义参数）
- Windows portproxy `0.0.0.0:18188 → 127.0.0.1:8188` + 防火墙放行 18188
- `COMFYUI_URL: http://192.168.50.231:18188`（固定局域网 IP）

**旧方案保留**：`video-cover-generation` skill（抽帧+PIL叠字）用于需精确标题文字叠加的场景，两套并存按需选。

### 阶段11：发布 + 核验

输入：成片（`07_成片/`）+ 封面（`09_封面/`）+ 标题方案
动作：
- B站（biliup-cli 1.2.1 直调，**tid=21 生活大区**——禁 232 科技科普，实测 biliup 分区被账号配置改写，默认 21 流量大区；`--cover 横版封面`）
- → 间隔 45-90s → 抖音（--headless + env -u DISPLAY + --thumbnail + --declaration "内容由AI生成"；cookie 失效（判活走服务端 `user/info`，本地 expires 不算数）用 `scripts/dy_login_win.py` 换新——Windows 真 Chrome 独立窗口扫码 → storage_state 过滤 douyin 域直写 WSL cookies（旧件自动备份）；Chrome M136 起非默认 user-data-dir 禁 `--remote-debugging-port`，必须复用 v5 的 chrome_sph_profile）
- → 间隔 45-90s → 视频号（**v5 `scripts/sph_publish_v5.py` 全自动**——CDP 真鼠标（发表按钮 scrollIntoView+真鼠标，JS click 半触发只弹窗不提交）+ 封面 + 声明原创；**2026-10-13 适配平台改版**：原创声明从拦截弹窗移到发表前页面内 checkbox，正解 = CDP 真鼠标点 `label.ant-checkbox-wrapper` 中心（点 input 本体坐标落空、antd 受控组件不认原生 setter；拦截弹窗的「声明原创」按钮已无声明语义，点击即漏声明）；核验根治假阳性：发表前独立 tab 抓列表 count 快照，发表后必须**恰好 +1** 才算 PUBLISHED_VERIFIED，否则 PUBLISHED_NOT_INCREMENTED 转人工；诊断模式 `SPH_DECL_DIAG=1`（弹窗不自动点，DOM 落盘+网络监听记真实 API）；坑见 pitfalls #50/#51；driver 完成钩子自动拉起，失败降级待发文件夹，手动补发 = `python sph_publish_v5.py <日期>`；发前想确认输入齐不齐就干跑 `python sph_publish_v5.py --check <日期>`——只解析发布信息、验成片/封面/标题/标签/飞书邻居，不碰 Chrome、不发飞书、不写 marker，退出码 0=可发 3=输入缺失 2=日期非法）
方法论：`cross-platform-publish` skill + `references/pitfalls.md` 发布踩坑
工具：`cross-platform-publish` skill 底层
核验（发布后，不能只看 exit 0）：
- B站：公开 view API `curl -s "https://api.bilibili.com/x/web-interface/view?bvid=<BV>"`（code=0 后校验 title/duration/tid=21 + 封面 sha256 比对本地；-404=仍在审核稍后重试）
- 抖音：作品管理页状态（已发布/审核中/不适宜公开）
- 视频号：**发表前后创作中心列表 `视频 (N)` count 快照必须恰好 +1**（v5 内置核验；同标题连发时"标题在场"恒真不算数——2026-10-13 假阳性 6 连根治）+ 人工复核工具 `scripts/sph_list_check.py`（connect_over_cdp 抓管理页，SPA 渲染需 25s+，复用已开 tab 优先）
产出：`10_发布/发布核验报告.md` 【落盘】
失败平台单独补发，不重跑全流程。

## 共享方法论（跨系统复用）

`references/video-craft/` 放跨系统复用的方法论（本 skill 和 OpenMontage 都用）。**2026-10-15 起已全部纳入本仓库 git 管理**，路径一律用 skill 内相对路径（不再依赖 `~/` 外部目录）：

- `personas.md` — 各环节角色提示词（编剧/导演/摄影师/翻译官/封面美术，两层叠加：身份+方法论）
- `shot-grammar.md` — 镜头语法（景别、运镜、转场、空间锚点继承）
- `camera-language.md` / `camera-movement.md` / `camera-angle-depth.md` / `camera-motion-transitions.md` — 景别决策表、运镜语义、机位角度与景深、转场
- `continuity-editing.md` / `point-of-view.md` — 连续性剪辑规则、POV 第一人称视角设计
- `katz-shot-by-shot.md` — 卡茨 9 域决策表（对话调度/移动调度/机位角度/景深）
- 书源决策表：`mckee-story-structure.md` / `truby-anatomy-of-story.md`（故事结构）、`proferes-directing-fundamentals.md` / `mamet-on-directing-film.md` / `lumet-making-movies.md`（导演方法论）
- `director-methodology.md` / `quality-modifiers.md` / `story-structure-hooks.md` / `staging-mobile-framing.md` — 导演/美术/钩子/调度
- `styles/` — 15 个风格库（视觉语言/运动语言/音频策略/负向约束/H3英文关键词）

SKILL.md 通过 context pointer 指向这些共享文件。**兼容性**：`~/references/video-craft/` 仍可用——`scripts/*.py` 的 `load_style()` 先查 skill 内路径、再回退 `~/`，两处一致时以 skill 内为准（该 home 目录已用 junction 指回本仓库，见 README「共享方法论」）。

## 工具与脚本

**脚本已复制到 skill 内 `scripts/`（自包含，不与 /home/lmr/comfy 混着管理）**。comfy 目录是旧版/开发版，skill 的 scripts/ 是正式版。改脚本改这里，改完同步 git。

- `pipeline_contract.py` — **流水线契约**：环节间只传文件强制机制（check_input 上游校验 / stamp_output 完成标记+目录hash / require_stamp 下游确认+hash一致性校验防污染 / require_stage0 阶段1闸门）。所有环节脚本必须 import。
- `run_pipeline.py` — **v3 driver（读报模式）**：全自动编排 s01-s13（音频先行双车道 + 飞书确认门④⑤ + stamps 断点续跑 + 发布核验 + s08 三轮判读闭环：本地先判→判官复判→人工指认，重烧以判官为准，台账 judge_rounds/judge_disagreements/human_flags 顶层平键，T16-T18 #128-#130）。用法/流程图/模型分配表/确认门协议见「运行方式：driver 模式」章节
- `strata_h3_lane.py` — **压缩车道核心（#57-59 立结构；#84 切 Strata）**：s04-s07 四阶段压成对本地 Strata 实例的单次 LLM 调用，直出 06_H3prompt 全量产物（整单契约/原子落盘/生成期约束门收口在脚本内；端点/模型单一事实源 STRATA_URL/STRATA_MODEL）。可独立 CLI 跑：`strata_h3_lane.py <slug> --style <名>`
- `strata_window.py` — **Strata 生命周期托管 + 显存窗口（#85）**：探活（/v1/models 200）→ powershell 跨界拉起（STRATA_START_CMD）→ 失败叫人阶梯（300s 加载窗口+预热，修复重启 ≤2 轮硬失败）；`stop`（netstat 取 PID → taskkill 树杀 → 探活离线+显存回落双确认）挂在 s08 生图前/s12 确认门前；`WINDOW_MODE` conflict/coexist 分支（ADR 0006）。原 kvmem_window.py 同票退役
- `preflight.py` — **环境护栏预检（#46/#52）**：driver 启动即执行——脚本 CRLF/bash -n（nt 下 stdin 喂内容绕盘符路径坑）、ComfyUI 可达、飞书凭证、翻页音效资产。Strata 离线不拦（#85：归 s04 入口自愈阶梯，无人值守须能自愈到底）。独立 CLI 自检：`preflight.py`
- `feishu_gate.py` — **飞书收发模块**（driver 确认门）：tenant_token 缓存 / text+image+file 发送 / 群历史消息轮询（REST 只读；`start_time/end_time` 只认 Unix 秒，返回项 `update_time` 是毫秒——单位错会让窗口起点落到未来、恒读 0 条，pitfalls #56/#T13 #125）/ 回复分类（通过/重生成/人工指认 flag/反馈——指认词命中→存证+真值回填+重烧复判不停机，T17 #129）。独立 CLI 自检：`feishu_gate.py send|image|file|poll|gate`
- `dsh_judge.py` — **判官客户端（T14 #126，S8 判读闭环第二意见）**：判官是跨侧调用：driver 起 Windows 侧 DSH 桌面宿主 headless 子进程（宿主归属与跨侧口径见 `docs/host-contract.md`；profile=`judgeprobe`，进仓在 `dsh-profile/judgeprobe/`），对本地 VLM 已判过的帧跑视觉复判，取回逐段判定（正常/翻转90/翻转180/倾斜）。`judge(rows)` 任何故障返回 ok=False 不抛异常（fail-open 采信本地）；`merge()` 四象限（本地翻+判官翻=正类重烧 / 本地正常+判官翻=抓漏报重烧 / 本地翻+判官正常=误报记账）+ `reburn_list()`；`status` 一条命令查可用性+运行配置，`selftest` 真跑一次两字符问答。跨侧铁律：帧路径经 `to_win_path()` 转 Windows 口径进 prompt，判官进程只见 Windows 路径。测试 `test_dsh_judge.py`（46 项：成功解析/超时/输出不合法/运行体缺失/跨侧口径/merge 四象限/env 容错/selftest 不抛）；driver 侧逐轮判定与分歧落台账顶层平键 judge_rounds/judge_disagreements（跨重启 list 拼接累积，T16 #128）
- `schema.py` — **契约单一事实源**：枚举（节奏/景别/Hook）、竖屏约束、各环节 JSON Schema（DIRECTOR_SHOT/PHOTOGRAPHER/HANDOFF/H3）、**CHAR_PER_SEC（语速实测 6.4）**、SCHEMA_VERSION（2.1）。所有脚本 import，禁止各自硬编码。
- `llm_json.py` — **LLM 调用层**：`llm_call_json`（流式 + finish_reason 自适应预算 + schema 强校验 + 缺字段重试 + 网络指数退避）+ `llm_call_text`（自由文本版，POV 用）。**JSON 提取用顶层括号配对**（抗推理文本污染——deepseek-v4-flash 复杂段输出大量推理文本含花括号，旧"首{到尾}"被干扰）。替代各脚本重复的 llm_call。
- `segment_script.py` — 阶段3：分段（在逐段视觉设计阶段内执行）
- `storyboard_photographer.py` — 阶段4：LLM 摄影师分镜增强（读导演设计 JSON + 交接摘要 → 执行字段 + `llm_json`/`schema.PHOTOGRAPHER_SCHEMA` 强校验 + **逐镜并行 `--concurrency` + 增量落盘断点续跑**）
- `storyboard_enhancer.py` — 阶段4：七项自检闸门（校验摄影师产出，枚举收敛 schema）
- `director_llm.py` — 阶段2/3：LLM 导演设计（`--directions` 创意方向卡 + 整体方案 / 逐段设计 JSON + 契约；**逐段设计逐镜并行 `--concurrency` + 线程安全落盘断点续跑**；`--style <名>` 注入风格块）
- `gen_handoff.py` — 阶段6前置：生成交接卡（读 shotXX.json + enhanced.json + 交接摘要 → `交接卡/shotNN.json`，HANDOFF_SCHEMA 强校验，阶段6 读此替代整体方案全文）
- `art_director.py` — 阶段5：美术画室（读 02+03+01+style → `05_美术画质/画质设计.json`，每段 光质/材质/景深/负面词，`llm_json`+`ART_SCHEMA` 强校验 + 并行）
- `director_to_h3_llm.py` — 阶段6：H3 prompt **汇聚转译**（交接卡 + 导演 JSON + 摄影师执行字段 + 美术画质 + 风格块 → 五路合一；`llm_json`+`H3_SCHEMA` 强校验 + 并行 `--concurrency`；`--reference` 模式适配 T2VA/I2VA/FL2VA/L2VA/Ref2VA）
- `pov_scene_llm.py` / `merge_pov_scenes.py` — 阶段6辅助
- `ui2api.py` — UI 工作流 → API 图转换器（submit_accel 硬依赖，2026-09-03 迁入 skill/scripts；**2026-10-13 起按 `__file__` 同目录导入——hermes 与 /home/lmr/comfy 的旧副本视为过期快照，勿改勿用**；fix_models 含 bypass 直通重接（多跳收敛）+ 主链完整性校验，pitfalls #52）
- `new_daily.py` — 读报单开工：从 /mnt/e/每日新中国/<日期>/ 一键建 slug + 带阶段1指纹（来源头/URL/验证表）的口播稿，打印 driver 启动命令（根治每日手工补头）
- `rotation_dataset.py` — 旋转跨期数据集：s08 末自动追加每段特征+vision 本地结论（vision_verdict 永不被覆盖）+判官复判 judge_* 字段（人机结论分字段、两条独立证据链，T16 #128）；**写口唯一**（#148）：`write_rows` 是唯一落盘口，`append_records`/`set_user_flag`/t15 回写都经它；`ROTATION_DATASET_READONLY=1` 时写活数据集抛 `DatasetReadOnlyError`（跑器给测试子进程强制注入，显式传 tempfile 路径不受限）；四类素材证据桶 `load_evidence_buckets`（1-confirmed/2-judge-found/3-unfixed/4-human-flag 人审正类；manifest 为账，T18 #130 起三桶、2026-10-22 ADR 0008 扩第四类；`evidence_frames` 行内元素为 `{frame,sha256,round,category}` 并兼容纯字符串存量）；`set_user_flag` 人工真值独立回填（source=human-flag，T17 #129）；`stats` 一条命令出判定数+场景数（白名单/换岗门槛决策依据）
- `gen_platform_titles.py` — 阶段10 平台定制：按 platform-title-playbook 为 B站/抖音/视频号各拟标题+封面大字+标签 → `09_封面/平台标题.json`（发布与确认门消费）
- `overlay_cover_text.py` — 阶段10 封面叠字：PIL 把各平台封面大字印上 Krea2 底图（按平台 UI 安全区排版，白字黑描边+投影）
- `gen_krea2_cover.py` — 阶段10 封面：一键生成横竖版 krea2 数字迷幻封面（自动填 198 参考文本 + 修 UNET 子目录/LoRA(1)后缀/loras 字段/模式切换/GetNode 指向 + 绕开 PromptExpand LLM 扩写 429；HERE 自包含读同目录 krea2_to_api）
- `submit_accel.py` — 阶段8：串行生成（`--script <稿子> --res <清晰度>` 输出到 稿子/07_成片/<清晰度>/，如 1080P/；不带 --script 落旧全局 480P 会跨项目污染）
- `compose_final.py` — 阶段8：合成（`--intro 片头 --newspaper 报纸图 --welcome-audio 欢迎词` 三段装配 + BGM 0.15）
- `extract_h3_frames.py` — 阶段8：H3 生成后抽帧（竖屏翻转 vision 检查前置，P0）
- `vision_server.py` / `vision-server-setup.sh` — 本地判读服务生命周期（probe/ensure/stop/url；供给=pin llama.cpp b11603 CPU 构建 + 三档 Qwen3-VL Q4_K_M+mmproj，断点续传+停滞自愈+LFS sha256 校验；**不开机自启，driver s08 入口自愈拉起**，ADR 0007）
- `vision_smoke.py` — 判读服务一键冒烟（探活/单帧判读/延迟，退出码区分服务/模型/逻辑问题）
- `vision_blindbench.py` — 盲比定档（三档 vs 存量 glm 结论，判据=翻转 0 漏检+一致率≥98% 取最小达标档；报告落 docs/spikes/）
- `newspaper_date_check.py` — 报纸日期三合一校验（9:00 报纸任务选图用；印刷=当日=稿件，不一致 exit 2 拦截）
- `cover_recheck.py` — Krea2 封面 vision 复检（自检门⑦机器预检，结论落盘 封面复检.json 供确认门⑤人审）
- `clip_probe.py` / `reconcile.py` — CLIP 线性探针推理封装 + VLM×CLIP 合成纯函数（T7 训练产出探针前层缺席=单路 VLM）
- `verify_output.py` — 阶段8：验收（九项 + 契约；`--enhanced` 时长用 TTS 回填值，风格锁=关键词共享，分辨率含 1088）。**agent/手工模式旁路工具，driver 不调用**（质量兜底已由抽帧 vision + 确认门④+⑤接管；对新风格误报见 pitfalls #19）
- `tts_batch.py` — 阶段7：Index-TTS-2.5 批量配音（读 `01_分段/口播台词/`）。**默认按 E2 模仿标准配方**（本人音色+目标情绪参考 weight 0.5+语速 0.88+收紧采样），`--no-passion` 关闭
- `sync_tts_durations.py` — 阶段7 收尾【agent/full 模式遗留，driver 无此循环】：TTS 实测时长回填（duration_est + 秒级指令缩放 + video_seconds 同步；默认 dry-run，`--write` 写回）。消除语速预估偏差 → 防 compose -shortest 裁视频尾部动作。回填后需重跑阶段6
- `tts_sentence_timeline.py` — 阶段6可选：按句生成 TTS + 句子级时间轴（IndexTTS batch concat keep-temp，ffprobe 实测每句时长 → 零 whisper 时间轴；`--sentence-gap-ms` 句间静音）
- `gen_subs_from_timeline.py` — 阶段8（**字幕时间轴根治，2026-08-31 定稿**）：whisper 词级时间戳映射原文 → 句子起止=句首字start/句末字end，零 ASR 错字。用法见 pitfalls #34
- `gen_precise_subs.py` — 阶段8（旧方案，仅兜底）：精确字幕（whisper 词级时间戳 + 原稿文本映射 + 语义断句 + 标题去重 + 段长=音频全长）→ `08_字幕/字幕_口播_精确.srt`
- `check_portrait_orientation.py` — 阶段6自检③b：竖屏方向约束检测（缺约束 FAIL / 环形环绕风险词 WARN），见 pitfalls #32
- `check_overwrite.py` — 重构期防覆盖校验（9 文件 hash 快照，`init`/`check`，确认未被并行任务覆盖；S6 后职责并入 pipeline_contract）
- `sph_publish_v5.py` — 阶段11 视频号自动发表**（当前正牌）**：CDP 真鼠标（发表按钮 scrollIntoView+真鼠标）+ 封面 + 原创声明 checkbox（点 `label.ant-checkbox-wrapper` 中心）+ count 快照核验（发表后恰好 +1）+ `SPH_DECL_DIAG=1` 人工诊断模式。前置：`restart_sph_chrome.ps1` 拉起带登录态的专用 Chrome（微信登录是内存态，杀 Chrome 即丢）。**位置自解析（#152）**：脚本按自身位置找 `feishu_gate.py` 等同仓邻居，不再依赖任何写死的安装路径，从哪份副本跑都是同一份；发前干跑 `--check <日期>` 可零副作用验输入（0=可发 / 3=输入缺失 / 2=日期非法）。坑见 pitfalls #50/#51/#60
- `sph_list_check.py` — 视频号管理页列表核验工具（connect_over_cdp + ensure_chrome；SPA 渲染 25s+，复用已开 tab；2026-10-13 排障沉淀）
- `sph_publish.py` — 旧版视频号发表/核验（被 v5 替代，仅存档；三实证坑见 pitfalls #50）
- `dy_login_win.py` — 抖音 cookie 换新（Windows 真 Chrome 扫码 → playwright connect_over_cdp 服务端判活 → storage_state 过滤 douyin 域经 \wsl.localhost 直写 WSL cookies，旧件自动备份；Chrome M136 起非默认 user-data-dir 禁 CDP 9222，必须复用 chrome_sph_profile）
- `test_storyboard_enhancer.py` / `test_verify_output.py` — 测试
- `make_platform_covers.py` — 阶段9：封面（在 video-cover-generation skill 内）
- `krea2_to_api.py` — 阶段9：Krea2 工作流 → ComfyUI API 图转换（198填参考文本 + 140/18分辨率 + 过滤静音节点 + SetNode/GetNode展开 + VAE补连 + **194 category 修正 + 185 LoraManager loras `{"__value__": 字典数组}` 处理**）；提交走 comfy-mcp `run_workflow`（node_errors 空 + outputs 非空才算成）

**运行方式**：`python3 <skill>/scripts/<脚本>.py <输入> <输出> [参数]`。脚本内的 `sys.path.insert` 已自动包含脚本所在目录（找 pipeline_contract 时无需加 PYTHONPATH）。

## 角色提示词（Personas）

各环节 LLM 调用的角色提示词在 `references/video-craft/personas.md`。两层叠加：身份层（角色投射）+ 方法论注入层（读对应方法论文件）。

风格库在 `references/video-craft/styles/`（15 个风格；`~/references/video-craft/styles/` 为兼容路径，经 junction 指向同一份），常用 7 个：
- paper-collage（纸质科普，默认）/ papercraft-stop-motion / 3d-animation / handdrawn-live / minimalist-product / infographic / realistic
- 每个含：视觉语言/运动语言/音频策略/负向约束/H3英文关键词
- **注入机制**：`director_llm.py` / `director_to_h3_llm.py` / `storyboard_photographer.py` 传 `--style <名>`，脚本读 styles/<名>.md 全文填入 system prompt 的 `{STYLE_BLOCK}` 占位——**加新风格只需加一个 styles/<名>.md，零代码改动**

## 固定片头 + 字幕规则（2026-08-29 定稿，所有《每日新中国》读报单必守）

**片头（每次都要，放最前）**：
1. 片头视频：`/mnt/e/每日新中国/每日新中国片头.mp4`（1080×1944，5.2s，永远放全片最前）
2. 片头配音：用户音色 `expressive-reference.wav` 生成 "欢迎来到每日新中国正面新闻简报，今天是（当日日期）以下是今日的主要内容"（日期=当日）
3. 说这句话时，画面用当日《每日新中国》报纸图（`/mnt/e/每日新中国/<日期>/<日期>_每日新中国_第N期.png`）
   - **报纸日期三合一校验**：报纸图上印刷的日期 = 当天日期 = 新闻稿日期，三者必须一致；选图前必须 vision 复核报纸上的印刷日期（不能只看文件名）——`python scripts/newspaper_date_check.py --image <报纸png> [--out slug/00_口播稿/报纸日期校验.json]`，不一致 exit 2 拦截（本地判读端点，ADR 0007）
4. 片头之后接入新闻主体视频
5. **BGM 规则**：片头视频段（纯片头，5.2s）不加 BGM（保留原片头音轨）；**从"欢迎来到每日新中国…"报纸配音段起加 BGM**，贯穿报纸段+主体新闻段全程（BGM 固定音量 0.15，amix normalize=0，fade in/out，人声主导——量化判据：混合后 mean - 人声 mean ≤2dB）

**字幕格式（每条新闻固定）**：
- j1. 画面最上方：该条新闻的标题字幕（当条新闻内固定不变）
- j2. 画面最下方：根据语音生成的口播字幕（逐句更新）
- j(a) 切到下一条新闻时，上方标题才切换
- j(b) 还在当条新闻内，上方标题不变

## 《每日新中国》读报成片标准实施方案（2026-08-29 定稿，全流程验证通过）

> 本节是《每日新中国》读报单（直接读报模式）从 TTS 到成片的**标准操作 + 检查点**，每单必守。流程：片头 → 报纸配音段 → 26 段新闻 → 字幕 → 发布。

### 片头装配（固定模板，每次复用）

1. **片头视频段**：`/mnt/e/每日新中国/每日新中国片头.mp4`（1080×1944, 5.2s）→ 缩放到 480×864@30fps（`scale=480:864:force_original_aspect_ratio=increase,crop=480:864,fps=30`）
2. **报纸配音段**：当日报纸图（三合一校验后）+ 用户音色配音（"欢迎来到每日新中国正面新闻简报，今天是（当日日期）以下是今日的主要内容"）→ 缩放 480×864（报纸图 `scale=480:864`）
3. **拼接**：`filter_complex concat` 三段（片头视频 → 报纸段 → 主体新闻），非 `-c copy` concat（不同编码参数会断）
4. **检查点①**：成片总时长 = 5.2 + 报纸段 + Σ(新闻音频时长)，误差 <0.5s

### BGM：固定音量铺底（2026-08-29 定稿，v8 验证通过）

> ⚠️ 2026-08-29 推翻 sidechain ducking 方案（实测不稳定）。定稿方案：**固定音量 0.15 + amix normalize=0 + fade 出入**。人声/混合音量差全部 ≤1.4dB（人声主导），用户验收通过。

- **规则**：片头视频段无 BGM（保留原片头音轨）；报纸段起 BGM 全程贯穿
- **方法**（固定音量，不用 sidechain——sidechain 在短句段会把 BGM 压没到 -40dB，且参数难调）：
  ```
  ffmpeg -i 片头段 -i 报纸段 -i 主体 -i BGM \
    -filter_complex "[2:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,settb=AVTB[mainv];\
    [3:a]volume=0.15,aloop=loop=-1:size=2e9,atrim=0:132,asetpts=N/SR/TB,afade=t=in:st=0:d=2,afade=t=out:st=129:d=3,asplit=2[bgm1][bgm2];\
    [1:a][bgm1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[np];\
    [2:a][bgm2]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mn];\
    [0:v][0:a][1:v][np][mainv][mn]concat=n=3:v=1:a=1[outv][outa]" \
    -map "[outv]" -map "[outa]" -c:v libx264 -preset fast -crf 24 -c:a aac -b:a 160k
  ```
  **关键参数**：
  - `volume=0.15` — BGM 铺底音量（竖版短视频安全值；0.3 会盖人声 +2~4.5dB，0.15 只 +1~1.4dB）
  - `amix normalize=0` — 关自动归一（normalize=1 会把 BGM 抬到人声电平）
  - `afade in 2s / out 3s` — 避免 BGM 突兀出现/消失
  - `atrim=0:132 + asetpts` — BGM 裁到成片时长，防循环接续卡顿
  - `asplit=2` — 同一 BGM 源分两路（报纸段+主体段各一路），否则 ffmpeg 报 "Invalid stream specifier"
  - 片头段用 `[0:a]` 直接 concat（**保留原片头音轨**，不要 -an 丢音频）
- 之后 `loudnorm=I=-16:TP=-1.5:LRA=11` 响度归一
- **检查点②（量化验收，必须做）**：
  ```
  # 对 3 个代表段（中段/高潮/尾部）分别测纯人声主体 vs 混合后成片的 mean_volume
  ffmpeg -ss <主体时间> -t 10 -i 主体_无BGM.mp4 -af volumedetect -f null -   # 人声
  ffmpeg -ss <成片时间> -t 10 -i 成片.mp4 -af volumedetect -f null -        # 混合
  # 判据：混合后 mean - 人声 mean ≤ +2dB（v8 实测 1.1~1.4dB ✅）；>+3dB 必须降 BGM
  ```
  - 注意：主体时间 = 成片时间 - 片头偏移（5.1 + 报纸段 7.99 = 13.09s）
  - 片头段应有人声/原音轨声（volumedetect 不接近 -91dB）；报纸段 BGM 起但人声主导

### 字幕：精确对齐 + 语义断句 + 去重（`gen_precise_subs.py`）

**核心**（一次性根治"字幕不贴合语音/断句怪/后段漂移"）：
1. **时间戳来源**：whisper `--word_timestamps True` 对 TTS 音频做词级对齐（缓存 `/tmp/wsout/{sid}.json`，复用不重跑）
2. **文本来源**：原文字稿 `01_分段/口播台词/shotXX.md`（不用 whisper 转写，避免错字）
3. **合成**：时间戳按字数比例映射到原稿 → SRT
4. **段长 = 音频全长**（ffprobe 实测，含尾部静音）——**不是** whisper 语音长！否则每段漏 0.1-0.3s，26 段累计漂移 5.7s（这是"后段字幕比语音慢"的根因）
5. **语义断句**：句号/问号/感叹号/分号断句 → 超 20 字在逗号/顿号/冒号/虚词（的/了/中）断 → 仍超则硬切但**数字保护**（"6.78万亿"不拆，断点前后数字/单位回退）；含 `丨` 的标题行先在 `丨` 处断
6. **去重**：口播句子 == 新闻标题时跳过（顶部标题已显示，底部不重复）——否则 6 条新闻底部字幕和顶部标题显示同一句话，观感像"字幕下方加了标题"
7. **时间轴基准**：主体起始 = 12.56s（片头 5.2 + 报纸段 7.36），逐段累加音频全长

**烧录**（两步，禁一步双滤镜）：
- 顶部标题：`drawtext`（`fontsize=18, y=20, x=(w-text_w)/2`，`enable='between(t,start,end)'` 按新闻时间窗）——**不用 subtitles/ass**（libass SRT 序号必须纯数字，`T1` 不识别；Alignment 参数不可靠，画到中间）
- 底部口播：`subtitles=/tmp/sub_precise.srt:force_style='Alignment=2,MarginV=40,FontSize=9'`（SRT 复制到 ASCII 路径 `/tmp/`，中文路径 ffmpeg 打不开）

**检查点③（自检）**：
- SRT 时间轴：首条 = 12.56s，末条 ≈ 成片总时长 - 0.5s（183.0 vs 183.7 合格）
- 无重叠 cue（monotonic 检查）
- vision 抽 2-3 采样点（含尾部 181s）：底部字幕内容 = 该处语音内容，且不含顶部标题重复
- 尾部采样必须验证（drift 最容易在尾部暴露）

### 合成顺序（最终版）

> 【agent/full 模式遗留——driver 模式由 compose_final（读报三段装配+翻页过渡+BGM 0.15）+ driver 烧录一步到位，本节仅供人工接管时参考。其中 sidechain ducking 已于 2026-08-29 推翻，定稿为固定音量 0.15 + amix normalize=0】

TTS → 段时长 = 音频全长 → compose_final 合并（-shortest）→ 片头三合一 + sidechain BGM → loudnorm → 烧顶部标题 drawtext → 烧底部口播 subtitles → 裁齐总时长 → 压缩(CRF28) → 发飞书审核 → 确认后封面+发布

## 读报模式·音频先行时序（2026-09-03 定稿）

分段（按新闻条目，est 初算）→ **TTS 提前**（实测每条真实时长）→ 按 TTS 实测时长设计镜头/段落（不再依赖 6.4字/s 预估+回填重跑循环）→ 设计+H3（压缩车道：s04 单次 Strata 调用直出；legacy 四阶段=导演/摄影/美术→H3）→ 合成（**新闻间 1s 黑屏静音垫片**，`SEG_GAP_S` 环境变量可调）→ 字幕 → 发布。

- 垫片由 compose_final 自动生成插入（同分辨率/帧率/采样率/声道严格对齐段参数，否则 concat -c copy 时间戳错乱时长翻倍）
- 垫片作用：新闻边界呼吸感 + 顶部标题自然消失 + 下条新闻黑场进入
- main_dur 以 _main_src.mp4 实测为准（含垫片），不能从 成片_无BGM 反推
- _main_bgm 视频重编码 30fps（H3 原生 24fps，与 intro/news 30fps 对齐，混帧率会让 concat 时间轴错位）
- _news_bgm/_main_bgm 音频统一 `-ar 44100 -ac 2`（与片头一致；混流 22050/1ch 会时长漂移）

## 踩坑记录

> 仅保留 3 条 P0 必犯级；其余全部踩坑（GPU/发布/合成/字幕/封面/竖屏等，含根因/修复/检测点）统一收敛在 `references/pitfalls.md`，排障先查该文件。

### 🚨 H3_MARKER 指纹必须选"结构必有"的串，禁用风格词（2026-09-03 P0）

**症状**：非纸质科普风格（如天宫）跑 submit_accel，每段 `[OVERRIDE_CHECK] 194 prompt 覆盖校验失败` 重试 3 次全 FAIL，脚本 exit 0 但 0 产出（静默失败）。

**根因**：T08 覆盖校验的指纹默认值曾是 `paper collage`——纸质科普时代每条 prompt 必含的风格词。换风格后 prompt 不含该词，校验把"覆盖成功"误判为"覆盖失败"在提交前拦截。且 POV/t2v 段的风格词（celestial 等）是否被 LLM 写入 prompt 不可控，换 `H3_MARKER=celestial` 仍会漏杀部分段。

**修复**：默认指纹改为 `integrated_multimodal_description`（转译脚本固定写入的字段头，所有 prompt 结构上必有），换任何风格无需再传环境变量。**原则：校验指纹永远选结构性必有的串（字段头/硬约束句如 off-screen voiceover），永不选内容性词（风格词/主题词）。**

### 🚨 ffmpeg concat 相对路径双重嵌套（2026-09-03 P0）

**症状**：`compose_final.py 07_成片/...`（output_dir 传相对路径）时报 `07_成片/07_成片/_merged/_intro.mp4 not found`。

**根因**：`ffmpeg -f concat` 的 list 文件条目路径是**相对 list 文件所在目录**解析的，而脚本写入的条目是相对 CWD 的路径 → 被再叠加一次 list 目录。

**修复**：所有 concat list 条目（_concat.txt/_main_shots.txt/_seg_concat.txt）统一 `os.path.abspath()` 转绝对路径后再写入。

### 🚨 逐段合成必须显式 -map，否则人声全丢（2026-08-29 P0 级）

**症状**：成片 13s 后（主体段）人声完全消失，只有画面。whisper 转录成片主体 = 乱码/空，但各段独立转录 = 正常 TTS 人声。

**根因**：合成逐段视频的命令 `ffmpeg -i 视频段 -i TTS音频 -c:a aac -shortest` **没加 `-map`**。H3 工作流输出视频**自带音轨**（`*-audio.mp4`），ffmpeg 默认选第一个音频流 = H3 自带音轨（静音/环境音），**TTS 人声完全没用上**。

**修复**：逐段合成必须显式 `-map 0:v -map 1:a`（视频取输入0，音频取输入1=TTS）：
```
ffmpeg -y -i <H3视频> -i <TTS.wav> -map 0:v -map 1:a \
  -vf fps=30 -c:v libx264 -preset fast -crf 20 \
  -c:a aac -b:a 128k -shortest -pix_fmt yuv420p <out.mp4>
```

**验证**：合成后必须 whisper 抽检主体开头 15-35s（转录应含真实新闻内容），不能只看 volumedetect（H3 自带音轨也有音量）。
