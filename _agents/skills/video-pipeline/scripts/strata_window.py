#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strata_window.py — Strata 生命周期托管：探活→跨界拉起→失败叫人 + 显存窗口纪律（#85）。

driver 全自动托管本地 Strata 实例（Windows 主机 8080），无人值守跑单不停摆：

  拉起（ensure_online，s04 入口 / s11 入口再拉）
    /v1/models 在线 → 预热一发即复用；离线 → 跨界拉起 → 300s 加载窗口内轮询就绪
    → 就绪后预热。加载超时 → 停止残留 + 修复重启，最多 MAX_ROUNDS 轮，耗尽
    StrataHardError 硬失败叫人（冷启动不占预算，与 KVMem 票3 预算语义同源）。
  停止（stop，s08 生图前 / s12 确认门前）
    在线才动手：netstat 取 8080 监听 PID → taskkill 树杀 → 探活转离线 + 被杀 pid
    自身显存回落（按进程归属，非整卡水位；<VRAM_USED_MAX）双确认后才放行烧卡；
    本就离线 already-off 幂等返回。
  劣化自愈不设独立接缝：「活着但劣化」形态（RAM 短缺降级，#83 §2）随请求升温
    自愈回满速，重启无益；「真死」形态由连败重试时 run_stage 的 _gate_strata
    再走 ensure_online 覆盖（探活拒连即触发阶梯）。

窗口纪律（#83 spike §3 实测落定，WINDOW_MODE env）：
    conflict（默认，本机实测：Strata 常驻即占 94% VRAM，与 H3/ComfyUI 生图互斥）
      = s04 拉起 → s08 生图前停 → s11 入口再拉 → s12 确认门前停（单条视频最多
      两次加载，各 20-150s，可接受）。模式判断归 driver 编排，本模块只管起停。
    coexist = 常驻不停（未来显存充裕的机器；driver 三处钩子按模式跳过 stop）。

跨界执行（#83 spike §1/§5 实测定案）：
    driver 跑在 WSL（生产形态）；Strata 在 Windows 侧。探活/预热 = WSL 经默认路由
    网关直连主机 IP（端点单一事实源 strata_h3_lane.BASE_URL，--host 0.0.0.0）；
    拉起 = powershell.exe Start-Process fire-and-forget（STRATA_START_CMD 可覆盖，
    默认 spike §5 串）；停止 = netstat.exe 取监听 PID → taskkill.exe /F /T 树杀
    （引擎子进程连带退出，实测 VRAM 回 466MiB 基线）。两侧均可运行（Windows 侧
    同名 exe 直接在 PATH）。

加载判据（#83 spike §2：就绪 ≠ 满速）：
    ① /v1/models 200（加载窗口 300s，实测热 18.2s/温 73.7s，真冷启动更慢仍在窗口内）
    ——ensure_online 的唯一放行/失败判据，超时走修复重启直至预算耗尽叫人；
    ② 就绪后预热一发（首补全实测 70.3s——专家缓存全冷属常态不算故障，长超时
    WARMUP_TIMEOUT 界定），**失败不拦截**：真实请求自带超时与整单重试（run_lane
    3 次调用），预热只是把冷启代价提前付掉、给 s04 的 900s 预算让路；预热失败
    且真实请求也败 = 整单连败走 driver 既有连败协议，不在本层叫人。

测试接缝（全桩，不真起服务）：probe / warmup 的 urlopen、launch 的 Popen、
port_pids / kill_tree / vram_used_mib 的 run、time、log 全为模块级可替换；
test_strata_window.py 原地替换驱动阶梯全部分支。

用法（driver）：
    import strata_window
    info = strata_window.ensure_online(notify=driver.notify)   # s04 / s11 入口
    info = strata_window.stop(notify=driver.notify)            # s08 / s12 入口
    timeout = strata_window.STALL_SECS                          # 单次 LLM 调用挂死判据
"""
import json
import os
import re
import subprocess
import time
import urllib.request

# 端点单一事实源在 strata_h3_lane（#86 定案）：BASE_URL 动态解析 WSL 网关/127.0.0.1，
# DEFAULT_MODEL 与请求方言（enable_thinking=false）同源。
from strata_h3_lane import BASE_URL, DEFAULT_MODEL

# ── 配置（env 可覆盖；口径沿袭 kvmem_window 票3/票4 定案）────────────────
PORT = int(BASE_URL.rsplit(":", 1)[-1])  # 监听端口从端点提取（默认 8080，勿双写）
# 拉起命令（#83 spike §5 定案 powershell 串 = run-iq3_s.bat 等价 + --host 0.0.0.0，
# 去 --open 免浏览器弹窗）；env STRATA_START_CMD 承载变更
START_CMD = os.environ.get(
    "STRATA_START_CMD",
    "powershell.exe -NoProfile -Command \"Start-Process -WindowStyle Hidden "
    "-FilePath 'E:\\AI\\LLM\\Strata\\.venv\\Scripts\\python.exe' "
    "-ArgumentList 'E:\\AI\\LLM\\Strata\\serve\\server.py','--engine','strata',"
    "'--config','E:\\AI\\LLM\\Strata\\strata-iq3_s.json','--port','8080','--host','0.0.0.0'\"")
LOAD_TIMEOUT = float(os.environ.get("STRATA_LOAD_TIMEOUT", "300"))  # 加载窗口（#83 §2 口径保留）
WARMUP_TIMEOUT = float(os.environ.get("STRATA_WARMUP_TIMEOUT", "180"))  # 首补全实测 70.3s，3 倍余量
MAX_ROUNDS = int(os.environ.get("STRATA_MAX_ROUNDS", "2"))   # 修复重启硬边界（票3 定案沿用）
STALL_SECS = int(os.environ.get("STRATA_STALL_SECS", "600"))  # 单次调用挂死判据（kvmem_window 迁移）
VRAM_USED_MAX = int(os.environ.get("STRATA_VRAM_USED_MAX", "4000"))  # 显存回落闸（全仓同值口径）
# 窗口纪律模式：conflict（#83 实测本机形态）= s08/s12 停；coexist = 常驻不停。
# driver 三处钩子读本常量判断；本模块不感知模式（编排职责在 driver）。
WINDOW_MODE = os.environ.get("STRATA_WINDOW_MODE", "conflict")
LOOP_GUARD = int(os.environ.get("STRATA_LOOP_GUARD", "4"))  # 外层迭代护栏（非重启类失败也收敛）

# 窗口级状态：open=窗口已开启（同窗口不重复清预算）；restarts=本窗口已用重启轮数
# （stop closed 即关窗清零，下次 ensure_online 重开新窗口）
_STATE = {"open": False, "restarts": 0}


class StrataHardError(RuntimeError):
    """自愈阶梯耗尽——硬失败叫人（调用方不得再重试阶梯）。"""


def log(msg):
    print(f"[strata-window {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _note(notify, msg):
    """阶梯动作通报：日志常开 + 飞书尽力而为（notify 由 driver 传 self.notify）。"""
    log(msg)
    if notify:
        try:
            notify(msg)
        except Exception as e:  # noqa: BLE001
            log(f"[notify] 发送失败（忽略）: {e}")


def probe(urlopen=None):
    """/v1/models 探活（5s 界，200 即在线；#83 spike 探活口径）。"""
    urlopen = urlopen or urllib.request.urlopen
    try:
        with urlopen(f"{BASE_URL}/v1/models", timeout=5) as r:
            return getattr(r, "status", 200) == 200
    except Exception:  # noqa: BLE001 — 探活对一切失败形态统一 False
        return False


WARMUP_BODY = json.dumps({
    "messages": [{"role": "user", "content": "Say OK"}],
    "model": DEFAULT_MODEL, "max_tokens": 8, "temperature": 0,
    "chat_template_kwargs": {"enable_thinking": False},
}).encode("utf-8")


def warmup(urlopen=None, timeout=None):
    """预热一发（#83 §2 判据②：就绪后首补全 70.3s 属常态，不算故障）。
    返回 True/False——失败**不抛不拦截**：真实请求自带超时与整单重试，预热只是
    把冷启代价提前付掉（s04 单次调用的 900s 预算因此不被首请求吃掉）。"""
    urlopen = urlopen or urllib.request.urlopen
    try:
        req = urllib.request.Request(f"{BASE_URL}/v1/chat/completions",
                                     data=WARMUP_BODY,
                                     headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=timeout or WARMUP_TIMEOUT) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001
        log(f"预热请求失败（不拦截，真实请求自行超时重试）: {type(e).__name__}: {e}")
        return False


def launch(popen=None):
    """跨界拉起：fire-and-forget Popen（不捕获不等待——interop 下等待长命令会挂，
    comfyui_selfheal.relaunch 同款拓扑；Start-Process 本身即异步发射）。
    Popen 抛出（找不到 powershell.exe 等）原样上抛，阶梯按「拉起失败」计轮。"""
    popen = popen or subprocess.Popen
    popen(START_CMD, shell=True, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)


def _run(argv, timeout=30):
    # text=False：netstat.exe 等 Windows 命令输出本地代码页（中文 Win = GBK），
    # utf-8 强解会炸读线程丢输出——PID 等关键字段是 ASCII，表头 decode 容错即可
    p = subprocess.run(argv, capture_output=True, text=False, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", errors="replace")
    return out


def port_pids(port=None, run=None):
    """netstat.exe 取 port 监听 PID 列表（Windows 版 netstat，WSL interop 与
    Windows 侧同名可执行均在 PATH；Linux netstat 的 -o 语义不同，不可混用）。"""
    run = run or _run
    out = run(["netstat.exe", "-ano"], timeout=25)
    pids = []
    for ln in out.splitlines():
        m = re.search(rf"TCP\s+\S+:{port or PORT}\s+\S+\s+LISTENING\s+(\d+)", ln)
        if m and int(m.group(1)) not in pids:
            pids.append(int(m.group(1)))
    return pids


def kill_tree(pid, run=None):
    """taskkill /F /T 树杀（引擎 strata.exe 是 python server 子进程，连带退出——
    #83 §5 实测 VRAM 回 466MiB 桌面基线）。"""
    run = run or _run
    run(["taskkill.exe", "/F", "/T", "/PID", str(pid)], timeout=30)


def vram_used_mib(run=None, timeout=30):
    """nvidia-smi 显存占用（MiB，GPU0）；查询失败 → None（调用方按「未知→放行」）。"""
    run = run or _run
    try:
        out = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                  timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.isdigit():
            return int(ln)
    return None


def vram_pids_mib(pids, run=None, timeout=30):
    """按进程归属查显存（MiB）：{pid: MiB}。

    2026-10-17 事故根因修复：vram_used_mib 取的是**整卡** memory.used，ComfyUI
    等常驻进程会把水位顶在 7.5GB 左右——「Strata 已释放」被误判成「显存未回落」，
    s12 因此白白失败一次。显存闸的语义应是「我们杀掉的那些 pid 是否还占着显存」，
    与别的进程无关。查询失败返回 {}（沿用「未知→放行」口径）。"""
    run = run or _run
    want = {int(p) for p in (pids or [])}
    if not want:
        return {}
    try:
        out = run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                   "--format=csv,noheader,nounits"], timeout=timeout)
    except Exception:  # noqa: BLE001
        return {}
    got = {}
    for ln in out.splitlines():
        a = [x.strip() for x in ln.split(",")]
        if len(a) < 2 or not a[0].isdigit() or not a[1].isdigit():
            continue
        pid = int(a[0])
        if pid in want:
            got[pid] = int(a[1])
    return got


def wait_online(timeout=None, interval=5.0, probe_fn=None):
    """加载窗口内轮询探活（有界，默认 LOAD_TIMEOUT）。probe_fn 供阶梯桩注入。"""
    probe_fn = probe_fn or probe
    deadline = time.time() + (timeout or LOAD_TIMEOUT)
    while time.time() < deadline:
        if probe_fn():
            return True
        time.sleep(interval)
    return probe_fn()


def wait_vram_idle(timeout=120.0, interval=5.0, vram_fn=None):
    """等显存回落（used < VRAM_USED_MAX，有界）。查询失败按放行（乐观）并记日志。"""
    vram_fn = vram_fn or vram_used_mib
    deadline = time.time() + timeout
    while time.time() < deadline:
        used = vram_fn()
        if used is None:
            log("nvidia-smi 查询失败（显存水位未知，按放行处理）")
            return True
        if used < VRAM_USED_MAX:
            return True
        time.sleep(interval)
    used = vram_fn()
    return used is None or used < VRAM_USED_MAX


def _use_restart():
    """重启预算扣减（窗口级硬边界）：耗尽即 StrataHardError。首次拉起不占预算
    （票3 预算语义：预算只数失败后的修复性重启）。"""
    if _STATE["restarts"] >= MAX_ROUNDS:
        raise StrataHardError(
            f"Strata 重启预算耗尽（本窗口已 {MAX_ROUNDS} 轮修复重启仍不健康）")
    _STATE["restarts"] += 1


def stop(notify=None, probe_fn=None, pids_fn=None, kill_fn=None, vram_fn=None):
    """Strata 停止（s08 生图前 / s12 确认门前）：探活在线才动手（幂等 already-off）。
    双确认后才算关窗：① 探活转离线（有界轮询）② **被我们杀掉的 pid 自身**显存回落
    （vram_pids_mib 按进程归属判定；整卡水位被 ComfyUI 等常驻进程占着不算泄漏，
    查询失败按放行）。确认不过 → RuntimeError（调用方不放行烧卡；s08 既有结构会先
    自愈 ComfyUI 假活再重关一次）。关窗即重置重启预算（下次 ensure_online 新窗口）。"""
    probe_fn = probe_fn or probe
    pids_fn = pids_fn or port_pids
    kill_fn = kill_fn or kill_tree
    vram_fn = vram_fn or vram_used_mib
    if not probe_fn():
        log(f"Strata 本就离线（{BASE_URL} 探活拒连），already-off")
        _STATE.update(open=False, restarts=0)
        return {"outcome": "already-off", "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    pids = pids_fn()
    if not pids:
        raise RuntimeError(
            f"Strata 探活在线但 {PORT} 端口无监听 PID（形态异常，人工核查 netstat）")
    _note(notify, f"🔄 Strata 窗口关闭：树杀 {PORT} 监听进程（pid {pids}），释放显存给生图")
    for pid in pids:
        kill_fn(pid)
    deadline = time.time() + 30
    while time.time() < deadline and probe_fn():
        time.sleep(2)
    if probe_fn():
        raise RuntimeError(f"Strata taskkill 后探活仍在线（{BASE_URL}）——不放行烧卡")
    if not wait_vram_idle(vram_fn=vram_fn):
        used = vram_fn()
        # 2026-10-17 事故：整卡水位 ≠ Strata 泄漏。ComfyUI 常驻时 used 恒在 7.5GB
        # 上下，旧判据把 s12 一次正常关窗判成失败（used=7509 ≥ 4000），driver 重试
        # 走 already-off 才放行。改为按进程归属：我们杀掉的 pid 仍在占显存才算泄漏。
        leaked = vram_pids_mib(pids)
        if leaked:
            raise RuntimeError(
                f"Strata 停止后自身仍占显存（{leaked} MiB）——不放行烧卡")
        log(f"显存 used={used} MiB ≥ {VRAM_USED_MAX}，但非 Strata 进程占用"
            f"（常驻进程，已杀 pid {pids} 均无显存）→ 按放行处理")
    used = vram_fn()
    log(f"Strata 窗口已关闭（显存 used={used} MiB）")
    _STATE.update(open=False, restarts=0)
    return {"outcome": "closed", "pids": pids, "vram_used_mib": used,
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def ensure_online(notify=None, probe_fn=None, launch_fn=None, stop_fn=None,
                  warmup_fn=None, wait_fn=None):
    """Strata 拉起阶梯（s04 / s11 入口）。在线即复用（预热后放行）；离线拉起，
    加载超时走「停止残留+修复重启」最多 MAX_ROUNDS 轮，耗尽 StrataHardError 硬失败
    叫人。冷启动不占预算。返回台账 info dict。全参数为测试注入点，生产不传。"""
    probe_fn = probe_fn or probe
    launch_fn = launch_fn or launch
    stop_fn = stop_fn or stop
    warmup_fn = warmup_fn or warmup
    wait_fn = wait_fn or wait_online
    if not _STATE["open"]:
        _STATE.update(open=True, restarts=0)
        log(f"Strata 窗口开启（预算 {MAX_ROUNDS} 轮修复重启，加载窗口 {LOAD_TIMEOUT:.0f}s）")
    events = []
    first_launch = True  # 首次拉起=冷启动，不占修复性重启预算
    for rnd in range(1, LOOP_GUARD + 1):
        if probe_fn():
            ok = warmup_fn()
            log(f"Strata 已在线（{BASE_URL}）→ 复用" + ("（预热通过）" if ok else "（预热失败，放行）"))
            return {"outcome": "reused", "rounds": rnd, "warmed": ok,
                    "events": events, "restarts": _STATE["restarts"],
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if first_launch:
            first_launch = False
            label = "冷启动"
        else:
            # 加载超时残留先停（探活拒连形态多数 already-off 幂等；半死形态真杀）
            try:
                info = stop_fn(notify=notify)
                if info["outcome"] == "closed":
                    events.append(f"r{rnd}:清理残留实例")
            except Exception as e:  # noqa: BLE001 — 停止失败不阻断重启阶梯（拉起会覆盖端口形态）
                events.append(f"r{rnd}:停止残留失败({e})")
            _use_restart()
            label = f"第{_STATE['restarts']}/{MAX_ROUNDS}轮修复重启"
        events.append(f"r{rnd}:拉起({label})")
        _note(notify, f"🔄 Strata 启动中（{label}，加载窗口 ≤{LOAD_TIMEOUT:.0f}s；"
                      f"就绪后首请求可达 {WARMUP_TIMEOUT:.0f}s 属正常预热）")
        try:
            launch_fn()
        except Exception as e:  # noqa: BLE001 — 拉起失败计一轮，预算内继续
            events.append(f"r{rnd}:拉起失败({e})")
            continue
        if wait_fn():
            ok = warmup_fn()
            log(f"Strata 窗口就绪（{label}，预热" + ("通过）" if ok else "失败但放行）"))
            return {"outcome": "started" if label == "冷启动" else "restarted",
                    "rounds": rnd, "warmed": ok, "events": events,
                    "restarts": _STATE["restarts"],
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        events.append(f"r{rnd}:{LOAD_TIMEOUT:.0f}s加载窗口内未就绪")
        _note(notify, f"⚠️ Strata {LOAD_TIMEOUT:.0f}s 加载窗口内未就绪"
                      f"（{'预算内将修复重启' if _STATE['restarts'] < MAX_ROUNDS else '最后一轮'}）")
    raise StrataHardError("Strata 拉起失败（自愈阶梯内未达在线）: " + "；".join(events[-4:]))
