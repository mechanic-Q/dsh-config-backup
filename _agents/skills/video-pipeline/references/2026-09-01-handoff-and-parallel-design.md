# 交接卡可实施设计 + ViMax 逐镜并行落地方案

> 2026-09-01 · 承接 `2026-09-01-pipeline-decoupling.md` · 状态：**可实施设计（待确认后动手）**
> 基于 graphify 依赖图 + 三个脚本源码实读（director_llm / storyboard_photographer / director_to_h3_llm）

## 〇、graphify 依赖图结论（防漏改地图）

```
pipeline_contract.py ← imports: director_llm / segment_script / storyboard_enhancer /
                              storyboard_photographer / verify_output / 2个test
```

**改字段契约（交接卡/节奏枚举）必须同步：pipeline_contract + 5 脚本 + 2 测试。**
文件级依赖（enhancer→shotXX.md→enhanced.json→prompt_XX.json）graphify 抓不到，靠本文档流程表保证。

---

## 一、交接卡可实施设计

### 1.1 交接卡格式（落盘文件：`02_逐段视觉设计/交接卡/shotNN.json`）

```json
{
  "id": "shot01",
  "一句话摘要": "空间站俯瞰蓝色星球，天宫气象开场",
  "情绪节奏": "建立",
  "信息点": "中国空间站从 400 公里俯瞰地球",
  "镜头决策": "远景，缓慢推近（导演唯一镜头权威）",
  "视觉重点": "蓝色星球 + 空间站轮廓 + 云海",
  "衔接_in": "开场：黑场渐入，云海浮现",
  "衔接_out": "星球弧线滑向画面右，引出下段",
  "风格锁": "天宫气象：云海浮现-现物-云合",
  "竖屏约束": "9:16 垂直，天空正上，禁环形/俯视"
}
```

**≈ 150 字/段**（现导演设计全文 ~500 字）。8 项全由**程序从结构化字段提取**（非 LLM 自由写，保证不丢信息）。

### 1.2 交接卡生成器（新脚本 `scripts/gen_handoff.py`，~60 行）

```
输入：02_逐段视觉设计/shotXX.md（导演设计）+ 03_分镜增强/分镜清单_enhanced.json（摄影师8字段）
输出：02_逐段视觉设计/交接卡/shotNN.json（8项程序提取）
规则：
  情绪节奏  ← 从 shotXX.md 【情绪基调】首词提取（6枚举）
  信息点    ← 从 enhanced.json info_point
  镜头决策  ← 从 shotXX.md 【镜头】（导演权威）
  衔接_in/out ← 从 enhanced.json continuity_handoff
  风格锁    ← 从 01_整体视觉方案/00_交接摘要.md 视觉主线（全篇一致，程序读一次）
  竖屏约束  ← 从 director_to_h3_llm.py PORTRAIT_CONSTRAINT 常量
```

### 1.3 阶段5 改为读交接卡（替换整体方案全文）

**改动点：`director_to_h3_llm.py` 循环体 216-217 行**

现状：
```python
if overall_ctx:
    parts.append(f"【全篇整体视觉方案（保持全片一致，风格/母题/色彩以此为准）】\n{overall_ctx}")
```

改为：
```python
handoff = load_handoff(sid)   # 读 02_逐段视觉设计/交接卡/shotNN.json
if handoff:
    parts.append(f"【镜头交接卡（本镜：情绪/镜头/信息点/衔接/风格锁/竖屏）】\n{json.dumps(handoff, ensure_ascii=False)}")
```

**收益：每镜从"整体方案全文 1.6K + 导演设计 500字 + 摄影师 8字段" → "交接卡 150字 + 摄影师8字段"**，阶段5 token 降 ~60%。

**注意**：`--overall` 参数保留（`00_交接摘要.md` 已 ≤15 行，本身就是轻量），交接卡替代的是"00_整体视觉方案.md 全文"，不是交接摘要。

### 1.4 交接卡在阶段3/4 的应用（第二步）

- 阶段3（导演设计）产出后：`gen_handoff.py` 生成交接卡 → 阶段4（摄影师）读交接卡 + 本镜设计（不读全篇）
- 阶段4 产出后：`gen_handoff.py` 更新交接卡（补衔接/锚点） → 阶段5 读最终交接卡

**交接卡是"活的"**：阶段3 生成初版，阶段4 补充，阶段5 消费。程序提取保证 8 项永不缺。

---

## 二、ViMax 逐镜并行落地方案

### 2.1 为什么可行（源码实证）

三个脚本的逐镜循环**无跨镜状态**：

| 脚本 | 循环 | 跨镜依赖 | 可并行？ |
|---|---|---|---|
| `director_llm.py` | for shot: LLM→写 shotXX.md | 无（每镜只依赖整体方案+本镜台词） | ✅ |
| `storyboard_photographer.py` | BATCH=2 串行批 | 无（每批独立，todo 列表预计算） | ✅（注意9router限流） |
| `director_to_h3_llm.py` | for n: 读本镜→LLM→写 json | **无**（205-249 行验证：每镜读 design+photo[sid]+overall_ctx，无跨镜变量） | ✅ |

### 2.2 落地方案 A：脚本内 asyncio.gather（最轻，仿 ViMax）

**改动点：`director_to_h3_llm.py` 205-250 行**

```python
import asyncio

async def process_one(n, ...):
    # 原循环体 206-249 行原样搬入，只把 print 换 log
    return sid, ok

async def main_async():
    tasks = [process_one(n, ...) for n in range(args.start, args.end + 1)]
    # 并发限流：信号量控制同时跑 N 个（默认 4）
    sem = asyncio.Semaphore(args.concurrency or 4)
    results = await asyncio.gather(*[wrap(sem, t) for t in tasks])
```

- `asyncio.Semaphore(4)` 控制并发 ≤4（防 9router 502/429，今日实测并行 8 会撞限流）
- 每镜 `llm_call` 已是独立 HTTP 请求，**无共享资源**，天然线程/协程安全
- 落盘仍是每镜独立 json，`pipeline_contract` 不变

**收益**：21 镜转译从串行 40-60 分钟 → 并发 4 约 **10-15 分钟**（质量不变，每镜 prompt 完全相同）。

### 2.3 落地方案 B：delegate_task 并行（彻底解耦）

如果同时要"交接卡 + 上下文隔离"：

```
主控：gen_handoff.py 生成全部交接卡
   ↓
对每镜 delegate_task：
  goal = "为 shotNN 生成 H3 prompt JSON"
  context = [交接卡 shotNN.json + 摄影师8字段(shotNN) + --style 风格块]
  子代理产出 shotNN.json（独立上下文，不携带全篇）
   ↓
主控：enforce_constraints 逐镜校验 + 落盘
```

**代价**：子代理可靠性需验证（skill 教训：status 不可信，每次 ls 验证）；多一层编排。
**适用**：当 token 是硬约束（如用付费 API）时选 B；当只是要提速时选 A。

### 2.4 推荐路径

| 阶段 | 动作 | 风险 |
|---|---|---|
| **第一步** | 交接卡生成器（gen_handoff.py）+ 阶段5 读交接卡 | 低，纯增量 |
| **第二步** | director_to_h3_llm.py 改 asyncio.gather 并发 4（方案A） | 中，需实测 9router 并发上限 |
| **第三步** | （可选）阶段3/4 也并行 + 交接到子代理 | 中高，后续再评估 |

---

## 三、影响面清单（graphify + 文件流确认）

| 改动 | 涉及文件 | 验证 |
|---|---|---|
| 交接卡生成 | 新增 `scripts/gen_handoff.py` | 跑 1 段核对 8 项齐全 |
| 阶段5 读交接卡 | `director_to_h3_llm.py` 216-217 行 | 跑 1 段看 user 消息含交接卡 |
| 并发化 | `director_to_h3_llm.py` 205-250 行 | 跑 3 段并发，对比串行结果一致 |
| 契约同步 | 若改节奏枚举 → pipeline_contract + 5 脚本 + 2 测试 | test_storyboard_enhancer / test_verify_output |

**红线**：
- 改字段契约必须同步 pipeline_contract（graphify 确认 5 脚本 import）
- 并发 ≤4（9router 今日实测并行限流）
- 保留全部校验闸门（enhancer/enforce/verify），只改"谁读什么"
- 今天读报视频优先出片，本方案作为下一轮专项

---

## 四、测试与验收

```
# 交接卡
python3 scripts/gen_handoff.py <项目>/02_逐段视觉设计 <项目>/03_分镜增强 <项目>/01_整体视觉方案
# → 检查 02_逐段视觉设计/交接卡/shot01.json 8 项齐全

# 阶段5 读交接卡（单段验证）
python3 director_to_h3_llm.py <设计> <输出> --start 1 --end 1 --durations <enhanced> --handoff <交接卡目录>
# → prompt json 的 user 消息含【镜头交接卡】

# 并发验证
time python3 director_to_h3_llm.py ... --concurrency 4 --start 1 --end 4
# → 对比串行结果，prompt 内容一致，时长降到 ~1/4
```

## 五、最小验证结果（2026-09-01 真实数据 21 段）

**`gen_handoff.py` 已实现并跑通**（21 段全生成，8 项程序提取）：

| 字段 | 提取结果 | 说明 |
|---|---|---|
| 一句话摘要 | ✅ 全 21 段 | 从【视觉重点】截取 |
| 镜头决策 | ✅ 全 21 段 | 导演【镜头】权威字段 |
| 视觉重点 | ✅ 全 21 段 | |
| 竖屏约束 | ✅ 全 21 段 | 常量注入 |
| 风格锁 | ✅ 修复后全 21 段 | 正则适配 `**视觉主线**：` 格式 |
| 情绪节奏 | ⚠️ 部分（~70%） | **依赖导演设计含 6 枚举词**；旧版设计 shot01/05 无节奏词 → 新导演 prompt 已强制，重跑阶段3解决 |
| 信息点 | ⚠️ 部分 | 依赖 enhanced.json info_point；手工补段版部分缺 |
| 衔接_in/out | ⚠️ 部分 | 同上，加 fallback 从导演设计【衔接】提取 |

**关键结论**：交接卡**继承上游质量，不创造质量**。8 项里 4 项依赖"上游数据完整"——交接卡方案的前提是**阶段3/4 的字段契约先落地**（导演输出节奏枚举词、摄影师补全 continuity_handoff）。生成器本身已健壮（双 fallback），上游数据到位后 8 项自然全齐。
