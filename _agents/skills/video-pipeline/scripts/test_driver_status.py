#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_driver_status.py — run_pipeline --status 只读四态自检（无框架，assert 直跑）。

票：#146 建立本文件；#166 补第 8 条（落盘窗口竞态）。

覆盖：
1. 四态判定：已完成 / 运行中 / 卡住 / 已崩，每条结论都必须带判定依据
2. report.json 的 finished_at 不是完成标记（连败/确认门/ finally 都会写它）
3. 「卡住」阈值可配：CLI > env > 默认 300，非法值 fail-fast
4. 只读契约：查询前后目录快照完全一致（不新增/不修改任何文件）
5. 一屏可读：输出含当前阶段、阶段耗时、心跳年龄、进度、日志尾
6. Heartbeat 写侧：start/enter/exit 落盘、原子替换无 .tmp 残留、守护线程推进 ts
7. 端到端：python scripts/run_pipeline.py --status 真的不建目录、不过 preflight
8. 落盘窗口竞态（#166）：读者恰在 os.replace 时持句柄 → 写侧短退避重试后仍落盘，
   读者按生产同款容错读取，--status 判定不回退「日志 mtime」口径。确定性注入，不靠压测。
"""
import builtins
import io
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import atomicio
import driver_status as ds
import run_pipeline as rp

NOW = time.time()


def jw(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def mk_slug(stamps=(), report=None, heartbeat=None, log=None, lane=None,
            name="daily-china-20261018"):
    """造一个假读报单目录。日志落在 slug 同级（new_daily 的 nohup 落点）。"""
    root = tempfile.mkdtemp(prefix="ds146_")
    slug = os.path.join(root, name)
    drv = os.path.join(slug, "_driver")
    os.makedirs(drv)
    for s in stamps:
        jw(os.path.join(drv, "stage_%s.json" % s), {"ts": NOW - 60, "stage": s})
    if report is not None:
        jw(os.path.join(drv, "report.json"), report)
    if heartbeat is not None:
        jw(os.path.join(drv, "heartbeat.json"), heartbeat)
    if lane:
        with open(os.path.join(drv, "lane.txt"), "w", encoding="utf-8") as f:
            f.write(lane)
    if log is not None:
        with open(os.path.join(root, "_driver_run.log"), "w", encoding="utf-8") as f:
            f.write(log)
    return slug


def hb(age_sec=0, **kw):
    """造心跳：ts 为 age_sec 秒前。"""
    obj = {"pid": os.getpid(), "host": ds.host_label(), "ts": NOW - age_sec,
           "at": "2026-10-18 23:00:00", "started_ts": NOW - 3600,
           "slug": "daily-china-20261018", "lane": "compressed",
           "stage": "s08", "stage_title": "H3 生成+抽帧竖屏检查（GPU，whisper 并行）",
           "stage_started_at": NOW - 900, "attempt": 1, "interval": 15,
           "exited": None}
    obj.update(kw)
    return obj


def alive_true(pid, host=None):
    return True


def alive_false(pid, host=None):
    return False


def alive_boom(pid, host=None):
    raise AssertionError("不该探活（心跳已记录退出，探活会因 pid 复用误判）")


def snapshot(root):
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = os.path.join(dirpath, name)
            st = os.stat(p)
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return out


# ── 1. 四态判定 ──────────────────────────────────────────────────────
# 已完成：s13 stamp 是唯一完成标记
slug = mk_slug(stamps=("s01", "s02", "s13"), heartbeat=hb(age_sec=7200))
rec = ds.collect(slug, now=NOW, alive_fn=alive_true)
assert rec["state"] == ds.STATE_DONE, rec["state"]
assert any("完成标记" in e for e in rec["evidence"]), rec["evidence"]
print("test_driver_status: 已完成（s13 stamp）OK")

# 运行中：进程在 + 心跳新鲜
slug = mk_slug(stamps=("s01", "s02"), heartbeat=hb(age_sec=5))
rec = ds.collect(slug, stall_sec=300, now=NOW, alive_fn=alive_true)
assert rec["state"] == ds.STATE_RUNNING, rec
assert rec["evidence"] and any("心跳" in e for e in rec["evidence"]), rec["evidence"]
assert rec["stage"] == "s08" and rec["attempt"] == 1, rec
assert rec["stage_elapsed_sec"] is not None and rec["stage_elapsed_sec"] > 800, rec
print("test_driver_status: 运行中（pid 在 + 心跳新鲜）OK")

# 卡住：进程在但心跳过期（10-17 期确认门轮询 6h20m 的形态）
slug = mk_slug(stamps=("s01", "s02"), heartbeat=hb(age_sec=22800))  # 6h20m
rec = ds.collect(slug, stall_sec=300, now=NOW, alive_fn=alive_true)
assert rec["state"] == ds.STATE_STUCK, rec["state"]
assert any("> 阈值" in e for e in rec["evidence"]), rec["evidence"]
# 阈值可配：把阈值抬到 7h，同一现场就应判「运行中」
rec2 = ds.collect(slug, stall_sec=25200, now=NOW, alive_fn=alive_true)
assert rec2["state"] == ds.STATE_RUNNING, rec2["state"]
print("test_driver_status: 卡住（pid 在 + 心跳过期）+ 阈值可调 OK")

# 已崩：进程不在 + 无完成标记；心跳里的退出码要说人话
slug = mk_slug(stamps=("s01", "s02", "s03", "s04"),
               heartbeat=hb(age_sec=1200,
                            exited={"code": 4, "reason": "确认门未通过",
                                    "at": "2026-10-18 22:00:00"}))
rec = ds.collect(slug, now=NOW, alive_fn=alive_boom)   # 退出记录在 → 不该探 pid
assert rec["state"] == ds.STATE_CRASHED, rec["state"]
joined = "；".join(rec["evidence"])
assert "无完成标记" in joined, joined
assert "确认门" in joined and "code=4" in joined, joined
print("test_driver_status: 已崩（pid 不在 + 无完成标记 + 退出码可读）OK")

# 已崩但没退出记录 → 明说「被 kill / 掉电 / 宿主重启」
slug = mk_slug(stamps=("s01",), heartbeat=hb(age_sec=3600))
rec = ds.collect(slug, now=NOW, alive_fn=alive_false)
assert rec["state"] == ds.STATE_CRASHED, rec["state"]
assert any("无退出记录" in e for e in rec["evidence"]), rec["evidence"]
print("test_driver_status: 已崩（无退出记录 = 被 kill）OK")

# 空目录不能被报成「已崩」
slug = mk_slug()
rec = ds.collect(slug, now=NOW, alive_fn=alive_false)
assert rec["state"] == ds.STATE_IDLE, rec["state"]
print("test_driver_status: 未开始（空目录不误报崩溃）OK")


# ── 2. report.json 的 finished_at 不是完成标记 ──────────────────────
# 阶段连败/确认门硬退出/run() finally 都会 flush 出 finished_at，此时并没有跑完
slug = mk_slug(stamps=("s01", "s02"),
               report={"started_at": "2026-10-18 20:00:00",
                       "finished_at": "2026-10-18 21:00:00",
                       "stages": {"s02": {"title": "TTS 音频先行", "seconds": 123.0}}},
               heartbeat=hb(age_sec=3000,
                            exited={"code": 3, "reason": "s02 连败 2 次",
                                    "at": "2026-10-18 21:00:00"}))
rec = ds.collect(slug, now=NOW, alive_fn=alive_boom)
assert rec["state"] != ds.STATE_DONE, "report.finished_at 被当成完成标记了（#146 踩坑）"
assert rec["state"] == ds.STATE_CRASHED, rec["state"]
# 正常退出但没有 s13：要提示「多半是 --stop-after 半程停止」，别一律当崩
slug = mk_slug(stamps=("s01",),
               heartbeat=hb(age_sec=600,
                            exited={"code": 0, "reason": "--stop-after s01 半程停止",
                                    "at": "2026-10-18 22:30:00"}))
rec = ds.collect(slug, now=NOW, alive_fn=alive_boom)
assert rec["state"] == ds.STATE_CRASHED, rec["state"]
assert any("半程停止" in n for n in rec["notes"]), rec["notes"]
print("test_driver_status: report.json finished_at 不作完成标记 OK")


# ── 3. 阈值解析优先级 ───────────────────────────────────────────────
assert ds.resolve_stall_sec(None, {}) == ds.DEFAULT_STALL_SEC == 300.0
assert ds.resolve_stall_sec(None, {ds.STALL_ENV: "42"}) == 42.0
assert ds.resolve_stall_sec(7, {ds.STALL_ENV: "42"}) == 7.0        # CLI 优先
for bad in ("abc", "0", "-5"):
    try:
        ds.resolve_stall_sec(bad, {})
    except SystemExit as e:
        assert "ERROR" in str(e), e
    else:
        raise AssertionError("非法阈值 %r 没有 fail-fast" % bad)
print("test_driver_status: 阈值 CLI>env>默认300 + 非法值 fail-fast OK")


# ── 4. 只读契约（AC3）───────────────────────────────────────────────
slug = mk_slug(stamps=("s01", "s02"), heartbeat=hb(age_sec=5),
               report={"started_at": "2026-10-18 22:00:00", "stages": {}},
               log="[driver 23:00:00] s08 — 开始\n[driver 23:00:01] 烧卡中\n")
root = os.path.dirname(slug)
before = snapshot(root)
buf = io.StringIO()
rc = ds.run_cli(workdir=slug, stall_after=None, env={}, out=buf)
after = snapshot(root)
assert rc == 0
assert before == after, "查询改动了文件: %s" % (
    {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
     if before.get(k) != after.get(k)})
print("test_driver_status: 只读（查询前后目录快照一致）OK")


# ── 5. 一屏可读（AC4）──────────────────────────────────────────────
text = buf.getvalue()
for want in ("状态", "依据", "阶段  s08", "本阶段已", "心跳", "阈值 5.0 min", "进度", "日志", "│ "):
    assert want in text, "输出缺 %r:\n%s" % (want, text)
assert "运行中" in text, text
print("test_driver_status: 一屏可读（阶段/耗时/心跳年龄/进度/日志尾）OK")


# ── 6. Heartbeat 写侧 ──────────────────────────────────────────────
tmpdir = tempfile.mkdtemp(prefix="ds146hb_")
h = rp.Heartbeat(tmpdir, "/tmp/some-slug", lane="compressed", interval=0.05)
assert not os.path.isfile(h.path), "构造 Heartbeat 不该落盘（dry-run/--status 会留痕）"
h.start()
beat = ds._load_json(h.path)
assert beat is not None, "首拍没落盘：%s" % h.path
assert beat["pid"] == os.getpid() and beat["host"] == ds.host_label(), beat
assert beat["exited"] is None and beat["lane"] == "compressed", beat
ts1 = beat["ts"]
time.sleep(0.3)                       # 守护线程应推进 ts
ts2 = ds._load_json(h.path)["ts"]
assert ts2 > ts1, "守护线程没推进心跳 ts（%s -> %s）" % (ts1, ts2)
h.enter("s09", "读报装配 compose", attempt=2)
beat = ds._load_json(h.path)
assert beat["stage"] == "s09" and beat["attempt"] == 2, beat
assert beat["stage_title"] == "读报装配 compose", beat
h.exit(4, "确认门未通过")
beat = ds._load_json(h.path)
assert beat["exited"]["code"] == 4 and "确认门" in beat["exited"]["reason"], beat
h.exit(4, "重复退出")                 # 幂等：不炸
assert not os.path.exists(h.path + ".tmp"), "原子写留下了 .tmp 残留"
print("test_driver_status: Heartbeat 写侧（start/enter/exit/原子替换）OK")


# ── 7. 端到端：run_pipeline.py --status ────────────────────────────
slug = mk_slug(stamps=("s01", "s02", "s03"),
               heartbeat=hb(age_sec=60, stage="s04",
                            stage_title="设计+H3 prompt（Strata 压缩车道，单次调用）"),
               lane="compressed", log="[driver 22:00:00] s04 — 开始\n")
root = os.path.dirname(slug)
before = snapshot(root)
env = dict(os.environ, PYTHONIOENCODING="utf-8")
p = subprocess.run([sys.executable, os.path.join(HERE, "run_pipeline.py"),
                    "--status", "--workdir", slug],
                   capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
after = snapshot(root)
assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
assert "状态" in p.stdout and "运行中" in p.stdout, p.stdout + p.stderr
assert before == after, "--status 在盘上留痕了（AC3 违反）"
# --stall-after 生效：阈值压到 30s，同一现场应变「卡住」
p2 = subprocess.run([sys.executable, os.path.join(HERE, "run_pipeline.py"),
                     "--status", "--workdir", slug, "--stall-after", "30"],
                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
assert p2.returncode == 0, p2.stderr
assert "卡住" in p2.stdout, p2.stdout
# 缺目标 fail-fast（不能默默跑全程）
p3 = subprocess.run([sys.executable, os.path.join(HERE, "run_pipeline.py"), "--status"],
                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
assert p3.returncode != 0 and "ERROR" in (p3.stdout + p3.stderr), p3
print("test_driver_status: 端到端 --status（只读、阈值生效、缺目标 fail-fast）OK")


# ── 8. slug 解析 ───────────────────────────────────────────────────
root = tempfile.mkdtemp(prefix="ds146slug_")
slug = os.path.join(root, "daily-china-20261018")
os.makedirs(os.path.join(slug, "00_口播稿"))
script = os.path.join(slug, "00_口播稿", "口播稿.md")
open(script, "w", encoding="utf-8").close()
assert ds.resolve_slug(script=script) == os.path.abspath(slug)
assert ds.resolve_slug(workdir=slug) == os.path.abspath(slug)
try:
    ds.resolve_slug()
except SystemExit as e:
    assert "ERROR" in str(e)
else:
    raise AssertionError("无目标时没有 fail-fast")
try:
    ds.resolve_slug(workdir=os.path.join(root, "不存在"))
except SystemExit as e:
    assert "不存在" in str(e)
else:
    raise AssertionError("目录不存在时没有 fail-fast")
print("test_driver_status: slug 解析（--script/--workdir/fail-fast）OK")


# ── 9. 探活：绝不猜 ────────────────────────────────────────────────
assert ds.pid_alive(os.getpid()) is True, "本进程应判存活"
assert ds.pid_alive(999999) in (False, None), "不存在的 pid 不该判存活"
assert ds.pid_alive(None) is None and ds.pid_alive("x") is None, "非法 pid 应返回未知"
print("test_driver_status: 探活（本进程 True / 不存在 False / 非法 None）OK")


# ── 10. 心跳残缺 / 探不准：说清不确定，不装作知道 ──────────────────
# (a) heartbeat.json 在但没有 ts：算不出年龄，必须明说，不能渲染成「没有 heartbeat.json」
slug = mk_slug(stamps=("s01",), heartbeat={"pid": os.getpid(), "host": ds.host_label(),
                                           "stage": "s02"})
rec = ds.collect(slug, alive_fn=alive_true)
assert any("没有可用的 ts" in n for n in rec["notes"]), rec["notes"]
text = ds.render(rec)
assert "没有可用的 ts 字段" in text, text
assert "没有 heartbeat.json" not in text, "文件明明在，不能说没有"
# (b) 探不准（pid 存活未知）+ 心跳新鲜 → 判运行中，但必须写明「进程存活未知」，
#     不能把「新鲜心跳」说成「进程在」（心跳最多滞后一个 interval）
slug = mk_slug(stamps=("s01",), heartbeat=hb(age_sec=5))
rec = ds.collect(slug, alive_fn=lambda pid, host=None: None)
assert rec["state"] == ds.STATE_RUNNING, rec["state"]
assert any("进程存活未知" in e for e in rec["evidence"]), rec["evidence"]
# (c) 探不准 + 心跳过期 → 卡住，并提示去宿主上 ps 确认
slug = mk_slug(stamps=("s01",), heartbeat=hb(age_sec=3600))
rec = ds.collect(slug, stall_sec=300, alive_fn=lambda pid, host=None: None)
assert rec["state"] == ds.STATE_STUCK, rec["state"]
assert any("跨宿主无法探活" in n for n in rec["notes"]), rec["notes"]
# (d) 旧运行无 heartbeat.json：心跳年龄回退日志 mtime，并注明回退口径
slug = mk_slug(stamps=("s01",), log="[driver 22:00:00] s02 — 开始\n")
rec = ds.collect(slug, alive_fn=lambda pid, host=None: None)
assert rec["heartbeat_source"] == "日志 mtime", rec["heartbeat_source"]
assert any("回退用日志 mtime" in n for n in rec["notes"]), rec["notes"]
assert "日志 mtime" in ds.render(rec)
# (e) scan_pids 在非 POSIX 进程树上不瞎猜：给空 proc_root 必须返回空表
assert ds.scan_pids(slug, proc_root="/nonexistent_proc_root") == []
# (f) 全部 stamp 都在时不该显示「无阶段记录」：收尾阶段要摆出来
slug = mk_slug(stamps=tuple("s%02d" % i for i in range(1, 14)))
rec = ds.collect(slug, alive_fn=alive_false)
assert rec["state"] == ds.STATE_DONE, rec["state"]
assert rec["stage"] == ds.COMPLETE_STAGE, rec["stage"]
text = ds.render(rec)
assert "无阶段记录" not in text and "收尾阶段（已完成）" in text, text
# (g) stamp 的 ts 缺失时，完成时刻退回文件 mtime，不打印「（?）」
assert "（?" not in text, text
print("test_driver_status: 心跳残缺/探不准（缺 ts、存活未知、日志回退、空进程表）OK")


# ── 11. 落盘窗口竞态（#166）─────────────────────────────────────────
# 现象：守护线程整文件重写 heartbeat.json 的那一瞬，并发读者与 os.replace 在
# Windows 上互斥（ERROR_SHARING_VIOLATION）→ PermissionError [Errno 13]：写侧
# 原实现一次就放弃（只留日志，心跳从此停更），读者裸 open 直接抛。全量跑偶发
# 假红即此（#162 收尾撞 1 次，单跑/并行都复现不了）。本节不靠压测抢时序，直接
# 注入「读者恰在窗口内持有句柄」——修复前必红，修复后必绿。
_REAL_REPLACE = os.replace


def replace_fails(times):
    """前 times 次 replace 抛 PermissionError（窗口内读者持句柄的形状）。"""
    calls = {"n": 0}

    def fake(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] <= times:
            raise PermissionError(13, "Permission denied", str(dst))
        return _REAL_REPLACE(src, dst, *a, **kw)

    return fake, calls


def with_replace(fake, fn):
    os.replace = fake
    try:
        return fn()
    finally:
        os.replace = _REAL_REPLACE


# (a) 写侧短退避重试：前两次撞上互斥，第三次必须落盘（不能一次就放弃）
h = rp.Heartbeat(tempfile.mkdtemp(prefix="ds166w_"), "/tmp/some-slug", interval=15)
h.started_ts = time.time()
fake, calls = replace_fails(2)
with_replace(fake, h._write)
beat = ds._load_json(h.path)
assert beat is not None, "两次瞬时互斥后写侧放弃了：心跳没落盘（#166 原样）"
assert calls["n"] == 3, "应重试到第 3 次成功，实际 replace %d 次" % calls["n"]
assert not os.path.exists(h.path + ".tmp"), "换盘成功后不该留 .tmp 残留"

# (b) 重试有上限、耗尽也不炸穿 driver，且不动旧心跳（读者永远读得到完整 JSON）
# 这条路径会真打一行「[heartbeat] 写入失败」——本意就是断言它被记下来（不静默），
# 所以临时接管 print，既拿到证据又不污染跑器输出。
logs = []
_real_print = builtins.print


def capture(*a, **kw):
    logs.append(" ".join(str(x) for x in a))


fake, calls = replace_fails(10 ** 6)
builtins.print = capture          # run_pipeline.log() 走的就是内建 print
try:
    with_replace(fake, h._write)      # 不许抛
finally:
    builtins.print = _real_print
assert calls["n"] == len(atomicio.RETRY_DELAYS) + 1, \
    "重试次数必须恰好等于预算 +1（不是死循环也不是一次就放弃）：%d" % calls["n"]
assert any("写入失败" in line for line in logs), \
    "重试耗尽必须留痕（不能静默），实际日志：%r" % (logs,)
assert ds._load_json(h.path) == beat, "放弃时改坏了旧心跳文件"
assert not os.path.exists(h.path + ".tmp"), "重试耗尽后要收拾 .tmp 现场"

# (c) 读者同容错（AC3）：窗口内撞一次互斥，collect() 不许退回「日志 mtime」口径
slug = mk_slug(stamps=("s01",), heartbeat=hb(age_sec=5),
               log="[driver 22:00:00] s02 — 开始\n")
_real_open = builtins.open
busy = {"n": 0}


def flaky_open(file, *a, **kw):
    if isinstance(file, str) and os.path.basename(file) == "heartbeat.json":
        busy["n"] += 1
        if busy["n"] == 1:
            raise PermissionError(13, "Permission denied", file)
    return _real_open(file, *a, **kw)


builtins.open = flaky_open
try:
    rec = ds.collect(slug, alive_fn=alive_true)
finally:
    builtins.open = _real_open
assert busy["n"] >= 2, "读者没重试：窗口内一次互斥就退回日志 mtime 了"
assert rec["heartbeat_source"] == ds.HEARTBEAT_NAME, rec["heartbeat_source"]
assert not any("回退用日志 mtime" in n for n in rec["notes"]), rec["notes"]
print("test_driver_status: 落盘窗口竞态（写侧重试 / 有上限 / 读者不退回日志口径）OK")


print("test_driver_status: 全部通过（四态 + 依据 + 阈值 + 只读 + 渲染 + 写侧 + 端到端 + 残缺 + 竞态）")
