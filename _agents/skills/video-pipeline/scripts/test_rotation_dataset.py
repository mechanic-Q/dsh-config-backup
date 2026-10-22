#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rotation_dataset.py — T3(#103) 判读结论 backend/model 归因字段自检（无框架，assert 直跑）。

覆盖：
1. classify_backend 纯函数：9router/20128 → 云端 + 别名 vision → 实名 glm-4.6v；
   本地端点 → llama-server + 模型名原样
2. build_records 新采集自动带 vision_backend/vision_model；不传 = 存量语义（9router/glm-4.6v）
3. 存量行（缺字段）向后兼容：row_backend/row_model 按 glm-4.6v 解读，load/stats 不炸
4. stats 透视支持按 vision_backend 分组
5. append_records upsert 合并后归因字段保留
6. 回归：verdict 判定语义不变（翻转/未判/正常）
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rotation_dataset as rd


def _mk_prompt_dir(tmp, shots):
    """06_H3prompt / 03_导演设计 最小夹具。shots: [(id, prompt, 镜头)]"""
    p_dir = os.path.join(tmp, "06_H3prompt")
    d_dir = os.path.join(tmp, "03_导演设计")
    os.makedirs(p_dir)
    os.makedirs(d_dir)
    for sid, prompt, lens in shots:
        json.dump({"id": sid, "prompt": prompt, "video_seconds": 3.0,
                   "camera_sanitized": False, "motion_stripped": False,
                   "portrait_risk": False},
                  open(os.path.join(p_dir, f"{sid}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        json.dump({"镜头": lens}, open(os.path.join(d_dir, f"{sid}.json"), "w",
                                       encoding="utf-8"), ensure_ascii=False)
    return p_dir, d_dir


# ── 1. classify_backend 纯函数 ──────────────────────────────────────────
b, m = rd.classify_backend("http://localhost:20128/v1/chat/completions", "vision")
assert (b, m) == ("9router", "glm-4.6v"), (b, m)
b, m = rd.classify_backend("http://192.168.50.231:8100/v1/chat/completions", "qwen3-vl-4b")
assert (b, m) == ("llama-server", "qwen3-vl-4b"), (b, m)
b, m = rd.classify_backend("http://localhost:8100/v1/chat/completions", "vision")
assert (b, m) == ("llama-server", "vision"), (b, m)  # 本地别名不重写
print("ok 1 classify_backend")


# ── 2. build_records 自动归因 ───────────────────────────────────────────
def _fixture(tmp):
    p_dir, d_dir = _mk_prompt_dir(tmp, [
        ("shot01", "Camera pushes forward through clouds.", "前推"),
        ("shot02", "The camera drifts slowly to the right.", "横移"),
    ])
    return build_vision(), p_dir, d_dir


def build_vision():
    return {"flipped": ["shot02"], "judge_failed": ["shot01:503"]}


tmp = tempfile.mkdtemp(prefix="rd_t3_")
vision, p_dir, d_dir = _fixture(tmp)
recs = rd.build_records(tmp, "2026-10-14", p_dir, d_dir, vision,
                        {"shot02": 1}, vision_backend="llama-server",
                        vision_model="qwen3-vl-4b")
assert len(recs) == 2
for r in recs:
    assert r["vision_backend"] == "llama-server", r
    assert r["vision_model"] == "qwen3-vl-4b", r
# 判定语义回归（测试 6 合并于此：翻转/未判/正常）
by_id = {r["shot"]: r["vision_verdict"] for r in recs}
assert by_id == {"shot01": "未判", "shot02": "翻转"}, by_id
print("ok 2 build_records 归因字段 + ok 6 verdict 语义回归")

# 不传 = 存量语义（旧调用方不炸、按 9router/glm-4.6v 落字段）
tmp2 = tempfile.mkdtemp(prefix="rd_t3b_")
vision, p2, d2 = _fixture(tmp2)
recs2 = rd.build_records(tmp2, "2026-10-14", p2, d2, vision, {})
for r in recs2:
    assert r["vision_backend"] == "9router" and r["vision_model"] == "glm-4.6v", r
print("ok 2b 缺省归因 = 9router/glm-4.6v")


# ── 3. 存量行向后兼容 ───────────────────────────────────────────────────
old_row = {"date": "2026-09-05", "slug": "daily-china-20260905", "shot": "shot01",
           "vision_verdict": "正常", "user_flagged": None}
assert rd.row_backend(old_row) == "9router"
assert rd.row_model(old_row) == "glm-4.6v"
ds_path = os.path.join(tmp, "ds.jsonl")
rd.append_records([old_row], ds_path)  # 缺字段的存量行照常写读
rows = rd.load(ds_path)
assert len(rows) == 1 and rd.row_backend(rows[0]) == "9router"
print("ok 3 存量兼容")


# ── 4. stats 按 backend 分组 ────────────────────────────────────────────
new_row = dict(old_row, date="2026-10-14", shot="shot01",
               vision_backend="llama-server", vision_model="qwen3-vl-4b")
rd.append_records([old_row, new_row], ds_path)
buf = io.StringIO()
with redirect_stdout(buf):
    rd.stats(ds_path)
out = buf.getvalue()
assert "vision_backend" in out, out
assert "llama-server" in out and "9router" in out, out
print("ok 4 stats backend 分组")


# ── 5. upsert 保留归因 ──────────────────────────────────────────────────
upd = dict(new_row, vision_verdict="翻转", vision_backend="llama-server",
           vision_model="qwen3-vl-8b")
rd.append_records([upd], ds_path)
rows = rd.load(ds_path)
hit = [r for r in rows if r["date"] == "2026-10-14" and r["shot"] == "shot01"]
assert len(hit) == 1 and hit[0]["vision_verdict"] == "翻转"
assert hit[0]["vision_model"] == "qwen3-vl-8b"
assert len(rows) == 2, len(rows)  # 旧行未被误覆盖/复制
print("ok 5 upsert 保留归因")

print("ALL PASS")
