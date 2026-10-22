#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""enum_correct.py — 景别死锁枚举级纠偏（ADR 0003，#47）。

七项自检⑦「景别交替」连败死锁的自动恢复：自检环节暴露纠偏候选（相邻硬重复对），
driver s05 执行纠偏——只改冲突对中**靠后一段**的设计「景别」闭合枚举（json + md
镜像同步），新值取「与前后镜都不重复的合法枚举中、与原值尺度距离最近者」；纠偏后
重跑七项自检一次，仍失败走现有叫人协议。

权界（ADR 0003 / CONTEXT.md 设计权界）：语义字段（场景/情绪/镜头/信息点）逐字不动；
旧 md-only 设计无闭合枚举字段，不可自动纠偏，保持人工路径。

用法（driver 内部 import；也可独立干跑）：
  python3 enum_correct.py <03_导演设计目录>            # 只探测，打印冲突与拟改值
"""
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

# 纠偏新值只在景别梯上取（运镜/过肩非尺度档不可作景别替身）
_LADDER = schema.SHOT_SCALE_LADDER


def _shot_num(sid):
    m = re.search(r"shot(\d+)", sid)
    return int(m.group(1)) if m else 0


def load_designs(design_dir):
    """加载 shotXX.json（按段号排序）→ [{sid, design, json_path, md_path}]。
    只收结构化设计（含「景别」合法枚举者才可纠偏）；md 镜像路径一并返回。"""
    out = []
    for fp in sorted(glob.glob(os.path.join(design_dir, "shot*.json")),
                     key=lambda p: _shot_num(os.path.basename(p))):
        sid = os.path.basename(fp)[:-5]
        try:
            design = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(design, dict):
            continue
        out.append({"sid": sid, "design": design, "json_path": fp,
                    "md_path": os.path.join(design_dir, f"{sid}.md")})
    return out


def _enum_level(design):
    """设计当前的景别枚举值；非法/缺失返回 ""（不可纠偏，对探测不可见）。"""
    v = design.get("景别")
    return v if v in schema.SHOT_TYPES else ""


def detect_conflicts(design_dir):
    """扫描相邻段「景别」枚举硬重复（完全相同才报，同级放行同⑦语义）。
    返回候选列表 [{prev_id, id, prev, cur}]，按段号序。只报枚举可纠的。
    与 enhancer ⑦ 有意不对称：⑦ 兼容旧 md 设计的【镜头】文本兜底提取，
    此处只认 json 枚举字段——文本兜底重复无闭合枚举可改，保持人工路径。"""
    designs = load_designs(design_dir)
    out = []
    for a, b in zip(designs, designs[1:]):
        va, vb = _enum_level(a["design"]), _enum_level(b["design"])
        if va and va == vb:
            out.append({"prev_id": a["sid"], "id": b["sid"], "prev": va, "cur": vb})
    return out


def pick_replacement(prev, cur, nxt):
    """纠偏新值（ADR 0003）：与前后镜都不硬重复的合法枚举中、与原值尺度距离最近者。
    不重复 = 非「完全相同」（同级放行对不构成阻碍，同⑦判定）；尺度距离按
    SHOT_SCALE_LADDER 梯位差；平手取更紧档（广角端是旋转防疫高危构图，特写方向
    更安全）；原值不在梯上（运镜/过肩）时按梯序取首个合法档。无解返回 ""。"""
    blocked = {v for v in (prev, nxt) if v}
    cands = [v for v in _LADDER if v not in blocked and v != cur]
    if not cands:
        return ""
    if cur in _LADDER:
        i = _LADDER.index(cur)
        # 梯位差升序，平手取更紧档（梯序靠后者）——广角端是旋转防疫高危构图
        return min(cands, key=lambda v: (abs(_LADDER.index(v) - i), -_LADDER.index(v)))
    return cands[0]


def _write_mirror(item, design):
    """落盘 json + md 镜像（md 格式与 director_llm 落盘逐字节同构：# sid + 【k】v）。"""
    with open(item["json_path"], "w", encoding="utf-8") as f:
        json.dump(design, f, ensure_ascii=False, indent=2)
    with open(item["md_path"], "w", encoding="utf-8") as f:
        f.write(f"# {item['sid']}\n\n")
        for k, v in design.items():
            if k != "id":
                f.write(f"【{k}】{v}\n\n")


def apply_corrections(design_dir, candidates=None, now=None):
    """执行纠偏并落盘，返回台账记录列表（driver 入 report.json enum_corrections）。
    candidates 缺省时现场重扫；每条先核实现状仍冲突（靠前纠偏可能已顺带解掉
    后一对，如三连同景别只改中段）。无解/现状已解的候选静默跳过。"""
    designs = {d["sid"]: d for d in load_designs(design_dir)}
    sids = sorted(designs, key=_shot_num)
    if candidates is None:
        candidates = detect_conflicts(design_dir)
    records = []
    for idx, c in enumerate(candidates):
        sid = c["id"]
        item = designs.get(sid)
        if item is None:
            continue
        pos = sids.index(sid)
        prev = _enum_level(designs[sids[pos - 1]]["design"]) if pos > 0 else ""
        nxt = _enum_level(designs[sids[pos + 1]]["design"]) if pos + 1 < len(sids) else ""
        cur = _enum_level(item["design"])
        if not cur or cur != prev:
            continue  # 现状已不冲突（前序纠偏顺带解决或候选过期）
        new = pick_replacement(prev, cur, nxt)
        if not new:
            continue
        item["design"]["景别"] = new  # 只动闭合枚举；语义字段（场景/情绪/镜头等）逐字不动
        _write_mirror(item, item["design"])
        records.append({
            "id": sid, "field": "景别", "from": cur, "to": new,
            "prev": prev, "next": nxt,
            "ts": now or time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return records


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    design_dir = sys.argv[1]
    cands = detect_conflicts(design_dir)
    if not cands:
        print(f"{design_dir}: 无相邻景别硬重复")
        return
    print(f"{design_dir}: {len(cands)} 处相邻硬重复")
    designs = {d["sid"]: d for d in load_designs(design_dir)}
    sids = sorted(designs, key=_shot_num)
    for c in cands:
        pos = sids.index(c["id"])
        nxt = _enum_level(designs[sids[pos + 1]]["design"]) if pos + 1 < len(sids) else ""
        cur = _enum_level(designs[c["id"]]["design"])
        print(f"  {c['prev_id']}({c['prev']}) → {c['id']}({cur})"
              f" next={sids[pos + 1] if pos + 1 < len(sids) else '-'}({nxt})"
              f" 拟改: {cur} → {pick_replacement(c['prev'], cur, nxt)}")


if __name__ == "__main__":
    main()
