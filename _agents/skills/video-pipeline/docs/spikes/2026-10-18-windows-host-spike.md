# Spike 报告：Windows 主执行的三处未知实测（票 #150，2026-10-18）

> 票 #150 的汇总实测记录。本票**不改流水线代码**（`scripts/*.py` 全程只读）；以下数字是 #151「把 `driver.current` 从 `wsl` 切到 `windows`」的设计参数来源。
> 三处未知各有一份独立子报告，本文件只做**汇总、交叉核对与定案**：
> - [TTS 经 wsl.exe 调用](2026-10-18-windows-host-tts.md)（AC1）
> - [whisper 走 interop 还是 Windows 本地](2026-10-18-windows-host-whisper.md)（AC2）
> - [字体预检与字幕/标题字体](2026-10-18-windows-host-font.md)（AC3）
>
> 环境：Windows 10 22H2（10.0.19045）+ WSL2 Ubuntu（kernel 6.6.114.1，NAT 模式，WSL 2.7.3.0），RTX 5080 16GB（driver 595.79），32 逻辑核，RAM 127GB。
> 样本：生产真实产物 `E:\每日新中国\2026-10-17\video\每日新中国b站\流水线\daily-china-20261017\`（17 段 TTS wav + 17 个 `_wsout` whisper JSON + 真实 `01_分段/口播台词`）。
> **GPU 使用声明**：三份子报告里的 whisper 与 font 部分全程**未占 GPU**（whisper 一律强制 `--device cpu` + `CUDA_VISIBLE_DEVICES=""`）。TTS 的 interop 层测量同样未占 GPU，但**真实推理在收尾时补跑了**（GPU 空闲窗口，基线 892 / 16,303 MiB，跑完回落）——数据见 §1.1 的真实推理表，这是本票唯一一次真实占卡。

## 结论速览

| AC | 结论 |
|---|---|
| **AC1** TTS 经 wsl.exe | **可行，真实推理已实测**：单段 **27.1 s**、17 段全量冷 **100.1 s** / 热 **95.3 s**（音频总时长 132.3 s），GPU 峰值 **10,298 MiB** / util 96%；生产 17 段的增量场景（部分段已存在被跳过）**69 s**。路径必须由 **Windows 侧预转 `/mnt/e/...`** 再传（`tts_batch.py` 不做翻译）；**argv 数组不经 shell** 是唯一安全写法；wsl.exe **不串行化**并发；退出码原样透传，**wsl.exe 自身失败 = 4294967295 + stdout UTF-16LE**；interop 税 ~110 ms/次占 **0.1–0.3%**。**2 路并发显存峰值 15,895/16,303 MiB（97.5%），与 ComfyUI 同时必 OOM → 必须串行。** |
| **AC2** whisper 路线 | **定案：保留 interop（WSL whisper），不迁 Windows 本地**。JSON 结构与生产 `_wsout` **100% 同构**（`gen_subs_from_timeline.py` 零改动）；本地 faster-whisper 常驻快 4.7×（4.41 s vs **强制 CPU** 的 22.1 s/段；生产 interop 走 GPU 是 15.0 s/段，差距远没那么大）但要新增 82 行转换器 + 1546.5 MiB 模型 + 155 MiB 包。**但 interop 必须补 `--device cpu`**——现在默认走 cuda，与 H3 抢同一张 5080。 |
| **AC3** 字体预检 | **定案：`os.path.isfile(assets/fonts/SmileySans-Oblique.ttf)`（0.099 ms）+ 族名改 `Smiley Sans Oblique` + s10 传 `fontsdir`**。Windows 的 libass 走 DirectWrite+GDI，`FontName=Smiley Sans` **必然静默回退雅黑**（那是 nameID 16，不是 Windows 族名）。**`fc-match` 在 Windows 上不可用**（格式串恒返回空串）；注册表值名是中文「得意黑 斜体」，按名 grep 匹配不到。失败策略 **fail-fast 不回退**。 |
| **AC4** WSL venv 保留 | **必须保留**：至少到 #151 落地**并在 Windows 宿主连续跑通 ≥3 个完整生产日**（含一次跨日、一次 ComfyUI 同时占 GPU 的时段）；**退役需单独一票**（重建 = 重下 16.2 G 权重 + 9.9 G cu128 torch 栈），不设自动过期。Windows 侧零安装（`indextts` 两个 python 都 `False`、`where whisper` rc=1）；WSL 独占 **26 G**（权重 16.2 G + venv 9.9 G）。**保留代价只有磁盘**——空闲态 venv 驻留内存 **0**（无进程映射它），调用时 RSS 峰值 960 MB 且 10 s 内回落。可清 whisper 缓存 **3.79 GB**（生产只用 turbo）。 |
| **AC5** 结论入仓 | 本文件 + 三份子报告，均已入 `docs/spikes/`。 |
| **⚠ 前置阻塞**（AC3 副产物） | **`C:\ffmpeg-master-latest-win64-gpl` 这个构建根本没有 `drawtext` 滤镜**，而它是 `PATH` 首选。s10 顶部标题在 Windows 上**必挂**，与字体转义无关。**这是 #151 的独立阻塞项，必须先解决。** |
| **⚠ 前提被证伪**（AC2 副产物） | `run_pipeline.py:787` docstring 与 `:843` 注释里的「whisper CPU 并行车道」**是愿望不是事实**：代码没写 `--device`，openai-whisper 默认 cuda，生产实际 **15.0 s/段**（只有 GPU 可能）。迁移时必须显式钉 CPU。（注意：`docs/host-contract.md` 全文**没有**「CPU 车道」字样，它只登记 `whisper | side=wsl`——误引已在此更正。） |

## 1. AC1：TTS 经 wsl.exe 调用

### 1.1 定案模板（可直接抄进 #151）

```python
# Windows 侧 driver 调 TTS 的唯一入口
WSL_EXE   = r"C:\Windows\System32\wsl.exe"
TTS_PY    = "/home/lmr/index-tts/.venv/bin/python"
TTS_CWD   = "/home/lmr/index-tts"
MODEL_DIR = "/home/lmr/index-tts/checkpoints_2_5"
TTS_BATCH = "/mnt/c/Users/LMR/.agents/skills/video-pipeline/scripts/tts_batch.py"

def to_wsl(win_path: str) -> str:
    drive, rest = os.path.splitdrive(os.path.abspath(win_path))
    return "/mnt/" + drive[0].lower() + rest.replace("\\", "/")

argv = [WSL_EXE, "-d", "Ubuntu",
        "--cd", TTS_CWD,
        "-e", "env", f"INDEXTTS2_MODEL_DIR={MODEL_DIR}",
        TTS_PY, TTS_BATCH,
        to_wsl(script_dir), to_wsl(voice_ref), to_wsl(out_dir)]
r = subprocess.run(argv, capture_output=True, timeout=7200)   # 不要 text=True
out = r.stdout.decode("utf-8", "replace")
```

调用层开销三组实测：stub 5 连跑 `[229.8, 228.8, 225.2, 228.7, 231.1] ms`（median **228.8 ms**）；**真实 `tts_batch.py` + 真实 17 个 shotNN.md 目录**（`--start 999` 跳过全部段）median **0.140 s**，rc 全 0；纯 interop 层 `wsl.exe -e true` median **108.6 ms**。

**真实推理（本票唯一一次占卡，GPU 空闲基线 892 / 16,303 MiB）**：

| 场景 | 墙钟 | GPU 峰值 | util 峰值 | rc |
|---|---|---|---|---|
| 单段（8.85 s 音频） | **27.1 s** | 10,291 MiB | 97% | 0 |
| 2 段 | **43.8 s** | — | — | 0 |
| 17 段全量（冷，权重未进页缓存） | **100.1 s** | 10,298 MiB | 96% | 0 |
| 17 段全量（热，权重已进页缓存） | **95.3 s** | 10,297 MiB | 96% | 0 |
| 2 路并发（各 1 段，同时起） | **40.6 s** | **15,895 MiB（97.5%）** | 96% | 0 / 0 |

17 段产出的音频总时长 **132.342 s**（生产同批是 133.02 s，吻合）。冷热差约 4.8 s = 权重 5.2 G 载入 + torch 初始化。
→ **TTS 是重度 GPU 消费者（单路 ~10.3 GB）**，与 ComfyUI/Strata 必须进 GPU 串行队列；2 路并发虽然都能 rc=0，但吃满 97.5% 显存，**任何并跑都会 OOM**。

### 1.2 三个必知事实

| 事实 | 实测依据 |
|---|---|
| **路径必须预转** | Windows 路径字符串能无损穿过 wsl.exe（`LEN=78` 原样收到），但 WSL 侧 `os.path.exists` 恒 **False**——`tts_batch.py` 不做翻译。转成 `/mnt/e/...` 后 `exists=True`，0.116 s。CJK 无损：三条路线 md5 同为 `39e54497cdbae70a07b1a91da261370b`。 |
| **argv 数组，绝不拼字符串** | 经 cmd.exe 拼命令行时反斜杠全被吃、按空格分词；单引号保留反斜杠但 `exists=False`；双引号反斜杠被吃。**`-e` 直连 exec 连引号问题都没有**（0.209 s）。 |
| **`text=True` 会静默吞输出** | Windows python 的 `text=True` 默认 **cp936**，WSL 输出是 UTF-8 → reader 线程抛 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xad`，但 **`run()` 照常返回 rc=0 且 `stdout=None`**。必须 `capture_output` + 手工 `decode("utf-8", "replace")`。 |

### 1.3 并发与 GPU 争用

- **不串行化**：2×sleep2 顺序 3.968 s / 并行 1.996 s（1.99×）；4 路 3.94×。CPU 死循环占满单核时另一条 echo 延迟与空闲无差异（`[101.1,96.4,101.2,100.1,97.2]` vs `[111.5,102.2,107.9,97.8,106.5] ms`）。
- **interop 税 = 80–180 ms/次（取 110 ms 作设计值）**，全在 wsl.exe 本身：`-e bash -lc` 94.6 / `-e bash -c` 95.3 / `-e <binary>` 95.4 ms 三者相同；lead 复测 9 连跑 median：`-e true` **108.6** / `-e python3 -c pass` **107.9** / `-e bash -lc true` **108.9 ms**（WSL 内 spawn python3 本身只要 27.9 ms）。whisper 报告里的 130–180 ms 是同量级的不同时段采样。**17 次独立 wsl.exe = 2.064 s vs 1 次内做同样事 0.136 s → 税 1.928 s**。→ **一次调用批处理全部段，不要按 shot 扇出**。
- **两侧是同一张卡**：UUID `GPU-650c43a4-f9a4-0765-b7b9-66512d60ef86`、PCI `00000000:01:00.0`、`memory.total` 16303 MiB 两侧全同；WSL 侧 GPU 经 `/dev/dxg` 直通。**但 WSL 侧 `--query-compute-apps` 恒为空**（只有表头），Windows 侧多为 `[Insufficient Permissions]`/`[N/A]` → **互斥只能按总量水位判断，且必须在 Windows 侧查**（host-contract `:116`：>2000 MiB 即 GPU 忙）。
- **`--terminate` 是禁区**：0.070 s 完成但杀掉 WSL 内全部常驻进程（tmux、qdrant、hermes、llama-server、comfy-mcp…），之后首条命令 **10.849 s** 冷启。

### 1.4 退出码与失败辨识

内部退出码**原样透传**（0/1/2/3/42/127/128/255 全对），但有 **8 位截断**（`exit 256`→0、`exit 300`→44）。`wsl.exe` 自身失败 = **4294967295（0xFFFFFFFF）**，stderr 为空、错误在 **stdout 的 UTF-16LE** 里。

| 情形 | rc | 辨识特征 |
|---|---|---|
| 目标 `.py` 不存在 | 2 | `python3: can't open file '/nonexistent/x.py'` |
| venv python 不存在（走 `bash -lc`） | 127 | `bash: line 1: ...: No such file or directory` |
| venv python 不存在（走 `-e` 直连） | 1 | `WSL (3110 - Relay) ERROR: CreateProcessCommon:818: execvpe(...) failed` |
| 未知发行版（从 **Windows python subprocess** 调） | **4294967295** | stderr 空，stdout UTF-16LE `WSL_E_DISTRO_NOT_FOUND` |
| 未知发行版（从 **bash / MSYS / cmd / PowerShell** 调） | **127** | stderr 为空或乱码 |
| 未知发行版（**WSL 内嵌套**调 wsl.exe） | **255** | — |
| 超时 | `TimeoutExpired` | 杀 wsl.exe 后 1 s 内 WSL 侧 `pgrep` 残留 **0**（无孤儿，可直接重试） |

**rc 形态随调用方变**（同一条 `-d NoSuchDistro` 命令实测）：Windows python subprocess → **4294967295**；bash/MSYS/cmd/PowerShell → **127**；WSL 内嵌套 → **255**。driver 是 python subprocess，**生产判据用 4294967295**；子报告里那条 127 是 bash 侧观测，不是矛盾。

**UTF-8 vs UTF-16LE 判别**：`--list -v` 原始字节 NUL 占比 **0.35**，`-e bash` 的 stdout NUL 占比 **0.00** → 按 NUL 字节判别（与 pitfalls #47 一致）。

真实 `tts_batch.py` 的签名：**rc=1 且 stdout 为空 = 崩溃**（实测 `stderr_len=419 B`），必须同时收 stderr；反过来 **rc=0 + stdout 空** 说明输出被 cp936 吞了，也该报警。

### 1.5 全量 vs 增量：两个数字都对，场景不同

2026-10-17 生产跑出的 17 个 wav：mtime 跨度 **68.97 s**、平均段间隔 **4.311 s**、音频总时长 **133.02 s**（ffprobe 逐段求和）→ 墙钟/音频比 **0.519**，约 2× 实时。

这条 68.97 s **不是全量生成**：tts_batch.py 会跳过已存在的 wav，生产上该目录是分次补齐的。本次**全量冷跑实测 100.1 s**（音频 132.3 s，墙钟/音频比 0.757）。

| 口径 | 墙钟 | 说明 |
|---|---|---|
| 全量生成 17 段 | **100.1 s（冷）/ 95.3 s（热）** | 本次实测 |
| 增量补齐（部分已存在） | **~69 s** | 生产 mtime 观测 |

interop 税 ~1.9 s 在两种口径下都只占 **0.1–0.3%**；`timeout=7200` 有 70× 余量。

**真正的固定成本不是 interop，是 import + 权重加载**：`import torch` **4.2 s（page cache 热）/ 8.8 s（冷页）**，加上 5.2 G 权重载入，冷热差 **4.8 s**——比 110 ms 的 interop 税大两个量级。

## 2. AC2：whisper 走 interop 还是 Windows 本地

### 2.1 对比与择一

| 维度 | A interop（WSL whisper） | B 本地（Windows faster-whisper） | 谁赢 |
|---|---|---|---|
| 单段耗时（8.5 s 音频） | **强制 CPU** 冷 27.96 s / 热 23.35 s，均值 **22.1 s**（生产 GPU 实跑 **15.0 s**） | 常驻 **4.41 s**（8 线程）/ 逐段 CLI **13.09 s** | B（4.7×，但是「B 常驻 vs A 强制 CPU」，不是「B vs A 生产」） |
| 17 段外推 | **376 s**（强制 CPU）／生产 GPU 实测 ~255 s | 常驻 **75 s** / 逐段 CLI 223 s | B（常驻）/ 平（逐段） |
| 固定开销 | wsl.exe **0.13–0.18 s（0.6–0.8%）** | `import ctranslate2` **5.6 s**（逐段形态占 **43%**） | **A** |
| 新增依赖体积 | **0** | 包 ~155 MiB + 模型 **1546.5 MiB** | **A** |
| CPU / 内存 | 688–943%（7–9 核）/ RSS **5.35 GB** | 393–402%（4 核）或 793–798%（8 核）/ RSS **2.28–2.33 GB** | B |
| JSON 兼容 | **零改动** | **零改动**，但要新增并维护 **82 行**转换器；`tokens` 退化为 `[]` | 平 |
| 失败面 | 音频缺失 rc=0 静默不产 JSON；distro 缺失 rc=4294967295（python subprocess） | 模型未下载 → 静默下载 19 分钟；截断风险 | A |

> **读表须知**：时间维度 B 确实赢，但这是「B 常驻 vs A **强制 CPU**」的比较。生产 A 走 GPU 是 15.0 s/段，B 逐段起进程是 13.09 s/段——只差 13%。择一看的是综合三条理由（下段），不是逐维度打分求和。

**定案：interop。** 三条理由：① JSON 同构零成本，本地路线要长期维护转换器且 `tokens` 永久退化（现在没人读，但是未来会咬人的债）；② 跨侧固定开销只占 0.6–0.8%，而本地逐段形态下 import 占 43%，13.09 s vs 22.1 s 只快 41%，远不是常驻的 4.7×；③ 本地多 1546.5 MiB 模型 + 155 MiB 包 + 一条新失败面（下载实测单连接 1.37 MB/s、`curl` 不带 `-L` 只拿到 100 字节重定向文本、`cat part_*` 截断会静默产出坏模型）。

### 2.2 必须修的一件事：显式钉 CPU

**「whisper 是 CPU 车道」这个前提本身是错的**，三重证据：

1. 代码里**没有任何 `--device`**（`scripts/run_pipeline.py:794` 的 whisper 命令只有 `--model turbo --language zh --word_timestamps True --output_format json --output_dir`）；
2. openai-whisper 20250625 默认值就是 cuda（`whisper/transcribe.py:532`、`whisper/__init__.py:131` 均为 `default="cuda" if torch.cuda.is_available() else "cpu"`），WSL 内 `torch.cuda.is_available()=True`、`get_device_name(0)="NVIDIA GeForce RTX 5080"`；
3. 生产 `_wsout` 17 个 JSON 的 mtime 跨度 **255.6 s → 15.0 s/段**，远快于 CPU 实测 22.1 s/段，只有 GPU 可能。

→ **迁移后 s08 的命令必须加 `--device cpu` 或 `CUDA_VISIBLE_DEVICES=""`（建议都写）**。否则 whisper 与 ComfyUI H3 争同一张 16 GB 5080，「双车道并行」不成立。

代价与收益：强制 CPU 后 s08 的 whisper 部分从 ~255 s 变 **~376 s（+121 s）**，但它与 GPU 生成真并行，整体 s08 不一定变慢。**#151 验收项应加一条：跑 s08 期间 `nvidia-smi` 的显存增量只应来自 ComfyUI 单方。**

### 2.3 JSON 同构（`gen_subs_from_timeline.py` 零改动）

interop 输出 vs 生产 `_wsout`（3 段全做）：top keys `['text','segments','language']` 全同；segment 11 键全同；word 键 `['word','start','end','probability']` 全同；`text` 逐字相同；词数全同；逐词 `start/end` 最大偏差 **0.020 s**、`probability` 最大偏差 0.0075。→ `scripts/gen_subs_from_timeline.py:20-29` 的 `load_words` **一行都不用改**。

### 2.4 新增的产物校验（rc=0 不代表有产物）

**音频缺失时 whisper rc=0 且静默不产 JSON**。→ 每段后断言 `<wsdir>/<sid>.json` 存在且 `len(segments)>0`。

## 3. AC3：字体预检的 Windows 替代

### 3.1 定案方案

| 步骤 | 做法 | 实测 |
|---|---|---|
| ① 族名常量 | `SUBTITLE_FONT_NAME` 默认值 `"Smiley Sans"` → **`"Smiley Sans Oblique"`** | `Smiley Sans` 在 Windows 上**必然静默回退**；`Smiley Sans Oblique` 命中 `SmileySans-Oblique, 0` |
| ② 预检 | **`os.path.isfile(<repo>/assets/fonts/SmileySans-Oblique.ttf)`** | **0.099 ms**，size=2629764 |
| ③ s10 烧录 | `subtitles` 同时传 `fontsdir=<仓库 assets/fonts 绝对路径>` | fontsdir 能让**未安装**的字体生效（Noto CJK 实测 ✓） |
| ④ 开发期二次校验 | fontTools 读 nameID 1 对比常量 | 47.28 ms，**不进每次运行** |

**失败策略：fail-fast，不回退。** 对齐 CONTEXT.md「必需资产」口径——字体是 skill 自带静态资产，缺失即 preflight 拦截。**「判不到字体就静默用 Noto」正是已废除的「保守放行」**，应一并废除。

「文件缺失」与「系统没装」是两个不同问题：前者 **fail-fast**，后者用 **fontsdir 消解（是修复不是回退）**，都不该退化成悄悄换 Noto。

### 3.2 为什么 `Smiley Sans` 必然回退

Windows 的 libass 走 **DirectWrite + GDI**（不用 fontconfig）。字体 name 表里：nameID 1 = `Smiley Sans Oblique` / `得意黑`；**nameID 16 = `Smiley Sans`**。DirectWrite 只认 nameID 1，所以 `Smiley Sans` 解析失败 → ArialMT → 微软雅黑。

**硬证据（相等类）**：同一组参数下，`Smiley Sans` / `Noto Sans CJK SC` / `NoSuchFontXYZ123` 三者输出 PNG **字节完全一致**——都是同一份雅黑渲染；`Smiley Sans Oblique` 与 `得意黑` **字节完全一致**——都是得意黑。

> ⚠ **不要跨节比对 md5**：本报告做过三组独立实验（drawtext `fontfile` 矩阵、`subtitles` 族名矩阵、lead 用 anaconda ffmpeg 4.3.1 复验），参数与 ffmpeg 版本都不同，三组的绝对 md5 值互不相等（各子报告里分别记作 `278ee561…`/`e5d2e6fa…`、`143dd4d9…`/`4e5bcc64…`、`372a27ca…`/`a7eeb084…`）。**可靠的结论只有「相等类」**：回退组三者相等、命中组两者相等，这个模式在三组里都复现。

**所有失败用例 rc 均为 0、无 warning**，只在 `-v verbose` 的 `fontselect:` 行露马脚。PNG 目视（720×1280）也确认：回退版是雅黑（横细竖粗、笔画末端喇叭口），命中版是得意黑（整体右倾、等粗直线、字形窄长）。

### 3.3 回退族名也要改

`SUBTITLE_FONT_FALLBACK` 从 `"Noto Sans CJK SC"` → **`"Noto Sans SC"`**。Windows 已装族名全枚举 **TOTAL=370**：有 `Noto Sans SC`(+5 变体)、`Microsoft YaHei`，**没有 `Noto Sans CJK SC`**——现常量会静默回退到雅黑 UI。

### 3.4 明确不可用的两条路

- **`fc-match`**：Windows 上只有 anaconda 带的 MSYS2 裁剪版。它**默认格式正常**（`fc-match "Smiley Sans"` → `SmileySans-Oblique-2.ttf: "Smiley Sans" "Oblique"`，rc=0），但 **`-f` 格式串是坏的**：`-f '%{family}'` / `-f '%{file}'` 对所有名字都返回空串（rc=0、91–115 ms）。而现有 `run_pipeline.py:1781` 恰好用的就是 `-f '%{family}'` → **照搬会恒判失败**。且它只在装了 anaconda 的机器上存在，不能作 skill 依赖。
- **注册表枚举**：HKCU 里那条的值名是中文「得意黑 斜体 (TrueType)」，按名 grep `smiley` **匹配不到**（必须按值路径匹配），且依赖「用户装了字体」而 skill 自带字体不需要装。

**降级方案**：ffmpeg 渲染探针（`-v verbose` 读 `fontselect:`，**1 次且含 SmileySans = 命中；2 次 = 回退**），0.052 s，仅当现场告警时跑。**判据用 fontselect 行数，不要用耗时**——命中与回退耗时无差别（0.300 s vs 0.303 s）。

## 4. AC4：WSL 侧 venv 是否需要保留

**结论：必须保留；退役需要单独一票，不设自动过期。**

| 依据 | 实测 |
|---|---|
| Windows 侧零安装 | `indextts` 在 anaconda py3.13 与 Python311 下 `find_spec` 均 **False**；`where whisper` **rc=1**；`E:\AI\index-tts` 等四个候选路径全 `exists=False` |
| WSL 独占体量 | `/home/lmr/index-tts` **26 G**：`checkpoints` 11 G（engine 2 fallback）+ `checkpoints_2_5` **5.2 G**（生产权重）+ `.venv` **9.9 G**（183 包，torch 2.8.0+cu128）；venv python 3.11.15 |
| 不可迁移性 | `checkpoints_2_5/hf_cache` 是**符号链接** → `checkpoints/hf_cache`，跨文件系统迁移会断链 |
| **保留代价只有磁盘** | 空闲态：`pgrep indextts/tts_batch/index-tts` **0 条**、`/proc/*/maps` 映射 `/home/lmr/index-tts` 的进程 **0 个** → **venv 完全不在内存里**。调用时 RSS 峰值 960 MB（`import infer_v2`），10 s 内回落 |
| 别拿「省内存」当理由 | WSL VM 的 vmmem WorkingSet **23.83 GiB**，主因是 llama-server（15.8 GB）等其它服务，**没有一字节属于 index-tts** |

**保留时长（与 whisper 侧统一口径）**：至少到 **#151 落地 + 在 Windows 宿主连续跑通 ≥3 个完整生产日**（含至少一次跨日、一次 ComfyUI 同时占 GPU 的时段）；要谈退役必须先有**单独一票**做「Windows 侧 index-tts 可行性」评估（重建 = 重下 16.2 G 权重 + 9.9 G cu128 torch 栈 + 解决包对 Linux 路径/符号链接的假设）。**不设自动过期**——本机磁盘余量（C: 100.5 G / E: 249.3 G）不构成删除压力，删任何一部分都是不可逆的数据损失。

**可以立刻清的部分**：`~/.cache/whisper/{tiny,base,small,large-v3}.pt` 共 **3.79 GB**（生产只用 turbo），保留 `large-v3-turbo.pt` 1.617 GB 与 `whisper` 包（仅 2.1 M）。**torch/triton（1.6 G + 641 M）不能只按 whisper 判断**——WSL 里 llama-server、qdrant 也在用。

## 5. 三处未知之外的发现

### 5.1 ⚠ 阻塞：PATH 首选的 ffmpeg 没有 drawtext

| ffmpeg | 版本 | drawtext |
|---|---|---|
| `C:\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe`（**裸名命中这个**） | N-111911-gc4ab17a62d-20230904 | **无** |
| `C:\Users\LMR\anaconda3\Library\bin\ffmpeg.exe` | 4.3.1 | 有 |
| WSL `/usr/bin/ffmpeg`（今天生产在用） | 6.1.1-3ubuntu5 | 有 |

该构建的 configuration **含** `--enable-libass --enable-libfreetype --enable-libfribidi --enable-fontconfig`，却**没把 drawtext 编进去**（546 个滤镜里 draw 系列只有 drawbox/drawgraph/drawgrid）。三重确认：`-filters | grep -w drawtext` 无输出、`-h filter=drawtext` → `Unknown filter 'drawtext'.`、实跑 → `No such filter: 'drawtext'` + `Error initializing a simple filtergraph`。

而 `run_pipeline.py:1809` 是**裸名**调用 `["ffmpeg", "-y", "-v", "error", ...]`（另见 `:1235`、`:1766` 两处 `sh(f'ffmpeg …')`），所以 Windows 上 s10 顶部标题**必挂**。libass 的 `subtitles`/`ass` 在（走 DirectWrite，不依赖 freetype），**只有 drawtext 缺**。

**处置**：这是 #151 的独立阻塞项，二选一——换一个同时有 drawtext + libass + 能编 5080 的 ffmpeg 发行版，或把顶部标题也改成 libass 路线（注意 pitfalls #279：Alignment=8 不可靠）。**本票只负责暴露，不负责修**。

### 5.2 drawtext / subtitles 的 Windows 路径转义（有 drawtext 时可用）

规则：**盘符冒号在 filtergraph 里要转义，引号会改变层数**——加单引号 → 恰好一层 `\:`；不加引号 → 两层 `\\:`。**裸反斜杠任何形式都不 work**（被当转义序列吃掉）；**双引号无效**（filtergraph 只认单引号）。

```python
fontfile = "'" + title_font.replace("\\", "/").replace(":", "\\:") + "'"
# 更稳：改用相对路径（cwd=仓库根），彻底绕开冒号与反斜杠
```

`subtitles` 的写法更严：**必须 `filename=` 命名参数 + 单引号 + 正斜杠 + 冒号一层转义**。`subtitles=C:/...` 直接 rc=**4294967274** 失败（Invalid argument）；`subtitles=filename='C\:/...'` → rc=0 ✓。

### 5.3 ⚠ 前提被证伪：whisper 的「CPU 并行车道」

见 §2.2。这条要写进 #151 验收项，否则迁移只是把「注释里的愿望」原样搬过去。

## 6. 对 #151 的参数直引

| #151 要做的事 | 直接可用的结论 |
|---|---|
| s02 TTS 调用点（`run_pipeline.py:511` 欢迎词 / `:516` 正文） | 换成 §1.1 的 `run_tts()`。**注意**：`--cd` 与 `-e env` 对真实 `tts_batch.py` **都不是必需的**（`scripts/tts_batch.py:64-76` 自设 `cwd` 并硬编码 `INDEXTTS2_MODEL_DIR`，四种形态全 rc=0），保留仅作防御 + 兼容将来直调 `cli_v2`；**别在注释里写「不设就 fallback engine 2」**——那是脚本行为，不是调用方责任 |
| 路径转换 | 复用 host-contract `:187` 的 converter。**转换责任在 Windows driver**，`tts_batch.py` 不改 |
| 子进程编码 | `scripts/*.py`（94 个文件、28 个含子进程）里 `subprocess.run/Popen/check_output/call/check_call` 共 **51 处，其中 42 处没写 `encoding`**；`run_pipeline.py` 单独看 **3/3 全没写**（`:163` 的 `sh()`、`:1811`、`:2145`）。跨 wsl.exe 的调用必须 `capture_output` + `decode("utf-8","replace")`，否则 stdout 静默变 `None`。（`docs/host-contract.md:26` 记的 54/42 是 #144 时点口径，不回填） |
| 错误判据 | 用 §1.4 的五分支；**rc=4294967295 = wsl.exe 自身故障**，走「环境异常」不重试 |
| 超时 | `timeout=7200` 保留（全量实测 100 s，70× 余量）；超时杀 wsl.exe 后 WSL 内无孤儿，可直接重试 |
| GPU 互斥 | s02 进 WSL 前在 **Windows 侧**查 `nvidia-smi --query-gpu=memory.used`，>2000 MiB 即等（host-contract `:116`）。**不要**在 WSL 侧自查（看不到争用者） |
| 并发 | 不要按 shot 扇出（税 ~110 ms/次）；**也不要并发跑两条 TTS**——实测 2 路并发显存峰值 **15,895/16,303 MiB（97.5%）**，与 ComfyUI/Strata 并跑必 OOM。TTS 进 GPU 串行队列 |
| `--terminate` | **禁用**（杀掉全部常驻服务 + 10.8 s 冷启） |
| s08 whisper 命令（`run_pipeline.py:794`） | 保留 interop 并**加 `--device cpu` + `CUDA_VISIBLE_DEVICES=""`**。跨侧传中文路径加 `MSYS2_ARG_CONV_EXCL='*'`；`bash -lc` 保 PATH（pitfalls #47） |
| s08 产物校验 | **新增**：每段后断言 `<wsdir>/<sid>.json` 存在且 `len(segments)>0` |
| s08 GPU 校验 | 跑 s08 期间 `nvidia-smi --query-compute-apps` 不应出现 whisper/python；显存增量只来自 ComfyUI |
| s10 字幕族名 | `SUBTITLE_FONT_NAME = "Smiley Sans Oblique"`（`:129`）；`SUBTITLE_FONT_FALLBACK = "Noto Sans SC"`（`:130`） |
| s10 预检（替代 `fc-match`，`:1781`） | `os.path.isfile(<repo>/assets/fonts/SmileySans-Oblique.ttf)`，缺失 → **preflight fail-fast** |
| s10 烧录参数（`:1804`） | `subtitles=filename='C\:/.../x.srt':fontsdir='C\:/.../assets/fonts':force_style='...FontName=Smiley Sans Oblique...'` |
| s10 标题（`:1801`） | ⚠ **先解决 ffmpeg 构建**（§5.1）；`run_pipeline.py:1809` 是裸名 `["ffmpeg", ...]` |
| 预检探活 | `wsl.exe -l -v`（0.056 s）。**不要**用 `import torch` 探活（3–6 s） |
| WSL 依赖保留 | 见 §4：全保留到 #151 落地 + 连续 3 个生产日，退役需单独一票；可清 whisper 缓存 3.79 GB |
| 字体目录整理 | `assets/fonts/` 里的 `README.md` 会被 fontsdir 当字体加载（`Error opening memory font`，非致命）。建议移出或另建 `assets/fonts/ttf/` |

## 7. 未知项（诚实清单）

TTS 推理已在收尾时真实跑过（§1.1 表，GPU 空闲窗口），所以下面这份清单比首版短：

- **17 段连续跑的内存增长未测**（只测了首尾与峰值）；**>30 s 长音频**未测（生产单段 7–9 s）。
- **模型加载耗时未单独拆出**：冷/热全量差 **4.8 s** 是「5.2 G 权重载入 + torch 初始化」的合计，没分别计时。
- **3 路以上并发的显存行为未测**：只测了 2 路（峰值 97.5%），已足以判定「不能与 ComfyUI 并跑」，但没测 3/4 路的失败形态。
- `/tmp/tts_batch_manifest.jsonl` 是 `tts_batch.py:64` 的**固定路径**，多进程并发会互相覆盖（代码层面确定存在，本次未复现——2 路并发用的是不同 in/out 目录，仍然共享这个 manifest 路径，**这是个真实隐患**）。
- **drawtext 缺失的根因未查证**：没查 BtbN 的构建脚本，也没测其他 Windows ffmpeg 发行版。**anaconda 4.3.1 有 drawtext 但它是 2020 年版**，libx264/x265 版本、`-c:a copy` 兼容、4K 性能都没测，**不建议直接切生产**。
- **生产 GPU 路径的 whisper 单段耗时未直接测**（子报告受禁占 GPU 约束），只能用 `_wsout` mtime 反推 15.0 s/段。本次 TTS 推理占卡时也没顺带测 whisper（那是另一条调用链）。
- **WSL 真冷启动未实测**（需 `--shutdown`，会打断常驻服务）。本机 WSL 一直是 Running，生产里几乎不触发。
- **`import ctranslate2` 5.6 s 的根因未验证**（怀疑 Defender 实时扫描；`import torch` 只 1.58 s 反证不是普遍现象）。
- **`得意黑` 在非中文 locale 的 Windows 上是否还能解析未测**（本机是中文 locale）。
- **用户字体目录与仓库两份字体文件版本不一致**：用户目录 2098640 B、仓库 2629764 B（name 表相同），渲染是否逐像素一致未比对。**建议统一用仓库那份 + fontsdir，不依赖用户装字体。**
- **多字体变体（Bold/Oblique）的族名 + subfamily 组合未测**。
- 长音频（>30 s）whisper 行为、faster-whisper 的 float32/int8_float32 基准，均未测。

## 8. 对迁移决策的总体判断

**三处未知都不构成 #150 票面担心的「迁移不可行」，但暴露出两个票面没预料到的阻塞/证伪项：**

1. **TTS 与 whisper 的 interop 路线都成立**，代价可量化且可接受（interop 税 80–180 ms/次，占 TTS 全量 100 s 的 **0.1–0.2%**、占 whisper 单段 22 s 的 **0.6–0.8%**）。票面的判断——「为 3 个轻量 CLI 让 6 个重依赖每次跨边界」——在实测上站得住：跨侧成本是**毫秒级**，真正的固定成本（`import torch` 4–9 s、TTS 权重载入、whisper 权重加载）在**两侧都一样要付**。
2. **字体预检的 Windows 替代不但存在，而且比 `fc-match` 更可靠**（0.099 ms 的文件检查 vs 一个在 Windows 上恒返回空串的移植版）。
3. **真正的迁移阻塞是 ffmpeg 构建缺 drawtext**，与三处未知无关，纯属环境问题，必须在 #151 之前解决。
4. **「whisper CPU 并行车道」是注释里的愿望**，迁移时必须显式钉 CPU，否则 s08 变成「两个 GPU 消费者」。
5. **WSL 侧 26 G 的 index-tts 是硬依赖，不能省**；但它的保留代价只有磁盘，空闲态内存占用为 0——「省内存」不构成删它的理由。
6. **TTS 是重度 GPU 消费者（单路峰值 10.3 GB / 16.3 GB）**，2 路并发就吃满 97.5% 显存。这修正了「轻量 CLI 不占卡」的隐含假设：`index-tts` 虽是 CLI，但它是本流水线里**除 ComfyUI 外最大的显存用户**，s02 必须进 GPU 串行队列，不能与 9:00 报纸管道的 Strata 或手动 ComfyUI 并跑。
