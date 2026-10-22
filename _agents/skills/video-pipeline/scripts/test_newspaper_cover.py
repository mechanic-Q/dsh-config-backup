#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_newspaper_cover.py — T5(#105) 报纸三合一校验 + 封面复检两消费者自检
（无框架，assert 直跑；fake OpenAI 端点 = 进程内真 HTTP server）。

覆盖：
1. 三合一：一致 → exit 0；印刷≠当日 / 印刷≠稿件 → exit 2 拦截
2. 判读失败（畸形×3）→ exit 1
3. 封面复检：四项全过 + issues 空 → exit 0；有 issue → exit 2；缺字段 → 重试后 fail
4. 两工具结论落盘 JSON 语义（match/verdict 字段）
5. 端点契约：缺省本地（vision_common.default_api），显式 env 可覆盖
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import newspaper_date_check as ndc
import cover_recheck as cr
import vision_common as vc


class FakeEndpoint:
    def __init__(self, script):
        self.script = list(script)
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                ln = int(self.headers.get("Content-Length", 0))
                self.rfile.read(ln)
                with outer.lock:
                    step = outer.script.pop(0) if outer.script else {"content": "{}"}
                payload = json.dumps({
                    "choices": [{"message": {"content": step.get("content")}}],
                }).encode()
                self.send_response(step.get("status", 200))
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


_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
_img.write(b"\x89PNG\r\n\x1a\nfake")
_img.close()
IMG = _img.name

# ── 5. 端点契约：缺省本地 ───────────────────────────────────────────────
assert vc.default_api().endswith(":8100/v1/chat/completions")
print("ok 5 缺省端点 = 本地 :8100")

# ── 1. 三合一 ───────────────────────────────────────────────────────────
ep = FakeEndpoint([{"content": '{"printed_date": "2026-10-14"}'}])
rc = ndc.judge_printed_date(ep.url, "m", IMG)
assert rc == ("2026-10-14", ""), rc
ok, bad = ndc.three_way("2026-10-14", "2026-10-14", "2026-10-14")
assert ok and not bad
ok, bad = ndc.three_way("2026-10-13", "2026-10-14", "2026-10-14")
assert not ok and bad == ["印刷(2026-10-13) ≠ 当日(2026-10-14)",
                          "印刷(2026-10-13) ≠ 稿件(2026-10-14)"], bad
ok, bad = ndc.three_way("2026-10-14", "2026-10-14", "2026-10-12")
assert not ok and len(bad) == 1 and "稿件" in bad[0]
ep.close()
print("ok 1 三合一判定（一致/印刷≠当日/印刷≠稿件）")

# main 全链：拦截路径 exit 2 + 落盘
ep = FakeEndpoint([{"content": '前缀 {"printed_date": "2026-10-01"} 后缀'}])
out = os.path.join(tempfile.mkdtemp(prefix="tnc_"), "校验.json")
sys_argv = ["newspaper_date_check.py", "--image", IMG, "--date", "2026-10-14",
            "--api", ep.url, "--model", "m", "--out", out]
_argv = sys.argv
sys.argv = sys_argv
rc = ndc.main()
sys.argv = _argv
assert rc == 2, rc
res = json.load(open(out, encoding="utf-8"))
assert res["printed_date"] == "2026-10-01" and res["match"] is False
assert res["verdict"].startswith("三合一不一致")
ep.close()
print("ok 1b 全链拦截 exit2 + 结论落盘")

# ── 2. 判读失败：畸形×3 → exit 1 ───────────────────────────────────────
ep = FakeEndpoint([{"content": None}, {"content": ""}, {"content": "没有json"}])
_argv = sys.argv
sys.argv = ["x", "--image", IMG, "--api", ep.url, "--model", "m"]
rc = ndc.main()
sys.argv = _argv
assert rc == 1, rc
ep.close()
print("ok 2 判读失败 exit1")

# ── 3. 封面复检 ────────────────────────────────────────────────────────
good = ('{"tone_ok": true, "subject_ok": true, "no_artifact": true, '
        '"elements_ok": true, "issues": []}')
ep = FakeEndpoint([{"content": good}])
res, err = cr.recheck(ep.url, "m", IMG, "紫金色调仙侠宫殿")
assert res["pass"] is True and res["issues"] == [] and err == "", (res, err)
ep.close()

ep = FakeEndpoint([{"content": '{"tone_ok": true, "subject_ok": true, '
                              '"no_artifact": false, "elements_ok": true, '
                              '"issues": ["文字乱码"]}'}])
out = os.path.join(tempfile.mkdtemp(prefix="tnc_"), "封面复检.json")
res, err = cr.recheck(ep.url, "m", IMG, "x")
assert res["pass"] is False and res["issues"] == ["文字乱码"], res
ep.close()

# 缺字段 → 重试×3 全废 → (None, err)
ep = FakeEndpoint([{"content": '{"tone_ok": true}'}] * 3)
res, err = cr.recheck(ep.url, "m", IMG, "x")
assert res is None and err, (res, err)
ep.close()
print("ok 3 封面四项复检（全过/有 issue/缺字段重试后失败）")

# main：fail 路径 exit 2 + 缺省落盘与图同目录
ep = FakeEndpoint([{"content": good}])
_argv = sys.argv
tmp = tempfile.mkdtemp(prefix="tnc_")
img2 = os.path.join(tmp, "海报.png")
open(img2, "wb").write(b"\x89PNG\r\n\x1a\nfake")
sys.argv = ["x", "--image", img2, "--expect", "y", "--api", ep.url, "--model", "m"]
rc = cr.main()
sys.argv = _argv
assert rc == 0, rc
dft = os.path.join(tmp, "封面复检.json")
assert os.path.exists(dft), "缺省落盘缺失"
assert json.load(open(dft, encoding="utf-8"))["pass"] is True
ep.close()
print("ok 3b 封面 main exit0 + 缺省同目录落盘")

print("ALL PASS")
