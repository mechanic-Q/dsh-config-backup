#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_strata_copychain.py — 文案链全切 Strata 红线自检（票 #86，无框架 assert 直跑）。

全部用假响应桩（不真连 Strata/9router，SPEC 测试决策）：
1. llm_json 缺省端点解析：LLM_API env 优先；缺省经 strata_h3_lane 单一事实源
   （STRATA_URL 优先、WSL 网关动态解析），9router 20128 缺省退役
2. llm_json 请求体红线：model 显式携带（默认 strata）、关思维链 =
   chat_template_kwargs.enable_thinking=false（#83 spike §4 T1 唯一有效写法）、
   不携带 9router 专属 thinking 字段；LLM_THINKING=on 仅翻转 enable_thinking
3. director_llm 自备客户端：端点与 llm_json 同源、请求体同红线（#86 去 9router 方言）
4. vision 判读独立 env：urllib_req 读 DRIVER_VISION_API（9router，全仓唯一 20128 引用），
   模型轮询体不变
5. legacy 车道注入：_strata_env 注入 Strata 端点；_stage_list 各 LLM 阶段 gate 旗标
6. 全仓 grep 红线：20128 仅剩 vision 一处；9router thinking 写法零残留；
   DRIVER_QWEN_* 零残留；start_qwen.sh 已退役
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm_json
import director_llm
import run_pipeline as rp


class _FakeResp:
    """假 urlopen 响应：read(n) 流式（llm_json SSE 解析）与 read() 全量（非流式）两用。"""
    def __init__(self, payload, content=""):
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._content = content
        self.status = 200

    def read(self, n=-1):
        if n == -1:
            return self._raw
        # 流式：首读给完整 SSE 事件（含 usage，_stream_once 收到即返回），次读给空（收尾）
        if not self._content:
            return b""
        self._content, body = "", self._content
        return body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _sse_payload(model_echo=None):
    evt = {"choices": [{"delta": {"content": '{"ok": true}'}, "finish_reason": None}],
           "usage": {"prompt_tokens": 3, "completion_tokens": 5}}
    chunk = b"data: " + json.dumps(evt).encode() + b"\n\ndata: [DONE]\n\n"
    return {"choices": []}, chunk


# ── 1. llm_json 缺省端点解析（子进程：api() 调用期解析，import 顺序无关）────
def _pyprint(code, extra_env):
    env = {k: v for k, v in os.environ.items()
           if k not in ("LLM_API", "STRATA_URL", "LLM_MODEL", "LLM_THINKING")}
    env.update(extra_env)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, cwd=HERE, timeout=120)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


api = _pyprint("import llm_json; print(llm_json.api())", {})
assert api.endswith("/v1/chat/completions") and "20128" not in api and "8888" not in api, \
    f"缺省端点须指 Strata（禁 20128/8888 残留缺省）: {api}"
api = _pyprint("import llm_json; print(llm_json.api())",
               {"STRATA_URL": "http://10.0.0.9:9999/"})
assert api == "http://10.0.0.9:9999/v1/chat/completions", \
    f"STRATA_URL env 须优先且尾斜杠剥掉（单一事实源 strata_h3_lane）: {api}"
api = _pyprint("import llm_json; print(llm_json.api())",
               {"LLM_API": "http://injected:1234/v1/chat/completions"})
assert api == "http://injected:1234/v1/chat/completions", f"LLM_API env 须优先: {api}"
# 坏顺序回归（#86 review）：run_pipeline 先 import strata_h3_lane、strata_h3_lane 在自身
# 初始化中途反向 import 本模块——端点若在 import 期缓存会拿到半初始化模块（BASE_URL 未定）
# 被 except 吞成 127.0.0.1 回退，WSL 内 driver 全部进程内调用静默打错端点。
api = _pyprint("import run_pipeline, llm_json; print(llm_json.api())",
               {"STRATA_URL": "http://10.0.0.9:9999"})
assert api == "http://10.0.0.9:9999/v1/chat/completions", \
    f"坏 import 顺序下缺省端点不得静默回退 127.0.0.1: {api}"
print("test_strata_copychain: llm_json 缺省端点解析（含坏顺序回归）OK")

# ── 2. llm_json 请求体红线（假 urlopen 桩，关思维链方言 + 默认模型名）────────
_captured = {}
_real_urlopen = urllib.request.urlopen
_sse_body, _sse_chunk = _sse_payload()


def _fake_stream_urlopen(req, timeout=None):
    _captured["url"] = req.full_url
    _captured["body"] = json.loads(req.data.decode("utf-8"))
    return _FakeResp({}, content=_sse_chunk)


try:
    urllib.request.urlopen = _fake_stream_urlopen
    obj = llm_json.llm_call_json("s", "u", {"type": "object"}, verbose=False)
finally:
    urllib.request.urlopen = _real_urlopen
assert obj == {"ok": True}, obj
assert _captured["url"] == llm_json.api(), (_captured["url"], llm_json.api())
assert _captured["body"]["model"] == "strata", \
    f"默认模型名须 strata（9router 档位名退役）: {_captured['body']['model']}"
assert _captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}, \
    "关思维链缺省写法 = chat_template_kwargs.enable_thinking=false（#83 spike T1）"
assert "thinking" not in _captured["body"], "9router 专属 thinking 字段禁止外发（风格红线）"
assert _captured["body"]["stream"] is True, "llm_json 流式口径不变"

_th_saved = llm_json.THINKING_ON
try:
    llm_json.THINKING_ON = True   # LLM_THINKING=on 同款（import 时读 env，模块常量桩）
    urllib.request.urlopen = _fake_stream_urlopen
    llm_json.llm_call_json("s", "u", {"type": "object"}, verbose=False)
finally:
    urllib.request.urlopen = _real_urlopen
    llm_json.THINKING_ON = _th_saved
assert _captured["body"]["chat_template_kwargs"] == {"enable_thinking": True}, _captured["body"]
assert "thinking" not in _captured["body"], "开思维链也不得携带 9router thinking 字段"
print("test_strata_copychain: llm_json 请求体红线 OK")

# ── 3. director_llm 自备客户端：端点同源 + 请求体红线 ────────────────────────
def _fake_plain_urlopen(req, timeout=None):
    _captured["url"] = req.full_url
    _captured["body"] = json.loads(req.data.decode("utf-8"))
    return _FakeResp({"choices": [{"message": {"content": "x" * 80}}], "usage": {}})


try:
    urllib.request.urlopen = _fake_plain_urlopen
    out = director_llm.llm_call([{"role": "user", "content": "hi"}], model="low-tier")
finally:
    urllib.request.urlopen = _real_urlopen
assert out == "x" * 80, out
assert _captured["url"] == llm_json.api(), "director 端点须与 llm_json.api() 调用期同源"
assert "20128" not in _captured["url"], _captured["url"]
assert _captured["body"]["model"] == "low-tier"
assert _captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}, \
    "director 请求体关思维链写法须与车道同方言（spike T1）"
assert "thinking" not in _captured["body"], "9router thinking 字段已从 director 客户端移除"
print("test_strata_copychain: director_llm 客户端红线 OK")

# ── 4. vision 独立 env：urllib_req 读 DRIVER_VISION_API（显式覆盖生效）──────
# （T2 #104 起缺省改指本地 llama-server :8100，vision_common.default_api 单一
# 事实源；9router 20128 降级为显式 env 回退。本节验证「显式 env 覆盖生效 +
# 缺省不带 20128」两条红线。）
_vis_saved = os.environ.get("DRIVER_VISION_API")
_body = json.dumps({"model": "vision", "messages": []}).encode()


def _fake_vision_urlopen(req, timeout=None):
    _captured["url"] = req.full_url
    return _FakeResp({"choices": [{"message": {"content": "{\"verdict\": \"正常\"}"}}],
                      "usage": {}})


try:
    os.environ["DRIVER_VISION_API"] = "http://vision-hub:20128/v1/chat/completions"
    urllib.request.urlopen = _fake_vision_urlopen
    d = rp.urllib_req(_body)
    assert d["choices"][0]["message"]["content"], d
    assert _captured["url"] == "http://vision-hub:20128/v1/chat/completions", _captured["url"]
    os.environ.pop("DRIVER_VISION_API", None)
    rp.urllib_req(_body)
    assert "20128" not in _captured["url"] and ":8100" in _captured["url"], \
        f"vision 缺省应指本地 llama-server :8100（T2 #104；20128 仅剩显式 env 回退）: {_captured['url']}"
finally:
    urllib.request.urlopen = _real_urlopen
    if _vis_saved is not None:
        os.environ["DRIVER_VISION_API"] = _vis_saved
    else:
        os.environ.pop("DRIVER_VISION_API", None)
print("test_strata_copychain: vision DRIVER_VISION_API 收敛 OK")

# ── 5. legacy 车道注入：_strata_env + _stage_list 旗标 ───────────────────────
env = rp.Driver.__new__(rp.Driver)._strata_env()
assert env == {"LLM_API": f"{rp.LANE_STRATA_URL}/v1/chat/completions"}, env
assert "8888" not in env["LLM_API"], env

d = rp.Driver.__new__(rp.Driver)
d.lane = "legacy"
legacy = {no: env_flag for no, _t, _fn, env_flag, _gpu in d._stage_list()}
d.lane = "compressed"
compressed = {no: env_flag for no, _t, _fn, env_flag, _gpu in d._stage_list()}
for st in ("s04", "s05", "s06", "s07"):
    assert legacy[st] == "strata", f"legacy {st} 须挂 Strata gate/注入旗标: {legacy}"
for st in ("s05", "s06", "s07"):
    assert compressed[st] is None, f"compressed {st} 为虚拟 stamp，不得挂 gate: {compressed}"
assert compressed["s04"] is None, "compressed s04 的 gate 在 _gate_strata 内自挂"
print("test_strata_copychain: legacy 车道注入与 gate 旗标 OK")

# ── 5b. 连败 hint 路由：legacy LLM 阶段消息须同时含阶段专项指引与 Strata 步骤 ──
# （#86 review 回归：Strata 步骤分支不得遮蔽 STAGE_MANUAL_HINTS——s05 的枚举级
# 纠偏人工指引曾因此到不了人）
_sent5b = []


class _CapFeishu5b:
    def send_text(self, text):
        _sent5b.append(text)


from ledger import Ledger as _Ledger5b

_real_exit5b, _real_log5b = sys.exit, rp.log
try:
    sys.exit = lambda code: None
    rp.log = lambda m: None
    d5 = rp.Driver.__new__(rp.Driver)
    d5.stamps_dir = tempfile.mkdtemp()
    d5.slug = "t"
    d5.script = __file__
    d5.date = "2026-10-12"
    d5.lane = "legacy"
    d5._t0 = 0.0
    d5.ledger = _Ledger5b(os.path.join(d5.stamps_dir, "report.json"), t0=0.0)
    d5.feishu = _CapFeishu5b()
    d5.args = types.SimpleNamespace(style="x")
    d5.d = {n: "/tmp/nowhere" for n in ("07_成片",)}
    d5.run_stage("s05", "摄影师（Strata）", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
finally:
    sys.exit = _real_exit5b
    rp.log = _real_log5b
assert any("枚举级纠偏" in t and "spike-strata-start.sh" in t for t in _sent5b), _sent5b
assert not any("start_qwen" in t for t in _sent5b), "8888 步骤不得回流"
print("test_strata_copychain: 连败 hint 路由（专项指引+Strata 步骤）OK")

# ── 6. 全仓 grep 红线 ────────────────────────────────────────────────────────
repo = os.path.dirname(HERE)
hits_20128 = []
hits_thinking = []
hits_qwen_env = []
for path in glob.glob(os.path.join(repo, "**", "*.py"), recursive=True) + \
            glob.glob(os.path.join(repo, "**", "*.sh"), recursive=True):
    rel = os.path.relpath(path, repo).replace("\\", "/")
    if rel.startswith(("graphify-out/", ".git/", "references/")) or \
            os.path.basename(path).startswith("test_"):
        continue
    src = open(path, encoding="utf-8", errors="replace").read()
    if "20128" in src:
        hits_20128.append(rel)
    if '{"type": "disabled"}' in src:
        hits_thinking.append(rel)
    if "DRIVER_QWEN" in src:
        hits_qwen_env.append(rel)
assert sorted(hits_20128) == sorted(["scripts/rotation_dataset.py", "scripts/run_pipeline.py"]), \
    f"20128 仅剩两处合法引用（判读显式 env 回退 + 旧云端行归因分类，均不发调用）: {hits_20128}"
assert "DRIVER_VISION_API" in open(os.path.join(repo, "scripts", "run_pipeline.py"),
                                   encoding="utf-8").read(), "该处须为 DRIVER_VISION_API 缺省值"
assert hits_thinking == [], f"9router thinking 写法零残留: {hits_thinking}"
assert hits_qwen_env == [], f"DRIVER_QWEN_* 已退役零残留: {hits_qwen_env}"
assert not os.path.exists(os.path.join(HERE, "start_qwen.sh")), "start_qwen.sh 须随 8888 退役"
print("test_strata_copychain: 全仓 grep 红线 OK")

print("test_strata_copychain: 全部通过")
