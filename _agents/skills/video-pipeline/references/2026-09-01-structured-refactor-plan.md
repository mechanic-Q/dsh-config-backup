# 流水线结构化重构技术方案（根治格式噪音 / token 冗余 / 职责重叠）

> 2026-09-01 · 用户确认方向：**自由文本+正则校验 → JSON + schema 强校验 + 交接卡 + 逐镜并行**
> 状态：**技术方案（待确认后分步实施）**
> 目标：保住现有质量（创作自由度/校验闸门/产物链），根治三类问题：①格式噪音 ②token 冗余 ③改一处漏一处

---

## 〇、问题根源（三合一）

| 病征 | 根源 |
|---|---|
| 格式噪音（节奏/Hook/景别 FAIL） | **自由文本输出 vs 死枚举校验**——结构矛盾，换模型治不了 |
| 改一处漏一处（导演改了校验器不知道） | **契约散落在 3+ 脚本**，无单一事实源 |
| token 浪费 60-70% | **全文接力**，未利用"镜间独立" |

**核心洞察**：所有成熟 agentic pipeline（ViMax Pydantic / LangGraph state / Tavily 结构化交接）都在做同一件事——**把自由文本的脆弱契约换成结构化数据的强契约**。本流水线卡在"半结构化"（8字段是 JSON，但导演设计仍是自由文本）。

---

## 一、目标架构

```
scripts/schema.py          ← 新增：单一事实源（字段契约 / 枚举 / 约束）
scripts/llm_json.py        ← 新增：LLM 调用 + JSON 强校验 + reasoning 兜底（复用现有逻辑）
director_llm.py            ← 改：导演输出 JSON（按 schema）
storyboard_photographer.py ← 改：摄影师输出 JSON（按 schema）
director_to_h3_llm.py     ← 改：读 JSON 交接卡 + asyncio 并行
storyboard_enhancer.py     ← 改：校验读 schema（不再各自硬编码枚举）
pipeline_contract.py       ← 改：契约版本号 + stamp 记录 schema 版本
```

---

## 二、schema.py 设计（单一事实源）

```python
# scripts/schema.py
"""流水线字段契约 — 唯一事实源。改契约只改这里。"""

# ---- 节奏枚举（唯一源）----
RHYTHM_WORDS = ["铺垫", "建立", "预备", "冲击", "制动", "稳定"]

# ---- 景别/镜头（导演权威，摄影师不重定）----
SHOT_TYPES = ["远景", "全景", "中景", "特写", "运镜", "过肩"]

# ---- Hook 类型 ----
HOOK_TYPES = ["visual-joke", "reversal", "suspense", "reveal", "callback", "tender"]

# ---- 竖屏约束（唯一源，3 处引用统一）----
PORTRAIT_CONSTRAINT = (
    "9:16 vertical composition, sky at the very top, ground at the very bottom, "
    "no rotation, upright camera, no ring-shaped/overhead/bird's-eye views"
)

# ---- 导演输出 schema（阶段3）----
DIRECTOR_SHOT_SCHEMA = {
    "type": "object",
    "required": ["id", "情绪基调", "场景", "主体", "镜头", "动作时间轴", "风格渗透"],
    "properties": {
        "情绪基调": {"type": "string", "enum_first_word": RHYTHM_WORDS},  # 首词须枚举
        "场景": {"type": "string"},
        "主体": {"type": "string"},
        "镜头": {"type": "string"},   # 导演唯一镜头权威
        "动作时间轴": {"type": "string"},
        "风格渗透": {"type": "string"},
    },
}

# ---- 摄影师输出 schema（阶段4：只做执行，不重定决策）----
PHOTOGRAPHER_SCHEMA = {
    "type": "object",
    "required": ["id", "duration_est", "per_second_directives", "reference_anchors", "continuity_handoff"],
    "properties": {
        "duration_est": {"type": "number"},
        "per_second_directives": {"type": "array"},   # 秒级指令（执行）
        "reference_anchors": {"type": "object"},      # 光线/空间锚点（执行）
        "continuity_handoff": {"type": "object"},     # 段间衔接（执行）
    },
    # ⚠️ 不含：shot_type / 节奏意图 / hook_type / info_point（导演已定，摄影师不重写）
}

# ---- 交接卡 schema（阶段间传递）----
HANDOFF_SCHEMA = {
    "type": "object",
    "required": ["id", "一句话摘要", "情绪节奏", "信息点", "镜头决策", "视觉重点",
                 "衔接_in", "衔接_out", "风格锁", "竖屏约束"],
}

# ---- H3 输出 schema（阶段5）----
H3_SCHEMA = {
    "type": "object",
    "required": ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"],
}

# ---- 契约版本（pipeline_contract stamp 记录，防新旧产物混用）----
SCHEMA_VERSION = "2.0"
```

---

## 三、llm_json.py 设计（LLM 调用 + 强校验）

```python
# scripts/llm_json.py
"""LLM 调用 + JSON 强校验 + reasoning 兜底。替代各脚本重复的 llm_call。"""

def llm_call_json(system, user, schema, model, max_tokens=2000, timeout=300, retries=6):
    """调用 LLM，要求输出 JSON，按 schema 校验。不合格重试（指数退避）。"""
    for attempt in range(retries):
        raw = _llm_call_raw(system, user, model, max_tokens, timeout)
        # ① 从回复提取 JSON（容忍 ```json 围栏）
        obj = _extract_json(raw)
        # ② schema 校验
        err = _validate_schema(obj, schema)
        if not err:
            return obj
        # ③ 不合格 → 把错误喂回 LLM 让它修正（不是容忍）
        user += f"\n\n上次输出不合规：{err}。请按 schema 重新输出纯 JSON。"
        time.sleep(2 ** attempt)  # 指数退避
    raise ValueError(f"LLM 连续 {retries} 次输出不合规: {err}")
```

**关键**：把"正则猜"换成"**不合格重试，错误喂回 LLM**"。这比"容忍噪音进下游"质量更高。

---

## 四、各脚本改造点

### 4.1 director_llm.py（导演 → JSON）

**改动**：
1. `SYSTEM_SHOT` 加"只输出 JSON，字段严格按 schema"（保留创作自由度，只是包装变 JSON）
2. `llm_call` → 换成 `llm_json.llm_call_json(system, user, DIRECTOR_SHOT_SCHEMA, ...)`
3. **保留 reasoning 兜底**（deepseek-v4-flash 正文落 reasoning）——llm_json 的 `_extract_json` 从 reasoning 尾部提取 JSON
4. 落盘：`shotXX.md` 改为 `shotXX.json`（结构化）

**质量保障**：导演仍自由创作内容（情绪/场景/主体/镜头描述），只是装进 JSON 结构。6 枚举词仍要求（schema `enum_first_word` 校验 + 重试）。

### 4.2 storyboard_photographer.py（摄影师 → JSON，删决策字段）

**改动**：
1. `SYSTEM_PHOTOGRAPHER`：删掉 shot_type/节奏意图/hook_type/info_point 的输出要求（导演已定）
2. 只输出：`duration_est / per_second_directives / reference_anchors / continuity_handoff`
3. 输入增加：导演设计 JSON（读 shotXX.json 的【镜头】【情绪基调】作为执行依据）
4. 换 `llm_json.llm_call_json` + `PHOTOGRAPHER_SCHEMA`
5. BATCH=2 → 保持（或逐步并行）

**质量保障**：摄影师不再重写导演决策（消重叠），专注秒级执行细节。

### 4.3 director_to_h3_llm.py（阶段5 → 读交接卡 + 并行）

**改动**：
1. 输入：读 `02_逐段视觉设计/交接卡/shotNN.json`（程序生成，见 gen_handoff.py 已验证）
2. 删除"读整体方案全文"→ 只读交接卡（含风格锁/竖屏/衔接）
3. `asyncio.gather` + `Semaphore(4)` 逐镜并行（ViMax 模式，源码已验证无跨镜状态）
4. 输出 H3 三字段 JSON → `llm_json.llm_call_json` + `H3_SCHEMA` 校验
5. enforce_constraints 保留（竖屏/off-screen/music N/A 程序注入，不靠 LLM）

**质量保障**：每镜 prompt 内容与串行完全一致（只是并发），token 降 ~60%，时间降 ~4x。

### 4.4 storyboard_enhancer.py（校验器 → 读 schema）

**改动**：
1. 删除脚本内硬编码的 RHYTHM_WORDS / SHOT_TYPES / HOOK_TYPES → `import schema`
2. 校验项保持 7 项，但数据源：
   - ②节奏 → 从导演设计 JSON 的【情绪基调】校验（非摄影师字段）
   - ④Hook → 从导演设计校验
   - ⑥镜头 → 从导演设计【镜头】校验（导演唯一权威）
   - ⑤信息点 → 从导演设计【信息点】校验
3. 校验对象从"enhanced.json"扩展为"enhanced.json + shotXX.json"

**质量保障**：校验强度不降（仍是 7 项），只是数据源对齐"导演做决策"的新分工。

### 4.5 pipeline_contract.py（版本 + schema + 产物 hash 校验）

**改动**：
1. `SCHEMA_VERSION` 写入 stamp（`stamp_output` 记录 schema 版本）
2. `check_input` 检查上游产物 schema 版本一致（防新旧混用）
3. 保留 require_stage0 / 文件交接机制（不动核心）
4. **产物 hash 校验（S6 正式需求，2026-09-01 用户确认）**：
   - `stamp_output`：记录关键产物的 sha256（如 enhanced.json / shotXX.json / prompt_XX.json）
   - `check_input`：下游读前对比 stamp 里的 hash，不一致 → 警告"产物被外部改动/污染"
   - **代价：sha256 一个 10KB 文件 ≈ 0.1ms（微秒级，运行时零成本）**
   - **覆盖：运行时产物串改/污染（480P 跨项目污染 / LOG 共享串项目 / sibling cron 改产物的同类问题）**
   - **边界**：不解决"开发期两个 agent 改同一份代码"（那是 git 分支隔离的活），只防"运行时产物无意串改"

### 4.6 防覆盖/防污染机制（新增，2026-09-01 讨论定稿）

| 场景 | 机制 | 代价 |
|---|---|---|
| 运行时产物污染（下游读错文件） | pipeline_contract hash 校验（4.5） | 微秒级 |
| 开发期代码并发写（cron 与重构同碰一脚本） | **git 分支/worktree 隔离**（任务隔离） | 一次性 |
| 重构期防覆盖确认 | `/tmp/vp_refactor.sha256` hash 快照（人工，随时对比） | 一次性 |

**原则**：运行时污染用 hash 校验（进流水线）；开发期并发用任务隔离（git），**不在流水线里做文件锁（flock）**——那是重机制，流水线本身是串行单任务，不需要。

---

## 五、迁移策略（保住已有一切）

| 产物 | 处理 |
|---|---|
| 已生成的 shotXX.md（自由文本） | 保留不删；新项目起用 JSON。**旧 md 用 gen_handoff 提取交接卡**，阶段5 兼容两种输入 |
| enhanced.json（8字段） | 保留；新格式去掉决策字段，旧格式兼容读取 |
| prompt_XX.json（H3 三字段） | 格式不变（已是 JSON） |
| 校验闸门 | 全保留（enforce/enhancer/verify），只改数据源 |

**向后兼容**：`llm_json._extract_json` 若拿不到 JSON（旧 md 无结构），fallback 到旧正则提取 → 老项目仍能跑。

---

## 六、分步实施（每步可验证，保质量）

| 步 | 内容 | 验证 |
|---|---|---|
| **S1** | 新增 `schema.py` + `llm_json.py`（含 reasoning 兜底 + JSON 提取 + 校验 + 重试） | 单测：喂 mock 输出，验证 JSON 提取/校验/重试 |
| **S2** | `director_llm.py` 改 JSON 输出 + schema | 跑 1 段，验证 shotXX.json 结构合规 + 创作质量不降（对比旧 md） |
| **S3** | `storyboard_photographer.py` 改 JSON + 删决策字段 | 跑 1 段，验证 4 字段执行输出 + 不重写导演决策 |
| **S4** | `gen_handoff.py` 接 schema（已有雏形）+ 阶段5 读交接卡 | 跑 1 段，验证 user 消息含交接卡 |
| **S5** | `director_to_h3_llm.py` asyncio 并行 | 3 段并发 vs 串行，prompt 一致 + 时间 ~4x |
| **S6** | `storyboard_enhancer.py` + `pipeline_contract.py` 接 schema | test_storyboard_enhancer 全过 + 契约版本校验 |
| **S7** | 全量回归：21 段走通 S2-S6 | verify_output 全过 + 成片质量对比 |

**每步独立可验证，不一次性大爆炸。** 任何一步质量下降 → 停在该步，单独诊断。

---

## 七、质量保障（不降级的具体措施）

1. **创作自由度保留**：导演/摄影师在 JSON 内仍自由写内容，只是"包装"从散文变 JSON
2. **校验强度提升**：从"正则猜 + 容忍噪音" → "schema 校验 + 不合格重试"
3. **recurring 兜底保留**：deepseek-v4-flash 的 reasoning 提取逻辑进 llm_json，不丢
4. **向后兼容**：老产物（md/enhanced 旧格式）仍能读，新项目才用新格式
5. **契约版本**：stamp 记录 SCHEMA_VERSION，防新旧混用
6. **逐步灰度**：S1-S7 每步单测/单段验证，不一次性推翻

---

## 八、红线

- 竖屏约束、off-screen、music N/A、POV 这些**程序注入约束不动**（不靠 LLM）
- 用户音色、GPU 串行（H3 生成）、发布闸门**不动**（与 LLM 层无关）
- 6 枚举词仍是导演决策的一部分（只是从"prompt 要求"升级为"schema 强制"）
- 任何一步质量下降 → 停在该步，不强行推进
