#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preflight.py — driver 环境护栏预检（#46，CONTEXT.md「环境预检」词条）。

driver 进程启动即执行：任何环境故障在进入流水线前 fail-fast 叫人，而不是烧完
几十分钟云端调用后才在 s05/s08 暴露（2026-09-21 期实测：CRLF 让 s05 连败退出时
s01-s04 已烧 45 分钟）。

范围＝环境护栏 + skill 静态资产（#52 新纳，词条边界扩展归 #53；每日资产——报纸图/片头——仍归 new_daily 与 s09）：
  1. shell 脚本行尾：发现 CRLF ＝ 工作区被本地工具改脏 → 叫人（不自动改，#46 拍板）
  2. shell 脚本 bash -n 语法检查
  3. ComfyUI 队列接口可达
  4. 飞书凭证存在（黑盒复用 FeishuGate 同一校验，与 Driver 后续构造零漂移）
  5. 翻页音效资产在位（缺失会让 compose 静默回退合成纸声，听审才暴露，#52）
  6. KVMem 压缩车道静态检查已随 #85 删除（kvmem_window 模块整体退役；Strata 在
     Windows 侧无 WSL 面缺件可查，其在线性由 s04 入口 strata_window.ensure_online
     自愈阶梯接管——启动预检不拦，见 run_pipeline.main 注释）
  qwen /health 探活与自动重启已随 #86 全链切 Strata 退役。

失败行为统一（调用方负责）：飞书叫人（消息附具体失败项与修法）+ 退出，stamps 不动。

用法（模块）：
    from preflight import check_static, human_message
    failures = check_static(HERE, COMFY_QUEUE_URL)

用法（CLI 自检，只跑静态检查，不发飞书）：
    python3 scripts/preflight.py
"""
import collections
import glob
import os
import subprocess
import sys
import urllib.request

# Windows 侧 ComfyUI 队列接口（submit_accel/gen_krea2_cover 同一服务）；
# 预检只验证连通（GET 200），不校验队列内容
COMFY_QUEUE_URL = os.environ.get("DRIVER_COMFY_QUEUE", "http://192.168.50.231:18188/queue")

# 翻页音效是 skill 静态资产（#52）：*.wav 曾被 gitignore 挡在库外，同步后缺失让
# compose_final 静默回退合成纸声，听审才暴露（0922 实证废整轮 GPU 装配+烧录+送审）
SFX_FLIP_REL = os.path.join("assets", "sfx", "page_flip_bigbook.wav")
SFX_FLIP_CDN_URL = "https://cdn.freesound.org/previews/726/726518_3199189-hq.mp3"

Failure = collections.namedtuple("Failure", "kind detail fix")

KIND_FEISHU_CREDS = "feishu-creds"  # 唯一跨文件比较的 kind（main 判断"能否飞书叫人"）


def _run(argv, timeout=None, stdin_payload=None):
    """子进程直调。超时/找不到可执行文件都转成 (非 0, 说明) 而不是抛异常 ——
    护栏自己是排查工具，不能反过来成为崩溃源（2026-10-20 实体：Windows 原生
    driver 从某些父进程链起时 bash.exe 挂死，TimeoutExpired 裸抛把整期冲掉，
    traceback 里连「哪个脚本坏了」都看不出来）。"""
    try:
        p = subprocess.run(argv, capture_output=True, text=False, timeout=timeout,
                           input=stdin_payload)
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（{timeout}s 未返回）: {' '.join(argv)}"
    except FileNotFoundError:
        return 127, f"命令不存在: {' '.join(argv)}"
    out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", errors="replace").strip()
    return p.returncode, out


def find_shell_scripts(scripts_dir):
    """只扫 driver 运行面（scripts/ 下 *.sh，非递归）。_test/ 等非运行面的 .sh
    不在护栏内——它们不会让本次运行失败，脏了由 .gitattributes 源头根治层兜住。"""
    return sorted(glob.glob(os.path.join(scripts_dir, "*.sh")))


def scan_crlf(scripts):
    """字节级扫 \\r\\n——行尾不属文本语义，解码/编码都不该碰它。"""
    hits = []
    for p in scripts:
        with open(p, "rb") as f:
            if b"\r\n" in f.read():
                hits.append(p)
    return hits


def check_bash_syntax(scripts, run=None):
    """逐个 bash -n，返回 {路径: 错误输出}（空 dict = 全过）。run 可注入。
    Windows（os.name == 'nt'）下走 stdin 喂脚本内容：Git Bash/MSYS 的 bash 对
    盘符路径文件参数一律找不到（`C:\\a` 反斜杠被当转义吞掉，`C:/a` 也不做 MSYS
    转换，2026-10-12 实测 rc=127 误报拦死 driver），stdin 绕开路径语义且对 WSL
    bash 同样成立；错误 dict 的键保持调用方传入的原路径（对账键不漂移）。
    stdin 形态的报错无文件名前缀（bash 只报 line N），文件名由本函数的键承载。"""
    run = run or _run
    errors = {}
    for p in scripts:
        if os.name == "nt":
            with open(p, "rb") as f:
                rc, out = run(["bash", "-n"], timeout=15, stdin_payload=f.read())
        else:
            rc, out = run(["bash", "-n", p], timeout=15)
        if rc != 0:
            errors[p] = (out or f"bash -n 退出码 {rc}").strip()
    return errors


def check_http(url, timeout=5.0, urlopen=None):
    """GET 探活只看连通（200）；返回 (ok, 摘要)。urlopen 可注入。"""
    urlopen = urlopen or urllib.request.urlopen
    try:
        with urlopen(url, timeout=timeout) as r:
            return r.status == 200, f"HTTP {r.status}"
    except Exception as e:  # noqa: BLE001 — 探活对一切失败形态统一返回 False
        return False, f"{type(e).__name__}: {e}"


def check_feishu_creds():
    """黑盒走 FeishuGate 构造校验（无网络，token 惰性）——driver 真正要用时
    撞的是同一套校验，预检与运行期永不漂移。返回失败 Failure（全绿为 None）。
    T13（2026-10-16）：凭证源已切到 DSH 的 channels.json（与 IM 助理同一套
    应用），旧 ~/.hermes/.env 降为兜底，故 fix 文案两条路都点名。"""
    from feishu_gate import FeishuGate, CHANNELS_JSON, GATE_CHAT_ID, HERMES_ENV
    try:
        FeishuGate()
        return None
    except RuntimeError as e:
        return Failure(KIND_FEISHU_CREDS, f"飞书凭证缺失: {e}",
                       f"补齐 {CHANNELS_JSON} 里飞书渠道的 config.appId/appSecret"
                       "（或设 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量）；"
                       f"确认门群须为 {GATE_CHAT_ID}（环境变量 FEISHU_GATE_CHAT_ID 可覆盖）；"
                       f"旧口径 {HERMES_ENV} 的 FEISHU_* 键仍作兜底。改完重跑")


def check_sfx_asset(scripts_dir):
    """翻页音效资产在位检查（skill 静态资产，路径相对 skill 根＝scripts/ 上级）。
    缺失即 Failure——compose 会静默回退合成纸声，要到飞书听审才暴露。在位返回 None。"""
    asset = os.path.join(os.path.dirname(os.path.abspath(scripts_dir)), SFX_FLIP_REL)
    if os.path.exists(asset):
        return None
    return Failure(
        "sfx-asset",
        f"翻页音效资产缺失: {asset}（compose_final 将静默回退合成纸声，听审才暴露，废整轮 GPU 装配+烧录）",
        f"下载 {SFX_FLIP_CDN_URL}（必须走 cdn. 域名，www 域名 404）放回原路径，"
        "按 assets/sfx/README.md 处理：裁首尾静音 + 峰值 -3dBFS + 10ms 防咔哒淡入淡出")


def check_static(scripts_dir, comfy_queue_url, run=None, urlopen=None):
    """环境护栏静态检查（qwen 探活/重启由调用方与 ensure_qwen_online 编排）。
    返回 Failure 列表（空 = 全绿）；顺序固定（crlf → bash-syntax → comfyui →
    feishu-creds → sfx-asset），叫人消息一次性列全，修一轮即可。
    CRLF 文件不再跑 bash -n——\\r 必然报语法错，同一文件双报是噪音。"""
    failures = []
    scripts = find_shell_scripts(scripts_dir)
    crlf = scan_crlf(scripts)
    for p in crlf:
        failures.append(Failure(
            "crlf", f"shell 脚本含 CRLF 行尾（被 Windows 侧本地工具改脏，WSL bash 会静默失败）: {p}",
            f"sed -i 's/\\r$//' \"{p}\"（或 git checkout -- 该文件；.gitattributes 已保证检出即 LF）"))
    for p, err in check_bash_syntax([p for p in scripts if p not in crlf], run=run).items():
        failures.append(Failure("bash-syntax", f"shell 脚本 bash -n 语法错误: {p}\n{err}",
                                "按报错修复脚本后重跑"))
    ok, detail = check_http(comfy_queue_url, urlopen=urlopen)
    if not ok:
        base = comfy_queue_url.rsplit("/", 1)[0]  # 叫人文案指向被覆写后的真实服务地址
        failures.append(Failure(
            "comfyui", f"ComfyUI 队列接口不可达: {comfy_queue_url}（{detail}）",
            f"Windows 侧启动 ComfyUI，浏览器开 {base} 确认在线后重跑"))
    f = check_feishu_creds()
    if f:
        failures.append(f)
    f = check_sfx_asset(scripts_dir)
    if f:
        failures.append(f)
    return failures


def human_message(failures):
    """飞书叫人消息：具体失败项 + 修法逐条列全；明示 stamps 未动（断点现场完好）。"""
    lines = ["🚨 driver 预检失败，环境护栏拦截（未进任何阶段，stamps 未动）"]
    for f in failures:
        lines.append(f"❌ {f.detail}")
        lines.append(f"   修法: {f.fix}")
    lines.append("全部修复后重跑同一条命令；自己修不了就把本消息转给 agent。")
    return "\n".join(lines)


if __name__ == "__main__":
    fails = check_static(os.path.dirname(os.path.abspath(__file__)), COMFY_QUEUE_URL)
    if not fails:
        print("preflight: 静态护栏全绿（Strata 生命周期由 driver s04 入口自动托管，#85）")
        sys.exit(0)
    for f in fails:
        print(f"✗ [{f.kind}] {f.detail}\n  修法: {f.fix}")
    sys.exit(1)
