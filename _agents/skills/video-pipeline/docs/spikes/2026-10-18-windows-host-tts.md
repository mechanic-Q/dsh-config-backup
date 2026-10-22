# Spike 报告：TTS 经 wsl.exe 调用实测（票 #150 AC1，2026-10-18）

> 票 #150 / team task-3 的实测记录。本票不改流水线代码；以下数字是 #151 迁移 TTS 调用点的设计参数来源。
> 环境：Windows 10 22H2（10.0.19045）+ WSL2 Ubuntu（WSL 2.7.3.0，kernel 6.6.114.1-1，NAT 模式），RTX 5080 16GB，driver 595.79。
> 被测对象：`scripts/tts_batch.py`（只读，未改）+ `/home/lmr/index-tts/.venv/bin/python`（3.11.15）+ `indextts.cli_v2`。
> **GPU 使用声明**：§1–§4 的路径/并发/退出码测量全程**未跑推理**（stub 代替，`import torch` / `from indextts.infer_v2 import IndexTTS2` 只测导入开销，不建 CUDA context、不加载 .pth；测量前后 `nvidia-smi` 均为 946 / 16,303 MiB 不变）。
> **§6.2 是真实推理实测**：在收尾时的一个 GPU 空闲窗口（基线 892 / 16,303 MiB）补跑，单段 / 2 段 / 17 段冷热 / 2 路并发全部跑完并回落。这是本票唯一一次占卡，数据见 §6.2。

## 结论速览

| AC | 结论 |
|---|---|
| A 路径怎么传 | **传 `/mnt/e/...`，由 Windows 侧预转换**。Windows 路径字符串能无损穿过 wsl.exe，但 WSL 侧 `os.path.exists` 恒 False（tts_batch.py 不做翻译）。 |
| A 引号 | 用 **argv 数组**（`subprocess.run(list)`，无 shell）。绝不拼命令行字符串；一旦经 cmd.exe 反斜杠全被吃。 |
| B 并发 | **无全局锁，不串行化**：2×sleep2 顺序 3.968s / 并行 1.996s（1.99×）；4 路 3.94×。CPU 满载不影响 interop 延迟。 |
| B 每次调用开销 | **80–180ms/次（取 110ms 作设计值）**（全在 wsl.exe interop 本身）。17 次独立调用比 1 次调 17 个动作多花 **1.928s**。 |
| C GPU 争用 | **两侧同一张卡**（同 UUID / 同 PCI / 同 16303 MiB）。但 **WSL 侧看不到 Windows 进程归因**，只能看总量水位 → 互斥必须在 Windows 侧按水位判断（host-contract `:116`：>2000 MiB 即忙）。**实测 TTS 单路峰值 10,298 MiB（util 96%），2 路并发峰值 15,895/16,303 MiB（97.5%）→ 与 ComfyUI/Strata 并跑必 OOM。** |
| D 退出码 | **内部命令退出码直接透传**（0–255，含 8 位截断）。wsl.exe 自身失败 = **4294967295**（0xFFFFFFFF）+ stdout UTF-16LE。 |
| D 超时 | `subprocess.run(timeout=)` 杀 wsl.exe 后 **WSL 内无孤儿**（sleep 30 → 残留计数 0）。 |
| E 模板 | `wsl.exe -d Ubuntu --cd /home/lmr/index-tts -e env INDEXTTS2_MODEL_DIR=... <venv-python> <tts_batch.py> <args>`。调用层：stub 版 median **228.8ms**、真实脚本空跑 median **140ms**、纯 interop 层 `-e true` median **108.6ms**（区间 80–180ms）。**真实推理（§6.2）**：单段 **27.1s**、17 段全量冷 **100.1s** / 热 **95.3s**。 |
| E 模板里 `--cd` / `-e env` | **对真实 `tts_batch.py` 都不是必需的**（脚本 `:70-77` 自己 `cwd=` 并硬编码 `INDEXTTS2_MODEL_DIR`；实测四种形态全 rc=0）。保留仅作防御与兼容直调 `cli_v2`（§5.2b）。 |
| E 嵌套坑 | 模板从 Windows 起 **单层** wsl.exe，WSL 内不再调 wsl.exe → 天然规避 pitfalls #47 的进程组连坐。 |
| AC4 WSL venv | **必须保留；退役需单独一票，不设自动过期**（至少到 #151 落地 + 连续 3 个生产日）。Windows 侧零安装（`indextts` 两个 python 都 False，`where whisper` rc=1）；WSL 独占 **26G**（权重 16.2G + venv 9.9G/183 包）。`checkpoints_2_5/hf_cache` 是符号链接，迁不动。 |
| AC4 fc-match | Windows anaconda 的 `fc-match.exe` **默认格式正常、`-f` 格式串恒返回空串**（能 list 893 个字体）→ 现有 `-f '%{family}'` 写法照搬会恒判失败，不是替代品。 |
| AC4 保留代价 | **9.9G venv 空闲时驻留内存 = 0**（无进程映射它、磁盘读 0）；代价只有 9.9G 磁盘。真正常驻的是 WSL VM 本身 **23.83 GiB**（vmmem WorkingSet），且主因是 llama-server 15.8 GB 等别的服务，不是 index-tts。 |

## 1. 路径怎么传（A）

三条路线全部实测（Windows 侧 python `subprocess.run(list_argv)`，不经 shell）。样本目录：
`E:\每日新中国\2026-10-17\video\每日新中国b站\流水线\daily-china-20261017\02_TTS语音\`（17 个 `shot*.wav`，5,867,310 B 合计）。

| # | 路线 | WSL 侧收到的值 | `os.path.exists` | 耗时 | 结论 |
|---|---|---|---|---|---|
| 1 | Windows 路径原样当 argv 传给 WSL python | `E:\每日新中国\...`（LEN=78，原样） | **False** | — | 能过边界，但 WSL 不认；须 python 侧自转 |
| 1b | 同一 Windows 路径内嵌进 `bash -lc` 字符串 | 原样 | **False** | 0.125s | 同上，且引入引号风险 |
| 2 | **先算好 `/mnt/e/...` 再传** | `/mnt/e/每日新中国/...` | **True** | **0.116s** | ✓ **定案** |
| 3a | 普通 env 传变量 | `<MISSING>` | — | — | ✗ **不会进 WSL** |
| 3b | `WSLENV=<VAR>` 只桥接 | `E:\...`（不翻译） | False | — | 只搬值不转路径 |
| 3c | `WSLENV=<VAR>/p` | `/mnt/e/...`（自动翻译） | **True** | — | ✓ 可用，但引入全局 WSLENV 副作用 |

**CJK 无损**：路线 1 到达 WSL 后 `printf %s "$1" | xxd -p` = `e6af8fe697a5e696b0e4b89de59bbd62e7ab99`，与 Windows 侧 `"每日新中国".encode("utf-8").hex()` 逐字节一致。三路线 md5 同为 `39e54497cdbae70a07b1a91da261370b`。

### 1.1 引号：唯一安全写法是 argv 数组

`bash -lc` 载荷里包 Windows 路径的三种引号实测：

| 写法 | 结果 | 判定 |
|---|---|---|
| 单引号 `'E:\每日...'` | 反斜杠**保留**（字节正确），但 exists=False | 字节对、语义不对 |
| 双引号 `"E:\每日..."` | 反斜杠被 bash 吃掉，exists=False | ✗ |
| 不加引号 | 反斜杠全被吃 **且按空格分词** → `E:每日新中国2026-10-17video...` | ✗✗ 连路径分隔都没了 |

**绕过 shell 解析的写法**（实测 0.225s，含空格+CJK 全 OK）：把路径作为独立 argv 尾随，载荷里用 `"$1"`：

```python
argv = ["wsl.exe","-d","Ubuntu","-e","bash","-lc", '<cmd 用 "$1">', "_", win_path]
```

但 #151 更该直接用 §5 的模板——**根本不进 bash**，`-e` 直接 exec，连这层引号问题都不存在（实测 0.209s，同样 OK）。

嵌套单引号陷阱：`bash -lc` 载荷内再出现单引号会截断载荷（实测 `NameError: name 'x' is not defined`, rc=1）。**载荷内只能用双引号**。

### 1.2 编码：cp936 会静默吞掉 WSL 输出

Windows python 的 `text=True` 默认编码是 **cp936/gbk**（`locale.getpreferredencoding(False)=cp936`），而 WSL 输出是 UTF-8。实测：reader 线程抛 `UnicodeDecodeError('gbk' codec can't decode byte 0xbd)`，**`run() 照常返回但 `stdout=None`**——错误信息静默丢失。

**必须** `encoding="utf-8"` 或收 bytes 后手工 `.decode("utf-8","replace")`。这条对全仓子进程同样适用——本次 AST 扫描 `scripts/*.py`（**94 个文件**、28 个含子进程）实测 **51 处 `subprocess.run/Popen/check_output/call/check_call`，其中 42 处没写 `encoding`**；`run_pipeline.py` 单独看是 **3/3 全没写**（`:163` 的 `sh()`、`:1811`、`:2145`）。（`docs/host-contract.md:26` 记的「54 处里 42 处」是 #144 时点口径，不回填）

### 1.3 tts_batch.py 自己不做路径翻译（实测）

| 传入参数 | rc | 耗时 | stderr 末行 |
|---|---|---|---|
| `/mnt/e/.../03_风格化文案`（该目录在样本里不存在） | **1** | 0.169s | `FileNotFoundError: [Errno 2] No such file or directory: '/mnt/e/每日新中国/2026-10-17/video/每日新中国b站/流水线/daily-china-20261017/03_风格化文案'` |
| `E:\每日新中国\...\03_风格化文案`（Windows 形态，WSL 侧不认） | **1** | 0.148s | `FileNotFoundError: [Errno 2] No such file or directory: 'E:\\每日新中国\\2026-10-17\\...\\03_风格化文案'` |

两者都是 rc=1、都是 traceback——**从退出码无法区分"路径写错"和"路径没翻译"**，只能看 stderr 里的路径形态。所以转换责任在调用方（#151 的 Windows driver）。

`tts_batch.py:42` 用 `os.path.abspath(args.output_dir)` 拼 manifest 的 `output`：传 `/mnt` 参数 → manifest 里就是 `/mnt` 路径（自证，实测 manifest 行 `"output": "/mnt/e/.../02_TTS语音/shot01.wav"`）；传 Windows 参数 → `abspath` 在 WSL 里得到垃圾。**这也是必须预转换的原因之一**。

## 2. 并发：不串行化（B）

| 场景 | 顺序 | 并行 | 加速比 |
|---|---|---|---|
| 2× `sleep 2` | 3.968s | **1.996s** | 1.99× |
| 4× `sleep 2` | 7.992s | **2.027s** | 3.94× |
| 8× `sleep 1` | — | 1.143s | — |
| 2 路 s02 形状 stub（内含 sleep 3 + 写 2 个 wav） | 6.079s | **3.117s** | 1.95× |
| 6 路 s02 形状 stub | — | 3.695s | 全部 rc=0 |

**结论：无全局锁，多个 wsl.exe 真并行**（WSL nproc=32，MemTotal 119,515,080 kB）。

CPU 争用不传导到 interop：一个 python 死循环占满单核时，另一条 echo 延迟 5 次 `[101.1, 96.4, 101.2, 100.1, 97.2]ms`，与空闲 `[111.5, 102.2, 107.9, 97.8, 106.5]ms` 无差异。

### 2.1 单次调用开销：~80–180ms，且是纯 interop 税

| 形式 | 最小耗时（n=6） |
|---|---|
| `-e bash -lc '<cmd>'` | 94.6ms |
| `-e bash -c '<cmd>'` | 95.3ms |
| `-e <binary>` 直接 exec | 95.4ms |

三者几乎相同 → **开销全在 wsl.exe interop 本身，bash 包装可忽略**。热态 `echo hi` 5 次 `[0.143,0.151,0.138,0.146,0.149]s`（含进程启动），首次 0.154s。

**与 whisper 报告的 0.13–0.18s 是同一量级的不同时段采样**，不是矛盾。收尾复测（9 连跑 median / lo / hi）：`-e /usr/bin/true` **108.6 / 99.8 / 113.8 ms**、`-e python3 -c pass` **107.9 / 102.2 / 120.2 ms**、`-e bash -lc true` **108.9 / 103.4 / 134.7 ms**；WSL 内 spawn 一次 python3 = **27.9 ms**。→ **设计值取 110 ms**（区间 80–180 ms，随机器负载浮动）。

**扇出代价**：17 次独立 wsl.exe（每次 stat 一个 wav）= **2.064s = 121.4 ms/次**；同等工作放进 1 次 wsl.exe = 0.136s。**interop 税 1.928s**。

→ 对 #151：TTS 是**一次调用批处理全部段**（tts_batch.py 本来就是 manifest 批量路线），不要按 shot 扇出。s08 的 whisper 词级时间戳若按段扇出，17 段就是白扔 ~1.9s。

### 2.2 `--terminate` 是禁区

| 操作 | 耗时 | 后果 |
|---|---|---|
| `wsl.exe --terminate Ubuntu` | 0.070s（rc=0，stdout UTF-16LE「操作成功完成。」） | 杀掉 WSL 内全部常驻进程 |
| terminate 后首条命令 | **10.849s**（冷启） | 每次约 11s 白等 |

实测 terminate 后 WSL 内的 tmux、qdrant、9router、hermes、llama-server、comfy-mcp、chrome-devtools 全被清掉（由 `.wsl-startup.sh` 自行拉起，但中间有窗口期）。**driver 绝不可用 `--terminate` 做"清理"或"重试前重置"**。

## 3. GPU 争用（C）

### 3.1 两侧看到的是同一张卡

| 字段 | Windows `nvidia-smi` | WSL 内 `nvidia-smi` |
|---|---|---|
| index / name | 0 / NVIDIA GeForce RTX 5080 | 0 / NVIDIA GeForce RTX 5080 |
| UUID | GPU-650c43a4-f9a4-0765-b7b9-66512d60ef86 | **同** |
| PCI bus | 00000000:01:00.0 | **同** |
| memory.total | 16303 MiB | **16303 MiB** |
| driver | 595.79 | **595.79** |

WSL 侧的 nvidia-smi 实体在 `/usr/lib/wsl/lib/nvidia-smi`，GPU 经 `/dev/dxg`（`crw-rw-rw- 10,125`）直通——**不是虚拟卡，是同一块 5080 的同一份显存**。

6 次交替采样，两侧 `memory.used` / `free` / `util` 全部落在同一区间：used **872–903 MiB**、free **15075–15106 MiB**（idle 基线 ~880 MiB，后段 938–940 MiB）。采样瞬间的 2–3 MiB 差是两次查询的时间差，不是两张卡。

### 3.2 但**不能按进程归因**（关键限制）

| 查询 | Windows 侧 | WSL 侧 |
|---|---|---|
| `--query-compute-apps` | 能看到桌面进程，但多数 `used_memory` 为 `[Insufficient Permissions]` / `[N/A]` | **恒为空**（`count=0`） |

→ **WSL 内看不到 Windows 侧 ComfyUI/Strata 的进程，Windows 侧也拿不到可靠的每进程显存**。两侧都只能读**全卡总量水位**。

采样延迟（这条决定互斥检查放哪一侧）：

| 路径 | min | median |
|---|---|---|
| Windows 原生 `nvidia-smi` | 35.3ms | 46.7ms（后段样本 48.9–70.5ms） |
| 经 wsl.exe 调 `nvidia-smi` | 135.5ms | 136.2ms（后段 123.1–190.0ms） |

### 3.3 互斥口径（引用 host-contract）

host-contract `docs/host-contract.md:114` 定的是「`nvidia-smi` 互斥把关」，`:116` 给的口径是「`nvidia-smi --query-gpu=memory.used` 水位 **>2000MiB 即 GPU 忙**」。

本次实测支持这个口径，并补两点：

1. **判据只能用水位，不能按进程**（§3.2）。水位判据的固有代价：无法区分"生产 ComfyUI 在跑"和"某个残留进程占着 1.5GB"，两者都判忙 → 只会**多等**，不会误占。这个方向的保守是正确的。
2. **检查必须在 Windows 侧做**（46.7ms vs 136.2ms，省 ~90ms；更重要的是 Windows 侧能看见 WSL 看不到的桌面/服务进程——虽然也不精确，但至少 `--query-compute-apps` 非空）。

**迁移后 s02 与 ComfyUI/Strata 共用一张 5080 的互斥结论**：s02 进 WSL 前，driver 在 Windows 侧查一次水位；>2000MiB 就等（串行队列），≤2000MiB 才进。**不能靠 WSL 侧自查**——它看不到争用者。s02 与 s08 天然不重叠（s02 在流水线早期），真正的冲突面是「s02 与 9:00 报纸管道的 Strata 常驻」和「s02 与手动跑的 ComfyUI」。

idle 基线 ~880–940 MiB 说明 2000MiB 阈值有约 1.1GB 余量——够容纳 WSL 侧 nvidia-smi 自身的抖动（实测 ±30MiB）。

## 4. 失败情形与退出码（D）

### 4.1 内部退出码：直接透传，无壳层改写

| WSL 内行为 | wsl.exe rc | 备注 |
|---|---|---|
| `exit 0 / 1 / 2 / 3 / 42 / 127 / 128 / 255` | **同值** | 无改写 |
| `exit 256` | **0** | **8 位截断** |
| `exit 300` | **44** | 8 位截断（300 & 0xFF） |
| `kill -9 $$` | 9 | |
| `kill -TERM $$` | 15 | |
| python 自杀 SIGKILL | 9 | |
| `-e /usr/bin/false` | 1 | `-e` 直连同样透传 |
| `-e /bin/sh -c 'exit 7'` | 7 | |

**信号与 128+n 不可区分**：`kill -9` 与 `exit 137` 都是 rc=9/137 的小整数，driver 无法判断"是自杀还是被 OOM kill"。tts_batch.py 里 `subprocess.run(timeout=3600)` 超时会抛 Python 异常而非返回码，这条在本层不构成问题。

### 4.2 四种失败情形的可辨识性（实测矩阵）

| # | 失败 | wsl.exe rc | stderr | 可辨识？ |
|---|---|---|---|---|
| 1 | **目标脚本路径不存在**（`python3 /nonexistent/x.py`） | **2** | `python3: can't open file '/nonexistent/x.py': [Errno 2] No such file or directory` | ✓ 独有形态 |
| 2 | **venv python 不存在**，走 `-e bash -lc` | **127** | `bash: line 1: /home/lmr/nope/.venv/bin/python: No such file or directory` | ✓ 但**与 command-not-found 同为 127**，靠 stderr 文本区分 |
| 2' | **venv python 不存在**，走 `-e` 直连 | **1** | `<3>WSL (3110 - Relay) ERROR: CreateProcessCommon:818: execvpe(...) failed: No such file or directory` | ✓ `WSL (` + `CreateProcessCommon` 前缀是 relay 层独有 |
| 3 | **WSL 内 exit 3** | **3** | 空 | ✓ 小整数 + stderr 无前缀 |
| 4 | **WSL 内被 kill**（`kill -9 $$`） | **9** | 空 | ⚠ 与 `exit 9` 不可区分 |
| 5 | **wsl.exe 自身失败**（未知发行版 `-d NoSuchDistro`） | **4294967295**（0xFFFFFFFF，signed −1） | **空**；错误在 **stdout**，UTF-16LE：`不存在具有所提供名称的分发。\r\n错误代码: Wsl/Service/WSL_E_DISTRO_NOT_FOUND` | ✓ rc 值独有 |
| 5' | wsl.exe 坏参数 `--bogus-flag` | 4294967295 | 同 5（stdout UTF-16LE） | ✓ |
| 6 | **wsl.exe 本身不存在** | python 抛 `FileNotFoundError(2, '系统找不到指定的文件。')` | — | ✓ 起进程前就炸 |
| 7 | **超时** | `TimeoutExpired`（2.02s，对 sleep 10） | — | ✓ 异常类型独有；1s 后 `pgrep` 残留 0 |

### 4.3 driver 判据（可直接实现）

```python
rc = r.returncode
err = r.stderr.decode("utf-8", "replace")
if rc == 4294967295:            # 0xFFFFFFFF
    -> wsl.exe 自身失败；读 stdout 按 UTF-16LE 解码取 Wsl/Service/... 错误码
elif rc == 1 and "WSL (" in err and "CreateProcessCommon" in err:
    -> WSL relay 层 exec 失败（二进制不存在/不可执行）
elif rc == 127 and err.startswith("bash: line"):
    -> 壳内找不到文件或命令（看 err 里是 "No such file" 还是 "command not found"）
elif rc == 2 and "can't open file" in err:
    -> python 解释器在，但目标 .py 不存在
elif 0 <= rc <= 255 and "WSL (" not in err:
    -> WSL 内命令自身退出码（真实业务失败）
```

**UTF-8 vs UTF-16LE 判别**：wsl.exe 自己产生的输出（`--list`、`--list -v`、自身错误）是 **UTF-16LE**；`-e bash` 内一切输出是 **UTF-8**。判据实测可靠：`--list -v` 原始字节 `b' \x00 \x00N\x00A\x00M\x00E\x00'`（NUL 占比 0.35），`-e bash` 的 stdout NUL 占比 **0.00**。**按 NUL 字节判别**（与 pitfalls #47 一致）。

### 4.4 真实 tts_batch.py 的失败形态（非 stub，实跑）

全部用**真实脚本** `/mnt/c/Users/LMR/.agents/skills/video-pipeline/scripts/tts_batch.py`（即 §5.1 模板里的 TTS_BATCH，路径已实测可打开）+ 真实 venv python：

| 情形 | rc | 耗时 | stdout | stderr |
|---|---|---|---|---|
| `--help`（证明 /mnt/c 下的 .py 能被 WSL venv python 打开） | **0** | **0.171s** | argparse usage 全文 | 空 |
| script_dir 不存在（传错目录） | **1** | 0.169s | **空** | `Traceback ... FileNotFoundError: [Errno 2] No such file or directory: '/mnt/e/.../03_风格化文案'` |
| 全部已存在 / 超出 `--start` 范围 | **0** | **0.146s** | `无待生成段（全部已存在或超出范围）` | 空 |
| 引擎侧失败 | tts_batch 内部 `sys.exit(1)` | — | `TTS batch failed: <内部 rc>` | 尾 400 字符 |

**真实 shotNN.md 目录的 5 连跑**（`01_分段/口播台词`，17 个 shotNN.md，`--start 999` 不触发推理）：

| # | rc | 耗时 | stdout |
|---|---|---|---|
| 1 | 0 | 0.148s | `无待生成段（全部已存在或超出范围）` |
| 2 | 0 | 0.140s | 同上 |
| 3 | 0 | 0.131s | 同上 |
| 4 | 0 | 0.146s | 同上 |
| 5 | 0 | 0.137s | 同上 |

**median 0.140s / min 0.131s / max 0.148s**，rc 全 0。这条比 §5.1 的 stub 版（median 228.8ms）更快，因为少了一层 stub 脚本解释；两者都在「一次 wsl.exe + 一次 venv python 启动」的量级内。

注意 **rc=1 且 stdout 为空**是 tts_batch.py 崩溃的签名——实测 `rc=1 / stdout_len=0 / stderr_len=419 B / 0.128s`（传一个不存在的 script_dir）。driver 若只记 rc 会丢掉全部诊断，**必须同时收 stderr 并按 UTF-8 解码**。

反过来，**rc=0 时 stdout 一定有内容**（`无待生成段（全部已存在或超出范围）`），所以「rc=0 + stdout 空」本身就该报警——它意味着输出被 §1.2 的 cp936 解码吞掉了。

## 5. 调用命令模板（E）

### 5.1 定案模板（调用层 median 228.8ms / 真实推理见 §6.2）

```python
# Windows 侧 driver（#151）调 TTS 的唯一入口
import subprocess, os

WSL_EXE   = r"C:\Windows\System32\wsl.exe"
DISTRO    = "Ubuntu"
TTS_PY    = "/home/lmr/index-tts/.venv/bin/python"
TTS_CWD   = "/home/lmr/index-tts"
MODEL_DIR = "/home/lmr/index-tts/checkpoints_2_5"
TTS_BATCH = "/mnt/c/Users/LMR/.agents/skills/video-pipeline/scripts/tts_batch.py"

def to_wsl(win_path: str) -> str:
    """E:\\每日新中国\\... -> /mnt/e/每日新中国/..."""
    drive, rest = os.path.splitdrive(os.path.abspath(win_path))
    return "/mnt/" + drive[0].lower() + rest.replace("\\", "/")

def run_tts(script_dir: str, voice_ref: str, out_dir: str, timeout=7200):
    argv = [
        WSL_EXE, "-d", DISTRO,
        "--cd", TTS_CWD,                                   # 裸 python 探活必需；对 tts_batch.py 冗余但保留（§5.2b）
        "-e", "env", f"INDEXTTS2_MODEL_DIR={MODEL_DIR}",   # 对 tts_batch.py 冗余（脚本自己设）；直调 cli_v2 时必需
        TTS_PY, TTS_BATCH,
        to_wsl(script_dir), to_wsl(voice_ref), to_wsl(out_dir),
    ]
    r = subprocess.run(argv, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
```

调用层 5 连跑（stub 代替推理）：`[229.8, 228.8, 225.2, 228.7, 231.1]ms`，**median 228.8ms**，rc 全 0。真实脚本空跑（`--start 999`）median 0.140s；纯 interop 层 `-e true` median 108.6ms。

**这条 argv 形状已被真实推理验证过**（§6.2）：17 段全量冷 100.12s / 热 95.34s，产出 17 个 wav、总时长 132.342s，rc=0。

**可直接抄的一行**（在 Windows `cmd` / PowerShell 里手工验证用；生产代码用上面的 argv 数组）：

```bat
C:\Windows\System32\wsl.exe -d Ubuntu --cd /home/lmr/index-tts -e env INDEXTTS2_MODEL_DIR=/home/lmr/index-tts/checkpoints_2_5 /home/lmr/index-tts/.venv/bin/python /mnt/c/Users/LMR/.agents/skills/video-pipeline/scripts/tts_batch.py /mnt/e/每日新中国/2026-10-17/video/每日新中国b站/流水线/daily-china-20261017/01_分段/口播台词 /home/lmr/qmr_content_creator/private/voice/references/expressive-reference.wav /mnt/e/每日新中国/2026-10-17/video/每日新中国b站/流水线/daily-china-20261017/02_TTS语音
```

（这条命令会真的触发推理、占 ~10.3 GB 显存。**手工验证时先加 `--start 999`** 走空跑；要跑真推理，先确认 `nvidia-smi` 水位 <2000 MiB。）

**模板里每个路径都是实测跑通的，不是推演**：

| 路径 | 实测证据 |
|---|---|
| `TTS_BATCH = /mnt/c/Users/LMR/.agents/skills/video-pipeline/scripts/tts_batch.py` | 用上面这条 argv 实跑：`--help` → **rc=0，0.171s**，argparse usage 正常输出；真实参数形状（17 个 shotNN.md 的 `01_分段/口播台词` + `--start 999`）→ **rc=0，median 0.140s**，5 连跑全 0。**该 .py 确实被 WSL 内的 venv python 从 /mnt/c 打开并执行**。 |
| `TTS_PY = /home/lmr/index-tts/.venv/bin/python` | 同上实跑；`-c 'import sys;print(sys.version.split()[0])'` → **rc=0，0.105s，输出 `3.11.15`** |
| `TTS_CWD = /home/lmr/index-tts` | 不带它 `import indextts` → **rc=1**，`ModuleNotFoundError: No module named 'indextts'`；带上 **rc=0**（耗时 0.107–0.278s 随冷热浮动，结论看 rc 不看耗时） |
| `MODEL_DIR = /home/lmr/index-tts/checkpoints_2_5` | `-e env INDEXTTS2_MODEL_DIR=... <py> -c 'print(os.environ.get(...))'` → **rc=0，0.106s**，原样回显该路径；目录 5.2G 实测存在 |

### 5.2 每个参数为什么这么写

| 参数 | 为什么 | 实测依据 |
|---|---|---|
| `-d Ubuntu` | 本机只有 Ubuntu(Running,2) 与 docker-desktop(Stopped)，不指定会落到默认发行版 | `wsl -l -v` |
| `--cd /home/lmr/index-tts` | **对真实 `tts_batch.py` 不是必需的**（实测见下），但**对裸 `python -c 'import indextts'` 探活是必需的**：不带 → `ModuleNotFoundError: No module named 'indextts'`（**rc=1**）；带上 **rc=0**。耗时随冷热在 0.107–0.278s 浮动（本次 0.278/0.107s；lead 复验 0.109/0.108s）——**判据是 rc 与报错文本，不是耗时** | 专项测量 + lead 复验 + 下面的「必要性实测」 |
| `-e env VAR=... <binary>` | 四种传法只有这种无副作用：普通 env ✗ 不桥接；`WSLENV=VAR` ✓ 但污染全局；`WSLENV=VAR/p` ✓ 但会顺带翻译值；**`-e env` 只在本次调用内设，无 WSLENV 全局副作用**。**但对真实 `tts_batch.py` 也不是必需的**（脚本自己设） | §1 路线 3 + 四种传法对比 + 下面的「必要性实测」 |
| 不用 `bash -lc` | 省一层 shell，且**彻底消除引号问题**；`-lc` 与 `-e` 直连的启动开销无差异（94.6 vs 95.4ms） | §2.1 |
| 路径全走 `to_wsl()` | tts_batch.py 不做翻译（§1.3），且 `os.path.abspath` 会把 manifest 写成 /mnt 形态 | §1.3 |
| `capture_output` + 手工 `decode("utf-8")` | `text=True` 默认 cp936 → `stdout=None` 静默丢输出 | §1.2 |
| `timeout=7200` | 沿用 `run_pipeline.py:516` 现值（欢迎词 1200s 见 `:511`） | 全量实测 100s，70× 余量（§6.2） |

### 5.2b 实测：`--cd` 与 `-e env` 对**真实 tts_batch.py** 是否必需

§5.2 里这两项容易被误读成「不写就跑不起来」。**实测：对真实 `tts_batch.py` 两项都不是必需的**，因为脚本内部自己做了：

`scripts/tts_batch.py:70-77`（只读引用）：

```python
env = dict(os.environ)
if args.engine == "2.5":
    env["INDEXTTS2_MODEL_DIR"] = "/home/lmr/index-tts/checkpoints_2_5"
cmd = [TTS_ENV, "-m", TTS_MOD, "batch", "--batch-file", manifest, "--voice", args.voice_ref, "--engine", args.engine]
r = subprocess.run(cmd, cwd="/home/lmr/index-tts", env=env, capture_output=True, text=True, timeout=3600)
```

→ 它**自己** `cwd="/home/lmr/index-tts"`、**自己**塞 `INDEXTTS2_MODEL_DIR`（且是硬编码覆盖，外部传什么都会被盖掉）。

实测矩阵（全部 rc=0，真实脚本 + 真实 17 个 shotNN.md 目录 + `--start 999`）：

| 调用形态 | rc | 耗时 | 结论 |
|---|---|---|---|
| 带 `--cd` + 带 `-e env`（§5.1 模板） | 0 | 0.140s | 基线 |
| **不带** `--cd`、**不带** `-e env` | **0** | **0.124s** | 照样跑通 |
| 带 `-e env INDEXTTS2_MODEL_DIR=/tmp/BOGUS`（故意传垃圾） | **0** | 0.120s | 脚本覆盖掉，不受影响 |
| `--engine 2`（脚本不设该变量） | 0 | 0.142s | 走 engine 2 权重 |

内部自证（用 `-c` 复现脚本的 `subprocess.run(cwd=..., env=...)` 行为）：内层 python 打印 `os.getcwd()` = **`/home/lmr/index-tts`**、`INDEXTTS2_MODEL_DIR` = **None**（外层没传，脚本内层才设）、`import indextts` **ok**。

**那 §5.1 为什么还留着这两项？**

| 项 | 保留理由 |
|---|---|
| `--cd /home/lmr/index-tts` | ① **防御**：若 #151 之后要跑任何裸 python 探活（`-c 'import indextts'`）而不带它，会 rc=1；② tts_batch.py 的 `cwd=` 是**脚本内部**的事，外层 cwd 影响的是 `tts_batch.py` 自身的相对路径解析与 Python `sys.path`——留着让两层一致，不依赖脚本实现细节。 |
| `-e env INDEXTTS2_MODEL_DIR=...` | **纯冗余**（脚本硬编码覆盖）。留着是因为 §5.1 的模板要能同时喂给「真实 tts_batch.py」和「将来可能的直调 `indextts.cli_v2`」两种调用形态——直调时这个变量就是必需的。**如果 #151 只调 tts_batch.py，这一项可以删。** |

**给 #151 的建议**：两项都保留（成本 0，`-e env` 只是多一个 argv），但**别在代码注释里写「不设就会 fallback engine 2」**——那是 `tts_batch.py` 内部的事，不是外层调用方的责任。

### 5.3 为什么它不踩 pitfalls #47 的嵌套 wsl.exe 坑

pitfalls #47 记录的问题是：**WSL 内再调 `wsl.exe`** 拉起守护进程时，外层会话退出会终结整个进程组，`nohup` 挡不住，必须 `setsid`。

本模板的方向是**反过来**的：Windows 原生进程起**一条** `wsl.exe` → WSL 内 exec 一个 python → python 内部 `subprocess.run` 起 `indextts.cli_v2`（同在 WSL 内，不跨界）。全链**只有一层 wsl.exe，且它在最外层**：

- 不存在"WSL 会话退出连坐 WSL 内守护进程"的场景——被拉起的进程随这条 wsl.exe 一起生灭，本来就该如此；
- 不存在第二个 wsl.exe 需要 `setsid`；
- tts_batch.py 的 `subprocess.run(timeout=3600)` 是 WSL 内 python 起 WSL 内 python，不跨界。

实测佐证（§4.2 #7）：Windows 侧 `timeout` 杀掉 wsl.exe 后 1s，WSL 内 `pgrep` 残留 0——**没有孤儿**。这正是"单层、进程组自然回收"的表现，与 #47 需要 `setsid` 的嵌套场景正相反。

## 6. 附加实测（对 #151 有直接影响）

### 6.1 /mnt 与 ext4 的 I/O 差（决定中间产物放哪）

E: 走 drvfs：

| 操作 | /mnt/e（drvfs） | /tmp（ext4） | 倍数 |
|---|---|---|---|
| 读 17 个 wav（stat/read） | 0.0378s = **2.22 ms/file** | 0.0012s = **0.07 ms/file** | **~30×** |
| 写 20MB | 0.047s = **421.9 MB/s** | 0.012s = **1719.9 MB/s** | **~4×** |

第二次跑 /mnt 仍 0.0438s（页缓存已热）→ **2.2ms/file 是 drvfs 每次 open 的固定成本，不是冷读**。

对 TTS 的意义：17 段写 wav 约 5.9MB，写侧成本可忽略（~14ms）；但如果 #151 之后要按 shot 逐个读回 wav 做时长回填，2.2ms/file 会累。**能一次批量读就别按文件扇出。**

### 6.2 真实推理实测（本票唯一一次占卡）

GPU 空闲窗口（基线 **892 / 16,303 MiB**），用 §5.1 的定案模板跑真实 `tts_batch.py` + 真实 17 个 shotNN.md（`01_分段/口播台词`），输出落 `/mnt/e/.tmp_tts150/`：

| 场景 | 墙钟 | GPU 峰值 | util 峰值 | rc | 备注 |
|---|---|---|---|---|---|
| 单段（8.85s 音频） | **27.075s** | 10,291 MiB | 97% | 0 | 前 13.3s 显存不动（权重载入），13.9s 起爬升，20.5s 到顶 |
| 2 段 | **43.785s** | — | — | 0 | |
| 17 段全量（冷） | **100.12s** | 10,298 MiB | 96% | 0 | 17 个 wav 全部产出 |
| 17 段全量（热，权重已在页缓存） | **95.34s** | 10,297 MiB | 96% | 0 | 冷热差 4.78s |
| 2 路并发（各 1 段，同时起） | **40.63s** | **15,895 / 16,303 MiB（97.5%）** | 96% | 0 / 0 | 两个都成功，但显存吃满 |

17 段产出音频总时长 **132.342s**（ffprobe 逐段求和），与生产同批的 133.02s 吻合 → 输出正确。

**三条定案**：

1. **TTS 是重度 GPU 消费者**：单路峰值 **10.3 GB**（63% 显存）。它虽是"轻量 CLI"，却是本流水线里除 ComfyUI 外最大的显存用户。→ s02 必须进 **GPU 串行队列**，与 9:00 报纸管道的 Strata、手动 ComfyUI 都不能并跑。
2. **并发不安全**：2 路并发虽然都 rc=0，但峰值 **97.5%**，任何第三方占用都会 OOM。tts_batch.py 的 manifest 批量路线本身已是单进程，**不要为了提速改成多进程**。
3. **冷热差 4.78s** = 5.2G 权重载入 + torch 初始化，**每次新 wsl.exe 调用都要付**。这解释了为什么"常驻 TTS 服务"是个值得单独评估的设计（本票未验证其收益）。

### 6.2b 生产节奏（mtime 观测，与全量不同场景）

取 2026-10-17 生产跑出的 17 个 wav 的 mtime：

| 指标 | 数值 |
|---|---|
| 17 段墙钟跨度 | **68.97s** |
| 音频总时长 | **133.02s** |
| 平均段间隔 | **4.311s** |
| 墙钟/音频比 | **0.519**（约 2× 实时） |

这条 **68.97s 不是全量生成**：tts_batch.py 跳过已存在的 wav，生产该目录是分次补齐的。本次全量冷跑 **100.12s**（墙钟/音频比 0.757）。两个数字不矛盾——**增量 ~69s / 全量 ~100s**。

对比：interop 开销 ~0.11–0.23s 在两种口径下都只占 **0.1–0.3%**，所以**迁移到 Windows 主执行对 TTS 总时长几乎无损**——这条是支持 #151 迁移的直接数据。`timeout=7200` 有 70× 余量。

### 6.3 导入开销（只测导入，不建 CUDA context）

| 动作 | 3 次实测 |
|---|---|
| `venv/bin/python -c pass` | `[118, 169, 180]ms` |
| `import torch`（不建 CUDA context） | `[6276, 4282, 2817]ms` |
| `import indextts.cli_v2`（cwd=index-tts） | `[159, 178, 150]ms` |

`import indextts.cli_v2` 只要 ~160ms 且**不加载权重**（合规探活点），实测 `sys.modules` 里**没有 torch**——它是惰性导入。

真正拉起 torch 的是 `indextts.infer_v2`（`cli_v2` 在用到推理时才 import 它）：

| 动作 | 第 1 次（冷页） | 第 2 次（page cache 热） | RSS 峰值 | 磁盘读 |
|---|---|---|---|---|
| `from indextts.infer_v2 import IndexTTS2` | **8.784s** | **4.225s** | 982,788 kB / 987,164 kB（约 960 MB） | 725,311,488 B / 6,017,024 B |

→ **这是 driver 侧的性能红线**：任何「进 WSL 跑一句 python 看看环境好不好」的探活，只要摸到 `infer_v2` 就是 **4–9s**；摸到 `import torch` 是 3–6s；只有 `cli_v2` 或 `-c pass` 才是 0.1–0.2s。探活一律用 `wsl.exe -l -v`（0.056s）或 `venv/bin/python -c pass`。

### 6.4 WSL 冷启动与常驻状态

| 状态 | 首条命令耗时 |
|---|---|
| WSL 已 Running（热） | 0.098–0.154s |
| `--terminate` 后冷启 | **10.849s** |

→ driver 若需要"WSL 在线"判据，**不要 terminate 再探测**；直接 `wsl.exe -l -v`（0.056s）读 STATE 列（UTF-16LE）。

## 7. WSL 侧 venv 是否需要保留（AC4）

### 7.1 Windows 侧没有 index-tts，且不是"装一下就有"

| 探测 | 结果 |
|---|---|
| `E:\AI\index-tts` / `D:\index-tts` / `C:\Users\LMR\index-tts` / 仓库内 `index-tts` | **全部 exists=False** |
| anaconda py3.13 `importlib.util.find_spec("indextts")` | **False** |
| Python311 `find_spec("indextts")` | **False** |
| `where.exe whisper` | rc=1，**未安装** |
| `where.exe fc-match` | rc=0 → `C:\Users\LMR\anaconda3\Library\bin\fc-match.exe`（**stub，见 7.3**） |

WSL 侧的实际体量（`du -sh`）：

| 目录 | 大小 | 说明 |
|---|---|---|
| `/home/lmr/index-tts` 合计 | **26G** | |
| ├ `checkpoints_2_5` | **5.2G** | Index-TTS-2.5 权重（`gpt.pth` 3.26G + `codec.pth` 607M + `s2mel.pth` 415M + `qwen0.6bemo4-merge/`） |
| ├ `checkpoints` | **11G** | engine 2 旧权重（fallback 用） |
| └ `.venv` | **9.9G** | 183 个包：torch 2.8.0+cu128、torchaudio 2.8.0+cu128、transformers 4.52.1、librosa、omegaconf、soundfile、huggingface_hub |
| Python 版本 | 3.11.15 | |

**结论**：26G 的 WSL 独占资产（其中 16.2G 是权重、9.9G 是 cu128 版 torch 栈），Windows 侧零安装。要在 Windows 重建 = 重新下 16.2G 权重 + 装 9.9G 的 cu128 torch 栈 + 解决 `indextts` 包对 Linux 路径/符号链接的假设。**不是 #151 的活**。

### 7.2 保留结论

**必须保留；退役需要单独一票，不设自动过期。**

理由：
1. `checkpoints`（11G）是 engine 2 的 fallback 权重。`tts_batch.py:71-73` **在脚本内部**只在 `--engine 2.5` 时设 `INDEXTTS2_MODEL_DIR`；`--engine 2` 时脚本不设该变量，indextts 就落到 engine 2 权重（这是**脚本行为**，与调用方传不传环境变量无关，见 §5.2b）。这套 fallback 只有在 WSL 侧完整保留时才有意义，删了就只剩一条路。
2. `checkpoints_2_5/hf_cache` 是**符号链接** → `/home/lmr/index-tts/checkpoints/hf_cache`。跨文件系统迁移会断链。
3. 生产产物（2026-10-17 的 17 段 wav）就是这套环境跑出来的，实测节奏见 §6.2。任何"重建后再比"都要重跑推理（本次禁做）。
4. host-contract `:99` 已登记 `index-tts` 服务在 `wsl` 侧，`:19` 明确"只剩 index-tts、whisper、fc-match 三个轻量 CLI 需要 WSL"。本次实测把这个判断量化了。

**保留时长（与 whisper 侧统一口径）**：至少到 **#151 落地 + 在 Windows 宿主连续跑通 ≥3 个完整生产日**（含至少一次跨日、一次 ComfyUI 同时占 GPU 的时段）；要谈退役必须先有**单独一票**做"Windows 侧 index-tts 可行性"评估。**不设自动过期**——本机磁盘余量（C: 100.5G / E: 249.3G）不构成删除压力。在那之前删除任何一部分都属于不可逆的数据损失（16.2G 权重可重下但需要网络+时间，9.9G venv 可重建但 cu128 版本已漂移）。

### 7.3 顺带实测：Windows 侧 fc-match 的 `-f` 格式串是坏的（与 AC3 交叉验证）

anaconda 带的 `fc-match.exe` **默认格式能正常匹配，但 `-f` 格式串对所有名字返回空字符串**：

先看默认格式（正常）：

```
> fc-match "Smiley Sans"
SmileySans-Oblique-2.ttf: "Smiley Sans" "Oblique"      ← rc=0，匹配到了
```

再看 `-f` 格式串（坏）：

| 字体名 | Windows `fc-match -f %{file}` | WSL `fc-match -f %{file}` |
|---|---|---|
| Microsoft YaHei | `''`（空） | `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` |
| SimHei | `''`（空） | 同上 |
| Smiley Sans | `''`（空） | `/home/lmr/.fonts/SmileySans-Oblique.ttf` |
| Noto Sans CJK SC | `''`（空） | `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` |
| Arial | `''`（空） | `/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf` |

而 Windows 的 `fc-list.exe` 却能列出 **893** 个字体（`C:/Windows/fonts\taile.ttf: Microsoft Tai Le:style=Regular` 这种形态）——**能列、默认格式能匹配、只有 `-f` 格式串坏**。WSL 侧 `fc-list` 190 个、`/usr/share/fonts` 199M。

→ 所以 `where fc-match` 成功**不能**当作"Windows 有替代品"的证据：现有 `run_pipeline.py:1781` 恰好用的是坏掉的 `-f '%{family}'` 形式，照搬会**恒判失败**（然后把字体静默换成回退值）。这条给 AC3 的结论做交叉验证（细节归 task-2 的 `2026-10-18-windows-host-font.md`）。

### 7.4 「保留 WSL venv」的实际代价（内存 / 磁盘 / 冷加载，Windows 侧实测）

**问题**：9.9G 的 venv 是常驻内存，还是只在调用时加载？WSL 不 terminate 时它到底占多少内存？

**答：空闲时占 0 内存，只在调用瞬间加载；真正常驻的是 WSL VM 本身，而主因是别的服务。**

#### (1) 谁在常驻——WSL 内 RSS 分解

| 进程 | RSS |
|---|---|
| llama-server | **15,833 MB** |
| python（非 index-tts） | 1,603 MB |
| python（非 index-tts） | 867 MB |
| qdrant | 841 MB |
| hermes | 356 MB |
| node / chrome-devtools / next-server / comfy-mcp | 253 / 182 / 126 / 72 MB |
| **全部进程 RSS 总和** | **20,932 MB** |
| pgrep 查 indextts / tts_batch / index-tts 关键词 | **0 条** |
| /proc/*/maps 里映射 /home/lmr/index-tts 的进程数 | **0** |

最后两行是关键：**没有任何进程映射 `/home/lmr/index-tts` 下的文件**——venv 与权重在空闲态完全不在内存里，只在磁盘上。

Windows 侧看这台 VM（`Get-Process vmmem`，实测 1.143s）：

| 指标 | 值 |
|---|---|
| `WorkingSet64` | **25,599,700,992 B（23.83 GiB）** |
| `PrivateMemorySize64` | **26,342,207,488 B（24.53 GiB）** |
| WSL 内 `free -m` | total 116,713 / used 5,622 / free 94,189 / buff/cache 17,944 / available 111,091 |

→ 23.83 GiB 里 RSS 只解释 20.9 GB，其余是内核 + page cache（17.9 GB buff/cache）。**这些没有一字节属于 index-tts。**

#### (2) 只在调用时加载：venv python 的 RSS 与磁盘读量

| 动作 | 耗时 | 峰值 RSS | 实际磁盘读 |
|---|---|---|---|
| venv/bin/python -c pass | 0.117s | **13,940 kB（13.6 MB）** | — |
| import indextts.cli_v2（cwd=index-tts） | **0.061s** | **17,300 kB（16.9 MB）** | **499,712 B（488 KB）** |
| from indextts.infer_v2 import IndexTTS2（第 1 次，冷页） | **8.784s** | **982,788 kB（960 MB）** | **725,311,488 B（692 MB）** |
| 同上（第 2 次，page cache 已热） | **4.225s** | 987,164 kB（964 MB） | **6,017,024 B（5.7 MB）** |

读法：
- cli_v2 **不 import torch**（实测 sys.modules 里没有 torch，RSS 只 17 MB）——所以 §6.3 那个 160ms 的「合规探活点」确实是零成本探活。
- 真正拉起 torch 的是 indextts.infer_v2：RSS 直接到 **960 MB**，冷页要从盘上读 **692 MB**（这就是 8.8s 的来源），热了之后只读 5.7 MB、4.2s 是纯 import/初始化。
- **注意**：这条 8.8s / 4.2s 只是 import，**不含加载 5.2G 权重**（本次禁跑推理）。所以生产单次 TTS 的真实首包延迟 = 4.2s（热）+ 权重加载（未测，见 §8）。

#### (3) 跑一次真实形状 tts_batch 前后的内存抖动

用真实参数形状跑 `tts_batch.py --start 999`（不触发推理，rc=0，0.156s）：

| 采样点 | WSL used | vmmem WorkingSet | GPU used / total |
|---|---|---|---|
| 跑之前 | 5,857 MB | 25,578,848,256 B | 946 / 16,303 MiB |
| 跑之后立刻 | 6,066 MB（+209） | 26,459,176,960 B（+880 MB） | 946 / 16,303 MiB |
| 静置 5s | 5,728 MB | — | — |
| 静置 10s | 5,612 MB | — | — |

**+880 MB 在 10s 内基本回落**（残余是 page cache，不是进程）。同时 GPU 显存前后**完全不变（946 MiB）**——这是「这条空跑命令不建 CUDA context」的自证（真实推理的占卡情况见 §6.2）。

#### (4) 为什么它不会自己归还：`.wslconfig` 实测内容

`C:\Users\LMR\.wslconfig`（实测存在，全文）：

```ini
[wsl2]
firewall=false
autoProxy=true
memory=116GB
vmIdleTimeout=-1
swap=0
localhostForwarding=true
virtiofs=true
[experimental]
sparseVhd=true
```

`vmIdleTimeout=-1` = **永不因空闲关停 VM**。所以「WSL 不 terminate 时的常驻内存」是一个**固定 23.8 GiB 的基线**，与是否保留 index-tts **无关**；保留 venv 只多占 **9.9G 磁盘**，内存增量在空闲态是 **0**，调用时峰值约 **1 GB**（960 MB RSS + page cache，10s 内回落）。

→ **AC4 的保留代价结论**：9.9G 磁盘 + 单次调用 4.2s（热）/ 8.8s（冷页）导入开销 + 调用瞬间约 1 GB 内存。**没有「常驻内存」这项代价**，不需要为了省内存而删 venv。

## 8. 未知项（诚实清单）

- **TTS 真实推理已在收尾时补跑**（§6.2，GPU 空闲窗口），所以本报告的核心耗时数字都是实测值，不再是外推。仍存的未知：
  - **17 段连续跑的内存增长未测**（只测了峰值与回落）；**>30s 长音频未测**（生产单段 7–9s）。
  - **权重加载耗时未单独拆出**：冷/热全量差 4.78s 是「5.2G 权重载入 + torch 初始化」的合计。
  - **3 路以上并发的失败形态未测**：只测了 2 路（峰值 97.5%），已足以判定不能与 ComfyUI 并跑。
- **多路并发 TTS 已验证"能跑但吃满显存"**：2 路并发两个都 rc=0，峰值 15,895/16,303 MiB（97.5%）。→ **#151 不要并发跑两条 TTS**——不是"未验证"，是**已验证会吃满**。
- `/tmp/tts_batch_manifest.jsonl` 是 tts_batch.py 的**固定路径**（`scripts/tts_batch.py:64`），多进程并发会互相覆盖。本次 2 路并发用的是不同 in/out 目录，**仍然共享这个 manifest 路径**——没炸是运气（两次写间隔够开），代码层面确定存在隐患。
- WSL 侧 `--query-compute-apps` 恒空的原因未深究（推测是 WSLg GPU 半虚拟化不暴露进程表）。归因限制本身已实测确认。
- 生产目录 `03_风格化文案/文案` 在 2026-10-17 样本里**不存在**（实际结构是 `00_口播稿 / 01_分段 / 02_TTS语音 / 06_H3prompt / 07_成片 / 08_字幕 / 09_封面`），`run_pipeline.py:492`（欢迎词）与 `:515`（正文）用的是 `01_分段/口播台词`。
- **本次已用真实 shotNN.md 目录跑通调用链**（`2026-10-17/.../01_分段/口播台词`，17 个 shotNN.md，`--start 999` 全跳过），rc=0、median 0.140s。**真实生成一批 17 段已实测**（§6.2）：全量冷 100.12s / 热 95.34s，产出 17 个 wav、总时长 132.342s，rc=0。生产 mtime 的 68.97s 是「部分段已存在被跳过」的增量场景，两者不矛盾。
- `indextts.infer_v2` 的 import 已实测（8.8s 冷 / 4.2s 热，RSS 960 MB），但**权重加载与显存占用未测**——§7.4 的「调用瞬间约 1 GB 内存」只是 import 层，真实推理时 venv 侧 RSS 与 GPU 显存都会更高。

## 9. 对 #151 实现的设计后果

| #151 要做的事 | 直接可用的结论 |
|---|---|
| s02 调用点改写（`run_pipeline.py:511` 欢迎词 / `:516` 正文） | 换成 §5.1 的 `run_tts()`：`wsl.exe -d Ubuntu --cd /home/lmr/index-tts -e env INDEXTTS2_MODEL_DIR=... <venv-python> <tts_batch.py> <to_wsl(...)>`。**注意 §5.2b**：`--cd`/`-e env` 对真实 tts_batch.py 都非必需（脚本自设 cwd 与变量，实测 4 种形态全 rc=0）；保留是防御 + 兼容将来直调 cli_v2，**别在注释里写「不设就 fallback engine 2」** |
| 路径转换 | 新增/复用 host-contract `:187` 的 converter（`^/mnt/([a-zA-Z])/(.*)$` 反向）。**转换责任在 Windows driver**，tts_batch.py 不改（只读） |
| 子进程编码 | 全仓 `scripts/*.py` 实测 **42/51 处**子进程没写 `encoding`（94 个文件里 28 个含子进程；`run_pipeline.py` 3/3），统一补 `encoding="utf-8"` 或走「utf-8→cp936→replace」三段解码（§1.2；否则 wsl.exe 的 stdout 会静默变 None） |
| 错误判据 | 用 §4.3 的五分支；**rc=4294967295 = wsl.exe 自身故障**，不是业务失败，应走"环境异常"而非"重试 1 次" |
| 超时 | `timeout=7200` 保留（全量实测 100s，70× 余量）；`timeout` 触发时 wsl.exe 被 kill 且 WSL 内无孤儿（§4.2 #7），可直接重试 |
| GPU 互斥 | s02 进 WSL 前在 **Windows 侧**查 `nvidia-smi --query-gpu=memory.used`，>2000MiB 即等（host-contract `:116`）。**不要**在 WSL 侧自查（看不到争用者，§3.2）。实测 TTS 单路峰值 **10,298 MiB**，这个阈值同时保护 TTS 不被挤爆 |
| 并发 | 不要按 shot 扇出（interop 税 ~110ms/次）；tts_batch.py 的 manifest 批量路线正确。**也不要并发跑两条 TTS**——实测 2 路峰值 15,895/16,303 MiB（97.5%），§6.2 |
| `--terminate` | **禁用**。需要重置就用别的办法；terminate 会杀掉 WSL 内全部常驻服务并引入 10.8s 冷启 |
| WSL venv 保留 | **必须保留**（AC4 结论）：index-tts 只有 WSL 侧有（5.2G 权重 + 3.11.15 venv），Windows 侧无替代。保留时长 = **#151 落地 + 连续 ≥3 个完整生产日**，退役需单独一票 |
| 保留代价（不删的理由更充分了） | 空闲态**内存 0**（`/proc/*/maps` 映射 index-tts 的进程数 = 0），只有 9.9G 磁盘；调用时 RSS 峰值 960 MB（import infer_v2）且 10s 内回落。**「省内存」不能成为删 venv 的理由**（§7.4） |
| TTS 首包延迟预算 | 每次调用固定付 **import + 权重载入**：实测冷热全量差 **4.78s**，单段端到端 **27.1s**（8.85s 音频）。这笔开销与 wsl.exe interop（~110ms）差两个量级。若 #151 想做「TTS 常驻服务」来省这笔钱，那是另一个设计（本报告未验证其收益） |
| 探活 | `wsl.exe -l -v`（0.056s，读 UTF-16LE 的 STATE 列）。**不要**用 import torch 探活（3–6s） |
