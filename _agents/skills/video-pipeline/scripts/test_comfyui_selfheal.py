#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_comfyui_selfheal.py — ComfyUI 僵尸自愈阶梯自检（外部触点全 mock，assert 直跑风格）。

覆盖：
1. 健康无闸门 → no-op（零动作零通知零杀进程）
2. 健康但显存 ≥ 阈值 → /free 卸载 → 回落 ok（不杀进程不重启）
3. 健康 + /free 无效（显存粘住不降）→ 转重启路径（杀+重启就绪）
4. 不健康（探活 500）+ 8188 有僵尸 PID → 杀僵尸 + 重启就绪（含二次探活确认）
5. 不健康 + 无监听 PID → 仅重启（无 PID 可杀）
6. 重启后 240s 仍不健康 → ok=False（调用方走连败协议）
7. 探活瞬时抖动（一次失败一次 200）→ 不误杀健康实例
8. 显存闸场景且队列在跑 → 先等排干再 /free
9. 自愈内部异常 → 绝不上抛（ok=False + outcome=error）
"""
import comfyui_selfheal as h

SAVED_KEYS = ("probe", "vram_used_mib", "pids_on_port", "kill_tree", "relaunch",
              "free_memory", "queue_running_count", "wait_healthy",
              "wait_queue_drain", "_sleep")


class Rec:
    def __init__(self):
        self.msgs = []

    def __call__(self, t):
        self.msgs.append(t)


def install(h, probe_seq, vram_seq=None, pids_seq=None, queue_seq=None,
            free_ok=True, ready=True):
    """外部触点全替换为按序消费假件；返回 (saved, calls)。

    probe_seq: [(ok, code, err)] 依次消费，取尽后保持 (True,"200","")
    vram_seq:  vram_used_mib 依次消费，取尽后粘住最后一个值
    pids_seq:  pids_on_port 依次消费（每轮一个 list），取尽后恒 []
    queue_seq: queue_running_count 依次消费，取尽后恒 0
    """
    st = {"probe": list(probe_seq), "vram": list(vram_seq or []),
          "pids": list(pids_seq or []), "queue": list(queue_seq or []),
          "kills": [], "relaunch": 0, "free": 0, "sleeps": 0, "ready": ready}
    saved = {k: getattr(h, k) for k in SAVED_KEYS}

    def probe(timeout=None):
        if st["probe"]:
            return st["probe"].pop(0)
        return (True, "200", "")

    def vram():
        if st["vram"]:
            v = st["vram"].pop(0)
            st["vram_last"] = v
            return v
        return st.get("vram_last", 1000)

    def pids(port=None):
        return st["pids"].pop(0) if st["pids"] else []

    def kill(pid):
        st["kills"].append(pid)
        return True

    def relaunch(tag=None):
        st["relaunch"] += 1
        return True

    def free(timeout=20):
        st["free"] += 1
        return free_ok

    def queue(timeout=8):
        if st["queue"]:
            return st["queue"].pop(0)
        return 0

    def ready_fn(timeout=None, interval=None):
        return st["ready"]

    def drain(timeout=None, interval=20):
        return True

    def nosleep(s):
        st["sleeps"] += 1

    h.probe = probe
    h.vram_used_mib = vram
    h.pids_on_port = pids
    h.kill_tree = kill
    h.relaunch = relaunch
    h.free_memory = free
    h.queue_running_count = queue
    h.wait_healthy = ready_fn
    h.wait_queue_drain = drain
    h._sleep = nosleep
    return saved, st


def restore(h, saved):
    for k, v in saved.items():
        setattr(h, k, v)


def test_1_healthy_noop():
    saved, st = install(h, [(True, "200", "")])
    rec = Rec()
    info = h.heal(notify=rec)
    assert info["ok"] and info["outcome"] == "healthy", info
    assert info["actions"] == [], info
    assert st["kills"] == [] and st["relaunch"] == 0 and st["free"] == 0
    assert rec.msgs == [], rec.msgs
    restore(h, saved)
    print("test_comfyui_selfheal: 1 健康 no-op OK")


def test_2_free_path():
    saved, st = install(h, [(True, "200", "")], vram_seq=[13000, 1000], queue_seq=[0])
    info = h.heal(notify=Rec(), vram_gate_mib=4000)
    assert info["ok"] and info["outcome"] == "freed", info
    assert st["free"] == 1 and st["kills"] == [] and st["relaunch"] == 0, st
    assert any("/free" in a for a in info["actions"]), info
    assert info["vram_after"] == 1000, info
    restore(h, saved)
    print("test_comfyui_selfheal: 2 /free 卸载路径 OK")


def test_3_free_fails_goes_restart():
    # /free 返回 200 但显存粘住 13000 不降 → 转重启路径
    saved, st = install(h, [(True, "200", "")], vram_seq=[13000], pids_seq=[["111"]])
    info = h.heal(notify=Rec(), vram_gate_mib=4000)
    assert info["ok"] and info["outcome"] == "restarted-ok", info
    assert st["free"] == 1 and st["kills"] == ["111"] and st["relaunch"] == 1, st
    restore(h, saved)
    print("test_comfyui_selfheal: 3 /free 无效转重启 OK")


def test_4_zombie_kill_relaunch():
    saved, st = install(h, [(False, "500", ""), (False, "500", "")],
                        pids_seq=[["515512"]])
    rec = Rec()
    info = h.heal(notify=rec)
    assert info["ok"] and info["outcome"] == "restarted-ok", info
    assert st["kills"] == ["515512"] and st["relaunch"] == 1, st
    assert any("僵尸" in m for m in rec.msgs), rec.msgs
    restore(h, saved)
    print("test_comfyui_selfheal: 4 杀僵尸+重启 OK")


def test_5_no_pid_relaunch_only():
    saved, st = install(h, [(False, "000", ""), (False, "000", "")], pids_seq=[[]])
    info = h.heal()
    assert info["ok"] and info["outcome"] == "restarted-ok", info
    assert st["kills"] == [] and st["relaunch"] == 1, st
    restore(h, saved)
    print("test_comfyui_selfheal: 5 无 PID 仅重启 OK")


def test_6_never_ready():
    saved, st = install(h, [(False, "000", ""), (False, "000", "")],
                        pids_seq=[[]], ready=False)
    info = h.heal()
    assert info["ok"] is False and info["outcome"] == "restarted-fail", info
    restore(h, saved)
    print("test_comfyui_selfheal: 6 重启后仍不健康 → ok=False OK")


def test_7_transient_blip_no_kill():
    saved, st = install(h, [(False, None, "timeout"), (True, "200", "")])
    info = h.heal()
    assert info["ok"] and info["outcome"] == "healthy", info
    assert st["kills"] == [] and st["relaunch"] == 0 and st["free"] == 0, st
    restore(h, saved)
    print("test_comfyui_selfheal: 7 探活瞬时抖动不误杀 OK")


def test_8_queue_drain_then_free():
    saved, st = install(h, [(True, "200", "")], vram_seq=[13000, 1000],
                        queue_seq=[1, 1, 0])
    info = h.heal(notify=Rec(), vram_gate_mib=4000)
    assert info["ok"] and info["outcome"] == "freed", info
    assert st["free"] == 1 and any("队列排干" in a for a in info["actions"]), info
    restore(h, saved)
    print("test_comfyui_selfheal: 8 队列排干后 /free OK")


def test_9_never_raises():
    saved, st = install(h, [(False, "500", ""), (False, "500", "")], pids_seq=[["1"]])

    def boom(pid):
        raise RuntimeError("boom")

    h.kill_tree = boom
    info = h.heal()
    assert info["ok"] is False and info["outcome"].startswith("error:"), info
    restore(h, saved)
    print("test_comfyui_selfheal: 9 内部异常不上抛 OK")


def test_10_shutdown_not_running():
    saved, st = install(h, [(True, "200", "")], vram_seq=[900])
    info = h.shutdown_comfyui()
    assert info["ok"] and info["outcome"] == "not-running", info
    assert st["kills"] == [], st
    restore(h, saved)
    print("test_comfyui_selfheal: 10 收尾-无实例 noop OK")


def test_11_shutdown_kills_and_settles():
    saved, st = install(h, [(True, "200", "")], vram_seq=[15000, 800],
                        pids_seq=[["911"]])
    rec = Rec()
    info = h.shutdown_comfyui(notify=rec)
    assert info["ok"] and info["outcome"] == "cleaned", info
    assert st["kills"] == list(info["pids"]) and st["kills"], st
    assert info["vram_after"] == 800, info
    assert any("收尾" in m for m in rec.msgs), rec.msgs
    restore(h, saved)
    print("test_comfyui_selfheal: 11 收尾-杀进程+显存回落 OK")


def test_12_shutdown_vram_stuck():
    saved, st = install(h, [(True, "200", "")], vram_seq=[15000], pids_seq=[["911"]])
    info = h.shutdown_comfyui()
    assert st["kills"] == ["911"], st
    assert info["killed"] and not info["ok"], info
    assert info["outcome"] == "cleaned-vram-not-settled", info
    restore(h, saved)
    print("test_comfyui_selfheal: 12 收尾-显存未回落告警 OK")


if __name__ == "__main__":
    test_1_healthy_noop()
    test_2_free_path()
    test_3_free_fails_goes_restart()
    test_4_zombie_kill_relaunch()
    test_5_no_pid_relaunch_only()
    test_6_never_ready()
    test_7_transient_blip_no_kill()
    test_8_queue_drain_then_free()
    test_9_never_raises()
    test_10_shutdown_not_running()
    test_11_shutdown_kills_and_settles()
    test_12_shutdown_vram_stuck()
    print("test_comfyui_selfheal: 全部通过")
