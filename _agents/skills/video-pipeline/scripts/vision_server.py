#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vision_server.py — 本地判读服务生命周期（#102/#104）：探活→拉起→就绪等待→停止。

形态（ADR 0007）：Windows 侧 llama.cpp llama-server（CPU 构建 b11603），
Qwen3-VL Q4_K_M + mmproj F16，端口 8100（0.0.0.0，WSL 经网关访问），
CPU 常驻、零显存——与烧卡窗口（Strata/ComfyUI）无任何牵连。不开机自启；
无人值守由 driver s08 入口 ensure_online 自愈（T2 接线）。

CLI:
  python vision_server.py probe [--tier 4b]        # 探活（在线且 alias 匹配 → ok）
  python vision_server.py launch --tier 4b         # WMI 脱会话拉起（不等待）
  python vision_server.py ensure --tier 4b [--wait 300]   # 探活→拉起→就绪等待
  python vision_server.py stop                     # 停服务（盲比换档用）
  python vision_server.py url                      # 打印端点 URL

env: VISION_JUDGE_HOME（默认 E:\\AI\\LLM\\vision-judge）、VISION_JUDGE_PORT（8100）
"""
import json
import os
import subprocess
import time
import urllib.request

import hostpath  # 宿主判定 / 跨侧路径 / 解码的单一事实源（#143）
from vision_common import VISION_PORT, wsl_gateway

VISION_HOME = os.environ.get("VISION_JUDGE_HOME", r"E:\AI\LLM\vision-judge")
BIN = os.path.join(VISION_HOME, "bin", "llama-server.exe")
LOG = os.path.join(VISION_HOME, "llama-server.log")
# 三档定档配置（T1 盲比对象；定档后 DRIVER_VISION_MODEL 缺省取 alias）
TIERS = {
    "2b": {"model": "Qwen3VL-2B-Instruct-Q4_K_M.gguf",
           "mmproj": "mmproj-Qwen3VL-2B-Instruct-F16.gguf", "alias": "qwen3-vl-2b"},
    "4b": {"model": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
           "mmproj": "mmproj-Qwen3VL-4B-Instruct-F16.gguf", "alias": "qwen3-vl-4b"},
    "8b": {"model": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
           "mmproj": "mmproj-Qwen3VL-8B-Instruct-F16.gguf", "alias": "qwen3-vl-8b"},
}
# launch ps1 落 ASCII Temp 路径（comfyui_selfheal 同款：interop 传参纯 ASCII，
# -File 调用规避 WSL→Windows powershell -Command 挂起）
LAUNCH_PS1_WIN = os.environ.get(
    "VISION_SERVER_PS1_WIN", r"C:\Users\LMR\AppData\Local\Temp\vision_server_launch.ps1")
LAUNCH_PS1_WSL = os.environ.get(
    "VISION_SERVER_PS1_WSL", "/mnt/c/Users/LMR/AppData/Local/Temp/vision_server_launch.ps1")
# 单图最小 token 下限（ADR 0007「请求与部署口径」明文：--image-min-tokens 1024；
# b11603 对 Qwen-VL 的 grounding 精度下限警告 llama.cpp#16842）。0 = 关掉（A/B 对照用）：
# 2026-10-21 实测缺此项时 652 图 token，判官对翻转零判别力。
MIN_IMG_TOKENS = int(os.environ.get("VISION_JUDGE_MIN_IMG_TOKENS", "1024"))


def _is_wsl():
    """WSL 判定（#143 起委托 hostpath 单一实现；保留本名供旧调用点）。"""
    return hostpath.is_wsl()


def _wsl_path(p):
    """Windows 盘符路径 → WSL /mnt 路径。WSL 侧 os.path.exists() 判 Windows 盘符
    路径恒假（run_pipeline.py:2066 同款铁律）——2026-10-17 s08 判读全灭的根因之一：
    本模块的存在性检查直接拿 E:\\... 去 os.path.exists，永远走「缺文件」。"""
    return hostpath.to_host_path(p)


def _ps_path():
    """powershell.exe 双端解析：Windows 侧本机路径 / WSL 侧 /mnt/<drive> 互操作路径。"""
    cand = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"),
                        "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    wsl = _wsl_path(cand)  # 2026-10-17 修：旧实现 "/mnt/c" + cand[2:] 产出
    for p in (cand, wsl):  # '/mnt/c\\Windows/...'（反斜杠未转 /）→ 恒不存在
        if os.path.exists(p):
            return p
    return wsl


def _decode_out(p):
    """powershell/interop 输出容错解码：先 UTF-8，退 GBK，最后替换。
    2026-10-17 事故：capture_output=True, text=True 在 WSL 用 UTF-8 解
    powershell.exe 的 Windows 控制台代码页（GBK/936）输出 → UnicodeDecodeError
    ('utf-8' codec can't decode byte 0xb0) 直接炸 launch；只关心 ASCII 标记
    （rc=/pid=/done），容错解码即可。"""
    return hostpath.decode_output(p)


def tier_cfg(tier):
    t = (tier or os.environ.get("VISION_JUDGE_TIER", "4b")).lower()
    if t not in TIERS:
        raise SystemExit(f"未知档位 {t!r}（可选 {sorted(TIERS)}）")
    return t, TIERS[t]


def base_url():
    """服务 URL：WSL 内经网关连 Windows 主机（同 strata/vision_common 口径），
    Windows 侧 127.0.0.1；VISION_JUDGE_URL 永远可覆盖。"""
    return os.environ.get("VISION_JUDGE_URL",
                          f"http://{wsl_gateway() or '127.0.0.1'}:{VISION_PORT}")


def probe(expect_alias=None, timeout=4):
    """探活：/health 200 即在线；expect_alias 给定时校验 /v1/models 模型名。
    返回 {"online": bool, "alias": str|None, "detail": str}。"""
    try:
        with urllib.request.urlopen(f"{base_url()}/health", timeout=timeout) as r:
            if r.status != 200:
                return {"online": False, "alias": None, "detail": f"health {r.status}"}
    except Exception as e:  # noqa: BLE001
        return {"online": False, "alias": None, "detail": str(e)[:80]}
    alias = None
    try:
        with urllib.request.urlopen(f"{base_url()}/v1/models", timeout=timeout) as r:
            alias = json.loads(r.read().decode())["data"][0]["id"]
    except Exception as e:  # noqa: BLE001
        return {"online": True, "alias": None, "detail": f"models:{e}"[:80]}
    if expect_alias and alias != expect_alias:
        return {"online": True, "alias": alias,
                "detail": f"档位不符：在跑 {alias}，期望 {expect_alias}"}
    return {"online": True, "alias": alias, "detail": "ok"}


def launch(tier):
    """WMI Win32_Process.Create 拉起（属主 WMI 服务，脱离会话/interop job——
    Start-Process 子进程活不过会话退出，comfyui_selfheal 09-28 A/B 实锤）。
    返回 (ok, pid_or_err)。CPU 推理：--threads 16 = 7950X 物理核数（SMT 超订反伤吞吐）。"""
    t, cfg = tier_cfg(tier)
    mp, mm = os.path.join(VISION_HOME, "models", cfg["model"]), \
        os.path.join(VISION_HOME, "models", cfg["mmproj"])
    for fp in (BIN, mp, mm):
        if not os.path.exists(_wsl_path(fp)):  # WSL 侧必须转 /mnt 再判（见 _wsl_path）
            return False, f"缺文件: {fp}（先跑 scripts/vision-server-setup.sh）"
    args = (f'"{BIN}" -m "{mp}" --mmproj "{mm}" --alias {cfg["alias"]} '
            f"--host 0.0.0.0 --port {VISION_PORT} "
            "--ctx-size 16384 --parallel 2 --threads 16 --threads-batch 16 "
            "--ubatch-size 1024 "  # prefill 批宽（10-14 实测提速）
            f"--image-min-tokens {MIN_IMG_TOKENS} "  # ADR 0007 口径：b11603 对
            # Qwen-VL 的 grounding 精度下限警告（llama.cpp #16842）——缺此项时
            # 1088x1920 帧只落 ~652 图 token，判读退化成措辞复读（2026-10-21 实证）
            f"--no-webui --log-file \"{LOG}\"")
    # 直接裸命令行给 WMI（10-14 实证：cmd /s /c ""…"" 外层引号剥到内层——
    # rc=0 但进程秒死无日志；路径全无空格无需 cmd 包装，日志走 --log-file
    # 不需要重定向。pitfalls #54 坑3）
    ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{CommandLine='" + args.replace(chr(39), chr(39) * 2) + "'}; "
          "Write-Output ('rc=' + $r.ReturnValue + ' pid=' + $r.ProcessId)")
    try:
        ps1_win, ps1_wsl = LAUNCH_PS1_WIN, LAUNCH_PS1_WSL
        d = os.path.dirname(ps1_wsl)
        if os.path.isdir(d):
            with open(ps1_wsl, "w", encoding="utf-8-sig") as f:
                f.write(ps)
        else:  # Windows 侧直跑（无 /mnt/c）：直接写 Win Temp
            ps1_wsl = ps1_win
            with open(ps1_win, "w", encoding="utf-8-sig") as f:
                f.write(ps)
        # 2026-10-17 事故：WSL interop **不翻译**命令行参数里的 /mnt/c/…，
        # 传 ps1_wsl 会让 powershell 报「-File 形式参数的实际参数不存在」。
        # 两侧写的是同一个文件（/mnt/c == C:\），恒给 Windows 路径即可。
        p = subprocess.run([_ps_path(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", ps1_win], capture_output=True, timeout=30)
        out = _decode_out(p)
        if "rc=0" in out:
            pid = out.split("pid=")[-1].strip()
            return True, pid
        return False, out.strip()[-200:]
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def wait_ready(tier, timeout=300, interval=3):
    """就绪等待：模型载入内存期间 /health 503 → 轮询到 200 且 alias 匹配。"""
    t, cfg = tier_cfg(tier)
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = probe(expect_alias=cfg["alias"], timeout=4)
        if st["online"] and st["alias"] == cfg["alias"]:
            return {"ready": True, "elapsed": round(time.time() - t0, 1), "detail": st}
        time.sleep(interval)
    return {"ready": False, "elapsed": timeout, "detail": st}


def ensure_online(tier=None, wait=300, notify=None):
    """自愈阶梯（语义对齐 strata_window.ensure_online）：在线复用 /
    离线拉起一次→就绪等待 / 耗尽返回 failure dict（不抛——调用方降级留痕）。"""
    t, cfg = tier_cfg(tier)
    st = probe(expect_alias=cfg["alias"])
    if st["online"] and st["alias"] == cfg["alias"]:
        return {"online": True, "launched": False, **st}
    if st["online"] and st["alias"]:  # 在线但档位不符：换档是盲比专属动作，这里只报
        return {"online": False, "launched": False, **st,
                "detail": f"{st['detail']}（stop 后 ensure --tier 重拉）"}
    if notify:
        notify(f"[vision] 本地判读服务失联，自动拉起一次（{t}）…")
    ok, info = launch(t)
    if not ok:
        return {"online": False, "launched": False, "detail": f"拉起失败: {info}"}
    rd = wait_ready(t, timeout=wait)
    if rd["ready"]:
        if notify:
            notify(f"[vision] 本地判读服务就绪（{t}，载入 {rd['elapsed']}s）")
        return {"online": True, "launched": True, "load_s": rd["elapsed"],
                "alias": cfg["alias"], "detail": "ok"}
    return {"online": False, "launched": True, "detail": f"拉起后未就绪: {rd['detail']}"}


def stop():
    """停服务（判读窗口关闭 / 盲比换档）。按命令行精确匹配 `--port <VISION_PORT>`
    的 llama-server 进程清理——不能按映像名全杀：Strata 引擎同为 llama-server，
    全杀会误伤 8080（2026-10-14 窗口化改造，全本地模型统一用完即关）。幂等：
    无进程即 done，probe 报 offline。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
          f"Where-Object {{$_.CommandLine -like '*--port {VISION_PORT}*'}} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
          "Write-Output done")
    p = subprocess.run([_ps_path(), "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30)
    if "done" not in _decode_out(p):
        # CIM 查询异常（权限/WMIC 缺失）退回名字全杀——此时应无 Strata 在跑
        # （conflict 窗口已停）；保留兜底但不静默，日志可见
        subprocess.run([_ps_path(), "-NoProfile", "-Command",
                        "Get-Process llama-server -ErrorAction SilentlyContinue | "
                        "Stop-Process -Force; Write-Output done"],
                       capture_output=True, timeout=30)
    time.sleep(2)
    return {"stopped": True, "probe": probe()}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="本地判读服务生命周期")
    ap.add_argument("cmd", choices=["probe", "launch", "ensure", "stop", "url"])
    ap.add_argument("--tier", default=None, help="2b/4b/8b（缺省 env 或 4b）")
    ap.add_argument("--wait", type=int, default=300)
    a = ap.parse_args()
    if a.cmd == "url":
        print(base_url())
    elif a.cmd == "probe":
        print(json.dumps(probe(expect_alias=tier_cfg(a.tier)[1]["alias"]
                                if a.tier else None), ensure_ascii=False))
    elif a.cmd == "launch":
        ok, info = launch(a.tier)
        print(("launched pid=" if ok else "launch-failed ") + str(info))
    elif a.cmd == "ensure":
        print(json.dumps(ensure_online(a.tier, wait=a.wait), ensure_ascii=False))
    elif a.cmd == "stop":
        print(json.dumps(stop(), ensure_ascii=False))


if __name__ == "__main__":
    main()
