#!/usr/bin/env python3
"""segment_script.py — 阶段2：文稿分段器（按口播时长切段）。

规则（skill 硬约束）：
  - 每段 ≤70 汉字（≈11 秒口播 @6.4字/秒，≤15s 上限留余量）
  - 段边界优先在句号/感叹号/问号处
  - 超长句强制二分（不硬切词中间）
  - 可短不可长

用法：
  segment_script.py <input.md> <output.json> [--max-chars 70]

输出：
  JSON 列表 [{id, section, text, chars, est_seconds}]
  id: shot01..shotNN, est_seconds = chars/CHAR_PER_SEC (不含余量，增强时再加)
"""
import json, os, re, sys, argparse

# 硬约束：阶段2运行前强制校验阶段1口播稿已落盘且合规（来源头+URL+验证表）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_contract as pc
import schema

CHAR_PER_SEC = schema.CHAR_PER_SEC  # 单一事实源（schema.py，实测 6.4）


def split_sentences(text):
    """按中文标点切成句子（保留标点）。"""
    # 在句号/感叹号/问号/省略号后断开
    parts = re.split(r'(?<=[。！？…])', text)
    return [p.strip() for p in parts if p.strip()]


def split_long(sentence, max_chars):
    """超长句按逗号/分号/顿号二分，再不行按长度硬切。"""
    if len(sentence) <= max_chars:
        return [sentence]
    # 先按逗号分
    segs = re.split(r'(?<=[，；、])', sentence)
    if len(segs) > 1:
        # 贪心合并到 ≤max_chars
        merged = []
        cur = ""
        for s in segs:
            if len(cur) + len(s) <= max_chars:
                cur += s
            else:
                if cur:
                    merged.append(cur)
                cur = s
        if cur:
            merged.append(cur)
        # 合并后仍有超长片段（长句无内部逗号）→ 直接硬切，不递归
        out = []
        for m in merged:
            if len(m) > max_chars:
                out.extend([m[i:i+max_chars] for i in range(0, len(m), max_chars)])
            else:
                out.append(m)
        return out
    # 无逗号：按长度硬切
    return [sentence[i:i+max_chars] for i in range(0, len(sentence), max_chars)]


def extract_body(md):
    """提取正文：去标题/去引用/去验证表，保留叙述段落。

    口播稿含信息源验证表（阶段1合规指纹），但表格内容不是口播正文，
    必须排除——否则分段器会把表格行切成段混入口播台词。
    """
    lines = md.split('\n')
    in_table = False
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        # 元信息区（信息源验证表/自检门）先于通用 # 过滤处理——否则 ## 头被
        # startswith('#') 提前吞掉，in_table 永远不触发
        if s.startswith('## 自检门') or s.startswith('## 信息源验证'):
            in_table = True
            continue
        if in_table and s.startswith('## ') and not s.startswith('## 自检门') and not s.startswith('## 信息源验证'):
            in_table = False  # 新小节开始，退出元信息区
        if in_table:
            continue
        if s.startswith('#') or s.startswith('>') or s.startswith('---'):
            continue
        # 跳过独立表格行（| 开头）
        if s.startswith('|'):
            continue
        # 去 markdown 强调符号
        s = re.sub(r'[*_`]', '', s)
        # 编辑占位行/句内占位串（如"（当日无真实报道，栏目留空）"）——进 TTS 会被念出
        cleaned = pc.strip_placeholder_lines(s, log=lambda m: print(m))
        if not cleaned.strip():
            continue
        s = cleaned.strip()
        out.append(s)
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser(description="文稿分段器")
    ap.add_argument("input_md")
    ap.add_argument("output_json")
    ap.add_argument("--max-chars", type=int, default=70)
    args = ap.parse_args()

    # 硬约束闸门：阶段1口播稿必须已落盘且合规
    pc.require_stage0(args.input_md)

    md = open(args.input_md, encoding="utf-8").read()
    body = extract_body(md)
    sentences = split_sentences(body)

    segments = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) <= args.max_chars:
            cur += s
        else:
            if cur:
                segments.append(cur)
            # 超长句拆分
            if len(s) > args.max_chars:
                for part in split_long(s, args.max_chars):
                    segments.append(part)
                cur = ""
            else:
                cur = s
    if cur:
        segments.append(cur)

    out = []
    for i, seg in enumerate(segments):
        chars = len(seg)
        out.append({
            "id": f"shot{i+1:02d}",
            "section": "正文",
            "text": seg,
            "chars": chars,
            "est_seconds": round(chars / CHAR_PER_SEC, 1),
        })

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    over = [s for s in out if s["chars"] > args.max_chars]

    # 落盘完成标记
    pc.stamp_output(args.output_json, {"total": len(out), "over_limit": len(over)})

    print(f"=== 分段完成 ===")
    print(f"共 {len(out)} 段 | 超限 {len(over)} 段")
    for s in out[:3]:
        print(f"  [{s['id']}] {s['chars']}字 {s['est_seconds']}s: {s['text'][:40]}")
    if over:
        for s in over[:5]:
            print(f"  OVER [{s['id']}] {s['chars']}字: {s['text'][:30]}")
    print(f"→ {args.output_json}")


if __name__ == "__main__":
    main()
