# Spike 报告：whisper 走 interop 还是 Windows 本地（票 #150 / AC2，2026-10-18）

> 票 #150 的实测记录。本票不改流水线代码；数字是 s08 字幕车道迁移决策的参数来源。
> 环境：Windows 10 22H2（10.0.19045）+ WSL2 Ubuntu（kernel 6.6.114.1），RTX 5080 16GB，32 逻辑核，RAM 127GB。
> 样本：生产真实产物 `E:\每日新中国\2026-10-17\video\每日新中国b站\流水线\daily-china-20261017\02_TTS语音\shot01.wav`（8.498503s）、shot02（9.380862s）、shot03（7.511655s），ffprobe 实测。
> 对照基准：`daily-china-20261017\_wsout\shot01..03.json`（2026-10-17 生产跑出的 openai-whisper 输出，17 段全在）。
> 测量纪律：全程 `CUDA_VISIBLE_DEVICES=""`；nvidia-smi 显存占用测量期间维持 812–946 MiB / util 0–3%（桌面基线，未被本票占用）。
> WSL 侧背景负载（测量期间常驻，未清理）：llama-server（GLM-4.7-Flash-IQ4_XS，-t 16，:8887）、mem0-server（~107% CPU）、qdrant、hermes gateway，loadavg ≈ 1.1。以下 WSL CPU% 数字是「有背景负载」条件下的。

## 结论速览

| AC | 结论 |
|---|---|
| A interop 耗时 | wsl.exe 固定开销 **0.13–0.18s（占单段 0.6–0.8%）**；turbo 强制 CPU 单段 **冷 27.96s / 热 23.35s**，3 段 21.2–23.4s（均值 22.1s） |
| A JSON 一致性 | **结构 100% 一致**：top/segment/word 键集合与顺序全同，`text` 逐字相同，时间戳最大偏差 **0.020s**，probability 最大偏差 **0.0075** → `gen_subs_from_timeline.py` **零改动** |
| B 本地 faster-whisper | 模型已下载并 sha256 校验通过；同构 JSON **能产出**（需自写 82 行转换器）；常驻进程 **4.4s/段**（int8，8 线程），逐段 CLI 进程 **13.1s/段**（import ctranslate2 占 5.6s） |
| C 对比 | 见 §3。**B 在耗时上快 4.7×，但多 1546.5 MiB 模型 + 155 MiB 包 + 自写转换器 + 一条新失败面** |
| D 择一 | **选 interop**（§4，三条理由） |
| E CPU 车道并行 | **代码注释里的前提是错的**（host-contract 未作此断言）：生产 whisper 实际跑在 GPU 上（`--device` 默认 cuda），与 H3 抢同一张 5080（§5） |

## 1. A 方案：interop（Windows 侧 `wsl -d Ubuntu -e bash -lc`）

### 1.1 跨侧固定开销（git bash 内 `time.perf_counter`，各 5 次）

| 命令 | 5 次实测（s） |
|---|---|
| `wsl -d Ubuntu -e true` | 0.132 / 0.135 / 0.134 / 0.141 / 0.141 |
| `wsl -d Ubuntu -e bash -lc 'echo hi'` | 0.149 / 0.175 / 0.176 / 0.176 / 0.169 |
| `... 'python3 -c pass'` | 0.177 / 0.165 / 0.175 / 0.195 / 0.181 |
| `... 'python3 -c "import torch"'` | 0.219 / 0.175 / 0.182 |

**wsl.exe 每次调用固定 0.13–0.18s**；`bash -lc` 比裸 `-e` 多 0.02–0.04s；热态 `import torch` 只加 ~0.03s。相对单段 21–28s 的转写耗时，跨侧开销占 **0.6%–0.8%**，可忽略。

> 口径说明：TTS 报告测得 95–121ms、本报告测得 130–180ms，**是同一量级的不同时段采样**。lead 收尾复测 9 连跑 median：`-e /usr/bin/true` 108.6ms、`-e python3 -c pass` 107.9ms、`-e bash -lc true` 108.9ms（WSL 内 spawn python3 仅 27.9ms）→ **统一按「80–180ms 区间，取 110ms 作设计值」**。

### 1.2 真实样本耗时（turbo，强制 CPU）

命令形状与 `scripts/run_pipeline.py:794` 生产命令**完全一致**，仅追加 `--device cpu` 以遵守本票禁 GPU 约束：

```bash
whisper "<wav>" --model turbo --language zh --word_timestamps True \
    --output_format json --output_dir <out> --device cpu
```

WSL 内用 `/usr/bin/time -v` 计时（脚本 `%TEMP%\wprobe\whisper_cpu.sh`），4 次总墙钟 102.2s：

| 序 | 文件 | 音频时长 | wall（s） | user（s） | sys（s） | CPU% | MaxRSS（GB） |
|---|---|---|---|---|---|---|---|
| 1（**冷**） | shot01 | 8.499 | **27.964** | 178.98 | 13.54 | 688 | 5.34 |
| 2（**热**） | shot01 | 8.499 | **23.352** | 171.60 | 9.88 | 777 | 5.35 |
| 3（热） | shot02 | 9.381 | 21.806 | 194.78 | 10.87 | 943 | 5.35 |
| 4（热） | shot03 | 7.512 | 21.218 | 169.10 | 9.67 | 842 | 5.35 |

- 冷/热差 **4.61s（16.5%）**：turbo 权重 1.62GB 从页缓存/磁盘载入 + torch 初始化。**每次 `wsl.exe` 调用都是新进程，这个代价每段都付一次**。
- 8.5s 音频 23s 墙钟 → **实时率约 2.7×**（CPU 侧 OMP 8–9 核）。
- 峰值 RSS **5.35GB**（torch + turbo + 中间张量）。
- 3 段热态均值 **22.13s**；按生产 17 段外推 **约 376s**（`_wsout` 17 个 JSON 的 mtime 跨度 255.6s ≈ 15.0s/段，见 §5.2 —— 生产走 GPU，所以更快）。

### 1.3 JSON 结构 diff（vs 生产 `_wsout`，3 段全做）

`%TEMP%\wprobe\diff2.py` 逐键比对三层：

| 项 | shot01 | shot02 | shot03 |
|---|---|---|---|
| top keys 与顺序 | `['text','segments','language']` **完全相同** | 同 | 同 |
| segments 数 | 4 / 4 | 4 / 4 | 3 / 3 |
| segment 键集合（11 键） | **全同** | 全同 | 全同 |
| word 键集合 | `['word','start','end','probability']` **全同** | 全同 | 全同 |
| `text` 逐字相同 | ✓（48 字） | ✓（55 字） | ✓（44 字） |
| 词数 | 48 / 48 | 55 / 55 | 42 / 42 |
| 逐词 start/end 最大偏差 | 0.000s | **0.020s** | 0.000s |
| 逐词 probability 最大偏差 | 0.0035 | 0.0075 | 0.0066 |
| 段级时间轴 | 相同 | 相同 | 相同 |

**结论：JSON 完全兼容，`scripts/gen_subs_from_timeline.py:20-29 load_words` 一行都不用改。** 唯一非零差异是浮点级抖动（0.02s = 50fps 的一帧）。

### 1.4 失败模式（interop 特有，全部实测）

| 场景 | 实测行为 | 危险度 |
|---|---|---|
| distro 不存在（`wsl -d NoSuchDistro`） | 从 **bash/MSYS 侧**调：rc=**127**，stderr 是 **UTF-16LE** 乱码，含 `WSL_E_DISTRO_NOT_FOUND`。**从 Windows python subprocess 调：rc=4294967295**（见 TTS 报告 §4.2；driver 是 python，生产按这个判） | 中：rc 形态随调用方变，判据要按 driver 的调用方式写 |
| **音频文件不存在** | whisper 打印 `Error opening input files: No such file or directory`，**rc=0，且不写 JSON** | **高：静默失败** |
| 输出目录不可写 | rc=**1** + `PermissionError: [Errno 13] Permission denied` traceback | 低：rc 可判 |
| 非 login shell 找不到 whisper | **本机不会发生**：`/usr/local/bin/whisper` 是 `/home/lmr/.local/bin/whisper` 的符号链接，`bash -c` 也能解析 | 低（但与 pitfalls 47 的通用结论不符，见 §6） |
| MSYS 路径转换 | git bash 把 `/mnt/e/每日新中国/...` 传给 Windows 侧 python 会变成 `...\mnt\e\ÿ...` | 中：跨侧传中文路径必须 `MSYS2_ARG_CONV_EXCL='*'` |
| WSL 整体没起 | `wsl.exe` 报 `WslRegisterDistribution failed` 或挂起 | 低：本机 WSL 有常驻服务，实测一直是 Running |

**最关键一条**：音频缺失时 **rc=0 且不产出 JSON**，而 `scripts/run_pipeline.py:796-797` 只看 rc —— 会当成成功，下游 `load_words` 返回空词表，字幕整段丢失而不报错。这条与选型无关，是**现状就存在的隐患**，建议 #151 顺手加「产物存在性」校验。

## 2. B 方案：Windows 本地 faster-whisper

### 2.0 模型获取（决策 + 实测）

- 现状：`C:\Users\LMR\.cache\huggingface\hub` 只有 `models--Systran--faster-whisper-small`（1.1GB），**turbo / large-v3 未缓存**。
- 目标仓库：faster-whisper 1.2.1 的 `_MODELS["turbo"]` = `mobiuslabsgmbh/faster-whisper-large-v3-turbo`（`faster_whisper/utils.py`）。
- **体积（HF API `?blobs=true`）**：`model.bin` **1,617,884,929 B（1542.9 MiB）** + `tokenizer.json` 2,710,337 + `vocabulary.json` 1,068,114 + `config.json` 2,263 + `preprocessor_config.json` 340 → **1,621,665,983 B ≈ 1546.5 MiB（1.51 GiB）**。
- **下载速度实测**：单连接 range 20MiB = 14.64s → **1.37 MB/s**；持续 20s 测 hf **2.18 MB/s**、hf-mirror.com 1.9 MB/s（镜像偶发 `RemoteDisconnected`）。按 1.37 MB/s 单流外推 **需约 19 分钟**。
- **决策：下载**（不下载只能写「未验证」，而 B 的耗时/CPU 是对比表的关键判据；后台跑不阻塞写作）。若失败则回退「未验证 + 以 §1.2 的 openai-whisper CPU 实测作替代判据」。
- **执行与结果（三层退避）**：
  1. `huggingface_hub.snapshot_download`：1.37 MB/s，太慢，kill。
  2. 自写 urllib 12 路分块：持续 `IncompleteRead` 重试，kill。
  3. **bash + curl 16 路分块并行（`%TEMP%\wprobe\dl_curl.sh`）：成功**。`model.bin` 1,617,884,929 B，**sha256 = `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`，与 HF LFS oid 逐字节一致**；日志 `elapsed=42s`（聚合 ≈ 38 MB/s，16 连接 × ~2.4 MB/s）。
- **两个踩坑（对 #151 有用）**：
  - 该仓库 `huggingface.co/mobiuslabsgmbh/...` 会 **302 跳到 `dropbox-dash/faster-whisper-large-v3-turbo`**。`curl` 不带 `-L` 只拿到 100 字节的 `Temporary Redirect` 文本，后续 `stat` 全报 No such file。手工下载必须 `-L`。
  - 并行分块要自己拼接 + 校验；`cat part_* > model.bin` 若中途有分块失败会**静默产出截断文件**（本票第一轮就踩到，靠 sha256 才发现）。生产化必须带校验。

### 2.1 转换代码成本（本地路线必须自写）

faster-whisper 的原生输出是**生成器 + 对象**，不是 openai-whisper 的 dict：

| 字段 | openai-whisper（`_wsout`） | faster-whisper 原生 | 转换动作 |
|---|---|---|---|
| 顶层 | `{text, segments, language}` | `(segments_generator, TranscriptionInfo)` | 必须**消费生成器**并自己拼 dict |
| segment.tokens | `list[int]`（token id） | **不暴露** | 只能填 `[]`（见 §2.4 影响） |
| segment.seek | `int`（帧偏移） | **不暴露** | 用 `int(round(start*100))` 近似 |
| segment.temperature | `float` | **不暴露** | 只能填 `0.0` |
| segment.avg_logprob / compression_ratio / no_speech_prob | 有 | 有（同名） | 直取 |
| word | `{word, start, end, probability}` | `Word(word, start, end, probability)` | 逐字段搬运 |
| 写文件 | `json.dump(result)` 无 indent | 需自己 `json.dump(..., ensure_ascii=False)` | 自己写 |

本票写的参考实现 **82 行**（`%TEMP%\wprobe\fw_transcribe.py`，单文件 CLI）。这不是大工程，但它是**必须新增并长期维护的一层**——openai-whisper 的 `WriteJSON` 是现成的，faster-whisper 没有。**这是本地路线的第一条隐性成本。**

### 2.2 耗时（turbo，int8，CPU，3 段）

| 运行形态 | load（s） | shot01 | shot02 | shot03 | 3 段均值 | 17 段外推 |
|---|---|---|---|---|---|---|
| 常驻进程，`cpu_threads=0`（ct2 自选，实测并行度 4 核） | 2.069 | 5.032 | 4.909 | 4.914 | **4.95s** | **84s** |
| 常驻进程，`cpu_threads=8` | 1.982 | 4.498 | 4.556 | 4.189 | **4.41s** | **75s** |
| 逐段独立进程（`--reuse-model` 关，模拟生产 subprocess-per-shot） | 1.98–2.05 | 6.607 | 5.470 | 5.112 | 5.73s | 97s |
| **逐段独立 CLI 端到端**（含解释器+import+加载，`fw_transcribe.py`） | — | **12.937** | **12.815** | **13.510** | **13.09s** | **223s** |

**为什么 CLI 端到端是 13s 而不是 5+2=7s？** 因为 `import ctranslate2` 单独就要 **5.6s**（`import faster_whisper` 6.0–6.2s；对照 `import torch` 只要 1.58s、`python -c pass` 0.043s）。这是本机 Windows 上 ctranslate2 的 DLL 加载开销（怀疑与 Defender 实时扫描有关，未验证）。**逐段起进程的形态下，这项固定成本占单段 43%。**

### 2.3 CPU / 内存

单段转写期间的进程 CPU 时间（`psutil.cpu_times` 前后差 / 墙钟，32 逻辑核）：

| `cpu_threads` | shot01 | shot02 | shot03 | 实际并行度 |
|---|---|---|---|---|
| 0（ct2 自选） | 393% | 398% | 397% | **≈4 核** |
| 4 | 402% | 399% | 400% | **4 核** |
| 8 | **798%** | **797%** | **793%** | **8 核** |

- 峰值 RSS **2.28–2.33GB**（int8 权重 ~1.5GB + 工作集），**是 interop CPU 路线 5.35GB 的 43%**。
- 对比 WSL whisper 强制 CPU 的 688–943%（7–9 核）：**faster-whisper 用 4 核就能跑到 4.4–5.0s/段，比 whisper 的 21–23s/段快 4.7×**。ct2 的 int8 + 更好的批处理是主因，不是线程数。
- `cpu_threads` 无法从 `WhisperModel` 对象读回（`ctranslate2.Translator` 未暴露该属性），上表的并行度是**从 `cpu_times` 反推**的：0 与 4 都稳定在 ~400%，8 稳定在 ~795%。
- 与 ComfyUI 的关系：8 线程形态会吃掉 8/32 核；4 线程形态 4/32 核。**ComfyUI 的 H3 采样是 GPU-bound，但 VAE decode / 图片编码 / 提示词处理在 CPU 上**，因此本地路线确实会和 ComfyUI 抢 CPU（详见 §5.3）。

### 2.4 JSON 一致性（vs 生产 `_wsout`，3 段）

**用生产真实函数验证**：直接 `import` `scripts/gen_subs_from_timeline.py` 的 `load_words(wsdir, sid)` 消费 faster-whisper 输出：

| 项 | shot01 | shot02 | shot03 |
|---|---|---|---|
| top keys 与顺序 | `['text','segments','language']` **全同** | 全同 | 全同 |
| segment 键集合（11 键） | **全同** | 全同 | 全同 |
| word 键集合 | `['word','start','end','probability']` **全同** | 全同 | 全同 |
| segments 数 | 4 / 4 | 4 / 4 | 3 / 3 |
| `text` 逐字相同 | ✓（48 字） | ✓（55 字） | ✓（44 字） |
| 词数 | 48 / 48 | 55 / 55 | 42 / 42 |
| 逐词**文字**差异 | **0** | **0** | **0** |
| `load_words` 三元组数 | 48 / 48 | 55 / 55 | 42 / 42 |
| 逐词 start/end 最大偏差 | 0.020s | 0.020s | 0.020s |
| 末词 end | 8.160 / 8.160 | 9.140 / 9.140 | 7.180 / 7.180 |
| 逐词 probability 最大偏差 | 0.0471 | 0.0325 | 0.0632 |
| 平均 probability | 0.985 / 0.985 | 0.969 / 0.967 | 0.947 / 0.943 |
| **segment.tokens 长度** | **23,17,13,17 → 0,0,0,0** | 24,20,18,17 → 0 | 28,17,16 → 0 |

**结论**：`load_words` 消费路径 **100% 兼容**（词数、文字、末词时间完全一致，时间偏差 0.020s = 一帧）。两个字段退化：
- `tokens` 恒为 `[]` —— 下游 `gen_subs_from_timeline.py` / `gen_precise_subs.py` 都**不读 tokens**，无影响。
- `probability` 分布整体偏移（最大 0.063）—— 下游**不读 probability**，无影响。

### 2.5 失败模式（本地路线特有）

| 场景 | 行为 | 危险度 |
|---|---|---|
| 模型未下载 | `WhisperModel("turbo")` 触发 huggingface_hub 下载，**首跑慢到不可用**（19 分钟） | 高：无人值守下表现为「卡住」 |
| 模型文件损坏/截断 | ct2 加载时报 `Invalid model file` / 段错误（未实测，属未知项） | 高：静默截断的分块下载正是本票踩过的坑 |
| `import ctranslate2` 失败 | 缺 VC++ runtime 或 DLL 冲突 → ImportError（本机正常，未验证故障态） | 中 |
| CUDA 被误开 | 若环境里 `CUDA_VISIBLE_DEVICES` 未清空且传 `device="cuda"`，会占 GPU（本票强制 `device="cpu"`） | 中：代码默认值必须写死 cpu |
| 中文路径 | Windows 侧 python 读 `E:\每日新中国\...` **正常**（本地路线反而没有跨侧编码问题） | 无 |

## 3. C 逐项对比

> **读表前必读（口径不一致的三处，别直接看「谁赢」列）**：
> 1. **A 的时间是强制 CPU 的数**。本票为守「禁占 GPU」约束，interop 侧一律 `--device cpu`，得到 22.1s/段。**生产实际是 GPU 跑 15.0s/段**（§5.2 由 mtime 反推）。所以「B 快 4.7×」是「B 常驻 vs A 强制 CPU」，不是「B 常驻 vs A 生产」。
> 2. **B 的 4.41s 是常驻进程的数**，逐段起进程的 CLI 形态是 **13.09s/段**（`import ctranslate2` 5.6s 占 43%）。driver 现在的调用形状是「一次 s08 里跑完 17 段」，常驻形态可行；但一旦要按段扇出（或 driver 重启），就退回 13.09s。
> 3. **「谁赢」列只表示该维度胜出方**，最终择一在 §4 综合三条理由（JSON 维护成本、固定开销占比、新增失败面），**不是逐维度打分求和**。时间维度确实 B 赢，但它没有赢到能抵消另外两条的程度。

| 维度 | A interop（WSL whisper） | B 本地（Windows faster-whisper） | 谁赢 |
|---|---|---|---|
| **单段耗时**（8.5s 音频，强制 CPU） | 冷 **27.96s** / 热 **23.35s**；3 段 21.2–23.4s，均值 **22.1s** | 常驻 **4.41s**（8 线程）/ 4.95s（自动）；逐段 CLI **13.09s** | **B**（快 4.7×，但见下一行） |
| **17 段总耗时外推** | **376s**（CPU 强制）／生产 GPU 实测约 255s | 常驻 **75s**；逐段 CLI **223s** | B（常驻）/ 平（逐段 CLI） |
| **跨侧/启动固定开销** | wsl.exe **0.13–0.18s（0.6–0.8%）** | `import ctranslate2` **5.6s**（逐段进程形态下占 43%） | **A** |
| **依赖体积（新增）** | **0**（WSL 已有 whisper 2.1M + torch 1.6G + triton 641M，模型缓存 5.1G 含 turbo 1.617GB） | 包 **~155 MiB**（ct2 61M + onnxruntime 41M + numpy 33M + tokenizers 7.6M + hf_hub 6.9M + av 4.6M + fw 1.5M）+ 模型 **1546.5 MiB** | **A** |
| **模型缓存位置** | `~/.cache/whisper/large-v3-turbo.pt`（1,617,941,637 B，WSL ext4） | `%TEMP%\wprobe\fw_turbo`（本票手工下载）；生产应落 `C:\Users\LMR\.cache\huggingface\hub\models--dropbox-dash--faster-whisper-large-v3-turbo` | 平 |
| **CPU 占用** | **688–943%（7–9 核）**，峰值 RSS **5.35GB** | **393–402%（4 核，自动）/ 793–798%（8 核）**，峰值 RSS **2.28–2.33GB** | **B**（省一半核、内存 43%） |
| **是否影响 GPU** | **会**——不加 `--device cpu` 时默认 cuda（§5.1），与 H3 抢 5080 | 不会（`device="cpu"` 写死） | **B** |
| **失败模式** | 音频缺失 **rc=0 静默不产 JSON**；distro 缺失 rc=127（bash 侧）/ 4294967295（python subprocess）+ UTF-16 乱码；WSL 未起需冷拉起 | 模型未下载→19 分钟静默下载；分块下载截断风险；`import` 失败 | A（失败面更少、更已知） |
| **JSON 兼容性** | **零改动**（键集合/顺序/文字/时间戳全同，最大偏差 0.020s） | **零改动**（`load_words` 实测通过；tokens 退化为 []、probability 偏移，均无人读） | **平**（都 0 改动，但 B 要新增 82 行转换器） |
| **无人值守稳定性** | 依赖 WSL 服务在跑；本机有常驻 llama-server/qdrant 所以一直是 Running；`wsl --shutdown` 后需 2–5s 冷拉起（未实测） | 纯 Windows 进程，无 WSL 依赖；但多一条「模型是否已缓存」的前置条件 | A（已有运维事实）／B（无跨界依赖）——见 §4 |

## 4. D 择一：**interop**

### 4.1 理由

1. **JSON 同构是零成本的，而本地路线要自己维护一层转换器。** interop 输出与生产 `_wsout` 的 top/segment/word 键集合与顺序、`text`、词数、末词时间**全部一致**，时间戳偏差 0.020s（一帧）。本地 faster-whisper 虽然也能产出同构 JSON，但需要新增并长期维护 82 行转换器，且 `tokens` 永久退化为 `[]`、`probability` 分布偏移 0.03–0.06 —— 现在没人读这两个字段，但这是一笔**未来会咬人的债**。
2. **跨侧固定开销只占 0.6–0.8%，而本地路线的固定开销在逐段进程形态下占 43%。** wsl.exe 0.13–0.18s vs 单段 21–28s；反过来，本地逐段 CLI 要付 `import ctranslate2` 5.6s，13.09s/段比 interop 的 22.1s/段只快 41%，远不是常驻形态的 4.7×。
3. **本地路线多 1546.5 MiB 模型 + 155 MiB 包 + 一条新的失败面。** 而且下载本身在本机实测是个坑：单连接 1.37 MB/s（19 分钟），必须 16 路并行 + sha256 校验才能安全拿到；`curl` 不带 `-L` 会拿到 302 重定向的 100 字节假文件；`cat part_*` 截断会静默产出坏模型。这些都是**新引入的运维面**，而 interop 侧 WSL 的 whisper 已经跑了整个生产周期。

### 4.2 但 interop 必须修一件事

**迁移后 `scripts/run_pipeline.py:794` 的命令必须显式加 `--device cpu`（或 `CUDA_VISIBLE_DEVICES=""`）。** 见 §5 —— 现在它默认走 cuda，与 H3 抢 5080。选 interop ≠ 保持原样。

### 4.3 WSL 侧 whisper 依赖要保留多久

- **保留到 s08 在 Windows 宿主上连续跑通 ≥ 3 个完整生产日**（含至少一次跨日、一次 ComfyUI 同时占用 GPU 的时段）。与 AC4 统一口径：**退役需单独一票，不设自动过期**。
- 之后也**不建议删**：WSL 里 `whisper` 包只有 **2.1M**，模型缓存 5.1G 里 turbo 占 1.617GB（其余 base/small/tiny/large-v3 共 3.5G 可清）。删掉省下的磁盘相对于 C 盘剩余 101G 不划算，而它是唯一一条「Windows 侧 Python 环境被改坏时」的兜底。
- **可清理的部分**：`~/.cache/whisper` 下的 `tiny.pt`(75MB)/`base.pt`(145MB)/`small.pt`(484MB) 共 704MB 与 s08 无关，可删；`large-v3.pt`(3.09GB) 生产未使用，可删。**保留 `large-v3-turbo.pt`(1.617GB)**。
- **torch/triton（1.6G + 641M）不能只按 whisper 判断**——WSL 里 llama-server、qdrant 等其它服务也在用，删之前要单独盘。

## 5. E：所谓「CPU 并行车道」——前提被证伪

### 5.1 事实：生产 whisper 实际跑在 GPU 上

`scripts/run_pipeline.py:786-787` 的 docstring 与 `:843` 注释写「whisper CPU 车道提前启动（双车道：GPU 生成 / CPU 转写）」，**但代码里没有任何 `--device` 参数**（注：`docs/host-contract.md` 全文没有「CPU 车道」字样，它只登记 `whisper | side=wsl`；这句话是 `run_pipeline.py` 注释自己的说法，本报告首版误引为 host-contract，已更正），而 openai-whisper 20250625 的默认值就是 cuda：

```
/home/lmr/.local/lib/python3.12/site-packages/whisper/transcribe.py:532
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu", ...)

/home/lmr/.local/lib/python3.12/site-packages/whisper/__init__.py:131
        device = "cuda" if torch.cuda.is_available() else "cpu"
```

WSL 内实测 `torch.cuda.is_available() = True`，`get_device_name(0) = "NVIDIA GeForce RTX 5080"`（torch 2.11.0+cu128）。**所以生产 s08 的 whisper 与 ComfyUI H3 抢的是同一张 16GB 5080。**

### 5.2 交叉验证：生产实际速度只有 GPU 才可能

- `_wsout` 17 个 JSON 的 mtime 从 1792220256.079（14:57:36）到 1792220511.650（15:01:51），跨度 **255.6s → 15.0s/段**。
- 本票强制 CPU 实测 **22.1s/段**（还是扣掉了 GPU 初始化）。若生产也走 CPU，17 段应 ≥ 376s，与 255.6s 不符。
- 反推：15.0s/段 = torch 启动(~1.6s) + CUDA init + 模型冷载 + GPU 推理 + JSON 写盘 —— 与「GPU 推理很快、启动占大头」的画像一致。

**结论：host-contract 的 services 表（`whisper | side=wsl`）本身没错，但「CPU 并行车道」这个运行假设是错的，它现在是「与 H3 争用同一张 GPU 的第三个消费者」。**

### 5.3 对 #151 的直接后果

| 若选 | 后果 |
|---|---|
| **interop 且不加 `--device cpu`** | 迁移后 s08 的 whisper 与 H3 生成**争同一张 5080**，双车道并行关系**不成立**，必须进 GPU 串行队列（或显式钉 CPU） |
| **interop + 显式 `--device cpu`** | 回到真 CPU 车道：22.1s/段 × 17 = **376s**，占 7–9/32 核、5.35GB RSS。与 GPU 生成真并行。**代价是 s08 里 whisper 部分从 ~255s 变成 ~376s（+121s）**，但因为它与 H3 并行，整体 s08 不一定变慢——取决于原来 GPU 争用是否已经让 H3 变慢 |
| **本地 faster-whisper（8 线程）** | 不碰 GPU，4.41s/段 × 17 = **75s**（常驻）或 223s（逐段 CLI），吃 8/32 核、2.3GB RSS。**但会和 ComfyUI 的 CPU 侧工作抢核**（VAE decode、图片编码、prompt 处理），也会和 WSL 里的 llama-server（`-t 16`）抢核——注意 WSL 与 Windows 共享同一批物理核 |

**最关键的判断**：无论选哪条路，**都必须显式声明 CPU**。当前代码的「CPU 车道」是注释里的愿望，不是运行时事实。这条应写进 #151 的验收项：**跑 s08 期间 `nvidia-smi` 的显存增量应来自 ComfyUI 单方，whisper 进程不应出现在 GPU 计算进程列表里。**

### 5.4 并行关系的真实代价

- interop CPU 形态（688–943%，7–9 核）与 H3 的 CPU 侧工作重叠度低（H3 主要吃 GPU），**并行关系成立**。
- 本地 8 线程形态（793–798%，8 核）与 ComfyUI CPU 侧重叠，**且因为 Windows 与 WSL 共享物理核，还会和 llama-server 的 16 线程叠加**。本机 32 逻辑核，worst case 8+16+ComfyUI 可逼近饱和 —— 这是本地路线被扣分的第二个理由（第一个是 §4.1 的 1546.5MiB）。

## 6. 未知项（诚实清单）

- **WSL 真冷启动未实测**：`wsl --shutdown` 后首次 `wsl.exe` 调用需 2–5s（业界经验值，**非本票实测**）。本机 WSL 有常驻 llama-server/qdrant/mem0，实际一直是 Running，所以生产里几乎不会触发；但若某次重启后 WSL 未自启，首个 shot 会额外付这个代价。需要 shutdown 才能测，会打断本机常驻服务，本票没做。
- **生产 GPU 路径的 whisper 单段耗时未直接测**：受本票「禁占 GPU」约束，只能用 `_wsout` mtime 跨度反推 15.0s/段。要拿真数字需占用 5080，与约束冲突。
- **faster-whisper 的 `float32` / `int8_float32` 未基准**：只测了 `int8`（ct2 4.7.1 CPU 支持 `{float32, int8, int8_float32}`）。int8 已做到 `text` 与 fp16 基线逐字相同，故未继续。
- **长音频未测**：样本都是 7.5–9.4s 的 TTS 单句。30s 以上（whisper 的窗口边界）行为、以及 VAD 相关表现未知（本票 `vad_filter=False`，与生产 openai-whisper 的默认行为对齐）。
- **17 段连续跑的稳定性/内存增长未测**：只跑了 3 段。常驻形态下 ct2 是否会随段数累积内存，未知。
- **`import ctranslate2` 5.6s 的根因未验证**：怀疑 Defender 实时扫描（`import torch` 只 1.58s 反证了不是普遍现象），但没有做排除法实验（如临时排除目录后重测）。若 #151 真要走本地路线，这 5.6s 必须先解释清楚——它决定了「逐段起进程」形态是否可用。
- **模型截断时的具体报错未测**：本票第一轮分块下载曾产出过截断文件（靠 sha256 发现），但没有故意用截断文件去加载看 ct2 报什么。§2.5 里那条标了「未实测」。
- **多段并行调用未测**：生产是逐段串行（`_whisper_thread` 里 for 循环）。若将来想并发跑 2–3 段，CPU/内存叠加行为未知。

## 7. 对后续票的参数直引

| 票 / 项 | 直接可用的参数 |
|---|---|
| #151 s08 whisper 命令 | 保留 interop：`wsl -d Ubuntu -e bash -lc 'export PATH="$HOME/.local/bin:$PATH"; CUDA_VISIBLE_DEVICES="" whisper "<wav>" --model turbo --language zh --word_timestamps True --output_format json --output_dir "<wsdir>" --device cpu'`。**`--device cpu` 与 `CUDA_VISIBLE_DEVICES=""` 两者至少其一，建议都写** |
| #151 超时口径 | 单段 CPU 实测 21.2–28.0s；生产 17 段约 376s。现有 `timeout=1800`（`run_pipeline.py:794`）充裕，`_wait_whisper` 2h 也充裕，**不必改** |
| #151 产物校验 | **新增**：whisper rc=0 **不代表产出 JSON**（音频缺失时 rc=0 且静默）。应在每段后断言 `<wsdir>/<sid>.json` 存在且 `len(segments)>0` |
| #151 跨侧调用纪律 | `MSYS2_ARG_CONV_EXCL='*'`（否则中文路径被 MSYS 改写）；`bash -lc` 保 PATH（pitfalls 47）；本机 `/usr/local/bin/whisper` 是符号链接，所以 `bash -c` 也能用，但**不要依赖这个巧合** |
| #151 GPU 校验 | s08 跑期间 `nvidia-smi --query-compute-apps` 不应出现 whisper/python 进程；显存增量应只来自 ComfyUI |
| WSL 依赖清理 | 可删 `~/.cache/whisper/{tiny,base,small,large-v3}.pt` 共 **3.79GB**（75.6M+145.3M+483.6M+3087.4M，生产只用 turbo）；**保留 `large-v3-turbo.pt` 1.617GB** 与 `whisper` 包 2.1M |
| 若将来改判本地路线 | 模型 `mobiuslabsgmbh/faster-whisper-large-v3-turbo` → 302 到 `dropbox-dash/...`，**必须 `curl -L`**；`model.bin` 1,617,884,929 B，sha256 `e76620f8…cc018da`；16 路并行分块 42s 可下完；转换器参考 `%TEMP%\wprobe\fw_transcribe.py`（82 行）；`device="cpu"` 必须写死；`cpu_threads=8` 比自动(4)快 11% |

---

**复现脚本**（全部在 `%TEMP%\wprobe\`，本票临时产物，未入库）：
`whisper_cpu.sh`（WSL 内 `/usr/bin/time -v whisper --device cpu`）、`diff2.py` / `diff_fw.py`（三层 JSON 逐键 diff）、`consume.py`（用生产 `load_words` 验证）、`fw_transcribe.py`（faster-whisper → openai-whisper 同构 JSON，82 行）、`fw_bench.py`（计时 + 峰值 RSS + CPU 采样）、`fw_cpu.py`（分线程数的 CPU 时间测量）、`dl_curl.sh`（16 路并行分块下载）。
