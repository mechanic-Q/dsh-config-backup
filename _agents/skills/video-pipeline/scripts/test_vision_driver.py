#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_vision_driver.py — T2(#104) driver 判读端点切换自检（无框架，assert 直跑）。

fake OpenAI 端点 = 本进程内真 HTTP server（127.0.0.1 随机端口），脚本化响应队列：
正常 verdict / 畸形 JSON / 503 / 空响应 / 慢响应。证明端点切换后判读面语义零变化：
三级判定 + 单帧单问 + 帧轮询 + 重试×2 + 判读留痕点名。

覆盖：
1. 缺省 env：判读端点 = 本地 llama-server（:8100，vision_common.default_api）
2. 显式 DRIVER_VISION_API：一条 env 切回指定端点（云端回退语义）
3. Jev 形态（T8 缺省）：请求带 logprobs/max_tokens=1/JEV_PROMPT，三级分流不变，
   置信度随台账；DRIVER_VISION_MODE=json 显式回退 JSON 形态（请求形状与 T7 一致）
4. 畸形 JSON / 503 / 空响应：重试×2 后 judge_fail 留痕（点名语义不变）
5. 慢响应（timeout 内）：正常出 verdict，不重试
6. _ensure_vision 阶梯：在线复用 / 拉起失败降级留痕（台账 s08.vision_server），
   判读链不炸；显式 env 时跳过托管
7. 置信度门控 e2e（fake 端点 + 假校准文件）：低置信 <0.60 点名、0.60-0.90 抽检、
   ≥0.90 自动过，三级全部出现在台账与 _gate_notes 文案
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
import vision_common as vc
from ledger import Ledger

PNG1PX = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1e480000000049454e44ae426082"
)).decode()


def jev_step(z3, delay=None):
    """Jev 形态脚本步：content=argmax 单字 + 三候选 logprobs（与 llama-server
    实测结构同构；z3 按 [正, 倾, 翻] VERDICT 顺序给 logits）。"""
    cand = ("正", "倾", "翻")
    ai = max(range(3), key=lambda k: z3[k])
    top = [{"token": cand[i], "logprob": z3[i], "top_logprobs": []}
           for i in range(3)]
    step = {"content": cand[ai],
            "logprobs": {"content": [{"token": cand[ai], "logprob": z3[ai],
                                      "top_logprobs": top}]}}
    if delay:
        step["delay"] = delay
    return step


# ── fake OpenAI 端点 ────────────────────────────────────────────────────
class FakeEndpoint:
    """脚本化响应队列：每项为 dict(content=..., status=..., delay=..., logprobs=...)；
    content=None 表示畸形 JSON；logprobs 给定时附进响应（Jev 形态）；记录每次
    请求体供断言。"""

    def __init__(self):
        self.script = []
        self.requests = []
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                ln = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(ln)
                with outer.lock:
                    outer.requests.append(json.loads(body))
                    step = outer.script.pop(0) if outer.script else \
                        {"content": '{"verdict": "正常"}'}
                if step.get("delay"):
                    import time as _t
                    _t.sleep(step["delay"])
                ch = {"message": {"content": step.get("content")}}
                if step.get("logprobs") is not None:
                    ch["logprobs"] = step["logprobs"]
                payload = json.dumps({"choices": [ch],
                                      "usage": {"completion_tokens": 5},
                                      }).encode()
                self.send_response(step.get("status", 200))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):  # 静默
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1/chat/completions"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def mk_frames_driver(tmp):
    """最小 Driver 夹具：vdir 含 shot01.mp4 / _frames 含 shot01.jpg（1px PNG）。"""
    d = rp.Driver.__new__(rp.Driver)
    d.slug = tmp
    vdir = os.path.join(tmp, "1080P")
    os.makedirs(vdir)
    open(os.path.join(vdir, "daily-china-x_shot01.mp4"), "wb").write(b"\0")
    os.makedirs(os.path.join(tmp, "_frames"))
    open(os.path.join(tmp, "_frames", "shot01.jpg"), "wb").write(b"\xff\xd8fake")
    d.ledger = Ledger(os.path.join(tmp, "report.json"), t0=0.0)
    d._prompt_portrait_risk = lambda sid: False  # 关多帧复核支路（另行单测过）
    d.notify = lambda t: None
    return d, vdir


_sleep_real = rp.time.sleep
rp.time.sleep = lambda s: None  # 退避重试零等待（语义不变，测试提速）

_saved_api = os.environ.pop("DRIVER_VISION_API", None)
_saved_mode = os.environ.pop(vc.MODE_ENV, None)
_saved_calib = rp._JEV_CALIB_PATH
# 缺省无校准文件 → 门控停用（置信度只记录）；门控 e2e 在第 7 节注入假校准
rp._JEV_CALIB_PATH = os.path.join(tempfile.mkdtemp(prefix="tvd_cal_"), "无.json")
# ── 1/2. 端点解析：缺省本地、显式 env 覆盖 ─────────────────────────────
assert rp.VISION_API_DEFAULT.endswith(":8100/v1/chat/completions"), rp.VISION_API_DEFAULT
assert rp.VISION_MODEL == "qwen3-vl-4b", rp.VISION_MODEL  # 定档 alias（盲比定档后同源）
os.environ["DRIVER_VISION_API"] = "http://127.0.0.1:20128/v1/chat/completions"
assert rp.urllib_req.__doc__ and "9router" in rp.urllib_req.__doc__
os.environ.pop("DRIVER_VISION_API", None)
print("ok 1/2 端点解析（缺省 :8100，显式 env 可覆盖）")

# ── 3. 三级分流：Jev 形态（T8 缺省）+ JSON 形态（env 显式回退）────────
# Jev：z3 按 [正, 倾, 翻] 给 logits；单 token + logprobs + JEV_PROMPT
for z3, expect_flip, expect_tilt in (([-3.0, -6.0, -0.1], 1, 0),   # 翻
                                     ([-3.0, -0.1, -6.0], 0, 1),   # 倾
                                     ([-0.1, -6.0, -3.0], 0, 0)):  # 正常
    ep = FakeEndpoint()
    os.environ["DRIVER_VISION_API"] = ep.url
    rp.VISION_MODEL = "fake-model"  # 导入期常量：测试直接打模块属性
    ep.script = [jev_step(z3)]
    tmp = tempfile.mkdtemp(prefix="tvd_")
    d, vdir = mk_frames_driver(tmp)
    flipped, tilted = d._check_frames(vdir)
    assert (len(flipped), len(tilted)) == (expect_flip, expect_tilt), (z3, flipped, tilted)
    assert len(ep.requests) == 1, len(ep.requests)  # 单帧单问：1 帧 1 请求
    body = ep.requests[0]
    assert body["model"] == "fake-model" and body["max_tokens"] == vc.JEV_MAX_TOKENS == 1
    assert body["logprobs"] is True and body["top_logprobs"] == vc.JEV_TOP_LOGPROBS
    assert body["messages"][0]["content"][0]["text"] == vc.JEV_PROMPT
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,")
    vconf = d.ledger.get("s08.vision.confidence")
    assert list(vconf) == ["shot01"] and 0.9 < list(vconf.values())[0] <= 1.0, vconf
    assert d.ledger.get("s08.vision.form") == "jev"
    assert d.ledger.get("s08.vision.calibrated") is False  # 无校准 → 门控停用
    assert d.ledger.get("s08.vision.low_conf") == []       # 未校准不点名
    ep.close()
print("ok 3a Jev 三级分流 + logprobs 请求形状 + 置信度入台账（未校准不门控）")

# JSON 形态（DRIVER_VISION_MODE=json 显式回退）：请求形状与 T7 完全一致
os.environ[vc.MODE_ENV] = "json"
for verdict, expect_flip, expect_tilt in (('{"verdict": "翻转"}', 1, 0),
                                          ('{"verdict": "倾斜"}', 0, 1),
                                          ('{"verdict": "正常"}', 0, 0)):
    ep = FakeEndpoint()
    os.environ["DRIVER_VISION_API"] = ep.url
    ep.script = [{"content": verdict}]
    tmp = tempfile.mkdtemp(prefix="tvd_")
    d, vdir = mk_frames_driver(tmp)
    flipped, tilted = d._check_frames(vdir)
    assert (len(flipped), len(tilted)) == (expect_flip, expect_tilt), (verdict, flipped, tilted)
    assert len(ep.requests) == 1, len(ep.requests)
    body = ep.requests[0]
    assert body["model"] == "fake-model" and body["max_tokens"] == vc.JUDGE_MAX_TOKENS
    assert "logprobs" not in body  # JSON 形态不带 logprobs
    assert body["messages"][0]["content"][0]["text"] == vc.JUDGE_PROMPT
    assert d.ledger.get("s08.vision.form") == "json"
    assert d.ledger.get("s08.vision.confidence") == {}
    ep.close()
os.environ.pop(vc.MODE_ENV, None)
print("ok 3b env 回退 JSON 形态（T7 行为零变化）")

# ── 4a. 畸形 JSON：重试×2 → judge_fail ────────────────────────────────
ep = FakeEndpoint()
os.environ["DRIVER_VISION_API"] = ep.url
ep.script = [{"content": None}, {"content": None}]  # 2 attempts × 1 model × 1 frame
tmp = tempfile.mkdtemp(prefix="tvd_")
d, vdir = mk_frames_driver(tmp)
flipped, tilted = d._check_frames(vdir)
assert flipped == [] and tilted == []
assert len(ep.requests) == 2, len(ep.requests)  # 重试×2 证据
assert d.ledger.get("s08.vision.judge_failed"), "留痕点名缺失"
assert d.ledger.get("s08.vision.judge_failed")[0].startswith("shot01:"), \
    d.ledger.get("s08.vision.judge_failed")
ep.close()
print("ok 4a 畸形 JSON → 重试×2 → 留痕")

# ── 4b. 503：同样重试×2 → judge_fail ──────────────────────────────────
ep = FakeEndpoint()
os.environ["DRIVER_VISION_API"] = ep.url
ep.script = [{"content": "x", "status": 503}, {"content": "x", "status": 503}]
tmp = tempfile.mkdtemp(prefix="tvd_")
d, vdir = mk_frames_driver(tmp)
flipped, tilted = d._check_frames(vdir)
assert len(ep.requests) == 2
assert d.ledger.get("s08.vision.judge_failed")[0].startswith("shot01:")
ep.close()
print("ok 4b 503 → 重试×2 → 留痕")

# ── 4c. 空响应（content 空串）：解析失败 → judge_fail ──────────────────
ep = FakeEndpoint()
os.environ["DRIVER_VISION_API"] = ep.url
ep.script = [{"content": ""}, {"content": ""}]
tmp = tempfile.mkdtemp(prefix="tvd_")
d, vdir = mk_frames_driver(tmp)
d._check_frames(vdir)
assert d.ledger.get("s08.vision.judge_failed"), "空响应应留痕"
ep.close()
print("ok 4c 空响应 → 留痕")

# ── 5. 慢响应（timeout 内）：单请求出 verdict，不重试 ──────────────────
ep = FakeEndpoint()
os.environ["DRIVER_VISION_API"] = ep.url
ep.script = [jev_step([-0.1, -6.0, -3.0], delay=2)]
tmp = tempfile.mkdtemp(prefix="tvd_")
d, vdir = mk_frames_driver(tmp)
flipped, tilted = d._check_frames(vdir)
assert len(ep.requests) == 1 and flipped == []
ep.close()
print("ok 5 慢响应不重试")

# ── 6. _ensure_vision 阶梯 ─────────────────────────────────────────────
class _FakeVS:
    calls = []

    @staticmethod
    def ensure_online(wait=None, notify=None):
        _FakeVS.calls.append(wait)
        return _FakeVS.result

os.environ.pop("DRIVER_VISION_API", None)  # 清 4c/5 残留，否则走"显式 env 跳过托管"分支
tmp = tempfile.mkdtemp(prefix="tvd_")
d, _ = mk_frames_driver(tmp)
sys.modules["vision_server"] = _FakeVS

_FakeVS.result = {"online": True, "launched": False, "alias": "qwen3-vl-4b"}
d._ensure_vision()
assert d.ledger.get("s08.vision_server.online") is True, d.ledger.get("s08.vision_server")

_FakeVS.result = {"online": False, "launched": True, "detail": "拉起后未就绪"}
d._ensure_vision()  # 降级路径：不抛
assert d.ledger.get("s08.vision_server.online") is False

# 显式 env：跳过托管（不发探活/拉起）
os.environ["DRIVER_VISION_API"] = ep.url
n = len(_FakeVS.calls)
d._ensure_vision()
assert len(_FakeVS.calls) == n, "显式 env 不得触发托管"
os.environ.pop("DRIVER_VISION_API", None)
del sys.modules["vision_server"]
print("ok 6 阶梯：在线复用/失败降级留痕/显式 env 跳过")

# ── 7. 置信度门控 e2e（T8 #117 AC：三级在九宫格点名文案可见）────────────
# 假校准文件（模型名与测试 VISION_MODEL 对齐 → calibrated=True，门控开）
cal_tmp = tempfile.mkdtemp(prefix="tvd_gate_")
cal_fp = os.path.join(cal_tmp, "jev-calibration.json")
with open(cal_fp, "w", encoding="utf-8") as f:
    json.dump({"temperature": 1.0, "model": "fake-model"}, f)
rp._JEV_CALIB_PATH = cal_fp
rp.VISION_MODEL = "fake-model"

# 三档各来一段：低置信 0.41 点名 / 抽检 0.72 / 自动过 0.95+
for z3, want_low, want_spot in (([0.0, -0.06, -0.5], True, False),   # p正≈0.41
                                ([0.0, -0.55, -6.0], False, True),   # p正≈0.72
                                ([0.0, -3.0, -5.0], False, False)):  # p正≈0.95
    ep = FakeEndpoint()
    os.environ["DRIVER_VISION_API"] = ep.url
    ep.script = [jev_step(z3)]
    tmp = tempfile.mkdtemp(prefix="tvd_")
    d, vdir = mk_frames_driver(tmp)
    d._check_frames(vdir)
    low = d.ledger.get("s08.vision.low_conf")
    spot = d.ledger.get("s08.vision.spot_check")
    assert bool(low) == want_low and bool(spot) == want_spot, (z3, low, spot)
    assert d.ledger.get("s08.vision.calibrated") is True
    ep.close()
# 三档点名文案：_gate_notes 用台账实测值拼装（低置信 + 抽检 + 自动过计数同现）
un, ti, cl, gn = rp._gate_notes([], [], [],
                                {"auto": 2, "spot": ["shot05:0.72"],
                                 "low": ["shot03:0.41"]})
assert "置信度门控" in gn and "shot03:0.41" in gn and "点名" in gn, gn
assert "shot05:0.72" in gn and "抽检" in gn and "其余 2 段" in gn, gn
un2, ti2, cl2, gn2 = rp._gate_notes([], [], [], None)
assert gn2 == ""  # 未校准/JSON 形态：门控不占文案
print("ok 7 门控三级 e2e（点名/抽检/自动过 → 台账 + 点名文案）")

# ── 8. 抽帧缓存时效（#187）─────────────────────────────────────────
# 2026-10-22 生产事故：_first.jpg 只判「不存在才抽」，重烧后旧帧永不失效，
# 5 段补烧全被判翻转、用的却是两天前（10-20 21:03）的画面。
# 不变式：缓存必须晚于源视频，否则等于判了一部已经不存在的片子。
_td = tempfile.mkdtemp(prefix="tvd_stale_")
_src = os.path.join(_td, "v.mp4")
_fp = os.path.join(_td, "v_first.jpg")
with open(_src, "wb") as _f:
    _f.write(b"x")
assert rp.frame_is_stale(_fp, _src) is True       # 缓存缺失 → 过期
with open(_fp, "wb") as _f:
    _f.write(b"x")
os.utime(_fp, (1000, 1000))
os.utime(_src, (3000, 3000))
assert rp.frame_is_stale(_fp, _src) is True       # 缓存比源旧 → 过期
os.utime(_fp, (5000, 5000))
assert rp.frame_is_stale(_fp, _src) is False      # 缓存晚于源 → 新鲜
os.utime(_fp, (3000, 3000))
assert rp.frame_is_stale(_fp, _src) is False      # 同秒 → 不算过期
print("ok 8 抽帧缓存时效（缺失/比源旧=过期，晚于或同秒=新鲜）")

rp.VISION_MODEL = "qwen3-vl-4b"

rp.VISION_MODEL = "qwen3-vl-4b"
rp._JEV_CALIB_PATH = _saved_calib
os.environ.pop(vc.MODE_ENV, None)
if _saved_mode:
    os.environ[vc.MODE_ENV] = _saved_mode
if _saved_api:
    os.environ["DRIVER_VISION_API"] = _saved_api
rp.time.sleep = _sleep_real
print("ALL PASS")
