# 2026-10-17 期复盘：driver 收尾 code=4（飞书 token 失效）与 s08 判读全灭

> 期次：daily-china-20261017 · 处理日期 2026-10-18
> 故障票：#137 · 关联 #123（vision 自愈阶梯 WSL 侧不可用）· 坑位：references/pitfalls.md 第 57 条

## 一、结果与时间线

整条流水线跑到 s12 全部完成，**唯独最后一步 s13 发布没有执行**，收尾 `_flush_exit(4)`。

| 时刻 | 事件 |
|---|---|
| 14:53 | driver 启动（slug `…/2026-10-17/video/每日新中国b站/流水线/daily-china-20261017`） |
| 14:53–15:43 | s01→s12 全通：s08 2071.3s（烧卡 17 段 / 判读 17 段全失败）、s10 字幕 31.8s、s11 封面 462.2s |
| 15:43:25 | 飞书审核包 8/8 发送成功，开始轮询确认门（timeout 6h） |
| 21:59:53† | 起轮询后约 6h16m 内无人回复可判；token 在此之前已被服务端判无效（反推：缓存 exp=1792248923 对应 20:55:23 取到的 token，却在 21:59:53 起被连续拒绝） |
| 22:03:17 | `/open-apis/im/v1/messages` 连续 20 次 `code=99991663 Invalid access token for authorization` → 门判 error → code=4 退出 |
| 430.3 min | 全流程总时长；`10_发布/` 从未生成 |

† 时钟来自 driver 台账与 /tmp token 缓存文件交叉推算。

## 二、根因

### 1. 飞书 token 被判无效后不自愈（终局直接原因，P0）

`scripts/feishu_gate.py`：

- `_api()` 每次请求只带当前缓存的 `tenant_access_token`，**没有「鉴权失败 → 重取 → 重试」路径**；
- `latest_user_texts()` 收到 `code != 0` 直接 `raise RuntimeError`；
- `gate_confirm()` 轮询里只累加 `fail_streak`，到 20 次就把整扇门判 `error`（`_ACTION["error"]="abort"` → driver 叫人 + 硬退出）。

**为什么这事会致命**：确认门是唯一不可跳过的人工审核门，token 失效等价于「人永远判不了」。而 token 被服务端提前判无效本身不是我们能控制的（expire=7200s，实测却在签发后约 65 分钟开始被拒，触发源未定位）——**能控制的只有自愈**。

顺带两处可观测性缺陷：

- 轮询失败**只在第 20 次落一行日志**，65 分钟静默期里日志完全空白，事后无法定位失效起点与错误码；
- 放弃条件注释写「约 10 分钟连续失败」，实现却是 20 × poll_s(10s) ≈ **3.3 分钟**。

### 2. 显存闸口径错：拿整卡水位判 Strata 泄漏（P1）

`scripts/strata_window.py`：`stop()` 用 `vram_used_mib()`（nvidia-smi 的**整卡** `memory.used`）判断「显存是否回落 < `VRAM_USED_MAX`=4000 MiB」。本机 ComfyUI 常驻约 7.5GB，于是**一次正常关窗被判失败**：

```
RuntimeError: Strata 停止后显存未回落（used=7509 MiB ≥ 4000）——不放行烧卡
```

driver 重试后走 `already-off` 早退分支，而该分支**整段跳过显存检查** → 无条件放行。所以闸既误报（正常关窗判失败）又漏报（真泄漏走 already-off 时查不出来）。语义上闸该问的是「我们杀掉的那些 pid 还占不占显存」，与别的进程无关。

### 3. 判读不短路：服务离线也逐段空转（P1）

`scripts/run_pipeline.py`：`_check_frames()` 首行调 `self._ensure_vision()` 却**丢弃返回值、无条件继续**；每段每模型 2 次、每次 `urllib_req(timeout=180)`。服务已确定离线时，17 段仍逐段重试，s08 因此多耗约 14 分钟。`_ensure_vision()` 本身也没有返回值契约（三条 return 路径只有一条返回 dict）。

### 4. vision 自愈阶梯在 WSL 侧从不可用（P0，与 #123 同源，本票收尾）

`scripts/vision_server.py` 四层叠加，任一层都足以让「拉起」这一级失效：

1. `VISION_HOME = r"E:\\AI\\LLM\\vision-judge"`，WSL 里 `os.path.exists(BIN)` 恒 False → 永远报「缺文件」（**即使 exe 存在**）；
2. `_ps_path()` 用 `"/mnt/c" + cand[2:]` 拼出 `/mnt/c\\Windows/System32/WindowsPowerShell/v1.0/powershell.exe`（反斜杠没换）→ `subprocess` 必 FileNotFoundError；
3. `subprocess.run(..., text=True)` 用 UTF-8 解 `powershell.exe` 的 GBK/936 输出 → `'utf-8' codec can't decode byte 0xb0 in position 20`；
4. `-File` 传的是 `/mnt/c/...ps1`，而 **WSL interop 不翻译命令行里的路径参数** → powershell 报 `-File 形式参数的实际参数"…"不存在`。

后果：`_ensure_vision` 的「探活→拉起→降级」三级阶梯，第二级在生产环境永不生效，探活失败等于直接降级（10-13 期 8/19 段 503 即同源）。

### 5. 劣化但不阻塞（遗留）

- Jev 形态校准 `T=40.4`，主判原始分布近独热（判别弱）——已有日志预警，真值增长后重跑 `jev_calibrate` 收敛；
- `references/clip-probe.np` 缺失 → clip 兜底层降级为单路 VLM，已有日志「兜底层缺席」。

## 三、修法

| 文件 | 改动 |
|---|---|
| `scripts/feishu_gate.py` | 新增 `AUTH_ERR_CODES={99991663}`、`POLL_FAIL_MAX=20`；`token(force=True)` 清内存缓存**并删磁盘 TOKEN_CACHE**；`_api()` 双发循环（命中鉴权码 → 记日志 → 强制重取 → 重试一次）；错误文案按 code 分流；轮询**每次失败都落日志**（含连续秒数） |
| `scripts/strata_window.py` | 新增 `vram_pids_mib(pids)`（`nvidia-smi --query-compute-apps=pid,used_memory`）；关窗闸改为「被杀 pid 是否仍占显存」，别家进程占卡按放行并记日志；查询失败沿用「未知→放行」 |
| `scripts/run_pipeline.py` | `_ensure_vision` 返回 bool；`_check_frames` 离线即整批 `judge_failed` + `skipped=vision_offline` 并 `return [], []`（不抽帧不重试）；批内首个拒连（Connection refused/timed out/Errno 110/111）后置 `dead` 事件，后续段跳过重试 |
| `scripts/vision_server.py` | 新增 `_is_wsl()`/`_wsl_path()`；存在性检查走翻译路径；`_ps_path()` 双候选取存在者；powershell 输出统一 `_decode_out()`（utf-8→gbk→replace）；`-File` 恒传 Windows 路径形态 ps1 |
| `scripts/test_strata_window.py` | 场景 10 拆两子场景：自身残留 → RuntimeError；别家占卡 → 放行 |

### 红/绿回路（不是「看着对」，是先红后绿）

```
# 修前
decision: {action: abort, status: error, reply: 历史消息拉取失败 code=99991663 …}  polls=20 token_fetches=1
# 修后
decision: {action: pass,  status: pass,  reply: 通过}                              polls=4  token_fetches=2
[gate] 接口 /open-apis/im/v1/messages 返回 code=99991663（token 失效）→ 重取 tenant_access_token 后重试一次
```

## 四、验收（本机 2026-10-18 16:05 实测）

| 检查 | 结果 |
|---|---|
| `test_feishu_gate.py` | 全部通过（含 2c 连续失败 20 次 → error） |
| `test_strata_window.py` | 全部通过（含场景 10 两子场景） |
| `test_run_pipeline.py` / `test_vision_driver.py` / `test_vision_chain.py` | 全部通过 / ALL PASS |
| strata 三形态 | 10-17 实况（7509 MiB 别家占卡）→ `closed` 放行；真泄漏（pid 仍占 12GB）→ RuntimeError；离线 → `already-off` |
| 判读短路 | 离线 → `return=[]/[] sh_called=0 skipped=vision_offline`；在线 → 照旧进抽帧 |
| vision 拉起 | `os.path.exists(/mnt/e/AI/LLM/vision-judge/bin/llama-server.exe)=True`、`_ps_path()` 命中、`launch()` 返回 True |
| `run_pipeline.py --help` | 正常输出 usage |

## 五、教训

1. **外部依赖的错误码必须有自愈路径**。飞书 token 提前失效是服务端行为，我们只能保证「判无效即重取重试」；没有这条路径，任何 6 小时的门都可能白等。
2. **静默期要靠日志切开**。65 分钟只落 1 行日志，等于把根因藏起来；每次失败都记（含连续秒数）是廉价且必要的。
3. **闸的判据要问对问题**。显存闸问的是「我的进程退没退」，不是「整卡还剩多少」；拿整卡水位等于把常驻邻居算成自己的账。
4. **WSL ↔ Windows 三件套**：路径存在性检查要翻译、命令行路径参数 interop 不翻译、GBK 输出必须显式解码。三者都是「看着应该对」的地方。
5. **失败要短路**。服务已判定离线时逐段重试没有任何信息增益，只有 14 分钟的空转。
