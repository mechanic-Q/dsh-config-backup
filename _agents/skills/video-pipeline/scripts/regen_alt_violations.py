#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regen_alt_violations.py — s5 景别交替事后校验的手动补跑工具（s4 ⑦ 连败现场修复用）。

背景（2026-09-06 daily-china-20260906 s4 连败叫人）：
  s5 内置的事后校验第2轮重生成被 process_one 的 done 集合 SKIP 吞掉（已根治 force=True），
  残留硬重复落到 s4 才暴露。本脚本对已落盘的导演设计补跑同款重生成循环，等价于 s5 第2轮。

用法（在 skill scripts/ 下执行）：
  python3 regen_alt_violations.py <项目>/03_导演设计 <项目>/03_导演设计 [--style 名] [--rounds N]
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema
import llm_json
import director_llm as dl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("design_dir", help="03_导演设计 目录")
    ap.add_argument("plan_dir", help="03_导演设计 目录")
    ap.add_argument("--style", default=None)
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()

    raw_fp = os.path.join(a.design_dir, "分镜清单_raw.json")
    if not os.path.exists(raw_fp):
        sys.exit(f"ERROR: 缺 {raw_fp}")
    shots = {s["id"]: s for s in json.load(open(raw_fp, encoding="utf-8"))}
    plan_fp = os.path.join(a.plan_dir, "00_整体视觉方案.md")
    plan = open(plan_fp, encoding="utf-8").read() if os.path.exists(plan_fp) else ""
    style_block = dl.load_style(a.style)

    def first_level(t):
        return schema.shot_first_level(t)

    for rnd in range(1, a.rounds + 1):
        fps = sorted(glob.glob(os.path.join(a.design_dir, "shot*.json")),
                     key=lambda fp: int(re.search(r"shot(\d+)", fp).group(1)))
        designs = {os.path.basename(fp)[:-5]: json.load(open(fp, encoding="utf-8")) for fp in fps}
        sids = sorted(designs)
        bad = [(pa, pb) for pa, pb in zip(sids, sids[1:])
               if first_level(designs[pa].get("镜头", "")) == first_level(designs[pb].get("镜头", ""))
               and first_level(designs[pb].get("镜头", ""))]
        if not bad:
            print(f"第{rnd}轮: 无重复，收敛")
            break
        print(f"第{rnd}轮: {bad}")
        for pa, pb in bad:
            prev = first_level(designs[pa].get("镜头", ""))
            extra = (f"\n\n⚠️硬约束：上一镜（{pa}）【镜头】首词已用「{prev}」，本镜【镜头】首词必须是其它景别"
                     f"（内容语义不变，运镜可微调以适配新景别）。可选景别：{'/'.join(schema.SHOT_TYPES)}。")
            design = llm_json.llm_call_json(
                dl.fill_style(dl.SYSTEM_SHOT, style_block),
                (f"整体视觉方案（用于保持全篇一致）：\n{plan[:2000]}\n\n---\n"
                 + dl.shot_prompt(shots[pb]) + extra),
                schema.DIRECTOR_SHOT_SCHEMA, model="strata", budget=2500, verbose=False)
            design = dl._normalize_shot_level(design)
            with open(os.path.join(a.design_dir, f"{pb}.json"), "w", encoding="utf-8") as f:
                json.dump(design, f, ensure_ascii=False, indent=2)
            with open(os.path.join(a.design_dir, f"{pb}.md"), "w", encoding="utf-8") as f:
                f.write(f"# {pb}\n\n")
                for k, v in design.items():
                    if k != "id":
                        f.write(f"【{k}】{v}\n\n")
            print(f"  {pb} 重生成: {design.get('镜头', '')[:36]} | 信息点: {str(design.get('信息点'))[:40]}")


if __name__ == "__main__":
    main()
