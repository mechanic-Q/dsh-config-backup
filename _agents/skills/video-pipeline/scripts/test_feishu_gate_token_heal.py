#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feishu_gate_token_heal.py — 确认门 token 失效自愈回归（#137，本地假飞书服务）。

10-17 期终局：token 在 expire 前被服务端判无效（code=99991663），旧实现既不重取也不
重试，20 次轮询失败后整扇门判 error → driver code=4、s13 发布从未执行（pitfalls 57 坑1）。

本测试钉死「判无效即重取重试一次」这条自愈路径：
    预热带 T1 → 服务端吊销 T1 → 轮询首拉被拒 → 重取 T2 → 重试拿到「通过」→ pass
以及每次失败都落日志（旧实现只在第 20 次落一行，65 分钟静默无法定位起点）。

用法：python3 scripts/test_feishu_gate_token_heal.py   （assert 直跑风格，失败即非 0）
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import feishu_gate as fg  # noqa: E402

# 令牌缓存改到本轮临时目录（不去碰真实缓存）
tmp = tempfile.mkdtemp(prefix="feishu_heal_")
fg.TOKEN_CACHE = os.path.join(tmp, "_feishu_token.json")
if os.path.exists(fg.TOKEN_CACHE):
    os.remove(fg.TOKEN_CACHE)

STATE = {"tokens": 0, "auth_rejects": 0, "valid_polls": 0, "revoked": set()}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if "/open-apis/auth/v3/tenant_access_token/internal" in self.path:
            STATE["tokens"] += 1
            return self._send({"code": 0,
                               "tenant_access_token": "T%d" % STATE["tokens"],
                               "expire": 7200})
        return self._send({"code": 404, "msg": "no such api"})

    def do_GET(self):
        if "/open-apis/im/v1/messages" not in self.path:
            return self._send({"code": 404, "msg": "no such api"})
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        if tok in STATE["revoked"]:
            STATE["auth_rejects"] += 1
            return self._send({"code": 99991663,
                               "msg": "Invalid access token for authorization."})
        STATE["valid_polls"] += 1
        items = []
        if STATE["valid_polls"] >= 3:   # 人审第三次拉取才回「通过」
            items = [{"msg_type": "text", "sender": {"sender_type": "user"},
                      "body": {"content": json.dumps({"text": "通过"})},
                      "update_time": int(time.time() * 1000)}]
        return self._send({"code": 0, "data": {"items": items}})


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]

# 凭证桩（同 test_feishu_gate 惯用法）：本测试只验 token 自愈，不该依赖本机
# ~/.dsh/dsh-im-connect/channels.json —— 无该文件的宿主（CI runner）上 FeishuGate()
# 会在构造器里抛「FEISHU_APP_ID/SECRET 缺失」，与所测目标无关。
fg._load_env = lambda: {"FEISHU_APP_ID": "cli_fixture", "FEISHU_APP_SECRET": "fixture",
                        "FEISHU_DAILY_NEWS_CHAT_ID": "oc_test"}

g = fg.FeishuGate(chat_id="oc_test", domain=base)
g.token()                        # 预热：拿 T1 并落进程外缓存
STATE["revoked"].add("T1")       # 服务端吊销（10-17 22:00 形态）

buf = io.StringIO()
t0 = time.time()
with contextlib.redirect_stdout(buf):
    r = g.gate_confirm(caption=None, since_ms=int(time.time() * 1000) - 1000,
                       timeout_s=60, poll_s=0.2)
out = buf.getvalue()
srv.shutdown()

print("decision:", json.dumps(r, ensure_ascii=False)[:200])
print("elapsed=%.2fs token_fetches=%d auth_rejects=%d"
      % (time.time() - t0, STATE["tokens"], STATE["auth_rejects"]))

assert r["status"] == "pass" and r["action"] == "pass" and r["reply"] == "通过", r
assert STATE["auth_rejects"] == 1, ("应只被拒 1 次（重取后即成功）", STATE)
assert STATE["tokens"] == 2, ("应重取 1 次 token", STATE)
assert "99991663" in out and "重取" in out, ("必须落一行重取日志", out[-500:])
print("test_feishu_gate_token_heal: 全部通过（token 失效自愈 + 每次失败落日志）")
