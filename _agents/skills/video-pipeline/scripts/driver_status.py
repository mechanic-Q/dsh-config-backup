#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""driver_status.py — run_pipeline --status 的只读状态判定（#146）。

一屏回答一个问题：这个读报单现在是在跑、卡住、崩了，还是已经完成？

四态判定（每条结论都附判定依据，不只给结论）：

  已完成  _driver/stage_s13.json 存在 —— 发布阶段 stamp 即完成标记
  运行中  进程存活 且 心跳年龄 <= 阈值
  卡住    进程存活 且 心跳年龄 >  阈值（进程在，但不再前进）
  已崩    进程不在 且 无完成标记（心跳里的 exited 记录给出上次退出码/原因）

另有一个显式空态「未开始」：目录里既无 stamps 也无 report/heartbeat/日志。
空目录必须与「已崩」区分——否则建单后还没起跑就被报成崩了。

为什么**不**用 report.json 的 finished_at 当完成标记：
ledger.flush() 在阶段连败退出（run_stage）、确认门硬退出（_flush_exit）和
run() 的 finally 里都会写 finished_at，它与「跑完」毫无关系。唯一可信的完成
标记是 s13 的 stamp。

心跳来源（两路，前者优先）：
  1. _driver/heartbeat.json —— driver 侧 Heartbeat 守护线程每 DRIVER_HEARTBEAT_SEC
     （默认 15s）原子重写；字段 pid/host/ts/stage/stage_title/stage_started_at/
     attempt/started_ts/lane，退出时补 exited{code,reason,at}
  2. _driver_run.log 的 mtime —— 旧版运行没有 heartbeat.json 时的回退口径

进程存活按宿主分流（宿主 id 一律取自 host_contract，本文件不自造口径）：
  POSIX   os.kill(pid, 0) —— 不发信号，纯探活
  Windows ctypes OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetExitCodeProcess
          —— **Windows 上绝不能 os.kill(pid, 0)**，那会真的把进程终止掉
  心跳记录的宿主与本机不同时经该宿主的 shell 探活；探不准就返回「未知」，绝不猜。

只读契约（#146 AC3）：本模块只 open(..., "r") / stat / listdir / 探活，
不写任何文件、不发通知、不重启进程。改文件的能力只存在于 driver 侧
（run_pipeline.Heartbeat），与查询侧严格分开。
"""
import os
import subprocess
import sys
import time

import atomicio
import host_contract
import hostpath

DEFAULT_STALL_SEC = 300.0     # 「卡住」阈值默认值（AC2；文档见 SKILL.md driver 用法）
STALL_ENV = "DRIVER_STATUS_STALL_SEC"

DRIVER_DIRNAME = "_driver"
HEARTBEAT_NAME = "heartbeat.json"
REPORT_NAME = "report.json"
LOG_NAME = "_driver_run.log"
BG_NAME = "bg_run.json"        # run_driver_bg.py 的后台运行记录（#195）


def _stage_ids():
    """阶段 id 不在这里另抄一份（维护规则 2：阶段 id 必须与 run_pipeline 的
    _stage_list 一致，改阶段先改 driver）——统一读宿主契约声明的阶段表。
    契约缺失/损坏时退回 s01-s13 的兜底，但只在「只是查个状态」的场合用，
    不掩盖契约问题（契约损坏本身由 test_host_contract.py 抓）。"""
    try:
        ids = tuple(host_contract.stage_ids())
    except Exception:  # noqa: BLE001 — 契约读不到不该让状态查询整体失败
        ids = tuple("s%02d" % i for i in range(1, 14))
    return ids or tuple("s%02d" % i for i in range(1, 14))


STAGE_ORDER = _stage_ids()
COMPLETE_STAGE = STAGE_ORDER[-1] if STAGE_ORDER else "s13"

STATE_RUNNING = "运行中"
STATE_STUCK = "卡住"
STATE_CRASHED = "已崩"
STATE_DONE = "已完成"
STATE_IDLE = "未开始"
STATES = (STATE_RUNNING, STATE_STUCK, STATE_CRASHED, STATE_DONE, STATE_IDLE)

EXIT_REASONS = {
    0: "正常退出（未写完成标记，多半是 --stop-after 半程停止或手工中断）",
    3: "阶段连败 2 次退出（见日志叫人消息）",
    4: "确认门未通过（超时/修改意见）退出",
}


# ── 宿主与进程存活 ────────────────────────────────────────────────────
def host_label():
    """当前宿主 id：一行委托 hostpath.detect_host（#169 收口，#143 同款姿势）。

    heartbeat.json 的 host 字段与 run_pipeline 的 self.host 都取这里；id 由
    hostpath 统一给出，本就落在 host_contract.hosts() 声明的词表内（词表读
    不到时退化到同一对内置 id），不再自判、也不留 "unknown" 第三态。
    """
    return hostpath.detect_host()


def pid_alive(pid, host=None, remote_timeout=5):
    """pid 是否存活。True/False/None（None=查不准，绝不猜）。

    host 为心跳记录的宿主；与本机不同则走跨宿主探活。

    本地实现按真实 OS（os.name）分派而不是按宿主 id：探活要回答的是
    「本进程能用哪套 API」，host_label() 回答的是「本机在契约里叫什么」——
    #169 收口后裸 Linux 上判定给 windows（三条 WSL 证据全假，语义正确），
    但那台机器上能用的仍是 POSIX 探活（CI 裸 runner 实测抓出）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    local = host_label()
    if host and host != local:
        return _remote_alive(pid, host, remote_timeout)
    if os.name == "nt":
        return _win_alive(pid)
    return _posix_alive(pid)


def _posix_alive(pid):
    try:
        os.kill(pid, 0)          # 信号 0 = 不投递，只做权限/存在性检查
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # 进程在，但不是本用户的
    except OSError:
        return False
    return True


def _win_alive(pid):
    """Windows 探活：OpenProcess + GetExitCodeProcess。

    绝不用 os.kill(pid, 0)：Windows 上 os.kill 走 TerminateProcess，
    探活会直接杀掉 driver（#146 踩坑）。"""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 — 查不准就返回未知，不猜
        return None


def _remote_alive(pid, host, timeout=5):
    """跨宿主探活：心跳宿主 wsl、本机 windows 时经 wsl 执行 kill -0。"""
    if host == "wsl" and host_label() == "windows":
        try:
            p = subprocess.run(["wsl", "-e", "sh", "-c", "kill -0 %d" % int(pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=timeout)
            return p.returncode == 0
        except Exception:  # noqa: BLE001 — 没有 wsl / 超时：未知
            return None
    return None


def scan_pids(slug, proc_root="/proc"):
    """进程表扫描（POSIX only）：找 cmdline 里带 run_pipeline.py 且提到本 slug 的
    pid。给旧版运行（无 heartbeat.json）兜底用；Windows 上无 /proc，返回空表。"""
    out = []
    want = os.path.basename(os.path.normpath(slug)).encode("utf-8", "replace")
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, name, "cmdline"), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if b"run_pipeline" not in raw:
            continue
        if want and want not in raw:
            continue
        out.append(int(name))
    return sorted(out)


# ── 阈值 ──────────────────────────────────────────────────────────────
def resolve_stall_sec(cli=None, env=None):
    """卡住阈值（秒）：CLI --stall-after > env DRIVER_STATUS_STALL_SEC > 默认 300。
    非法值 SystemExit（fail-fast：阈值配错会让四态判定整体失真）。"""
    env = os.environ if env is None else env
    raw, src = cli, "--stall-after"
    if raw in (None, ""):
        raw, src = env.get(STALL_ENV), STALL_ENV
    if raw in (None, ""):
        return DEFAULT_STALL_SEC
    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise SystemExit("ERROR: %s=%r 不是数字秒数" % (src, raw))
    if val <= 0:
        raise SystemExit("ERROR: %s=%r 必须 > 0（秒）" % (src, raw))
    return val


# ── 读盘（只读）──────────────────────────────────────────────────────
def _load_json(path):
    """读 JSON；不存在/坏掉返回 None（不抛，不留痕）。

    实现收口到 atomicio.load_json（#166）：写侧换盘与读者读盘在 Windows 上互斥，
    原来一次撞上就返回 None → collect() 静默退回「日志 mtime」口径，把判定依据
    说错却不报错。现在读写两侧共用同一套短退避。"""
    return atomicio.load_json(path)


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def find_log(slug, bg=None):
    """日志候选：bg_run.json 记的本次后台日志优先（#195：每轮独立文件，最准），
    其次 slug 同级 _driver_run.log（new_daily 的 nohup 落点），再次 slug 内同名文件。
    都没有返回 None。"""
    slug = os.path.abspath(slug)
    parent = os.path.dirname(slug)
    cands = []
    if isinstance(bg, dict) and bg.get("log"):
        cands.append(bg["log"])
    cands.extend((os.path.join(parent, LOG_NAME), os.path.join(slug, LOG_NAME)))
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def read_log_tail(path, n=3):
    """日志尾 n 行（只读尾部 8KB，不整文件载入）。"""
    if not path:
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > 8192:
                f.seek(size - 8192)
            raw = f.read()
        text = raw.decode("utf-8", "replace")
    except OSError:
        return []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


def _list_stamps(drv):
    """_driver 下已完成的阶段号（s01-s13，升序）。"""
    out = []
    try:
        names = os.listdir(drv)
    except OSError:
        return out
    for name in names:
        if not (name.startswith("stage_") and name.endswith(".json")):
            continue
        no = name[len("stage_"):-len(".json")]
        if no in STAGE_ORDER:
            out.append(no)
    out.sort()
    return out


def _age_vs_stall(age, stall, alive):
    """心跳年龄与阈值的比较句（四态里 RUNNING/STUCK 共用，别两处各写一遍）。"""
    if age is None:
        return "无心跳年龄可比（无 %s 也无日志），只能按进程存活判" % HEARTBEAT_NAME
    if alive is True:
        return ("心跳 %s 前 <= 阈值 %s" if age <= stall
                else "心跳 %s 前 > 阈值 %s（进程在，但已不再前进）") % (_dur(age), _dur(stall))
    return ("心跳 %s 前 <= 阈值 %s" if age <= stall
            else "心跳 %s 前 > 阈值 %s") % (_dur(age), _dur(stall))


def _report_stage_field(rep, stage, key):
    """从 report.json 的阶段台账里取某阶段的某个字段（取不到返回 None）。

    report 的 stages 结构可能残缺（老台账/半截写入），这里逐层 isinstance 兜底。"""
    if not stage or not isinstance(rep, dict):
        return None
    stages = rep.get("stages")
    if not isinstance(stages, dict):
        return None
    blk = stages.get(stage)
    if not isinstance(blk, dict):
        return None
    return blk.get(key)


def _fmt_at(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "?"


def _bg_is_current(bg, hb):
    """bg_run.json 记的是不是「当前这一轮」（否则是上一轮的残留记录）。

    同一个 slug 可以先后跑多轮：先一次后台运行（留下 exited/exit_code 的记录），
    再手工前台跑一轮。若不判新旧，旧记录的退出码/日志会把新运行误判成「已崩」。
    判定：两边都有 pid 时按 pid 是否同一个进程；否则按 started_ts 谁更晚；
    两边都没时间戳时宁可真（保持有记录就用的旧行为）。
    """
    if not isinstance(bg, dict):
        return False
    if not isinstance(hb, dict):
        return True
    try:
        hp = int(hb.get("pid")) if hb.get("pid") is not None else None
        bp = int(bg.get("driver_pid")) if bg.get("driver_pid") is not None else None
    except (TypeError, ValueError):
        hp = bp = None
    if hp is not None and bp is not None:
        return hp == bp
    try:
        return float(bg.get("started_ts") or 0) >= float(hb.get("started_ts") or 0)
    except (TypeError, ValueError):
        return True


def _dur(sec):
    """时长人类可读。"""
    if sec is None:
        return "未知"
    sec = float(sec)
    if sec < 60:
        return "%.0fs" % sec
    if sec < 3600:
        return "%.1f min" % (sec / 60.0)
    return "%.1f h" % (sec / 3600.0)


# ── 判定 ──────────────────────────────────────────────────────────────
def collect(slug, stall_sec=None, now=None, alive_fn=None):
    """采集 + 判定。返回一条状态记录（纯数据，渲染归 render）。

    alive_fn(pid, host) -> True/False/None，默认 pid_alive；测试可注入。
    只读：本函数不写任何文件。"""
    now = time.time() if now is None else float(now)
    stall = DEFAULT_STALL_SEC if stall_sec is None else float(stall_sec)
    alive_fn = alive_fn or pid_alive

    slug = os.path.abspath(slug)
    drv = os.path.join(slug, DRIVER_DIRNAME)
    rec = {
        "slug": slug, "slug_name": os.path.basename(os.path.normpath(slug)),
        "driver_dir": drv, "stall_sec": stall, "now": now,
        "state": None, "evidence": [], "notes": [],
        "stage": None, "stage_title": None, "stage_elapsed_sec": None, "attempt": None,
        "heartbeat_age_sec": None, "heartbeat_source": None, "heartbeat_at": None,
        "pid": None, "pid_host": None, "pid_alive": None,
        "started_ts": None, "elapsed_sec": None, "lane": None,
        "completed": [], "finished_ts": None,
        "exited": None, "log_path": None, "log_age_sec": None, "log_tail": [],
        "report": None, "heartbeat": None, "bg": None,
    }

    stamps = _list_stamps(drv)
    rec["completed"] = stamps
    rep = _load_json(os.path.join(drv, REPORT_NAME))
    hb = _load_json(os.path.join(drv, HEARTBEAT_NAME))
    bg = _load_json(os.path.join(drv, BG_NAME))
    if not isinstance(bg, dict):
        bg = None
    if bg is not None and not _bg_is_current(bg, hb):
        # 上一轮后台运行留下的记录：不能沿用它的日志/pid/退出码，否则新运行会被
        # 判成「已崩」（心跳明明新鲜）
        rec["notes"].append("%s 记的是上一轮运行（pid 对不上/更早），本次不拿它的"
                            "日志、pid 与退出码" % BG_NAME)
        bg = None
    rec["report"], rec["heartbeat"], rec["bg"] = rep, hb, bg

    lane_fp = os.path.join(drv, "lane.txt")
    lane = _read_text(lane_fp).strip() if os.path.isfile(lane_fp) else ""
    rec["lane"] = (hb or {}).get("lane") or lane or None

    log_path = find_log(slug, bg)
    rec["log_path"] = log_path
    if log_path:
        try:
            rec["log_age_sec"] = now - os.path.getmtime(log_path)
        except OSError:
            pass
    rec["log_tail"] = read_log_tail(log_path)

    # 心跳（两路，heartbeat.json 优先）
    if isinstance(hb, dict) and hb.get("ts"):
        try:
            rec["heartbeat_age_sec"] = now - float(hb["ts"])
            rec["heartbeat_source"] = HEARTBEAT_NAME
            rec["heartbeat_at"] = _fmt_at(hb["ts"])
        except (TypeError, ValueError):
            pass
    if rec["heartbeat_age_sec"] is None and rec["log_age_sec"] is not None:
        rec["heartbeat_age_sec"] = rec["log_age_sec"]
        rec["heartbeat_source"] = "日志 mtime"
        try:
            rec["heartbeat_at"] = _fmt_at(os.path.getmtime(log_path))
        except OSError:
            pass
    hb_age = rec["heartbeat_age_sec"]
    age = hb_age
    # 心跳缺失的两种情形要说清（AC4：心跳年龄必须可读，不能糊成「没有心跳」）
    if hb_age is None and isinstance(hb, dict):
        rec["notes"].append("%s 存在但没有可用的 ts 字段，算不出心跳年龄"
                            % HEARTBEAT_NAME)
    if rec["heartbeat_source"] == "日志 mtime":
        rec["notes"].append("无 %s，心跳年龄回退用日志 mtime；日志长时间不更新也可能"
                            "只是阶段内静默（如 H3 生成），不等于卡住" % HEARTBEAT_NAME)

    # pid + 存活
    exited = (hb or {}).get("exited")
    rec["exited"] = exited if isinstance(exited, dict) else None
    if rec["exited"] is None and bg and bg.get("status") == "exited" \
            and bg.get("exit_code") is not None:
        # 后台记录的退出码救回「没有心跳的退场」：预检/宿主守卫在 Driver 构造前
        # 就 sys.exit 了，heartbeat.json 根本不存在（#195 AC2 的缺口）
        rec["exited"] = {"code": bg.get("exit_code"),
                         "reason": "后台记录（driver 未及写心跳）",
                         "at": _fmt_at(bg.get("ended_ts")) if bg.get("ended_ts") else "?"}
        rec["notes"].append("退出码取自 %s（心跳无退出记录；预检/宿主守卫在心跳建立前"
                            "就退场了）" % BG_NAME)
    elif bg and bg.get("status") in ("failed", "blocked") and rec["exited"] is None:
        # 看守没把 driver 起起来（或守卫拒绝）：给一句原因，别让它长成「被 kill」
        rec["notes"].append("后台记录状态 %s：%s" % (bg.get("status"),
                                                   bg.get("error") or "无原因"))
    pid = (hb or {}).get("pid")
    pid_host = (hb or {}).get("host")
    if pid is None and bg:
        pid = bg.get("driver_pid") or bg.get("pid")
        pid_host = bg.get("host") or pid_host
        if pid is not None:
            rec["notes"].append("pid 取自 %s（心跳未记录 pid）" % BG_NAME)
    if pid is None and hb is None:
        pids = scan_pids(slug)
        if pids:
            pid = pids[0]
            rec["notes"].append("无 %s（旧版运行），pid 由进程表扫描得到" % HEARTBEAT_NAME)
        elif host_label() == "windows":
            rec["notes"].append("本机 Windows 无 /proc，扫不到旧运行（无 %s）的 pid；"
                                "到 driver 宿主用 ps 确认更准" % HEARTBEAT_NAME)
    if pid is not None:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = None
    rec["pid"] = pid
    rec["pid_host"] = pid_host

    if rec["exited"] is not None:
        # 已写过退出记录：进程必然已走。不再探 pid（防 pid 复用误判成「在跑」）
        alive = False
        rec["notes"].append("心跳已记录退出，不再探活 pid（防 pid 复用误判）")
    elif pid is not None:
        alive = alive_fn(pid, rec["pid_host"])
    else:
        alive = None
    rec["pid_alive"] = alive

    # 阶段与耗时（心跳优先：它写的是「此刻正在跑哪一阶段」；没有心跳才从
    # stamps + report 推断，report 的 seconds 只在阶段收尾时才有）
    stage = (hb or {}).get("stage")
    if stage:
        rec["stage"] = stage
        rec["stage_title"] = (hb or {}).get("stage_title")
        s_start = (hb or {}).get("stage_started_at")
        if s_start:
            try:
                rec["stage_elapsed_sec"] = now - float(s_start)
            except (TypeError, ValueError):
                pass
        rec["attempt"] = (hb or {}).get("attempt")
    else:
        rec["stage"] = next((s for s in STAGE_ORDER if s not in stamps), None)
    if rec["stage_title"] is None:
        # 心跳没记标题（旧版运行/心跳刚起）时，report 里的阶段台账是唯一来源
        rec["stage_title"] = _report_stage_field(rep, rec["stage"], "title")
    if rec["stage_elapsed_sec"] is None:
        rec["stage_elapsed_sec"] = _report_stage_field(rep, rec["stage"], "seconds")

    # 启动时刻 / 已跑时长
    started = (hb or {}).get("started_ts")
    if started is None and isinstance(rep, dict):
        sa = rep.get("started_at")
        if sa:
            try:
                started = time.mktime(time.strptime(str(sa), "%Y-%m-%d %H:%M:%S"))
            except (TypeError, ValueError, OverflowError):
                started = None
    if started is not None:
        try:
            rec["started_ts"] = float(started)
            rec["elapsed_sec"] = now - float(started)
        except (TypeError, ValueError):
            pass

    # ── 四态判定（顺序即优先级）──────────────────────────────────────
    ev = rec["evidence"]
    if COMPLETE_STAGE in stamps:
        rec["state"] = STATE_DONE
        s13_fp = os.path.join(drv, "stage_%s.json" % COMPLETE_STAGE)
        s13 = _load_json(s13_fp)
        if isinstance(s13, dict) and isinstance(s13.get("ts"), (int, float)):
            rec["finished_ts"] = s13["ts"]
        if rec["finished_ts"] is None:
            # stamp 的 ts 缺失/不是数字（手工造的、老格式）：退回文件 mtime，
            # 宁可给个近似完成时刻，也不要打印「（?）」
            try:
                rec["finished_ts"] = os.path.getmtime(s13_fp)
            except OSError:
                pass
        ev.append("完成标记 %s/stage_%s.json 存在（%s）"
                  % (DRIVER_DIRNAME, COMPLETE_STAGE, _fmt_at(rec["finished_ts"])))
        if rec["exited"] and rec["exited"].get("code") not in (0, None):
            ev.append("但心跳记的退出码是 %s——完成标记优先" % rec["exited"].get("code"))
    elif alive is True:
        ev.append("进程 pid %d 存活（宿主 %s）" % (pid, rec["pid_host"] or host_label()))
        fresh = age is not None and age <= stall
        rec["state"] = STATE_RUNNING if fresh else STATE_STUCK
        ev.append(_age_vs_stall(age, stall, alive=True))
    elif alive is False:
        rec["state"] = STATE_CRASHED
        if pid is not None:
            ev.append("进程 pid %d 已不在（宿主 %s）" % (pid, rec["pid_host"] or host_label()))
        else:
            ev.append("没有任何进程在跑这个 slug")
        ev.append("无完成标记 %s/stage_%s.json" % (DRIVER_DIRNAME, COMPLETE_STAGE))
        if rec["exited"]:
            code = rec["exited"].get("code")
            ev.append("上次退出 code=%s（%s）at %s"
                      % (code, EXIT_REASONS.get(code, rec["exited"].get("reason") or "未知原因"),
                         rec["exited"].get("at") or "?"))
        else:
            ev.append("心跳无退出记录 —— 被 kill / 掉电 / 宿主重启")
    else:
        # 探不准（跨宿主无 wsl / pid 未记录）：退回心跳口径，并说明不确定
        pid_txt = "进程存活未知（pid %s）" % (pid if pid is not None else "未记录")
        if age is not None:
            fresh = age <= stall
            rec["state"] = STATE_RUNNING if fresh else STATE_STUCK
            ev.append(_age_vs_stall(age, stall, alive=None))
            ev.append(pid_txt + ("—— 心跳新鲜是活着的强证据，但不等于探到了进程"
                                 if fresh else "—— 若宿主上已无该进程则其实是已崩"))
            rec["notes"].append("跨宿主无法探活；确认办法：到 %s 上 ps 看 pid"
                                % (rec["pid_host"] or "driver 宿主"))
        elif stamps or rep:
            rec["state"] = STATE_CRASHED
            ev.append("无 %s、无日志，但有阶段产物" % HEARTBEAT_NAME)
            ev.append("无完成标记 %s/stage_%s.json" % (DRIVER_DIRNAME, COMPLETE_STAGE))
        else:
            rec["state"] = STATE_IDLE
            ev.append("目录里无 stamps / %s / %s / 日志 —— 还没起跑"
                      % (REPORT_NAME, HEARTBEAT_NAME))

    if rec["state"] == STATE_DONE and rec["stage"] is None:
        # 全部 stamp 都在时「下一个没做的阶段」是 None——但已完成的一单不该显示
        # 「无阶段记录」，把收尾阶段摆出来（标题仍从 report 台账取）
        rec["stage"] = COMPLETE_STAGE
        rec["stage_title"] = _report_stage_field(rep, COMPLETE_STAGE, "title")

    if rec["state"] == STATE_CRASHED and rec["exited"] \
            and rec["exited"].get("code") == 0:
        rec["notes"].append("上次是正常退出但没有 s13 完成标记 —— 多半是 "
                            "--stop-after 半程停止或手工中断，不是崩")
    return rec


# ── 渲染 ──────────────────────────────────────────────────────────────
def render(rec):
    """一屏可读（AC4）：状态 / 依据 / 当前阶段 / 阶段耗时 / 心跳年龄 / 进度 / 日志尾。"""
    out = ["读报单 %s  [%s]" % (rec.get("slug_name"), rec.get("slug"))]
    out.append("  状态  %s" % rec.get("state"))
    out.append("  依据  %s" % "；".join(rec.get("evidence") or ["（无）"]))

    stage = rec.get("stage")
    if stage:
        head = "  阶段  %s %s" % (stage, rec.get("stage_title") or "（未记录标题）")
        bits = []
        if rec.get("state") == STATE_DONE:
            bits.append("收尾阶段（已完成）")
        elif rec.get("stage_elapsed_sec") is not None:
            bits.append("本阶段已 %s" % _dur(rec["stage_elapsed_sec"]))
        if rec.get("attempt"):
            bits.append("第 %s 次尝试" % rec["attempt"])
        if bits:
            head += "  " + " · ".join(bits)
        out.append(head.rstrip())
    else:
        out.append("  阶段  （无阶段记录）")

    age = rec.get("heartbeat_age_sec")
    src = rec.get("heartbeat_source")
    if age is not None:
        out.append("  心跳  %s 前 · %s · 来源 %s（阈值 %s；--stall-after 或 %s 可调）"
                   % (_dur(age), rec.get("heartbeat_at") or "?", src,
                      _dur(rec.get("stall_sec")), STALL_ENV))
    elif isinstance(rec.get("heartbeat"), dict):
        out.append("  心跳  %s 在但没有可用的 ts 字段 —— 算不出心跳年龄" % HEARTBEAT_NAME)
    else:
        out.append("  心跳  无（没有 %s，也没有日志）" % HEARTBEAT_NAME)

    bits = []
    if rec.get("started_ts"):
        bits.append("启动 %s" % _fmt_at(rec["started_ts"]))
        bits.append("已跑 %s" % _dur(rec.get("elapsed_sec")))
    if rec.get("lane"):
        bits.append("车道 %s" % rec["lane"])
    if rec.get("pid") is not None:
        bits.append("pid %s" % rec["pid"])
    if bits:
        out.append("  运行  " + " · ".join(bits))

    bg = rec.get("bg")
    if isinstance(bg, dict):
        bg_bits = ["run_id %s" % (bg.get("run_id") or "?"),
                   "状态 %s" % (bg.get("status") or "?")]
        if bg.get("exit_code") is not None:
            bg_bits.append("退出码 %s" % bg["exit_code"])
        if bg.get("driver_pid") is not None:
            bg_bits.append("driver pid %s" % bg["driver_pid"])
        out.append("  后台  " + " · ".join(bg_bits))

    done = rec.get("completed") or []
    if done:
        span = done[0] if len(done) == 1 else "%s-%s" % (done[0], done[-1])
        out.append("  进度  %d/%d 阶段完成（%s）" % (len(done), len(STAGE_ORDER), span))
    else:
        out.append("  进度  0/%d 阶段完成" % len(STAGE_ORDER))

    log_path = rec.get("log_path")
    if log_path:
        out.append("  日志  %s · %s 前更新"
                   % (log_path, _dur(rec.get("log_age_sec"))))
        for ln in (rec.get("log_tail") or []):
            out.append("        │ " + ln[:200])
    else:
        out.append("  日志  未找到 %s" % LOG_NAME)

    for note in (rec.get("notes") or []):
        out.append("  注    " + note)
    return "\n".join(out)


def resolve_slug(script=None, workdir=None):
    """--status 的 slug 解析（与 Driver.__init__ 同口径，但**不建目录**）。"""
    if workdir:
        slug = os.path.abspath(workdir)
    elif script:
        base = os.path.dirname(os.path.abspath(script))
        if os.path.basename(base) == "00_口播稿":
            base = os.path.dirname(base)
        slug = base
    else:
        raise SystemExit("ERROR: --status 需要 --workdir <slug目录> 或 --script <口播稿>"
                         "（本工具只读，不会自己去猜是哪一单）")
    if not os.path.isdir(slug):
        raise SystemExit("ERROR: slug 目录不存在: %s" % slug)
    return slug


def run_cli(script=None, workdir=None, stall_after=None, env=None, out=None):
    """--status 命令体：采集 → 渲染 → 打印。返回退出码（0 成功 / 2 用法错误）。

    注意：这里只 print，不写文件、不发通知、不重启（AC3）。"""
    out = sys.stdout if out is None else out
    slug = resolve_slug(script=script, workdir=workdir)
    stall = resolve_stall_sec(stall_after, env)
    rec = collect(slug, stall_sec=stall)
    print(render(rec), file=out)
    return 0


def main(argv=None):
    """独立入口：python scripts/driver_status.py --workdir <slug> [--stall-after 秒]"""
    import argparse
    ap = argparse.ArgumentParser(
        prog="driver_status.py",
        description="只读查询读报单状态（运行中/卡住/已崩/已完成）——#146")
    ap.add_argument("--script", default=None, help="口播稿路径（与 --workdir 二选一）")
    ap.add_argument("--workdir", default=None, help="slug 目录（与 --script 二选一）")
    ap.add_argument("--stall-after", type=float, default=None, metavar="SEC",
                    help="「卡住」阈值秒数（默认 %d，也可用 env %s）"
                         % (DEFAULT_STALL_SEC, STALL_ENV))
    args = ap.parse_args(argv)
    return run_cli(script=args.script, workdir=args.workdir, stall_after=args.stall_after)


if __name__ == "__main__":
    sys.exit(main())
