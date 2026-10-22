# 流水线解耦设计：交接卡 + 子代理解耦（阶段1~5）

> 2026-09-01 讨论定稿 · 状态：**方案讨论稿，未实施**
> 目标：在**保留全部产物与校验闸门**的前提下，消除阶段1~5 的上下文冗余传递，降低 token 消耗并支持并行。

## 一、问题现状（量化）

当前阶段1~5 是"全文接力"式串行传递：

| 环节 | 输入 token 量 | 冗余说明 |
|---|---|---|
| 阶段2 整体方案 | 口播稿全文 ~4K | ✅ 必要（通读才能定方向） |
| 阶段3 每段设计 | 整体方案(1.6K)×21 + 本段台词 | ⚠️ 整体方案每段重读 = **21×1.6K = 33K 冗余** |
| 阶段4 摄影师 | 全部21段设计(10K) + 整体方案 | ⚠️ 8字段已结构化，仍带全部设计原文 |
| 阶段5 每镜转译 | 整体方案 + 本镜enhanced + 本镜设计 | ⚠️ 整体方案每镜重读 = **21×1.6K = 33K 冗余** |

**估算：约 60-70% 输入 token 是"每段重读的整体方案 + 跨段重复原文"——纯冗余。**

## 二、核心原则：只传"决策 + 约束"，不传"原始全文"

必须跨阶段传递的只有两类信息：
1. **决策信息**（方向/情绪/镜头/信息点）——必须继承
2. **约束信息**（风格锁/竖屏/格式契约）——必须贯穿

**不需要传**：每阶段完整原始文本（口播稿全文、全部设计、全部8字段）。

## 三、业界验证（2026-09 调研）

### Tavily Deep Research（supervisor-subagent）
> ReAct → supervisor-sub-agent + context separation。子代理用 **reflection tools 提炼步骤间信息**，而非携带全文。Deep Research Bench 第一 + token 显著下降。

### 生产实测收益
| 案例 | 手法 | 收益 |
|---|---|---|
| Tavily | 上下文分离 + 反思提炼 | token 大降，DR Bench 第一 |
| Code Mode | 沙箱执行（工具结果不回流） | 可靠 56%→100%，成本 -50%，token ~3x 降 |
| Cursor | 动态上下文发现 + 长输出转文件 | token -46.9% |

### 关键警示：The Multi-Agent Trap
> "Unstructured multi-agent networks amplify errors up to **17.2x** compared to single-agent baselines."

**你的流水线已规避**：文件落盘 + 程序化校验（enhancer/enforce/verify）+ 结构化8字段 = 交接有硬校验兜底。**解耦时必须保留这些硬闸门**，只解耦"上下文传递"。

## 四、目标架构：交接卡 + 子代理解耦

```
主控（当前会话，保留小上下文）：
  - 维护 全局卡（方向/风格锁/节奏分布）≈ 500字
  - 逐镜派发 delegate_task：context = [全局卡 + 本段要求 + 前段交接卡]
  - 收集产物 → 落盘 → 程序校验（原样保留）
  - 不累积子代理中间推理

子代理（阶段3导演 / 阶段4摄影师 / 阶段5翻译官）：
  - 隔离上下文（delegate_task 天然如此）
  - 只拿到 [全局卡 + 本段要求 + 前段交接]
  - 产出本段文件 → 返回摘要 → 上下文销毁
```

### 交接卡格式（草案）

```
# 交接卡 shotNN（阶段N → 阶段N+1）
- 本段一句话摘要：
- 情绪/节奏：[铺垫/建立/预备/冲击/制动/稳定]
- 关键信息点：
- 镜头决策（导演）：景别+运镜
- 视觉重点：
- 与前段衔接（in）：… / 向后段交接（out）：…
- 风格锁命中：…
- 竖屏约束：…
```

**≈ 100-200 字/段**，替代"本段设计全文 500字"。

## 五、落地路径（渐进，不推翻）

| 步骤 | 内容 | 风险 | 收益 |
|---|---|---|---|
| **第一步** | 只加**交接卡**：阶段3/4 产出时自动生成紧凑摘要，阶段5 只读交接卡 + 本镜文件，不读整体方案全文 | 低 | token 立省 ~33K+，质量不变 |
| **第二步** | 阶段3/4/5 逐镜改 `delegate_task` 并行（每镜一个干净子代理） | 中 | token 再降 + 获得并行 |
| **第三步（可选）** | 引入 LangGraph/CrewAI 承载编排（若流水线更复杂） | 高 | 更完整编排能力 |

## 六、与 Hermes 原生工具的关系

| 需求 | 现成方案 | 是否要写 |
|---|---|---|
| 逐镜并行设计 | `delegate_task`（隔离上下文） | ❌ 现成 |
| 并行 + 失败重试 + 进度 | `hermes kanban swarm`（worker→verifier→synthesizer） | ❌ 现成 |
| 阶段间依赖 | `hermes kanban link` | ❌ 现成 |
| 逐镜模型切换 | `hermes kanban set-model` | ❌ 现成 |
| 校验/汇总 | swarm 的 `--verifier`/`--synthesizer` | ❌ 现成 |
| **交接卡格式** | **要写**（业务逻辑，任何方案都得写） | ⚠️ 一小段 |

**结论：调度器不用写（kanban swarm / delegate_task 现成），要写的是交接卡格式 + 21镜→worker 映射 + 每镜 goal/context 模板。**

## 七、参考框架

- **ViMax**（HKUDS，12.2k star，MIT）：agentic 视频生成，Director/Screenwriter/Producer 角色代理 + pipelines 编排。**架构参考**（角色/编排设计），不能直接替换（面向云端视频模型，非本地 ComfyUI+H3）。
- **LangGraph**：图式编排 + 显式 state + 并行分支。
- **CrewAI**：角色化 agents + handoff + Flows。
- **OpenAI Agents SDK**：handoffs 路由式交接 + 三模式（Magentic/Handoff/Agents-as-Tools）。
- **Temporal.io**：持久化工作流编排（视频编辑系统用其调度 workers）。

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| 交接卡信息丢失（漏了前段做了什么） | 交接卡由程序从结构化字段提取（非 LLM 自由写），必含 6 项 |
| 子代理 status 不可信 | 每次 delegate_task 后 `ls -la` + 行数验证（skill 教训） |
| 并行消耗 API 配额（402/429） | 并行度 ≤4，先查余额，失败指数退避 |
| 解耦丢硬校验 → 掉进 17x 陷阱 | **校验闸门原样保留**（enhancer/enforce/verify），只在子代理层隔离上下文 |

## 九、ViMax 编排分析（2026-09-01 实读源码）

**ViMax（HKUDS，12.2k star，MIT）—— agentic 视频生成，与本流水线同构。** 已 clone 到 `/tmp/vimax` 源码分析。

### 目录结构
```
agents/    13 个角色代理：screenwriter/script_planner/storyboard_artist/
            character_extractor/scene_extractor/event_extractor/... 
            （Pydantic 结构化输出，model_validate 强校验）
pipelines/ 3 条流水线：script2video(46K) / idea2video / novel2movie
prompts/   agent.md(角色基) + workflow.md(工作流)
interfaces/ 数据结构（ShotBriefDescription/ShotDescription/Camera 等 Pydantic 模型）
tools/     渲染后端（RenderBackend，接视频模型）
```

### 关键交接模式（与本方案对照）

| ViMax 做法 | 你的流水线 | 启示 |
|---|---|---|
| **storyboard.json 落盘**：`design_storyboard()` 先查文件已存在则加载，不存在才调 LLM 生成 → 断点续跑 | 你的 shotXX.md / enhanced.json 落盘 | ✅ 一致 |
| **每镜独立目录**：`working_dir/shots/{idx}/shot_description.json` 逐镜落盘 | 你的 02_逐段/05_H3prompt 逐镜文件 | ✅ 一致 |
| **`asyncio.gather` 并行分解**：`decompose_visual_descriptions` 对全部镜并行 `decompose_visual_description_for_single_shot` | 你的串行逐镜 LLM | **可借鉴：逐镜并行** |
| **角色代理 + Pydantic 强类型**：每个 agent 返回结构化模型，`model_validate` 强校验 | 你的 8 字段 + enhancer 七项校验 | ✅ 一致 |
| **storyboard（镜头表）→ 逐镜 decompose（视觉描述）→ 逐镜 render** 三段式 | 你的 阶段3设计→阶段4增强→阶段5 prompt | ✅ 同构 |

### ViMax 给我们的 3 个直接可借鉴点
1. **逐镜并行**（`asyncio.gather`）——镜间独立，可直接并行，不必串行。你的阶段3/4/5 的逐镜调用可照此改并行。
2. **"落盘即断点"**——所有中间产物落盘，重跑自动跳过已存在（你已有 pipeline_contract，一致）。
3. **Pydantic 强类型**——每个 agent 输出严格结构，`model_validate` 强校验。你的 8 字段 JSON + enhancer 是等效实现。

### ViMax 与你的差异（为什么不能直接替换）
- 面向**云端视频模型**（Gemini/OpenRouter），非本地 ComfyUI+H3（你的 GPU 串行铁律是 ViMax 没有的硬约束）
- **无人工确认门**（你的方向选择/成片审核），纯自动
- 无"中文口播 + Index-TTS + 字幕 + 封面 + 发布"这些你已建成的下游

## 十、LangGraph / CrewAI 重不重？是什么？（2026-09-01 调研）

### 它们是什么

| 框架 | 本质 | 核心概念 |
|---|---|---|
| **LangGraph** | 有向图状态机编排 | `StateGraph`（节点+边）+ 显式 `state`（跨节点传递的共享结构）+ checkpoint（持久化）+ 并行分支 |
| **CrewAI** | 角色化协作 | `Agent`（角色）+ `Task` + `Crew`（编排）+ `handoff`（上下文交接）+ `Flow`（事件驱动） |

### 重不重？—— 判断

| 维度 | LangGraph | CrewAI | 你的 Hermes 原生 |
|---|---|---|---|
| 依赖 | 需装 langchain/langgraph 包 | 需装 crewai 包 | **零新增** |
| 学习曲线 | 中（图+state+checkpoint 概念） | 中（角色+任务+crew） | 低（已熟悉） |
| 对你的流水线价值 | 状态图 + 并行 + 断点 | 角色化 + handoff | delegate_task 已覆盖隔离+并行 |
| 与现有脚本融合 | 要重写编排层 | 要重写编排层 | **直接复用现有脚本** |

**结论：对你这套已建成的流水线，LangGraph/CrewAI 是"重"的**——它们解决的是"从零搭多代理编排"的问题，而你已经有：文件交接（=state）、pipeline_contract（=checkpoint）、逐镜脚本（=agents）。引入它们是**引入新依赖 + 重写编排层**，收益只是编排更"正规"，代价是破坏已验证的产物链。

**除非未来流水线复杂到**（多分支 / 条件路由 / 动态重试策略），才值得引入。当前 Hermes `delegate_task` + `kanban swarm` + 现有脚本已覆盖全部需求。
