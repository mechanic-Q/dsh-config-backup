#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_run_pipeline.py — run_pipeline 修复自检（无框架，assert 直跑）。

覆盖：
1. LEGACY_STAGE_MAP 不再含与新号撞名的 s10-s14（0908 撞名降级事故根因）
2. 台账快照合并幂等 → 已迁至 test_ledger.py（票7 #65 Ledger module）
3. rotation_dataset 词表：shot18 铺展描述不误报、shot19 orbit 句可提取
4. #48 确认门退出路径：timeout/feedback 先 flush 台账再硬退出（os._exit(4)），
   pass 路径行为不变（返回、不退出）
5. #48 硬退出机制实测：残留非 daemon 线程下 _flush_exit 秒退且台账已落盘
   （sys.exit 等线程收尾会挂死——0922 实测 30+ 分钟，此处子进程验证对照）
6. #59 双车道接线：车道解析优先级（CLI>env>compressed）/ 阶段表随车道切换 /
   车道切换自动清 s04-s07 stamps / 虚拟 stamp 落盘 / s04_design_h3 接线
   （run_lane 0/1 契约、对账错位硬失败）/ #84 Strata 在线门接缝（探活桩）/
   report_summary 车道分支 + 票7 bug② 回归（flip_regen_rounds 键为 mode）/
   连败叫人消息路由（Strata 专用步骤，不被 qwen 分支劫持）。
   #85 注：劣化自愈（原 6i）随 KVMem 托管退役，Strata 自愈归 #85 重建后补测
7. #170 log() cp936 降级：gbk strict stdout 下打印含 emoji 消息不掀翻进程，
   emoji 转 U+ 转义、中文段原样保留（该测试自身裸跑也安全：只打 ASCII+中文）
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_pipeline as rp
from ledger import Ledger

# ── 1. 迁移表撞名 ────────────────────────────────────────────────────
new_ids = {f"s{i:02d}" for i in range(1, 14)}
clash = {o for o, n in rp.Driver.LEGACY_STAGE_MAP.items() if o in new_ids}
assert not clash, f"迁移表与新阶段号撞名: {clash}"

# 幂等行为：已迁移完成的目录重复启动不再有迁移动作
tmp = tempfile.mkdtemp()
for n in range(1, 14):
    json.dump({"ts": 0}, open(os.path.join(tmp, f"stage_s{n:02d}.json"), "w"))
log_calls = []
_orig_log = rp.log
rp.log = lambda msg: log_calls.append(msg)
try:
    Driver = rp.Driver
    drv = Driver.__new__(Driver)  # 不走 __init__，只测方法
    drv.stamps_dir = tmp
    Driver._migrate_stamps(drv)
    stamps_now = sorted(f for f in os.listdir(tmp) if f.endswith(".json"))
    assert stamps_now == [f"stage_s{n:02d}.json" for n in range(1, 14)], stamps_now
    mig = [m for m in log_calls if "迁移" in m]
    assert not mig, f"全量新号 stamps 不应触发迁移: {mig}"
finally:
    rp.log = _orig_log

# ── 2. 台账快照合并幂等 → 迁至 test_ledger.py（票7 #65：Ledger module 接管，
#        原 write_report 的 Driver.__new__ 属性汤测法随之退役）───────────────

# ── 3. rotation_dataset 词表 ─────────────────────────────────────────
from rotation_dataset import BROADER_LATERAL, camera_sentence
assert not BROADER_LATERAL.search(
    "The push-in unfolds the composition left-to-right within the vertical frame."), "shot18 铺展误报"
assert BROADER_LATERAL.search(
    "The camera executes a slow, steady horizontal tracking movement from left to right."), "真实横移仍须命中"
s19 = ("The camera executes a slow orbit with small amplitude at slow speed around "
       "the converging vessels, maintaining the glowing golden line.")
assert camera_sentence(s19), "shot19 orbit 句应可提取"

# ── 4. #48 确认门退出路径：flush 台账 + 硬退出；pass 路径不变 ────────────
class _FakeFeishu:
    def __init__(self, result):
        self._r = result
        self.sent = []

    def gate_confirm(self, **kw):
        return self._r

    def send_text(self, text):
        self.sent.append(text)


def mk_gate_driver(tmp_dir, result):
    d = rp.Driver.__new__(rp.Driver)
    d.stamps_dir = tmp_dir
    d.slug = "t"
    d._t0 = 0.0
    d.ledger = Ledger(os.path.join(tmp_dir, "report.json"), t0=0.0)
    d.feishu = _FakeFeishu(result)
    d.args = types.SimpleNamespace(gate_timeout=1)
    return d


tmp4 = tempfile.mkdtemp()
_exits = []
_real__exit = os._exit
os._exit = lambda code: _exits.append(code)
try:
    for status in ("timeout", "feedback", "error"):
        d = mk_gate_driver(tmp4, {"action": "abort", "status": status,
                                  "reply": "" if status == "timeout" else "意见"})
        d._gate("12", "成片审核", "cap")
        assert _exits == [4], (status, _exits)
        rep = json.load(open(os.path.join(tmp4, "report.json"), encoding="utf-8"))
        # 硬退出前台账已落盘：门回复（含本次 status）不丢
        assert rep["gate_replies"][-1]["status"] == status, rep["gate_replies"]
        _exits.clear()
    # pass 路径行为不变：正常返回、不触发硬退出、不写台账（全新目录，无 report.json 生成）
    d_pass = mk_gate_driver(tempfile.mkdtemp(), {"action": "pass", "status": "pass", "reply": "通过"})
    d_pass._gate("12", "成片审核", "cap")
    assert _exits == [], _exits
    assert not os.path.exists(os.path.join(d_pass.stamps_dir, "report.json"))
    # retry 决议循环（票8 #67 Q2/Q3）：shot_regen 带 shots → regen_fn(shots) →
    # 重新送审；regen 无 shots 键 → shots 空表（封面重做语义）→ pass 返回，不退出
    class _SeqFeishu:
        def __init__(self, results):
            self._rs = list(results)
            self.sent = []

        def gate_confirm(self, **kw):
            return self._rs.pop(0)

        def send_text(self, text):
            self.sent.append(text)

    _regen_calls = []
    d_retry = mk_gate_driver(tempfile.mkdtemp(), None)
    d_retry.feishu = _SeqFeishu([
        {"action": "retry", "status": "shot_regen", "reply": "重生成 shot02", "shots": ["shot02"]},
        {"action": "retry", "status": "regen", "reply": "重生成"},
        {"action": "pass", "status": "pass", "reply": "通过"},
    ])
    d_retry._gate("12", "成片审核", "cap",
                  regen_fn=lambda shots=None: _regen_calls.append(shots))
    assert _regen_calls == [["shot02"], []], _regen_calls
    assert _exits == [], _exits
    # pass 路径不 flush（票7 定案）：三条留痕问 ledger 增量视图，不在盘上
    assert [e["status"] for e in d_retry.ledger.get("gate_replies")] == ["shot_regen", "regen", "pass"]
    assert not os.path.exists(os.path.join(d_retry.stamps_dir, "report.json"))
    # retry 后 abort：_flush_exit 先落盘——重做轮的留痕不丢（门退出台账完整）
    d_ab = mk_gate_driver(tempfile.mkdtemp(), None)
    d_ab.feishu = _SeqFeishu([
        {"action": "retry", "status": "shot_regen", "reply": "重生成 shot02", "shots": ["shot02"]},
        {"action": "abort", "status": "feedback", "reply": "还是不对"},
    ])
    d_ab._gate("12", "成片审核", "cap", regen_fn=lambda shots=None: None)
    assert _exits == [4], _exits
    _rep_ab = json.load(open(os.path.join(d_ab.stamps_dir, "report.json"), encoding="utf-8"))
    assert [e["status"] for e in _rep_ab["gate_replies"]] == ["shot_regen", "feedback"]
finally:
    os._exit = _real__exit

# ── 5. #48 硬退出机制实测：非 daemon 线程残留下秒退 ─────────────────────
# sys.exit 会被非 daemon 线程挂死（0922 实测 30+ 分钟）；子进程起一个挂着
# 30 分钟非 daemon 线程的 driver，走 _flush_exit，须立即退出且台账落盘。
tmp5 = tempfile.mkdtemp()
_snippet = f"""
import os, sys, threading, time
sys.path.insert(0, {HERE!r})
import run_pipeline as rp
from ledger import Ledger
t = threading.Thread(target=lambda: time.sleep(1800))
t.start()  # 非 daemon：sys.exit 会等它，os._exit 不等
d = rp.Driver.__new__(rp.Driver)
d.stamps_dir = {tmp5!r}
d._t0 = 0.0
d.ledger = Ledger(os.path.join({tmp5!r}, "report.json"), t0=0.0)
d.ledger.record_event("gate_replies", {{"gate": "12", "status": "timeout", "reply": "", "at": "t"}})
d._flush_exit(4)
"""
_t0 = time.time()
_p = subprocess.run([sys.executable, "-c", _snippet], capture_output=True, text=True, timeout=60)
_elapsed = time.time() - _t0
assert _p.returncode == 4, (_p.returncode, _p.stderr[-500:])
assert _elapsed < 30, f"硬退出耗时 {_elapsed:.1f}s，未绕过线程收尾"
_rep5 = json.load(open(os.path.join(tmp5, "report.json"), encoding="utf-8"))
assert _rep5["gate_replies"][0]["status"] == "timeout", _rep5

# ── 6. #59 双车道接线：车道选择 / 阶段表切换 / 虚拟 stamp / 车道切换清 stamps ──
# 6a. 车道选择优先级：CLI --lane > env DRIVER_LANE > 默认 compressed；非法值 SystemExit
d = rp.Driver.__new__(rp.Driver)
assert rp.resolve_lane(None, None) == "compressed", "默认须压缩车道"
assert rp.resolve_lane(None, "legacy") == "legacy", "env 可切旧车道"
assert rp.resolve_lane("legacy", "compressed") == "legacy", "CLI 优先于 env"
assert rp.resolve_lane("compressed", "legacy") == "compressed"
try:
    rp.resolve_lane("gsq", None)
    raise AssertionError("非法车道 gsq 应 SystemExit")
except SystemExit:
    pass
# falsy（空串/None）：回退默认而非报错（DRIVER_LANE= 空串 = 未设置）
assert rp.resolve_lane("", None) == "compressed"
assert rp.resolve_lane(None, "") == "compressed"
print("test_run_pipeline: 车道选择优先级 OK")

# 6b. 阶段表随车道切换：compressed = s04 单阶段 Strata + s05/s06/s07 虚拟；
#     legacy = 原 v3 四阶段链。阶段号/顺序保持 s01-s13 兼容（外部查询/断点续跑依赖）。
COMPRESSED_TITLES = {
    "s04": "设计+H3 prompt（Strata 压缩车道，单次调用）",
    "s05": "虚拟 stamp（压缩车道已并入 s04）",
    "s06": "虚拟 stamp（压缩车道已并入 s04）",
    "s07": "虚拟 stamp（压缩车道已并入 s04）",
}


def mk_lane_driver(lane):
    d = rp.Driver.__new__(rp.Driver)
    d.lane = lane
    return d


stages_c = mk_lane_driver("compressed")._stage_list()
stages_l = mk_lane_driver("legacy")._stage_list()
assert [s[0] for s in stages_c] == [s[0] for s in stages_l] == [f"s{i:02d}" for i in range(1, 14)]
c = {no: (title, fn) for no, title, fn, _, _ in stages_c}
l = {no: (title, fn) for no, title, fn, _, _ in stages_l}
assert c["s04"][1].__name__ == "s04_design_h3", "压缩车道 s04 须为单阶段设计+H3"
assert l["s04"][1].__name__ == "s04_director" and l["s07"][1].__name__ == "s07_h3prompt", "旧车道须保留 v3 链"
for no in ("s05", "s06", "s07"):
    assert c[no][1].__name__ == "_noop_virtual", f"压缩车道 {no} 须虚拟阶段"
    assert c[no][0] == COMPRESSED_TITLES[no], c[no][0]
    assert l[no][1].__name__ != "_noop_virtual", f"旧车道 {no} 不得虚拟化"
# s08 及以后两车道同一实现（下游无感知）；bound method 每次访问都是新对象，比 __func__
for no in ("s01", "s02", "s03", "s08", "s09", "s10", "s11", "s12", "s13"):
    assert c[no][1].__func__ is l[no][1].__func__, no
print("test_run_pipeline: 双车道阶段表 OK")

# 6c. 车道切换守卫：lane.txt 记录既往车道，变化 → 自动清 s04-s07 stamps + 台账留痕；
#     相同/首次 → 不动。旧车道四阶段各有产物，不清会半新半旧。
tmp6 = tempfile.mkdtemp()
d = rp.Driver.__new__(rp.Driver)
d.stamps_dir = tmp6
d.lane = "compressed"
d.ledger = Ledger(os.path.join(tmp6, "report.json"), t0=0.0)


def _put_stamp(no):
    json.dump({"ts": 1}, open(os.path.join(tmp6, f"stage_{no}.json"), "w"))


for no in ("s03", "s04", "s05", "s06", "s07", "s08"):
    _put_stamp(no)
d._guard_lane_switch()   # 首次（无 lane.txt）：只写记录不清 stamps
assert os.path.exists(os.path.join(tmp6, "stage_s04.json"))
assert open(os.path.join(tmp6, "lane.txt"), encoding="utf-8").read().strip() == "compressed"
d.lane = "legacy"
d._guard_lane_switch()   # compressed → legacy：清 s04-s07，保 s03/s08
assert not os.path.exists(os.path.join(tmp6, "stage_s04.json"))
assert not os.path.exists(os.path.join(tmp6, "stage_s07.json"))
assert os.path.exists(os.path.join(tmp6, "stage_s03.json")) and os.path.exists(
    os.path.join(tmp6, "stage_s08.json")), "车道无关阶段不得误清"
assert d.ledger.get("lane_switch_cleared") == ["s04", "s05", "s06", "s07"]
assert open(os.path.join(tmp6, "lane.txt"), encoding="utf-8").read().strip() == "legacy"
d.lane = "legacy"
d.ledger.record("lane_switch_cleared", [])
d._guard_lane_switch()   # 同车道：不动
assert os.path.exists(os.path.join(tmp6, "stage_s08.json"))
assert d.ledger.get("lane_switch_cleared") == []
print("test_run_pipeline: 车道切换清 stamps OK")

# 6d. 虚拟 stamp 落盘：meta 带 lane/virtual，断点续跑 done() 生效
tmp6b = tempfile.mkdtemp()
d = rp.Driver.__new__(rp.Driver)
d.stamps_dir = tmp6b
d.lane = "compressed"
d.date = "2026-09-24"
d._script_hash = lambda: "abc123"
d._write_virtual_stamps()
for no in ("s05", "s06", "s07"):
    assert d.done(no), f"虚拟 stamp {no} 须落盘"
    meta = json.load(open(os.path.join(tmp6b, f"stage_{no}.json"), encoding="utf-8"))["meta"]
    assert meta["lane"] == "compressed" and meta["virtual"] is True, meta
print("test_run_pipeline: 虚拟 stamp OK")

# 6e. s04_design_h3：注入假 run_lane → 走通对账 + 台账；失败 → 异常上抛（run_stage 重试语义不变）
# 6e. s04_design_h3：注入假 _run_lane（真实 run_lane 契约：0=成功 / 1=连败零落盘）
#     → 走通对账 + 台账 + 虚拟 stamp；连败（1）→ RuntimeError（run_stage 重试语义不变）
tmp6c = tempfile.mkdtemp()
d = rp.Driver.__new__(rp.Driver)
d.stamps_dir = tmp6c   # stamp() 落盘位置（虚拟 stamp 断言也用它）
d.slug = tmp6c
d.script = __file__   # stamp() 要算 script_hash
d.date = "2026-09-24"
d.lane = "compressed"
d.args = types.SimpleNamespace(style="chinese-celestial-palace")
d.d = {n: os.path.join(tmp6c, n) for n in ("01_分段", "06_H3prompt")}
d.ledger = Ledger(os.path.join(tmp6c, "report.json"), t0=0.0)
os.makedirs(os.path.join(tmp6c, "01_分段"))
os.makedirs(os.path.join(tmp6c, "06_H3prompt"))
json.dump([{"id": "shot01"}, {"id": "shot02"}],
          open(os.path.join(tmp6c, "01_分段", "分镜清单_raw.json"), "w", encoding="utf-8"))
d._gate_strata = lambda: None         # #84 接缝：Strata 在线门注入为直通（6f 单测）
calls = []


def fake_run_lane(slug, style_name=None):
    """假车道执行：外部行为仿真——成功落盘 rec 产物返回 0；连败返回 1（零落盘）。"""
    calls.append((slug, style_name))
    if len(calls) > 1:
        return 1
    for sid in ("shot01", "shot02"):
        json.dump({"id": sid, "prompt": "p", "video_seconds": 9.5,
                   "portrait_risk": False, "camera_sanitized": False},
                  open(os.path.join(tmp6c, "06_H3prompt", f"{sid}.json"), "w", encoding="utf-8"))
    return 0


d._run_lane = fake_run_lane
d.s04_design_h3()
assert calls == [(tmp6c, "chinese-celestial-palace")], calls
rep = d.ledger.get("s04")
assert rep["lane"] == "compressed" and rep["ok"] == 2, rep
# 虚拟 stamp 由 s04_design_h3 自动补写
for no in ("s05", "s06", "s07"):
    assert d.done(no), f"成功后虚拟 stamp {no} 须落盘"
# 成功路径重跑（stamps 在）：s04 也 done → run_stage 短路，不重复烧调用

# 失败路径：run_lane 返回 1 → RuntimeError（不写台账成功块、不写虚拟 stamp）
try:
    d.s04_design_h3()
    raise SystemExit("run_lane 连败（返回 1）应 RuntimeError")
except RuntimeError as e:
    assert "整单连败" in str(e), e
# 对账错位：目录混入多余段 → 硬失败（不许 stale 产物伪装齐段续跑进 s08）
json.dump({"id": "shot99"}, open(os.path.join(tmp6c, "06_H3prompt", "shot99.json"), "w",
                                 encoding="utf-8"))
d._run_lane = lambda slug, style_name=None: 0
try:
    d.s04_design_h3()
    raise SystemExit("对账错位应 RuntimeError")
except RuntimeError as e:
    assert "对账错位" in str(e) and "shot99" in str(e), e
print("test_run_pipeline: s04_design_h3 接线 OK")

# 6f. _gate_strata 生命周期钩子接缝（#85）：ensure_online 自愈阶梯被调且台账留痕；
#     假阶梯原样替换模块级函数（gate 内以 strata_window.ensure_online 全局名引用，
#     monkeypatch rp.strata_window.ensure_online 生效）
d = rp.Driver.__new__(rp.Driver)
d.lane = "compressed"
d.notify = lambda t: None
d.ledger = Ledger(os.path.join(tempfile.mkdtemp(), "report.json"), t0=0.0)
_real_ensure = rp.strata_window.ensure_online
try:
    rp.strata_window.ensure_online = lambda notify=None: {
        "outcome": "started", "restarts": 0, "warmed": True,
        "at": "2026-10-12 00:00:00"}
    d._gate_strata()
    _rec = d.ledger.get("strata.s04_entry")
    assert _rec["outcome"] == "started" and _rec["restarts"] == 0, _rec
finally:
    rp.strata_window.ensure_online = _real_ensure
print("test_run_pipeline: Strata 生命周期钩子接缝 OK")

# 6g. report_summary 压缩车道分支：s04 新语义块替换旧 v3 设计统计展示
d = rp.Driver.__new__(rp.Driver)
d.ledger = Ledger(os.path.join(tempfile.mkdtemp(), "report.json"), t0=0.0)
d.ledger.record("s04", {"lane": "compressed", "ok": 16, "total": 16, "camera_sanitized": 1})
d.ledger.record("s07", {"total": 16, "ok": 16, "camera_sanitized": 1})
s = d.report_summary()
assert "压缩车道 16/16" in s and "运镜消毒 1 段" in s, s
# 旧 v3 统计（无 lane 键）行为不变：横移超 1/3 才展示
d.ledger.record("s04", {"lateral_design_ratio": 0.5})
assert "横移类运镜占 50%" in d.report_summary()
# 票7 bug② 回归：flip_regen_rounds 键为 mode（0911 改键），旧代码读 r["sanitized"]
# 会 KeyError——翻转重生成期跑到发布通知即炸；strip/rewrite 记消毒，resample 不记
d.ledger.record("s08", {"flip_regen_rounds": [
    {"round": 1, "shots": ["shot02"], "mode": "strip"},
    {"round": 2, "shots": ["shot03"], "mode": "resample"}]})
s2 = d.report_summary()
assert "翻转重生成 第1轮1段(消毒)；第2轮1段" in s2, s2
print("test_run_pipeline: report_summary 车道分支 OK")

# 6h. 连败叫人消息路由：压缩车道 s04 必须走 Strata 专用步骤（步骤文本含 llama 字样的
#     重启命令，也不得被 qwen 分支劫持——8888 重启步骤在 Strata/H3 同卡窗口内照做必触显存互斥）
_sent = []
class _CapFeishu:
    def send_text(self, text):
        _sent.append(text)


def mk_s4_fail_driver():
    d = rp.Driver.__new__(rp.Driver)
    d.stamps_dir = tempfile.mkdtemp()
    d.slug = "t"
    d.script = __file__
    d.date = "2026-09-24"
    d.lane = "compressed"
    d._t0 = 0.0
    d.ledger = Ledger(os.path.join(d.stamps_dir, "report.json"), t0=0.0)
    d.feishu = _CapFeishu()
    d.args = types.SimpleNamespace(style="x")
    d.d = {n: "/tmp/nowhere" for n in ("07_成片",)}
    return d


_real_exit = sys.exit
_real_log = rp.log
try:
    sys.exit = lambda code: None          # 连败路径 sys.exit(3)，测试吃掉
    rp.log = lambda msg: None
    d = mk_s4_fail_driver()
    d._gate_strata = lambda: None
    d._run_lane = lambda slug, style_name=None: 1   # 整单连败
    import llm_json as _lj_mod
    d.run_stage("s04", "设计+H3 prompt（Strata）", d.s04_design_h3)
finally:
    sys.exit = _real_exit
    rp.log = _real_log
assert any("Strata 排查" in t and "8888" not in t for t in _sent), _sent
assert not any("start_qwen.sh" in t for t in _sent), "叫人消息不得劫持到 8888 步骤"
# 票7 review 回归：连败落盘的阶段条目四键齐全（旧 setdefault 键形冻结；
# 曾因 add 增量只带增量键，首跑缺 errors/seconds 偏离格式）
_st = json.load(open(os.path.join(d.stamps_dir, "report.json"), encoding="utf-8"))["stages"]["s04"]
assert set(_st) >= {"title", "attempts", "errors", "seconds"}, _st
assert _st["attempts"] == 2 and _st["seconds"] == 0.0 and len(_st["errors"]) == 2, _st
print("test_run_pipeline: 连败消息路由（Strata 专用步骤）OK")

import strata_window as _sw
import comfyui_selfheal as _cs

# 6j. s08 入口关窗（#85 窗口纪律：conflict 模式生图前停 Strata，断点续跑直入
#     s08 时同样经此关窗；coexist 模式常驻不停）。窗口纪律不分车道——两车道
#     文案链同指 Strata（#86），分叉点只剩窗口模式。
_real_stop6j = _sw.stop
_real_mode6j = _sw.WINDOW_MODE
_real_heal6j = _cs.heal
_real_shutdown6j = _cs.shutdown_comfyui
try:
    _cs.heal = lambda notify=None, vram_gate_mib=None: {"ok": True, "actions": []}
    # 测试机 heal 探活必失败 → 会走 WMI 重启路径动真系统，必须桩掉（历史 6j 未桩，
    # 属漏桩；本票顺手闭合）
    _cs.shutdown_comfyui = lambda notify=None, wait_vram=True: {"outcome": "stubbed"}
    _sw.WINDOW_MODE = "conflict"
    d = rp.Driver.__new__(rp.Driver)
    d.ledger = Ledger(os.path.join(tempfile.mkdtemp(), "report.json"), t0=0.0)
    d.notify = lambda t: None
    _stop_calls = []
    _sw.stop = lambda notify=None: (_stop_calls.append(notify), {"outcome": "closed"})[1]
    # s08_h3 走到 prompt 对账即止：分镜清单缺失抛 OSError（FileNotFoundError），但关窗已发生
    d.d = {n: "/tmp/nowhere" for n in ("01_分段", "06_H3prompt", "07_成片")}
    try:
        d.s08_h3()
        raise SystemExit("产物缺失应 RuntimeError")
    except OSError:
        pass
    assert len(_stop_calls) == 1, "conflict 模式 s08 入口必须关窗"
    assert d.ledger.get("strata_window.s08_entry_close")["outcome"] == "closed"

    _sw.WINDOW_MODE = "coexist"
    d = rp.Driver.__new__(rp.Driver)
    d.ledger = Ledger(os.path.join(tempfile.mkdtemp(), "report.json"), t0=0.0)
    d.notify = lambda t: None
    d.d = {n: "/tmp/nowhere" for n in ("01_分段", "06_H3prompt", "07_成片")}
    _stop_calls.clear()
    try:
        d.s08_h3()
    except OSError:
        pass
    assert _stop_calls == [], "coexist 模式不得停 Strata（常驻）"
finally:
    _sw.stop = _real_stop6j
    _sw.WINDOW_MODE = _real_mode6j
    _cs.heal = _real_heal6j
    _cs.shutdown_comfyui = _real_shutdown6j
print("test_run_pipeline: s08 关窗接缝 OK")

# ── 7. 票9 #72：s11_cover 源码移植术拆除——ref 文件传参 seam ────────────
# 旧形态：读 gen_krea2_cover.py 源码 re.sub 三补丁（REF_H/REF_V/HERE）→
# 写临时副本 _gen_cover_run.py 执行；新形态：ref 落盘 slug/_cover_ref.txt →
# --ref-file 直调原脚本。桩 sh()/WIN_COVER_OUT/_platform_covers，收割逻辑
# （mtime 快照 + PIL 横竖分类 + copy2）走真实现。
from PIL import Image as _PILImage

_real_sh = rp.sh
_real_wco = rp.WIN_COVER_OUT
_real_log9 = rp.log  # try 前取值（文件惯例）：try 内任何一步抛异常 finally 都能还原
try:
    _tmp9 = tempfile.mkdtemp()
    _slug9 = os.path.join(_tmp9, "slug")
    _cover9 = os.path.join(_tmp9, "09_封面")
    _wco9 = os.path.join(_tmp9, "comfy_out")
    for _p in (_slug9, _cover9, _wco9):
        os.makedirs(_p, exist_ok=True)
    rp.WIN_COVER_OUT = _wco9
    _sh_cmds = []

    def _fake_sh(cmd, env=None, timeout=None, cwd=None):
        _sh_cmds.append((cmd, timeout))
        # 模拟 ComfyUI 出图：一横一竖落 WIN_COVER_OUT
        _PILImage.new("RGB", (1536, 768), "red").save(os.path.join(_wco9, "ComfyUI_001.png"))
        _PILImage.new("RGB", (768, 1344), "blue").save(os.path.join(_wco9, "ComfyUI_002.png"))
        return 0, "done"

    rp.sh = _fake_sh
    d = rp.Driver.__new__(rp.Driver)
    d.slug = _slug9
    d.d = {"09_封面": _cover9}
    d.notify = lambda t: None
    d.ledger = Ledger(os.path.join(tempfile.mkdtemp(), "report.json"), t0=0.0)
    d._cover_ref_text = lambda: "当日参考文本'引号'\"双引号\"\n第二行"
    d._platform_covers = lambda: None
    # s11 入口生命周期钩子桩掉（曾漏桩——真拉起了 Windows Strata，21s 冷启动
    # 意外验证了 launch 链路，但测试必须隔离外部世界）
    _ensure9_calls = []
    _real_ensure9 = rp.strata_window.ensure_online
    rp.strata_window.ensure_online = lambda notify=None: (
        _ensure9_calls.append(notify), {"outcome": "started", "restarts": 0,
                                        "warmed": True, "at": "2026-10-12 00:00:00"})[1]
    rp.log = lambda msg: None
    d.s11_cover()
    assert len(_ensure9_calls) == 1, "s11 生成路径必须开窗（底图已在的断点路径不开）"
    # sh 命令：直调原脚本 + --ref-file，不再有临时副本
    assert len(_sh_cmds) == 1, _sh_cmds
    _cmd, _to = _sh_cmds[0]
    assert "gen_krea2_cover.py" in _cmd and "--ref-file" in _cmd, _cmd
    assert "_gen_cover_run" not in _cmd, f"临时副本应已消失: {_cmd}"
    assert _to == 10800, _cmd
    # ref 落盘：内容逐字（转义雷走文件不走 shell）
    _ref_fp = os.path.join(_slug9, "_cover_ref.txt")
    assert os.path.exists(_ref_fp), "ref 应落盘 slug/_cover_ref.txt"
    assert open(_ref_fp, encoding="utf-8").read() == d._cover_ref_text()
    assert not os.path.exists(os.path.join(_slug9, "_gen_cover_run.py")), "临时副本不得再写"
    # 收割走真实现：横竖分类 + copy2 到 09_封面
    assert d._covers == [os.path.join(_cover9, "海报_krea2数字迷幻_横版.png"),
                         os.path.join(_cover9, "海报_krea2数字迷幻_竖版.png")], d._covers
    for _c in d._covers:
        assert os.path.exists(_c), _c
    # 断点续跑路径：底图已在 → 不再起 GPU（sh 零调用）
    _sh_cmds.clear()
    d2 = rp.Driver.__new__(rp.Driver)
    d2.slug, d2.d = _slug9, {"09_封面": _cover9}
    d2._platform_covers = lambda: None
    d2.s11_cover()
    assert _sh_cmds == [], "底图已在应跳过生成"
    assert len(d2._covers) == 2
    assert len(_ensure9_calls) == 1, "底图已在的断点路径不开窗（不白拉 LLM 服务）"
finally:
    rp.sh = _real_sh
    rp.WIN_COVER_OUT = _real_wco
    rp.log = _real_log9
    rp.strata_window.ensure_online = _real_ensure9
print("test_run_pipeline: 票9 s11_cover 移植术拆除接缝 OK")

# ── 10. #170 log() cp936 降级：预检叫人消息（🚨）在 gbk strict stdout 下不掀翻 ──
# 诚实验证：不靠 PYTHONIOENCODING（跑器注入那是「跑器绿」，本票钉的是「裸跑也不崩」），
# 用 gbk+strict 的 TextIOWrapper 盖住 sys.stdout，让 print 走真 strict 编码路径。
_real_stdout = sys.stdout
_buf = io.BytesIO()
try:
    sys.stdout = io.TextIOWrapper(_buf, encoding="gbk", errors="strict")
    rp.log("🚨 环境预检失败（测试桩消息）：ComfyUI 无响应")
finally:
    _got = _buf.getvalue()
    sys.stdout = _real_stdout
_got_text = _got.decode("gbk", errors="replace")
assert "UnicodeEncodeError" not in _got_text  # 没崩到把异常文本打进输出（防御性）
assert "\\U0001f6a8" in _got_text, f"emoji 应降级为 U+ 转义: {_got_text!r}"
assert "环境预检失败" in _got_text, f"中文段必须原样保留: {_got_text!r}"
assert "[driver " in _got_text, f"时间戳前缀应保留: {_got_text!r}"
print("test_run_pipeline: #170 log() cp936 降级 OK（gbk strict 下 emoji 转义、中文保留）")

print("test_run_pipeline: 双车道接线全部通过")
print(f"test_run_pipeline: 全部通过（#48 子进程硬退出 {_elapsed:.1f}s）")
