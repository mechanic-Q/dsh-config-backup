#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_driver_bg.py — 一条命令把 driver 脱到后台跑，日志与退出码可追（#195）。

## 为什么需要它

run_pipeline.py 是前台长跑（一单 1-2 小时，确认门最长 8 小时）。原来后台启动靠
`nohup setsid ... &`（Windows 侧不成立）或 WMI 裸启动，两种都留下碎片：日志落点
不固定、启动完就丢了 pid、**真实退出码无从查起**（#194 实测 `| tail -3` 把退出码
吞了）。本工具把「启动」与「查询」分开，各管一件事：

  启动  python scripts/run_driver_bg.py --script <slug>/00_口播稿/口播稿.md [--platforms bilibili,douyin]
  查询  python scripts/run_driver_bg.py --status --workdir <slug>
        （也可沿用 python scripts/run_pipeline.py --status …，它已认得后台记录）

driver 自己的参数原样透传：不认识的参数一律转给 run_pipeline.py，所以
`--platforms` / `--lane` / `--gate-timeout 28800` / `--gate-approved` 都照旧能用。

## 落点（都在 <slug>/_driver/ 下，稳定、可追）

  bg-<run_id>.log   该次运行的完整 stdout/stderr（每次启动独立文件，不互相覆盖）
  bg_run.json       该次运行的记录：run_id / 看守 pid / driver pid / 状态 / 退出码 / 起止
  heartbeat.json    driver 自己写的心跳（原有），与 bg_run.json 交叉印证

## 退出码语义（重要：别把它当成 driver 的退出码）

  本命令的退出码只表示「这次启动成没成」：0 已起来 / 1 守卫拒绝或启动失败 / 2 用法错。
  driver 的真实退出码（0/3/4）去 bg_run.json 或 --status 查——所以**不要**把本命令
  的输出接到 `| tail` / `| head` 上判结果（#194 的教训：管道会吞退出码）。

## 单实例守卫（AC4，「严禁并行跑两期」）

s13 收尾的 shutdown_comfyui() 会清卡，杀掉另一期正在烧段的 ComfyUI（2026-10-19
事故）。本工具两层拦：

  1. 同 slug：_driver/heartbeat.json 或 bg_run.json 记的进程还活着 → 拒绝再起
  2. 机器级：~/.video-pipeline/driver_bg.lock（DRIVER_BG_LOCK 可覆盖）原子占锁；
     别的读报单已在跑 → 拒绝；锁主进程已死 → 接管并告警；跨宿主探不准 → 拒绝

**明确文档化**：锁只覆盖「经本工具启动」的运行。手工前台 `python scripts/run_pipeline.py`
不持锁，也不会被这里拦住——跨期并行的纪律仍靠入口自查；本工具不提供
--allow-parallel，也不打算有。停掉一单请走正常退出，别手删锁文件（僵尸锁会自动接管）。

## 宿主

Windows：自建 WMI（Win32_Process.Create）脱离启动，子进程属 WMI 服务，活过会话退出
（与 comfyui_selfheal.launch_detached 同一机制；但那份实现写死单一路径的 ps1，两次
启动会互相覆盖，故这里每次启动写一个一次性 ps1）。POSIX：Popen(start_new_session)。

为什么不直接 `Popen(DETACHED_PROCESS)`：2026-10-19 #151 实测后台 spawn 偶发日志
0 字节、进程静默死；WMI 是唯一在 09-28 A/B 里活过会话退出的路子。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import atomicio  # noqa: E402 — 先把脚本目录放进 sys.path（同仓裸脚本惯例）
import driver_status  # noqa: E402

RUN_PIPELINE = os.path.join(HERE, "run_pipeline.py")
RECORD_NAME = "bg_run.json"
LOG_PREFIX = "bg-"
VERIFY_SEC = 3.0          # 启动后验活窗口（handoff 20261022 建议 3s）
POLL_SEC = 0.1
LOCK_ENV = "DRIVER_BG_LOCK"
# 子进程输出强制 UTF-8 + 不缓冲：Windows 控制台 cp936 会炸，日志要能实时追
CHILD_ENV = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}

STATUS_RUNNING = "running"
STATUS_EXITED = "exited"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"


# ── 小工具 ────────────────────────────────────────────────────────────
def now():
    return time.time()


def stamp_at(ts):
    """本地时间戳；非法/缺失给「?」，绝不抛。"""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "?"


def make_run_id():
    """run_id：秒级时间 + 本进程 pid。同一秒连启两次也不撞名。"""
    return "%s-p%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())


# ── 落点 ──────────────────────────────────────────────────────────────
def driver_dir(slug):
    return os.path.join(os.path.abspath(slug), driver_status.DRIVER_DIRNAME)


def log_path(slug, run_id):
    return os.path.join(driver_dir(slug), LOG_PREFIX + run_id + ".log")


def record_path(slug):
    return os.path.join(driver_dir(slug), RECORD_NAME)


def write_record(path, rec):
    """原子写 bg_run.json（复用 atomicio 的 Windows 换盘退避，读写两侧同款容错）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
        f.write("\n")
    atomicio.replace_with_retry(tmp, path)


def read_record(slug):
    return atomicio.load_json(record_path(slug))


# ── 机器级单实例锁 ────────────────────────────────────────────────────
def lock_path():
    """默认落用户主目录。不写死「日期根」——那会撞 check_mechanical 规则 4 的
    跨侧路径字面量（同一个默认值在 run_pipeline 里已有唯一来源）。"""
    env = os.environ.get(LOCK_ENV)
    if env:
        return os.path.abspath(env)
    return os.path.join(os.path.expanduser("~"), ".video-pipeline", "driver_bg.lock")


def read_lock(path=None):
    path = lock_path() if path is None else path
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def lock_state(lock, alive_fn=None):
    """锁主的死活：alive / dead / unknown（跨宿主探不准就是 unknown，绝不擅自接管）。"""
    if not isinstance(lock, dict) or lock.get("pid") is None:
        return "dead"
    try:
        pid = int(lock["pid"])
    except (TypeError, ValueError):
        return "dead"
    alive = (alive_fn or driver_status.pid_alive)(pid, lock.get("host"))
    if alive is True:
        return "alive"
    if alive is None:
        return "unknown"
    return "dead"


def write_lock(path, payload):
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.write("\n")
    atomicio.replace_with_retry(tmp, path)


def acquire_lock(path, payload, alive_fn=None):
    """原子占锁。返回 (ok, action, owner)。

    action：acquired（新占）/ taken_over（接管僵尸锁）/ busy（拒绝，owner 是占用者）。
    O_CREAT|O_EXCL 保证两个启动器不会同时占上；只有锁主确认已死才允许接手。
    """
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for _attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            owner = read_lock(path)
            if lock_state(owner, alive_fn) != "dead":
                return False, "busy", owner
            # 僵尸锁：删了再原子抢（两步间仍有理论竞态；最终把关是同 slug 探活）
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
        return True, ("acquired" if _attempt == 1 else "taken_over"), \
            (None if _attempt == 1 else owner)
    return False, "busy", read_lock(path)


def release_own_lock(run_id):
    """只释放「自己这个 run_id 且 pid 还是自己」的锁。

    成功启动后锁已由看守进程接管（pid 改写），启动器退出时不能把它的锁删了。"""
    path = lock_path()
    owner = read_lock(path)
    if isinstance(owner, dict) and owner.get("run_id") == run_id \
            and owner.get("pid") == os.getpid():
        try:
            os.unlink(path)
        except OSError:
            pass


def adopt_lock(run_id):
    """看守进程接手启动器占的锁：把 pid 改成自己的，天塌了锁主也还是活进程。"""
    path = lock_path()
    owner = read_lock(path)
    if not isinstance(owner, dict) or owner.get("run_id") != run_id:
        return False
    owner.update({"pid": os.getpid(), "role": "supervisor"})
    try:
        write_lock(path, owner)
    except OSError:
        return False
    return True


def lock_payload(slug, run_id, logp, recp):
    return {"run_id": run_id, "pid": os.getpid(), "host": driver_status.host_label(),
            "slug": os.path.abspath(slug), "log": logp, "record": recp,
            "role": "launcher", "started_ts": now()}


# ── 守卫 ──────────────────────────────────────────────────────────────
def slug_busy(slug, alive_fn=None):
    """同 slug 是否已有活着的 driver。返回 (busy, 说明)。"""
    alive_fn = alive_fn or driver_status.pid_alive
    drv = driver_dir(slug)
    hb = atomicio.load_json(os.path.join(drv, driver_status.HEARTBEAT_NAME))
    if isinstance(hb, dict) and hb.get("pid") and not hb.get("exited"):
        try:
            pid = int(hb["pid"])
        except (TypeError, ValueError):
            pid = None
        if pid is not None and alive_fn(pid, hb.get("host")) is True:
            return True, ("%s/heartbeat.json 的 driver pid %d 还活着（阶段 %s）"
                          % (driver_status.DRIVER_DIRNAME, pid, hb.get("stage") or "?"))
    bg = read_record(slug)
    if isinstance(bg, dict) and bg.get("status") == STATUS_RUNNING:
        pid = bg.get("driver_pid") or bg.get("pid")
        try:
            pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid = None
        if pid is not None and alive_fn(pid, bg.get("host")) is True:
            return True, ("%s 记的 driver pid %d 还在跑（run_id %s）"
                          % (RECORD_NAME, pid, bg.get("run_id") or "?"))
    return False, ""


def check_guards(slug, alive_fn=None):
    """除机器级锁之外的同步守卫（锁在下面 acquire_lock 里原子判）。"""
    busy, why = slug_busy(slug, alive_fn)
    if busy:
        return False, "同一读报单已在跑，拒绝再起：" + why
    owner = read_lock()
    if owner is not None:
        state = lock_state(owner, alive_fn)
        if state == "alive":
            return False, ("机器级锁被占用（run_id %s · 读报单 %s · pid %s）"
                           % (owner.get("run_id") or "?", owner.get("slug") or "?",
                              owner.get("pid")))
        if state == "unknown":
            return False, ("机器级锁主在宿主 %s 上，探不准死活（pid %s）——确认已停后"
                           "手工删 %s 再来" % (owner.get("host") or "?", owner.get("pid"),
                                              lock_path()))
    return True, ""


# ── 脱离启动 ──────────────────────────────────────────────────────────
def _powershell():
    return os.environ.get("DRIVER_BG_POWERSHELL") or shutil.which("powershell") \
        or "powershell.exe"


def launch_detached(supervise_argv, run_id, ps_dir=None):
    """把看守进程脱到会话之外。返回 True/False（只代表「命令发出去了」）。

    Windows：一次性 ps1（落 temp、ASCII 文件名）+ WMI Win32_Process.Create。
    POSIX：Popen(start_new_session=True)。
    测试可替换本函数（见 test_run_driver_bg.py）——不必真的脱离会话。"""
    if os.name == "nt":
        cmd_line = subprocess.list2cmdline(list(supervise_argv))
        ps_path = os.path.join(ps_dir or tempfile.gettempdir(),
                               "driver_bg_%s.ps1" % run_id)
        # 单引号字符串里唯一要转义的是单引号本身（翻倍）；中文/反斜杠都原样
        ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
              "-Arguments @{CommandLine='%s'}; "
              "Write-Output ('WMI rc=' + $r.ReturnValue + ' pid=' + $r.ProcessId)"
              % cmd_line.replace("'", "''"))
        with open(ps_path, "w", encoding="utf-8-sig") as f:
            f.write(ps)
        try:
            p = subprocess.run([_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-File", ps_path],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60)
            ok = p.returncode == 0 and "WMI rc=0" in (p.stdout or "")
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            try:
                os.unlink(ps_path)          # 一次性脚本，发完就收（失败留着便于查）
            except OSError:
                pass
        return ok
    try:
        subprocess.Popen(list(supervise_argv), cwd=ROOT, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return False
    return True


def supervise_argv(run_id, slug, driver_args):
    """看守进程的 argv（`--` 之后原样透传，不做 shell 引号拼装）。"""
    return [sys.executable, os.path.abspath(__file__),
            "--supervise", "--run-id", run_id, "--slug", slug,
            "--"] + list(driver_args)


def wait_started(slug, run_id, verify_sec=None):
    """等看守落下本次记录。返回 (rec, note)；rec 为 None 表示没等到。"""
    verify_sec = VERIFY_SEC if verify_sec is None else max(0.0, float(verify_sec))
    deadline = time.time() + verify_sec
    while True:
        rec = read_record(slug)
        if isinstance(rec, dict) and rec.get("run_id") == run_id:
            st = rec.get("status")
            if st in (STATUS_EXITED, STATUS_BLOCKED, STATUS_FAILED):
                return rec, ""
            if st == STATUS_RUNNING and rec.get("driver_pid"):
                return rec, ""
        if time.time() >= deadline:
            return None, ("启动后 %.1fs 内没见过本次运行（run_id %s）的记录 %s"
                          % (verify_sec, run_id, record_path(slug)))
        time.sleep(POLL_SEC)


# ── 看守进程（脱离会话后跑的就是这段） ────────────────────────────────
def supervise(run_id, slug, recp, logp, driver_args):
    """占锁 → 起 driver → 等退 → 落退出码 → 释放锁。返回 driver 的退出码。

    这里没有控制台（Windows 由 WMI 拉起）：所有可见输出写进日志文件本身，
    绝不用 print——print 到无效 stdout 会直接抛。"""
    os.makedirs(os.path.dirname(logp), exist_ok=True)
    rec = {
        "run_id": run_id, "pid": os.getpid(), "host": driver_status.host_label(),
        "slug": os.path.abspath(slug), "log": logp, "record": recp,
        "started_ts": now(), "status": STATUS_RUNNING,
        "driver_argv": list(driver_args), "driver_pid": None,
        "ended_ts": None, "exit_code": None, "error": None,
    }
    adopted = adopt_lock(run_id)
    env = dict(os.environ)
    env.update(CHILD_ENV)
    env["DRIVER_BG_RUN_ID"] = run_id
    cmd = [sys.executable, RUN_PIPELINE] + list(driver_args)
    rc = 1
    try:
        with open(logp, "a", encoding="utf-8", errors="replace", buffering=1) as lf:
            lf.write("[bg %s] run_id=%s host=%s owner_pid=%d lock=%s\n"
                     % (stamp_at(now()), run_id, rec["host"], os.getpid(),
                        "已接管" if adopted else "未匹配（继续，并行风险请自查）"))
            lf.write("[bg %s] cwd=%s\n" % (stamp_at(now()), ROOT))
            lf.write("[bg %s] cmd=%s\n" % (stamp_at(now()), subprocess.list2cmdline(cmd)))
            write_record(recp, rec)
            try:
                proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                        stdout=lf, stderr=subprocess.STDOUT)
            except OSError as exc:
                rec.update(status=STATUS_FAILED, error="起 driver 失败：%s" % exc,
                           ended_ts=now())
                write_record(recp, rec)
                lf.write("[bg %s] 起 driver 失败：%s\n" % (stamp_at(now()), exc))
                return 1
            rec["driver_pid"] = proc.pid
            write_record(recp, rec)
            rc = proc.wait()
        rec.update(status=STATUS_EXITED, exit_code=int(rc), ended_ts=now())
        write_record(recp, rec)
        return rc
    finally:
        release_own_lock(run_id)


# ── 回执 / 查询 ───────────────────────────────────────────────────────
def report_started(slug, rec, logp, run_id):
    print("已后台启动 driver（脱离本终端，会话退出不影响）")
    print("  run_id  %s" % run_id)
    print("  读报单  %s" % os.path.abspath(slug))
    print("  日志    %s" % logp)
    print("  记录    %s" % record_path(slug))
    if rec.get("driver_pid"):
        print("  pid     driver %s（看守 %s）" % (rec.get("driver_pid"), rec.get("pid")))
    print("  查询    python scripts/run_driver_bg.py --status --workdir \"%s\"" % slug)
    print("  提醒    本命令退出码只代表启动成功；driver 的真实退出码看上一条「查询」或 %s"
          % RECORD_NAME)


def status_cli(script=None, workdir=None, out=None):
    """查这次后台运行的存活/退出码（只读）。"""
    out = sys.stdout if out is None else out
    slug = driver_status.resolve_slug(script=script, workdir=workdir)
    rec = read_record(slug)
    if not isinstance(rec, dict):
        print("未见后台运行记录（%s）——这一单不是 run_driver_bg 起的，或还没起过。\n"
              "心跳/日志口径请用：python scripts/run_pipeline.py --status --workdir \"%s\""
              % (record_path(slug), slug), file=out)
        return 0
    pid = rec.get("driver_pid") or rec.get("pid")
    alive = None
    if pid is not None and rec.get("status") == STATUS_RUNNING:
        alive = driver_status.pid_alive(int(pid), rec.get("host"))
    print("后台运行 %s  [%s]" % (rec.get("run_id"), os.path.abspath(slug)), file=out)
    state_txt = rec.get("status") or "?"
    if alive is not None:
        state_txt += "（driver 进程%s）" % ("仍在" if alive else "已不在")
    print("  状态    %s" % state_txt, file=out)
    if rec.get("exit_code") is not None:
        print("  退出码  %s" % rec["exit_code"], file=out)
    print("  起止    %s → %s"
          % (stamp_at(rec.get("started_ts")),
             stamp_at(rec.get("ended_ts")) if rec.get("ended_ts") else "（未结束）"), file=out)
    if rec.get("pid") is not None or rec.get("driver_pid") is not None:
        print("  pid     看守 %s · driver %s"
              % (rec.get("pid"), rec.get("driver_pid")), file=out)
    if rec.get("error"):
        print("  原因    %s" % rec["error"], file=out)
    print("  日志    %s" % rec.get("log"), file=out)
    for ln in driver_status.read_log_tail(rec.get("log"), 5):
        print("          │ " + ln[:200], file=out)
    if rec.get("status") == STATUS_RUNNING and alive is False:
        print("  注      记录仍是 running 但进程已不在——看守被杀/掉电，"
              "driver 退出前没来得及写回记录（退出码未知）", file=out)
    owner = read_lock()
    if isinstance(owner, dict) and owner.get("run_id") and \
            owner.get("run_id") != rec.get("run_id"):
        print("  锁      当前锁属于 run_id %s（%s · pid %s）——另有运行，严禁并行"
              % (owner.get("run_id"), owner.get("host") or "?", owner.get("pid")), file=out)
        if lock_state(owner) == "unknown":
            print("          跨宿主探不准死活；确认已停再手工删锁 %s" % lock_path(), file=out)
    print("  详细    python scripts/run_pipeline.py --status --workdir \"%s\"" % slug, file=out)
    return 0


# ── 入口 ──────────────────────────────────────────────────────────────
def split_at_sep(argv):
    """`--` 之后原样交给 run_pipeline（argv 直调，不做 shell 引号拼装）。"""
    if "--" in argv:
        i = argv.index("--")
        return list(argv[:i]), list(argv[i + 1:]), True
    return list(argv), [], False


def build_parser():
    ap = argparse.ArgumentParser(
        prog="run_driver_bg.py",
        description="后台启动 driver（脱离终端）+ 查这次运行的退出码（#195）")
    ap.add_argument("--script", default=None,
                    help="口播稿路径（启动必填；--status 时可改用 --workdir）")
    ap.add_argument("--workdir", default=None, help="读报单目录（默认由 --script 推导）")
    ap.add_argument("--status", action="store_true",
                    help="只读查询本次后台运行的存活/退出码")
    ap.add_argument("--verify-sec", type=float, default=VERIFY_SEC, metavar="SEC",
                    help="启动后验活窗口秒数（默认 %(default)s）")
    ap.add_argument("--supervise", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--slug", default=None, help=argparse.SUPPRESS)
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    head, tail, has_sep = split_at_sep(argv)
    a, unknown = build_parser().parse_known_args(head)
    driver_args = tail if has_sep else unknown

    if a.supervise:
        if not a.run_id or not a.slug:
            print("ERROR: --supervise 需要 --run-id/--slug（内部用法）", file=sys.stderr)
            return 2
        slug = os.path.abspath(a.slug)
        return supervise(a.run_id, slug, record_path(slug), log_path(slug, a.run_id),
                         driver_args)

    if a.status:
        return status_cli(script=a.script, workdir=a.workdir)

    if not a.script:
        print("ERROR: 缺 --script；只查状态用 --status --workdir <slug>", file=sys.stderr)
        return 2
    script = os.path.abspath(a.script)
    if not os.path.isfile(script):
        print("ERROR: 口播稿不存在: %s" % script, file=sys.stderr)
        return 2
    slug = driver_status.resolve_slug(script=script, workdir=a.workdir)

    ok, why = check_guards(slug)
    if not ok:
        print("拒绝启动：%s" % why, file=sys.stderr)
        print("（严禁并行跑两期：s13 收尾 shutdown_comfyui 会清卡，杀掉另一期正在烧段的"
              "ComfyUI）", file=sys.stderr)
        return 1

    run_id = make_run_id()
    logp = log_path(slug, run_id)
    recp = record_path(slug)
    ok, action, owner = acquire_lock(lock_path(),
                                     lock_payload(slug, run_id, logp, recp))
    if not ok:
        print("拒绝启动：机器级锁被占用（run_id %s · 读报单 %s）——严禁并行跑两期"
              % ((owner or {}).get("run_id") or "?", (owner or {}).get("slug") or "?"),
              file=sys.stderr)
        return 1
    if action == "taken_over":
        print("告警：接管了一把僵尸锁（原 run_id %s 的进程已不在）"
              % ((owner or {}).get("run_id") or "?"), file=sys.stderr)

    full_driver = ["--script", script]
    if a.workdir:
        full_driver += ["--workdir", os.path.abspath(a.workdir)]
    full_driver += driver_args

    try:
        if not launch_detached(supervise_argv(run_id, slug, full_driver), run_id):
            print("脱离启动失败（WMI/Popen 没把看守拉起来）——driver 未起，锁已释放",
                  file=sys.stderr)
            return 1
        rec, note = wait_started(slug, run_id, a.verify_sec)
        if rec is None:
            print("已发出启动命令，但%s" % note, file=sys.stderr)
            print("  日志  %s" % logp, file=sys.stderr)
            print("（#151 那类「后台 spawn 静默死」就是这个症状；必要时改前台跑）",
                  file=sys.stderr)
            return 1
        st = rec.get("status")
        if st == STATUS_BLOCKED:
            print("拒绝启动：%s" % (rec.get("error") or "机器级锁被占用"), file=sys.stderr)
            return 1
        if st == STATUS_FAILED:
            print("启动失败：%s" % (rec.get("error") or "看守进程起 driver 失败"),
                  file=sys.stderr)
            print("  日志  %s" % logp, file=sys.stderr)
            return 1
        if st == STATUS_EXITED:
            print("driver 在验活窗口内就退出了（exit=%s）——这次启动没成"
                  % rec.get("exit_code"), file=sys.stderr)
            print("  日志  %s" % logp, file=sys.stderr)
            for ln in driver_status.read_log_tail(logp, 5):
                print("        │ " + ln[:200], file=sys.stderr)
            return 1
        pid = rec.get("driver_pid")
        if pid is not None and driver_status.pid_alive(int(pid), rec.get("host")) is not True:
            print("记录说在跑，但 driver pid %s 探不到（可能刚崩）——请看日志：%s"
                  % (pid, logp), file=sys.stderr)
            return 1
        report_started(slug, rec, logp, run_id)
        return 0
    finally:
        # 成功时锁已由看守进程接管（pid 已改写），这里不会误删；失败路径把锁还回去
        release_own_lock(run_id)


if __name__ == "__main__":
    sys.exit(main())
