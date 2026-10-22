#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_run_driver_bg.py — #195 自检：后台启动器 + 机器级单实例锁 + s13 收尾打点。

无框架，assert 直跑（python scripts/test_run_driver_bg.py）。
覆盖：
  1. telemetry_line：单行 JSON 形状（前缀 / 排序 / 中文不转义）
  2. s13_publish 收尾：真的打一行 [telemetry]（阶段 / 耗时 / 产物路径 / 平台结果），
     且不混进既有输出
  3. bg_run.json：落盘原子写、--status 能读出 run_id / 退出码 / 日志尾
  4. 单实例守卫：锁被活进程佔 → busy；僵尸锁 → 接管；探不准 → 拒绝；
     同 slug 心跳/bg 记录里进程还活着 → 拒绝
  5. 启动器验活四条路径：running→0 / blocked→1 / failed→1 / 窗口内已退→1 / 无记录→1，
     且 driver 参数 `--` 原样透传
  6. driver_status 集成：有 bg 记录时用它的日志；心跳无退出码时补退出码；
     心跳有自己的退出记录时以心跳为准；running + 活进程 → 运行中
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import driver_status as ds  # noqa: E402 — 先把脚本目录放进 sys.path（同仓裸脚本惯例）
import run_driver_bg as rdbg  # noqa: E402
import run_pipeline as rp  # noqa: E402

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


# ── 夹具 ──────────────────────────────────────────────────────────────
def mk_slug():
    root = tempfile.mkdtemp(prefix="rdbg-")
    os.makedirs(os.path.join(root, ds.DRIVER_DIRNAME), exist_ok=True)
    return root


def jw(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def env_lock(path):
    """把机器级锁指到临时文件（生产默认在用户主目录，测试不碰它）。"""
    old = os.environ.get(rdbg.LOCK_ENV)
    os.environ[rdbg.LOCK_ENV] = path
    return old


def restore_lock(old):
    if old is None:
        os.environ.pop(rdbg.LOCK_ENV, None)
    else:
        os.environ[rdbg.LOCK_ENV] = old


# ── 1. telemetry_line ────────────────────────────────────────────────
def test_telemetry_line():
    section("telemetry_line 形状")
    line = rp.telemetry_line({"b": "中文", "a": 1})
    check(line == '[telemetry] {"a":1,"b":"中文"}',
          "前缀 + sort_keys + 中文不转义（日志里可读、可 grep）")
    payload = json.loads(line[len("[telemetry] "):])
    check(payload == {"a": 1, "b": "中文"}, "去掉前缀后是合法 JSON，能原样解析回来")


# ── 2. s13 收尾打点 ──────────────────────────────────────────────────
def test_s13_telemetry():
    section("s13 收尾打点")
    root = tempfile.mkdtemp(prefix="rdbg-s13-")
    d = rp.Driver.__new__(rp.Driver)
    d.date = "2026-10-22"
    d.platforms = ["bilibili"]
    d.d = {}
    for name in ("07_成片", "09_封面", "10_发布"):
        d.d[name] = os.path.join(root, name)
        os.makedirs(d.d[name], exist_ok=True)
    video = os.path.join(d.d["07_成片"], "最终成片.mp4")
    write_text(video, "fake video")
    write_text(os.path.join(d.d["09_封面"], "封面_横.png"), "fake cover")
    d._plat_covers = {}
    d._headline = lambda: "测试标题"
    d.notify = lambda msg: None
    d.report_summary = lambda: ""

    calls = []
    orig_log, orig_run, orig_sleep = rp.log, rp.run_argv, time.sleep
    rp.log = lambda msg: calls.append(msg)
    rp.run_argv = lambda argv, timeout=None: (0, "已上传 BV1AbCdEf")
    time.sleep = lambda sec: None
    try:
        d.s13_publish()
    finally:
        rp.log, rp.run_argv, time.sleep = orig_log, orig_run, orig_sleep

    tele = [ln for ln in calls if ln.startswith("[telemetry] ")]
    check(len(tele) == 1, "s13 收尾恰好打一行 [telemetry]（别的日志一条没多）")
    payload = json.loads(tele[0][len("[telemetry] "):])
    check(payload["stage"] == "s13" and payload["title"] == "发布+核验",
          "打点带阶段号与标题")
    check(isinstance(payload["seconds"], (int, float)) and payload["seconds"] >= 0,
          "打点带真实耗时（秒）")
    art = payload["artifacts"]
    check(os.path.isfile(art["report"]), "产物路径里报告真实存在：发布核验报告.md")
    check(art["video"] == video and art["pending_dir"].endswith("待发视频号"),
          "产物路径里成片与待发文件夹都对得上")
    check(payload["platforms"]["bilibili"] == {"ok": True, "bv": "BV1AbCdEf"},
          "平台结果原样入打点（B站 BV 核验）")
    shutil.rmtree(root, ignore_errors=True)


# ── 3. 记录与查询 ────────────────────────────────────────────────────
def test_record_and_status():
    section("bg_run.json 与 --status")
    slug = mk_slug()
    logp = write_text(os.path.join(slug, ds.DRIVER_DIRNAME, "bg-r1.log"),
                      "第一行\n第二行\n第三行\n")
    recp = rdbg.record_path(slug)
    rec = {
        "run_id": "20261022-120000-p1", "pid": os.getpid(), "host": ds.host_label(),
        "slug": slug, "log": logp, "record": recp,
        "started_ts": time.time() - 120, "status": rdbg.STATUS_EXITED,
        "driver_argv": ["--script", "x"], "driver_pid": 424242,
        "ended_ts": time.time(), "exit_code": 3, "error": None,
    }
    rdbg.write_record(recp, rec)
    check(os.path.isfile(recp), "记录原子落盘（%s）" % os.path.basename(recp))
    check(rdbg.read_record(slug)["exit_code"] == 3, "写进去的退出码能读回来")

    out = io.StringIO()
    rdbg.status_cli(workdir=slug, out=out)
    txt = out.getvalue()
    check(rec["run_id"] in txt, "--status 打出 run_id")
    check("退出码  3" in txt, "--status 打出真实退出码（#194 的教训：不看管道退出码）")
    check(logp in txt and "第三行" in txt, "--status 给日志路径与日志尾")
    check("driver 进程" not in txt, "--status 对已退出的运行不做存活探测（不误报在跑）")

    empty = mk_slug()
    out2 = io.StringIO()
    rdbg.status_cli(workdir=empty, out=out2)
    check("未见后台运行记录" in out2.getvalue(),
          "没有记录时明说「不是本工具起的」，不假冒状态")


# ── 4. 单实例守卫 ────────────────────────────────────────────────────
def test_guards():
    section("单实例守卫")
    root = tempfile.mkdtemp(prefix="rdbg-lock-")
    lockf = os.path.join(root, "driver_bg.lock")
    old = env_lock(lockf)
    try:
        slug = mk_slug()
        payload = rdbg.lock_payload(slug, "run-a", "/tmp/a.log", "/tmp/a.json")
        ok, action, _owner = rdbg.acquire_lock(lockf, payload)
        check(ok and action == "acquired", "空锁能原子占上（O_CREAT|O_EXCL）")

        ok2, action2, owner2 = rdbg.acquire_lock(
            lockf, rdbg.lock_payload(slug, "run-b", "/tmp/b.log", "/tmp/b.json"),
            alive_fn=lambda pid, host: True)
        check(not ok2 and action2 == "busy" and owner2["run_id"] == "run-a",
              "锁主还活着 → 第二个启动器被拒（严禁并行跑两期）")

        ok3, action3, _ = rdbg.acquire_lock(
            lockf, rdbg.lock_payload(slug, "run-c", "/tmp/c.log", "/tmp/c.json"),
            alive_fn=lambda pid, host: None)
        check(not ok3 and action3 == "busy",
              "锁主死活探不准（跨宿主）→ 拒绝，绝不擅自接管")

        rdbg.write_lock(lockf, {"run_id": "dead-run", "pid": 999999,
                                "host": ds.host_label(), "slug": slug})
        ok4, action4, owner4 = rdbg.acquire_lock(
            lockf, rdbg.lock_payload(slug, "run-d", "/tmp/d.log", "/tmp/d.json"),
            alive_fn=lambda pid, host: False)
        check(ok4 and action4 == "taken_over" and owner4["run_id"] == "dead-run",
              "僵尸锁（进程已不在）→ 接管并留下原 run_id 供告警")

        rdbg.release_own_lock("run-d")
        check(not os.path.exists(lockf), "自己的锁收尾能释放（run_id + pid 双匹配）")

        # 同 slug 守卫：心跳
        hb = {"pid": os.getpid(), "host": ds.host_label(), "stage": "s08",
              "ts": time.time()}
        jw(os.path.join(slug, ds.DRIVER_DIRNAME, ds.HEARTBEAT_NAME), hb)
        busy, why = rdbg.slug_busy(slug, alive_fn=lambda pid, host: True)
        check(busy and "heartbeat.json" in why,
              "同 slug 心跳里进程还活着 → 拒绝再起")
        jw(os.path.join(slug, ds.DRIVER_DIRNAME, ds.HEARTBEAT_NAME),
           dict(hb, exited={"code": 3, "at": "x"}))
        busy2, _ = rdbg.slug_busy(slug, alive_fn=lambda pid, host: False)
        check(not busy2, "心跳已记录退出 → 不算占用（防 pid 复用误判）")

        # 同 slug 守卫：bg 记录
        rdbg.write_record(rdbg.record_path(slug), {
            "run_id": "run-x", "pid": os.getpid(), "host": ds.host_label(),
            "status": rdbg.STATUS_RUNNING, "driver_pid": os.getpid(), "log": "",
        })
        busy3, why3 = rdbg.slug_busy(slug, alive_fn=lambda pid, host: True)
        check(busy3 and rdbg.RECORD_NAME in why3,
              "bg_run.json 记的 driver 还在跑 → 拒绝再起")
    finally:
        restore_lock(old)
        shutil.rmtree(root, ignore_errors=True)


# ── 5. 启动器验活 ────────────────────────────────────────────────────
def run_launcher(slug, script, fake_rec, extra=None, verify="0.3"):
    """跑 main()，用假 launch_detached 代替真脱离（CI 的 ubuntu 也能跑）。

    假启动器把「看守进程本该写下的」record 落盘，这样验活路径可以逐条断言。"""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    captured = {}
    orig = rdbg.launch_detached

    def fake(argv, run_id, ps_dir=None):
        captured["argv"] = list(argv)
        if fake_rec is None:
            return True
        logp = rdbg.log_path(slug, run_id)
        write_text(logp, "假日志最后一行\n")
        rec = {"run_id": run_id, "pid": os.getpid(), "host": ds.host_label(),
               "slug": slug, "log": logp, "record": rdbg.record_path(slug),
               "started_ts": time.time(), "status": fake_rec.get("status", "running"),
               "driver_argv": [], "driver_pid": fake_rec.get("driver_pid", os.getpid()),
               "ended_ts": time.time(), "exit_code": fake_rec.get("exit_code"),
               "error": fake_rec.get("error")}
        rdbg.write_record(rec["record"], rec)
        return True

    rdbg.launch_detached = fake
    argv = ["--script", script, "--workdir", slug, "--verify-sec", verify]
    argv += list(extra or [])
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            rc = rdbg.main(argv)
    finally:
        rdbg.launch_detached = orig
    return rc, buf_out.getvalue(), buf_err.getvalue(), captured


def test_launcher_paths():
    section("启动器验活路径")
    root = tempfile.mkdtemp(prefix="rdbg-launch-")
    lockf = os.path.join(root, "driver_bg.lock")
    old = env_lock(lockf)

    def case():
        """每例一个全新 slug：上一例的 running 记录会（正确地）触发同 slug 守卫，
        不换 slug 就没法测后面的路径。"""
        slug = mk_slug()
        os.makedirs(os.path.join(slug, "00_口播稿"), exist_ok=True)
        script = write_text(os.path.join(slug, "00_口播稿", "口播稿.md"), "稿")
        return slug, script

    try:
        slug, script = case()
        rc, out, err, cap = run_launcher(
            slug, script, {"status": "running"}, extra=["--platforms", "bilibili"])
        check(rc == 0 and "已后台启动 driver" in out,
              "running + pid 活着 → 启动成功（退出码 0 只代表启动）")
        check("run_id" in out and "_driver" in out, "回执给出 run_id 与稳定落点")
        argv = cap.get("argv") or []
        sep = argv.index("--") if "--" in argv else -1
        check(sep > 0 and argv[sep + 1:] ==
              ["--script", script, "--workdir", os.path.abspath(slug),
               "--platforms", "bilibili"],
              "driver 参数经 `--` 原样透传（不做 shell 引号拼装）")
        check(not os.path.exists(lockf), "启动成功后启动器不误删看守的锁、也不留自己的锁")

        slug, script = case()
        rc, _out, err, _cap = run_launcher(
            slug, script, {"status": "blocked", "error": "锁被占用"})
        check(rc == 1 and "拒绝启动" in err, "看守说 blocked → 启动器报拒绝、退出 1")

        slug, script = case()
        rc, _out, err, _cap = run_launcher(
            slug, script, {"status": "failed", "error": "起 driver 失败：boom"})
        check(rc == 1 and "启动失败" in err and "boom" in err,
              "看守说 failed → 启动器报失败原因、退出 1")

        slug, script = case()
        rc, _out, err, _cap = run_launcher(
            slug, script, {"status": "exited", "exit_code": 3})
        check(rc == 1 and "验活窗口内就退出" in err and "假日志最后一行" in err,
              "窗口内已退出 → 立刻报错并给日志尾，不假装启动成功")

        slug, script = case()
        rc, _out, err, _cap = run_launcher(slug, script, None)
        check(rc == 1 and "内没见过本次运行" in err,
              "假启动器什么也没写（#151 静默死症状）→ 报诊断、退出 1")

        slug, script = case()
        rc, _out, err, _cap = run_launcher(slug, script, None, verify="0")
        check(rc == 1, "verify-sec 0 也走同一条诊断路径（不卡死）")

        # 同 slug 守卫真的能挡住第二次启动（不能只测函数）
        slug, script = case()
        rc1, _o, _e, _c = run_launcher(slug, script, {"status": "running"})
        rc2, _o, err2, cap2 = run_launcher(slug, script, {"status": "running"})
        check(rc1 == 0 and rc2 == 1 and "同一读报单已在跑" in err2
              and "argv" not in cap2,
              "同 slug 已有活运行时，第二次启动在 launch 之前就被拒")
    finally:
        restore_lock(old)
        shutil.rmtree(root, ignore_errors=True)


# ── 6. driver_status 集成 ────────────────────────────────────────────
def test_status_integration():
    section("driver_status 认得后台记录")
    alive_false = lambda pid, host: False       # noqa: E731 — 一行桩，便于读
    alive_true = lambda pid, host: True         # noqa: E731
    slug = mk_slug()
    bg_log = write_text(os.path.join(slug, ds.DRIVER_DIRNAME, "bg-r9.log"),
                        "a\nb\nbg 最后一行\n")
    jw(os.path.join(slug, ds.DRIVER_DIRNAME, ds.BG_NAME), {
        "run_id": "r9", "host": ds.host_label(), "log": bg_log,
        "pid": os.getpid(), "driver_pid": 999998, "status": "exited",
        "exit_code": 3, "ended_ts": time.time() - 5,
    })
    rec = ds.collect(slug, alive_fn=alive_false)
    check(rec["log_path"] == bg_log, "有 bg 记录时优先用它的日志（每轮独立文件）")
    check(rec["state"] == ds.STATE_CRASHED, "无完成标记 + 进程不在 → 已崩")
    check(rec["exited"] and rec["exited"]["code"] == 3,
          "心跳没写退出码（预检阶段就退场）时，用 bg 记录的退出码补上")
    check(any(ds.BG_NAME in n for n in rec["notes"]),
          "补退出码这件事在「注」里说明来源，不冒充心跳")
    txt = ds.render(rec)
    check("后台" in txt and "退出码 3" in txt and "bg 最后一行" in txt,
          "一屏里能看到后台 run_id/状态/退出码与日志尾")

    # 心跳自己有退出记录时，以心跳为准（不被 bg 覆盖）
    slug2 = mk_slug()
    jw(os.path.join(slug2, ds.DRIVER_DIRNAME, ds.HEARTBEAT_NAME),
       {"pid": os.getpid(), "host": ds.host_label(), "ts": time.time(),
        "exited": {"code": 0, "at": "2026-10-22 12:00:00"}})
    jw(os.path.join(slug2, ds.DRIVER_DIRNAME, ds.BG_NAME),
       {"run_id": "r10", "host": ds.host_label(), "log": "",
        "status": "exited", "exit_code": 3})
    rec2 = ds.collect(slug2, alive_fn=alive_false)
    check(rec2["exited"]["code"] == 0, "两边都有退出记录时以心跳为准（bg 只补缺）")

    # running + 活进程 → 运行中；pid 取自 bg（心跳还没写）
    slug3 = mk_slug()
    bg_log3 = write_text(os.path.join(slug3, ds.DRIVER_DIRNAME, "bg-r11.log"), "x\n")
    jw(os.path.join(slug3, ds.DRIVER_DIRNAME, ds.BG_NAME), {
        "run_id": "r11", "host": ds.host_label(), "log": bg_log3,
        "pid": os.getpid(), "driver_pid": os.getpid(), "status": "running",
    })
    rec3 = ds.collect(slug3, alive_fn=alive_true)
    check(rec3["state"] == ds.STATE_RUNNING, "bg 记录 running + 进程活着 → 运行中")
    check(rec3["pid"] == os.getpid() and rec3["log_path"] == bg_log3,
          "心跳还没起时 pid / 日志都从 bg 记录回填")


    # 上一轮后台运行的记录 + 本轮新心跳（pid 不同）→ 不能拿旧记录判本轮
    slug4 = mk_slug()
    bg_log4 = write_text(os.path.join(slug4, ds.DRIVER_DIRNAME, "bg-r12.log"),
                         "上一轮的日志\n")
    jw(os.path.join(slug4, ds.DRIVER_DIRNAME, ds.BG_NAME), {
        "run_id": "r12", "host": ds.host_label(), "log": bg_log4,
        "pid": 999997, "driver_pid": 999998, "status": "exited",
        "exit_code": 3, "ended_ts": time.time() - 3600,
        "started_ts": time.time() - 7200,
    })
    jw(os.path.join(slug4, ds.DRIVER_DIRNAME, ds.HEARTBEAT_NAME), {
        "pid": os.getpid(), "host": ds.host_label(), "ts": time.time(),
        "started_ts": time.time() - 60, "stage": "s08",
    })
    rec4 = ds.collect(slug4, alive_fn=alive_true)
    check(rec4["exited"] is None,
          "心跳新鲜、pid 对不上时，不拿上一轮 bg 记录的退出码（否则误判已崩）")
    check(rec4["state"] == ds.STATE_RUNNING, "这一轮仍在跑 → 运行中")
    check(rec4["log_path"] != bg_log4, "也不拿上一轮的日志当本轮日志")
    check(any("上一轮" in n for n in rec4["notes"]),
          "跳过的旧记录在「注」里说明，不静默")


def main():
    test_telemetry_line()
    test_s13_telemetry()
    test_record_and_status()
    test_guards()
    test_launcher_paths()
    test_status_integration()
    print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")


if __name__ == "__main__":
    main()
