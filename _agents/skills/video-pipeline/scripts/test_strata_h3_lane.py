#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_strata_h3_lane.py — 压缩车道核心脚本自检（无框架，assert 直跑，仿 test_run_pipeline.py）。

全部用假 LLM 响应跑通（不依赖真实 Strata 服务，SPEC 测试决策）：
1. rec 契约字段齐全（id/prompt/video_seconds/portrait_risk/camera_sanitized）
2. video_seconds 权威 = duration_est，非 est_seconds
3. 三字段头特征串（submit_accel 覆盖校验依赖）
4. portrait_risk 启发式命中规则（横移词/人物词/竖屏风险词；否定句与叙述者 S1 不误报）
5. 整单缺段重试语义：缺段整单重调、连败硬失败且零落盘
6. 生成期约束门行为不变：竖屏句前移 / 横移改写 / 旁白句与 music N/A 自动补齐 / 残余横移句级消毒
7. 请求形态红线（#84 Strata 版）：OpenAI 标准体——model 字段显式携带（STRATA_MODEL，默认
   strata）、非流式（无 stream 字段）、chat_template_kwargs.enable_thinking=false（#83 spike
   T1 唯一有效写法）、不携带 9router 专属 thinking 字段；STRATA_URL 缺省端点解析；
   call_strata 假响应桩（OpenAI 响应解析，不真连服务）
8. parse_shots 容错（围栏/杂文/缺 shots）
"""
import json
import os
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import strata_h3_lane as lane

# ── 夹具：真实 slug 目录结构 + duration_est ≠ est_seconds 的分镜清单 ──────────
BOARD = [
    {"id": "shot01", "text": "我国“天关”卫星与“拉索”联合观测发现脉冲星尾迹。", "chars": 26,
     "est_seconds": 8.9, "duration_est": 9.54, "section": "正文"},
    {"id": "shot02", "text": "科研团队重建了粒子的传播路径。", "chars": 15,
     "est_seconds": 10.0, "duration_est": 11.2, "section": "正文"},
    {"id": "shot03", "text": "这一发现改写了此前的扩散模型。", "chars": 15,
     "est_seconds": 6.0, "duration_est": 6.5, "section": "正文"},
]

_VERT = lane._VERT_SENT
_CONSTRAINT = lane.CONSTRAINT


def fake_shot(sid, desc_body, snd="Soft ambient wind with a low electronic hum."):
    """构造一次合规的假 LLM shot（含竖屏句/旁白句/三字段），desc_body 为正文。"""
    desc = f"This is a standard vertical 9:16 portrait video. [Shot 1] Cinematic style. {desc_body} {_CONSTRAINT}"
    return {"id": sid, "integrated_multimodal_description": desc,
            "overall_soundscape": snd, "non_diegetic_music": "N/A"}


def fake_content(shots, fenced=False):
    payload = json.dumps({"shots": shots}, ensure_ascii=False)
    return f"```json\n{payload}\n```" if fenced else payload


def mk_slug():
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "01_分段"))
    with open(os.path.join(tmp, "01_分段", "分镜清单_raw.json"), "w", encoding="utf-8") as f:
        json.dump(BOARD, f, ensure_ascii=False)
    return tmp


# ── 1+2+3. rec 契约：字段齐全 / video_seconds=duration_est / 三字段头 ─────────
tmp = mk_slug()
shots = [fake_shot(s["id"], f"The observatory dome rises against a star field, shot {i:02d} scene body words "
                        "describing the pulsar trail, the detector array and the slow vertical drift of the camera.")
         for i, s in enumerate(BOARD, 1)]
summary = lane.persist_shots(shots, BOARD, os.path.join(tmp, "06_H3prompt"))
assert len(summary) == 3, summary
for s in BOARD:
    rec = json.load(open(os.path.join(tmp, "06_H3prompt", f"{s['id']}.json"), encoding="utf-8"))
    for key in ("id", "prompt", "video_seconds", "portrait_risk", "camera_sanitized"):
        assert key in rec, (s["id"], key, sorted(rec))
    assert rec["id"] == s["id"]
    # 权威 = duration_est（TTS 实测），绝不取 est_seconds
    assert rec["video_seconds"] == s["duration_est"], (s["id"], rec["video_seconds"])
    assert rec["video_seconds"] != s["est_seconds"], "误用 est_seconds"
    # 三字段头特征串（submit_accel 覆盖校验依赖代码保证，不靠 LLM）
    p = rec["prompt"].lower()
    assert "integrated_multimodal_description" in p, s["id"]
    assert "overall_soundscape" in p, s["id"]
    assert "non_diegetic_music: n/a" in p, s["id"]
    assert rec["camera_sanitized"] is False, "合规 shot 不应触发消毒"
    assert rec["scene_source"] == "strata_lane"
print("test_strata_h3_lane: rec 契约/时长权威/三字段头 OK")

# ── 4. portrait_risk 启发式命中规则 ───────────────────────────────────────────
assert lane.portrait_risk("The camera trucks right past the stone pillars.") is True, "横移词须命中"
assert lane.portrait_risk("A robed figure seen from behind, lips closed, not speaking.") is True, "人物词须命中"
assert lane.portrait_risk("No person appears in this shot.") is False, "否定句不得误报人物"
assert lane.portrait_risk("The narrator (S1) says in an off-screen voiceover.") is False, "叙述者 S1 不是画面人物"
assert lane.portrait_risk(
    "Static locked shot of a celestial palace, vertical pillars rise upward.", issues=[]) is False, "干净段不得误报"
assert lane.portrait_risk("clean text", issues=["⚠️竖屏风险词命中: ['circular arena']"]) is True, "竖屏风险词须命中"
assert lane.portrait_risk("clean text", issues=["横移运镜已消毒为纵向/推进（1 处）"]) is True, "横移改写路径也须命中（宁可多查，两路收口对称）"
# 落盘级联：干净 shot → portrait_risk=False
rec1 = json.load(open(os.path.join(tmp, "06_H3prompt", "shot01.json"), encoding="utf-8"))
assert rec1["portrait_risk"] is False, rec1["portrait_risk"]
print("test_strata_h3_lane: portrait_risk 启发式 OK")

# ── 5. 整单缺段重试语义 ───────────────────────────────────────────────────────
# 5a. 首次缺 1 镜 → 整单重调；第二次齐 → 成功且 16/16…（此处 3/3）落盘
tmp5 = mk_slug()
calls = []


def flaky_caller():
    calls.append(1)
    shots_all = [fake_shot(s["id"], f"Scene body for {s['id']} with enough descriptive words.") for s in BOARD]
    if len(calls) == 1:
        return fake_content(shots_all[:-1]), {}   # 缺最后一镜
    return fake_content(shots_all), {}


rc = lane.run_lane(tmp5, caller=flaky_caller)
assert rc == 0, rc
assert len(calls) == 2, f"缺段应整单重调一次，实调 {len(calls)} 次"
have = sorted(f[:-5] for f in os.listdir(os.path.join(tmp5, "06_H3prompt")) if f.endswith(".json"))
assert have == [s["id"] for s in BOARD], have

# 5b. 连败 → 恰好 1+retries 次整单调用后硬失败，零落盘（不写半个产物、绝不回退云端）
tmp5b = mk_slug()
calls_bad = []


def bad_caller():
    calls_bad.append(1)
    return fake_content([fake_shot(s["id"], "body") for s in BOARD[:-1]]), {}   # 永远缺一镜


rc = lane.run_lane(tmp5b, retries=2, caller=bad_caller)
assert rc == 1, rc
assert len(calls_bad) == 3, f"1+2 次整单调用，实调 {len(calls_bad)}"
assert not os.path.exists(os.path.join(tmp5b, "06_H3prompt")), "连败不得落盘任何产物"

# 5c. 网络异常也走整单重试
tmp5c = mk_slug()
calls_net = []


def net_fail_caller():
    calls_net.append(1)
    if len(calls_net) == 1:
        raise ConnectionError("connection refused")
    return fake_content([fake_shot(s["id"], "body words here") for s in BOARD]), {}


rc = lane.run_lane(tmp5c, caller=net_fail_caller)
assert rc == 0 and len(calls_net) == 2, (rc, len(calls_net))
print("test_strata_h3_lane: 整单缺段重试/连败零落盘/网络重试 OK")

# ── 6. 生成期约束门行为不变（与旧车道 enforce_constraints / sanitize 同一门）────
tmp6 = mk_slug()
# 6a. 竖屏句写在中间 → 前移到 desc 第一句；横移改写：camera tracks right → slowly pushes forward
desc_mid = (f"[Shot 1] Cinematic style. {_VERT} The camera tracks right along the wall. "
            f"Scene body describing the hall. {_CONSTRAINT}")
# 6b. 残余横向行进句（enforce 改写表覆盖不到）→ 句级消毒 + camera_sanitized + risk
desc_lateral = (f"This is a standard vertical 9:16 portrait video. [Shot 1] Cinematic style. "
                f"A tracking shot from left to right reveals the hall. Scene body. {_CONSTRAINT}")
shots6 = [
    {"id": "shot01", "integrated_multimodal_description": desc_mid,
     "overall_soundscape": "wind", "non_diegetic_music": "N/A"},
    {"id": "shot02", "integrated_multimodal_description": desc_lateral,
     "overall_soundscape": "wind", "non_diegetic_music": "N/A"},
    # 6c. 缺旁白句 + 缺 music 字段 → 自动补齐
    {"id": "shot03", "integrated_multimodal_description":
        f"This is a standard vertical 9:16 portrait video. [Shot 1] Cinematic style. "
        f"Scene body describing the detector hall in enough words.",
     "overall_soundscape": "wind"},
]
summary6 = lane.persist_shots(shots6, BOARD, os.path.join(tmp6, "06_H3prompt"))
r1 = json.load(open(os.path.join(tmp6, "06_H3prompt", "shot01.json"), encoding="utf-8"))
desc_part = r1["prompt"].split("overall_soundscape:")[0]
assert desc_part.strip().split("\n", 1)[1].startswith("This is a standard vertical"), "竖屏句须前移到 desc 第一句"
assert "camera slowly pushes forward" in r1["prompt"], "横移改写（旧车道 PAN_REWRITES）失效"
assert "tracks right" not in r1["prompt"]
assert r1["portrait_risk"] is True, "改写收口的横移意图须标记 risk（与消毒路径对称，宁可多查）"
r2 = json.load(open(os.path.join(tmp6, "06_H3prompt", "shot02.json"), encoding="utf-8"))
assert r2["camera_sanitized"] is True, "残余横移句须句级消毒"
assert "tracking shot from left to right" not in r2["prompt"], "消毒后不得残留横移句"
assert r2["portrait_risk"] is True, "消毒前的横移意图须标记 risk（宁可多查）"
assert "The camera stays upright and holds mostly still" in r2["prompt"], "消毒替身句须在"
r3 = json.load(open(os.path.join(tmp6, "06_H3prompt", "shot03.json"), encoding="utf-8"))
assert "voiceover narration" in r3["prompt"] and "lips remain completely closed" in r3["prompt"], "旁白句须自动补"
assert "non_diegetic_music: N/A" in r3["prompt"], "music N/A 须自动补"
print("test_strata_h3_lane: 生成期约束门（竖屏前移/横移改写/句级消毒/自动补齐）OK")

# ── 5d. duration_est 预检：缺回填在烧调用前就硬失败（零网络调用）──────────────
tmp5d = mk_slug()
nodur = [{k: v for k, v in s.items() if k != "duration_est"} for s in BOARD]
with open(os.path.join(tmp5d, "01_分段", "分镜清单_raw.json"), "w", encoding="utf-8") as f:
    json.dump(nodur, f, ensure_ascii=False)
calls_pre = []
try:
    lane.run_lane(tmp5d, caller=lambda: calls_pre.append(1) or ("", {}))
    raise SystemExit("缺 duration_est 预检应硬失败")
except SystemExit as e:
    assert "duration_est" in str(e), e
assert calls_pre == [], "预检失败不得发起任何网络调用"

# ── 6d. 缺 duration_est → 硬失败（绝不静默回退 est_seconds）───────────────────
board_nodur = [{k: v for k, v in s.items() if k != "duration_est"} for s in BOARD[:1]]
try:
    lane.persist_shots([fake_shot("shot01", "body")], board_nodur, os.path.join(tempfile.mkdtemp(), "o"))
    raise SystemExit("缺 duration_est 应硬失败")
except lane.ContractError as e:
    assert "duration_est" in str(e), e
print("test_strata_h3_lane: duration_est 权威硬失败 OK")

# ── 6e. ID 错位/多余段 → 整单失败，零落盘 ─────────────────────────────────────
try:
    lane.persist_shots([fake_shot(s["id"], "b") for s in BOARD] + [fake_shot("shot99", "x")],
                       BOARD, os.path.join(tempfile.mkdtemp(), "o"))
    raise SystemExit("多余段应整单失败")
except lane.ContractError:
    pass
try:
    lane.persist_shots([{"id": "shot01", "integrated_multimodal_description": "x"}], BOARD[:1],
                       os.path.join(tempfile.mkdtemp(), "o"))
    raise SystemExit("缺字段 shot 应整单失败（缺 soundscape 时 assemble 仍成文但整单缺段检查在前）")
except lane.ContractError:
    pass
print("test_strata_h3_lane: ID 错位整单失败 OK")

# ── 7. 请求形态红线（#84 Strata 版）：OpenAI 标准体 + 关思维链方言 + 禁 thinking 字段 ──
body = lane.build_request_body(BOARD, "STYLE BLOCK TEXT")
assert body["model"] == "strata", "model 字段须显式携带（spike T4：缺了服务端虽容忍，但请求形态必须可断言）"
assert body["chat_template_kwargs"] == {"enable_thinking": False}, body.get("chat_template_kwargs")
assert "thinking" not in body, "不得携带 9router 专属 thinking 字段（spike T5：发了也关不掉）"
assert "stream" not in body, "非流式红线：不得携带 stream 字段"
assert body["messages"][0]["role"] == "system"
assert "STYLE BLOCK TEXT" in body["messages"][0]["content"], "风格块须注入 SYSTEM"
assert "质量补丁" in body["messages"][0]["content"], "四块质量补丁须在 SYSTEM"
sys_c = body["messages"][0]["content"]
assert "人物镜头逐段判断" in sys_c and "250" in sys_c and "风格锁" in sys_c and "横移" in sys_c
assert "{_VERT_SENT}" not in sys_c and "{CONSTRAINT}" not in sys_c, "占位符必须替换为真实约束句"
assert lane._VERT_SENT in sys_c, "竖屏约束句须逐字注入 SYSTEM"
assert lane.CONSTRAINT in sys_c, "旁白契约句须逐字注入 SYSTEM"
assert "{{" not in sys_c, "replace 模板不转义花括号，双花括号会原样发给模型"
usr = body["messages"][1]["content"]
for s in BOARD:
    assert s["id"] in usr and s["text"] in usr, s["id"]
assert "9.5s" in usr, "user 行应带 duration_est 时长"
assert "POV" in usr, "POV 标记须在 user 行（shot01 每 5 段第 1 段）"
# 纯函数：同输入两次构建结果一致
assert body == lane.build_request_body(BOARD, "STYLE BLOCK TEXT"), "build_request_body 须纯函数"
# model 参数化：显式传入覆盖默认（STRATA_MODEL env 在 run_lane 层读取，build 层纯注入）
assert lane.build_request_body(BOARD, "", model="custom-name")["model"] == "custom-name"
print("test_strata_h3_lane: 请求形态红线 OK")

# ── 7a. STRATA_URL 缺省端点解析（spike §1 定案：WSL 走网关 IP 动态发现，勿写死）──
_real_in_wsl, _real_gw = lane._in_wsl, lane._wsl_gateway
try:
    lane._in_wsl = lambda: False
    lane._wsl_gateway = lambda: None
    assert lane.default_base_url() == "http://127.0.0.1:8080", "非 WSL 须直连本机 8080"
    lane._in_wsl = lambda: True
    assert lane.default_base_url() == "http://127.0.0.1:8080", "WSL 内网关发现失败须回退 127.0.0.1"
    lane._wsl_gateway = lambda: "172.25.176.1"
    assert lane.default_base_url() == "http://172.25.176.1:8080", "WSL 内须连网关 IP（Windows 主机）"
finally:
    lane._in_wsl, lane._wsl_gateway = _real_in_wsl, _real_gw
# _wsl_gateway 本体：默认路由输出取 via 地址
class _FakeRoute:
    def __init__(self, stdout):
        self.stdout = stdout
_real_in_wsl, _real_run = lane._in_wsl, lane.subprocess.run
try:
    lane._in_wsl = lambda: True
    lane.subprocess.run = lambda *a, **k: _FakeRoute("default via 172.25.176.1 dev eth0\n")
    assert lane._wsl_gateway() == "172.25.176.1", "网关须取 default via 后的 IP"
    lane.subprocess.run = lambda *a, **k: _FakeRoute("")
    assert lane._wsl_gateway() is None, "空路由输出须回退 None"
finally:
    lane._in_wsl, lane.subprocess.run = _real_in_wsl, _real_run
# BASE_URL 求值口径：STRATA_URL env 优先（rstrip 尾斜杠），未设走缺省解析
_real_env = os.environ.get("STRATA_URL")
try:
    os.environ["STRATA_URL"] = "http://10.0.0.9:9999/"
    assert (os.environ.get("STRATA_URL", "").rstrip("/") or lane.default_base_url()) \
        == "http://10.0.0.9:9999", "STRATA_URL env 须优先且尾斜杠剥掉"
finally:
    if _real_env is None:
        os.environ.pop("STRATA_URL", None)
    else:
        os.environ["STRATA_URL"] = _real_env
print("test_strata_h3_lane: STRATA_URL 缺省端点解析 OK")

# ── 7b. call_strata 假响应桩：OpenAI 标准请求/响应解析（不真连服务）────────────
_captured = {}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(req, timeout=None):
    _captured["url"] = req.full_url
    _captured["body"] = json.loads(req.data.decode("utf-8"))
    _captured["timeout"] = timeout
    return _FakeResp({"choices": [{"message": {"content": "RESPONSE-CONTENT"}}],
                      "usage": {"prompt_tokens": 11, "completion_tokens": 22}})


_real_urlopen = urllib.request.urlopen
try:
    urllib.request.urlopen = _fake_urlopen
    content, usage = lane.call_strata("http://127.0.0.1:8080/", lane.build_request_body(BOARD, "S"),
                                      timeout=123)
finally:
    urllib.request.urlopen = _real_urlopen
assert _captured["url"] == "http://127.0.0.1:8080/v1/chat/completions", _captured["url"]
assert _captured["body"]["model"] == "strata" and _captured["body"]["chat_template_kwargs"] \
    == {"enable_thinking": False}, "发出的请求体须含 model 与关思维链方言"
assert "thinking" not in _captured["body"] and "stream" not in _captured["body"], "红线字段不得外发"
assert _captured["timeout"] == 123
assert content == "RESPONSE-CONTENT" and usage["completion_tokens"] == 22, (content, usage)
# reasoning_content 形态（spike T0：思维链分离字段）不影响 content 提取；content 为 null → 空串
try:
    urllib.request.urlopen = lambda req, timeout=None: _FakeResp(
        {"choices": [{"message": {"reasoning_content": "chain...", "content": None}}], "usage": {}})
    content2, usage2 = lane.call_strata("http://127.0.0.1:8080", {}, timeout=5)
finally:
    urllib.request.urlopen = _real_urlopen
assert content2 == "" and usage2 == {}, (content2, usage2)
print("test_strata_h3_lane: call_strata 假响应桩 OK")

# ── 7c. run_lane 的 model 默认路径与显式透传 ──────────────────────────────────
tmp7 = mk_slug()
# caller 绕过 body 构建，spy build_request_body 捕获 run_lane 实际传入的 model
_real_brb, _real_dm = lane.build_request_body, lane.DEFAULT_MODEL
_cap = []


def _spy(board, style, **kw):
    _cap.append(kw.get("model"))
    return _real_brb(board, style, **kw)


def _ok_caller():
    return fake_content([fake_shot(s["id"], "body words") for s in BOARD]), {}


try:
    lane.build_request_body = _spy
    rc = lane.run_lane(tmp7, caller=_ok_caller, model="env-model")
    assert rc == 0 and _cap[-1] == "env-model", _cap
    # 默认路径：model=None → DEFAULT_MODEL（import 时读 STRATA_MODEL env 冻结，桩模块常量验证）
    lane.DEFAULT_MODEL = "patched-model"
    rc = lane.run_lane(mk_slug(), caller=_ok_caller)
    assert rc == 0 and _cap[-1] == "patched-model", _cap
finally:
    lane.build_request_body, lane.DEFAULT_MODEL = _real_brb, _real_dm
print("test_strata_h3_lane: run_lane model 默认/透传 OK")

# ── 8. parse_shots 容错 ──────────────────────────────────────────────────────
shots8 = [{"id": "shot01", "prompt": "x"}]
assert lane.parse_shots(fake_content(shots8)) == shots8
assert lane.parse_shots(fake_content(shots8, fenced=True)) == shots8
assert lane.parse_shots("前缀杂文 " + fake_content(shots8) + " 后缀") == shots8
assert lane.parse_shots("没有 json") is None
assert lane.parse_shots(json.dumps({"nope": 1})) is None
assert lane.parse_shots("") is None
print("test_strata_h3_lane: parse_shots 容错 OK")

# ── 9. 风格块按名加载（repo references/styles）───────────────────────────────
style = lane.load_style("realistic")
assert isinstance(style, str) and len(style) > 50, "realistic 风格块应可加载"
sys_with_style = lane.build_system("MY STYLE LOCK")
assert "MY STYLE LOCK" in sys_with_style
print("test_strata_h3_lane: 风格块加载 OK")

print("test_strata_h3_lane: 全部通过")
