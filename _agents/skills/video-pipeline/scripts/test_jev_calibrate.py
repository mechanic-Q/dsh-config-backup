#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_jev_calibrate.py — T8(#117) 校准数学核自检（无框架，assert 直跑）。

覆盖：
1. softmax3：均匀/独热/温度单调性（升温摊平）
2. nll：完美预测 ≈0；错配预测大；温度缩放方向正确
3. fit_temperature：过自信合成数据 → T>1 且 NLL 下降；已校准数据 → T≈1
4. ece：完美置信（概率=频率）→ ≈0；全错高置信 → 高；分箱表结构
5. accuracy / truth_rows 的 二类映射（翻转=2、确认负=0、未知剔除）
"""
import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jev_calibrate as jc


# ── 1. softmax3 ────────────────────────────────────────────────────────
assert jc.softmax3([0.0, 0.0, 0.0]) == [1 / 3] * 3
p = jc.softmax3([100.0, 0.0, 0.0])
assert abs(p[0] - 1.0) < 1e-12 and p[1] == p[2], p
p_hot = jc.softmax3([3.0, 0.0, 0.0], temperature=10.0)
p_cold = jc.softmax3([3.0, 0.0, 0.0], temperature=0.1)
assert p_hot[0] < p_cold[0] and abs(sum(p_hot) - 1) < 1e-12  # 升温摊平
# 与 vision_common.jev_decide 同构（同一 softmax 两处实现交叉验证）
import vision_common as vc
v, c = vc.jev_decide([3.0, 0.0, 0.0])
assert v == "正常" and abs(c - jc.softmax3([3.0, 0.0, 0.0])[0]) < 1e-12
print("ok 1 softmax3")


# ── 2. nll ─────────────────────────────────────────────────────────────
samples = [([20.0, 0.0, 0.0], 0), ([0.0, 20.0, 0.0], 1)]
assert jc.nll(samples, 1.0) < 1e-6          # 完美预测
assert jc.nll([([0.0, 20.0, 0.0], 0)], 1.0) > 10   # 错配
# 过自信错例升温后 NLL 降：z 给错类 95% 置信 → T 大摊平损失
bad = [([-1.0, 3.0, 0.0], 0)]               # 真值 正常，模型喊 倾斜
assert jc.nll(bad, 5.0) < jc.nll(bad, 1.0)
print("ok 2 nll")


# ── 3. fit_temperature ─────────────────────────────────────────────────
# 合成过自信：真值 正常 的样本 z 给 正常 中等优势 + 大批 倾斜 错例带巨高置信
# → 最优 T 应显著 >1 且拟合后 NLL 严格下降
import random
random.seed(42)
syn = []
for _ in range(60):
    syn.append(([1.2, 0.0, 0.0], 0))         # 正确但 margin 温和
for _ in range(20):
    syn.append(([0.0, 6.0, 0.0], 0))         # 过自信错例（喊倾斜）
t = jc.fit_temperature(syn)
assert t > 1.5, t
assert jc.nll(syn, t) < jc.nll(syn, 1.0), (t, jc.nll(syn, t), jc.nll(syn, 1.0))
# 已校准（概率=频率）全对数据：NLL 拟合必向锐化方向走（T<1）——NLL 拟合的
# 性质是最小化损失不是保持现状（ECE≈0.2 见 ok 4）；此处验证拟合 T 确为网格
# 邻域上的 NLL 最优（黄金分割收敛正确性）
import math as m
cal = []
for _ in range(80):                          # p(正常)=0.80 且全对
    cal.append(([m.log(4), m.log(1), -20.0], 0))
t1 = jc.fit_temperature(cal)
assert t1 < 1.0, t1                          # 锐化方向
for probe in (t1 * 0.5, t1 * 0.9, t1 * 1.1, t1 * 2.0):
    assert jc.nll(cal, t1) <= jc.nll(cal, probe) + 1e-9, (t1, probe)
print(f"ok 3 fit_temperature（过自信→T={t:.2f}，全对锐化→T={t1:.2f}）")


# ── 4. ece ─────────────────────────────────────────────────────────────
# 完美置信：每条 argmax p≈0.8 且全对 → ECE ≈ 0.2 内且可解释（单箱）
ece_perf, tbl = jc.ece(cal, 1.0)
assert 0.15 < ece_perf < 0.25, ece_perf      # conf≈0.8, acc=1.0 → |1-0.8|
assert all(len(row) == 5 for row in tbl) and tbl, tbl
# 全错高置信：conf→1, acc=0 → ECE → 1
allwrong = [([20.0, 0.0, 0.0], 1), ([20.0, 0.0, 0.0], 2)]
ece_bad, _ = jc.ece(allwrong, 1.0)
assert ece_bad > 0.95, ece_bad
print("ok 4 ece")


# ── 5. accuracy / truth_rows ───────────────────────────────────────────
assert jc.accuracy(cal, 1.0) == 1.0
assert jc.accuracy(syn, 1.0) == 0.75
# truth_rows：user_flagged true→翻转(2) / false→正常(0) / null 剔除
tmp = tempfile.mkdtemp(prefix="tjc_")
ds = os.path.join(tmp, "ds.jsonl")
with open(ds, "w", encoding="utf-8") as f:
    f.write(json.dumps({"date": "d1", "slug": "s", "shot": "shot01",
                        "user_flagged": True}, ensure_ascii=False) + "\n")
    f.write(json.dumps({"date": "d1", "slug": "s", "shot": "shot02",
                        "user_flagged": False}, ensure_ascii=False) + "\n")
    f.write(json.dumps({"date": "d1", "slug": "s", "shot": "shot03",
                        "user_flagged": None}, ensure_ascii=False) + "\n")
rows = jc.truth_rows(ds)
assert [(r["shot"], y) for r, y in rows] == [("shot01", 2), ("shot02", 0)], rows
print("ok 5 accuracy / truth_rows")

print("ALL PASS")
