#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev_calibrate.py — Jev 形态置信度校准（T8 #117）：温度缩放拟合 + ECE 报告。

为什么：未校准 softmax 虚高是 Jev 生态公认坑（票面红线）——门控三级阈值
（≥0.90 自动过 / 0.60-0.90 抽检 / <0.60 点名）只对**校准后**置信度生效，
校准参数未落盘时 driver 门控整体停用（reconcile.calibrated=False）。

怎么做：真值 = rotation-dataset.jsonl 的 user_flagged（true=翻转正 /
false=确认负，与 CLIP 训练同源同口径）。对每条真值的在盘帧跑一次 Jev 投影
（vision_common.jev_body，与 driver 判读面同 prompt 同解析），得三候选 logits
z3；温度缩放 p = softmax(z3/T)，拟合 T 最小化真值 NLL（1 维网格+黄金分割，
无需 scipy）。产物：
  references/jev-calibration.json   ← run_pipeline 门控唯一读取源
  docs/spikes/jev-calibration-<日期>.md  ← NLL/ECE 前后对比 + 可靠性表

重跑经济：z3 逐帧缓存 _test/jev-z-cache-<model>.jsonl（键=帧路径+模型），
真值增长后重拟合只补判新增帧，历史帧零推理。

当前口径注（票面已知）：34 条真值全为 翻转/正常 二类（无倾斜真值）——
倾斜类概率由 NLL 隐式压低；倾斜真值入库后重跑即回三分类拟合，无需改码。

用法：
  python scripts/jev_calibrate.py                    # 全量：补判+拟合+落盘
  python scripts/jev_calibrate.py --limit 5          # 试跑口径
  python scripts/jev_calibrate.py --refit            # 只用缓存重拟合（零推理）
"""
import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import vision_common as vc
import rotation_dataset as rd
import rotation_groundtruth as rg
from reconcile import GATE_HI, GATE_LO  # 门控阈值唯一定义处（reconcile.py）

CALIB_OUT = os.path.join(HERE, "..", "references", "jev-calibration.json")


# ── 数学核（纯函数，test_jev_calibrate.py 直测）────────────────────────
def softmax3(z3, temperature=1.0):
    """三候选 softmax——实现收敛 vision_common.softmax3（评审收敛，勿另写）。"""
    return vc.softmax3(z3, temperature)


def nll(samples, temperature):
    """samples = [(z3, label_idx)]；真值类平均 NLL。"""
    total = 0.0
    for z3, y in samples:
        p = softmax3(z3, temperature)[y]
        total -= math.log(max(p, 1e-12))
    return total / max(len(samples), 1)


def fit_temperature(samples):
    """1 维拟合：logspace 粗网格 → 黄金分割细化。NLL(T) 实践光滑单谷；
    拟合上限 50（再大等价均匀分布，无信息）。"""
    grid = [math.exp(x) for x in
            [math.log(0.05) + (math.log(50.0) - math.log(0.05)) * i / 99
             for i in range(100)]]
    best = min(grid, key=lambda t: nll(samples, t))
    i = grid.index(best)
    lo = grid[max(i - 1, 0)]
    hi = grid[min(i + 1, len(grid) - 1)]
    gr = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c = b - gr * (b - a)
    d = a + gr * (b - a)
    fc, fd = nll(samples, c), nll(samples, d)
    for _ in range(60):
        if b - a < 1e-4:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = nll(samples, c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = nll(samples, d)
    t = (a + b) / 2
    return t if nll(samples, t) <= nll(samples, best) else best


def ece(samples, temperature, bins=15):
    """期望校准误差：argmax 正确率 vs 最大置信度，等宽分箱加权。"""
    per = [[] for _ in range(bins)]
    for z3, y in samples:
        p = softmax3(z3, temperature)
        i = max(range(len(p)), key=lambda k: p[k])
        b = min(int(p[i] * bins), bins - 1)
        per[b].append((p[i], i == y))
    total = len(samples)
    err = 0.0
    table = []
    for b, items in enumerate(per):
        if not items:
            continue
        conf = sum(x[0] for x in items) / len(items)
        acc = sum(1 for x in items if x[1]) / len(items)
        err += len(items) / total * abs(acc - conf)
        table.append((b / bins, (b + 1) / bins, len(items), conf, acc))
    return err, table


def accuracy(samples, temperature):
    ok = sum(1 for z3, y in samples
             if max(range(3), key=lambda k: softmax3(z3, temperature)[k]) == y)
    return ok / max(len(samples), 1)


# ── 采样与拟合编排 ─────────────────────────────────────────────────────
def truth_rows(dataset=None):
    """真值行 → (row, label_idx)。翻转=2（VERDICT 顺序 正/倾/翻），确认负=0。"""
    out = []
    for r in rd.load(dataset or rd.DATASET):
        if r.get("user_flagged") is True:
            out.append((r, 2))
        elif r.get("user_flagged") is False:
            out.append((r, 0))
    return out


def load_zcache(model):
    fp = os.path.join(HERE, "..", "_test", f"jev-z-cache-{model}.jsonl")
    cache = {}
    if os.path.exists(fp):
        for line in open(fp, encoding="utf-8"):
            try:
                d = json.loads(line)
                cache[d["frame"]] = d["z3"]
            except Exception:  # noqa: BLE001
                continue
    return fp, cache


def collect_z3(rows, model, api, limit=0, zcache_fp=None, cache=None,
               judge=True):
    """真值行 → samples [(z3, label)]。帧级样本（同 CLIP 训练清单口径：
    中帧 + 首/尾变体各算一条）。judge=False 时只用缓存（--refit）。"""
    samples, judged, missing = [], 0, 0
    out = open(zcache_fp, "a", encoding="utf-8") if (judge and zcache_fp) else None
    try:
        for k, (r, y) in enumerate(rows):
            if limit and judged >= limit:
                break
            frames = rg.frames_of(r)
            if not frames:
                missing += 1
                continue
            for fp in frames:
                z3 = (cache or {}).get(fp)
                if z3 is None:
                    if not judge:
                        continue
                    d = vc.post_chat(api, vc.jev_body(model, fp), timeout=240)
                    z3 = vc.jev_project(d)
                    judged += 1
                    if out:
                        out.write(json.dumps({"frame": fp, "z3": z3,
                                              "model": model,
                                              "label": y},
                                             ensure_ascii=False) + "\n")
                        out.flush()
                        print(f"  [{judged}] {r['date']} {r['shot']} "
                              f"{os.path.basename(fp)} z={['%.2f' % v for v in z3]}")
                samples.append((z3, y))
    finally:
        if out:
            out.close()
    return samples, judged, missing


def render_report(model, n_samples, n_rows, n_flip, t, before, after, table):
    """md 报告：NLL/ECE/accuracy 前后对比 + 校准后可靠性表。"""
    lines = [f"# Jev 置信度校准报告（{time.strftime('%Y-%m-%d')}）\n",
             f"- 模型：{model}｜样本：{n_samples} 帧（真值 {n_rows} 行，"
             f"其中翻转正类帧 {n_flip}）",
             f"- 拟合温度 T = {t:.4f}（<1 = 原始过冷、>1 = 原始过自信）",
             f"- NLL：{before['nll']:.4f} → {after['nll']:.4f}｜"
             f"ECE：{before['ece'] * 100:.2f}% → {after['ece'] * 100:.2f}%｜"
             f"accuracy：{before['acc'] * 100:.2f}% → {after['acc'] * 100:.2f}%"
             f"（帧级 argmax 对行级真值标签；温度缩放不改 argmax，此列前后不变——"
             f"它与 4B 中帧漏检的已知限制一致，衡量判别力；门控可用性取决于"
             f"ECE 而非它）",
             f"- 门控阈值（reconcile.py 唯一定义）：≥{GATE_HI} 自动过 / "
             f"{GATE_LO}-{GATE_HI} 抽检 / <{GATE_LO} 点名（仅校准后生效）\n",
             "## 校准后可靠性表（argmax 置信度分箱）\n",
             "| 置信区间 | n | 平均置信 | 实际正确率 |", "|---|---|---|---|"]
    for lo, hi, n, conf, acc in table:
        lines.append(f"| {lo:.2f}-{hi:.2f} | {n} | {conf * 100:.1f}% | "
                     f"{acc * 100:.1f}% |")
    lines.append("\n重跑：`python scripts/jev_calibrate.py`（真值增长后；"
                 "z 缓存使重拟合零推理）")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Jev 置信度校准（温度缩放 + ECE）")
    ap.add_argument("--api", default=None)
    ap.add_argument("--model", default=vc.DEFAULT_MODEL)
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--out", default=CALIB_OUT)
    ap.add_argument("--limit", type=int, default=0, help="最多补判 N 帧（试跑）")
    ap.add_argument("--refit", action="store_true", help="只用 z 缓存重拟合")
    a = ap.parse_args()

    rows = truth_rows(a.dataset)
    pos = sum(1 for _, y in rows if y == 2)
    print(f"真值：{len(rows)} 行（翻转正 {pos} + 确认负 {len(rows) - pos}）"
          f"模型 {a.model}")
    if not rows:
        print("无真值可校准")
        return 1

    zc_fp, cache = load_zcache(a.model)
    api = a.api or vc.default_api()
    samples, judged, missing = collect_z3(
        rows, a.model, api, limit=a.limit, zcache_fp=zc_fp, cache=cache,
        judge=not a.refit)
    print(f"样本：{len(samples)} 帧（本轮补判 {judged}，缺帧 {missing} 行）")
    if len(samples) < 10:
        print("样本 <10，不足以拟合（先补真值）")
        return 1

    t = fit_temperature(samples)
    before = {"nll": nll(samples, 1.0), "ece": ece(samples, 1.0)[0],
              "acc": accuracy(samples, 1.0)}
    ece_after, table = ece(samples, t)
    after = {"nll": nll(samples, t), "ece": ece_after, "acc": accuracy(samples, t)}
    print(f"拟合 T={t:.4f}｜NLL {before['nll']:.4f}→{after['nll']:.4f}｜"
          f"ECE {before['ece'] * 100:.2f}%→{after['ece'] * 100:.2f}%")

    n_flip = sum(1 for _, y in samples if y == 2)
    calib = {"temperature": round(t, 4), "model": a.model,
             "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "n_samples": len(samples), "n_rows": len(rows),
             "n_flip_frames": n_flip, "n_normal_frames": len(samples) - n_flip,
             "nll_before": round(before["nll"], 4), "nll_after": round(after["nll"], 4),
             "ece_before": round(before["ece"], 4), "ece_after": round(after["ece"], 4),
             "gate": {"hi": GATE_HI, "lo": GATE_LO},
             "note": "user_flagged 真值帧级样本；倾斜类无独立真值（NLL 隐式压低）"}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(calib, f, ensure_ascii=False, indent=2)
    print(f"校准参数 → {os.path.abspath(a.out)}")

    md = render_report(a.model, len(samples), len(rows), n_flip, t, before,
                       after, table)
    md_fp = os.path.join(HERE, "..", "docs", "spikes",
                         f"jev-calibration-{time.strftime('%Y%m%d')}.md")
    os.makedirs(os.path.dirname(md_fp), exist_ok=True)
    with open(md_fp, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"报告 → {md_fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
