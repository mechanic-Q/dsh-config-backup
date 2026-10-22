#!/usr/bin/env python3
"""gen_handoff.py — 交接卡生成器（S4：接 schema 强校验）

输入：03_导演设计/shotXX.json|md + 04_分镜增强/分镜清单_enhanced.json + 03_导演设计/00_交接摘要.md
输出：03_导演设计/交接卡/shotNN.json（8 项程序提取 + schema.HANDOFF_SCHEMA 强校验）
"""
import json, os, re, sys, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

RHYTHM_WORDS = schema.RHYTHM_WORDS  # 单一事实源（schema.py）

# 竖屏约束（中文交接卡版，供阶段5 中文阅读；英文注入版在 schema.PORTRAIT_CONSTRAINT）
PORTRAIT_CONSTRAINT_ZH = "9:16 垂直，天空正上，地面正下，不旋转，镜头正立；禁环形/环绕/俯视"

def extract_rhythm(design_txt):
    """从导演设计【情绪基调】首词提取 6 枚举节奏词"""
    m = re.search(r"【情绪基调】\s*([^【\n]+)", design_txt)
    if not m:
        return ""
    head = m.group(1).strip()
    for w in RHYTHM_WORDS:
        if head.startswith(w):
            return w
    return head[:6]  # 无枚举词则截断（诚实标记）

def extract_field(design_txt, key):
    m = re.search(rf"【{key}】\s*([^【\n]+)", design_txt)
    return m.group(1).strip() if m else ""

def extract_shot_decision(design_txt):
    """【镜头】= 导演唯一镜头权威"""
    return extract_field(design_txt, "镜头")

def extract_visual_focus(design_txt):
    return extract_field(design_txt, "视觉重点")

def extract_style_lock(overall_summary):
    """从 00_交接摘要.md 视觉主线提取风格锁（全篇一致）。
    兼容三种格式：'视觉主线：内容' / '**视觉主线**：内容' / '## 视觉主线\n内容'"""
    m = re.search(r"\*{0,2}视觉主线\*{0,2}\s*[:：]\s*(.+)", overall_summary)
    if m:
        return m.group(1).strip()
    # '## 视觉主线' 标题后紧跟非空行（真实摘要常见格式）
    m = re.search(r"#+\s*视觉主线\s*\n+([^\n#]+)", overall_summary)
    if m:
        return m.group(1).strip()
    return ""

def extract_rhythm_fallback(design_txt):
    """导演设计无【情绪基调】字段时，从【镜头】/全文找 6 枚举节奏词"""
    for w in RHYTHM_WORDS:
        if w in design_txt:
            return w
    return ""

PORTRAIT_CONSTRAINT = "9:16 垂直，天空正上，地面正下，不旋转，镜头正立；禁环形/环绕/俯视"

def main():
    ap = argparse.ArgumentParser(description="交接卡生成器")
    ap.add_argument("design_dir", help="03_导演设计/")
    ap.add_argument("enhanced_json", help="04_分镜增强/分镜清单_enhanced.json")
    ap.add_argument("overall_summary", help="03_导演设计/00_交接摘要.md")
    ap.add_argument("--out", default=None, help="输出目录（默认 design_dir/交接卡）")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=9999)
    args = ap.parse_args()

    enhanced = {}
    if os.path.exists(args.enhanced_json):
        for s in json.load(open(args.enhanced_json, encoding="utf-8")):
            enhanced[s["id"]] = s

    overall = open(args.overall_summary, encoding="utf-8").read() if os.path.exists(args.overall_summary) else ""
    style_lock = extract_style_lock(overall)

    out_dir = args.out or os.path.join(args.design_dir, "交接卡")
    os.makedirs(out_dir, exist_ok=True)

    for n in range(args.start, args.end + 1):
        sid = f"shot{n:02d}"
        # shotXX.json（新）优先，shotXX.md（旧）fallback
        jfp = os.path.join(args.design_dir, f"{sid}.json")
        mfp = os.path.join(args.design_dir, f"{sid}.md")
        if os.path.exists(jfp):
            try:
                d = json.load(open(jfp, encoding="utf-8"))
                design = "\n".join(f"【{k}】{v}" for k, v in d.items() if k != "id")
            except Exception:
                design = open(mfp, encoding="utf-8").read() if os.path.exists(mfp) else ""
        elif os.path.exists(mfp):
            design = open(mfp, encoding="utf-8").read()
        else:
            continue
        ph = enhanced.get(sid, {})

        # 第一句摘要 = 【视觉重点】前 20 字（程序提取）
        focus = extract_visual_focus(design) or extract_field(design, "场景")
        rhythm = extract_rhythm(design) or extract_rhythm_fallback(design)
        info_pt = ph.get("info_point", "") or extract_field(design, "信息点")
        handoff = {
            "id": sid,
            "一句话摘要": focus[:30] if focus else "",
            "情绪节奏": rhythm,
            "信息点": info_pt,
            "镜头决策": extract_shot_decision(design),
            "视觉重点": focus,
            "衔接_in": ph.get("continuity_handoff", {}).get("in", "") or extract_field(design, "衔接"),
            "衔接_out": ph.get("continuity_handoff", {}).get("out", ""),
            "风格锁": style_lock,
            "竖屏约束": PORTRAIT_CONSTRAINT_ZH,
        }
        # schema 强校验（HANDOFF_SCHEMA 要求 10 字段齐全）
        errs = schema.validate_schema(handoff, schema.HANDOFF_SCHEMA)
        if errs:
            print(f"[交接卡] {sid} ⚠️ 缺字段: {errs[:2]}")
        json.dump(handoff, open(os.path.join(out_dir, f"{sid}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"[交接卡] {sid} ✓ 节奏={handoff['情绪节奏']} 摘要={handoff['一句话摘要'][:18]}")

if __name__ == "__main__":
    main()
