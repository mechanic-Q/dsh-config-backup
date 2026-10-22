# 宿主契约：谁在哪跑、用哪套语法、产物落在哪

> **本文是唯一事实源。** 文末的机读声明块是唯一可被程序读取的声明，正文五张表（实测数据、阶段、shell、产物目录、跨侧服务）与它逐格一一对应。改任何一格都必须同时改声明块，否则 `scripts/test_host_contract.py` 会当场抓出漂移。解析入口只有一个：`scripts/host_contract.py`。

## 为什么要有这份文档

在 #144 之前，全仓没有一处说明 **driver 必须在哪个宿主跑**：6 处文档各说各话（一处说 9:00 报纸是 Windows 原生、另一处还写着 WSL 的 `run_all.sh`），agent 把 Git Bash（MSYS）当成 WSL 用，报错时谁也说不清「这条命令本该在哪跑」。本文件把口径收成一份：AGENTS.md 与 SKILL.md 只留指针，阶段命令、shell 用途、产物目录一律以这里为准。

## 当前执行宿主（`windows`）

#151 迁移完成后 driver 是 `scripts/run_pipeline.py`，在 Windows 原生 python 下逐阶段执行 s01-s13：本仓 python 子进程全部走 argv 直调（run_argv），TTS/whisper/biliup/sau 四个 WSL 独占依赖经单点 wsl.exe 调用。本声明的 `current` 不依赖任何探测器，探测器只用于守卫比对声明（#145）。

> Windows 才是硬件与服务的所在侧：迁移后阶段命令原生执行，少一层 interop 与 /mnt 路径翻译；#150 实测 7 个重依赖里 6 个有 Windows 原生实现，只剩 index-tts、whisper、biliup、sau 等 WSL 侧依赖，它们各自是单点调用，可以经一条非嵌套 wsl.exe 走通。

### 实测数据（当前宿主的理由）

| # | 实测 | 出处 |
|---|---|---|
| 1 | 7 个重依赖里 6 个有 Windows 原生实现；WSL 独占的只有 index-tts、whisper、fc-match 三个轻量 CLI | #150 |
| 2 | driver 与 s01-s13 阶段命令全部由 subprocess.run(shell=True) 走 WSL bash；#144 实测 54 处子进程里 42 处没写编码、49 处 /mnt/ 路径、9 个文件盘符硬编码 | #144 |
| 3 | ComfyUI 在 Windows 192.168.50.231:18188，每次提交都必须经 powershell.exe 跨侧调用 | #144 |
| 4 | Strata 常驻 Windows 主机 8080，WSL 侧只能经默认网关 IP 访问 | docs/adr/0006-strata-backend-and-window.md |
| 5 | 判官 llama-server 在 Windows 8100，DSH 桌面宿主与发布用 Chrome 也都在 Windows 侧 | docs/adr/0007-vision-localization-cpu-resident.md |
| 6 | pitfalls 记录：Windows 侧 driver 用非嵌套 wsl.exe 拉起实测存活，迁移不必担心父会话退出连坐进程组 | references/pitfalls.md:471-479 |

## 三个 shell：用途与禁区

| shell | 用途（一句） | 禁区（一句） |
|---|---|---|
| `wsl-bash` | WSL 服务调用面：driver 经单点 wsl.exe 进 WSL 跑 index-tts/whisper/biliup/sau | 禁止在 Windows 原生进程里直接假定它是可用 shell；跨侧调用必须显式写 wsl.exe |
| `git-bash` | 仅供人在 Windows 上手敲 git/gh 与仓库日常命令 | 禁止用 Git Bash 跑 pipeline 或任何阶段命令：它是 MSYS 不是 WSL，没有 /mnt 与 Linux 进程语义 |
| `windows-native` | 当前执行宿主的正式执行面：阶段命令由 Windows python 与 powershell/cmd 原生执行 | 禁止用 cmd 内建命令替代 POSIX 语义；需要 bash 语义时只能经单点 wsl.exe 进 WSL |

补充三条口径：python 子进程一律 argv 直调（`run_argv`，不经 shell）；`wsl.exe -d Ubuntu` 是从 Windows 进 WSL 的唯一标准口（单层、非嵌套）；`cmd /s /c` 只用于需要 `cmd` 解析 `>` 重定向的脱离启动场景。

## 13 个阶段

| 阶段 | 名称 | 跨侧依赖（WSL 侧服务） | 语法 | 产物目录 |
|---|---|---|---|---|
| `s01` | 分段 + 口播台词 | 无 | `python` | `01_分段` |
| `s02` | TTS 音频（欢迎词 + 正文） | `index-tts` | `python`、`wsl.exe` | `02_TTS语音` |
| `s03` | TTS 时长回填 | 无 | `python` | `01_分段` |
| `s04` | 设计与 H3 prompt（compressed 单次直出） | 无 | `python` | `03_导演设计`、`06_H3prompt`、`00_口播稿` |
| `s05` | 摄影师分镜（legacy） | 无 | `python` | `04_分镜增强` |
| `s06` | 美术画质（legacy） | 无 | `python` | `05_美术画质` |
| `s07` | H3 prompt（legacy） | 无 | `python` | `06_H3prompt` |
| `s08` | H3 生成 + 抽帧竖屏检查 | `whisper` | `python`、`powershell`、`wmi`、`wsl.exe` | `07_成片`、`_wsout` |
| `s09` | 读报装配 | 无 | `python` | `07_成片`、`08_字幕` |
| `s10` | 字幕与烧录 | 无 | `python` | `07_成片`、`08_字幕` |
| `s11` | Krea2 封面 | 无 | `python`、`powershell` | `09_封面` |
| `s12` | 确认门 ④⑤（飞书） | 无 | `python` | 无 |
| `s13` | 发布 | `biliup`、`sau` | `python`、`wsl.exe`、`cmd`、`wmi`、`powershell` | `10_发布` |

阶段间只传文件：输入读上游目录、输出写本阶段列出的产物目录，路径一律相对 `$SLUG`（定义见下一节末尾）；进 WSL 侧命令的参数路径先转 /mnt 口径（`win_to_wsl`），进判官 prompt 的帧路径先转 Windows 口径。

语法标记含义：`python` = 直接调 python 解释器或进程内执行（Windows 原生）；`powershell` = `powershell.exe -NoProfile -Command`；`cmd` = `cmd /s /c`；`wsl.exe` = 从 Windows 侧 `wsl -d Ubuntu` 的单点调用；`wmi` = WMI `Win32_Process.Create` 脱离启动。

车道提醒：compressed 车道 s04 单次 Strata 调用直出 `06_H3prompt`，s05-s07 只写虚拟 stamp；legacy 车道才真跑这三段。Strata 窗口纪律不变：s04 入口 ensure_online，s08 前停，s11 前再拉，s12 前停。

## 产物目录契约

| 目录 | 路径口径（Windows 宿主） | 谁写 | 谁读 | 能否清理 |
|---|---|---|---|---|
| `00_口播稿` | `$SLUG\00_口播稿` | `new_daily.py`、`s04` | `s01`、`s04`、`s11` | ❌ 输入锚点与方向锁，永不清理 |
| `01_分段` | `$SLUG\01_分段` | `s01`、`s03` | `s03`、`s08`、`s10` | ✅ 下游已产出后可按期删，重跑会重建 |
| `02_TTS语音` | `$SLUG\02_TTS语音` | `s02` | `s03`、`s09`、`s10` | ✅ 成片确认后可删，重跑会重合成 |
| `03_导演设计` | `$SLUG\03_导演设计` | `s04` | `s05`、`s06`、`s07` | ✅ legacy 车道中间件，compressed 车道不产出，可删 |
| `04_分镜增强` | `$SLUG\04_分镜增强` | `s05` | `s06`、`s07` | ✅ legacy 车道中间件，可删 |
| `05_美术画质` | `$SLUG\05_美术画质` | `s06` | `s07` | ✅ legacy 车道中间件，可删 |
| `06_H3prompt` | `$SLUG\06_H3prompt` | `s04`、`s07` | `s08` | ❌ s08 的输入与返工依据，保留到发布完成 |
| `07_成片` | `$SLUG\07_成片` | `s08`、`s09`、`s10` | `s09`、`s10`、`s12`、`s13` | ✅ 发布成功后可按期删，最终成片与核验报告留档到确认前 |
| `08_字幕` | `$SLUG\08_字幕` | `s09`、`s10` | `s10` | ✅ 烧录完成后可删，s10 可从时间轴重建 |
| `09_封面` | `$SLUG\09_封面` | `s11` | `s12`、`s13` | ❌ 平台标题与各平台封面都是发布输入，发布完成后才可删 |
| `10_发布` | `$SLUG\10_发布` | `s13` | `s13`、`sph_publish_v5.py` | ❌ 待发视频号与核验报告在视频号发布完成前不可删 |
| `_driver` | `$SLUG\_driver` | `driver` | `driver` | ✅ 删哪个阶段的 stamp 就重跑哪个阶段，排障时专删 |
| `_wsout` | `$SLUG\_wsout` | `s08` | `s10` | ✅ whisper 词级缓存，删掉后 s10 会等待重算 |
| `DATE_ROOT` | `E:\每日新中国` | `newspaper-cron`、`sph_publish_v5.py` | `s09` | ❌ 报纸与概述是 s09 输入，不删；logs 可按日期清理 |
| `COMFYUI_OUT` | `E:\AI\Comfyui-WF-2026.8.8\Comfyui-WF-2026.8.8\ComfyUI\output` | `comfyui` | `s11` | ✅ 拷进 09_封面 后可删，建议留最近 7 天便于返工 |
| `KREA2_ROOT` | `E:\AI\Krea2资源\数字迷幻工作流、规则、插件、例图\工作流` | `human` | `s11` | ❌ 工作流与规则是手工维护的唯一副本，禁删 |
| `TEMP` | `C:\Users\LMR\AppData\Local\Temp` | `s11`、`comfyui_selfheal.py` | `s11`、`comfyui_selfheal.py` | ✅ object_info.json、krea2_*.json、launch ps1 都是每次重建的临时件，可删 |
| `SAU_ROOT` | `\\wsl.localhost\Ubuntu\home\lmr\social-auto-upload` | `s13` | `s13` | ❌ cookie 与登录态是唯一副本，禁删 |

`$SLUG` 指稿子旁的 slug 项目目录：`E:\每日新中国\<日期>\<slug>`。**项目位置铁律：项目建在稿子旁边，严禁建到 `/home/lmr` 或任何 WSL 家目录。** 目录树本身见 SKILL.md 文件管理一节，这里只登记「谁写、谁读、能不能清」。

## 跨侧服务

| 服务 | 运行侧 | 端点／位置 | 用途 | 访问方式 |
|---|---|---|---|---|
| `comfyui` | `windows` | 192.168.50.231:18188（Windows 主机 ComfyUI，GPU 串行） | s08 H3 视频生成、s11 Krea2 封面出图 | HTTP 提交 workflow；driver 迁移后本机直连（env 可覆盖端点） |
| `strata` | `windows` | Windows 主机 8080（Strata vLLM 服务） | s01/s04-s08 的本地 LLM 推理：分段、设计、H3 prompt、文本重写 | OpenAI 兼容 /v1；Windows 本机直连 127.0.0.1 |
| `llama-server` | `windows` | Windows 主机 8100（Vision-judge llama-server） | s08 抽帧竖屏判读的本地 qwen3-vl-4b | HTTP /v1/chat/completions；地址由 vision_server.py 经 powershell.exe 双端解析 |
| `dsh-judge` | `windows` | E:\AI\deepseek-harness\DeepSeek Harness.exe 与 dsh-profile/judgeprobe | s08 关键帧的第二意见复判 | interop 起 Windows 桌面宿主 headless 子进程；帧路径先 to_win_path() 转 Windows 口径 |
| `index-tts` | `wsl` | WSL 本地 GPU 上的 Index-TTS-2.5 服务 | s02 欢迎词与正文口播配音 | tts_batch.py 直连本地服务，目前只有 WSL 侧有这套依赖 |
| `whisper` | `wsl` | WSL 内 whisper CLI（turbo，词级时间戳） | s08 并行词级时间戳、s10 字幕时间轴 | 子进程调用，输出 JSON 落 _wsout |
| `biliup` | `wsl` | /home/lmr/social-auto-upload/tools/biliup/linux-x86_64/biliup | s13 B 站投稿（以 bv 号为成功判据） | CLI + cookie 文件，目前只有 WSL 版二进制 |
| `sau` | `wsl` | /home/lmr/social-auto-upload/.venv/bin/sau | s13 抖音/快手/头条投稿 | CLI headless，cwd=SAU_DIR，env -u DISPLAY |
| `chrome-cdp` | `windows` | 127.0.0.1:9222（chrome_sph_profile 调试端口） | s13 视频号发布器驱动本机 Chrome | CDP HTTP；Chrome 由 powershell Start-Process 拉起 |
| `wmi-detach` | `windows` | WMI Win32_Process.Create（comfyui_selfheal.launch_detached） | s13 视频号发布器脱离启动、ComfyUI 无头直启 | WMI 建独立进程，driver 会话退出不影响 |
| `dsh-creds` | `windows` | C:\Users\LMR\.dsh\dsh-im-connect\channels.json | s12 飞书确认门读群凭据 | Windows 侧直接读 C:\Users\LMR\.dsh\dsh-im-connect\channels.json（env > ~/.dsh > 候选路径） |
| `newspaper-files` | `windows` | E:\每日新中国\<日期>\3新闻_概述.md 与报纸 PNG | s09 读报装配的报纸页与当期概述 | Windows 侧直读 E:\每日新中国；9:00 报纸管道为 Windows 原生 |

「跨侧」指 driver 所在侧与服务所在侧不同：#151 迁移后 driver 在 Windows，跨侧的只剩 index-tts、whisper、biliup、sau 四个 WSL 服务，全部经单点 wsl.exe 调用。

## Windows 侧两条定时链路


这两条链路的入口语句原本散在 SKILL.md 的「每日无人值守」一节；按 #144 的收敛口径，SKILL 只留指针，入口、前置检查与产物口径收在这里，执行宿主都是 `windows`。

**9:00 报纸（Windows 原生）**：入口 `powershell -NoProfile -ExecutionPolicy Bypass -File '//wsl.localhost/Ubuntu/home/lmr/projects/Daily/run_all.ps1'`（shim 自动甄别 Git/MSYS bash；Git Bash 里 UNC 必须写正斜杠，反斜杠 UNC 会被吃掉）。自动启停 Strata @8080（~15.7GB，nvidia-smi 互斥把关），全链实测 15-20 分钟。产物 = `E:\每日新中国\<日期>\3新闻_概述.md` + 报纸 PNG；占位摘要 = 质量闸门命中，人工改写后 `.venv/Scripts/python.exe step8.py --date <日期>` 重跑；日志 = `E:\每日新中国\logs\cron-<日期>.log`。

**9:15 视频 driver**：前置检查在 Windows 侧查 `Get-CimInstance Win32_Process`（`bash.exe` 且命令行含 `run_all`，应无输出；WSL 的 `pgrep` 看不到 Windows 原生进程），辅以 `nvidia-smi --query-gpu=memory.used` 水位 >2000MiB 即 GPU 忙；ComfyUI 只在 Windows 侧 `curl 127.0.0.1:8188/queue` 判在线（18188 的 portproxy 常驻监听 ≠ 在线）；随后 `new_daily.py` 建读报单，再经 WMI 脱离启动 driver（`--gate-timeout 28800`）。入口层不代用户做确认门决策：绝不传 `--gate-approved`，失败只通知不代批，绝不并行第二条流水线。

## 迁移与守卫怎么读这份声明

- **唯一入口** `scripts/host_contract.py`：`hc.current_host()` / `hc.stage("s08")` / `hc.paths()` / `hc.services()`。scripts/ 里只有它出现本文档的文件名，其他脚本不许自己读文档（测试会查）。
- **#145 启动守卫**：`main()` 开头拿实际宿主与 `hc.current_host()` 比对（#151 已落地）；不匹配 exit 2 并指向本文件，不是 Python 堆栈。`DRIVER_HOST_OVERRIDE` 可豁免但必须打警告「已豁免，非权威宿主」。
- **#151 迁移已落地**：`driver.current` 已切到 `windows`，守卫随之校验 Windows；阶段表已两列合一，跨侧依赖与语法取原目标列。回滚方式见 PR（feat/151-windows-host）。
- **手工核对**：`python scripts/host_contract.py current`、`python scripts/host_contract.py stage s08`、`python scripts/host_contract.py json`；契约缺失或损坏一律 exit 2。

## 维护规则

1. 表格与机读声明逐格一致：新增或修改阶段、目录、服务时，声明块与表格两处同时改；`scripts/test_host_contract.py` 逐格比对，不靠人工记忆。
2. 阶段 id 必须与 `run_pipeline._stage_list` 一致（s01-s13），改阶段先改 driver。
3. 历史记录（`references/pitfalls.md`、ADR、postmortem）保留当时的结论，不回填新口径；需要面向未来的判断，写进本文件。
4. 宿主判定、路径转换、子进程解码收口到 #143 的单一模块；本文件只声明口径，不实现它们。
5. 迁移完成后本文件不删除：已从「当前/目标两列」变成「单列 + 历史理由」，继续承担唯一事实源。

## 机读声明（唯一事实源）

解析：`python scripts/host_contract.py json`；失败时 `HostContractError` 消息带文档路径。

```json host-contract
{
  "contract": "host-contract",
  "version": 2,
  "hosts": {
    "windows": {
      "label": "Windows 11 主机（当前执行宿主，也是全部重型服务的所在侧）",
      "evidence": "ComfyUI、Strata、llama-server、DSH 判官、发布用 Chrome、9:00 报纸管道都在 Windows 原生运行；#151 迁移后 driver 亦在此原生执行"
    },
    "wsl": {
      "label": "WSL2 Ubuntu（服务宿主：index-tts、whisper、biliup、sau 四个 WSL 独占依赖）",
      "evidence": "这四个依赖目前只有 WSL 侧有可用实现，driver 经单点 wsl.exe 调用；不再承担 driver 执行面"
    }
  },
  "driver": {
    "current": "windows",
    "current_why": "Windows 才是硬件与服务的所在侧：迁移后阶段命令原生执行，少一层 interop 与 /mnt 路径翻译；#150 实测 7 个重依赖里 6 个有 Windows 原生实现，只剩 index-tts、whisper、fc-match 三个轻量 CLI 需要 WSL，它们各自是单点调用，可以经一条非嵌套 wsl.exe 走通。",
    "evidence": [
      {
        "fact": "7 个重依赖里 6 个有 Windows 原生实现；WSL 独占的只有 index-tts、whisper、fc-match 三个轻量 CLI",
        "source": "#150"
      },
      {
        "fact": "driver 与 s01-s13 阶段命令全部由 subprocess.run(shell=True) 走 WSL bash；#144 实测 54 处子进程里 42 处没写编码、49 处 /mnt/ 路径、9 个文件盘符硬编码",
        "source": "#144"
      },
      {
        "fact": "ComfyUI 在 Windows 192.168.50.231:18188，每次提交都必须经 powershell.exe 跨侧调用",
        "source": "#144"
      },
      {
        "fact": "Strata 常驻 Windows 主机 8080，WSL 侧只能经默认网关 IP 访问",
        "source": "docs/adr/0006-strata-backend-and-window.md"
      },
      {
        "fact": "判官 llama-server 在 Windows 8100，DSH 桌面宿主与发布用 Chrome 也都在 Windows 侧",
        "source": "docs/adr/0007-vision-localization-cpu-resident.md"
      },
      {
        "fact": "pitfalls 记录：Windows 侧 driver 用非嵌套 wsl.exe 拉起实测存活，迁移不必担心父会话退出连坐进程组",
        "source": "references/pitfalls.md:471-479"
      }
    ]
  },
  "paths": {
    "current_rule": "driver 在 Windows：本侧路径写成 <盘符>:\\...，跨到 WSL 的路径写成 \\\\wsl.localhost\\Ubuntu\\...；子进程命令行里的路径不做隐式翻译",
    "slug_rule": "每份稿子建一个 slug 项目目录，位置在稿子旁边；严禁建到 /home/lmr 或任何 WSL 家目录",
    "converter": "WSL→Windows 用 ^/mnt/([a-zA-Z])/(.*)$ 转成 <大写盘符>:\\\\<剩余>；Windows→WSL 反向替换；两份历史实现（dsh_judge.to_win_path 与 vision_server._wsl_path）收口到 #143 的单一模块"
  },
  "syntax_kinds": [
    "bash",
    "python",
    "powershell",
    "cmd",
    "wsl.exe",
    "wmi"
  ],
  "stages": [
    {
      "id": "s01",
      "name": "分段 + 口播台词",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": [
        "01_分段"
      ]
    },
    {
      "id": "s02",
      "name": "TTS 音频（欢迎词 + 正文）",
      "host": "windows",
      "services": [
        "index-tts"
      ],
      "syntax": [
        "python",
        "wsl.exe"
      ],
      "artifacts": [
        "02_TTS语音"
      ]
    },
    {
      "id": "s03",
      "name": "TTS 时长回填",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": [
        "01_分段"
      ]
    },
    {
      "id": "s04",
      "name": "设计与 H3 prompt（compressed 单次直出）",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": [
        "03_导演设计",
        "06_H3prompt",
        "00_口播稿"
      ]
    },
    {
      "id": "s05",
      "name": "摄影师分镜（legacy）",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": [
        "04_分镜增强"
      ]
    },
    {
      "id": "s06",
      "name": "美术画质（legacy）",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": [
        "05_美术画质"
      ]
    },
    {
      "id": "s07",
      "name": "H3 prompt（legacy）",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": [
        "06_H3prompt"
      ]
    },
    {
      "id": "s08",
      "name": "H3 生成 + 抽帧竖屏检查",
      "host": "windows",
      "services": [
        "whisper"
      ],
      "syntax": [
        "python",
        "powershell",
        "wmi",
        "wsl.exe"
      ],
      "artifacts": [
        "07_成片",
        "_wsout"
      ]
    },
    {
      "id": "s09",
      "name": "读报装配",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": [
        "07_成片",
        "08_字幕"
      ]
    },
    {
      "id": "s10",
      "name": "字幕与烧录",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": [
        "07_成片",
        "08_字幕"
      ]
    },
    {
      "id": "s11",
      "name": "Krea2 封面",
      "host": "windows",
      "services": [],
      "syntax": [
        "python",
        "powershell"
      ],
      "artifacts": [
        "09_封面"
      ]
    },
    {
      "id": "s12",
      "name": "确认门 ④⑤（飞书）",
      "host": "windows",
      "services": [],
      "syntax": [
        "python"
      ],
      "artifacts": []
    },
    {
      "id": "s13",
      "name": "发布",
      "host": "windows",
      "services": [
        "biliup",
        "sau"
      ],
      "syntax": [
        "python",
        "wsl.exe",
        "cmd",
        "wmi",
        "powershell"
      ],
      "artifacts": [
        "10_发布"
      ]
    }
  ],
  "shells": [
    {
      "id": "wsl-bash",
      "host": "wsl",
      "usage": "WSL 服务调用面：driver 经单点 wsl.exe 进 WSL 跑 index-tts/whisper/biliup/sau",
      "forbidden": "禁止在 Windows 原生进程里直接假定它是可用 shell；跨侧调用必须显式写 wsl.exe"
    },
    {
      "id": "git-bash",
      "host": "windows",
      "usage": "仅供人在 Windows 上手敲 git/gh 与仓库日常命令",
      "forbidden": "禁止用 Git Bash 跑 pipeline 或任何阶段命令：它是 MSYS 不是 WSL，没有 /mnt 与 Linux 进程语义"
    },
    {
      "id": "windows-native",
      "host": "windows",
      "usage": "当前执行宿主的正式执行面：阶段命令由 Windows python 与 powershell/cmd 原生执行",
      "forbidden": "禁止用 cmd 内建命令替代 POSIX 语义；需要 bash 语义时只能经单点 wsl.exe 进 WSL"
    }
  ],
  "services": [
    {
      "id": "comfyui",
      "side": "windows",
      "endpoint": "192.168.50.231:18188（Windows 主机 ComfyUI，GPU 串行）",
      "usage": "s08 H3 视频生成、s11 Krea2 封面出图",
      "access": "HTTP 提交 workflow；driver 迁移后本机直连（env 可覆盖端点）"
    },
    {
      "id": "strata",
      "side": "windows",
      "endpoint": "Windows 主机 8080（Strata vLLM 服务）",
      "usage": "s01/s04-s08 的本地 LLM 推理：分段、设计、H3 prompt、文本重写",
      "access": "OpenAI 兼容 /v1；Windows 本机直连 127.0.0.1"
    },
    {
      "id": "llama-server",
      "side": "windows",
      "endpoint": "Windows 主机 8100（Vision-judge llama-server）",
      "usage": "s08 抽帧竖屏判读的本地 qwen3-vl-4b",
      "access": "HTTP /v1/chat/completions；地址由 vision_server.py 经 powershell.exe 双端解析"
    },
    {
      "id": "dsh-judge",
      "side": "windows",
      "endpoint": "E:\\AI\\deepseek-harness\\DeepSeek Harness.exe 与 dsh-profile/judgeprobe",
      "usage": "s08 关键帧的第二意见复判",
      "access": "interop 起 Windows 桌面宿主 headless 子进程；帧路径先 to_win_path() 转 Windows 口径"
    },
    {
      "id": "index-tts",
      "side": "wsl",
      "endpoint": "WSL 本地 GPU 上的 Index-TTS-2.5 服务",
      "usage": "s02 欢迎词与正文口播配音",
      "access": "tts_batch.py 直连本地服务，目前只有 WSL 侧有这套依赖"
    },
    {
      "id": "whisper",
      "side": "wsl",
      "endpoint": "WSL 内 whisper CLI（turbo，词级时间戳）",
      "usage": "s08 并行词级时间戳、s10 字幕时间轴",
      "access": "子进程调用，输出 JSON 落 _wsout"
    },
    {
      "id": "biliup",
      "side": "wsl",
      "endpoint": "/home/lmr/social-auto-upload/tools/biliup/linux-x86_64/biliup",
      "usage": "s13 B 站投稿（以 bv 号为成功判据）",
      "access": "CLI + cookie 文件，目前只有 WSL 版二进制"
    },
    {
      "id": "sau",
      "side": "wsl",
      "endpoint": "/home/lmr/social-auto-upload/.venv/bin/sau",
      "usage": "s13 抖音/快手/头条投稿",
      "access": "CLI headless，cwd=SAU_DIR，env -u DISPLAY"
    },
    {
      "id": "chrome-cdp",
      "side": "windows",
      "endpoint": "127.0.0.1:9222（chrome_sph_profile 调试端口）",
      "usage": "s13 视频号发布器驱动本机 Chrome",
      "access": "CDP HTTP；Chrome 由 powershell Start-Process 拉起"
    },
    {
      "id": "wmi-detach",
      "side": "windows",
      "endpoint": "WMI Win32_Process.Create（comfyui_selfheal.launch_detached）",
      "usage": "s13 视频号发布器脱离启动、ComfyUI 无头直启",
      "access": "WMI 建独立进程，driver 会话退出不影响"
    },
    {
      "id": "dsh-creds",
      "side": "windows",
      "endpoint": "C:\\Users\\LMR\\.dsh\\dsh-im-connect\\channels.json",
      "usage": "s12 飞书确认门读群凭据",
      "access": "Windows 侧直接读 C:\\Users\\LMR\\.dsh\\dsh-im-connect\\channels.json（env > ~/.dsh > 候选路径）"
    },
    {
      "id": "newspaper-files",
      "side": "windows",
      "endpoint": "E:\\每日新中国\\<日期>\\3新闻_概述.md 与报纸 PNG",
      "usage": "s09 读报装配的报纸页与当期概述",
      "access": "Windows 侧直读 E:\\每日新中国；9:00 报纸管道为 Windows 原生"
    }
  ],
  "artifact_dirs": [
    {
      "id": "00_口播稿",
      "path": "$SLUG\\00_口播稿",
      "writer": [
        "new_daily.py",
        "s04"
      ],
      "readers": [
        "s01",
        "s04",
        "s11"
      ],
      "cleanable": false,
      "clean_rule": "输入锚点与方向锁，永不清理"
    },
    {
      "id": "01_分段",
      "path": "$SLUG\\01_分段",
      "writer": [
        "s01",
        "s03"
      ],
      "readers": [
        "s03",
        "s08",
        "s10"
      ],
      "cleanable": true,
      "clean_rule": "下游已产出后可按期删，重跑会重建"
    },
    {
      "id": "02_TTS语音",
      "path": "$SLUG\\02_TTS语音",
      "writer": [
        "s02"
      ],
      "readers": [
        "s03",
        "s09",
        "s10"
      ],
      "cleanable": true,
      "clean_rule": "成片确认后可删，重跑会重合成"
    },
    {
      "id": "03_导演设计",
      "path": "$SLUG\\03_导演设计",
      "writer": [
        "s04"
      ],
      "readers": [
        "s05",
        "s06",
        "s07"
      ],
      "cleanable": true,
      "clean_rule": "legacy 车道中间件，compressed 车道不产出，可删"
    },
    {
      "id": "04_分镜增强",
      "path": "$SLUG\\04_分镜增强",
      "writer": [
        "s05"
      ],
      "readers": [
        "s06",
        "s07"
      ],
      "cleanable": true,
      "clean_rule": "legacy 车道中间件，可删"
    },
    {
      "id": "05_美术画质",
      "path": "$SLUG\\05_美术画质",
      "writer": [
        "s06"
      ],
      "readers": [
        "s07"
      ],
      "cleanable": true,
      "clean_rule": "legacy 车道中间件，可删"
    },
    {
      "id": "06_H3prompt",
      "path": "$SLUG\\06_H3prompt",
      "writer": [
        "s04",
        "s07"
      ],
      "readers": [
        "s08"
      ],
      "cleanable": false,
      "clean_rule": "s08 的输入与返工依据，保留到发布完成"
    },
    {
      "id": "07_成片",
      "path": "$SLUG\\07_成片",
      "writer": [
        "s08",
        "s09",
        "s10"
      ],
      "readers": [
        "s09",
        "s10",
        "s12",
        "s13"
      ],
      "cleanable": true,
      "clean_rule": "发布成功后可按期删，最终成片与核验报告留档到确认前"
    },
    {
      "id": "08_字幕",
      "path": "$SLUG\\08_字幕",
      "writer": [
        "s09",
        "s10"
      ],
      "readers": [
        "s10"
      ],
      "cleanable": true,
      "clean_rule": "烧录完成后可删，s10 可从时间轴重建"
    },
    {
      "id": "09_封面",
      "path": "$SLUG\\09_封面",
      "writer": [
        "s11"
      ],
      "readers": [
        "s12",
        "s13"
      ],
      "cleanable": false,
      "clean_rule": "平台标题与各平台封面都是发布输入，发布完成后才可删"
    },
    {
      "id": "10_发布",
      "path": "$SLUG\\10_发布",
      "writer": [
        "s13"
      ],
      "readers": [
        "s13",
        "sph_publish_v5.py"
      ],
      "cleanable": false,
      "clean_rule": "待发视频号与核验报告在视频号发布完成前不可删"
    },
    {
      "id": "_driver",
      "path": "$SLUG\\_driver",
      "writer": [
        "driver"
      ],
      "readers": [
        "driver"
      ],
      "cleanable": true,
      "clean_rule": "删哪个阶段的 stamp 就重跑哪个阶段，排障时专删"
    },
    {
      "id": "_wsout",
      "path": "$SLUG\\_wsout",
      "writer": [
        "s08"
      ],
      "readers": [
        "s10"
      ],
      "cleanable": true,
      "clean_rule": "whisper 词级缓存，删掉后 s10 会等待重算"
    },
    {
      "id": "DATE_ROOT",
      "path": "E:\\每日新中国",
      "writer": [
        "newspaper-cron",
        "sph_publish_v5.py"
      ],
      "readers": [
        "s09"
      ],
      "cleanable": false,
      "clean_rule": "报纸与概述是 s09 输入，不删；logs 可按日期清理"
    },
    {
      "id": "COMFYUI_OUT",
      "path": "E:\\AI\\Comfyui-WF-2026.8.8\\Comfyui-WF-2026.8.8\\ComfyUI\\output",
      "writer": [
        "comfyui"
      ],
      "readers": [
        "s11"
      ],
      "cleanable": true,
      "clean_rule": "拷进 09_封面 后可删，建议留最近 7 天便于返工"
    },
    {
      "id": "KREA2_ROOT",
      "path": "E:\\AI\\Krea2资源\\数字迷幻工作流、规则、插件、例图\\工作流",
      "writer": [
        "human"
      ],
      "readers": [
        "s11"
      ],
      "cleanable": false,
      "clean_rule": "工作流与规则是手工维护的唯一副本，禁删"
    },
    {
      "id": "TEMP",
      "path": "C:\\Users\\LMR\\AppData\\Local\\Temp",
      "writer": [
        "s11",
        "comfyui_selfheal.py"
      ],
      "readers": [
        "s11",
        "comfyui_selfheal.py"
      ],
      "cleanable": true,
      "clean_rule": "object_info.json、krea2_*.json、launch ps1 都是每次重建的临时件，可删"
    },
    {
      "id": "SAU_ROOT",
      "path": "\\\\wsl.localhost\\Ubuntu\\home\\lmr\\social-auto-upload",
      "writer": [
        "s13"
      ],
      "readers": [
        "s13"
      ],
      "cleanable": false,
      "clean_rule": "cookie 与登录态是唯一副本，禁删"
    }
  ]
}
```
