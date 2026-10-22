# Spike 报告：Strata 事实项实测（票 #83，2026-10-12）

> 票 #83 / spec #82 的实测记录。本票不改流水线代码；以下数字是 #84–#87 的设计参数来源。
> 环境：Windows 10 22H2（10.0.19045）+ WSL2 Ubuntu（kernel 6.6.114.1，NAT 模式），RTX 5080 16GB，RAM 127GB。
> Strata：engine 0.1.42，qwen3.8-flash-next-iq3_s，context 65536，kv int8，权重 83.6GB（SSD+GPU 混合，expert-cache auto）。

## 结论速览

| AC | 结论 |
|---|---|
| WSL→Windows 连通 | **主机 IP 定案**（`--host 0.0.0.0` + WSL 网关 IP，防火墙默认放行） |
| 冷启动 vs 300s | 热缓存 18.2s / 温缓存 73.7s+首补全 70.3s；**300s 口径保留但须覆盖"就绪后首个慢请求"** |
| VRAM 共存 | 空闲 15.3GiB / 16.3GiB（94%）；**冲突**，走分段起停 |
| 关思维链方言 | `chat_template_kwargs: {"enable_thinking": false}` 定案；**缺 model 不报错（spec 假设被证伪）** |
| 跨界起停 | powershell.exe 拉起 ✓（两跑）、taskkill /F /T 停止 ✓（VRAM 回 466MiB 基线） |

## 1. 连通方式定案：主机 IP

三选一实测：

- **mirrored networking**：直接出局。需要 Windows 11 22H2+，本机 Win10 19045 不支持，无测试意义。
- **netsh portproxy**：无管理员提权即失败，实测报错「请求的操作需要提升(作为管理员运行)」。无人值守 driver 不能依赖一次性提权，出局。
- **Windows 主机 IP**：✓ 定案。server.py 支持 `--host`（默认 127.0.0.1，无管理员要求）；加 `--host 0.0.0.0` 后 WSL 经网关 IP 直连成功，**Windows 防火墙未拦截，无需任何防火墙规则**。

定案理由：免提权、配置单点落在启动命令（spec #82 已规划的 `STRATA_START_CMD` env 天然承载）、WSL 侧网关 IP 可动态发现（`ip route show default`）不怕 NAT 子网变化。

安全知悉：`0.0.0.0` 会向局域网暴露无鉴权 LLM 端点。家用网络可接受；如需收紧，后续可加作用域限定的防火墙规则（需一次性提权）或绑定 vEthernet 适配器 IP（缺点：地址随子网漂移）。

实测基准路径（WSL 内）：

```bash
GW=$(ip route show default | awk '/default/ {print $3; exit}')   # 172.25.176.1（动态）
curl http://$GW:8080/v1/chat/completions ...
```

## 2. 加载时长 vs 300s 超时口径

两次实测（同一启动命令，WSL 计时）：

| 样本 | 页缓存状态 | /v1/models 200 | 首个补全完成 | 全程 |
|---|---|---|---|---|
| 1（紧随基准跑后） | 热 | **18.2s** | +0.7s | 18.9s |
| 2（停止 3 分钟后） | 部分逐出 | **73.7s** | +70.3s | **144.1s** |

两个对 #84–#86 有直接设计后果的现象：

1. **就绪 ≠ 满速**。服务器 /v1/models 返回 200 后，首个真实请求仍可能慢一个数量级：样本 2 中专家缓存全冷，日志显示降级模式（`prefill: the CPU share is off for this run: the host could not give the activation buffer (RAM is short)`），前两个请求仅 **1.3–1.7 tok/s**（专家逐 token 走 PCIe 流式），随请求升温，缓存命中 45.6%→68.9%→（热态）85–88% 后回到满速。
2. **RAM 短缺降级是真实风险**：127GB RAM 装 83.6GB 权重，页缓存被逐后启动会触发该降级。

**结论**：300s 超时口径保留，但判据必须两层——① `/v1/models` 轮询到 200（样本 2 用了 73.7s，真冷启动只会更慢，仍是 300s 内但余量不是 18 倍那种假象）；② 就绪后**首个补全请求配长超时或先发一两个预热 ping**（样本 2 首补全耗时 70.3s）。两层都失败才走叫人。

## 3. VRAM 与 H3 生图共存性：冲突

| 状态 | VRAM（nvidia-smi，全卡 16303 MiB） |
|---|---|
| Strata 空闲（已加载） | **15311 MiB（94%）** |
| Strata 生成中 | 15620–15815 MiB |
| taskkill 停止后 | 466 MiB（桌面基线） |

权重+KV 常驻即占满 94%，空闲不释放；生图需整卡。**结论：冲突**——spec #82 的窗口纪律走回退分支：s04 入口拉起 → s08 生图前 taskkill 停 → s11 入口再拉起 → 流水线尾部停（单条视频最多两次加载）。按样本 1/2 数据，两次加载各约 20s–150s，可接受。

## 4. 请求方言定案

实测矩阵（全部经 WSL→主机 IP 真连，`temperature 0`）：

| # | 请求变体 | HTTP | reasoning | content | 结论 |
|---|---|---|---|---|---|
| T0 | 裸请求 | 200 | 113ch | `4` | **默认开思维链**（llama.cpp 分离到 `reasoning_content`，`content` 可为 null） |
| T1 | `chat_template_kwargs: {"enable_thinking": false}` | 200 | **0ch** | `4` | **✓ 关思维链定案写法** |
| T2 | 未知顶层字段 `totally_unknown_field` | 200 | 有 | — | **未知字段容忍**（忽略） |
| T3 | `chat_template_kwargs` 传垃圾值 | 200 | 有 | — | 容忍（值被忽略） |
| T4 | **缺 `model` 字段** | 200 | 有 | — | **不报错**，server 自动补默认名 |
| T5 | 9router 风格 `thinking: {"type":"disabled"}` | 200 | 有 | — | 容忍但**无效**（按未知字段忽略，关不掉思维链） |
| T6 | prompt 内 `/no_think` 软开关 | 200 | 196ch | `4` | **无效**（该模型不吃这个开关） |
| T7 | `model: ""` | 200 | 有 | — | 容忍 |

对 spec #82 的两处修正：

- spec 写「缺 model 字段是否报错」待验证——**实测不报错**（T4/T7）。#85 的请求红线测试相应改写：缺 model 断言"容忍且返回 server 默认模型名"，不断言 400。
- 关思维链只有 `chat_template_kwargs.enable_thinking:false` 一种有效写法；9router `thinking` 字段与 `/no_think` 均无效。「禁 9router thinking 字段」红线保留为风格红线（发了也不炸，但关不掉）。

吞吐参考（同机并行基准跑的日志数字，热态）：生成 **148–159 tok/s**（2372-token 输出，spec 解码 draft 接受率 ~96%），prompt 处理 540–595 tok/s；KV 流式 99.9% 命中 VRAM。

## 5. 跨界起停实测

- **拉起**（WSL 内，两跑均成功）：

```bash
powershell.exe -NoProfile -Command "Start-Process -WindowStyle Hidden \
  -FilePath 'E:\AI\LLM\Strata\.venv\Scripts\python.exe' \
  -ArgumentList 'E:\AI\LLM\Strata\serve\server.py','--engine','strata',\
'--config','E:\AI\LLM\Strata\strata-iq3_s.json','--port','8080','--host','0.0.0.0'"
```

  （= 官方 `run-iq3_s.bat` 等价命令 + `--host 0.0.0.0`，去掉 `--open` 免浏览器弹窗。`STRATA_START_CMD` env 即承载此串。）

- **停止**（WSL 内）：`netstat.exe -ano` 取 8080 监听 PID → `taskkill.exe /F /T /PID <pid>`，树杀连带引擎子进程。验证：监听消失、`strata.exe` 进程消失、WSL curl 连接拒绝、VRAM 回 466MiB 基线。

可复现脚本：`scripts/spike-strata-start.sh`、`scripts/spike-strata-stop.sh`、`scripts/spike-strata-dialect.sh`。

## 6. 未知项（诚实清单）

- **真冷启动**（主机重启后页缓存全空）未实测——需要重启才能测。已知两点外推：权重 83.6GB 需从 SSD 全量读入，加载时间 ≥ 样本 2 的 73.7s；首请求降级慢速几乎必然出现。300s 口径 + 两层判据覆盖该场景，但 #84 联调时若首次真跑贴线，应先怀疑此项。
- 防火墙行为未在其他网络配置下复验（当前「未拦截」是本机 WSL vSwitch 默认放行，若用户改动网络配置文件需复查）。
- 多请求并发/排队行为未测（driver 是串行调用，无关紧要）。

## 7. 对后续票的参数直引

| 票 | 直接可用的参数 |
|---|---|
| #84 生命周期 | 探活=`GET /v1/models` 200；拉起=§5 powershell 串；超时=300s + 首请求长超时/预热；停止=`taskkill /F /T /PID`（PID 取自 netstat 8080 监听） |
| #85 方言红线 | 关思维链=§4 T1 写法；缺 model=容忍（勿断言报错）；未知字段=容忍；禁 `thinking` 字段保留为风格红线 |
| #86 窗口纪律 | 冲突分支：s08 前/s11 后起停，单视频两次加载，每次 20–150s |
| STRATA_URL | `http://$(ip route show default 的网关):8080/v1`（WSL 侧动态发现，勿写死 IP） |
