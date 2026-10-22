#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_vision_evidence.py — T11(#121) 判翻证据链自检（无框架，assert 直跑）。

覆盖：
1. 判翻即留证：fake 端点判翻转 → 定罪帧复制到 evidence/round0/（字节一致）+
   台账 s08.vision.evidence 行（sid/round/frame/conf/form）
2. 零翻转 → 不建目录不落行（翻/倾斜/正常各一段，只翻的留）
3. 多轮累积：round0/round1 两次判翻 → 证据两行两目录互不覆盖
4. 风险段首帧单判（10-14 拍板）：判读帧=证据帧=_first，仅 1 次请求
5. 复制失败降级：copy2 抛异常 → 不炸主链，翻转判定照常生效，无证据行
6. build_records：vision.evidence → 数据集行 evidence_frames（在盘过滤）
7. rotation_groundtruth.export：标注行的证据帧进训练清单（同帧同标签）
8. 审核页：report 带 evidence → 机判翻转桶展示证据缩略图；缺盘文件过滤
"""
import base64
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_pipeline as rp
import rotation_dataset as rd
import rotation_groundtruth as rg
import vision_common as vc
from ledger import Ledger

_sleep_real = rp.time.sleep
rp.time.sleep = lambda s: None
_saved = {k: os.environ.pop(k) for k in ("DRIVER_VISION_API", vc.MODE_ENV)
          if k in os.environ}
_saved_calib = rp._JEV_CALIB_PATH
rp._JEV_CALIB_PATH = os.path.join(tempfile.mkdtemp(prefix="tve_cal_"), "无.json")


def jev_flip(z3):
    cand = ("正", "倾", "翻")
    ai = max(range(3), key=lambda k: z3[k])
    top = [{"token": cand[i], "logprob": z3[i], "top_logprobs": []} for i in range(3)]
    return {"content": cand[ai], "logprobs": {"content": [
        {"token": cand[ai], "logprob": z3[ai], "top_logprobs": top}]}}


class FakeEndpoint:
    """按序脚本化 Jev 响应。"""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []  # 请求体记录（单帧单问断言用）
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                ln = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(ln)
                outer.requests.append(json.loads(body))
                step = outer.script.pop(0) if outer.script else jev_flip([-0.1, -6, -3])
                payload = json.dumps({"choices": [{"message": {"content": step["content"]},
                                                   "logprobs": step["logprobs"]}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1/chat/completions"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


FLIP = jev_flip([-3.0, -6.0, -0.1])     # 翻转（conf≈0.945）
OK = jev_flip([-0.1, -6.0, -3.0])       # 正常


def mk_driver(tmp, shots=("shot01",), risk=False):
    d = rp.Driver.__new__(rp.Driver)
    d.slug = tmp
    vdir = os.path.join(tmp, "1080P")
    os.makedirs(vdir)
    os.makedirs(os.path.join(tmp, "_frames"))
    for sid in shots:
        open(os.path.join(vdir, f"daily-x_{sid}.mp4"), "wb").write(b"\0")
        open(os.path.join(tmp, "_frames", f"{sid}.jpg"), "wb").write(b"\xff\xd8mid")
        if risk:
            open(os.path.join(tmp, "_frames", f"{sid}_first.jpg"), "wb").write(b"\xff\xd8first")
            open(os.path.join(tmp, "_frames", f"{sid}_last.jpg"), "wb").write(b"\xff\xd8last")
    d.ledger = Ledger(os.path.join(tmp, "report.json"), t0=0.0)
    d._prompt_portrait_risk = lambda sid: risk
    d.notify = lambda t: None
    return d, vdir


# ── 1. 判翻即留证 ──────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="tve_")
d, vdir = mk_driver(tmp)
ep = FakeEndpoint([FLIP])
os.environ["DRIVER_VISION_API"] = ep.url
assert not os.path.exists(os.path.join(tmp, "report.json")), "前置：未 flush 前不应有盘上台账"
flipped, tilted = d._check_frames(vdir, evidence_tag="round0")
# ADR 0008 决策 7（#189）：判读轮末显式 flush——返回时本轮账已落盘
assert os.path.exists(os.path.join(tmp, "report.json")), "判读轮末未 flush 台账"
assert flipped == ["shot01"] and tilted == [], (flipped, tilted)
ev = d.ledger.get("s08.vision.evidence")
assert ev and len(ev) == 1, ev
row = ev[0]
assert row["sid"] == "shot01" and row["round"] == "round0" and row["form"] == "jev", row
assert 0 < row["conf"] <= 1, row
src = os.path.join(tmp, "_frames", "shot01.jpg")
assert os.path.exists(row["frame"]) and row["frame"] != src, row
assert open(row["frame"], "rb").read() == open(src, "rb").read()  # 字节一致副本
assert os.path.join("evidence", "round0") in row["frame"], row
ep.close()
print("ok 1 判翻即留证（副本字节一致 + 台账行齐）")

# ── 2. 零翻转不落证 ────────────────────────────────────────────────────
tmp2 = tempfile.mkdtemp(prefix="tve_")
d2, vdir2 = mk_driver(tmp2, shots=("shot01", "shot02", "shot03"))
ep = FakeEndpoint([OK, OK, OK])  # 全正常（并发段序不定，脚本同构）
os.environ["DRIVER_VISION_API"] = ep.url
flipped2, tilted2 = d2._check_frames(vdir2, evidence_tag="round0")
assert flipped2 == [] and tilted2 == []
assert not d2.ledger.get("s08.vision.evidence"), "零翻转不得留证据行"
assert not os.path.isdir(os.path.join(tmp2, "_frames", "evidence")), "零翻转不得建目录"
ep.close()
print("ok 2 零翻转零证据（不建目录不落行）")

# ── 3. 多轮累积互不覆盖 ────────────────────────────────────────────────
ep = FakeEndpoint([FLIP, FLIP])
os.environ["DRIVER_VISION_API"] = ep.url
d._check_frames(vdir, evidence_tag="round1")  # 同一 driver 再判一轮仍翻
ev = d.ledger.get("s08.vision.evidence")
assert len(ev) == 2 and [r["round"] for r in ev] == ["round0", "round1"], ev
for r in ev:
    assert os.path.exists(r["frame"]), r  # 两份副本各自在盘
ep.close()
print("ok 3 多轮累积（round0/round1 两行两目录）")

# ── 4. 风险段首帧单判（2026-10-14 拍板：三帧复核废弃）──────────────────
tmp3 = tempfile.mkdtemp(prefix="tve_")
d3, vdir3 = mk_driver(tmp3, risk=True)
_real_ffprobe = rp.ffprobe_dur
rp.ffprobe_dur = lambda p: 10.0  # 假时长（_first 帧已在盘，不走 ffmpeg）
ep = FakeEndpoint([FLIP])  # 首帧单判：仅 1 次请求、判的是 _first 非中帧
os.environ["DRIVER_VISION_API"] = ep.url
flipped3, _ = d3._check_frames(vdir3, evidence_tag="round0")
assert flipped3 == ["shot01"], flipped3
ev3 = d3.ledger.get("s08.vision.evidence")
assert os.path.basename(ev3[0]["frame"]) == "shot01_first.jpg", ev3  # 判读帧=证据帧
assert open(ev3[0]["frame"], "rb").read() == b"\xff\xd8first"
assert len(ep.requests) == 1, len(ep.requests)  # 三帧复核已废：单帧单问
ep.close()
rp.ffprobe_dur = _real_ffprobe
print("ok 4 风险段首帧单判（判读帧/证据帧=_first，仅 1 次请求）")

# ── 5. 复制失败降级不炸主链 ────────────────────────────────────────────
tmp4 = tempfile.mkdtemp(prefix="tve_")
d4, vdir4 = mk_driver(tmp4)
ep = FakeEndpoint([FLIP])
os.environ["DRIVER_VISION_API"] = ep.url
_real_copy2 = rp.shutil.copy2
rp.shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(OSError("磁盘炸了"))
try:
    flipped4, _ = d4._check_frames(vdir4, evidence_tag="round0")
finally:
    rp.shutil.copy2 = _real_copy2
assert flipped4 == ["shot01"], flipped4  # 判定照常生效（重烧主链不受影响）
assert not d4.ledger.get("s08.vision.evidence"), "失败不得留半行"
ep.close()
print("ok 5 复制失败降级（判定生效/无证据行/不炸）")

# ── 6. build_records evidence_frames ──────────────────────────────────
tmp5 = tempfile.mkdtemp(prefix="tve_")
pdir = os.path.join(tmp5, "06_H3prompt")
os.makedirs(pdir)
json.dump({"id": "shot01", "prompt": "static shot."},
          open(os.path.join(pdir, "shot01.json"), "w", encoding="utf-8"))
ev_yes = os.path.join(tmp5, "evidence", "round0", "shot01.jpg")
ev_miss = os.path.join(tmp5, "evidence", "round1", "shot01.jpg")
os.makedirs(os.path.dirname(ev_yes))
open(ev_yes, "wb").write(b"\xff\xd8ev")
recs = rd.build_records(tmp5, "2026-10-14", pdir, os.path.join(tmp5, "03"),
                        {"flipped": ["shot01"], "judge_failed": [],
                         "evidence": [{"sid": "shot01", "round": "round0",
                                       "frame": ev_yes},
                                      {"sid": "shot01", "round": "round1",
                                       "frame": ev_miss}]},
                        {})
assert recs[0]["vision_verdict"] == "翻转"
assert recs[0]["evidence_frames"] == [ev_yes], recs[0]["evidence_frames"]  # 缺盘过滤
print("ok 6 build_records evidence_frames（在盘过滤）")

# ── 7. export 训练清单引用证据帧 ───────────────────────────────────────
ds = os.path.join(tmp5, "ds.jsonl")
with open(ds, "w", encoding="utf-8") as f:
    f.write(json.dumps({"date": "2026-10-14", "slug": "sl", "shot": "shot01",
                        "vision_verdict": "翻转", "user_flagged": True,
                        "user_flagged_source": "t",
                        "evidence_frames": [ev_yes, ev_miss]},
                       ensure_ascii=False) + "\n")
mf = os.path.join(tmp5, "manifest.jsonl")
import io
from contextlib import redirect_stdout
with redirect_stdout(io.StringIO()):
    rg.export(mf, synthetic=False, dataset=ds)
lines = [json.loads(l) for l in open(mf, encoding="utf-8")]
assert [l["frames"] for l in lines] == [[ev_yes]], lines  # 仅在盘证据帧；缺盘剔除
assert lines[0]["label"] == "翻转" and lines[0]["date"] == "2026-10-14"
print("ok 7 export 证据帧进训练清单（同帧同行标签）")

# ── 8. 审核页证据桶 ────────────────────────────────────────────────────
import vision_bench_review as vr
slug = os.path.join(tmp, "daily-china-20261015")
os.makedirs(os.path.join(slug, "_driver"))
os.makedirs(os.path.join(slug, "_frames"))
open(os.path.join(slug, "_frames", "shot01.jpg"), "wb").write(b"\xff\xd8mid")
open(ev_yes, "wb").write(b"\xff\xd8ev")  # 仍在盘
report = {"date": "2026-10-15",
          "s08": {"vision": {"checked": 1, "flipped": ["shot01"], "tilted": [],
                             "judge_failed": [], "form": "jev", "calibrated": False,
                             "evidence": [{"sid": "shot01", "round": "round0",
                                           "frame": ev_yes, "conf": 0.945,
                                           "form": "jev"},
                                          {"sid": "shot01", "round": "round1",
                                           "frame": ev_miss, "conf": 0.9,
                                           "form": "jev"}]}}}
json.dump(report, open(os.path.join(slug, "_driver", "report.json"), "w",
                       encoding="utf-8"), ensure_ascii=False)
rows, _, _ = vr.daily_rows(slug, report, dataset=ds)
assert rows[0]["evidence"] and len(rows[0]["evidence"]) == 1, rows[0]["evidence"]
assert rows[0]["evidence"][0]["round"] == "round0"
html_fp = os.path.join(tmp, "daily-review.html")
vr.daily_page(slug, html_fp, dataset=ds)
html = open(html_fp, encoding="utf-8").read()
assert "证据帧" in html and ev_yes.replace("\\", "/") in html, "证据缩略图缺失"
print("ok 8 审核页机判翻转桶展示证据帧（缺盘过滤）")

rp.time.sleep = _sleep_real
rp._JEV_CALIB_PATH = _saved_calib
for k, v in _saved.items():
    os.environ[k] = v
print("ALL PASS")
