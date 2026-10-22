#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_vision_shortcircuit.py — s08 判读短路回归（#137，pitfalls 57 坑3）。

10-17 期：判读服务已确定离线，_check_frames 仍逐段 × 2 模型 × 2 次 × timeout=180 空转，
17 段多耗约 14 分钟。修法：_ensure_vision 返回 bool，离线即整批记 judge_fail 并短路
（不抽帧、不重试）；在线路径行为不变。

用法：python3 scripts/test_vision_shortcircuit.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_pipeline as rp  # noqa: E402


class FakeLedger(object):
    def __init__(self):
        self.data = {}

    def record(self, k, v):
        self.data[k] = v

    def get(self, k):
        return self.data.get(k)

    def flush(self):
        pass


def make_driver(slug):
    d = object.__new__(rp.Driver)
    d.ledger = FakeLedger()
    d.slug = slug
    d.stamps_dir = slug
    d.d = {}
    d.notify = lambda *a, **k: None
    return d


slug = tempfile.mkdtemp(prefix="vision_short_")
vdir = os.path.join(slug, "07_成片", "1080P")
os.makedirs(vdir)
for n in ("shot01", "shot02"):
    open(os.path.join(vdir, "daily-china-20261017_%s_x.mp4" % n), "wb").write(b"")

# ── 场景 1：服务离线 → 整批 judge_fail + 短路（不抽帧、不建 _frames）
d = make_driver(slug)
d._ensure_vision = lambda: False
called = []


def boom(*a, **k):
    called.append(a)
    raise AssertionError("离线时不应调用 extract_h3_frames")


_saved_run_argv = rp.run_argv
rp.run_argv = boom
flipped, tilted = d._check_frames(vdir)
rec = d.ledger.get("s08.vision") or {}
print("[offline] return=%s/%s sh_called=%d skipped=%s judge_failed=%d"
      % (flipped, tilted, len(called), rec.get("skipped"),
         len(rec.get("judge_failed") or [])))
assert flipped == [] and tilted == [], (flipped, tilted)
assert not called, "离线时不得进入抽帧"
assert rec.get("skipped") == "vision_offline", rec
assert len(rec.get("judge_failed") or []) == 2, rec
assert rec.get("checked") == 2, rec
assert not os.path.isdir(os.path.join(slug, "_frames")), "离线时不应建 _frames"

# ── 场景 2：服务在线 → 照旧走到抽帧（用异常证明已进入抽帧步骤）
d2 = make_driver(slug)
d2._ensure_vision = lambda: True


def extract_boom(*a, **k):
    raise RuntimeError("EXTRACT_CALLED")


rp.run_argv = extract_boom
try:
    d2._check_frames(vdir)
    raise AssertionError("在线时应进入抽帧")
except RuntimeError as e:
    assert str(e) == "EXTRACT_CALLED", e

print("test_vision_shortcircuit: 全部通过（离线短路 + 在线照旧）")
