#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_daily_review.py — T9(#118) 日常判定审核视图 + 真值一键回填闭环自检
（无框架，assert 直跑）。

覆盖：
1. rotation_dataset.set_user_flag：只动真值字段不动采集字段 / missing 上报 /
   幂等重放（人审改判覆盖）
2. daily_rows：report.json s08.vision → 逐段审核行（翻转/未判/点名/探针分歧/
   置信度/库内已有真值徽标）
3. daily_page：HTML 产物含 data-slug/标注按钮/导出脚本（file:// 直引原图）
4. apply_marks：导出 JSON → 数据集回填 → source 记录（真值飞轮闭环）
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rotation_dataset as rd
import vision_bench_review as vr


# ── 1. set_user_flag ───────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="tdr_")
ds = os.path.join(tmp, "ds.jsonl")
with open(ds, "w", encoding="utf-8") as f:
    f.write(json.dumps({"date": "2026-10-13", "slug": "daily-china-20261013", "shot": "shot01",
                        "vision_verdict": "正常", "prompt_lateral_leak": True,
                        "user_flagged": None}, ensure_ascii=False) + "\n")
    f.write(json.dumps({"date": "2026-10-13", "slug": "daily-china-20261013", "shot": "shot02",
                        "vision_verdict": "正常", "user_flagged": None},
                       ensure_ascii=False) + "\n")
    f.write(json.dumps({"date": "2026-10-13", "slug": "daily-china-20261013", "shot": "shot03",
                        "vision_verdict": "未判", "user_flagged": None},
                       ensure_ascii=False) + "\n")
updated, missing = rd.set_user_flag(
    [("2026-10-13", "daily-china-20261013", "shot01", True),
     ("2026-10-13", "daily-china-20261013", "shot09", False)],  # shot09 不在库
    dataset_path=ds, source="daily-review-2026-10-13")
assert updated == [("2026-10-13", "daily-china-20261013", "shot01")] and \
    missing == [("2026-10-13", "daily-china-20261013", "shot09")], (updated, missing)
rows_ds = {r["shot"]: r for r in rd.load(ds)}
r1, r2 = rows_ds["shot01"], rows_ds["shot02"]
assert r1["user_flagged"] is True and \
    r1["user_flagged_source"] == "daily-review-2026-10-13", r1
assert r1["vision_verdict"] == "正常" and r1["prompt_lateral_leak"] is True, r1  # 采集字段原样
assert r2["user_flagged"] is None and "user_flagged_source" not in r2, r2  # 未标记行零接触
# 人审改判覆盖（幂等重放）：shot02 标 False（shot01 保持 True 供第 2 节徽标用）
rd.set_user_flag([("2026-10-13", "daily-china-20261013", "shot02", False)], dataset_path=ds)
assert rd.load(ds)[1]["user_flagged"] is False
print("ok 1 set_user_flag（只动真值字段/missing 上报/改判覆盖）")


# ── 2-4. 日常视图 + 回填闭环 ───────────────────────────────────────────
slug = os.path.join(tmp, "daily-china-20261013")
os.makedirs(os.path.join(slug, "_driver"))
os.makedirs(os.path.join(slug, "_frames"))
report = {"date": "2026-10-13", "slug": slug,
          "s08": {"vision": {
              "checked": 3, "flipped": ["shot02"], "tilted": [],
              "judge_failed": ["shot03:503 Service"],
              "clip_escalated": [], "low_conf": ["shot01:0.41"],
              "spot_check": [], "gate_auto": 0,
              "confidence": {"shot01": 0.41},
              "form": "jev", "calibrated": True,
              "probe_shadow": [{"sid": "shot01", "probe": "翻转", "conf": 0.81,
                                "vlm": "正常", "final": "正常"},
                               {"sid": "shot02", "probe": "翻转", "conf": 0.77,
                                "vlm": "翻转", "final": "翻转"}]}}}
with open(os.path.join(slug, "_driver", "report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False)
for sid in ("shot01", "shot02", "shot03"):
    open(os.path.join(slug, "_frames", f"{sid}.jpg"), "wb").write(b"\xff\xd8fake")
open(os.path.join(slug, "_frames", "shot01_first.jpg"), "wb").write(b"\xff\xd8f")  # 变体不单列

rows, date, slug_name = vr.daily_rows(slug, report, dataset=ds)
assert (date, slug_name) == ("2026-10-13", "daily-china-20261013"), (date, slug_name)
assert [r["shot"] for r in rows] == ["shot01", "shot02", "shot03"], rows  # 变体未混入
by = {r["shot"]: r for r in rows}
assert by["shot02"]["final"] == "翻转" and by["shot03"]["final"] == "未判"
assert by["shot01"]["final"] == "正常" and by["shot01"]["vlm_conf"] == 0.41
assert by["shot01"]["reasons"] == ["低置信:shot01:0.41"], by["shot01"]["reasons"]
assert by["shot01"]["probe_agree"] is False          # 探针翻转 vs 主判正常
assert by["shot02"]["probe_agree"] is True
assert by["shot01"]["flagged"] is True              # 第 1 节已回填的库内真值
assert by["shot02"]["flagged"] is False and by["shot03"]["flagged"] is None

html_fp = os.path.join(tmp, "daily-review.html")
vr.daily_page(slug, html_fp, dataset=ds)
html = open(html_fp, encoding="utf-8").read()
assert 'data-slug="daily-china-20261013"' in html and "data-date" in html
assert "exportMarks" in html and "翻转</button>" in html
assert "探针分歧段" in html and "机判翻转段" in html
assert "库:未翻" in html and "库:翻转" in html  # 库内已有真值徽标（shot02/shot01）

# apply 闭环：导出 JSON → 回填 → 数据集三行全有真值
result_fp = os.path.join(tmp, "daily-review-2026-10-13.json")
with open(result_fp, "w", encoding="utf-8") as f:
    json.dump({"slug": slug_name, "date": "2026-10-13", "marks": [
        {"date": "2026-10-13", "slug": slug_name, "shot": "shot01", "flagged": True},
        {"date": "2026-10-13", "slug": slug_name, "shot": "shot03", "flagged": False},
        {"date": "2026-10-13", "slug": slug_name, "shot": "shot04", "flagged": True},
    ]}, f, ensure_ascii=False)
import io
from contextlib import redirect_stdout
buf = io.StringIO()
with redirect_stdout(buf):
    rc = vr.apply_marks(result_fp, dataset=ds)
assert rc == 0, buf.getvalue()
flags = {(r["shot"]): r["user_flagged"] for r in rd.load(ds)}
assert flags == {"shot01": True, "shot02": False, "shot03": False}, flags
srcs = {r["shot"]: r["user_flagged_source"] for r in rd.load(ds)}
assert srcs["shot01"] == "daily-review-2026-10-13" \
    and srcs["shot03"] == "daily-review-2026-10-13"  # apply 只动标注过的行
assert srcs["shot02"] == "daily-review"  # 改判轮的 source 原样保留
out = buf.getvalue()
assert "回填 2 行" in out and "shot04" in out  # missing 上报
print("ok 2-4 日常视图分桶/徽标 + 导出→apply 回填闭环")

print("ALL PASS")
