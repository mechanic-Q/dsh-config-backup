#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reconcile_clip.py — T4(#106) reconcile 纯函数 + CLIP 封装 + 双路装配自检
（无框架，assert 直跑）。

覆盖：
1. reconcile 四象限表驱动：一致 / 冲突升级 / 单路缺失 / 双失败
2. 边角：CLIP 翻转 vs VLM 倾斜 / CLIP 倾斜 vs VLM 正常 / CLIP 正常 vs VLM 翻转 /
   VLM 失败 + CLIP 翻转附加点名 / 置信度缺失
3. clip_probe：npz 存取回环 / 注入 embed_fn 的预测数学（softmax argmax）/ 类名
   越界防护 / 探针缺失降级 / probe_status 对 open_clip 缺装的降级
3b. 四向旋转搜索形态（T7 rotate4）：W4/b4/infer/thr 回环、r*→upright_deg 映射、
   margin 阈值启停、单参 embed_fn 注入必须降级成层失败（不得静默算错）
1c. 探针升主判（T10 #119）：翻转直采信 / VLM 置信度门控停用 / 两路取严 /
   探针缺席自动回落 VLM 单路（AC2 不回归）
4. 假探针端到端演练：_check_frames 全链（fake VLM 端点 + 假探针）→ 分歧段进
   台账 clip_escalated + 确认门④点名文案；无探针时行为与现状一致（无回归）
"""
import base64
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import reconcile as rc
import clip_probe as cp
import run_pipeline as rp
from ledger import Ledger


# ── 1/2. reconcile 表驱动 ───────────────────────────────────────────────
def R(vlm_v, clip_v=None, conf=0.9):
    return rc.reconcile({"verdict": vlm_v}, None if clip_v is None
                        else {"verdict": clip_v, "confidence": conf})

# 象限① 一致 → 采信
for v in ("正常", "倾斜", "翻转"):
    r = R(v, v)
    assert (r["verdict"], r["source"], r["escalate"]) == (v, "both", False), (v, r)
# 象限② 冲突：CLIP 翻转 vs VLM 正常/倾斜 → 升级人审点名（VLM 判定生效）
for v in ("正常", "倾斜"):
    r = R(v, "翻转")
    assert (r["verdict"], r["escalate"], r["confidence"]) == (v, True, 0.9), (v, r)
assert "人审点名" in R("正常", "翻转")["note"]
# 象限③ 单路缺失：CLIP 缺席 → VLM 单路（现状行为）
r = R("正常", None)
assert (r["verdict"], r["source"], r["escalate"], r["confidence"]) == \
    ("正常", "vlm", False, None), r
r = rc.reconcile({"verdict": None}, {"verdict": "垃圾", "confidence": 1.0})
assert (r["verdict"], r["source"]) == ("judge_fail", "none"), r
# 象限④ 双失败 → judge_fail 照旧留痕
r = rc.reconcile({"verdict": None}, None)
assert (r["verdict"], r["source"], r["escalate"]) == ("judge_fail", "none", False), r
# 边角：VLM 失败 + CLIP 翻转 → judge_fail + 附加点名（宁误报不漏报）
r = rc.reconcile({"verdict": None}, {"verdict": "翻转", "confidence": 0.8})
assert (r["verdict"], r["escalate"]) == ("judge_fail", True), r
assert "附加" in r["note"]
# 边角：倾斜级分歧与反向分歧 → 采信 VLM + note 记分歧
r = R("正常", "倾斜")
assert (r["verdict"], r["escalate"]) == ("正常", False) and "分歧" in r["note"], r
r = R("翻转", "正常")
assert (r["verdict"], r["escalate"]) == ("翻转", False) and "分歧" in r["note"], r
# 边角：置信度缺失不炸
r = rc.reconcile({"verdict": "正常"}, {"verdict": "正常"})
assert r["confidence"] == 0.0 and r["verdict"] == "正常", r
print("ok 1/2 reconcile 四象限 + 边角")


# ── 1b. 置信度门控（T8 #117）────────────────────────────────────────────
assert (rc.GATE_HI, rc.GATE_LO) == (0.90, 0.60), (rc.GATE_HI, rc.GATE_LO)
# 红线：未校准 → 置信度只记录、不门控（低置信也不点名不抽检）
r = rc.reconcile({"verdict": "正常", "confidence": 0.42}, None, calibrated=False)
assert (r["verdict"], r["escalate"], r["spot_check"]) == ("正常", False, False), r
# 校准后三级：≥0.90 自动过（both 分支 note 空，零噪音）/ 中间带抽检 / <0.60 点名
r = rc.reconcile({"verdict": "正常", "confidence": 0.95},
                 {"verdict": "正常", "confidence": 0.9}, calibrated=True)
assert (r["escalate"], r["spot_check"], r["note"]) == (False, False, ""), r
r = rc.reconcile({"verdict": "正常", "confidence": 0.72}, None, calibrated=True)
assert (r["escalate"], r["spot_check"]) == (False, True) and "抽检" in r["note"], r
r = rc.reconcile({"verdict": "正常", "confidence": 0.41}, None, calibrated=True)
assert r["escalate"] is True and "点名" in r["note"], r
# 边界闭开：0.60 属抽检带（<0.60 才点名）、0.90 属自动过（<0.90 才抽检）
r = rc.reconcile({"verdict": "正常", "confidence": 0.60}, None, calibrated=True)
assert (r["spot_check"], r["escalate"]) == (True, False), r
r = rc.reconcile({"verdict": "正常", "confidence": 0.90}, None, calibrated=True)
assert (r["spot_check"], r["escalate"]) == (False, False), r
# 低置信 × CLIP 分歧：双重升级，分歧与点名同在 note；缘由旗标机器可读
r = rc.reconcile({"verdict": "正常", "confidence": 0.41},
                 {"verdict": "翻转", "confidence": 0.8}, calibrated=True)
assert r["escalate"] and "分歧" in r["note"] and "点名" in r["note"], r
assert r["escalate_clip"] is True and r["escalate_low"] is True, r
# 单一缘由时旗标不误报
r = rc.reconcile({"verdict": "正常", "confidence": 0.41}, None, calibrated=True)
assert r["escalate_clip"] is False and r["escalate_low"] is True, r
r = rc.reconcile({"verdict": "正常"}, {"verdict": "翻转", "confidence": 0.8},
                 calibrated=False)
assert r["escalate_clip"] is True and r["escalate_low"] is False, r
# CLIP 缺席 + 中间带：单路口径下门控照常作用
r = rc.reconcile({"verdict": "倾斜", "confidence": 0.75}, None, calibrated=True)
assert r["source"] == "vlm" and r["spot_check"] and "缺席" in r["note"] and "抽检" in r["note"], r
# JSON 形态（无置信度）：校准在位也不门控（confidence 通道照旧走 CLIP）
r = rc.reconcile({"verdict": "正常"}, {"verdict": "正常", "confidence": 0.5},
                 calibrated=True)
assert r["escalate"] is False and r["spot_check"] is False and r["confidence"] == 0.5, r
# 非法置信度值 → 当缺失处理，不炸不门控
r = rc.reconcile({"verdict": "正常", "confidence": "垃圾"}, None, calibrated=True)
assert r["escalate"] is False and r["spot_check"] is False, r
# 抽检不改变主判分流：倾斜照旧只标记（spot 与 verdict 无关）
r = rc.reconcile({"verdict": "倾斜", "confidence": 0.70}, None, calibrated=True)
assert (r["verdict"], r["escalate"], r["spot_check"]) == ("倾斜", False, True), r
print("ok 1b 置信度门控三级 + 未校准停用红线")


# ── 1c. 探针升主判（T10 #119 换岗）──────────────────────────────────────
# 探针翻转 → 直接判翻转；不再要求 VLM 同意，也不再降级成"人审点名"
r = rc.reconcile({"verdict": "正常", "confidence": 0.41},
                 {"verdict": "翻转", "confidence": 0.86}, calibrated=True,
                 clip_primary=True)
assert (r["verdict"], r["source"], r["escalate"]) == ("翻转", "clip", False), r
assert "探针主判" in r["note"]
# 主判换岗后 VLM 置信度门控停用（未校准的 4B 置信度不再有意义）
assert r["escalate_low"] is False and r["spot_check"] is False, r
# 两路取严：VLM 说翻转、探针说正常 → 仍记翻转（宁误报不漏报）
r = rc.reconcile({"verdict": "翻转"}, {"verdict": "正常", "confidence": 0.7},
                 clip_primary=True)
assert (r["verdict"], r["source"]) == ("翻转", "vlm"), r
# 探针正常 + VLM 失败 → 正常（source=clip）：这是换岗的核心收益，不再整批 judge_fail
r = rc.reconcile({"verdict": None}, {"verdict": "正常", "confidence": 0.74},
                 clip_primary=True)
assert (r["verdict"], r["source"]) == ("正常", "clip"), r
# 探针缺席 → clip_primary 自动失效，VLM 单路口径与现状逐字一致（AC2 不回归）
r = rc.reconcile({"verdict": "正常", "confidence": 0.41}, None, calibrated=True,
                 clip_primary=True)
assert (r["verdict"], r["source"], r["escalate"]) == ("正常", "vlm", True), r
assert "缺席" in r["note"]
# 双向都失败 → judge_fail 留痕不变
r = rc.reconcile({"verdict": None}, None, clip_primary=True)
assert (r["verdict"], r["source"]) == ("judge_fail", "none"), r
print("ok 1c 探针升主判（翻转直采信 / 两路取严 / 缺席回落 AC2）")


# ── 3. clip_probe ───────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="trc_")
probe_fp = os.path.join(tmp, "probe.npz")
W = np.array([[2.0, 0.0], [0.0, 2.0], [0.0, 0.0]], dtype=np.float32)   # 正常/倾斜/翻转
b = np.array([-0.5, -1.0, -3.0], dtype=np.float32)
cp.save_probe(probe_fp, W, b, ["正常", "倾斜", "翻转"])
p, err = cp.load_probe(probe_fp)
assert p is not None and err == "", err
assert [c for c in np.load(probe_fp)["classes"]] == ["正常", "倾斜", "翻转"]

# 注入 embed_fn：vec=[1,0] → logits=[1.5,-1,-3] → 正常（高分）
v, c = p.predict("x.jpg", embed_fn=lambda ip: np.array([1.0, 0.0]))
assert (v, 0 < c <= 1) == ("正常", True), (v, c)
v, c = p.predict("x.jpg", embed_fn=lambda ip: np.array([0.0, 1.0]))   # → 倾斜
assert v == "倾斜", (v, c)
v, c = p.predict("x.jpg", embed_fn=lambda ip: np.array([0.0, 0.0]))   # b 最大 → 正常
assert v == "正常"
# 类名越界防护（越界类须落在 argmax 位才会被映射层捕获）
cp.save_probe(probe_fp, W, b, ["正常", "横躺", "翻转"])
p2, _ = cp.load_probe(probe_fp)
v, reason = p2.predict("x.jpg", embed_fn=lambda ip: np.array([0.0, 1.0]))
assert v is None and "越界" in reason, (v, reason)
# 探针缺失降级
p3, reason = cp.load_probe(os.path.join(tmp, "不存在.npz"))
assert p3 is None and "缺失" in reason
# 嵌入失败 → 层降级（不炸）
p4, _ = cp.load_probe(probe_fp)
v, reason = p4.predict("x.jpg", embed_fn=lambda ip: (_ for _ in ()).throw(OSError("爆")))
assert v is None and "爆" in reason
st = cp.probe_status(os.path.join(tmp, "不存在.npz"))
assert st["available"] is False and st["reason"]
print("ok 3 clip_probe 数学/越界/缺失/失败降级")


# ── 3b. 四向旋转搜索形态（T7 rotate4 / T10 #119）────────────────────────
# 合成一个「按 r 度转之后被判为正立」的满分头：embed 返回施加旋转的 one-hot，
# 则 p0[r] 只在 r = 把该帧转正的那一档最高 —— 用它验 r*/upright_deg/margin 数学。
rot_fp = os.path.join(tmp, "probe_rot.npz")
W4 = np.eye(4, dtype=np.float32)
b4 = np.zeros(4, dtype=np.float32)
W3z = np.zeros((3, 4), dtype=np.float32)
# rotate_search 工件不带 class3 头（W/b=None）：旧版推理读不到 W 就会报"探针损坏"
# → 层缺席 → 退回 VLM；若写全零头，旧版 argmax 恒为"正常"，是静默永远判正常
cp.save_probe(rot_fp, None, None, ["正常", "倾斜", "翻转"],
              W4=W4, b4=b4, infer="rotate_search", thr=0.0)
pr, err = cp.load_probe(rot_fp)
assert pr is not None and err == "", err
assert pr.rotation_mode is True and pr.infer == "rotate_search", (pr.infer, pr.rotation_mode)
assert pr.W is None and pr.b is None, "四向工件不应带 class3 头"
# 工件字段回环：W4/b4/thr 原样落盘（推理侧只用它们）
_z = np.load(rot_fp)
assert _z["W4"].shape == (4, 4) and _z["b4"].shape == (4,) and float(_z["thr"]) == 0.0
assert str(_z["infer"]) == "rotate_search"
assert "W" not in _z.files and "b" not in _z.files, _z.files
# W 与 W4 同时缺席 = 工件无法判读，落盘时就该拒绝（不许产出哑工件）
try:
    cp.save_probe(rot_fp, None, None, ["正常", "倾斜", "翻转"])
    raise AssertionError("W/W4 全缺仍落盘成功 —— 会产出无法判读的探针")
except AssertionError as _e:
    assert "不能同时缺席" in str(_e), _e
# 缺 class3 头 + 非四向形态（人工构造）→ 必须诚实返回层失败，不许默认判正常
_bare = cp.ClipProbe(W=None, b=None, classes=["正常", "倾斜", "翻转"])
v, reason = _bare.predict("upright.jpg", embed_fn=lambda p: np.zeros(4, dtype=np.float32))
assert v is None and "class3" in str(reason), (v, reason)


def _rot_embedder(true_rot_deg):
    """模拟一张「自身被转了 true_rot_deg」的帧：embed(path, deg) 返回 deg 的 one-hot。"""
    idx = {0: 0, 90: 1, 180: 2, 270: 3}
    off = idx[true_rot_deg]
    return lambda path, deg: np.array([1.0 if i == (idx[deg] + off) % 4 else 0.0
                                       for i in range(4)], dtype=np.float32)


# 正立帧（自身旋转 0）→ r*=0 → 正常，且不误报
v, c = pr.predict("upright.jpg", embed_fn=_rot_embedder(0))
assert v == "正常" and pr.last["r_star"] == 0 and pr.last["frame_rot_deg"] == 0, (v, pr.last)
# 自身转了 90° 的帧 → 再转 270° 才扶正 → r*=270，自身角 frame_rot_deg=90
# （生产实测 10-19 shot05 就是这一档：r*=270 / 自身 90°）
v, c = pr.predict("rot90.jpg", embed_fn=_rot_embedder(90))
assert v == "翻转", (v, pr.last)
assert pr.last["r_star"] == 270 and pr.last["frame_rot_deg"] == 90, pr.last
# 自身 270° → r*=90 / frame_rot_deg=270
v, _ = pr.predict("rot270.jpg", embed_fn=_rot_embedder(270))
assert v == "翻转" and pr.last["r_star"] == 90 and pr.last["frame_rot_deg"] == 270, pr.last
# 自身 180° → r*=180 / frame_rot_deg=180
v, _ = pr.predict("rot180.jpg", embed_fn=_rot_embedder(180))
assert v == "翻转" and pr.last["r_star"] == 180 and pr.last["frame_rot_deg"] == 180, pr.last

# 阈值：把 margin 压到 0.05（类 0 略高于「转正」那档之外的最大项）→ thr=0.5 时不判翻转
weak = np.array([[1.0, 0.0, 0.0, 0.0],
                 [0.0, 1.06, 0.0, 0.0],
                 [0.0, 0.0, 1.0, 0.0],
                 [0.0, 0.0, 0.0, 1.0]], dtype=np.float32)
cp.save_probe(rot_fp, None, None, ["正常", "倾斜", "翻转"],
              W4=weak, b4=b4, infer="rotate_search", thr=0.5)
pw, _ = cp.load_probe(rot_fp)
v, _ = pw.predict("weak.jpg", embed_fn=_rot_embedder(90))
assert v == "正常" and pw.last["margin"] < 0.5, (v, pw.last)   # 证据太弱 → 让位 VLM/人审
cp.save_probe(rot_fp, None, None, ["正常", "倾斜", "翻转"],
              W4=weak, b4=b4, infer="rotate_search", thr=0.0)
pw0, _ = cp.load_probe(rot_fp)
v, _ = pw0.predict("weak.jpg", embed_fn=_rot_embedder(90))
assert v == "翻转", (v, pw0.last)                              # 同帧 thr=0 就点名

# 单参 embed_fn 注入 → 旋转搜索拿不到旋转维度：必须降级成层失败，绝不能静默算错
v, reason = pr.predict("x.jpg", embed_fn=lambda path: np.array([1.0, 0, 0, 0]))
assert v is None and "argument" in str(reason), (v, reason)
print("ok 3b rotate_search 数学/r*→frame_rot_deg/阈值/单参注入降级")


# ── 4. 假探针端到端（fake VLM 端点 + 假探针 → _check_frames 全链）───────
class _FakeEndpoint:
    """z3 给定时返回 Jev 形态响应（T8 缺省形态）；否则返回 content 原文。"""

    def __init__(self, content=None, z3=None):
        self.content = content
        self.z3 = z3
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                ln = int(self.headers.get("Content-Length", 0))
                self.rfile.read(ln)
                if outer.z3 is not None:
                    cand = ("正", "倾", "翻")
                    ai = max(range(3), key=lambda k: outer.z3[k])
                    top = [{"token": cand[i], "logprob": outer.z3[i],
                            "top_logprobs": []} for i in range(3)]
                    ch = {"message": {"content": cand[ai]},
                          "logprobs": {"content": [
                              {"token": cand[ai], "logprob": outer.z3[ai],
                               "top_logprobs": top}]}}
                else:
                    ch = {"message": {"content": outer.content}}
                payload = json.dumps({"choices": [ch]}).encode()
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


class _FakeProbe:
    """假探针：按 shot 名脚本化 verdict；调用记录供断言。"""
    script = {}
    calls = []

    def predict(self, image_path):
        sid = os.path.basename(image_path).split(".")[0]
        _FakeProbe.calls.append(sid)
        v = _FakeProbe.script.get(sid)
        return (v, 0.87) if v else (None, "no-verdict")


def mk_frames_driver(tmp):
    d = rp.Driver.__new__(rp.Driver)
    d.slug = tmp
    vdir = os.path.join(tmp, "1080P")
    os.makedirs(os.path.join(tmp, "_frames"))
    os.makedirs(vdir)
    for sid in ("shot01", "shot02"):
        open(os.path.join(vdir, f"daily-x_{sid}.mp4"), "wb").write(b"\0")
        open(os.path.join(tmp, "_frames", f"{sid}.jpg"), "wb").write(b"\xff\xd8f")
    d.ledger = Ledger(os.path.join(tmp, "report.json"), t0=0.0)
    d._prompt_portrait_risk = lambda sid: False
    d.notify = lambda t: None
    return d, vdir


_sleep_real = rp.time.sleep
rp.time.sleep = lambda s: None
_saved = {k: os.environ.pop(k) for k in ("DRIVER_VISION_API", "DRIVER_VISION_MODEL") if k in os.environ}
os.environ["DRIVER_VISION_MODEL"] = "qwen3-vl-4b"


def _fake_clip_module(status, probe=None):
    """clip_probe 假模块（sys.modules 注入；load 每次给新实例——类属性共享脚本状态）。"""
    return type("FakeClipModule", (), {
        "probe_status": staticmethod(lambda path=None: status),
        "load_probe": staticmethod(lambda path=None: ((probe or _FakeProbe)(), "")),
    })


# 端到端：VLM 全正常（Jev z3）、假探针 shot02 翻转 → 分歧点名
_FakeProbe.script = {"shot01": "正常", "shot02": "翻转"}
_FakeProbe.calls = []
sys.modules["clip_probe"] = _fake_clip_module(
    {"available": True, "classes": ["正常", "倾斜", "翻转"], "clip_model": "fake"},
    _FakeProbe)  # load_probe 返回 _FakeProbe() 实例（predict 是实例方法）
ep = _FakeEndpoint(z3=[-0.1, -6.0, -3.0])
os.environ["DRIVER_VISION_API"] = ep.url
tmp = tempfile.mkdtemp(prefix="trc_")
d, vdir = mk_frames_driver(tmp)
flipped, tilted = d._check_frames(vdir)
assert flipped == [] and tilted == [], (flipped, tilted)   # VLM 判定照常生效（不重烧）
esc = d.ledger.get("s08.vision.clip_escalated")
assert esc and esc[0].startswith("shot02:") and "人审点名" in esc[0], esc
assert sorted(_FakeProbe.calls) == ["shot01", "shot02"], _FakeProbe.calls  # 并发完成序不定
ep.close()

# T9 #118 影子台账：探针 vs VLM 对照落 s08.vision.probe_shadow（不进主链）。
# 上面 shot02 = CLIP翻转 vs VLM正常（点名），shot01 = 双正常
shadow = d.ledger.get("s08.vision.probe_shadow")
assert shadow and len(shadow) == 2, shadow
by = {r["sid"]: r for r in shadow}
assert by["shot01"] == {"sid": "shot01", "probe": "正常", "conf": 0.87,
                        "vlm": "正常", "final": "正常"}, by["shot01"]
assert by["shot02"]["probe"] == "翻转" and by["shot02"]["vlm"] == "正常" \
    and by["shot02"]["final"] == "正常", by["shot02"]  # 分歧只记录不改变主链
# env 门控（DRIVER_PROBE_SHADOW=0）：不落台账，主链判定不受影响
_FakeProbe.script = {"shot01": "正常", "shot02": "正常"}
os.environ["DRIVER_PROBE_SHADOW"] = "0"
ep = _FakeEndpoint(z3=[-0.1, -6.0, -3.0])
os.environ["DRIVER_VISION_API"] = ep.url
tmp = tempfile.mkdtemp(prefix="trc_")
d1, vdir1 = mk_frames_driver(tmp)
d1._check_frames(vdir1)
assert d1.ledger.get("s08.vision.probe_shadow") == [], "影子 env 门控失效"
assert d1.ledger.get("s08.vision.flipped") == []
os.environ.pop("DRIVER_PROBE_SHADOW", None)
ep.close()

# 无探针（层缺席）→ 行为与现状一致：无点名、单路 VLM 判定照常
sys.modules["clip_probe"] = _fake_clip_module({"available": False, "reason": "探针文件缺失"})
ep = _FakeEndpoint(z3=[-0.1, -6.0, -3.0])
os.environ["DRIVER_VISION_API"] = ep.url
tmp = tempfile.mkdtemp(prefix="trc_")
d2, vdir2 = mk_frames_driver(tmp)
flipped, tilted = d2._check_frames(vdir2)
assert d2.ledger.get("s08.vision.clip_escalated") == []
assert d2.ledger.get("s08.vision.clip_layer")["available"] is False
assert d2.ledger.get("s08.vision.flipped") == []
ep.close()

# VLM 失败 + 假探针翻转 → judge_fail 留痕 + 附加点名（装配级：宁误报不漏报）
_FakeProbe.script = {"shot01": "翻转", "shot02": "正常"}
sys.modules["clip_probe"] = _fake_clip_module(
    {"available": True, "classes": ["正常", "倾斜", "翻转"], "clip_model": "fake"},
    _FakeProbe)


class _FailEndpoint:
    def __init__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _ErrHandler)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1/chat/completions"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


class _ErrHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


ep_fail = _FailEndpoint()
os.environ["DRIVER_VISION_API"] = ep_fail.url
tmp = tempfile.mkdtemp(prefix="trc_")
d4, vdir4 = mk_frames_driver(tmp)
flipped, tilted = d4._check_frames(vdir4)
jf = d4.ledger.get("s08.vision.judge_failed")
esc4 = d4.ledger.get("s08.vision.clip_escalated")
assert jf and all(x.startswith("shot0") for x in jf), jf
assert esc4 and esc4[0].startswith("shot01:"), esc4  # shot01 CLIP 翻转附加点名
ep_fail.close()

# _gate_notes 纯函数：三张名单 + 门控三级文案（评审 a1 回归锚 + T8 扩展）
un, ti, cl, gn0 = rp._gate_notes(["shot01:503"], ["shot02"], ["shot03:分歧"])
assert "shot01:503" in un and "未经机检" in un
assert "shot02" in ti and "倾斜" in ti
assert "shot03:分歧" in cl and "宁误报不漏报" in cl
assert gn0 == ""
un2, ti2, cl2, gn2 = rp._gate_notes([], [], [])
assert un2 == "" and ti2 == "" and cl2 == "" and gn2 == ""
_, _, _, gn3 = rp._gate_notes([], [], [],
                              {"auto": 3, "spot": ["shot02:0.72"],
                               "low": ["shot04:0.41"]})
assert "置信度门控" in gn3 and "shot04:0.41" in gn3 and "shot02:0.72" in gn3 \
    and "其余 3 段" in gn3, gn3
print("ok 4b VLM失败+CLIP翻转附加点名 + _gate_notes")

# VLM 判翻转 + 探针缺席：翻转照旧进 flipped（重烧主链零回归）
sys.modules["clip_probe"] = _fake_clip_module({"available": False, "reason": "x"})
ep = _FakeEndpoint(z3=[-3.0, -6.0, -0.1])
os.environ["DRIVER_VISION_API"] = ep.url
tmp = tempfile.mkdtemp(prefix="trc_")
d3, vdir3 = mk_frames_driver(tmp)
flipped, tilted = d3._check_frames(vdir3)
assert flipped == ["shot01", "shot02"], flipped
ep.close()

sys.modules.pop("clip_probe", None)
rp.time.sleep = _sleep_real
for k, v in _saved.items():
    os.environ[k] = v
print("ok 4 假探针端到端 + 层缺席零回归")

print("ALL PASS")
