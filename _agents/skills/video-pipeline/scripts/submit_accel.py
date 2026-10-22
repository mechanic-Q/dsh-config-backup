#!/usr/bin/env python3
"""submit_accel.py — 用「▶▷MiniMaxH3-加速视频流整合」工作流串行生成 H3 视频。

流程：
  1. ui2api 转换工作流（覆盖 199 时长 + 194 prompt）
  2. powershell 桥接提交到 Windows ComfyUI
  3. 等待完成 → 拷贝到项目 480P 文件夹

用法：
  submit_accel.py <start> <end> <prompt_dir>
    prompt_dir: 项目 06_H3prompt/（每段 {id}.json: {prompt, video_seconds}）
"""
import json, os, sys, time, subprocess, random, re, shutil, glob, tempfile

# #151：双宿主口径——WSL 下 /mnt/e，Windows 下盘符直写（本仓脚本目录推断 ComfyUI 根）
if os.name == "nt":
    _COMFY_ROOT = r"E:\AI\Comfyui-WF-2026.8.8\Comfyui-WF-2026.8.8\ComfyUI"  # noqa: mechanical
    WORKFLOW = os.path.join(_COMFY_ROOT, "user", "default", "workflows", "▶▷MiniMaxH3-加速视频流整合.json")
    WIN_OUT = os.path.join(_COMFY_ROOT, "output", "video", "MiniMax_H3")
    WIN_OUT_ROOT = os.path.join(_COMFY_ROOT, "output")
    PROJ_OUT = r"E:\AI\Comfyui-WF-2026.8.8\minimax-h3-5080\480P"  # noqa: mechanical
    LOG = os.path.join(tempfile.gettempdir(), "generated_480P_test.jsonl")
else:
    WORKFLOW = "/mnt/e/AI/Comfyui-WF-2026.8.8/Comfyui-WF-2026.8.8/ComfyUI/user/default/workflows/▶▷MiniMaxH3-加速视频流整合.json"
    WIN_OUT = "/mnt/e/AI/Comfyui-WF-2026.8.8/Comfyui-WF-2026.8.8/ComfyUI/output/video/MiniMax_H3"
    WIN_OUT_ROOT = "/mnt/e/AI/Comfyui-WF-2026.8.8/Comfyui-WF-2026.8.8/ComfyUI/output"
    PROJ_OUT = "/mnt/e/AI/Comfyui-WF-2026.8.8/minimax-h3-5080/480P"
    LOG = "/home/lmr/comfy/generated_480P_test.jsonl"
SLUG = ""  # 项目前缀（默认空=不重命名，兼容旧用法）
HERE = os.path.dirname(os.path.abspath(__file__))  # ui2api 同目录（#110 起随仓走）
# ComfyUI 探活地址（走 Windows 侧端口代理，非 127.0.0.1；main 守卫与 wait_done
# 离线快速失败共用同一口径，避免两处各自写一个 URL）
PROBE_URL = "http://192.168.50.231:18188/system_stats"
# WSL 侧 curl 的绝对路径（经 /mnt/c 调 Windows 的 curl.exe）；只在非 nt 分支用
WSL_CURL = "/mnt/c/Windows/System32/curl.exe"  # noqa: mechanical


def _ps_utf8(cmd):
    r"""PowerShell 前缀：强制控制台/管道输出走 UTF-8。

    2026-10-20 实测：不设时中文异常信息按 cp936 吐字节，Python 按 utf-8 解出来是
    一串替换字符（"������ʱ��"），排障时等于没有信息（#124 铁律的镜像方向）。"""
    return ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$OutputEncoding = [Console]::OutputEncoding; " + cmd)


def ps(cmd, timeout=60):
    r"""调用 Windows PowerShell（WSL 用完整 System32 路径；Windows 直接 PATH 解析）。

    #151 双宿主口径，2026-10-20 补齐 Windows 侧（此前只有 WSL 口径，Windows
    driver 一到 s08 就 WinError 2 整期退场）：
    Windows 下走 argv 直调（shell=False，不经过 cmd.exe），命令体作为 -Command 的
    单个实参——因此 **绝不能转义 $ 和双引号**。此前的转义分支把 "\$body" 变成
    字面量变量名，Invoke-WebRequest 拿不到请求体却静默返回，属最难查的一类失败。
    """
    PS = ("powershell.exe" if os.name == "nt"
          else "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if os.name == "nt":
        full = [PS, "-NoProfile", "-Command", _ps_utf8(cmd)]
        r = subprocess.run(full, capture_output=True, text=True,
                           timeout=timeout, encoding='utf-8', errors='replace')
    else:
        # WSL：经 bash 解析，'"' 会先被 bash 吃掉一层，故 $ 与 " 必须转义。
        if "'" in cmd:
            escaped = cmd.replace('$', '\\$').replace('"', '\\"')
            full = f"{PS} -NoProfile -Command \"{escaped}\""
        else:
            full = f"{PS} -NoProfile -Command '{cmd}'"
        r = subprocess.run(full, shell=True, capture_output=True, text=True,
                           timeout=timeout, encoding='utf-8', errors='replace')
    return r.stdout + r.stderr


def _curl_probe_cmd():
    """返回 (curl, sink)：Windows 用 curl.exe + NUL，WSL 用 /mnt/c 绝对路径 + /dev/null。"""
    if os.name == "nt":
        return "curl.exe", "NUL"
    return (WSL_CURL if os.path.exists(WSL_CURL) else "curl"), "/dev/null"


def _probe_online(timeout=8):
    """探活 ComfyUI（与 main 守卫同口径）。返回 True/False，绝不抛。

    2026-10-20 实测：ComfyUI 出片后进程猝死，wait_done 仍每轮 20s 空转读 history，
    直到 2h 超时才退出——期间 driver 与人都以为「还在生成」。
    """
    import subprocess as _sp
    curl, sink = _curl_probe_cmd()
    try:
        r = _sp.run([curl, "-s", "-m", str(int(timeout)), "-o", sink, "-w", "%{http_code}",
                     PROBE_URL], capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
        return r.stdout.strip() == "200"
    except Exception:
        return False


def _find_new_output(pid, since):
    """在 ComfyUI output 树里找该段 after=since 之后的产物（按 mtime 取最新）。

    history 读不到时的兜底判据：产物文件才是完成状态的单一事实源
    （与 main 里 [SKIP] 的口径一致）。
    """
    if not pid:
        return ""
    try:
        hits = [f for f in glob.glob(os.path.join(WIN_OUT_ROOT, "**", f"*{pid}*.mp4"),
                                     recursive=True)
                if os.path.getmtime(f) >= since]
    except Exception:
        return ""
    return sorted(hits, key=os.path.getmtime)[-1] if hits else ""


def fix_models(api, wf=None):
    """只保留 395 VHS_VideoCombine 主链路节点（反向 BFS），其余移除 + 模型名修正。

    2026-10-13 工作流改版后主链：194 T2V → 213 视频VAE + 208 音频VAE → 209
    CreateVideo → 394 DLSSNR_Video(2x 放大) → 395 出片；模型链 214 UNET → 399
    SparkAttentionSM120 → 205 LoRA。旧 193 SaveVideo 输出已 bypass 断开（当晚
    UI 试跑曾走它，终版弃用）；音频链虽生成但 395.audio 未接，出片无音轨。
    """
    # 模型名映射（工作流引用 → 实际存在）——LoRA 原值（反斜杠）就是正确的，不映射
    model_fix = {
        "Minimax_H3\\minimax_h3_fl2va_pruned_int8_convrot.safetensors": "minimax_h3_fl2va_int8_convrot.safetensors",
        "minimax_h3_fl2va_pruned_int8_convrot.safetensors": "minimax_h3_fl2va_int8_convrot.safetensors",
        "qwen3vl_32b_minimax_h3_int8_convrot.safetensors": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        # LoRA 实际在 loras/ 根目录（无 minimax_h3 子目录），工作流反斜杠子目录引用会 400
        "minimax_h3\\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors": "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors",
        "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors": "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors",
    }
    # 194 主链路（395 VHS_VideoCombine 反向 BFS）。
    from collections import deque
    visited = set()
    q = deque(["395"])
    while q:
        nid = q.popleft()
        if nid in visited or nid not in api:
            continue
        visited.add(nid)
        for k, v in api[nid]["inputs"].items():
            if isinstance(v, list) and len(v) == 2:
                q.append(str(v[0]))
    # bypass 直通重接：UI bypass(mode=4) 节点转成 API 后没有 mode 概念，会被当
    # 真节点执行。现主链内 399.model ← 210(bypass 的 SageAttention) ← 214，UI
    # 执行时 210 直通放行；API 必须改接到 214，否则 SparkAttention 前多套一层 patch。
    if wf is not None:
        bypassed = {str(n["id"]) for n in wf["nodes"] if n.get("mode") == 4}
        for _ in range(len(visited) + 1):
            rewired = False
            for nid in visited:
                for k, v in api[nid]["inputs"].items():
                    if isinstance(v, list) and len(v) == 2 and str(v[0]) in bypassed:
                        src = next((x for x in api[str(v[0])]["inputs"].values()
                                    if isinstance(x, list) and len(x) == 2), None)
                        if src is None:
                            raise ValueError(
                                f"[BYPASS] {nid}.{k} 指向 bypass 节点 {v[0]}，"
                                "后者无 link 输入可直通——工作流接线有误，请检查")
                        api[nid]["inputs"][k] = list(src)
                        rewired = True
            if not rewired:
                break
        visited -= bypassed
    # 移除非主链路节点
    for nid in [n for n in api if n not in visited]:
        del api[nid]
    # 主链完整性校验：剪枝后任何 link 指向链外节点都会在 ComfyUI 侧 400（工作
    # 流后续再改版时把静默失败变成可读错误）
    for nid in visited:
        for k, v in api[nid]["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and str(v[0]) not in visited:
                raise ValueError(f"[CHAIN] {nid}.{k} 指向主链外节点 {v[0]}（被剪或不存在）")
    # 剪掉 QwenTE 提示增强链（194.prompt 被覆盖后 239/240/252/253/254 是死链，省显存）
    for nid in ["239", "240", "252", "253", "254"]:
        if nid in api:
            del api[nid]
    # 模型名修正
    for nid, node in api.items():
        for k, v in node["inputs"].items():
            if isinstance(v, str) and v in model_fix:
                node["inputs"][k] = model_fix[v]
    return api


def convert_workflow(prompt_text, duration):
    """ui2api 转换 + 覆盖 199(时长) + 194(prompt)。"""
    # ui2api 单一事实源 = 本脚本同目录份（曾指向 hermes 旧克隆：两处内容恰好
    # 一致才没出事，但克隆停在 9 月中已与仓库漂移——2026-10-13 改为随脚本走，
    # /home/lmr/comfy/ui2api.py 是更早的旧副本，均不再引用）
    import importlib.util
    _HERE = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("ui2api", f"{_HERE}/ui2api.py")
    ui2api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui2api)
    wf = json.load(open(WORKFLOW, encoding='utf-8'))
    api = ui2api.convert(wf)
    api = fix_models(api, wf)
    # 覆盖 199 时长
    api["199"]["inputs"]["value"] = duration
    # 覆盖 194 prompt（T2V 主分支）——2026-10-13 改版后 194.prompt 是直连 widget
    # （TE 增强链 239/240/252/253/254 已全部 bypass 且不再进主链），直接赋值即可
    api["194"]["inputs"]["prompt"] = prompt_text
    # 输出文件名带 pid（395 VHS_VideoCombine 的 filename_prefix，输出 1080P/DLSS 链）
    # 自适应：相对路径 480P/ 输出 + 项目前缀（如 480P/icehouse-hyperthermal-event_shot01）
    # ComfyUI 输出到 output/480P/，拷贝阶段再转到稿子同级 480P/
    prefix = os.environ.get("CUR_PID", "shot")
    if SLUG:
        prefix = f"{SLUG}_{prefix}"
    api["395"]["inputs"]["filename_prefix"] = "480P/" + prefix

    # === T08: prompt 覆盖校验（防静默失败）===
    # 特征串必须选"所有 prompt 结构上必有"的串（转译脚本固定写入的字段头），
    # 不能用风格词（paper collage/celestial 会随风格切换失灵，误杀合法 prompt）
    marker = os.environ.get("H3_MARKER", "integrated_multimodal_description")
    actual_prompt = api["194"]["inputs"].get("prompt", "")
    if marker not in actual_prompt.lower():
        raise ValueError(
            f"[OVERRIDE_CHECK] 194 prompt 覆盖校验失败: 不含特征串 '{marker}'\n"
            f"实际 prompt 前 100 字: {actual_prompt[:100]!r}"
        )
    # 确认不是工作流默认值（"精灵女子"示例）：只要含我们的 marker 就是覆盖成功
    if "精灵女子" in actual_prompt and marker not in actual_prompt and "integrated_multimodal_description" not in actual_prompt:
        raise ValueError("[OVERRIDE_CHECK] 194 prompt 疑似为工作流默认值（未覆盖成功）")
    return api


def submit(api):
    # #151 双宿主：JSON 落各自宿主临时目录；Windows 直写 C:\Users\...\Temp（ps cmd
    # 里的 'C:\Users\...' 字面量与之同址）。WSL 保留 cp 到 /mnt/c 语义。
    if os.name == "nt":
        tmp_json = os.path.join(tempfile.gettempdir(), "api_accel.json")
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump({"prompt": api}, f, ensure_ascii=False)
    else:
        tmp_json = "/tmp/api_accel.json"
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump({"prompt": api}, f, ensure_ascii=False)
        subprocess.run(["cp", tmp_json,
                        "/mnt/c/Users/LMR/AppData/Local/Temp/api_accel.json"], check=True)  # noqa: mechanical
    cmd = (
        "$body = [System.IO.File]::ReadAllBytes('C:\\Users\\LMR\\AppData\\Local\\Temp\\api_accel.json'); "
        "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/prompt' -Method Post -Body $body "
        "-ContentType 'application/json' -TimeoutSec 15 -UseBasicParsing; Write-Host $r.Content } "
        "catch { Write-Host ('ERROR ' + $_.Exception.Message); if ($_.ErrorDetails) { Write-Host $_.ErrorDetails.Message } }"
    )
    return ps(cmd)


def wait_done(prompt_id, timeout=7200, pid=None):
    """等 ComfyUI 出片。返回 (status, completed, found_file)。

    2026-10-20 实测缺陷：ComfyUI 出片后进程猝死（GPU 4 次迭代跑完、写盘完成，
    随后整进程消失），history 是内存态一并丢失，本循环仍每轮 sleep 20s 读
    history，直到 2h 超时才放弃——driver 侧表现为「提交 shot05」后一动不动，
    人无法判断是慢还是死。现加离线快速失败：
      - 每轮同时探活；连续 3 次探活失败（约 60s）即判离场；
      - 离场前先在 output 树里找该段本次的产物（产物文件才是完成状态事实源），
        找到就当成功处理（history 丢失但片子已在盘上），找不到才算失败。
    """
    start = time.time()
    offline = 0
    while time.time() - start < timeout:
        out = ps(f"$h = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/history/{prompt_id}' -UseBasicParsing -TimeoutSec 5; $h.Content")
        try:
            h = json.loads(out.strip())
            if h and prompt_id in h:
                st = h[prompt_id].get("status", {})
                return st.get("status_str"), st.get("completed"), ""
        except Exception:
            pass
        offline = offline + 1 if not _probe_online() else 0
        if offline >= 3:
            hit = _find_new_output(pid, start - 120)
            if hit:
                print(f"[OFFLINE] ComfyUI 已离线，但找到产物（按产物文件判完成）: {hit}")
                return "OFFLINE_RECOVERED", True, hit
            print("[OFFLINE] ComfyUI 已离线且无产物（连续 3 次探活失败）→ 快速失败")
            return "OFFLINE", False, ""
        time.sleep(20)
    return "TIMEOUT", False, ""


def queue_status():
    """读 ComfyUI 队列。解析失败时返回 {}（不是伪造「忙」）。

    旧实现解析失败一律返回 {"queue_running": [1]}，把「ComfyUI 离线/输出不可
    解析」伪装成「队列在跑」——主循环于是在 :412 无限 [WAIT]，永远不报错。
    现在返回空 dict 由调用方判定：{} 表示未知，调用方须自行探活。
    """
    out = ps("$q = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/queue' -UseBasicParsing -TimeoutSec 5; $q.Content")
    out = out.strip().lstrip('\ufeff')
    s, e = out.find('{'), out.rfind('}')
    if s >= 0 and e > s:
        try:
            return json.loads(out[s:e+1])
        except Exception:
            return {}
    return {}


def free_memory():
    cmd = (
        "try { $body = '{\"unload_models\": true, \"free_memory\": true}'; "
        "$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/free' -Method Post -Body $body "
        "-ContentType 'application/json' -UseBasicParsing -TimeoutSec 15; "
        "if ($r.StatusCode -eq 200) { Write-Host 'OK' } else { Write-Host ('HTTP ' + $r.StatusCode) } } "
        "catch { Write-Host ('ERR ' + $_.Exception.Message) }"
    )
    return "OK" in ps(cmd)


def verify_history_prompt(prompt_id, expected_marker, timeout=30):
    """T08: 提交后查 history，确认实际执行的 prompt 含我们的特征串。"""
    marker = os.environ.get("H3_MARKER", "integrated_multimodal_description")
    start = time.time()
    while time.time() - start < timeout:
        out = ps(f"$h = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/history/{prompt_id}' -UseBasicParsing -TimeoutSec 5; $h.Content")
        try:
            h = json.loads(out.strip())
            if h and prompt_id in h:
                # 从 history 的 prompt 字段检查
                hist_prompt = json.dumps(h.get(prompt_id, {}).get("prompt", {}), ensure_ascii=False)
                return marker in hist_prompt.lower()
        except Exception:
            pass
        time.sleep(5)
    return False


def _find_gpu_llama():
    """同卡互斥守卫（#151 双宿主）：列出带 --n-gpu-layers 的 llama-server 进程行。

    Windows 侧没有 pgrep/pkill（2026-10-20 实测：Windows driver 一进 s08 就
    FileNotFoundError WinError 2，整期卡死两次退场）——Windows 走
    Get-CimInstance Win32_Process，WSL 保留 pgrep 口径。
    """
    if os.name == "nt":
        out = ps("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" "
                 "| Select-Object -ExpandProperty CommandLine")
        return [l for l in (out or "").splitlines() if "--n-gpu-layers" in l]
    import subprocess as _sp
    # pgrep 属 procps，纯 Linux 容器/精简镜像里可能没有；没有工具就没有 WSL 侧
    # 的 llama-server 可言，显式跳过（2026-10-20 CI 实证：裸调会 FileNotFoundError
    # 把入口冲掉，和 netstat.exe 同型）。
    if not shutil.which("pgrep"):
        print("[GUARD] 跳过 GPU llama-server 守卫：本机没有 pgrep")
        return []
    r = _sp.run(["pgrep", "-af", "llama-server"], capture_output=True,
                encoding="utf-8", errors="replace")
    return [l for l in (r.stdout or "").splitlines() if "--n-gpu-layers" in l]


def _kill_gpu_llama(lines):
    """停掉上一步找到的 GPU 版 llama-server（只停 GPU 版；CPU 版 8887 不占显存不动）。"""
    if os.name == "nt":
        # 从命令行回推 pid：CommandLine 里没有 pid，用同名进程逐个核
        out = ps("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" "
                 "| ForEach-Object { if ($_.CommandLine -like '*--n-gpu-layers*') "
                 "{ Write-Host $_.ProcessId } }")
        for tok in (out or "").split():
            if tok.strip().isdigit():
                import subprocess as _sp
                _sp.run(["taskkill.exe", "/F", "/T", "/PID", tok.strip()],
                        capture_output=True)
        return
    import subprocess as _sp
    _sp.run(["pkill", "-f", "llama-server.*--n-gpu-layers"], capture_output=True)


def main():
    start, end = int(sys.argv[1]), int(sys.argv[2])
    prompt_dir = sys.argv[3]
    # 自适应项目目录（用户 2026-09-02 要求）：
    # H3 直出 1080 → 输出到"稿子所在目录/07_成片/1080P/"，成片文件夹内按清晰度管理（不再有独立 480P/）
    # 用法：submit_accel.py <start> <end> <prompt_dir> --script <稿子路径> [--res 1080P]
    global LOG, PROJ_OUT, SLUG
    res_dir = "1080P"
    if "--res" in sys.argv:
        res_dir = sys.argv[sys.argv.index("--res") + 1]
    script_path = None
    if "--script" in sys.argv:
        script_path = sys.argv[sys.argv.index("--script") + 1]
    if script_path and os.path.isfile(script_path):
        # 稿子目录 = script_path 所在目录；若该目录是 00_口播稿（新11阶段），项目根取其父目录
        script_dir = os.path.dirname(os.path.abspath(script_path))
        if os.path.basename(script_dir) in ("00_口播稿", "00_口播稿/", "00_口播稿"):
            script_dir = os.path.dirname(script_dir)
        stage7 = os.path.join(script_dir, "07_成片")
        PROJ_OUT = os.path.join(stage7, res_dir)
        os.makedirs(PROJ_OUT, exist_ok=True)
        LOG = os.path.join(stage7, "generated.jsonl")
        base = os.path.basename(script_path)
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", base)
        slug = re.sub(r"-稿\.md$|\.md$|-口播稿\.md$", "", slug)
        if not slug or slug == "口播稿":
            # 文件名退化（如 口播稿.md）→ 用项目根目录名作 slug（如 daily-china-20260901）
            slug = os.path.basename(script_dir)
        SLUG = slug
        print(f"[PROJ] 稿子: {script_path}")
        print(f"[PROJ] H3 输出: {PROJ_OUT}（{res_dir}）")
        print(f"[PROJ] 记录文件: {LOG}")
        print(f"[PROJ] 项目前缀: {SLUG}")
    else:
        # 兼容旧用法：prompt_dir 上一级 / 480P
        _pd = os.path.abspath(prompt_dir)
        _proj = os.path.dirname(_pd)
        if os.path.basename(_pd) in ("06_H3prompt", "06_H3prompt", "04_H3prompt", "h3_prompts") and os.path.isdir(_proj):
            PROJ_OUT = os.path.join(_proj, "480P")
            os.makedirs(PROJ_OUT, exist_ok=True)
            LOG = os.path.join(_proj, "generated_480P.jsonl")
            print(f"[PROJ] 产物目录: {PROJ_OUT}")
            print(f"[PROJ] 记录文件: {LOG}")
            SLUG = os.path.basename(_proj)
    # ── 同卡互斥守卫（硬约束#1，2026-09-08 前置到烧卡入口）──────────────
    # H3 offload 峰值 ~30GB vs 16GB 显存；LLM 服务常驻 ~15GB 时显存↔内存倒腾，
    # 每段 87s 拖到 5-6 分钟（0907 期实锤，132min/22段）。守卫原先只在 driver
    # s08 内——agent 分步/手动烧卡时无人执行。
    # #85 起驻留形态两个：Strata（Windows 8080，python server + strata.exe，不叫
    # llama-server）与残余 llama-server（WSL 侧已无人拉，守卫保留防手工残留）。
    # driver 路径下 s08 入口已停 Strata，此处幂等兜底（conflict 窗口外手动烧卡用）。
    import subprocess as _sp
    _gpu_llama = _find_gpu_llama()
    if _gpu_llama:
        print("[GUARD] ⚠️ 检测到 GPU 版 llama-server 驻留（与 H3 同卡互斥，硬约束#1）")
        print("[GUARD] 自动停掉以释放显存（0907 期未清时 87s/段 → 6min/段）")
        _kill_gpu_llama(_gpu_llama)
        import time as _t
        _t.sleep(3)
    # netstat.exe / taskkill.exe 只在有 Windows interop 的宿主存在（Windows 原生，
    # 或 WSL 的 Windows PATH 透传）。纯 Linux 宿主（CI、容器）没有它们——
    # 2026-10-20 CI 实测：裸调 netstat.exe 直接 FileNotFoundError 把入口冲掉，
    # 连「ComfyUI 不可达」的快速失败都到不了。缺工具时**显式跳过并留痕**，
    # 不能静默 pass（否则又是「失败伪装成正常」）。
    _netstat_exe = shutil.which("netstat.exe") or shutil.which("netstat")
    _strata_pids = []
    if _netstat_exe:
        _ns = _sp.run([_netstat_exe, "-ano"], capture_output=True, timeout=25)
        # text=False：netstat.exe 输出本地代码页（中文 Win=GBK），utf-8 强解炸读线程
        _netstat_out = ((_ns.stdout or b"") + (_ns.stderr or b"")).decode("utf-8", "replace")
        _strata_pids = sorted({l.split()[-1] for l in _netstat_out.splitlines()
                               if ":8080 " in l and "LISTENING" in l.upper()
                               and l.split()[-1].isdigit()})
    else:
        print("[GUARD] 跳过 Strata 端口守卫：本机没有 netstat.exe/netstat"
              "（非 Windows 宿主，8080 上不可能有 Windows 侧 Strata）")
    if _strata_pids:
        print(f"[GUARD] ⚠️ 检测到 Strata 8080 监听（pid {_strata_pids}，"
              "与 H3 同卡互斥，硬约束#1）")
        print("[GUARD] 自动 taskkill 释放显存（conflict 窗口纪律；"
              "driver 路径 s08 入口已停过，此处幂等）")
        for _p in _strata_pids:
            _sp.run(["taskkill.exe", "/F", "/T", "/PID", _p], capture_output=True)
        import time as _t
        _t.sleep(3)
    try:
        _q = _sp.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv=noheader,nounits"],
                     capture_output=True, text=True, timeout=15)
        _used = int(_q.stdout.strip().splitlines()[0])
        if _used > 6000:
            print(f"[GUARD] ⚠️ 烧卡前显存已占用 {_used}MiB（ComfyUI 自身加载除外的话请检查 Windows 侧）")
    except Exception:
        pass
    # ── 守卫结束 ──────────────────────────────────────────────────────────
    # ComfyUI 探活：连不上快速失败（此前会挂起在提交/轮询，浪费时间不可考）
    # #151 双宿主：Windows 侧 curl.exe + NUL；WSL 侧 /mnt/c 路径 + /dev/null。
    _curl, _sink = _curl_probe_cmd()
    _rc = _sp.run([_curl, "-s", "-m", "8", "-o", _sink, "-w", "%{http_code}",
                   PROBE_URL],
                  capture_output=True, text=True, encoding="utf-8", errors="replace")
    if _rc.stdout.strip() != "200":
        print(f"[FAIL] ComfyUI (192.168.50.231:18188) 不可达 (HTTP {_rc.stdout.strip()})。"
              "请在 Windows 侧启动绘世启动器/ComfyUI 后重试。")
        sys.exit(3)
    print("[PROJ] ComfyUI 在线 OK")



    # 2026-09-08 根治：产物完成状态单一事实源 = 产物文件本身。
    # generated.jsonl 此前也作跳过判据（双状态源必漂移）：翻转重生成删了产物，
    # 这里仍按记录 SKIP——0906 期 4 段歪镜头漏进已发布成片的事故同源。
    # jsonl 自此降级为纯审计日志（仍追加，不参与判断）。
    failed = []
    for n in range(start, end + 1):
        pid = f"shot{n:02d}"
        if glob.glob(os.path.join(PROJ_OUT, f"*{pid}*.mp4")):
            print(f"[SKIP] {pid} 产物已存在")
            continue
        pf = os.path.join(prompt_dir, f"{pid}.json")
        if not os.path.exists(pf):
            print(f"[MISS] {pid} no prompt file")
            failed.append(pid)
            continue
        shot = json.load(open(pf, encoding="utf-8"))
        # 队列确认（未知/离线不当作「忙」：旧逻辑会在这里无限 [WAIT]）
        q = queue_status()
        _q_offline = 0
        while q.get("queue_running"):
            print(f"[WAIT] {pid} queue busy...")
            time.sleep(30)
            q = queue_status()
            if not q:
                _q_offline += 1
                if _q_offline >= 2 and not _probe_online():
                    print(f"[FAIL] {pid} 排队时 ComfyUI 已离线，放弃本段（不无限等）")
                    break
        # 2026-09-11 用户实证：段间不要清缓存——ComfyUI 会把已分级权重留在 RAM，
        # 连续段同结构热执行不重载（人工操作改提示词连跑即是此理）。
        # 旧设计每段 POST /free 全量卸载，逼出每段 60-140s 的重复装载。
        # 仅在换项目/换工作流结构时才需要 /free（当前单项目串行无此需求）。
        os.environ["CUR_PID"] = pid
        # === T08: 提交前覆盖校验（失败自动重试 2 次）===
        api = None
        for attempt in range(3):
            try:
                api = convert_workflow(shot["prompt"], shot["video_seconds"])
                break
            except ValueError as e:
                print(f"[OVERRIDE_RETRY] {pid} attempt {attempt+1}/3: {e}")
                if attempt == 2:
                    print(f"[FAIL] {pid} override check failed after 3 attempts, skip")
                    api = None
                    break
        if api is None:
            failed.append(pid)
            continue
        resp = submit(api)
        m = re.search(r'"prompt_id"\s*:\s*"([^"]+)"', resp)
        if not m:
            print(f"[FAIL] {pid} submit error: {resp[:300]}")
            failed.append(pid)
            continue
        pid_id = m.group(1)
        # === T08: 提交后 history 验证实际执行的 prompt ===
        print(f"[RUN] {pid} submitted ({shot['video_seconds']}s) id={pid_id}")
        verify = verify_history_prompt(pid_id, shot["prompt"])
        if not verify:
            print(f"[WARN] {pid} history prompt 与提交不一致（可能未生效），继续但标记")
        status, completed, found = wait_done(pid_id, pid=pid)
        if completed:
            # 离线兜底路径：history 已随进程消失，产物在盘上（filename 就是唯一线索）
            new_files = [found] if found else []
            # 只拷贝本次 prompt_id 的实际输出（防跨项目残留混入）
            hist = "" if found else ps(f"$h = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/history/{pid_id}' -UseBasicParsing -TimeoutSec 5; $h.Content")
            hist = hist.strip().lstrip('\ufeff')
            try:
                h = json.loads(hist[hist.find('{'):hist.rfind('}')+1])
                outs = h.get(pid_id, {}).get("outputs", {})
                for node_id, node_out in outs.items():
                    for f in node_out.get("gifs", []) + node_out.get("videos", []):
                        fn = f.get("filename", "")
                        if fn:
                            # filename 是相对 ComfyUI output 根的路径（如 480P/xxx.mp4 或 video/MiniMax_H3/xxx.mp4）
                            # 注意：subfolder 单独返回（如 480P），需拼接
                            sub = f.get("subfolder", "")
                            rel = os.path.join(sub, fn).replace("\\", "/") if sub else fn
                            new_files.append(os.path.join(WIN_OUT_ROOT, rel.lstrip("/\\")))
            except Exception:
                new_files = []
            if not new_files:
                # 兜底：按 pid 匹配（兼容带项目前缀名）+ 最近 10 分钟产出（全 output 树）
                import time as _t
                cutoff = _t.time() - 600
                new_files = [f for f in glob.glob(os.path.join(WIN_OUT_ROOT, "**", f"*{pid}*"), recursive=True)
                             if os.path.getmtime(f) >= cutoff]
            copied = []
            for f in sorted(set(new_files), key=os.path.getmtime):
                try:
                    src_name = os.path.basename(f)
                    # 输出文件名加项目前缀，防跨项目同名混淆（如 icehouse-20260812_shot01_00006_.mp4）
                    # 若源文件已带 SLUG 前缀（工作流 filename_prefix 已注入），不重复加
                    if SLUG and not src_name.startswith(SLUG + "_"):
                        dst_name = f"{SLUG}_{src_name}"
                    else:
                        dst_name = src_name
                    dst = os.path.join(PROJ_OUT, dst_name)
                    shutil.copy2(f, dst)
                    copied.append(dst)
                except Exception as e:
                    print(f"[COPY_WARN] {e}")
            rec = {"id": pid, "prompt_id": pid_id, "status": status,
                   "outputs": sorted(new_files), "copied": copied}
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if not copied:
                print(f"[FAIL] {pid} 无产物可拷（history 与 output 树都没找到）")
                failed.append(pid)
            else:
                print(f"[OK] {pid}: {len(new_files)} 个新输出 → copied {len(copied)}")
        else:
            # 失败必须反映到退出码：此前 [WARN] 后继续下一段，driver 侧看到 rc=0
            # 以为成功，产物缺失要等 s08 末尾完整性核对才暴露（掩盖了真因）。
            print(f"[WARN] {pid} status={status}")
            failed.append(pid)
    return failed


if __name__ == "__main__":
    failed_shots = main()
    if failed_shots:
        print(f"[FAIL] 本次有段未成功: {failed_shots}")
        sys.exit(1)
