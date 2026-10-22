# ADR 0006：LLM 后端全切本地 Strata——选型、连通与显存窗口纪律

- 状态：已接受（2026-10-12，spec #82 grilling 定案；票 #83 实测 / #84-#86 落地 / #87 收口）
- 取代：[ADR 0004](0004-compressed-lane-kvmem.md) 中「压缩车道本地服务 = KVMem（WSL 18201）」的
  后端选型与窗口托管部分；该 ADR 的**车道二分**与**质量兜底转移**设计不变（压缩的收益
  与风险结构同源，只是换了引擎）。
- 上下文：压缩车道后端 KVMem（WSL，IQ2_S 27B，18201）实测健康但维护面重——跨界托管
  踩过嵌套 wsl.exe 杀守护（票4）、tp 假劣化（冒烟口径两修）、GSQ 档对照一直待拍板；
  每条 16 镜单次调用 49.8s 的吞吐在今天看已非最优。Strata（llama.cpp 系 engine + GLM
  投机解码 draft）提供同源模型家族的更高吞吐位与原生 OpenAI 兼容端点，#83 spike 以
  实测数据定案迁移（docs/spikes/2026-10-12-strata-spike.md）。

## 决策

1. **全切 Strata，vision 除外**：文案/结构生成（压缩车道 s04 单次调用、legacy s04-s07
   四阶段链、封面参考文本、平台标题、s08 翻转重写）全部指向本地 Strata
   （`http://<WSL网关>:8080`，模型名 `strata` = Qwen3.8-Flash-Next IQ3_S，context
   65536 / kv int8 / 权重 83.6GB SSD+GPU 混合）；端点/模型单一事实源在
   `strata_h3_lane`（STRATA_URL/STRATA_MODEL env）。**vision 判读保留 9router 云端**
   （`DRIVER_VISION_API`，全仓唯一 20128 引用）——s08 抽帧判读与烧卡同时段争卡，
   多模态物理上不可本地化。
2. **连通 = Windows 主机 IP**（spike §1 三选一实测）：server `--host 0.0.0.0` + WSL
   内默认路由网关动态发现（`ip route show default`，NAT 子网漂移免疫）；mirrored
   需 Win11 22H2+、portproxy 需管理员提权，均出局。安全知悉：0.0.0.0 向局域网暴露
   无鉴权端点，家用网络可接受。
3. **显存窗口纪律 = conflict 分支**（spike §3 实测：Strata 常驻即 15311 MiB / 94%，
   空闲不释放，与 H3/ComfyUI 生图互斥）：s04 入口拉起 → **s08 生图前停** → s11 入口
   再拉 → **s12 确认门前停**，单条视频常态两次加载（各 20-150s）；s08 rewrite 轮
   「先全部 LLM 重写再统一烧卡」分相（第三窗，仅翻转实际发生时）。`coexist` 开关
   （STRATA_WINDOW_MODE）留给显存充裕的机器常驻。
4. **生命周期统一 strata_window 托管**（#85）：探活（/v1/models 200）→ powershell
   跨界拉起（STRATA_START_CMD，spike §5 定案串）→ 失败叫人阶梯；加载超时 300s 口径
   + 就绪后预热（首补全 ~70s 属专家缓存预热非故障，spike §2）；停止 = netstat 取
   PID → taskkill /F /T 树杀 → 探活离线 + 显存回落双确认。KVMem 窗口托管、8888
   spawn/pkill 与启动体系、preflight KVMem 静态检查整体退役（代码零残留）。

## 请求方言红线（spike §4 实测矩阵定案）

- 关思维链唯一有效写法：`chat_template_kwargs: {"enable_thinking": false}`（T1）；
  9router 风格 `thinking` 字段（T5）与 `/no_think` 软开关（T6）均**无效**——禁 9router
  thinking 字段保留为风格红线（发了不炸但关不掉）。
- 缺 `model` 字段服务端容忍并补默认名（T4/T7，spec 假设「会报错」被证伪）——红线仍
  要求显式携带，使请求形态可断言。
- 未知顶层字段容忍（T2/T3）。吞吐参考：热态生成 148-159 tok/s（draft 接受率 ~96%）。

## 后果

- 单一模型档位：KVMem IQ2_S/GSQ 对照议题随 18201 退役一并封存（#61 的「重试 2 次叫人
  不回退」偏好延续到 Strata 阶梯的 MAX_ROUNDS=2）。
- 维护面收窄：无 WSL 服务面（KVMem 运行包/模型缺件检查随之删除）、无嵌套 wsl.exe
  spawn 形态（powershell fire-and-forget）、预热代价显式化（冷启 18-74s + 首补全
  ~70s，driver 通报口径「勿当劣化重启」）。
- 新增故障面：RAM 127GB 装 83.6GB 权重，页缓存被逐时启动降级（专家走 PCIe 流式
  1.3-1.7 tok/s）——随请求升温自愈，不做重启接线（重启反而清缓存）。
