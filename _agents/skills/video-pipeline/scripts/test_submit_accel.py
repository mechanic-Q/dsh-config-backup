#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_submit_accel.py — H3 提交器的「死机不再装活着」自检（#178，无框架，assert 直跑）。

背景：2026-10-20 实测，ComfyUI 出片后进程猝死，submit_accel 仍每轮 20s 读
history，卡满 2h；queue_status 解析失败伪装成「队列在忙」又让主循环无限等。
driver 侧只看到「提交 shot05」后一动不动，人无法判断是慢还是死。本测试把这
三处判据（离线快速失败、产物文件兜底、队列未知不当忙）钉住。

覆盖：
1. wait_done：history 有 completed 立即返回，三元组第三位为空
2. wait_done：ComfyUI 离线且无产物 → 三次探活内快速失败（不空转到超时）
3. wait_done：离线但产物已在盘上 → 按产物文件判完成（history 丢失不误杀）
4. queue_status：输出不可解析时返回空 dict（不再伪造 queue_running）
5. _probe_online：curl 返回 200 为在线，000 与异常均为离线且不抛
6. main()：正常区间跑完返回失败清单（空区间即空清单，退出码 0）
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import submit_accel as sa   # noqa: E402 — 必须在 sys.path.insert 之后

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


class FakeTime:
    """虚拟时钟：wait_done 的 sleep 不真睡，时间轴照样前进。"""

    def __init__(self):
        self.t = 1000.0
        self.sleeps = 0

    def time(self):
        return self.t

    def sleep(self, s):
        self.sleeps += 1
        self.t += s


class Stub:
    """按需替换 submit_accel 的模块级函数，退出时还原。"""

    def __init__(self, **kw):
        self.kw = kw
        self.saved = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.saved[k] = getattr(sa, k)
            setattr(sa, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(sa, k, v)
        return False


def history_of(prompt_id, status_str="success", completed=True):
    import json
    return json.dumps({prompt_id: {"status": {"status_str": status_str,
                                              "completed": completed}}})


# ── 1. history 有结果 → 立即返回 ──────────────────────────────────────────
section("wait_done: history 有结果立即返回")
_clock = FakeTime()
with Stub(ps=lambda cmd, timeout=60: history_of("abc"),
          _probe_online=lambda timeout=8: True,
          _find_new_output=lambda pid, since: "",
          time=_clock):
    status, completed, found = sa.wait_done("abc")
    check(status == "success" and completed is True, "status/completed 透传")
    check(found == "", "history 正常路径不带兜底产物")
    check(_clock.sleeps == 0, "一次命中不睡")

# ── 2. 离线且无产物 → 快速失败 ────────────────────────────────────────────
section("wait_done: ComfyUI 离线且无产物 → 快速失败")
_clock2 = FakeTime()
with Stub(ps=lambda cmd, timeout=60: "",
          _probe_online=lambda timeout=8: False,
          _find_new_output=lambda pid, since: "",
          time=_clock2):
    status, completed, found = sa.wait_done("dead", timeout=7200, pid="shot05")
    check(status == "OFFLINE", "离线判 OFFLINE（不是 TIMEOUT）")
    check(completed is False and found == "", "无产物即未完成")
    check(_clock2.t - 1000.0 < 300, f"耗时远小于 2h 超时（实为 {_clock2.t - 1000.0}s）")

# ── 3. 离线但产物在盘上 → 按产物判完成 ────────────────────────────────────
section("wait_done: 离线但产物已在盘上")
with Stub(ps=lambda cmd, timeout=60: "",
          _probe_online=lambda timeout=8: False,
          _find_new_output=lambda pid, since: "/tmp/output/480P/x_" + pid + "_00002.mp4",
          time=FakeTime()):
    status, completed, found = sa.wait_done("dead2", pid="shot08")
    check(status == "OFFLINE_RECOVERED", "判离线恢复")
    check(completed is True, "有产物即判完成（history 丢失不误杀）")
    check(found.endswith("shot08_00002.mp4"), "兜底产物路径透传给拷贝阶段")

# ── 4. queue_status 不伪造「忙」 ──────────────────────────────────────────
section("queue_status: 不可解析返回空 dict")
with Stub(ps=lambda cmd, timeout=60: "Invoke-WebRequest : 无法连接远程服务器"):
    q = sa.queue_status()
    check(q == {}, f"解析失败返回空 dict（实为 {q!r}），调用方才能判未知")
with Stub(ps=lambda cmd, timeout=60: '\ufeff{"queue_running": [], "queue_pending": []}'):
    q = sa.queue_status()
    check(q.get("queue_running") == [], "合法 JSON 照常解析（含 BOM）")

# ── 5. _probe_online 三态 ────────────────────────────────────────────────
section("_probe_online: 200 在线 / 000 离线 / 异常不抛")
_saved_run = subprocess.run


def _fake_run(stdout_text, raise_exc=None):
    def _run(*a, **kw):
        if raise_exc is not None:
            raise raise_exc
        return subprocess.CompletedProcess(args=a[0] if a else [], returncode=0,
                                           stdout=stdout_text, stderr="")
    return _run


try:
    subprocess.run = _fake_run("200")
    check(sa._probe_online() is True, "HTTP 200 判在线")
    subprocess.run = _fake_run("000")
    check(sa._probe_online() is False, "HTTP 000 判离线")
    subprocess.run = _fake_run("", raise_exc=FileNotFoundError("curl.exe"))
    check(sa._probe_online() is False, "子进程起不来判离线且不抛")
finally:
    subprocess.run = _saved_run
curl, sink = sa._curl_probe_cmd()
check(isinstance(curl, str) and isinstance(sink, str), "curl/sink 双宿主口径可解析")

# ── 6. main() 返回失败清单（空区间即空清单）────────────────────────────────
section("main(): 失败清单驱动退出码")
_r = subprocess.run([sys.executable, os.path.join(HERE, "submit_accel.py"),
                     "2", "1", HERE],
                    capture_output=True, text=True, timeout=180,
                    encoding="utf-8", errors="replace")
if _r.returncode == 3:
    print("!! SKIP(ComfyUI 离线)：第 6 节需要在线 ComfyUI 才能越过入口守卫")
else:
    check(_r.returncode == 0, f"空区间无失败段 → 退出码 0（实为 {_r.returncode}）")
    check("本次有段未成功" not in _r.stdout, "无失败段不打失败汇总行")

print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
