#!/usr/bin/env python3
"""director_llm.py — 用 LLM（本地 Strata，#86 起；原 9router 云端）做全篇+逐段的视觉导演设计。

两阶段（用户要求：既是分开的，又是整体的一部分）：
  阶段A 全篇视觉把控：LLM 通读所有段台词 → 产出整体视觉方案
    （视觉主线 / 场景系统 / 重复母题 / 色彩节奏 / 节奏曲线 / 段间衔接原则）
  阶段B 逐段创意设计：每段基于整体方案 + 本段台词（含节奏意图/信息点/景别）
    → 独立视觉导演设计（环境 / 主体 / 镜头 / 动作时间轴 / 风格渗透）

用法：
  director_llm.py <enhanced.json> --out-dir <输出目录> [--model strata] [--only-scenes]
    --only-scenes 只做逐段（跳过整体方案，用于续跑）
输出：
  整体方案: <out>/00_整体视觉方案.md
  逐段:     <out>/shotXX.md
"""
import glob, json, os, sys, argparse, urllib.request, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# 流水线契约：环节间只传文件（强制）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_contract as pc
import llm_json
import schema

# 端点在调用期经 llm_json.api() 解析（LLM_API env 优先，缺省本地 Strata；与文案链单一
# 事实源同源）——import 期缓存会踩 strata_h3_lane 半初始化时序，见 llm_json.api docstring

def load_style(style_name):
    """读 styles/<名>.md 全文作为风格块注入 system prompt。空/None 返回空串。"""
    if not style_name:
        return ""
    for base in (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "references", "video-craft", "styles"),
                 os.path.expanduser("~/references/video-craft/styles")):
        p = os.path.join(base, f"{style_name}.md")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    print(f"WARN: 风格文件不存在 {style_name}（references/video-craft/styles/{style_name}.md），无风格块运行")
    return ""


SYSTEM_SHOT = """你是资深影视视觉导演。

对每段中文口播台词，你要做完整的视觉导演设计，**只输出一个 JSON 对象**（不要输出任何 JSON 以外的文字、不要 markdown 围栏），字段严格为：

{"id": "段号", "情绪基调": "本段情绪与节奏，首词必须是6词之一：铺垫/建立/预备/冲击/制动/稳定，可附描述如 冲击——万线归碑", "场景": "这一段发生的环境（具体场景感，不能抽象）", "主体": "画面核心物体/元素（与台词直接相关）", "景别": "从枚举选一：远景/全景/中景/特写/大全景/大远景/中近景/近景/中全景", "运镜": "从枚举选一：推近/拉远/环绕/静止/横移/升降（实际以系统分配的【运镜配额】为准）", "镜头": "景别 + 运镜类别 + 具体描述（节奏意图决定：铺垫=缓慢推近/远景、建立=平稳运镜/全景、预备=轻微推进/中景、冲击=快速推近/特写、制动=缓慢拉远/中景、稳定=静止微移/全景）", "动作时间轴": "按段时长分 3 段（0-1/3、1/3-2/3、2/3-1），每段一个明确动作，与台词语义呼应", "风格渗透": "本段如何延续整体风格，并点出与前段/后段的视觉衔接", "信息点": "本段台词要传达给观众的核心信息（1-2 句概括，数字/专有名词/因果保留，给下游校验与交接卡用）"}

硬性要求：
1. 每段设计必须**与台词内容强相关**——台词讲什么，画面就演什么（数字、专有名词、因果都要有视觉对应物）
2. 必须**有创意**：每段的场景/主体/动作要不同，不能套用相同模板；同一场景系统内的段落要有递进或变化
3. 画面内无人声（口播是旁白）：不要设计"人物说话"
4. **情绪基调首词必须是 6 节奏词之一**（铺垫/建立/预备/冲击/制动/稳定），这是下游校验和摄影师执行的依据
5. 输出纯 JSON，不要解释过程，不要 markdown 围栏
6. **人物镜头逐段判断（内容驱动，不是配额）**：先问「这段台词的信息点和情绪落在人身上，还是环境/数据上」——
   落在人（个体故事/民生冷暖/情绪转折/冲突对抗）→ 人物中近景或特写，由虚构仙侠角色执行台词中的动作（人物不说话）；
   落在宏观（数据/政策/产业/天文地理）→ 宏大空镜，或人物作为比例锚点；
   情绪爆点（冲击）→ 允许面部/手部/器物特写。是否用人物、用多近，由每段内容独立决定
7. **反模板（多样性硬约束）**：相邻两段不得「同景别+同运镜」；「横移/长卷展开」类运镜由系统按配额
   分配（每 4 段至多 1 段，见输入的【运镜配额】），未分配到横移的段严禁输出运镜=横移
8. "景别"/"运镜" 两字段必须严格使用枚举值（下游程序校验，非枚举会整段打回）

{STYLE_BLOCK}"""

SYSTEM_OVERALL = """你是资深影视视觉导演，负责一部 AI 视频短片的**全篇视觉把控**。

以下是整部分镜清单（每段的台词、节奏意图、信息点）。请通读后输出整体视觉方案（中文）：

【视觉主线】全篇用什么视觉线索贯穿
【场景系统】设计 3-5 个核心场景，说明每个场景在哪些段落出现、如何演进
【重复母题】1-2 个反复出现的母题，作为视觉记忆点
【色彩节奏】全篇色彩如何变化（开场/中段/高潮/结尾的色调走向）
【节奏曲线】全篇节奏意图（铺垫/建立/预备/冲击/制动/稳定）的分布规律，说明视觉上如何配合
【段间衔接】相邻段之间视觉如何衔接（动作连续/场景切换/母题延续），保证"既是分开的又是整体的一部分"

硬性要求：
1. 全篇是完整叙事，视觉必须有整体演进
2. 输出中文，400-600 字，直接给方案，不要解释过程

{STYLE_BLOCK}"""


SYSTEM_DIRECTIONS = """你是资深影视视觉导演，负责一部 AI 视频短片的**整体视觉方向提案**。

以下是整部分镜清单（每段的台词、节奏意图、信息点）。请通读后，给出 **3 个不同的整体视觉方案方向**（中文），每个方向必须：
1. 有清晰的视觉主线（用什么视觉线索贯穿全片）
2. 有明确的美术风格关键词（色调/质感/象征符号系统）
3. 说明这个方向最擅长表达什么情绪/主题
4. 说明风险或局限（什么内容这个方向表达不好）

**只输出一个 JSON 对象**（不要任何解释、不要 markdown 围栏），字段严格为：

{"directions": [{"方向名": "简短有力的方向名", "视觉主线": "...", "美术风格": "色调/质感/象征符号", "最适合表达": "...", "局限": "..."}]}

第一个方向是推荐方向（下游自动采纳第一个）。

{STYLE_BLOCK}"""

# free-text 回退用（JSON 契约连败 6 次才走；md 卡供人工阅读与旧版正则解析）
SYSTEM_DIRECTIONS_MD = """你是资深影视视觉导演，负责一部 AI 视频短片的**整体视觉方向提案**。
通读分镜清单后给出 3 个不同的整体视觉方案方向（中文），每个方向包含：视觉主线、美术风格关键词、最擅长表达的情绪/主题、风险或局限。

输出格式（严格按此结构，不要解释过程，不要 JSON）：

## 方向一：<方向名>
【视觉主线】...
【美术风格】...
【最适合表达】...
【局限】...

## 方向二：<方向名>
...

{STYLE_BLOCK}"""


def assign_move_quota(ids):
    """横移确定性配额（2026-09-07）：prompt 软约束四轮实测全部超标（58-84%），
    并行生成互相不可见 → 改为生成端分配。每 4 段允许 1 段横移（25% < 1/3 警戒线），
    其余段在非横移运镜中轮转（相邻必不同类）。返回 {sid: 运镜类别}。"""
    cycle = ["推近", "环绕", "静止", "拉远", "升降"]
    quota = {}
    k = 0
    for i, sid in enumerate(ids):
        if (i + 1) % 4 == 0:
            quota[sid] = "横移"
        else:
            quota[sid] = cycle[k % len(cycle)]
            k += 1
    return quota


def _normalize_shot_level(design):
    """景别首词归一（2026-09-06 根治 s4 ⑥「超大全景」不在枚举 → 摄影师阶段才发现且无法自愈）：
    首词未以合法景别开头时，用 schema.shot_first_level 提取的标准景别（含复合词最长命中）
    重写首词；提取结果仍不合法则原样保留（下游自检兜底）。"""
    txt = (design.get("镜头") or "").strip()
    if not txt:
        return design
    first = re.split(r"[→➔>·/，,、＋+]", txt)[0].strip()
    if any(first.startswith(t) for t in schema.SHOT_TYPES):
        return design
    best = schema.shot_first_level(txt)
    if best in schema.SHOT_TYPES:
        design["镜头"] = best + txt[len(first):]
    # 结构化字段归一（2026-09-07）：景别不在枚举 → 标准景别；运镜不在枚举 → 按文本归类
    jb = design.get("景别") or ""
    if jb and jb not in schema.SHOT_TYPES:
        norm = schema.shot_first_level(jb)
        design["景别"] = norm if norm in schema.SHOT_TYPES else best if best in schema.SHOT_TYPES else jb
    jm = design.get("运镜") or ""
    if jm and jm not in schema.SHOT_MOVE_TYPES:
        for t in schema.SHOT_MOVE_TYPES:
            if t in jm:
                design["运镜"] = t
                break
    return design


def fill_style(template, style_block):
    """把风格块填入模板的 {STYLE_BLOCK} 占位。"""
    return template.replace("{STYLE_BLOCK}", style_block)


def llm_call(messages, model, max_tokens=800, timeout=120, retries=2, answer_marker=False):
    """answer_marker=True：要求模型先输出 <<<ANSWER>>> 行再写正文，返回前按标记
    剥离——low 组合（GLM/DeepSeek）会把整段思维链写进 content（0907 实测整体
    方案带病落盘），标记法等价于 llm_call_json 的括号配对防护；漏写标记退回原文。"""
    if answer_marker:
        messages = [dict(m) for m in messages]
        messages[-1]["content"] += (f"\n\n[输出格式硬要求] 第一行只写标记 {llm_json.ANSWER_MARKER}，"
                                    "换行后再写正文；标记之前一个字都不要输出。")
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.85,
        "max_tokens": max_tokens,
        # 关思维链（流水线质量口径）：chat_template_kwargs.enable_thinking=false 是
        # Strata（llama.cpp 系）唯一有效写法（#83 spike §4 T1）；9router 专属
        # thinking 字段禁发（风格红线，#86 去 9router 方言）
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(llm_json.api(), data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
            if "data: [DONE]" in raw:
                raw = raw[:raw.find("data: [DONE]")].strip()
            resp = json.loads(raw)
            llm_json.record_usage(resp.get("usage"))
            msg = resp["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""
            # deepseek-v4-flash: 正文常落在 reasoning 尾部（content 被截断）
            if len(content.strip()) < 60:
                reasoning = msg.get("reasoning") or ""
                # 1) Draft/最终块后的设计正文
                m = re.search(r"(?:Draft\s*\d*[:：]?\s*\n?)((?:【场景】|场景[:：]|【环境】)[\s\S]*)$", reasoning, re.MULTILINE)
                if m:
                    content = m.group(1)
                else:
                    # 2) 最后一个 【场景】 块
                    matches = list(re.finditer(r"【场景】", reasoning))
                    if matches:
                        content = reasoning[matches[-1].start():]
                    else:
                        # 3) 退化：最后一个 '场景' 起
                        idx = reasoning.rfind("场景")
                        if idx != -1:
                            content = reasoning[idx:]
            if answer_marker and llm_json.ANSWER_MARKER in content:
                content = content.split(llm_json.ANSWER_MARKER)[-1]
            return content.strip()
        except Exception as e:
            if attempt == retries:
                return f"ERROR: {e}"
            time.sleep(3)


def shot_prompt(shot):
    return (
        f"段号：{shot['id']}\n"
        f"台词：{shot['text']}\n"
        f"节奏意图：{shot.get('节奏意图', '建立')}\n"
        f"信息点：{shot.get('info_point', '')}\n"
        f"预计时长：{shot.get('duration_est', shot.get('est_seconds', 10))} 秒\n\n"
        f"请为这段设计完整的视觉导演方案。"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("enhanced_json")
    ap.add_argument("--out-dir", default="03_导演设计")
    ap.add_argument("--model", default="strata")
    ap.add_argument("--only-scenes", action="store_true", help="跳过整体方案（续跑）")
    ap.add_argument("--directions", action="store_true", help="阶段A前出3个创意方向供用户选")
    ap.add_argument("--selected-direction", default=None, help="指定选定的方向名（配合--directions使用后手动传入）")
    ap.add_argument("--style", default=None, help="风格名（styles/<名>.md，注入三个 system prompt）")
    ap.add_argument("--max-shots", type=int, default=9999)
    ap.add_argument("--concurrency", type=int, default=4, help="阶段B 逐段设计并行度（默认4；本地服务排队串行，安全值）")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    style_block = load_style(args.style)
    if args.style:
        print(f"[风格] 已注入: {args.style}（{len(style_block)} 字）")

    # 流水线契约：上游（阶段4分镜增强）必须已落盘
    pc.check_input(args.enhanced_json, "阶段2分段产物")

    shots = json.load(open(args.enhanced_json, encoding="utf-8"))
    shots = [s for s in shots if int(s["id"][4:]) <= args.max_shots]
    print(f"共 {len(shots)} 段")

    # 阶段A-前置：创意方向卡（出 2-3 个方向供用户选；JSON 契约 + md 人读双落盘）
    directions_file = os.path.join(args.out_dir, "00_创意方向卡.md")
    directions_json_file = os.path.join(args.out_dir, "00_创意方向卡.json")
    if args.directions and not os.path.exists(directions_file) and not os.path.exists(directions_json_file):
        summary = "\n".join(
            f"[{s['id']}] {s['text'][:50]}...（节奏:{s.get('节奏意图','建立')}）"
            for s in shots
        )
        print("[阶段A-前置] LLM 生成 3 个创意方向（JSON 契约）...")
        directions_md, directions_obj = "", None
        try:
            obj = llm_json.llm_call_json(
                fill_style(SYSTEM_DIRECTIONS, style_block),
                f"分镜清单（共 {len(shots)} 段）：\n\n{summary}",
                schema.DIRECTIONS_SCHEMA, model=args.model, budget=2000, verbose=False)
            directions_obj = obj
            lines = ["# 创意方向卡（3 选 1）\n"]
            for i, d in enumerate(obj.get("directions", []), 1):
                lines.append(f"## 方向{'一二三四五'[i-1]}：{d.get('方向名', '')}")
                lines.append(f"【视觉主线】{d.get('视觉主线', '')}")
                lines.append(f"【美术风格】{d.get('美术风格', '')}")
                lines.append(f"【最适合表达】{d.get('最适合表达', '')}")
                lines.append(f"【局限】{d.get('局限', '')}\n")
            directions_md = "\n".join(lines)
        except ValueError as e:
            print(f"[阶段A-前置] JSON 契约失败（回退 free-text）: {e}")
            directions = llm_call(
                [{"role": "system", "content": fill_style(SYSTEM_DIRECTIONS_MD, style_block)},
                 {"role": "user", "content": f"分镜清单（共 {len(shots)} 段）：\n\n{summary}"}],
                args.model, max_tokens=1200, timeout=180, answer_marker=True)
            if not directions.startswith("ERROR"):
                directions_md = directions
        if directions_md:
            with open(directions_file, "w", encoding="utf-8") as f:
                f.write(directions_md + "\n\n---\n请选择一个方向（回复“方向一/二/三”或方向名），选定后将据此出整体视觉方案。\n")
            if directions_obj:
                with open(directions_json_file, "w", encoding="utf-8") as f:
                    json.dump(directions_obj, f, ensure_ascii=False, indent=1)
            print(f"[阶段A-前置] → {directions_file}"
                  + (" + 00_创意方向卡.json" if directions_obj else ""))
            print("请用户选择方向后，用 --selected-direction '<方向名>' 重新运行")
            return  # 等用户选方向
        else:
            print(f"[阶段A-前置] FAIL，跳过直接出方案")

    # 阶段A：整体视觉方案（只跑一次）
    overall_file = os.path.join(args.out_dir, "00_整体视觉方案.md")
    if not args.only_scenes and not os.path.exists(overall_file):
        summary = "\n".join(
            f"[{s['id']}] {s['text'][:50]}...（节奏:{s.get('节奏意图','建立')}）"
            for s in shots
        )
        print("[阶段A] LLM 生成整体视觉方案（JSON 契约）...")
        direction_ctx = ""
        if args.selected_direction:
            direction_ctx = f"\n\n用户已选定创意方向：{args.selected_direction}\n请基于此方向出整体视觉方案。\n"
        elif os.path.exists(directions_file):
            direction_ctx = "\n\n参考已生成的创意方向卡（见 00_创意方向卡.md），自由选择最适合的方向出方案。\n"
        # JSON 通道（0907）：free-text 通道曾把思维链带进整体方案落盘，且 max_tokens
        # 截断丢正文尾段；llm_call_json 括号配对提取 + schema 强校验 + budget 自适应根治。
        try:
            plan_obj = llm_json.llm_call_json(
                fill_style(SYSTEM_OVERALL, style_block),
                f"分镜清单（共 {len(shots)} 段）：\n\n{summary}{direction_ctx}\n\n"
                "输出 JSON，六个字段（值=中文字符串，共 400-600 字，不要英文、不要推理过程）："
                "视觉主线 / 场景系统 / 重复母题 / 色彩节奏 / 节奏曲线 / 段间衔接。",
                schema.OVERALL_PLAN_SCHEMA, model=args.model, budget=2500, verbose=False)
            plan = "\n".join(f"【{k}】{plan_obj.get(k, '')}"
                             for k in ("视觉主线", "场景系统", "重复母题", "色彩节奏", "节奏曲线", "段间衔接"))
        except ValueError as e:
            print(f"[阶段A] JSON 契约失败: {e}")
            plan = ""
        if plan:
            with open(overall_file, "w", encoding="utf-8") as f:
                f.write(f"# 全篇整体视觉方案（{len(shots)} 段）\n\n{plan}\n")
            print(f"[阶段A] → {overall_file}")
        else:
            print(f"[阶段A] FAIL")
    else:
        plan = open(overall_file, encoding="utf-8").read() if os.path.exists(overall_file) else ""
        print(f"[阶段A] 已有整体方案（{len(plan)} 字），跳过")

    # 阶段B：逐段导演设计
    done = set()
    for f in os.listdir(args.out_dir):
        m = re.match(r"shot(\d+)\.md", f)
        if m:
            done.add(f"shot{int(m.group(1)):02d}")

    print(f"[阶段B] 逐段设计：已完成 {len(done)}/{len(shots)}")

    # 逐镜并行（每段独立 LLM + schema 强校验，ThreadPoolExecutor 并发）
    # 每段落盘 json+md（线程安全），断点续跑自动跳过已完成
    write_lock = threading.Lock()

    def process_one(shot, extra="", force=False, assigned_move=""):
        sid = shot["id"]
        out_md = os.path.join(args.out_dir, f"{sid}.md")
        out_json = os.path.join(args.out_dir, f"{sid}.json")
        if not force and (sid in done or (os.path.exists(out_json) and not extra)):
            return f"[SKIP] {sid}"
        ctx = f"整体视觉方案（用于保持全篇一致）：\n{plan[:2000]}\n\n---\n" if plan else ""
        if assigned_move:
            ctx += (f"【运镜配额】本段运镜枚举值必须为「{assigned_move}」"
                    f"（全片多样性分配，不可更改；景别与镜头描述可自由发挥）\n\n---\n")
        try:
            design = llm_json.llm_call_json(
                fill_style(SYSTEM_SHOT, style_block), ctx + shot_prompt(shot) + extra,
                schema.DIRECTOR_SHOT_SCHEMA, model=args.model, budget=2500,
                verbose=True)
        except ValueError as e:
            return f"[FAIL] {sid}: {e}"
        if assigned_move:
            design["运镜"] = assigned_move  # 配额最终裁决（LLM 违配额时直接覆盖）
        design = _normalize_shot_level(design)
        # 落盘双格式（线程安全）：.json（结构化，新标准）+ .md（兼容旧下游读取）
        with write_lock:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(design, f, ensure_ascii=False, indent=2)
            with open(out_md, "w", encoding="utf-8") as f:
                f.write(f"# {sid}\n\n")
                for k, v in design.items():
                    if k != "id":
                        f.write(f"【{k}】{v}\n\n")
        return f"[OK] {sid}"

    todo = [s for s in shots if s["id"] not in done]
    # 横移配额（2026-09-07）：全量段序上分配，续跑段已定型的运镜保留
    quota = assign_move_quota([s["id"] for s in shots])
    for s in done:
        quota.pop(s, None)
    lat_total = sum(1 for v in quota.values() if v == "横移")
    print(f"[运镜配额] 横移 {lat_total}/{len(quota)} 段（每 4 段 1 段，<1/3 警戒线）")
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = [ex.submit(process_one, s, "", False, quota.get(s["id"], "")) for s in todo]
        for fut in as_completed(futs):
            print(fut.result())

    # 景别交替事后校验（2026-09-06 根治 s4 ⑦ 连败叫人）：并行生成互不知晓，
    # 相邻段首景别硬重复是结构性产物；违例段带着「上一镜已用 X」约束重生成（≤2 轮）。
    def first_level(txt):
        return schema.shot_first_level(txt)

    for _round in range(3):
        fps = sorted(glob.glob(os.path.join(args.out_dir, "shot*.json")),
                     key=lambda fp: int(re.search(r"shot(\d+)", fp).group(1)))
        designs = {os.path.basename(fp)[:-5]: json.load(open(fp, encoding="utf-8")) for fp in fps}
        sids = sorted(designs)
        bad = [(pa, pb) for pa, pb in zip(sids, sids[1:])
               if first_level(designs[pa].get("镜头", "")) == first_level(designs[pb].get("镜头", ""))
               and first_level(designs[pb].get("镜头", ""))]
        if not bad:
            break
        print(f"\n[景别交替] 第 {_round + 1} 轮：{[b for _, b in bad]} 与上一镜同景别 → 带约束重生成")
        for pa, pb in bad:
            shot = next((s for s in shots if s["id"] == pb), None)
            if shot:
                prev = first_level(designs[pa].get("镜头", ""))
                r = process_one(shot, force=True, extra=(
                    f"\n\n⚠️硬约束：上一镜（{pa}）【镜头】首词已用「{prev}」，"
                    f"本镜【镜头】首词必须是其它景别（内容语义不变，运镜可微调以适配新景别）。"
                    f"可选景别：{'/'.join(schema.SHOT_TYPES)}。"))
                print(f"  [景别交替] {pb}: {r}")
                if r.startswith("[FAIL]"):
                    raise RuntimeError(f"景别交替重生成失败 {pb}: {r}")

    print(f"\n完成: {len(os.listdir(args.out_dir))} 个文件 → {args.out_dir}")

    # 流水线契约：产出落盘标记（供下游 director_to_h3_llm 确认）
    pc.stamp_output(args.out_dir, {"shots": len(shots)})


if __name__ == "__main__":
    main()
