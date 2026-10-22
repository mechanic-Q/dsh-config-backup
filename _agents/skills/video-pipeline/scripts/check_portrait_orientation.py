#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_portrait_orientation.py — 竖屏方向约束检测（P0，2026-08-31 用户纠正后新增）

用法：python3 check_portrait_orientation.py <06_H3prompt目录> [--require-constraint] [--exit-on-risk]

检查每段 prompt：
  1. 是否含竖屏约束句（vertical 9:16 portrait video / sky at the very top / camera is held upright；2026-09-11 起为正向表述，否定式已废除）
  2. 是否命中竖屏风险词（环形/环绕/俯视 → 易生成横构图旋转90°）

输出：每段 OK / WARN（风险词）/ FAIL（缺约束）。
退出码：0=全部通过；1=有风险词命中（默认只警告）；2=缺竖屏约束（--require-constraint 时）。
"""
import json, os, sys, glob, re

PORTRAIT_REQUIRED = [
    "vertical 9:16 portrait video",
    "sky at the very top",
    "camera is held upright",
    "sky-ground boundary perfectly level",
]
PORTRAIT_RISK_TERMS = ["ring-shaped arena", "circular colosseum", "circular arena", "ring arena",
                       "from above", "aerial view", "bird's eye", "overhead view", "top view",
                       "panoramic shot", "surrounding stands", "stands rising around", "colosseum"]


def main():
    pdir = sys.argv[1]
    require_constraint = "--require-constraint" in sys.argv
    exit_on_risk = "--exit-on-risk" in sys.argv

    files = sorted(glob.glob(os.path.join(pdir, "shot*.json")))
    if not files:
        print(f"[FAIL] 目录无 shot*.json: {pdir}")
        sys.exit(2)

    risk_count = 0
    fail_count = 0
    for f in files:
        sid = os.path.basename(f)[:-5]
        d = json.load(open(f, encoding="utf-8"))
        p = d.get("prompt", "").lower()
        # 1. 约束检查
        missing = [k for k in PORTRAIT_REQUIRED if k not in p]
        # 2. 风险词
        hits = [t for t in PORTRAIT_RISK_TERMS if t in p]
        if missing:
            fail_count += 1
            print(f"[FAIL] {sid}: 缺竖屏约束 {missing}")
        elif hits:
            risk_count += 1
            print(f"[WARN] {sid}: 竖屏风险词 {hits} —— 环形/环绕/俯视场景易旋转90°，生成后必须抽帧人工复核")
        else:
            print(f"[OK]   {sid}")

    print(f"\n=== {len(files)} 段: OK {len(files)-risk_count-fail_count} / WARN {risk_count} / FAIL {fail_count} ===")
    if fail_count:
        sys.exit(2)
    if exit_on_risk and risk_count:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
