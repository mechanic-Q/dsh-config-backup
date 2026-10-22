#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""comfyui_selfheal.py — Windows ComfyUI 僵尸自愈（s08 烧卡的配套恢复，2026-09-28）。

故障模式（09-26 / 09-28 两次实锤，详见记忆 comfyui-cuda-crash-zombie）：
  H3 烧卡早期偶发 GPU 层 CUDA 故障（cudaErrorUnknown，09-28 实锤在 DLSSNR_Video
  节点；09-26 伴随 NVML 访问违例与 whisper CUDA 报错），ComfyUI 进程不退出变
  "僵尸"：HTTP 半死（碰 GPU 的端点 500 / 挂起）、死抓 ~13GB 显存不放 → driver
  两道闸（submit_accel 预检 "ComfyUI 不可达"、s08 入口 Strata 停服 "显存未回落"，
  #85 起 strata_window.stop）
  先后拦下 → 连败 2 次退出 + 飞书叫人 → 一次瞬时抖动变成全天停摆（需人工清僵尸）。

heal(notify, vram_gate_mib=None)（s08_h3 入口调用，探活 200 时零动作零开销）：
  1. 探活 /system_stats（Windows curl.exe → 18188 portproxy，与 submit_accel
     预检同一口径 → "自愈说健康" ⇒ "submit_accel 预检必过"；连续两次失败才判
     不健康，防瞬时抖动误杀健康实例）
  2. 不健康 → netstat 找 8188 监听 PID → taskkill //T //F 清僵尸（显存随之释放）
     → WMI Win32_Process.Create 无头直启全新实例（与 cron 同参；属主 = WMI 服务，
     **脱离 WSL interop job**——09-28 实验 A/B 实锤：interop 直启与 Start-Process
     脱离的子进程都活不过 wsl 会话退出，WMI 创建的不受影响；launch 指令写
     UTF-8-BOM ps1 落 ASCII Temp 路径后 fire-and-forget 执行——不捕获不等待，
     规避 interop 下 powershell -File 挂起；中文日志路径只在 ps1 内容里，
     不跨 WSL→Windows 命令行边界）
     → 轮询 /system_stats 200（≤ READY_TIMEOUT）
  3. vram_gate_mib 给定（s08 入口 Strata 停服 "显存未回落" 场景，#85）且实例健康但显存 ≥ 阈值：
     先等队列排干（防误停进行中的生成）→ POST /free 卸载缓存权重 → 确认显存
     回落；无效转重启路径
  全程 notify 播报、自身绝不抛异常（失败 → ok=False，调用方按连败协议叫人）。
  已知边界：探活只覆盖 /system_stats——CUDA 局部性错误（单节点失败但上下文
  未死）探测不到，那类错误由翻转重生成梯队与产物对账兜底。
"""
import json
import os
import subprocess
import sys
import time

# 显存闸缺省（全仓同值口径 4000 MiB：used<此值=无竞争服务；原引 kvmem_window，
# 该模块随 #85 退役后内联——改值须与 strata_window.VRAM_USED_MAX 一并核对）
_GATE_DEFAULT = 4000

# System32 工具双端解析（#151：driver 主执行翻 Windows 后本模块被 Windows 侧
# 直接 import——Windows 上同名 exe 都在 PATH，不再拼 /mnt/c 前缀）
if os.name == "nt":
    CURL = os.environ.get("COMFYUI_CURL", "curl.exe")
    CMD = os.environ.get("COMFYUI_CMD", "cmd.exe")
    PS = os.environ.get("COMFYUI_PS", "powershell.exe")
else:
    _WIN = "/mnt/c/Windows/System32"
    CURL = _WIN + "/curl.exe"
    CMD = _WIN + "/cmd.exe"
    PS = _WIN + "/WindowsPowerShell/v1.0/powershell.exe"

HEAL_URL = os.environ.get("COMFYUI_HEAL_URL", "http://192.168.50.231:18188/system_stats")
HEAL_PORT = int(os.environ.get("COMFYUI_HEAL_PORT", "8188"))
VRAM_GATE_MIB = int(os.environ.get("COMFYUI_HEAL_VRAM_GATE_MIB", _GATE_DEFAULT))
PROBE_TIMEOUT = int(os.environ.get("COMFYUI_HEAL_PROBE_TIMEOUT", "8"))
READY_TIMEOUT = float(os.environ.get("COMFYUI_HEAL_READY_TIMEOUT", "240"))
READY_INTERVAL = float(os.environ.get("COMFYUI_HEAL_READY_INTERVAL", "10"))
QUEUE_DRAIN_TIMEOUT = float(os.environ.get("COMFYUI_HEAL_QUEUE_DRAIN_TIMEOUT", "600"))
COMFY_DIR_WIN = r"E:\AI\Comfyui-WF-2026.8.8\Comfyui-WF-2026.8.8\ComfyUI"
COMFY_PY_WIN = r"E:\AI\Comfyui-WF-2026.8.8\Comfyui-WF-2026.8.8\python\python.exe"
# 重启目标可注入（演练用假件）；默认 = 生产 ComfyUI（参数与 cron 无头直启同款）
HEAL_RELAUNCH_EXE_WIN = os.environ.get("COMFYUI_HEAL_RELAUNCH_EXE", COMFY_PY_WIN)
HEAL_RELAUNCH_ARGS_WIN = os.environ.get(
    "COMFYUI_HEAL_RELAUNCH_ARGS",
    "main.py --listen 0.0.0.0 --port 8188 --fast fp16_accumulation")
HEAL_LOG_DIR_WIN = os.environ.get("COMFYUI_HEAL_LOG_DIR_WIN", r"E:\每日新中国\logs")
# launch ps1 落 ASCII Temp 路径（interop 传参纯 ASCII）；内容 UTF-8-BOM（中文安全）。
# HEAL_PS1_WIN/WSL 双口径：写文件用本机口径，Popen 拉起用 Windows 口径（#151：
# Windows 侧跑时两者同路径，不再出现把 "/mnt/c/…" 当文件名建出的怪文件）
HEAL_PS1_WIN = os.environ.get(
    "COMFYUI_HEAL_PS1_WIN", r"C:\Users\LMR\AppData\Local\Temp\comfyui_heal_launch.ps1")
HEAL_PS1_WSL = os.environ.get(
    "COMFYUI_HEAL_PS1_WSL",
    HEAL_PS1_WIN if os.name == "nt" else "/mnt/c/Users/LMR/AppData/Local/Temp/comfyui_heal_launch.ps1")
FREE_BODY = '{"unload_models": true, "free_memory": true}'

_sleep = time.sleep  # 测试注入点


def log(msg):
    # #170：cp936 裸跑/控制台下 ⚠️ 等 emoji 不可编码，strict print 直接掀翻进程
    # （test_comfyui_selfheal 裸跑实测）。与 run_pipeline.log() 同款 per-call 降级。
    line = f"[comfy-heal {time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(enc, "backslashreplace").decode(enc, "replace"), flush=True)


def _run(cmd, timeout=30):
    """子进程直调（列表参数，无 shell 引号问题）；Windows 侧输出编码混杂 → replace。"""
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, errors="replace")


def probe(timeout=None):
    """→ (healthy, code, detail)。code=="200" 视为健康；
    system_stats 内部调 torch.cuda.mem_get_info——CUDA 上下文死亡时必抛错（500），
    进程整个退出时连接拒绝（code 000）——两种僵尸形态都逃不过这个探针。"""
    t = PROBE_TIMEOUT if timeout is None else timeout
    try:
        r = _run([CURL, "-s", "-m", str(t), "-o", "NUL", "-w", "%{http_code}", HEAL_URL],
                 timeout=t + 5)
        code = (r.stdout or "").strip()
        detail = (r.stderr or "").strip()[:100]
        return code == "200", code, detail
    except Exception as e:  # noqa: BLE001 — 探活自身异常按不健康处理
        return False, None, f"{type(e).__name__}: {str(e)[:80]}"


def pids_on_port(port=None):
    """8188 监听 PID（Windows netstat）。杀进程的目标是 ComfyUI 本体（8188），
    不是 18188——那是 portproxy（iphlpsvc），杀它只会剪断转发链。"""
    port = HEAL_PORT if port is None else port
    r = _run([CMD, "/c", f"netstat -ano | findstr :{port}"], timeout=25)
    pids = set()
    for ln in (r.stdout or "").splitlines():
        parts = ln.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(":" + str(port)):
            pids.add(parts[4])
    return sorted(pids)


def kill_tree(pid):
    r = _run([CMD, "/c", f"taskkill /PID {pid} /T /F"], timeout=30)
    return r.returncode == 0


def vram_used_mib():
    try:
        r = _run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                 timeout=20)
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.isdigit():
                return int(ln)
    except Exception:  # noqa: BLE001 — 查询失败按未知处理（调用方区分场景）
        pass
    return None


def free_memory(timeout=20):
    """POST /free 卸载全部缓存权重 → True=HTTP 200。仅对健康实例调用
    （僵尸实例的 /free 同样会撞 CUDA 错误，无意义）。"""
    try:
        r = _run([CURL, "-s", "-m", str(timeout), "-X", "POST", "http://127.0.0.1:8188/free",
                  "-H", "Content-Type: application/json", "-d", FREE_BODY,
                  "-o", "NUL", "-w", "%{http_code}"], timeout=timeout + 5)
        return (r.stdout or "").strip() == "200"
    except Exception:  # noqa: BLE001
        return False


def queue_running_count(timeout=8):
    """→ 队列在跑任务数；查询失败 → -1（未知，调用方按保守处理）。"""
    try:
        r = _run([CURL, "-s", "-m", str(timeout), "http://127.0.0.1:8188/queue"],
                 timeout=timeout + 5)
        d = json.loads((r.stdout or "").strip().lstrip("\ufeff"))
        return len(d.get("queue_running") or [])
    except Exception:  # noqa: BLE001
        return -1


def relaunch(tag=None):
    """WMI Win32_Process.Create 拉起 ComfyUI（属主 = WMI 服务，完全脱离 WSL
    interop job——09-28 实验 A/B 实锤：interop 直启与 Start-Process 脱离的子进程
    都活不过 wsl 会话退出，WMI 创建的不受影响）。
    launch 指令写 UTF-8-BOM ps1 落 ASCII Temp 路径 → powershell fire-and-forget
    （Popen + DEVNULL，不捕获不等待——规避 interop 下 powershell 挂起）。
    返回 True=Popen 成功（真正存活由 wait_healthy 轮询确认）。"""
    tag = tag or time.strftime("%Y%m%d%H%M%S")
    logp = HEAL_LOG_DIR_WIN + "\\" + f"comfyui-headless-{tag}-heal.log"
    # cmd /s /c 整体引号包裹：/s 让 cmd 剥掉首尾引号后，exe/重定向目标各自带引号
    cmd_line = (r'cmd /s /c "cd /d ' + COMFY_DIR_WIN + r' && "' + HEAL_RELAUNCH_EXE_WIN
                + r'" ' + HEAL_RELAUNCH_ARGS_WIN
                + r' > "' + logp + r'" 2>&1"')
    ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          f"-Arguments @{{CommandLine='{cmd_line}'}}; "
          "Write-Output ('WMI rc=' + $r.ReturnValue + ' pid=' + $r.ProcessId)")
    with open(HEAL_PS1_WSL, "w", encoding="utf-8-sig") as f:
        f.write(ps)
    subprocess.Popen([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                      HEAL_PS1_WIN],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def wait_healthy(timeout=None, interval=None):
    timeout = READY_TIMEOUT if timeout is None else timeout
    interval = READY_INTERVAL if interval is None else interval
    for _ in range(max(1, int(timeout / interval))):
        ok, _, _ = probe()
        if ok:
            return True
        _sleep(interval)
    return probe()[0]


def wait_queue_drain(timeout=None, interval=20):
    timeout = QUEUE_DRAIN_TIMEOUT if timeout is None else timeout
    for _ in range(max(1, int(timeout / interval))):
        if queue_running_count() <= 0:
            return True
        _sleep(interval)
    return queue_running_count() <= 0


def heal(notify=None, vram_gate_mib=None):
    """ComfyUI 僵尸自愈 → 台账 dict（ok/actions/probe/vram_before/vram_after/outcome）。
    vram_gate_mib=None（s08 入口）：仅处理"不健康"——此时 H3 前置的 LLM 服务
      （Strata，#85 conflict 纪律下由 s08 入口停服）可能尚未退出，总显存水位不参与
      判断（误判会错杀/误卸）；
    vram_gate_mib=4000（s08 入口停服 "显存未回落" 场景）：另处理"健康但显存 ≥
      阈值"——重试场景下 ComfyUI 缓存权重属合法占用，/free 卸载即可放行。
    自身绝不抛异常（失败 → ok=False，调用方按连败协议叫人）。"""
    info = {"ok": False, "actions": [], "probe": "", "vram_before": None,
            "vram_after": None, "outcome": "", "at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def note(text):
        log(text)
        if notify:
            try:
                notify(text)
            except Exception:  # noqa: BLE001 — 播报失败不影响自愈
                pass

    try:
        ok, code, err = probe()
        if not ok:
            _sleep(3)
            ok, code, err = probe()  # 二次确认：防瞬时抖动误杀健康实例
        vram0 = vram_used_mib()
        info["probe"] = f"HTTP {code}" if ok else f"不可达（{err or code}）"
        info["vram_before"] = vram0
        if ok and (vram_gate_mib is None or vram0 is None or vram0 < vram_gate_mib):
            info.update(ok=True,
                        outcome="healthy" if vram_gate_mib is None else "healthy-below-gate")
            return info

        if ok:
            # 健康但显存 ≥ 阈值：重试场景下是 ComfyUI 自己缓存的 H3 权重 → /free 卸载
            note(f"🔄 ComfyUI 在线但显存占用 {vram0}MiB ≥ {vram_gate_mib}MiB（缓存权重未释放）")
            info["actions"].append(f"探活 200 显存 {vram0}MiB → /free 卸载")
            if queue_running_count() > 0:
                note("🔄 ComfyUI 队列有任务在跑，先等排干（≤10 分钟）…")
                info["actions"].append("队列排干" + ("" if wait_queue_drain() else "（超时）"))
            free_memory()
            _sleep(3)
            vram1 = vram_used_mib()
            info["vram_after"] = vram1
            if vram1 is not None and vram1 < vram_gate_mib:
                info.update(ok=True, outcome="freed")
                note(f"✅ ComfyUI 缓存权重已卸载，显存回落 {vram1}MiB")
                return info
            note(f"⚠️ /free 后显存仍 {vram1}MiB，转入重启路径")
            info["actions"].append("/free 未奏效 → 转重启")

        reason = "探活失败" if not ok else "/free 未奏效"
        note(f"🔄 ComfyUI 转入重启路径（{reason}）…")
        pids = pids_on_port()
        if pids:
            for pid in pids:
                kill_tree(pid)
            info["actions"].append(f"杀僵尸 PID {','.join(pids)}")
            note(f"🔄 已清理 ComfyUI 僵尸进程（PID {','.join(pids)}），等显存释放…")
            _sleep(3)
        relaunch(tag=time.strftime("%Y%m%d%H%M%S"))
        info["actions"].append("无头重启 ComfyUI")
        note("🔄 ComfyUI 无头重启中，等待就绪…")
        healthy = wait_healthy()
        info["vram_after"] = vram_used_mib()
        info["ok"] = bool(healthy)
        info["outcome"] = "restarted-ok" if healthy else "restarted-fail"
        note("✅ ComfyUI 自愈完成，已就绪" if healthy
             else "⚠️ ComfyUI 重启后仍不健康（按连败协议叫人）")
    except Exception as e:  # noqa: BLE001 — 自愈绝不炸穿主流程
        info["ok"] = False
        info["outcome"] = f"error: {type(e).__name__}: {str(e)[:120]}"
        note(f"⚠️ ComfyUI 自愈过程异常（{info['outcome']}），按连败协议叫人")
    return info


def wait_vram_below(gate, timeout=90, interval=5):
    for _ in range(max(1, int(timeout / interval))):
        v = vram_used_mib()
        if v is None or v < gate:
            return True
        _sleep(interval)
    v = vram_used_mib()
    return v is None or v < gate


def shutdown_comfyui(notify=None, wait_vram=True):
    """流程收尾：清理硬件占用——杀掉 8188 监听 PID（ComfyUI 本体）并确认显存回落
    基线（VRAM_GATE_MIB）。无实例 → ok=True/not-running。
    2026-09-28 用户约定：流程最后执行完毕必须清硬件占用（否则僵尸/缓存占 ~13GB
    连累次日 9:00 报纸任务的 LLM 装载）。自身绝不抛异常。"""
    info = {"ok": False, "pids": [], "killed": False, "vram_before": vram_used_mib(),
            "vram_after": None, "outcome": "", "at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def note(text):
        log(text)
        if notify:
            try:
                notify(text)
            except Exception:  # noqa: BLE001 — 播报失败不影响清理
                pass

    try:
        pids = pids_on_port()
        info["pids"] = pids
        if not pids:
            info.update(ok=True, outcome="not-running", vram_after=info["vram_before"])
            return info
        note(f"🧹 流程收尾：清理 ComfyUI（PID {','.join(pids)}），把显卡还给系统…")
        for pid in pids:
            kill_tree(pid)
        info["killed"] = True
        _sleep(3)
        settled = wait_vram_below(VRAM_GATE_MIB, timeout=60, interval=5) if wait_vram else True
        v = vram_used_mib()
        info["vram_after"] = v
        info["ok"] = bool(settled)
        info["outcome"] = "cleaned" if settled else "cleaned-vram-not-settled"
        note(f"🧹 ComfyUI 已清理，显存 {v}MiB（基线 <{VRAM_GATE_MIB}MiB）"
             if settled else f"⚠️ ComfyUI 已清理但显存仍 {v}MiB（存在其他占用者，不影响已交付内容）")
    except Exception as e:  # noqa: BLE001 — 收尾清理绝不炸穿主流程
        info["ok"] = False
        info["outcome"] = f"error: {type(e).__name__}: {str(e)[:120]}"
        note(f"⚠️ 收尾清理异常（{info['outcome']}）")
    return info


def launch_detached(cmd_line):
    r"""WMI Win32_Process.Create 启动任意命令（属主 WMI 服务，脱离 interop job
    ——driver 退出/会话销毁都不影响子进程；09-28 实验 A/B 实锤 interop 直启
    与 Start-Process 脱离的子进程都活不过会话退出）。
    cmd_line 例: cmd /s /c ""C:\...\python.exe" "E:\...\script.py" arg > "log" 2>&1"
    自身绝不抛异常；真正存活由调用方轮询确认。"""
    try:
        ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
              f"-Arguments @{{CommandLine='{cmd_line.replace(chr(39), chr(39) * 2)}'}}; "
              "Write-Output ('WMI rc=' + $r.ReturnValue + ' pid=' + $r.ProcessId)")
        with open(HEAL_PS1_WSL, "w", encoding="utf-8-sig") as f:
            f.write(ps)
        subprocess.Popen([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          HEAL_PS1_WIN],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001
        return False
