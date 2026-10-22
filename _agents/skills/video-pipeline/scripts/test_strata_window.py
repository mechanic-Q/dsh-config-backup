#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_strata_window.py — Strata 生命周期托管自检（#85，assert 直跑风格）。

测试决策（沿袭 test_kvmem_window SPEC）：假 urlopen/假进程/假 Popen 测分支逻辑，
**不真起服务**——strata_window 的全部外部触点（probe / warmup 的 urlopen、launch 的
Popen、port_pids / kill_tree / vram_used_mib 的 run、wait_* 的探活与显存桩、time、log）
都是模块级函数或参数注入点，原地替换即可驱动阶梯全部分支。

覆盖（票 #85 验收）：
1. probe：200 在线 / 拒连离线
2. ensure_online 快路径：在线 → 复用（不 launch，预热一发）
3. 离线 → 拉起 → 加载窗口内就绪 → 预热（outcome=started，冷启动不占预算）
4. 加载超时 → 清残留 + 修复重启 → 第二次就绪（restarts=1，占预算）
5. 持续超时 → MAX_ROUNDS 轮后 StrataHardError（硬失败叫人）
6. launch：默认 powershell 串 + fire-and-forget Popen 形态
7. port_pids：netstat 输出解析（IPv4/IPv6 去重、非监听行忽略）
8. stop：在线树杀 → 探活转离线 + 显存回落 → closed（关窗清预算）
9. stop 幂等：离线 already-off 不动手
10. stop 失败：taskkill 后仍在线 RuntimeError / 自身显存泄漏 RuntimeError / 别家占卡放行
11. 重启预算硬边界：第 MAX_ROUNDS+1 次 _use_restart 即 StrataHardError
12. wait_online / wait_vram_idle 有界性（fake time 推进）
13. warmup：预热失败不抛（返回 False）
14. WINDOW_MODE 默认 conflict（#83 §3 落定）；STALL_SECS 迁移值 600
"""
import json
import os
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import strata_window as sw

# ── 假时间：sleep 记账不真睡；time.time() 手动推进 ────────────────────────
class FakeTime:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def time(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def strftime(self, *a):
        return "2026-10-12 00:00:00"


_SAVED = {k: getattr(sw, k) for k in
          ("probe", "warmup", "launch", "stop", "port_pids", "kill_tree",
           "vram_used_mib", "wait_online", "wait_vram_idle", "time", "log")}


def restore():
    for k, v in _SAVED.items():
        setattr(sw, k, v)


def reset_state():
    sw._STATE.update(open=False, restarts=0)


class _Resp:
    def __init__(self, body=b"{}", status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def stub(online=False, launch_fails=False, vram=2000, launch_sets_online=True):
    """一键打桩：探活/拉起/停止/显存/时间。返回 (记录 dict, 探活开关 dict)。
    launch_sets_online=False 用于加载超时形态（拉起命令发出但服务一直不就绪，
    就绪与否完全由 wait 桩决定）。"""
    rec = {"launches": 0, "kills": [], "notes": [], "logs": [], "warms": 0}
    state = {"online": online}

    def _probe(urlopen=None):
        return state["online"]

    def _launch(popen=None):
        if launch_fails:
            raise RuntimeError("Popen 爆炸")
        rec["launches"] += 1
        if launch_sets_online:
            state["online"] = True

    def _stop(notify=None, **kw):
        was = state["online"]
        state["online"] = False
        rec["kills"].append("stop")
        return {"outcome": "closed" if was else "already-off"}

    def _warmup(urlopen=None, timeout=None):
        rec["warms"] += 1
        return True

    sw.probe = _probe
    sw.launch = _launch
    sw.stop = _stop
    sw.warmup = _warmup
    # 加载窗口等待桩：直接读在线态（真实现轮询 probe，FakeTime 下等价但绕远）
    sw.wait_online = lambda timeout=None, interval=5.0, probe_fn=None: state["online"]
    sw.vram_used_mib = lambda run=None, timeout=30: vram
    sw.time = FakeTime()
    sw.log = lambda msg: rec["logs"].append(msg)

    def _notify(text):
        rec["notes"].append(text)
    rec["notify"] = _notify
    rec["state"] = state
    return rec, state


# ── 1. probe：200 / 拒连 ─────────────────────────────────────────────
assert sw.probe(urlopen=lambda url, timeout=None: _Resp()) is True
assert sw.probe(urlopen=lambda url, timeout=None: _Resp(status=404)) is False
def _boom(url, timeout=None):
    raise urllib.error.URLError("conn refused")
assert sw.probe(urlopen=_boom) is False
# 探活 URL 口径：BASE_URL + /v1/models（#83 spike 定案）
_seen = []
sw.probe(urlopen=lambda url, timeout=None: (_seen.append(url), _Resp())[1])
assert _seen == [f"{sw.BASE_URL}/v1/models"], _seen
restore()
print("test_strata_window: probe OK")

# ── 2. 快路径：在线 → 复用，不 launch ────────────────────────────────
reset_state()
rec, state = stub(online=True)
info = sw.ensure_online(notify=rec["notify"])
assert info["outcome"] == "reused" and info["warmed"] is True, info
assert rec["launches"] == 0 and info["restarts"] == 0, (rec["launches"], info)
assert sw._STATE["open"] is True
print("test_strata_window: 在线复用 reused OK")

# ── 3. 离线 → 拉起 → 就绪 → 预热（started，冷启动不占预算）────────────
reset_state()
rec, state = stub(online=False)
info = sw.ensure_online(notify=rec["notify"])
assert info["outcome"] == "started" and info["restarts"] == 0, info
assert rec["launches"] == 1 and rec["warms"] == 1, (rec["launches"], rec["warms"])
assert any("冷启动" in n for n in rec["notes"]), rec["notes"]
print("test_strata_window: 冷启动 started OK")

# ── 4. 加载超时 → 清残留 + 修复重启 → 就绪（restarted，占预算 1）───────
reset_state()
rec, state = stub(online=False, launch_sets_online=False)
_seq = {"wait_calls": 0}


def _wait4(timeout=None, interval=5.0, probe_fn=None):
    _seq["wait_calls"] += 1
    return _seq["wait_calls"] >= 2  # 第一次加载超时，重启后第二次就绪
sw.wait_online = _wait4
info = sw.ensure_online(notify=rec["notify"])
assert info["outcome"] == "restarted" and info["restarts"] == 1, info
assert rec["launches"] == 2, rec["launches"]          # 冷启动 + 1 轮修复重启
assert "stop" in rec["kills"], rec["kills"]           # 重启前清残留
assert any("修复重启" in n for n in rec["notes"]), rec["notes"]
print("test_strata_window: 超时修复重启 restarted OK")

# ── 5. 持续超时 → MAX_ROUNDS 轮后 StrataHardError ────────────────────
reset_state()
rec, state = stub(online=False, launch_sets_online=False)
sw.wait_online = lambda timeout=None, interval=5.0, probe_fn=None: False
try:
    sw.ensure_online(notify=rec["notify"])
    raise SystemExit("持续超时应 StrataHardError")
except sw.StrataHardError as e:
    assert "预算" in str(e) or "未达在线" in str(e), e
assert rec["launches"] == 1 + sw.MAX_ROUNDS, rec["launches"]  # 冷启动 + 2 轮修复
print("test_strata_window: 持续超时硬失败 OK")

# ── 6. launch：默认 powershell 串 + fire-and-forget 形态 ──────────────
restore()
class _FakePopen:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        return object()
fp = _FakePopen()
sw.launch(popen=fp)
argv, kw = fp.calls[0]
assert argv == sw.START_CMD and "powershell.exe" in argv, argv
assert "Start-Process" in argv and "0.0.0.0" in argv, argv
assert kw.get("shell") is True and kw.get("start_new_session") is True, kw
assert kw.get("stdout") == sys.modules["subprocess"].DEVNULL
print("test_strata_window: launch 形态 OK")

# ── 7. port_pids：netstat 输出解析 ───────────────────────────────────
NETSTAT_OUT = ("\r\n活动连接\r\n\r\n  Proto  本地地址          外部地址        状态           PID\r\n"
               "  TCP    172.25.176.1:8080        0.0.0.0:0              LISTENING       12340\r\n"
               "  TCP    [::]:8080                [::]:0                 LISTENING       12340\r\n"
               "  TCP    127.0.0.1:8188           0.0.0.0:0              LISTENING       999\r\n"
               "  TCP    172.25.176.1:8080        1.2.3.4:5555           ESTABLISHED     777\r\n")
_calls = []
def _run7(argv, timeout=30):
    _calls.append(argv)
    return NETSTAT_OUT
pids = sw.port_pids(run=_run7)
assert pids == [12340], pids                       # 双栈去重、异端口与 ESTABLISHED 排除
assert _calls == [["netstat.exe", "-ano"]], _calls
assert sw.port_pids(port=8188, run=_run7) == [999]
print("test_strata_window: port_pids 解析 OK")

# ── 8. stop：树杀 → 双确认 → closed（关窗清预算）──────────────────────
reset_state()
sw._STATE.update(open=True, restarts=sw.MAX_ROUNDS)  # 预算已用满，关窗应清零
rec, state = stub(online=True, vram=2000)
sw.stop = _SAVED["stop"]                             # 场景 8 测真 stop 实现（stub 桩的是壳）
sw.port_pids = lambda port=None, run=None: [12340, 12341]
sw.kill_tree = lambda pid, run=None: (rec["kills"].append(pid), state.__setitem__("online", False))
sw.wait_vram_idle = lambda timeout=120.0, interval=5.0, vram_fn=None: True
info = sw.stop(notify=rec["notify"])
assert info["outcome"] == "closed" and sorted(info["pids"]) == [12340, 12341], info
assert rec["kills"] == [12340, 12341], rec["kills"]
assert sw._STATE == {"open": False, "restarts": 0}, sw._STATE
print("test_strata_window: stop closed OK")

# ── 9. stop 幂等：离线 already-off 不动手 ────────────────────────────
reset_state()
rec, state = stub(online=False)
sw.stop = _SAVED["stop"]                             # 同场景 8：测真实现的早退路径
sw.port_pids = lambda port=None, run=None: (_ for _ in ()).throw(AssertionError("不应查端口"))
info = sw.stop()
assert info["outcome"] == "already-off" and not rec["kills"], info
print("test_strata_window: stop already-off OK")

# ── 10. stop 失败两形态 ──────────────────────────────────────────────
reset_state()
rec, state = stub(online=True, vram=14900)
sw.stop = _SAVED["stop"]                             # 失败形态同样测真实现
state2 = rec["state"]
sw.port_pids = lambda port=None, run=None: [12340]
sw.kill_tree = lambda pid, run=None: None            # 杀了但探活仍在线（假死）
sw.probe = lambda urlopen=None: True
try:
    sw.stop()
    raise SystemExit("杀后仍在线应 RuntimeError")
except RuntimeError as e:
    assert "仍在线" in str(e), e
# 显存不回落，且**就是我们杀的 pid 没退干净** → RuntimeError 不放行（按进程归属判定）
reset_state()
_saved_pids_fn = sw.vram_pids_mib
rec, state = stub(online=True, vram=14900)
sw.stop = _SAVED["stop"]
sw.port_pids = lambda port=None, run=None: [12340]
sw.kill_tree = lambda pid, run=None: state.__setitem__("online", False)  # 服务退了
sw.wait_vram_idle = lambda timeout=120.0, interval=5.0, vram_fn=None: False
sw.vram_pids_mib = lambda pids, run=None, timeout=30: {12340: 12000}
try:
    sw.stop()
    raise SystemExit("自身仍占显存应 RuntimeError")
except RuntimeError as e:
    assert "自身仍占显存" in str(e) and "不放行" in str(e), e

# 10-17 期实况：整卡水位高，但占卡的是别的进程（ComfyUI 常驻 7.5GB）→ 放行
reset_state()
rec, state = stub(online=True, vram=7509)
sw.stop = _SAVED["stop"]
sw.port_pids = lambda port=None, run=None: [12340]
sw.kill_tree = lambda pid, run=None: state.__setitem__("online", False)
sw.wait_vram_idle = lambda timeout=120.0, interval=5.0, vram_fn=None: False
sw.vram_pids_mib = lambda pids, run=None, timeout=30: {}
info = sw.stop()
assert info["outcome"] == "closed" and info["vram_used_mib"] == 7509, info
sw.vram_pids_mib = _saved_pids_fn
restore()
print("test_strata_window: stop 失败形态 OK")

# ── 11. 重启预算硬边界：_use_restart 第 MAX_ROUNDS+1 次即 StrataHardError ──
reset_state()
try:
    for _ in range(sw.MAX_ROUNDS):
        sw._use_restart()
    sw._use_restart()
    raise SystemExit("超预算应 StrataHardError")
except sw.StrataHardError as e:
    assert "预算耗尽" in str(e), e
reset_state()
print("test_strata_window: 重启预算硬边界 OK")

# ── 12. wait_online / wait_vram_idle 有界性（fake time 推进）─────────
restore()
ft = FakeTime()
sw.time = ft
sw.probe = lambda urlopen=None: False
assert sw.wait_online(timeout=50, interval=10) is False
assert ft.t - 1000.0 <= 60, ft.t
ft2 = FakeTime()
sw.time = ft2
sw.vram_used_mib = lambda run=None, timeout=30: 9000
assert sw.wait_vram_idle(timeout=40, interval=10) is False
assert ft2.t - 1000.0 <= 50, ft2.t
ft3 = FakeTime()
sw.time = ft3
sw.vram_used_mib = lambda run=None, timeout=30: 100
assert sw.wait_vram_idle(timeout=40, interval=10) is True
print("test_strata_window: 等待循环有界 OK")

# ── 13. warmup 真实现：失败不抛；请求体方言（enable_thinking=false）────
restore()
_body_seen = []


def _warm_urlopen(req, timeout=None):
    _body_seen.append((req.full_url, json.loads(req.data.decode("utf-8")), timeout))
    return _Resp(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode())
assert sw.warmup(urlopen=_warm_urlopen) is True
url, body, to = _body_seen[0]
assert url == f"{sw.BASE_URL}/v1/chat/completions", url
assert body["chat_template_kwargs"] == {"enable_thinking": False}, body  # #83 §4 T1 定案写法
assert body["max_tokens"] == 8 and to == sw.WARMUP_TIMEOUT, (body, to)
assert sw.warmup(urlopen=_boom) is False             # 拒连 → False 不抛
def _warm_too_long(req, timeout=None):
    raise TimeoutError("180s 界")
assert sw.warmup(urlopen=_warm_too_long) is False
print("test_strata_window: warmup 方言与失败语义 OK")

# ── 14. 口径常量与模式默认值（#83 落定）───────────────────────────────
assert sw.WINDOW_MODE == "conflict", sw.WINDOW_MODE   # 本机实测 94% VRAM 冲突
assert sw.LOAD_TIMEOUT == 300.0, sw.LOAD_TIMEOUT      # 300s 口径保留
assert sw.MAX_ROUNDS == 2, sw.MAX_ROUNDS              # 票3 预算语义沿用
assert sw.STALL_SECS == 600, sw.STALL_SECS            # 挂死判据迁移值
assert sw.PORT == 8080, sw.PORT
print("test_strata_window: 口径常量 OK")

restore()
print("test_strata_window: 全部通过")
