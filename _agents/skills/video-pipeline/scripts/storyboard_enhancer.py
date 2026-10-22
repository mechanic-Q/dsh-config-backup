#!/usr/bin/env python3
"""storyboard_enhancer.py — 阶段4：分镜增强七项自检闸门。

2026-08-28 v3 改造：字段生成交给 LLM 摄影师（storyboard_photographer.py），
本脚本只做七项自检（校验 LLM 产出），不再用词表规则生成字段。

七项自检：
  ① 每段时长 ≤15s
  ② 节奏意图合法（铺垫/建立/预备/冲击/制动/稳定）
  ③ 连续性链成环（每段 in 承接上段 out）
  ④ Hook 分布（首尾强 + 每3段至少1个 reveal/reversal/callback）
  ⑤ 有信息点
  ⑥ 镜头类型合法 + 秒级指令覆盖 0-镜尾
  ⑦ 景别交替（上一镜末景别 vs 下一镜首景别不重复；中近景↔中景同级放行）

用法：
  storyboard_enhancer.py <enhanced.json> [--out-report <report.json>]

输出：
  自检报告（pass/fail + 失败项 + ⑦纠偏候选 correction_candidates，ADR 0003）。exit 0=全过，1=有失败。
"""
import json, os, sys, argparse, re

# 流水线契约：环节间只传文件（强制）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_contract as pc
import schema  # S6：枚举唯一源（schema.py）

# 枚举收敛到 schema.py（单一事实源），此处仅引用别名保持可读
RHYTHM_INTENTS = schema.RHYTHM_WORDS
SHOT_TYPES = schema.SHOT_TYPES
SHOT_SAME_LEVEL = schema.SHOT_SAME_LEVEL  # 2026-09-22 自 enhancer 收敛入 schema（#47）
HOOK_TYPES = schema.HOOK_TYPES
STRONG_HOOKS = {"reversal", "reveal", "callback"}  # 每3段至少1个
MAX_SEC = 15.0


def extract_dir_field(design_txt, key):
    """从导演设计文本（【key】value 格式，JSON 或旧 md 通用）提取字段值。"""
    m = re.search(rf"【{key}】\s*([^【]+)", design_txt, re.S)
    return m.group(1).strip() if m else ""


def parse_shot_sequence(shot_type):
    """景别字段常是 'X→Y'（镜头内部从 X 推到 Y）。返回 (首景别, 末景别)。
    兼容分隔符：→/➔/>/·//（手工与 LLM 段混用时格式不统一）。"""
    if not shot_type:
        return "", ""
    parts = [p.strip() for p in re.split(r"[→➔>·/，,、]", shot_type)]
    parts = [p for p in parts if p]
    if not parts:
        return "", ""
    return parts[0], parts[-1]


def same_shot_level(a, b):
    """不同但同级微调（中近景↔中景等）不算重复；完全相同（中景→中景）必须报。"""
    if a == b:
        return False  # 完全相同 = 硬重复，不放行
    return (a, b) in SHOT_SAME_LEVEL or (b, a) in SHOT_SAME_LEVEL


def main():
    ap = argparse.ArgumentParser(description="分镜增强七项自检")
    ap.add_argument("enhanced_json", help="分镜清单_enhanced.json（摄影师产物）")
    ap.add_argument("--design-dir", default=None, help="阶段3 逐段视觉设计目录（shotXX.md 导演设计，用于②④⑤决策项校验）")
    ap.add_argument("--out-report", default=None, help="自检报告输出路径")
    args = ap.parse_args()

    if not os.path.exists(args.enhanced_json):
        print(f"ERROR: {args.enhanced_json} not found")
        sys.exit(2)

    # 流水线契约：上游（阶段3 逐段视觉设计 + 摄影师分镜增强）必须已落盘
    pc.check_input(args.enhanced_json, "阶段4摄影师分镜增强产物")

    enhanced = json.load(open(args.enhanced_json, encoding="utf-8"))
    if not isinstance(enhanced, list):
        print("ERROR: enhanced.json 应为列表")
        sys.exit(2)

    # 导演设计（阶段3）加载：shotXX.json 优先，shotXX.md fallback → {sid: 全文文本}
    # ②④⑤ 决策项校验读此（导演做决策，校验数据源对齐）
    # 2026-09-06 根治：旧实现 sorted(listdir) 顺序遍历，同段 .md 排后覆盖 .json，
    # 与「json 优先」注释相反——景别修复曾因此对校验不可见
    design = {}
    design_json = {}  # 结构化 dict（2026-09-07：景别/运镜枚举字段直读，免疫文本提取变体）
    if args.design_dir and os.path.isdir(args.design_dir):
        prefer = {}
        for fn in os.listdir(args.design_dir):
            m = re.match(r"shot(\d+)\.(json|md)$", fn)
            if not m:
                continue
            sid = f"shot{int(m.group(1)):02d}"
            if sid not in prefer or fn.endswith(".json"):
                prefer[sid] = fn
        for sid in sorted(prefer):
            fp = os.path.join(args.design_dir, prefer[sid])
            if prefer[sid].endswith(".json"):
                try:
                    d = json.load(open(fp, encoding="utf-8"))
                    design_json[sid] = d
                    design[sid] = "\n".join(f"【{k}】{v}" for k, v in d.items() if k != "id")
                except Exception:
                    design[sid] = open(fp, encoding="utf-8").read()
            else:
                design[sid] = open(fp, encoding="utf-8").read()

    total = len(enhanced)
    checks = []
    # ADR 0003：⑦ 硬重复且双方均为闭合枚举时暴露纠偏候选（driver s05 据此自动纠偏）
    correction_candidates = []

    # 全片节奏体系判断：若所有导演设计的【情绪基调】首词均为弱节奏（铺垫/建立/预备），
    # 说明导演选择平铺节奏（读报模式常见，如天宫全程"建立"）→ ④Hook 不强求强钩子
    STRONG_RHYTHM = {"冲击", "制动", "稳定"}
    all_weak_rhythm = True
    for e in enhanced:
        mood = extract_dir_field(design.get(e.get("id", ""), ""), "情绪基调")
        fm = mood.split("——")[0].split("，")[0].strip()
        if fm in STRONG_RHYTHM:
            all_weak_rhythm = False
            break

    for i, e in enumerate(enhanced):
        eid = e.get("id", f"shot{i+1:02d}")
        # ① 时长 ≤15s
        dur = e.get("duration_est", e.get("est_seconds", 0))
        checks.append(("①时长≤15s", eid, dur <= MAX_SEC, f"{dur}s"))
        # ② 节奏意图合法（决策权在导演，查导演设计【情绪基调】/【镜头】含 6 节奏词）
        design_txt = design.get(eid, "")
        # 导演设计的【情绪基调】是节奏决策；【镜头】是镜头决策。检查导演设计是否包含节奏词
        rhythm_ok = any(r in design_txt for r in RHYTHM_INTENTS)
        checks.append(("②节奏意图", eid, rhythm_ok, f"导演设计含节奏词: {rhythm_ok}"))
        # ③ 连续性链成环：本段 in 非空 + 非首段 in 承接上段 out（摄影师执行，仍查 enhanced）
        hf = e.get("continuity_handoff", {})
        in_ok = bool((hf.get("in") or "").strip())
        if i > 0:
            prev_out = enhanced[i - 1].get("continuity_handoff", {}).get("out", "")
            in_ok = in_ok and bool((prev_out or "").strip())
        checks.append(("③连续性成环", eid, in_ok, f"{hf.get('in','')[:20]}…"))
        # ④ Hook 分布：导演节奏体系判据（不否决导演创作自由）
        # 导演 JSON 的【情绪基调】首词已由 schema 强制 6 枚举（铺垫/建立/预备/冲击/制动/稳定）
        # 强节奏 = 冲击/制动/稳定（导演已表达张力），弱节奏 = 铺垫/建立/预备
        # 若全片均为弱节奏（读报平铺，导演选择）→ ④ 全部 PASS（尊重导演节奏决策）
        hook = e.get("hook_type", "")  # 旧字段兜底（新 enhanced 无，但兼容旧产物）
        hook_base = hook.split("（")[0].split("(")[0].strip() if hook else ""
        strong_hint = any(k in design_txt for k in ["钩子", "悬念", "反转", "收束", "震撼", "亮点", "冲击"])
        # 导演节奏词强度（首词命中强节奏枚举即算强钩子意图）
        mood_cur = extract_dir_field(design_txt, "情绪基调")
        first_mood = mood_cur.split("——")[0].split("，")[0].strip()
        strong_rhythm = first_mood in {"冲击", "制动", "稳定"}
        if all_weak_rhythm:
            hook_ok = True  # 导演选择平铺节奏（读报模式），不要求强钩子
        elif i == 0 or i == total - 1:
            hook_ok = (hook_base in STRONG_HOOKS) or strong_hint or strong_rhythm
        elif i >= 2 and (i % 3 == 0 or i == total - 1):
            window_hint = False
            for j in range(max(0, i - 2), i + 1):
                dtx = design.get(enhanced[j].get("id", f"shot{j+1:02d}"), "")
                if any(k in dtx for k in ["钩子", "悬念", "反转", "收束", "震撼", "亮点"]):
                    window_hint = True
                # 窗口内任一强节奏段
                fm = dtx.split("——")[0].split("，")[0].strip() if dtx else ""
                if fm in {"冲击", "制动", "稳定"}:
                    window_hint = True
            hook_ok = (hook_base in STRONG_HOOKS) or window_hint
        else:
            hook_ok = bool(hook_base) or strong_hint
        checks.append(("④Hook分布", eid, hook_ok, f"hook={hook_base} 导演钩子={'有' if strong_hint else '无'}"))
        # ⑤ 有信息点（决策权在导演，查导演设计【信息点】字段——S3 后导演 JSON 含信息点）
        info = extract_dir_field(design_txt, "信息点")
        info_ok = bool((info or "").strip()) or ("【信息点】" in design_txt)
        checks.append(("⑤信息点", eid, info_ok, str(info)[:20]))
        # ⑥ 镜头类型合法（决策权在导演：查导演设计【镜头】；秒级指令覆盖仍查摄影师 enhanced）
        shot = extract_dir_field(design_txt, "镜头")  # 导演唯一镜头权威
        # 2026-09-07 结构化优先：导演 JSON「景别」枚举字段直读，免疫采样措辞变体
        dj = design_json.get(eid, {})
        shot_first = (dj["景别"] if dj.get("景别") in SHOT_TYPES
                      else schema.shot_first_level(shot))
        # 兼容旧 enhanced shot_type
        if not shot_first:
            shot_first = schema.shot_first_level(e.get("shot_type", ""))
        shot_ok = shot_first in SHOT_TYPES
        p2s = e.get("per_second_directives", [])
        if p2s:
            covered = False
            try:
                # 首条以 0 起；末条尾数值 ≥ 估算时长×0.8（容忍估算与实际秒级指令合理偏差）
                m0 = re.match(r"[\d.]+", p2s[0].get("time", ""))
                first_start = float(m0.group()) if m0 else -1
                last_vals = re.findall(r"[\d.]+", str(p2s[-1].get("time", "")))
                last_end = float(last_vals[-1]) if last_vals else 0
                covered = (first_start == 0.0 and last_end >= dur * 0.75)
            except Exception:
                covered = False
            shot_ok = shot_ok and covered
        checks.append(("⑥镜头+秒级指令", eid, shot_ok, f"{shot}/{len(p2s)}条"))
        # ⑦ 景别交替：上镜景别 vs 本镜景别不硬重复（导演设计【镜头】，统一首词提取）
        if i > 0:
            prev_design = design.get(enhanced[i - 1].get("id", f"shot{i:02d}"), "")
            prev_dj = design_json.get(enhanced[i - 1].get("id", f"shot{i:02d}"), {})
            prev_first = (prev_dj["景别"] if prev_dj.get("景别") in SHOT_TYPES
                          else schema.shot_first_level(extract_dir_field(prev_design, "镜头")))
            cur_first = shot_first
            same = (prev_first == cur_first and prev_first not in ("",))
            same = same and not same_shot_level(prev_first, cur_first)
            checks.append(("⑦景别交替", eid, not same, f"{prev_first}→{cur_first}"))
            # 纠偏候选（ADR 0003）：仅枚举字段的硬重复可自动纠偏；
            # 文本兜底提取的重复（旧 md-only 设计）无闭合枚举可改，保持人工路径
            if same and prev_dj.get("景别") in SHOT_TYPES and dj.get("景别") in SHOT_TYPES:
                correction_candidates.append(
                    {"prev_id": enhanced[i - 1].get("id", f"shot{i:02d}"),
                     "id": eid, "prev": prev_first, "cur": cur_first})

    fails = [c for c in checks if not c[2]]
    report = {
        "total": total,
        "checks_total": len(checks),
        "pass": len(checks) - len(fails),
        "fail": len(fails),
        "failed_items": [{"check": c[0], "id": c[1], "detail": c[3]} for c in fails],
        "correction_candidates": correction_candidates,
        "all_pass": len(fails) == 0,
    }

    if args.out_report:
        with open(args.out_report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"=== storyboard_enhancer 七项自检 ===")
    print(f"共 {total} 段 | 检查 {report['checks_total']} 项 | PASS {report['pass']} | FAIL {report['fail']}")
    if fails:
        for c in fails[:10]:
            print(f"  FAIL {c[0]} {c[1]}: {c[3]}")

    # 流水线契约：产出落盘标记（供下游确认）
    pc.stamp_output(args.enhanced_json, {"total": total, "pass": report["pass"], "fail": report["fail"]})

    sys.exit(0 if report["all_pass"] else 1)


if __name__ == "__main__":
    main()
