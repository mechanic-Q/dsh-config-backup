#!/usr/bin/env python3
"""check_overwrite.py — 重构期间防覆盖校验（轻量，供 S1-S7 期间随时跑）

用法：
  python3 check_overwrite.py init    # 记录当前 4 个核心文件的 sha256（首次）
  python3 check_overwrite.py check   # 对比是否被外部改动（每次改动后/任何时刻）

覆盖的文件：schema.py / llm_json.py / director_llm.py / storyboard_photographer.py
（后续 S4-S6 新增/改动文件可 append）

代价：sha256 计算微秒级。S6 后此职责并入 pipeline_contract（正式需求），本脚本可退役。
"""
import hashlib
import json
import os
import sys

CORE_FILES = [
    "schema.py",
    "llm_json.py",
    "director_llm.py",
    "storyboard_photographer.py",
]
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".overwrite_state.json")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if len(sys.argv) < 2:
        print("用法: check_overwrite.py init|check")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "init":
        st = {f: sha256(f) for f in CORE_FILES if os.path.exists(f)}
        json.dump(st, open(STATE, "w"), indent=2)
        print(f"[init] 已记录 {len(st)} 个文件 hash → {STATE}")
        for f, h in st.items():
            print(f"  {f}: {h[:16]}...")
    elif cmd == "check":
        if not os.path.exists(STATE):
            print("⚠️ 未 init，先跑: check_overwrite.py init")
            sys.exit(1)
        st = json.load(open(STATE))
        changed = []
        for f, expected in st.items():
            cur = sha256(f) if os.path.exists(f) else None
            if cur != expected:
                changed.append(f)
        if changed:
            print("⚠️ 以下文件被外部改动（可能被 sibling 覆盖）:")
            for f in changed:
                print(f"  ✗ {f}")
            print("\n恢复建议: git checkout 或从备份恢复")
            sys.exit(2)
        else:
            print(f"✅ {len(st)} 个核心文件无覆盖")
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
