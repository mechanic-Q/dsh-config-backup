#!/usr/bin/env python3
"""run_pipeline.py — 读报模式 driver：全自动编排阶段 2-11（wayfinder #28/#31）。

设计决议（#30 定稿 + 2026-09-05 拍板）：
  - 确认门：④+⑤ 合并为一次审核（成片+封面横竖版一次发飞书，「重生成」=重做封面再送审）
  - 参数面：--script 必填（--status 除外）/ --res 1080P / --style 默认天宫 /
    --platforms 默认 bilibili,douyin,kuaishou,toutiao
  - 可观测性（#146）：_driver/heartbeat.json 每 15s 由守护线程推进，--status 只读
    查询四态（运行中/卡住/已崩/已完成）并打印判定依据；「卡住」阈值默认 300s 可调
  - 断点续跑：stamps 自动检测跳过已完成阶段；口播稿 hash 变了自动清 stamps 重跑
  - 失败策略：driver 层每阶段重试 1 次；连败 2 次→飞书叫人+优雅退出（stamps 留现场）
  - vision：封面复检并入确认门⑤人审；阶段8 抽帧竖屏检查用 9router vision 模型批量看
  - GPU 串行队列：TTS → 封面 → LLM 设计阶段（Strata）→ H3；whisper 字幕 CPU 并行
  - 模型分配（#86）：全部文案/结构生成 = 本地 Strata（LLM_API 缺省 Strata；两车道同源）；
    vision 判读 = 本地 llama-server :8100（T2 #104 起；9router 20128 降为显式 env 回退，
    全仓引用见 test_strata_copychain 白名单）；判读形态 Jev 缺省（T8 #117）
  - Strata 生命周期（#85）：s04/s11 入口 ensure_online 自愈阶梯（探活→跨界拉起→
    失败叫人）；显存窗口纪律 conflict 模式（#83 §3 实测：常驻占 94% VRAM 与生图
    互斥）= s08 生图前停 → s11 前再拉 → s12 确认门前停（单条视频最多两次加载）；
    s08 rewrite 轮先全部 LLM 重写再统一烧卡（分相，防带着 LLM 服务烧卡 OOM）

用法：
  python3 scripts/run_pipeline.py --script <口播稿.md路径> [选项]
  python3 scripts/run_pipeline.py --script ... --dry-run   # 只打印执行计划不跑
  python3 scripts/run_pipeline.py --status --script <口播稿.md路径>   # 只读查状态

选项：
  --script PATH       口播稿路径（除 --status 外必填；slug 目录自动推导）
  --res RES           输出分辨率（默认 1080P）
  --style NAME        风格块名 styles/<名>.md（默认 chinese-celestial-palace 天宫）
  --platforms PLATS   逗号分隔 bilibili,douyin,kuaishou,toutiao（视频号恒降级待发文件夹，不设参数）
  --lane LANE         设计车道（默认 compressed：s04-s07 压成 Strata 单次调用直出
                      06_H3prompt，s05/s06/s07 写虚拟 stamp；legacy：v3 四阶段链）。
                      切换车道自动清 s04-s07 stamps——两车道产物语义不同，半新半旧
                      会错乱；env DRIVER_LANE 同效，CLI 优先
  --bgm PATH          BGM 文件（默认无 BGM；compose_final 不叠轨）
  --workdir PATH      slug 目录（默认从 --script 推导：…/流水线/daily-china-YYYYMMDD/）
  --date YYYY-MM-DD   日期（默认从 slug 目录名推导）
  --fresh              清空 stamps 全量重跑（口播稿 hash 变化时自动触发，无需手动）
  --dry-run            打印执行计划（每阶段 run/skip）后退出
  --gate-timeout SEC   确认门等待上限（默认 3600s）
  --status             只读查询本单状态（运行中/卡住/已崩/已完成）后退出：不改文件、
                      不发通知、不重启。判定依据随结论一起打印（#146）
  --stall-after SEC   「卡住」阈值（默认 300s：心跳超过它没更新即判卡住；
                      也可用 env DRIVER_STATUS_STALL_SEC 调，CLI 优先）
"""
import argparse
import atexit
import glob
import json
import os
import platform
import re
import hashlib
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import atomicio
import preflight
import enum_correct
import strata_window
import vision_common
from reconcile import reconcile as reconcile_vlm_clip, GATE_LO, GATE_HI
from vision_common import norm_verdict
from ledger import Ledger
import driver_status
import hostpath

# #151：driver 主执行宿主翻到 Windows（host-contract 单列后 driver.current=windows）。
# 模块级声明参与 check_mechanical 规则 6：本文件出现 /mnt/、/proc、wslpath 即违规；
# 合法的 wsl.exe 单点 interop 调用（TTS/whisper/s13 发布）用行内 noqa 豁免并注明。
__host__ = "windows"

# 环境与固定资产（SKILL.md 读报标准 2026-08-29 定稿）
# Strata 生命周期托管（#85）：探活→跨界拉起→失败叫人的自愈阶梯在 strata_window，
# driver 在 s04/s11 入口 ensure_online、s08/s12 入口 stop（显存窗口纪律，见 _stage_list
# 与各钩子注释）。8888 llama-server 托管（spawn/pkill/残留判定）已随 #86 全链切
# Strata 退役，kvmem_window 窗口托管模块同票删除。

# ── 压缩车道（#57 SPEC / 票2 #59 接线 / #84 切 Strata）────────────────
# s04-s07 四个文字阶段压成对本地 Strata 实例的单次 LLM 调用（strata_h3_lane）。
# 旧 v3 链保留为显式开关（--lane legacy / DRIVER_LANE），结构不动；#86 起 legacy
# 端点同切 Strata，仅退出四阶段链结构。
# 服务端点/模型名单一事实源在 strata_h3_lane（STRATA_URL/STRATA_MODEL env）；
# 生命周期归 strata_window（#85），窗口纪律模式 strata_window.WINDOW_MODE
# （conflict=生图前停/s11 前再拉/尾部停；coexist=常驻）。
import strata_h3_lane
LANE_STRATA_URL = strata_h3_lane.BASE_URL
LANES = ("compressed", "legacy")
STRATA_MANUAL_STEPS = (
    "Strata 生命周期由 driver 自动托管（#85：探活→拉起→失败叫人）。手动处置仅兜底：\n"
    f"1) 探活：curl -m 5 {LANE_STRATA_URL}/v1/models（200 即在线，#83 spike 探活口径；"
    "WSL 内缺省端点自动走网关 IP）\n"
    "2) Windows 侧拉起：bash scripts/spike-strata-start.sh（powershell Start-Process "
    "--host 0.0.0.0，spike §5 定案串）；停止用 scripts/spike-strata-stop.sh\n"
    "3) 首请求慢是常态（专家缓存预热 ~70s，#83 spike §2），300s 内自行恢复，勿急着重启\n"
    "4) 重跑同一条 run_pipeline.py 命令，stamps 断点自动续跑\n"
    "（或 --lane legacy 退回旧 v3 四阶段链；切换车道会自动清 s04-s07 stamps）")

# 各阶段人工干预提示（叫人消息必附——2026-09-05 用户要求：需人工时必须告知如何干预）
STAGE_MANUAL_HINTS = {
    "s02": ("TTS 排查：\n1) nvidia-smi 看显存是否被占满（llama-server/H3 残留）\n"
           "2) Index-TTS 服务是否在线\n3) 处理后重跑同命令，stamps 续跑"),
    "s05": ("LLM 排查：见下方 Strata 手动步骤（首请求慢 ~70s 专家预热不属故障，勿当劣化重启）；"
            "⑦景别交替硬重复已自动纠偏（ADR 0003 枚举级纠偏，台账 report.json enum_corrections），\n"
            "纠偏后仍连败 = 失败项非枚举可纠（看 04_分镜增强/_自检报告.md 定位段号）→\n"
            "手改 03_导演设计/shotXX.json 对应字段（语义字段勿动）→ 重跑同命令续跑"),
    "s06": "LLM 排查：见下方 Strata 手动步骤",
    "s07": "LLM 排查：见下方 Strata 手动步骤",
    "s04-compressed": ("Strata 排查（生命周期自动托管已上线，此为兜底）：\n"
                       f"1) 服务是否在线：curl -m 5 {LANE_STRATA_URL}/v1/models（200 即在线）\n"
                       "2) 首请求慢（专家缓存预热 ~70s）不属故障，勿当劣化重启（#83 spike §2）\n"
                       "3) 确认挂死/连败：bash scripts/spike-strata-stop.sh 后 spike-strata-start.sh 重启\n"
                       "4) 或 --lane legacy 退回旧 v3 四阶段链（自动清 s04-s07 stamps）"),
    "s08": ("H3 排查：\n1) Windows ComfyUI 是否在线：浏览器开 http://192.168.50.231:18188\n"
           "2) 查报错：{log}\n"
           "3) 单段补跑：python3 {here}/submit_accel.py <段号> <段号> <slug>/06_H3prompt --script <口播稿> --res 1080P"),
    "s09": ("装配排查：\n1) 素材在位？片头 {intro}、当日报纸图、欢迎词 02_TTS语音/_welcome/shot01.wav\n"
            "2) ffmpeg 具体报错见 driver 终端日志"),
    "s10": ("字幕/烧录排查：\n1) whisper 缓存 _wsout/ 是否有各段 json\n"
            "2) ffmpeg 报错见 driver 终端日志（多为字体/滤镜参数）"),
    "s11": ("封面排查：\n1) Windows ComfyUI 是否在线：http://192.168.50.231:18188\n"
            "2) Krea2 工作流文件是否在原位（gen_krea2_cover.py 的 WF 常量）\n"
            "3) 检查 slug/09_封面/ 是否已有产出"),
    "s13": ("发布排查：\n1) B站账号登录态：sau bilibili check --account diyi\n"
            "2) 抖音 cookie 是否过期（过期则飞书发待发文件夹路径人工手发）"),
}
# （#86 前：8888 加载等待窗口 QWEN_LOAD_TIMEOUT，随 8888 托管一并退役）
# T2 #104（ADR 0007）：判读端点缺省翻转 = 本地 llama-server（CPU 常驻 @8100，
# OpenAI 协议同构，判读面零改动）；9router 保留为显式 env 回退（DRIVER_VISION_API
# 一条切回云端）。模型名 = 盲比定档 alias（vision_blindbench.py 产出）。
VISION_MODEL = os.environ.get("DRIVER_VISION_MODEL", "qwen3-vl-4b")
VISION_API_DEFAULT = vision_common.default_api()

# 字体（2026-09-05 用户选定得意黑 Smiley Sans，OFL 开源）。#151：宿主翻 Windows 后
# fontconfig/fc-match 不再可用（#150 AC3）——标题 drawtext 与字幕回退都改为
# 「字体文件存在性」判定，assets 缺失直接 fail-fast，不再静默回退族名。
_TITLE_FONT_ASSET = os.path.normpath(os.path.join(HERE, "..", "assets", "fonts", "SmileySans-Oblique.ttf"))
TITLE_FONT_FILE = os.environ.get("DRIVER_TITLE_FONT") or _TITLE_FONT_ASSET
SUBTITLE_FONT_NAME = os.environ.get("DRIVER_SUB_FONT", "Smiley Sans")
SUBTITLE_FONT_FALLBACK = "Noto Sans SC"
# VOICE_REF 是 WSL 侧 index-tts 的输入（只进 wsl.exe 命令行，保持 Linux 口径）
VOICE_REF = os.environ.get("DRIVER_VOICE_REF",
                           "/home/lmr/qmr_content_creator/private/voice/references/expressive-reference.wav")
# BGM：纪录片风格（pixabay_music_Documentary），2026-09-05 用户拍板（此前读报单同款）。
# BGM：纪录片风格（pixabay_music_Documentary），2026-09-05 用户拍板（此前读报单同款）。
# BGM 实体在 WSL 侧；Windows 宿主经 UNC（\\wsl.localhost）直读，ffmpeg 可直接打开。
DEFAULT_BGM = os.environ.get("DRIVER_BGM",
                             r"\\wsl.localhost\Ubuntu\home\lmr\labs\OpenMontage\pixabay_music_Documentary.mp3")  # noqa: mechanical — 声明宿主的本机口径常量（WSL 资源 UNC 直读）
# #151：Windows 主执行 → 盘符口径（env 可整体覆盖）；WSL 独占资源保持 /home 口径
DATE_ROOT = os.environ.get("DRIVER_DATE_ROOT", r"E:\每日新中国")  # noqa: mechanical
INTRO_MP4 = os.path.join(DATE_ROOT, "每日新中国片头.mp4")
INTRO_DUR = 5.2
SAU = "/home/lmr/social-auto-upload/.venv/bin/sau"
SAU_DIR = "/home/lmr/social-auto-upload"
ACCOUNT = "diyi"
BILI_TID = "21"  # 生活大区（禁 232，SKILL.md 阶段11 定稿）
# B站 10 标签策略（2026-09-09 用户拍板）：4 活动流量标签 + 6 话题标签凑满 10。
# 话题含用户点名的「国际局势」「国防」（B站观众偏好）。换季活动标签需换（开学季→8.20-10.10）。
BILI_ACTIVITY_TAGS = "哔哩哔哩开学季,开学季,创作激励,涨粉计划"
BILI_TOPIC_TAGS = "每日新中国,国际局势,国防,今日要闻,中国天眼,科普"
BILI_TAGS = f"{BILI_ACTIVITY_TAGS},{BILI_TOPIC_TAGS}"
DRIVER_DIRNAME = "_driver"
# 运行心跳（#146）：_driver/heartbeat.json 每 N 秒原子重写一次，run_pipeline --status
# 只读读取它判定四态。15s 足够细（阈值默认 300s = 20 拍），又不至于把磁盘刷爆。
DRIVER_HEARTBEAT_SEC = 15
WIN_COVER_OUT = os.environ.get("DRIVER_COMFY_OUT",
    r"E:\AI\Comfyui-WF-2026.8.8\Comfyui-WF-2026.8.8\ComfyUI\output")  # noqa: mechanical
CN_NUM = {"0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
          "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}


def _sha256_file(fp):
    """帧内容哈希（#182「清单是账」）：与 t15_replay.sha256 同算法，流式读取。

    证据行落哈希后，帧被覆盖/移动可由 rotation_dataset.verify_evidence_hashes
    逐条对出来。读失败返回空串（存证降级，不炸主链）。"""
    try:
        h = hashlib.sha256()
        with open(fp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _load_evidence_manifest(ev_dir, episode):
    """生产侧证据清单读口（ADR 0008 决策 4：_archive_evidence_buckets 与
    _flag_commit 共用一套解析/落盘，别各写一份 manifest 逻辑）。

    返回 (manifest_path, manifest, entries)；坏清单/无清单都从头建，episode
    只补缺省（已有 episode 不覆盖——重跑同期的清单仍是首见那期口径）。"""
    from rotation_dataset import PROD_EVIDENCE_MANIFEST   # 函数内 import：
    # 本文件不顶层拉 rotation_dataset（与 2521 同口径，避免模块级互相牵连）
    mf_path = os.path.join(ev_dir, PROD_EVIDENCE_MANIFEST)
    try:
        manifest = json.load(open(mf_path, encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 无清单/坏清单都从头建
        manifest = {}
    manifest.setdefault("episode", list(episode))
    entries = manifest.setdefault("evidence", [])
    return mf_path, manifest, entries


def _write_evidence_manifest(ev_dir, mf_path, manifest):
    """生产侧证据清单写口：清单唯一落盘点（缩进/编码口径一处定）。"""
    os.makedirs(ev_dir, exist_ok=True)
    with open(mf_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def log(msg):
    # #170：cp936 裸跑/控制台下 emoji（🚨⚠️❌）与 U+FFFD 不可编码，strict print 直接
    # UnicodeEncodeError 掀翻 driver（预检叫人消息走这里，2884 行 msg 含 🚨）。per-call
    # 降级：先按 stdout 真实编码正常打，失败再 backslashreplace→dec 转义重打。不用入口
    # reconfigure——全局副作用会污染测试对 sys.stdout 的替换。happy path 零额外成本。
    line = f"[driver {time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(enc, "backslashreplace").decode(enc, "replace"), flush=True)


def telemetry_line(payload):
    """#195 结构化打点：单行 JSON，前缀 [telemetry]，供 grep / 脚本解析。

    为什么要有：s13_publish 原来一条 log() 都没有（2026-10-22 发布期 9 分钟零行，
    事后只能拿心跳 ts 猜），而发布是全程最久的阶段。只加收尾一行、机器可读
    （阶段 / 耗时 / 产物路径），既有输出格式一条不动——所以前缀固定、字段排序固定，
    解析方不需要猜。"""
    return "[telemetry] " + json.dumps(payload, ensure_ascii=False,
                                       sort_keys=True, separators=(",", ":"))


def sh(cmd, env=None, timeout=None, cwd=None):
    """跑外部命令，返回 (rc, 输出尾部)。

    #151：宿主翻 Windows 后 shell=True 落到 cmd.exe /c，引号嵌套语义与 bash 不同——
    只用于无嵌套引号的简单命令（ffmpeg/ffprobe/wsl.exe 单层）；本仓 Python 脚本
    子进程一律走 run_argv（argv 数组直调，绕开 cmd 引号地狱）。
    """
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=e, cwd=cwd)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode, out


def win_to_wsl(p):
    """Windows 口径路径 → WSL /mnt 口径（#150 AC1：wsl.exe 单层调用的 argv 换算）。

    hostpath.to_host_path 在 Windows 宿主上是恒等（它只服务 WSL 侧），这里
    driver 从 Windows 发起 interop，必须自己做盘符→/mnt 的换算。
    """
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p or "")  # noqa: mechanical  # win_to_wsl 是 driver 侧合法转换实现（test_hostpath §7 白名单收录，规则 7 样例同理）
    if not m:
        return p
    return "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")


WSL_DISTRO = os.environ.get("DRIVER_WSL_DISTRO", "Ubuntu")


def _filter_escape(p):
    r"""Windows 路径 → ffmpeg 滤镜参数口径（libass/drawtext）：盘符冒号须双层转义 C\\\\:/。
    libavfilter 第一层解析消费一层反斜杠，args 层再消费一层；单层 C\:/ 会把 'C' 与
    '/Users/...' 错拆成 filename/original_size 两个选项（2026-10-19 烧卡小样实测）。"""
    return (p or "").replace("\\", "/").replace(":", r"\\:")


def _cover_shell(argv, timeout):
    """封面生成子进程入口：tests 桩 rp.sh 时经 sh() 走（回归 #72 桩面），
    否则 run_argv 直调（#151 argv 化）。"""
    if sh.__module__ != __name__:
        return sh(" ".join(str(a) for a in argv), timeout=timeout)
    return run_argv(argv, timeout=timeout)


def _resolve_ffmpeg():
    """选一个带 drawtext 滤镜的 ffmpeg（#150 前置阻塞：PATH 首选发行版缺 drawtext）。

    顺序：env DRIVER_FFMPEG → PATH 首个 → 逐候选探测 `ffmpeg -filters` 是否含
    drawtext；都不带则退回 PATH 首个（让烧录阶段报出原生错误，不静默换行为）。
    探测结果缓存模块级（探测一次 ~50ms，不重复起进程）。
    """
    global _FFMPEG_BIN
    if _FFMPEG_BIN:
        return _FFMPEG_BIN
    env_bin = os.environ.get("DRIVER_FFMPEG")
    if env_bin:
        _FFMPEG_BIN = env_bin
        return _FFMPEG_BIN
    first = shutil.which("ffmpeg") or "ffmpeg"
    try:
        p = subprocess.run([first, "-hide_banner", "-filters"], capture_output=True,
                           text=False, timeout=30)
        if b"drawtext" in (p.stdout or b""):
            _FFMPEG_BIN = first
            return _FFMPEG_BIN
    except Exception:  # noqa: mechanical — 探测失败按「不带 drawtext」处理
        pass
    for cand in (r"C:\Users\LMR\anaconda3\Library\bin\ffmpeg.exe",):  # noqa: mechanical
        if os.path.isfile(cand):
            try:
                p = subprocess.run([cand, "-hide_banner", "-filters"], capture_output=True,
                                   text=False, timeout=30)
                if b"drawtext" in (p.stdout or b""):
                    log(f"[ffmpeg] PATH 首选缺 drawtext，改用 {cand}")
                    _FFMPEG_BIN = cand
                    return _FFMPEG_BIN
            except Exception:  # noqa: BLE001
                continue
    log("[ffmpeg] ⚠ 未找到带 drawtext 的 ffmpeg，烧录阶段将失败；env DRIVER_FFMPEG 可指定")
    _FFMPEG_BIN = first
    return _FFMPEG_BIN


_FFMPEG_BIN = ""
class _LazyFfmpeg(str):
    """惰性 ffmpeg 路径：str 形态直传可用；真正落 argv 前经 _resolved() 完成探测。"""
    def _resolved(self) -> str:
        return _resolve_ffmpeg()


FFMPEG_BIN = _LazyFfmpeg("ffmpeg")  # 探测推迟到首次消费（import 期不起子进程）


def wsl_argv(argv):
    """把本仓脚本调用包装成 wsl.exe 单层调用（#150 定案模板）。

    - 路径参数：Windows 口径自动换 /mnt；已是 /home 或 /mnt 的原样保留
    - 解释器：WSL 侧 python3（商店桩问题只存在于 Windows 侧）
    - 失败判据：wsl.exe 自身故障 rc=4294967295（0xFFFFFFFF）
    """
    out = ["wsl.exe", "-d", WSL_DISTRO, "python3"]
    for a in argv:
        m = re.match(r"^([A-Za-z]):[\\/]", a or "")  # noqa: mechanical  # 同上：argv 盘符判定，委托 win_to_wsl 换算
        out.append(win_to_wsl(a) if m else a)
    return out


def run_argv(argv, env=None, timeout=None, cwd=None):
    """argv 数组直调子进程，返回 (rc, 输出尾部)。

    #151：Windows 侧子进程标配——不经 shell（cmd 引号语义不可靠）；text 不开
    （subprocess-text 规则 3 要求 encoding 或 bytes），输出经 hostpath.decode_output
    容错解码（UTF-8→GBK）。python3 裸名在 Windows 是商店桩（rc=49 无输出），
    本仓脚本统一 sys.executable。
    """
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        p = subprocess.run(argv, capture_output=True, text=False,
                           timeout=timeout, env=e, cwd=cwd)
        out = hostpath.decode_output(p)
        return p.returncode, out.strip()
    except subprocess.TimeoutExpired as t:
        out = hostpath.decode_output(getattr(t, "stdout", b"") or b"") + \
            hostpath.decode_output(getattr(t, "stderr", b"") or b"")
        return 124, out.strip() or f"timeout after {timeout}s"


def frame_is_stale(fp, src):
    """抽帧缓存是否已过期：文件不在盘，或**比源视频旧**。

    #187 实锤（2026-10-22 生产）：`_first.jpg` 只按「不存在才抽」缓存，重烧后旧帧
    永远不失效——5 段补烧全部被判翻转，用的却是 10-20 21:03 的旧帧。这类缺陷
    不留痕迹，只让判读结论静默错位，必须按「缓存必须晚于源」这条不变式判。
    """
    return (not os.path.exists(fp)) or os.path.getmtime(fp) < os.path.getmtime(src)


def ffprobe_dur(path):
    rc, out = sh(f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"', timeout=30)
    try:
        return float(out.strip().splitlines()[-1])
    except Exception:
        return 0.0


# ── LLM 服务守卫 ─────────────────────────────────────────────────────
# Strata 探活/生命周期单一实现 = strata_window（#85）；旧 run_pipeline.strata_healthy
# 与 _gate_strata 的探活-报错形态已收敛删除（探活拒连在 ensure_online 阶梯内自愈）。


def resolve_lane(cli_lane, env_lane):
    """车道解析（纯函数）：CLI --lane > env DRIVER_LANE > 默认 compressed。
    非法值 SystemExit（fail-fast，与 preflight 哲学一致）。"""
    lane = cli_lane or env_lane or "compressed"
    if lane not in LANES:
        raise SystemExit(f"ERROR: 非法车道 {lane!r}（可选 {LANES}；"
                         f"compressed=Strata 单次调用直出 / legacy=v3 四阶段链）")
    return lane

# ── 运行心跳（#146）写侧 ─────────────────────────────────────────────
class Heartbeat:
    """_driver/heartbeat.json 的写侧守护（只读判定在 driver_status.py）。

    为什么另起一个文件、不复用 report.json：
      report.json 的 finished_at 由 ledger.flush() 写，而 flush 在「阶段连败退出」
      「确认门硬退出」「run() 的 finally」里都会调——它只说明台账落过盘，与「跑完
      了」毫无关系（#146 踩坑一）。report.json 也只在阶段边界刷新，进程卡在确认门
      轮询时它一直是旧的——10-17 期轮询 6 小时 20 分钟、期间无从判断死活的活证据。

    写法：ts 每 DRIVER_HEARTBEAT_SEC 秒由守护线程重写（纯时间戳推进不需要阶段变化），
    stage/stage_title/stage_started_at/attempt 只在阶段切换时更新；每次整文件原子
    重写（tmp + os.replace），读者永远看到完整 JSON，不会读到半截（同 ledger._write）。
    换盘走 atomicio.replace_with_retry：Windows 上并发读者持有 heartbeat.json 时
    os.replace 会 PermissionError，原实现一次就放弃 → 心跳停更、--status 把活进程
    判成「卡住」（#166）。重试只针对这一种瞬时冲突，有硬上限、有 .tmp 清理。
    退出时补 exited{code,reason,at}，让 --status 能说清「上次是确认门没过退出」而不是
    一律报「已崩」。"""

    def __init__(self, stamps_dir, slug, lane=None, interval=None):
        self.path = os.path.join(stamps_dir, "heartbeat.json")
        self.slug = slug
        self.lane = lane
        self.interval = float(interval or os.environ.get("DRIVER_HEARTBEAT_SEC")
                              or DRIVER_HEARTBEAT_SEC)
        self.host = driver_status.host_label()
        self.started_ts = None
        self.stage = None
        self.stage_title = None
        self.stage_started_at = None
        self.attempt = None
        self.exited = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._atexit_done = False

    # —— 写 ——
    def _snapshot(self, now):
        return {"pid": os.getpid(), "host": self.host, "ts": now,
                "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "started_ts": self.started_ts, "slug": self.slug, "lane": self.lane,
                "stage": self.stage, "stage_title": self.stage_title,
                "stage_started_at": self.stage_started_at, "attempt": self.attempt,
                "interval": self.interval,
                "exited": self.exited}

    def _write(self, now=None):
        """原子落盘（tmp + 短退避换盘）。异常只落日志，绝不炸穿 driver。"""
        now = time.time() if now is None else now
        try:
            with self._lock:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._snapshot(now), f, ensure_ascii=False, indent=1)
                atomicio.replace_with_retry(tmp, self.path)
        except Exception as e:  # noqa: BLE001 — 心跳失败不能拖垮主流程
            log(f"[heartbeat] 写入失败（忽略）: {type(e).__name__}: {e}")

    # —— 生命周期 ——
    def start(self):
        """启动：记 pid/宿主/启动时刻，落第一拍，起守护线程。"""
        self.started_ts = time.time()
        self._write()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)
            self._thread.start()
        # 兜底：解释器正常收尾（SystemExit / 未捕获异常）也留退出记录；os._exit 路径
        # 绕过 atexit，那几条硬退出由调用点显式 exit()
        if not self._atexit_done:
            atexit.register(self._atexit)
            self._atexit_done = True
        return self

    def _loop(self):
        while not self._stop.wait(self.interval):
            self._write()

    def enter(self, stage, title=None, attempt=None):
        """阶段切换/重试：更新阶段指针并立刻落盘（--status 才能显示当前阶段）。"""
        self.stage = stage
        self.stage_title = title
        self.stage_started_at = time.time()
        self.attempt = attempt
        self._write()

    def exit(self, code, reason=None):
        """写退出记录并停线程（进程即将结束或已到断点）。幂等。"""
        self.exited = {"code": int(code), "reason": reason or "",
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._stop.set()
        self._write()

    def _atexit(self):
        if self.exited is None:
            self.exited = {"code": 0, "reason": "解释器退出（未显式记录原因）",
                           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._write()
        self._stop.set()


class NullHeartbeat:
    """无心跳占位（#146）：测试用 Driver.__new__ 裸实例、以及任何没起跑的场景。

    心跳是可观测性设施，不该成为「没走 __init__ 就不能调方法」的隐形前置条件——
    早先直接 self.hb.enter(...) 让 4 个老测试文件全线 AttributeError。"""

    path = None

    def start(self):
        return self

    def enter(self, stage, title=None, attempt=None):
        pass

    def exit(self, code, reason=None):
        pass


class Driver:
    # 心跳句柄（#146）。用 property 而非普通属性：__init__ 里是 self._hb = Heartbeat(...)，
    # 而测试的 Driver.__new__ 裸实例没有 _hb，这里兜一个 NullHeartbeat，行为不变、不落盘。
    @property
    def hb(self):
        obj = self.__dict__.get("_hb")
        if obj is None:
            obj = NullHeartbeat()
            self.__dict__["_hb"] = obj
        return obj

    def __init__(self, args):
        self.args = args
        self.script = os.path.abspath(args.script)
        if not os.path.exists(self.script):
            sys.exit(f"ERROR: 口播稿不存在: {self.script}")
        # slug 推导：…/daily-china-YYYYMMDD/00_口播稿/口播稿.md 或直接给 slug 内路径
        base = os.path.dirname(self.script)
        if os.path.basename(base) == "00_口播稿":
            base = os.path.dirname(base)
        self.slug = os.path.abspath(args.workdir) if args.workdir else base
        if not os.path.isdir(self.slug):
            sys.exit(f"ERROR: slug 目录不存在: {self.slug}（用 --workdir 指定）")
        m = re.search(r"(\d{8})", os.path.basename(self.slug))
        self.date = args.date or (f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else time.strftime("%Y-%m-%d"))
        self.platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
        self.stamps_dir = os.path.join(self.slug, DRIVER_DIRNAME)
        os.makedirs(self.stamps_dir, exist_ok=True)
        # 运行心跳（#146）：构造不落盘，run() 里 start() 才写第一拍——dry-run 与
        # --status 都不该在盘上留痕。
        self._hb = Heartbeat(self.stamps_dir, self.slug)
        # 资源/质量台账（--report 常开：无数据就无法系统性优化，2026-09-05 用户拍板）。
        # Ledger module（票7 #65，架构评审候选1）：快照/合并/落盘收口一处，写点走
        # 五动词 interface（record/record_event/get/flush/summary）；s_gate_review
        # 的跨重启恢复也经此（全库唯一读方）。
        self._t0 = time.time()
        self.ledger = Ledger(os.path.join(self.stamps_dir, "report.json"),
                             meta={"slug": self.slug, "date": self.date, "style": args.style,
                                   "started_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                             t0=self._t0)
        # 目录契约（2026-09-07 阶段号配对重整）：00 输入；01-06 对应 s01-s07；
        # 07 成片(s08-s10 共用)、08 字幕、09 封面、10 发布。口播台词归 01_分段，
        # shot 设计+交接卡归 03_导演设计。
        self.d = {n: os.path.join(self.slug, n) for n in (
            "00_口播稿", "01_分段", "02_TTS语音", "03_导演设计", "04_分镜增强",
            "05_美术画质", "06_H3prompt", "07_成片", "08_字幕", "09_封面",
            "10_发布")}

        from feishu_gate import FeishuGate
        self.feishu = FeishuGate(dry=bool(args.dry_run))

        # 导演逐段并发（2026-09-07 可调化：默认 4；本地服务排队串行，安全值）
        self.director_concurrency = int(os.environ.get("DRIVER_DIRECTOR_CONCURRENCY", "4"))

        # BGM 预检：设了但文件不在 → 启动即失败（好过 compose 阶段才炸）
        bgm = args.bgm
        if bgm and not os.path.exists(bgm):
            sys.exit(f"ERROR: BGM 文件不存在: {bgm}（--bgm 传空串可禁用 BGM）")

        self._migrate_stamps()
        # 双车道（#59）：CLI > DRIVER_LANE > compressed；车道变化先清 s04-s07 stamps。
        # dry-run 只看不拦（plan 的既有契约）：车道切换守卫是落盘动作，跳过。
        self.lane = resolve_lane(args.lane, os.environ.get("DRIVER_LANE"))
        self.hb.lane = self.lane
        if not args.dry_run:
            self._guard_lane_switch()
        self._refresh_stamps()

    # ── stamps：断点续跑（#30 决议3）────────────────────────────────────
    # 2026-09-07 阶段号重整：旧工序号（s2/s3/s4b/s5/s4/s5b/s6/s9-s14，音频先行
    # 重排后乱序）统一迁移为执行顺序号 s01-s13（纯整数）。
    # 2026-09-08 删 s10-s14 迁移项：旧号与新号 s09-s13 撞名，每次启动都会把
    # 新 stamps 当旧文件降级覆盖（当日实证：字幕 stamp 被删→封面两轮重跑；
    # 若再重启会把发布 stamp 降级成门 stamp = 免确认重发风险）。重整后的
    # 首次启动早已完成全量迁移，撞名项只会造成破坏，无正向价值。
    LEGACY_STAGE_MAP = {"s2": "s01", "s3": "s02", "s4b": "s03", "s5": "s04",
                        "s4": "s05", "s5b": "s06", "s6": "s07", "s9": "s08"}

    def _migrate_stamps(self):
        """旧名 stamp → 新名。按目标序处理：s09(源s10)先于 s10(源s11)，
        防止链式覆盖。幂等，新旧并存时旧文件静默删除。"""
        rev = sorted(self.LEGACY_STAGE_MAP.items(), key=lambda kv: kv[1])
        for old, new in rev:
            old_fp = os.path.join(self.stamps_dir, f"stage_{old}.json")
            new_fp = os.path.join(self.stamps_dir, f"stage_{new}.json")
            if os.path.exists(old_fp):
                if os.path.exists(new_fp):
                    os.remove(old_fp)
                else:
                    os.replace(old_fp, new_fp)
                log(f"stamps 迁移: stage_{old} → stage_{new}")

    def _script_hash(self):
        import hashlib
        return hashlib.sha256(open(self.script, "rb").read()).hexdigest()[:16]

    def _refresh_stamps(self):
        """口播稿 hash 变了 → 自动清全部 stamps 重跑。"""
        cur = self._script_hash()
        dirty = False
        for f in glob.glob(os.path.join(self.stamps_dir, "stage_*.json")):
            try:
                d = json.load(open(f))
            except Exception:
                dirty = True
                os.remove(f)
                continue
            if d.get("script_hash") != cur:
                dirty = True
                os.remove(f)
        if dirty:
            log("口播稿 hash 变化 → 已清空 stamps，全量重跑")

    def stamp(self, stage, meta=None):
        json.dump({"script_hash": self._script_hash(), "ts": time.time(),
                   "date": self.date, "meta": meta or {}},
                  open(os.path.join(self.stamps_dir, f"stage_{stage}.json"), "w"),
                  ensure_ascii=False, indent=2)

    def done(self, stage):
        return os.path.exists(os.path.join(self.stamps_dir, f"stage_{stage}.json"))

    # ── 双车道（#59 压缩车道接线）────────────────────────────────────
    # compressed：s04 单阶段（strata_h3_lane 单次调用直出 06_H3prompt），s05/s06/s07
    # 写虚拟 stamp 保持断点续跑与外部查询兼容；legacy：原 v3 四阶段链原样保留。
    LANE_STAGES = ("s04", "s05", "s06", "s07")   # 车道切换互斥区（两车道产物语义不同）

    def _guard_lane_switch(self):
        """车道切换守卫：stamps_dir/lane.txt 记录既往车道。变化（或首次）时——
        首次不动 stamps（可能是旧版现场续跑）；变化则自动清 LANE_STAGES stamps 并
        台账留痕（lane_switch_cleared）。不清会半新半旧：压缩车道的 s05/s06 虚拟
        stamp 配旧车道缺失的 04_/05_ 产物，对账门与断点续跑全部错乱。"""
        lane_fp = os.path.join(self.stamps_dir, "lane.txt")
        prev = open(lane_fp, encoding="utf-8").read().strip() if os.path.exists(lane_fp) else ""
        if prev == self.lane:
            return
        if prev and prev in LANES:
            cleared = [s for s in self.LANE_STAGES if self.done(s)]
            for s in cleared:
                os.remove(os.path.join(self.stamps_dir, f"stage_{s}.json"))
            self.ledger.record("lane_switch_cleared", cleared)
            log(f"车道切换 {prev} → {self.lane}：已清 stamps {cleared}"
                f"（两车道 s04-s07 产物语义不同，半新半旧会错乱）")
        else:
            log(f"车道: {self.lane}（首次记录，不清既有 stamps）")
        with open(lane_fp, "w", encoding="utf-8") as f:
            f.write(self.lane + "\n")

    def _write_virtual_stamps(self):
        """压缩车道 s04 成功后补写 s05/s06/s07 虚拟 stamp（meta 带 lane/virtual）。
        外部查询（stamps 即进度）与断点续跑（done() 短路）都依赖这三个文件在。"""
        for s in ("s05", "s06", "s07"):
            self.stamp(s, meta={"lane": self.lane, "virtual": True})

    def _noop_virtual(self):
        """压缩车道虚拟阶段体：真实工作已在 s04_design_h3 完成，此处无事可做。
        stamp 由 run_stage 统一落（含 virtual meta，见 run_stage）；正常路径
        _write_virtual_stamps 先写、被 done() 短路，根本走不到这里。"""

    def _gate_strata(self, ledger_key="strata.s04_entry"):
        """Strata 在线门（各 LLM 阶段入口统一生命周期钩子，#85）：ensure_online
        自愈阶梯（在线复用 / 离线跨界拉起 / 加载超时修复重启，耗尽 StrataHardError）。
        硬失败 → run_stage 重试 1 次 → 飞书叫人（消息带兜底手动步骤）。台账记
        outcome 供 report 审计（started/reused/restarted + 重启轮数）。"""
        info = strata_window.ensure_online(notify=self.notify)
        self.ledger.record(ledger_key, {"outcome": info["outcome"],
                                        "restarts": info["restarts"],
                                        "url": LANE_STRATA_URL,
                                        "at": info["at"]})

    def _run_lane(self, slug, style_name=None):
        """压缩车道执行接缝：转发 strata_h3_lane.run_lane（唯一网络 IO 在其内）。
        返回 0=成功 / 1=整单连败（未写产物）。独立方法=测试注入口
        （monkeypatch 本方法即可，不需要假服务）。
        单次调用超时 = strata_window.STALL_SECS（10 分钟，挂死判据自 kvmem_window
        迁移，#85）。"""
        import strata_h3_lane
        return strata_h3_lane.run_lane(slug, base_url=LANE_STRATA_URL, style_name=style_name,
                                       timeout=strata_window.STALL_SECS)

    def s04_design_h3(self):
        """s04 单阶段「设计+H3 prompt（Strata 压缩车道）」：单次 LLM 调用直出
        06_H3prompt/shotXX.json 全量产物（重试/整单契约/落盘收口都在 run_lane 内）。
        成功后补写 s05/s06/s07 虚拟 stamp；连败（run_lane=1）→ run_stage 重试 1 次
        再叫人——重试的 _gate_strata 会再走 ensure_online，服务真死形态（探活拒连）
        已由阶梯自动重启覆盖；「活着但劣化」形态 #83 实测为 RAM 降级自愈（随请求
        升温回满速），不做显式重启接线。"""
        self._gate_strata()
        if self._run_lane(self.slug, style_name=self.args.style) != 0:
            raise RuntimeError(
                f"压缩车道整单连败（run_lane 返回 1，未写产物；"
                f"#84 阶段服务手动起，排查后重跑；整单契约详见上方日志）。\n{STRATA_MANUAL_STEPS}")
        raw = json.load(open(os.path.join(self.d["01_分段"], "分镜清单_raw.json"), encoding="utf-8"))
        want_ids = {s["id"] for s in raw}
        files = sorted(glob.glob(os.path.join(self.d["06_H3prompt"], "*.json")))
        have = {os.path.basename(fp)[:-5] for fp in files}
        missing = sorted(want_ids - have)
        extra = sorted(have - want_ids)
        if missing or extra:
            # run_lane 整单契约下不应到达；到达即说明现场有旧产物混杂，硬失败叫人
            raise RuntimeError(f"压缩车道对账错位（缺 {missing} 多 {extra}；"
                               f"06_H3prompt 旧产物混杂？清空该目录后重跑）")
        sanitized = sum(1 for fp in files
                        if json.load(open(fp, encoding="utf-8")).get("camera_sanitized"))
        self.ledger.record("s04", {"lane": self.lane, "ok": len(have), "total": len(raw),
                                   "camera_sanitized": sanitized})
        log(f"[s04] 压缩车道 {len(have)}/{len(raw)} 段落盘"
            + (f"，运镜消毒 {sanitized} 段" if sanitized else ""))
        self._write_virtual_stamps()
        log("[s04] s05/s06/s07 虚拟 stamp 已落盘（压缩车道单阶段完成）")

    # ── 阶段执行骨架：重试1次 → 连败叫人（#30 决议4）────────────────────
    def run_stage(self, stage, title, fn, env=None):
        if self.done(stage):
            log(f"[{stage}] {title} — stamps 已完成，跳过")
            return
        log(f"[{stage}] {title} — 开始")
        self.hb.enter(stage, title)
        self.feishu.send_text(f"▶️ {title} 开始")
        # 预建阶段条目（旧 setdefault 预置四键；add 增量只带增量键，缺此步
        # 首跑落盘会缺 errors/seconds，偏离冻结键形——票7 review 修正）
        self.ledger.record(f"stages.{stage}",
                           {"title": title, "attempts": 0, "errors": [], "seconds": 0.0})
        last_err = ""
        for attempt in (1, 2):
            self.hb.enter(stage, title, attempt=attempt)
            try:
                if env:
                    self._gate_strata(ledger_key=f"strata.{stage}_entry")
                import llm_json as _lj
                _tk0 = dict(_lj.STATS)
                t0 = time.time()
                fn()
                # 阶段计账走 ledger 累加语义（attempts/seconds 相加、tokens 逐字段）
                self.ledger.record(
                    f"stages.{stage}",
                    {"title": title, "attempts": 1, "seconds": round(time.time() - t0, 1),
                     "tokens": {"prompt": _lj.STATS["prompt_tokens"] - _tk0["prompt_tokens"],
                                "completion": _lj.STATS["completion_tokens"] - _tk0["completion_tokens"],
                                "calls": _lj.STATS["calls"] - _tk0["calls"]}},
                    add=True)
                # 虚拟阶段 stamp 带 virtual meta（外部查询可区分真实/虚拟完成；
                # 手工清 stamp 后重跑也经此重建，不丢标记）
                self.stamp(stage, meta={"virtual": True} if fn == self._noop_virtual else None)
                log(f"[{stage}] {title} — 完成")
                self.feishu.send_text(f"✅ {title} 完成")
                return
            except Exception as e:  # noqa: BLE001 — driver 层兜底，错误进飞书
                self.ledger.record(
                    f"stages.{stage}",
                    {"title": title, "attempts": 1,
                     "errors": [f"{type(e).__name__}: {str(e)[:200]}"]},
                    add=True)
                last_err = f"{type(e).__name__}: {e}"
                log(f"[{stage}] 第{attempt}次失败: {last_err[:300]}")
                if attempt == 1:
                    time.sleep(5)
        self.ledger.flush()
        # 连败叫人 hint 路由：压缩 s04 走专用排查；legacy 车道 LLM 阶段（s04-s07）
        # 阶段专项指引（如 s05 枚举纠偏）与 Strata 手动步骤都要到人。勿按 "llama"/
        # "qwen" 子串劫持：Strata 引擎（llama.cpp 系）错误串也含 llama 字样，8888
        # 重启步骤已退役（#86），照做必然徒劳（同卡窗口内还触显存互斥）。
        if stage == "s04" and self.lane == "compressed":
            hint = STAGE_MANUAL_HINTS.get("s04-compressed", "") + "\n"
        elif stage in ("s04", "s05", "s06", "s07"):
            hint = STAGE_MANUAL_HINTS.get(stage, "") + "\n" + STRATA_MANUAL_STEPS + "\n"
        else:
            log_path = os.path.join(self.d["07_成片"], "generated.jsonl") if stage == "s08" else ""
            hint = STAGE_MANUAL_HINTS.get(stage, "").replace("{log}", log_path).replace(
                "{here}", HERE).replace("{intro}", INTRO_MP4) + \
                ("\n" if STAGE_MANUAL_HINTS.get(stage) else "")
        self.feishu.send_text(
            f"🚨 [{title}] 连败 2 次，需要人工/agent 介入。\n错误：{last_err[:300]}\n\n{hint}"
            f"现场已留存（stamps），处理后重启 driver 即从断点续跑；自己修不了就把本消息转给 agent。\nslug: {self.slug}")
        # 收尾：连败退场前清硬件占用——最常见残留正是假活 ComfyUI 抓着 ~13GB
        # （09-26/09-28 实锤），不清会把瞬时故障放大成次日全停；Strata 同停（#85
        # 窗口纪律兜底：不管停在哪个阶段，退场不留 LLM 服务占卡，幂等 already-off）
        try:
            if strata_window.WINDOW_MODE == "conflict":
                sd = strata_window.stop(notify=self.notify)
                log(f"[strata-window] 连败退场关窗: {sd.get('outcome')}")
        except Exception as e:  # noqa: BLE001 — 退场关窗失败不影响叫人
            log(f"[strata-window] 退场关窗异常（忽略）: {e}")
        try:
            import vision_server
            sd = vision_server.stop()
            log(f"[vision] 连败退场关判读窗（probe: {sd.get('probe', {}).get('detail', '')}）")
        except Exception as e:  # noqa: BLE001 — 退场清理失败不影响叫人
            log(f"[vision] 退场关窗异常（忽略）: {e}")
        try:
            import comfyui_selfheal
            sd = comfyui_selfheal.shutdown_comfyui(notify=self.notify)
            log(f"[comfy-heal] 连败退场清卡: {sd.get('outcome')}"
                f" (vram_after={sd.get('vram_after')} MiB)")
        except Exception as e:  # noqa: BLE001 — 收尾清理失败不影响叫人
            log(f"[comfy-heal] 收尾清理异常（忽略）: {e}")
        self.hb.exit(3, f"{stage} 连败 2 次")
        sys.exit(3)

    def notify(self, text):
        """飞书通知是尽力而为：发送失败只落日志，绝不允许炸穿主流程（2026-09-06 根治）。"""
        try:
            self.feishu.send_text(text)
        except Exception as e:  # noqa: BLE001
            log(f"[notify] 发送失败（忽略）: {e}")

    # ── 阶段实现 ────────────────────────────────────────────────────────
    def s01_segment(self):
        """阶段2/3a 分段 + 口播台词落盘（segment_script.py + driver 补齐台词目录）。"""
        raw_json = os.path.join(self.d["01_分段"], "分镜清单_raw.json")
        os.makedirs(self.d["01_分段"], exist_ok=True)
        rc, out = run_argv([sys.executable, os.path.join(HERE, "segment_script.py"),
                            self.script, raw_json])
        if rc != 0:
            raise RuntimeError(f"segment_script 失败: {out[-300:]}")
        self._write_scripts(raw_json)

    def _write_scripts(self, raw_json):
        """口播台词/shotXX.md（给 TTS 的纯原文）——原流程由 agent 手写，driver 补齐。"""
        sc_dir = os.path.join(self.d["01_分段"], "口播台词")
        os.makedirs(sc_dir, exist_ok=True)
        for s in json.load(open(raw_json, encoding="utf-8")):
            fp = os.path.join(sc_dir, f"{s['id']}.md")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(s["text"] + "\n")

    def s02_tts(self):
        """阶段7 提前（音频先行）：欢迎词 + 正文 TTS（Index-TTS，GPU）。"""
        os.makedirs(self.d["02_TTS语音"], exist_ok=True)
        # 欢迎词（每日日期不同，单独生成）
        m = re.match(r"\d{4}-(\d{1,2})-(\d{1,2})", self.date)
        welcome_text = f"欢迎来到每日新中国正面新闻简报，今天是{int(m.group(1))}月{int(m.group(2))}日，以下是今日的主要内容。"
        wdir = os.path.join(self.d["02_TTS语音"], "_welcome")
        os.makedirs(wdir, exist_ok=True)
        with open(os.path.join(wdir, "shot01.md"), "w", encoding="utf-8") as f:
            f.write(welcome_text + "\n")
        wout = os.path.join(self.d["02_TTS语音"], "_welcome")
        os.makedirs(wout, exist_ok=True)
        rc, out = run_argv(wsl_argv([os.path.join(HERE, "tts_batch.py"), wdir, VOICE_REF, wout]), timeout=1200)
        if rc != 0 or not os.path.exists(os.path.join(wout, "shot01.wav")):
            raise RuntimeError(f"欢迎词 TTS 失败: {out[-300:]}")
        # 正文 TTS
        sc_dir = os.path.join(self.d["01_分段"], "口播台词")
        rc, out = run_argv(wsl_argv([os.path.join(HERE, "tts_batch.py"), sc_dir, VOICE_REF,
                                     self.d["02_TTS语音"]]), timeout=7200)
        if rc != 0:
            raise RuntimeError(f"正文 TTS 失败: {out[-300:]}")

    def s03_backfill(self):
        """TTS 实测时长回填 raw.json（音频先行核心：导演按真实时长设计，无回填重跑循环）。"""
        raw_json = os.path.join(self.d["01_分段"], "分镜清单_raw.json")
        shots = json.load(open(raw_json, encoding="utf-8"))
        aud = self.d["02_TTS语音"]
        for s in shots:
            fp = os.path.join(aud, f"{s['id']}.wav")
            if not os.path.exists(fp):
                raise RuntimeError(f"TTS 音频缺失: {fp}")
            s["duration_est"] = round(ffprobe_dur(fp) + 0.3, 2)  # +0.3s 余量，与 sync_tts_durations 一致
        json.dump(shots, open(raw_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def s04_director(self):
        """s04 导演：方向卡自动选推荐项 + 整体方案 + 逐段设计（本地 Strata，#86 起；创意层）。

        目录配对（2026-09-07）：raw 读 01_分段，全部产物（方向卡/整体方案/
        交接摘要/shotXX/交接卡）落 03_导演设计，不再有 01→02 搬移。"""
        raw_json = os.path.join(self.d["01_分段"], "分镜清单_raw.json")
        d03 = self.d["03_导演设计"]
        os.makedirs(d03, exist_ok=True)
        style = ["--style", self.args.style] if self.args.style else []
        # 方向锁定（2026-09-07）：选定方向持久化到稿子旁，重跑（--fresh/换采样）不漂移
        lock_fp = os.path.join(self.slug, "00_口播稿", "_方向锁定.txt")
        locked = open(lock_fp, encoding="utf-8").read().strip() if os.path.exists(lock_fp) else ""
        if locked:
            log(f"方向锁定（沿用既往选择，跳过方向卡生成）: {locked}")
        # 方向卡（跑一次；已存在则直接解析。md=人读，json=机器读契约）
        card = os.path.join(d03, "00_创意方向卡.md")
        card_json = os.path.join(d03, "00_创意方向卡.json")
        if not locked and not os.path.exists(card) and not os.path.exists(card_json):
            rc, out = run_argv([sys.executable, os.path.join(HERE, "director_llm.py"), raw_json,
                                "--out-dir", d03] + style,
                               env=self._strata_env(), timeout=1800)
            if rc != 0 or not os.path.exists(card):
                raise RuntimeError(f"方向卡生成失败: {out[-300:]}")
        # 自动选方向：优先结构化 JSON（机器读取契约，2026-09-05 方向卡 JSON 化），
        # 旧 md 卡（续跑现场）回退正则解析；已锁定时直接沿用
        name = locked
        if not name and os.path.exists(card_json):
            try:
                dirs = json.load(open(card_json, encoding="utf-8")).get("directions", [])
                if dirs:
                    name = str(dirs[0].get("方向名", "")).strip().strip("*").strip()
            except Exception as e:
                log(f"方向卡 JSON 解析失败（回退 md 正则）: {e}")
        if not name:
            self._clean_direction_card(card)
            for line in open(card, encoding="utf-8"):
                m = re.match(r"#*\s*\*{0,2}(?:方案)?方向[一二三四五1-5][：:：]?\s*(.+)", line.strip())
                if not m:
                    continue
                cand = m.group(1).strip().split("（")[0].strip().rstrip("*").strip()
                if not cand or "<" in cand or "【" in cand or "】" in cand:
                    continue
                name = cand
                break
        if not name:
            raise RuntimeError("方向卡解析失败：JSON 与 md 均未取得方向名")
        if not locked:
            with open(lock_fp, "w", encoding="utf-8") as f:
                f.write(name + "\n")
            log(f"方向已锁定 → {lock_fp}")
        log(f"选定方向: {name}")
        # 整体方案 + 逐段设计（统一落 03_导演设计）；并发可调（DRIVER_DIRECTOR_CONCURRENCY）
        rc, out = run_argv([sys.executable, os.path.join(HERE, "director_llm.py"), raw_json,
                            "--out-dir", d03, "--selected-direction", name,
                            "--concurrency", str(self.director_concurrency)] + style,
                           env=self._strata_env(), timeout=7200)
        if rc != 0:
            raise RuntimeError(f"导演设计失败: {out[-300:]}")
        moved = len(glob.glob(os.path.join(d03, "shot*.json")))
        log(f"逐段设计 {moved} 段 → 03_导演设计/")
        # 设计多样性台账：景别分布 + 横移类运镜占比（单调监测 + 旋转高危前置预警）
        self._record_design_stats(d03)
        # 交接摘要（≤15 行，LLM 提炼）
        self._make_summary(d03)

    def _record_design_stats(self, d03):
        """统计导演设计的景别分布与横移类运镜占比，入 report。
        0904 教训：导演连续 10+ 段「全景+平稳横移（长卷）」——既是单调根源，也是旋转高危运镜。"""
        from collections import Counter
        types, lateral, total = Counter(), 0, 0
        move_dist = Counter()
        for fp in sorted(glob.glob(os.path.join(d03, "shot*.json"))):
            try:
                dj = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            total += 1
            # 结构化枚举字段优先（2026-09-07），文本正则兜底（旧项目）
            jb = dj.get("景别")
            if jb:
                types[jb] = types.get(jb, 0) + 1
            else:
                cam = str(dj.get("镜头", "")).strip()
                m = re.match(r"^(大全景|大远景|远景|全景|中景|近景|特写|过肩)", cam)
                if m:
                    types[m.group(1)] += 1
            mv = dj.get("运镜")
            if mv:
                move_dist[mv] += 1
            else:
                cam = str(dj.get("镜头", "")).strip()
                if re.search(r"横移|长卷|平行推移|匀速行进", cam):
                    lateral += 1
            if mv == "横移":
                lateral += 1
        if move_dist:
            self.ledger.record("s04.move_dist", dict(move_dist))
        if not total:
            return
        self.ledger.record("s04.shot_types", dict(types))
        self.ledger.record("s04.lateral_design_ratio", round(lateral / total, 2))
        log(f"[s04] 设计景别分布: {dict(types)}；横移类运镜 {lateral}/{total}"
            + (" ⚠️ 超 1/3（旋转高危+单调）" if lateral / total > 1 / 3 else ""))

    def _clean_direction_card(self, card):
        """剥离 free-text 调用泄漏的推理前缀：卡片正文从第一个真实「## 方向X：名字」行开始。"""
        lines = open(card, encoding="utf-8").readlines()
        start = None
        for i, line in enumerate(lines):
            m = re.match(r"#*\s*\*{0,2}(?:方案)?方向[一二三四五1-5][：:：]?\s*(.+)", line.strip())
            if m and "<" not in m.group(1):
                start = i
                break
        if start:
            body = lines[start:]
            if not body[0].startswith("#"):
                body.insert(0, "# 创意方向卡（自动选推荐项）\n\n")
            cleaned = "".join(body).strip() + "\n"
            if cleaned != "".join(lines):
                with open(card, "w", encoding="utf-8") as f:
                    f.write(cleaned)
                log(f"方向卡已剥离推理前缀（{len(lines)-start} 行正文）")

    def _make_summary(self, d01):
        import llm_json, schema
        overall = os.path.join(d01, "00_整体视觉方案.md")
        summary = os.path.join(d01, "00_交接摘要.md")
        if os.path.exists(summary):
            return
        plan = open(overall, encoding="utf-8").read()
        # JSON 通道（0907）：free-text 曾把思维链带进摘要，且 plan[:4000] 截断让模型
        # 没看到全部设计自己编方向；llm_call_json 括号配对抗推理 + schema 强字段 +
        # 五字段每字段一行，行数硬约束（自检门②）天然满足。
        obj = llm_json.llm_call_json(
            "你是导演组交接员。把整体视觉方案提炼为交接摘要 JSON（值=中文，每值 ≤80 字，不要推理过程）。",
            plan + "\n\n输出 JSON，字段：方向名 / 视觉主线 / 风格锁 / 色彩基线 / 禁忌。",
            schema.HANDOFF_SUMMARY_SCHEMA, model=os.environ.get("LLM_MODEL", "strata"),
            budget=1200, verbose=False)
        lines = [f"**{k}**：{obj.get(k, '')}"
                 for k in ("方向名", "视觉主线", "风格锁", "色彩基线", "禁忌")]
        with open(summary, "w", encoding="utf-8") as f:
            f.write("# 交接摘要\n\n" + "\n".join(lines) + "\n")

    def _strata_env(self):
        """legacy 车道 s05-s07 子进程 LLM env 注入（#86 起指 Strata；8888 托管 env 退役）。
        端点单一事实源 = strata_h3_lane（LANE_STRATA_URL）；不传 LLM_THINKING =
        关思维链缺省（#29 结论，llm_json 侧 chat_template_kwargs 同口径）。"""
        return {"LLM_API": f"{LANE_STRATA_URL}/v1/chat/completions"}

    def s05_photographer(self):
        """阶段4 摄影师 + 七项自检（Strata）。风格锁经 01/交接摘要 注入（此脚本无 --style 参数）。"""
        enhanced = os.path.join(self.d["04_分镜增强"], "分镜清单_enhanced.json")
        os.makedirs(self.d["04_分镜增强"], exist_ok=True)
        rc, out = run_argv([sys.executable, os.path.join(HERE, "storyboard_photographer.py"),
                            os.path.join(self.d["01_分段"], "分镜清单_raw.json"),
                            self.d["03_导演设计"], self.d["03_导演设计"], enhanced,
                            "--concurrency", "4"], env=self._strata_env(), timeout=7200)
        if rc != 0 or not os.path.exists(enhanced):
            raise RuntimeError(f"摄影师失败: {out[-300:]}")
        check_argv = [sys.executable, os.path.join(HERE, "storyboard_enhancer.py"), enhanced,
                      "--design-dir", self.d["03_导演设计"],
                      "--out-report", os.path.join(self.d["04_分镜增强"], "_自检报告.md")]
        rep_fp = os.path.join(self.d["04_分镜增强"], "_自检报告.md")
        rc, out = run_argv(check_argv)
        if rc != 0:
            # ⑦ 景别硬重复死锁的自动恢复（ADR 0003 枚举级纠偏，#47）：
            # 纠偏 + 复检一次；仍失败按现有协议重试 → 叫人（0908 实证重试不可能自愈，
            # 纠偏是唯一自愈路径，其余失败项仍需人工）
            rc, out = self._enum_recheck(check_argv, rep_fp)
            if rc != 0:
                raise RuntimeError(f"七项自检失败（含枚举级纠偏复检）: {out[-300:]}")
        # 交接卡（2026-09-07 补位：driver 此前从未调用 gen_handoff，s07 五路汇聚
        # 实际缺「交接卡」一路，一直走整体方案全文 fallback）
        rc, out = run_argv([sys.executable, os.path.join(HERE, "gen_handoff.py"),
                            self.d["03_导演设计"], enhanced,
                            os.path.join(self.d["03_导演设计"], "00_交接摘要.md"),
                            "--out", os.path.join(self.d["03_导演设计"], "交接卡")])
        if rc != 0:
            raise RuntimeError(f"交接卡生成失败: {out[-300:]}")

    def _enum_recheck(self, check_cmd, rep_fp):
        """⑦ 景别交替硬重复 → 枚举级纠偏（ADR 0003）后复检一次。
        只改冲突对靠后段的「景别」闭合枚举（json + md 镜像），语义字段逐字不动；
        台账 report.json enum_corrections 留痕（纠偏即写盘，防后续崩溃丢失）。
        返回 (rc, out)：无⑦失败项 / 报告不可读 / 无可纠候选 / 复检仍败 一律返回
        失败，由调用方按现有协议处理（纠偏只兜枚举冲突，不兜其余自检失败）。
        注意："⑦景别交替" 标签与 storyboard_enhancer 的检查名一致（报告契约）。"""
        try:
            rep = json.load(open(rep_fp, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — 报告不可读则纠偏无从谈起
            return 1, f"自检报告不可读，枚举级纠偏跳过: {e}"
        if not any(f.get("check") == "⑦景别交替" for f in rep.get("failed_items", [])):
            return 1, "失败项不含⑦景别交替，枚举级纠偏不适用"
        records = enum_correct.apply_corrections(
            self.d["03_导演设计"], candidates=rep.get("correction_candidates"))
        if not records:
            return 1, "⑦ 失败但无可纠候选（冲突值非闭合枚举，保持人工路径）"
        for r in records:
            self.ledger.record_event("enum_corrections", r)
        for r in records:
            log(f"[s05] 枚举级纠偏 {r['id']}: 景别 {r['from']} → {r['to']}"
                f"（前镜 {r['prev']} / 后镜 {r['next'] or '∅'}），已留痕台账")
        self.ledger.flush()
        return run_argv(check_cmd)

    def s06_art(self):
        """阶段5 美术画质（Strata）。"""
        out = os.path.join(self.d["05_美术画质"], "画质设计.json")
        os.makedirs(self.d["05_美术画质"], exist_ok=True)
        style = ["--style", self.args.style] if self.args.style else []
        rc, o = run_argv([sys.executable, os.path.join(HERE, "art_director.py"),
                          os.path.join(self.d["04_分镜增强"], "分镜清单_enhanced.json"),
                          self.d["03_导演设计"],
                          os.path.join(self.d["03_导演设计"], "00_交接摘要.md"),
                          "--out", out, "--concurrency", "4"] + style,
                         env=self._strata_env(), timeout=3600)
        if rc != 0 or not os.path.exists(out):
            raise RuntimeError(f"美术设计失败: {o[-300:]}")

    def s07_h3prompt(self):
        """阶段6 H3 画面 prompt（Strata，含竖屏约束强制注入）。"""
        os.makedirs(self.d["06_H3prompt"], exist_ok=True)
        style = ["--style", self.args.style] if self.args.style else []
        rc, out = run_argv([sys.executable, os.path.join(HERE, "director_to_h3_llm.py"),
                            self.d["03_导演设计"], self.d["06_H3prompt"],
                            "--durations", os.path.join(self.d["04_分镜增强"], "分镜清单_enhanced.json"),
                            "--handoff", os.path.join(self.d["03_导演设计"], "交接卡"),
                            "--art", os.path.join(self.d["05_美术画质"], "画质设计.json"),
                            "--overall", self.d["03_导演设计"],
                            "--concurrency", "4"] + style,
                           env=self._strata_env(), timeout=7200)
        # 完整输出落盘：异常消息只带 out[-300:]，截断曾把连败原因丢得不可考（0906 实证）
        try:
            with open(os.path.join(self.stamps_dir, "s6_last_run.log"), "w", encoding="utf-8") as f:
                f.write(out or "")
        except OSError:
            pass
        # 硬对账门：prompt ID 集合 == 分镜清单段 ID 集合（数量相等不够——ID 错位也算失败）
        raw = json.load(open(os.path.join(self.d["01_分段"], "分镜清单_raw.json"), encoding="utf-8"))
        want = {s["id"] for s in raw}
        have = {os.path.basename(fp)[:-5] for fp in glob.glob(os.path.join(self.d["06_H3prompt"], "*.json"))}
        missing = sorted(want - have)
        if missing:
            raise RuntimeError(f"H3 prompt 缺段（{len(have & want)}/{len(want)}）: 缺 {missing}；脚本尾部: {out[-300:]}")
        # 文件齐但脚本报失败 = 连续重试仍不合格（失败段残留产物已被脚本清掉，见 director_to_h3_llm）
        if rc != 0:
            m = re.search(r"RESULT .*fail=(\[[^\]]*\])", out)
            raise RuntimeError(f"H3 prompt 单段连败 {m.group(1) if m else ''}（{len(have & want)}/{len(want)} 段在盘但含不合格）；"
                               f"重跑 s07 只补失败段（增量跳过已合格段）；脚本尾部: {out[-300:]}")
        m = re.search(r"RESULT total=(\d+) ok=(\d+) fail=(\[[^\]]*\])", out)
        sanitized = sum(1 for fp in glob.glob(os.path.join(self.d["06_H3prompt"], "*.json"))
                        if json.load(open(fp, encoding="utf-8")).get("camera_sanitized"))
        self.ledger.record("s07", {"total": len(want), "ok": len(have & want),
                                   "fail": m.group(3) if m else "n/a", "camera_sanitized": sanitized})
        log(f"[s07] 对账 {len(have & want)}/{len(want)} 全通过"
            + (f"，运镜消毒 {sanitized} 段" if sanitized else ""))

    def _whisper_thread(self):
        """字幕 whisper 词级时间戳（CPU 车道，与 H3 GPU 车道并行）。"""
        wsdir = os.path.join(self.slug, "_wsout")
        os.makedirs(wsdir, exist_ok=True)
        for a in sorted(glob.glob(os.path.join(self.d["02_TTS语音"], "shot*.wav"))):
            sid = os.path.basename(a)[:-4]
            if os.path.exists(os.path.join(wsdir, f"{sid}.json")):
                continue
            # #150 AC2 实测定案：生产实走 GPU 与 H3 抢卡（原「GPU 空闲」前提证伪），
            # 补 --device cpu + CUDA_VISIBLE_DEVICES= 强制 CPU 车道；wsl.exe 单层。
            rc, out = run_argv(["wsl.exe", "-d", WSL_DISTRO, "env", "CUDA_VISIBLE_DEVICES=",
                                "whisper", win_to_wsl(a), "--model", "turbo", "--language", "zh",
                                "--word_timestamps", "True", "--device", "cpu",
                                "--output_format", "json", "--output_dir", win_to_wsl(wsdir)],
                               timeout=1800)
            if rc != 0:
                log(f"[whisper] {sid} 失败（字幕车道继续）: {out[-200:]}")
        self._ws_done = True

    def s08_h3(self):
        """阶段8 H3 生成（GPU 串行）→ 抽帧竖屏检查（vision 模型）→ 翻转重生成 ≤1 轮。"""
        # ComfyUI 僵尸自愈（2026-09-28：09-26/09-28 两次烧卡 CUDA 猝死后 ComfyUI
        # 进程假活占 ~13GB 显存不放 → window_close 显存回落闸被堵死 → 连败退出全天停摆。
        # 入口先探活，不健康则清僵尸+重启；详见 comfyui_selfheal 模块头注释）
        import comfyui_selfheal
        heal = comfyui_selfheal.heal(notify=self.notify)
        if heal.get("actions"):
            log("[comfy-heal] " + "；".join(heal["actions"]))
            self.ledger.record("comfyui_selfheal", heal)
        # 同卡互斥（硬约束#1）：WSL 与 Windows ComfyUI 共用一张 5080。
        # 显存窗口纪律（#85，strata_window.WINDOW_MODE=conflict 默认）：Strata 常驻
        # 即占 94% VRAM（#83 §3 实测冲突），s08 生图前 taskkill 停服——s07 完成后进入
        # s08 前自动关窗：探活转离线 + 显存回落双确认后才放行烧卡；s05/s06/s07 为
        # 虚拟 stamp，s08 入口即 s07 完成后的首个执行点，跨进程断点续跑同样经此关窗。
        # coexist 模式（显存充裕的机器）跳过关窗常驻。停止失败（探活仍在线/显存未
        # 回落）≠ 一定是 Strata 没退干净：更常见是 ComfyUI CUDA 猝死后进程假活占着
        # ~13GB（09-26/09-28 实锤）→ 自愈（清僵尸/卸缓存）后重停一次。
        # 旧 KVMem window_close 与 WSL 侧 llama-server pkill 残留守卫随 #85 删除
        # （Strata 在 Windows 侧，WSL 无人再拉 llama-server）。
        if strata_window.WINDOW_MODE == "conflict":
            try:
                info = strata_window.stop(notify=self.notify)
            except RuntimeError as e:
                heal2 = comfyui_selfheal.heal(notify=self.notify,
                                              vram_gate_mib=strata_window.VRAM_USED_MAX)
                log("[comfy-heal] " + "；".join(heal2.get("actions") or ["无动作"]))
                self.ledger.record("comfyui_selfheal", heal2)
                if not heal2.get("ok"):
                    raise
                info = strata_window.stop(notify=self.notify)
            self.ledger.record("strata_window.s08_entry_close", info)
        prompts = sorted(glob.glob(os.path.join(self.d["06_H3prompt"], "*.json")))
        # s08 预检对账：进 GPU 前以 raw 段 ID 全集为权威（缺尾段曾因 len(prompts) 权威静默过检）
        raw = json.load(open(os.path.join(self.d["01_分段"], "分镜清单_raw.json"), encoding="utf-8"))
        ids = [s["id"] for s in raw]
        have = {os.path.basename(fp)[:-5] for fp in prompts}
        missing = [sid for sid in ids if sid not in have]
        if missing:
            raise RuntimeError(f"s08 预检：prompt 缺段 {missing}（不烧 GPU，先修 s07）")
        n = len(ids)
        vdir = os.path.join(self.d["07_成片"], "1080P")
        os.makedirs(vdir, exist_ok=True)
        # whisper CPU 车道提前启动（双车道：GPU 生成 / CPU 转写）
        self._ws_started = True
        self._ws_done = False
        wt = threading.Thread(target=self._whisper_thread, daemon=True)
        wt.start()
        self._h3_submit(ids, vdir)
        # 产物完整性：按 raw ID 全集核对（不再以 prompt 数为权威）
        missing = [sid for sid in ids
                   if not glob.glob(os.path.join(vdir, f"*{sid}*.mp4"))
                   and not os.path.exists(os.path.join(vdir, f"{sid}.mp4"))]
        if missing:
            raise RuntimeError(f"H3 产物缺段: {missing}（查 {self.d['07_成片']}/generated.jsonl 与 Windows ComfyUI output）")
        # 竖屏检查 + 重生成梯队（2026-09-12 用户拍板「出错必消毒」）：
        # 1) 删除式消毒 + 重采样：把该段 prompt 里全部运动语义句整句删除（orbit/横移/
        #    推拉/扫掠全家族）+ 否定式约束块换正向表述 + strict 横构图场景语义三级删除，
        #    无运动语义 = 无倾斜诱因，画面确定性竖直（原样重采样已废除：0906/0912 两期
        #    实证重掷同概率不放大概率，还烧 4.5 分钟 GPU）
        # 2) 按因竖直化重写（LLM 兜底）→ 仍翻 = 人审点名
        flipped, tilted = self._check_frames(vdir)
        # T16 #128：_check_frames 内已接判官（round0 全量、复检轮 only=本轮重烧段）。
        # 判官生效时 flipped=重烧名单（confirmed+judge_found，判官为准）；
        # local_false（本地误报）不在其中 → 天然不删产物/不重烧/不计入末尾
        # still_flipped，判词与分歧落顶层平键 judge_rounds / judge_disagreements。
        # fail-open（判官不可用）时 flipped=本地名单，行为与旧版完全一致。
        rounds = []
        strat = ((1, "strip"), (2, "rewrite"))
        for round_no, mode in strat:
            if not flipped:
                break
            mode_label = {"strip": "（删除式消毒：运动句整删）",
                          "rewrite": "（按因竖直化重写版 prompt）"}.get(mode, "（原 prompt 二次采样）")
            rounds.append({"round": round_no, "shots": flipped, "mode": mode or "resample"})
            self.notify(f"⚠️ 抽帧检查发现 {len(flipped)} 段疑似翻转（{flipped}），"
                        f"第 {round_no} 轮重生成（判官为准名单）{mode_label}")
            # rewrite 轮分相（#85 窗口纪律与烧卡的交点）：LLM 重写全部翻转段（Strata
            # 在线）→ 停服 → 再统一烧卡。旧结构逐段「LLM 重写→立即烧卡」与 Strata
            # 94% VRAM 常驻互斥（#83 §3），带着 LLM 服务烧 H3 必 OOM——rewrite 是
            # #86 遗留的窗口缝，本票以「先文案后烧卡」两相闭合。rewrite 轮的前提
            # 是 06_H3prompt 齐段（s08 预检已核对），必有段可重写，直接开窗。
            # 豁免说明：此窗使单视频加载次数可能到 3 次，超出「最多两次」的常态
            # 口径——但 rewrite 仅在翻转实际发生时触发（异常修复路径，两期实证
            # 0-4 段），常态仍为两次；为守住「活着但劣化不重启」的 #83 结论不并窗。
            strata_reopen = mode == "rewrite"
            if strata_reopen:
                info = strata_window.ensure_online(notify=self.notify)
                self.ledger.record("strata_window.s08_rewrite_reopen", info)
            # 2026-10-20 修（shot19 实锤）：先改 prompt，改成功才删产物重烧。
            # 原顺序是「先删产物再重写」，重写失败（如 Strata 离线）时产物已删、
            # prompt 没变 → 重烧出同一画面（或被 submit_accel 的「产物已存在」
            # SKIP 成零动作），白等一轮 GPU 却报「已重烧」。
            reburn = []
            for sid in flipped:
                if mode == "rewrite" and not self._rewrite_prompt_vertical(sid):
                    log(f"[s08] {sid} 竖直化重写不可用，本轮跳过重烧（保留现有画面）")
                    continue
                # 重生成前必须删旧产物：_h3_submit 按"产物已存在"跳过，
                # 不删则翻转段永远重生成不了（0906 期 4 段实锤漏进成片）
                for old in glob.glob(os.path.join(vdir, f"*{sid}*.mp4")):
                    os.remove(old)
                    log(f"[s08] {sid} 判翻转，删旧产物强制重烧: {os.path.basename(old)}")
                # 同步清 submit_accel 的完成记录（0907 审查发现的 b978fba 残留缝隙：
                # 它内部还按 generated.jsonl 的 done 集合跳过，只删产物仍会被 SKIP）
                gj = os.path.join(self.d["07_成片"], "generated.jsonl")
                if os.path.exists(gj):
                    kept = [l for l in open(gj, encoding="utf-8") if f'"{sid}"' not in l]
                    if len(kept) != sum(1 for _ in open(gj, encoding="utf-8")):
                        open(gj, "w", encoding="utf-8").writelines(kept)
                        log(f"[s08] {sid} 已从 generated.jsonl 移除完成记录")
                if mode == "strip":
                    self._strip_motion_prompt_file(sid)
                reburn.append(sid)
            if strata_reopen:
                info = strata_window.stop(notify=self.notify)
                self.ledger.record("strata_window.s08_rewrite_close", info)
            if not reburn:
                log(f"[s08] 第 {round_no} 轮无可重烧段（{flipped} 全部保留现画面），退出重生成梯队")
                break
            for sid in reburn:
                self._h3_submit([sid], vdir)
            flipped = reburn
            # 复检轮只判本轮重烧段（judge recheck via only=…），判官也只复核这批
            flipped, _ = self._check_frames(vdir, only=flipped,
                                            evidence_tag=f"round{round_no}")
        self.ledger.record("s08.segments", n)
        self.ledger.record("s08.flip_regen_rounds", rounds)
        self.ledger.record("s08.still_flipped", flipped)
        # #194：3-unfixed 收尾补写。重烧梯队跑完仍翻的段是最难正类（「修不好」，
        # 不是「还没修好」）——此前只在 docstring 里被声明、生产侧没有写者。
        # 落点选在 dataset 记录与下方 still_flipped raise 之前：硬失败路径下
        # 证据帧与 manifest 条目也已经存账，不随进程退出丢。存证失败不阻塞
        # 主链（与 1/2 两类归档同语义）。
        try:
            self._archive_unfixed_evidence(flipped, rounds)
        except Exception as e:  # noqa: BLE001 — 存证失败不阻塞收尾
            log(f"[evidence] 3-unfixed 收尾归档异常（不阻塞）: {e}")
        # 旋转数据集（2026-09-05 用户拍板：白名单缓行，先跨期攒数据看共性再集中分析）
        # T16 #128：判官结论只进 judge_* 字段（字段名照 t15_replay L283-288），
        # vision_verdict 保持本地判读原样不受判官覆盖。
        try:
            from rotation_dataset import append_records, build_records, classify_backend
            vb, vm = classify_backend(  # #103 归因：结论可区分云端 glm 与本地模型
                os.environ.get("DRIVER_VISION_API", VISION_API_DEFAULT), VISION_MODEL)
            judge_map = self._judge_fields_for_dataset()
            recs = build_records(self.slug, self.date, self.d["06_H3prompt"],
                                 self.d["03_导演设计"],
                                 self.ledger.get("s08.vision") or {},
                                 {sid: rr["round"] for rr in rounds for sid in rr["shots"]},
                                 vision_backend=vb, vision_model=vm, judge=judge_map)
            append_records(recs)
            log(f"[dataset] 旋转数据集 +{len(recs)} 条 → references/rotation-dataset.jsonl")
        except Exception as e:  # noqa: BLE001
            log(f"[dataset] 记录失败（不影响主流程）: {e}")
        # ADR 0008 决策 7（#189）：s08 收尾显式落盘——数据集写入之后立即 flush，
        # 后续判读关窗 / still_flipped raise 之前本轮进度已在盘上（崩溃不丢整轮账）。
        self.ledger.flush()
        # 判读窗口关闭（2026-10-14 用户拍板：全本地模型统一用完即关，ADR 0007
        # 「CPU 常驻」例外取消）——判读/重烧全部完成即停 8100；gate④ 镜头级重做
        # 经 _check_frames → _ensure_vision 自愈阶梯自动重拉，不依赖常驻。
        # 停止按命令行精确匹配 --port 8100（vision_server.stop），不误伤 Strata。
        try:
            import vision_server
            sd = vision_server.stop()
            log(f"[vision] 判读窗口关闭（probe: {sd.get('probe', {}).get('detail', '')}）")
        except Exception as e:  # noqa: BLE001 — 关窗失败不影响已交付
            log(f"[vision] 判读关窗异常（忽略）: {e}")
        if flipped:
            raise RuntimeError(f"重生成后仍翻转: {flipped}（需要人工/agent 处理）")
        if tilted:
            log(f"[s08] 倾斜段（只标记，确认门④人审定夺）: {tilted}")

    def _strip_motion_prompt_file(self, sid, strict=True):
        """删除式消毒（2026-09-11 用户拍板）：整句删除运动语义句，置 motion_stripped。
        strict=True（默认，发现歪之后的清理）：横向构图场景语义也按三级清单删除
        （用户拍板「所有可能诱因都删」）。零命中（prompt 已无诱因）时不动文件。"""
        import schema
        fp = os.path.join(self.d["06_H3prompt"], f"{sid}.json")
        rec = json.load(open(fp, encoding="utf-8"))
        prompt, n_del = schema.strip_motion_text(rec.get("prompt", ""), strict=strict)
        if not n_del:
            log(f"[s08] {sid} 无诱因可删（纯重采样）")
            return
        rec["prompt"] = prompt
        rec["motion_stripped"] = True
        rec["camera_sanitized"] = True
        json.dump(rec, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        for old in glob.glob(os.path.join(self.d["07_成片"], "1080P", f"*{sid}*.mp4")):
            os.remove(old)
        log(f"[s08] {sid} 删除式消毒 strict={strict}（删 {n_del} 处诱因），旧视频已删")

    def _regen_shots_strip_motion(self, shots, evidence_tag="gate4"):
        """镜头级重生成（确认门④「重生成 shotXX」）：删除式消毒 → 重烧 → 复检，
        仍翻对该段竖直化重写再烧一次。返回仍翻段列表。
        evidence_tag：复判定罪帧的台账/目录标签（默认 gate4；T17 指认回路传 human_flag）。"""
        vdir = os.path.join(self.d["07_成片"], "1080P")
        for sid in shots:
            for old in glob.glob(os.path.join(vdir, f"*{sid}*.mp4")):
                os.remove(old)
                log(f"[gate] {sid} 删旧产物: {os.path.basename(old)}")
            gj = os.path.join(self.d["07_成片"], "generated.jsonl")
            if os.path.exists(gj):
                kept = [l for l in open(gj, encoding="utf-8") if f'"{sid}"' not in l]
                if len(kept) != sum(1 for _ in open(gj, encoding="utf-8")):
                    open(gj, "w", encoding="utf-8").writelines(kept)
            self._strip_motion_prompt_file(sid)
            self._h3_submit([sid], vdir)
        still, _ = self._check_frames(vdir, only=shots, evidence_tag=evidence_tag)
        if still:
            self.notify(f"⚠️ 删除式消毒后仍翻转: {still}，对该段按因竖直化重写再烧一次")
            rewritten = []
            for sid in still:
                # 2026-10-20 修（shot19 实锤）：重写失败时**不得**删旧产物再提交——
                # 删了产物才提交，prompt 却没变，重烧出来的是同一画面（甚至被
                # submit_accel 的「产物已存在」SKIP 掉变成零动作），却对外宣称
                # 「已按因竖直化重烧」。改为先重写、成功才删产物重烧。
                if self._rewrite_prompt_vertical(sid):
                    for old in glob.glob(os.path.join(vdir, f"*{sid}*.mp4")):
                        os.remove(old)
                        log(f"[gate] {sid} 删旧产物（竖直化重写后重烧）: {os.path.basename(old)}")
                    self._h3_submit([sid], vdir)
                    rewritten.append(sid)
            if rewritten:
                still, _ = self._check_frames(vdir, only=rewritten, evidence_tag=evidence_tag + "b")
            elif still:
                log(f"[s08] 竖直化重写全部不可用，{still} 保留消毒版画面（不空烧），转人审")
        # gate④ 重做的判读窗口同步关闭（同 s08 收尾语义；再进 s09+ 无判读需求）
        try:
            import vision_server
            vision_server.stop()
            log("[vision] gate④ 重做判读窗口关闭")
        except Exception as e:  # noqa: BLE001 — 关窗失败不影响交付
            log(f"[vision] gate④ 关窗异常（忽略）: {e}")
        return still

    def _prompt_is_lateral(self, sid):
        """该段 prompt 是否命中横向行进运镜（决定重生成是否直接用消毒版）。"""
        import schema
        fp = os.path.join(self.d["06_H3prompt"], f"{sid}.json")
        if not os.path.exists(fp):
            return False
        return bool(schema.lateral_camera_sentences(json.load(open(fp, encoding="utf-8")).get("prompt", "")))

    def _prompt_portrait_risk(self, sid):
        """该段是否 s07 标记横构图风险词命中（决定 vision 判首帧还是中帧——
        2026-10-14 拍板：风险段首帧单判，三帧复核已废弃）。"""
        fp = os.path.join(self.d["06_H3prompt"], f"{sid}.json")
        if not os.path.exists(fp):
            return False
        return bool(json.load(open(fp, encoding="utf-8")).get("portrait_risk"))

    def _rewrite_prompt_vertical(self, sid):
        """按因竖直化重写（2026-09-08）：翻转的根因多是场景级横构图语义
        （俯瞰/环形/横排），运镜消毒打不到。用 LLM 以「翻转失败+原 prompt」
        重写 imd：保留 [Shot N]/竖屏约束句/时间轴/旁白句，场景改竖直纵深。
        走本地 Strata（#86 起文案链全本地；显存窗口纪律见 s08 入口注释，归 #85）。
        失败不阻塞（退回消毒路径）。
        返回 True=prompt 已改写、可以重烧；False=没改写（调用方不得删产物重烧，
        否则是零动作空烧，见 2026-10-20 shot19 实锤）。"""
        fp = os.path.join(self.d["06_H3prompt"], f"{sid}.json")
        if not os.path.exists(fp):
            return False
        try:
            import llm_json
            rec = json.load(open(fp, encoding="utf-8"))
            imd = str(rec.get("prompt", ""))
            obj = llm_json.llm_call_json(
                "你是 H3 视频提示词修复专家。以下 prompt 生成的视频画面出现了 90° 翻转/横构图。",
                imd + "\n\n请重写 integrated_multimodal_description 字段（其余字段原样保留），要求：\n"
                "1. 保留全部 [Shot N] 分段结构与时间戳、竖屏约束句（第一句）、结尾旁白句\n"
                "2. 场景构图整体改为竖直纵深：地面/通道从前景延伸到画面顶部灭点，竖直结构（建筑/光柱/人物）沿垂直方向堆叠\n"
                "3. 移除/替换俯瞰、鸟瞰、环形环绕、水平横排等横构图语义\n"
                "4. 运镜只保留纵向（tilt up/down）与缓慢前推\n"
                "5. 严禁否定式表述（never/not/forbidden/no + rotated/tilted/sideways 等词）——"
                "否定句会反向诱发旋转（实证）；只写正向描述\n"
                "输出 JSON：{\"integrated_multimodal_description\": \"重写后的完整英文描述\"}",
                {"type": "object", "required": ["integrated_multimodal_description"],
                 "properties": {"integrated_multimodal_description": {"type": "string"}}},
                model=os.environ.get("LLM_MODEL", "strata"), budget=3000, verbose=False)
            new_imd = obj["integrated_multimodal_description"]
            snd = re.search(r"overall_soundscape:\s*([\s\S]*?)\n\nnon_diegetic_music", imd)
            rec["prompt"] = (f"integrated_multimodal_description:\n{new_imd}\n\n"
                             f"overall_soundscape:\n{snd.group(1).strip() if snd else ''}\n\n"
                             f"non_diegetic_music: N/A")
            json.dump(rec, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            log(f"[s08] {sid} prompt 已按因竖直化重写（{len(new_imd)} 字符）")
            return True
        except Exception as e:  # noqa: BLE001 — 重写失败退回消毒路径
            log(f"[s08] {sid} 竖直化重写失败（退回消毒）: {e}")
            return False

    def _sanitize_prompt_file(self, sid):
        """运镜消毒（s08 车道内，纯文本无 LLM）：横向行进运镜句 → 竖直安全句，删旧视频强制重烧。"""
        import schema
        fp = os.path.join(self.d["06_H3prompt"], f"{sid}.json")
        rec = json.load(open(fp, encoding="utf-8"))
        prompt, nfix = schema.sanitize_camera_text(rec.get("prompt", ""))
        if not nfix:
            return
        rec["prompt"] = prompt
        rec["camera_sanitized"] = True
        json.dump(rec, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        for old in glob.glob(os.path.join(self.d["07_成片"], "1080P", f"*{sid}*.mp4")):
            os.remove(old)
        log(f"[s08] {sid} prompt 运镜消毒（{nfix} 句），旧视频已删")

    def _h3_submit(self, ids, vdir):
        """逐段提交 H3（GPU 串行）。ids 为 raw 段 ID 列表（如 ['shot01',...]）。
        产物完成状态单一事实源 = 产物文件（driver 与 submit_accel 同一判据，
        2026-09-08 根治双状态源漂移）；generated.jsonl 仅审计日志。"""
        todo = []
        for sid in ids:
            if not glob.glob(os.path.join(vdir, f"*{sid}*.mp4")) and \
                    not os.path.exists(os.path.join(vdir, f"{sid}.mp4")):
                todo.append(sid)
        if not todo:
            log(f"H3 生成 {ids[0]}-{ids[-1]}：全部段已有产物，跳过提交")
            return
        log(f"H3 生成（GPU 串行，Windows ComfyUI）：待烧 {todo}")
        for sid in todo:
            i = int(re.sub(r"\D", "", sid))
            log(f"  提交 {sid}")
            rc, out = run_argv([sys.executable, os.path.join(HERE, "submit_accel.py"),
                                str(i), str(i), self.d["06_H3prompt"],
                                "--script", self.script, "--res", "1080P"],
                               timeout=7200)  # 单段 2h 上限
            if rc != 0:
                raise RuntimeError(f"submit_accel {sid} 失败: {out[-300:]}")

    def _ensure_vision(self):
        """本地判读服务自愈阶梯（T2 #104 / ADR 0007；语义对齐 strata_window.
        ensure_online）：探活 → 失联自动拉起一次 → 就绪等待 → 彻底失败降级留痕
        （台账 s08.vision_server），不放弃当日——判读失败段照旧进确认门④九宫格。
        显式 DRIVER_VISION_API env（云端回退/手指定端点）时跳过托管。
        返回 bool：True=判读面可用（在线/显式端点），False=离线（调用方须短路整批判读）。"""
        if os.environ.get("DRIVER_VISION_API"):
            log("[vision] 显式 DRIVER_VISION_API 已设（云端回退/手指定），跳过本地服务托管")
            return True
        try:
            import vision_server
        except Exception as e:  # noqa: BLE001 — 模块不可用不影响判读面（失败留痕兜底）
            log(f"[vision] vision_server 模块不可用（跳过托管，失败留痕兜底）: {e}")
            return False

        def _n(t):
            log(t)
            try:
                self.notify(t)
            except Exception:  # noqa: BLE001
                pass
        try:
            st = vision_server.ensure_online(
                wait=int(os.environ.get("DRIVER_VISION_READY_TIMEOUT", "300")),
                notify=_n)
        except Exception as e:  # noqa: BLE001
            st = {"online": False, "launched": False, "detail": f"阶梯异常: {e}"}
        self.ledger.record("s08.vision_server", st)
        if st.get("online"):
            log(f"[vision] 本地判读服务在线（本调用拉起={st.get('launched')}，"
                f"alias={st.get('alias')}）")
        else:
            log(f"[vision] ⚠️ 本地判读服务不可用: {st.get('detail')}——"
                f"降级为判读失败留痕，失败段进确认门④九宫格人审")
        # 2026-10-17 故障：返回值给调用方做短路判据（离线时不逐段盲试）
        return bool(st.get("online"))

    def _check_frames(self, vdir, only=None, evidence_tag="round0"):
        """抽帧 + vision 模型逐帧判竖直（三级：正常/倾斜/翻转）。返回 (flipped, tilted)。
        2026-09-05 验收教训：批量 5 图一次问会漏报（shot06/07/16 全转了却判正常）——
        改为单帧单问 + 直白判断指令。
        2026-09-05 补充（rotation-analysis 实证）：glm-4.6v 对本风格（人物极小+云海）
        的旋转判定不可靠（4 种提示词策略全部漏报 shot07），故本检查定位为「辅助信号 +
        判读留痕」：判读失败重试 2 次后记录进报告并提示人审九宫格，不再静默放行；
        旋转主防线 = s07 横移运镜门 + 确认门④九宫格人审。
        2026-09-11 三级判定（用户拍板）：倾斜（15°+ 斜构图）只标记不自动重烧——
        风格里存在刻意的斜线构图，误判代价高；倾斜段进确认门④点名名单交人审。
        2026-10-14 起（T2 #104）端点缺省本地 llama-server；入口先走 _ensure_vision
        自愈阶梯（探活→拉起→降级），判读面本身零改动。
        2026-10-14 起（T8 #117）判读缺省 Jev 形态：候选词单字 + logprobs 投影出三
        分类分布（单 token，省 decode），置信度经温度缩放校准后进门控三级
        （≥0.90 自动过 / 0.60-0.90 抽检 / <0.60 点名）；校准参数缺失时门控停用
        （红线：未校准置信度不参与门控）。DRIVER_VISION_MODE=json 一条 env 回退
        旧 JSON 形态（行为与 T7 完全一致）。
        2026-10-14 起（T11 #121）判翻即留证：触发重烧的翻转判定，其定罪帧在下一轮
        复检重抽覆盖前复制到 _frames/evidence/<tag>/（evidence_tag：s08 首检
        round0 / 重检 roundN / 确认门④重做 gate4）；仅翻转段留证（其余段帧不被
        覆盖），复制失败降级留痕不阻塞判读主链。evidence 随台账逐轮累积。
        2026-10-14 起（用户拍板）：横构图风险段首帧单判（10% 处），原首/中/尾
        三帧复核废弃——构图级失败从首帧即完整可见（详见 _judge_one 内注释）。"""
        vision_ok = self._ensure_vision()
        form = vision_common.judge_mode()
        cal_t, cal_ok, cal_note = _load_jev_calibration()
        if form == "json":
            log("[vision] DRIVER_VISION_MODE=json：JSON 形态判读（无置信度/门控停用）")
        elif cal_ok:
            log(f"[vision] Jev 形态（门控开，校准 T={cal_t}）")
            if cal_t >= 10.0:
                log(f"[vision] ⚠️ 校准 T={cal_t:.1f}：主判原始分布近独热（判别弱），"
                    f"预期大部分段落入低置信点名——真值增长后重跑 jev_calibrate 收敛")
        else:
            log(f"[vision] Jev 形态（门控停用）: {cal_note[:80]}")
        vids = sorted(glob.glob(os.path.join(vdir, "*.mp4")))
        sids = [os.path.basename(v) for v in vids]
        targets = [s for s in sids if only is None
                   or any(x in s for x in only)]  # 0912 修复：文件名带项目前缀（daily-china-YYYYMMDD_shotXX_…），startswith("shotXX") 永远空匹配→复检空转
        if not vision_ok:
            # 2026-10-17 现场（daily-china-20261017）：判读服务离线后仍逐段盲试——
            # 15:22/15:27/15:31 三波、每段 2 次 × 180s 连接超时，17 段全灭白烧 14 分钟；
            # extract_h3_frames 的 1200s 抽帧同样是纯浪费。离线即整批记判失败并短路返回：
            # 段照旧进确认门④九宫格人审（不放弃当日、不改判读主链语义）。
            judge_fail = [f"{s}:判读服务离线" for s in targets]
            log(f"[vision] ⚠️ 判读服务离线 → 跳过抽帧与判读：{len(targets)} 段整批记 judge_fail，"
                f"交确认门④九宫格人审")
            _prev_ev = (self.ledger.get("s08.vision") or {}).get("evidence") or []
            self.ledger.record("s08.vision", {
                "checked": len(targets), "flipped": [], "tilted": [],
                "judge_failed": judge_fail, "clip_escalated": [],
                "clip_layer": {"available": False}, "form": form, "calibrated": cal_ok,
                "confidence": {}, "spot_check": [], "low_conf": [], "gate_auto": 0,
                "probe_shadow": [], "evidence": list(_prev_ev),
                "skipped": "vision_offline"})
            return [], []
        frames = os.path.join(self.slug, "_frames")
        os.makedirs(frames, exist_ok=True)
        rc, out = run_argv([sys.executable, os.path.join(HERE, "extract_h3_frames.py"),
                            vdir, frames], timeout=1200)
        # 备用视觉模型（2026-09-05 用户拍板）：判读失败时跨帧×跨模型轮询；未配则仅帧轮询
        models = [VISION_MODEL] + ([os.environ["DRIVER_VISION_MODEL_BACKUP"]]
                                   if os.environ.get("DRIVER_VISION_MODEL_BACKUP") else [])
        flipped, tilted, judge_fail, clip_escalated = [], [], [], []
        spot_check, low_conf, conf_map = [], [], {}  # T8 门控台账
        judged_map = {}  # T11：sid → 定罪帧（判翻证据复制的源）
        probe_rows = []  # T9 影子台账：探针 vs VLM 逐段对照（只落台账不进主链）
        # CLIP 平行兜底层（T4 #106）：缺席/失败 = 单路 VLM，行为与现状完全一致（AC2）
        clip_probe, clip_layer_status = None, {"available": False}
        try:
            import clip_probe as _cpmod
            clip_layer_status = _cpmod.probe_status()
            if clip_layer_status["available"]:
                clip_probe, lerr = _cpmod.load_probe()
                log(f"[clip] 兜底层在线（{clip_layer_status.get('clip_model')}，"
                    f"classes={clip_layer_status.get('classes')}"
                    f"，形态={clip_layer_status.get('infer', 'class3')}"
                    + (f"，thr={clip_layer_status.get('thr')}"
                       if clip_layer_status.get("thr") is not None else "") + "）")
                if getattr(clip_probe, "rotation_mode", False):
                    # T10 #119 换岗：四向旋转搜索探针有真朝向能力（实测 recall
                    # 0.93 / 留出误报 3.2%），而 4B 四向题 acc 0.42（2026-10-22
                    # 实测：任何旋转体都答错）→ 探针升主判，VLM 降为旁证
                    log("[clip] 四向旋转搜索形态 → 探针升主判，VLM 转旁证（T10 #119）")
            else:
                log(f"[clip] 兜底层缺席（单路 VLM）: {clip_layer_status.get('reason', '')[:90]}")
        except Exception as e:  # noqa: BLE001 — 层缺席不进判读链
            log(f"[clip] 兜底层加载异常（单路 VLM）: {e}")
        _lk = threading.Lock()
        # 2026-10-17：连接级失败一旦确认，同批剩余段不再各烧 2×180s（Errno 110 是
        # 端口无人监听，重试 100 次也不会好）
        dead = threading.Event()
        dead_signs = ("Connection refused", "Connection timed out", "Errno 110", "Errno 111")

        def _judge_one(name):
            """单段 vision 判读（线程安全；2026-09-08 并行化：串行 22 段
            曾耗时 2-3 小时，是执行慢的第二大头）。"""
            m = re.search(r"shot(\d+)", name)
            sid = f"shot{int(m.group(1)):02d}" if m else name[:7]
            cand = (glob.glob(os.path.join(frames, f"{sid}.jpg"))
                    or glob.glob(os.path.join(frames, f"{sid}.png")))
            if not cand:
                with _lk:
                    judge_fail.append(f"{sid}:无抽帧")
                log(f"[vision] {sid} 无抽帧，跳过（确认门④九宫格人审兜底）")
                return
            # 横构图风险段（s07 标记 portrait_risk）：判首帧（10% 处）。
            # 2026-10-14 用户拍板：构图级失败（90° 族）从首帧即完整可见，首帧单判
            # 足够；原「首/中/尾三帧全判」废弃——中帧可能落在转完回正的稳定段反而
            # 漏报，三帧只多烧判读时长不增检出。首帧抽不出（ffmpeg 失败）回退中帧。
            cands = [cand[0]]
            if self._prompt_portrait_risk(sid):
                vfp = os.path.join(vdir, name)
                dur = ffprobe_dur(vfp)
                fp = os.path.join(frames, f"{sid}_first.jpg")
                # 2026-10-22 生产事故（#187）：这里原来只判「不存在才抽」，重烧后
                # 上一轮的 _first.jpg 仍被当成本轮画面判读——5 段补烧全部被判翻转，
                # 实际用的是 10-20 的旧帧。缓存有效性必须按「晚于源视频」判。
                if frame_is_stale(fp, vfp):
                    sh(f'ffmpeg -y -v error -ss {max(0.0, dur * 0.1):.2f} -i "{vfp}" '
                       f'-frames:v 1 "{fp}"', timeout=60)
                if os.path.exists(fp):
                    cands = [fp]
                log(f"[vision] {sid} 横构图风险段 → 首帧判读")
            verdict, err, judged_fp, vconf = "", "", None, None
            for fp in cands:  # 帧轮询：单帧空响应换帧再试（原「保守放行」已废除）
                # 缩图预算统一走 vision_common.image_b64（原生分辨率 prefill 16s/帧
                # → 长边 896 后 3.2s，pitfalls #55；env DRIVER_VISION_IMG_MAX_SIDE 可关）
                b64 = vision_common.image_b64(fp)
                # T8 #117：请求体形状单一事实源在 vision_common（jev_body/judge_body，
                # 支持 b64 串直传）；两形态只差 helper 选择。max_tokens 必须给
                # reasoning 留预算：glm-4.6v 是思考模型，100 会被 reasoning_tokens
                # 吃光 → content 空串 → JSON 解析炸（0914 实证：10/19 段「判读均
                # 失败」，shot06 旋转漏网进成片——JSON 形态 JUDGE_MAX_TOKENS=200）
                body = (vision_common.jev_body if form == "jev"
                        else vision_common.judge_body)(models[0], b64)
                for mdl in models:  # 模型轮询：备用模型未配置时等同单模型
                    if mdl != models[0]:
                        body = json.loads(body.decode())
                        body["model"] = mdl
                        body = json.dumps(body, ensure_ascii=False).encode()
                    for attempt in range(2):  # 判读失败退避重试
                        if dead.is_set():
                            err = "判读服务离线（同批已确认连接失败，跳过重试）"
                            break
                        try:
                            d = urllib_req(body)
                            if form == "jev":  # Jev 投影：三候选 logits → 校准置信度
                                z3 = vision_common.jev_project(d)
                                verdict, vconf = vision_common.jev_decide(z3, cal_t)
                            else:
                                txt = d["choices"][0]["message"]["content"]
                                j = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
                                verdict = str(j.get("verdict", ""))
                            err = ""
                            judged_fp = fp  # CLIP 平行判同帧（评审 (c2)：帧差非模型差）
                            break
                        except Exception as e:  # noqa: BLE002
                            err = f"{mdl}:{e}"
                            if any(k in err for k in dead_signs):
                                dead.set()
                            else:
                                time.sleep(3 * (attempt + 1))
                    if not err:
                        break
                if not err and ("翻" in verdict or "倾" in verdict or "rotat" in verdict.lower()
                                or "tilt" in verdict.lower()):
                    break
            # 双路合成（T4 #106）：VLM 失败传 judge_fail；CLIP 缺席传 None（单路降级）。
            # T8：Jev 形态附带主判置信度，校准在位才参与门控（reconcile 红线）
            clip_result = None
            if clip_probe is not None:
                # VLM 失败也判：reconcile「judge_fail + CLIP 翻转附加点名」才可达
                # （宁误报不漏报，评审 (c1)）；判 VLM 实际成功帧，缺省首帧
                cv, c2 = clip_probe.predict(judged_fp if not err else cands[0])
                if cv:
                    clip_result = {"verdict": cv, "confidence": c2}
            rec = reconcile_vlm_clip(
                {"verdict": norm_verdict(verdict) if not err else None,
                 "confidence": vconf if (not err and form == "jev") else None},
                clip_result, calibrated=cal_ok,
                clip_primary=bool(getattr(clip_probe, "rotation_mode", False)))
            with _lk:
                # T9 影子台账（#118）：探针 vs VLM vs（后续人审回填）三方对照，
                # 只落台账不进主链——影子不点名、不重烧
                if clip_result:
                    probe_rows.append(
                        {"sid": sid, "probe": clip_result["verdict"],
                         "conf": round(float(clip_result["confidence"]), 4),
                         "vlm": (norm_verdict(verdict) if not err else "judge_fail"),
                         "final": rec["verdict"]})
                if rec["verdict"] == "judge_fail":
                    judge_fail.append(f"{sid}:{err[:60]}")
                    log(f"[vision] {sid} 判读均失败（已记录，确认门④九宫格人审兜底）: {err[:120]}")
                else:
                    if vconf is not None:
                        conf_map[sid] = round(vconf, 4)
                    judged_map[sid] = judged_fp or (cands[0] if cands else None)
                    if rec["verdict"] == "翻转":
                        flipped.append(sid)
                        log(f"[vision] {sid} → 翻转" + _conf_tail(vconf, form))
                    elif rec["verdict"] == "倾斜":
                        tilted.append(sid)
                        log(f"[vision] {sid} → 倾斜（只标记，确认门④人审定夺）"
                            + _conf_tail(vconf, form))
                    else:
                        log(f"[vision] {sid} 正常（{len(cands)} 帧）"
                            + ("；CLIP 同判" if clip_result else "") + _conf_tail(vconf, form))
                    if rec["spot_check"]:
                        spot_check.append(f"{sid}:{vconf:.2f}")
                # escalate 缘由走 reconcile 显式旗标（机器可读，免文案反推）：
                # CLIP 翻转分歧/附加 → clip_escalated；校准后低置信 → low_conf
                # （双触发两边都进，宁误报不漏报）
                if rec["escalate"]:
                    if rec["escalate_clip"]:
                        clip_escalated.append(f"{sid}:{rec['note'][:70]}")
                    if rec["escalate_low"]:
                        low_conf.append(f"{sid}:{vconf:.2f}")
                    log(f"[clip] {sid} ⚠️ {rec['note']}")

        if len(targets) > 1:
            with ThreadPoolExecutor(max_workers=min(6, len(targets))) as ex:
                list(ex.map(_judge_one, targets))
        else:
            for t in targets:
                _judge_one(t)
        flipped.sort()
        tilted.sort()
        gate_auto = (sum(1 for c in conf_map.values() if c >= GATE_HI)
                     if cal_ok and form == "jev" else 0)
        # T11 #121：判翻即留证——下一轮复检重抽会覆盖 _frames 原路径，定罪帧必须
        # 先落副本到 _frames/evidence/<tag>/。仅触发重烧的翻转段留证（倾斜/失败/
        # 低置信段帧不被覆盖，台账已有名单）；复制失败降级留痕不阻塞判读主链。
        # evidence 跨轮累积（读改写本进程增量，round0..N / gate4 各留一份）
        evidence = list((self.ledger.get("s08.vision") or {}).get("evidence") or [])
        for sid in flipped:
            fp = judged_map.get(sid)
            if not fp or not os.path.exists(fp):
                continue
            ev_dir = os.path.join(frames, "evidence", evidence_tag)
            dst = os.path.join(ev_dir, os.path.basename(fp))
            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "frame_win": hostpath.to_win_path(dst),
                                 "sha256": _sha256_file(dst),
                                 "verdict": "翻转", "conf": conf_map.get(sid), "form": form})
            except Exception as e:  # noqa: BLE001 — 宁缺证据不停线
                log(f"[evidence] {sid} 证据帧复制失败（不阻塞）: {e}")
        # T9 影子 env 门控（缺省开；=0 只是不落台账，探针层本身照 T4 语义运转）
        if os.environ.get("DRIVER_PROBE_SHADOW", "1") == "0":
            probe_rows = []
        if probe_rows:
            log(f"[probe-shadow] {len(probe_rows)} 行探针 vs VLM 对照 → 台账 "
                f"s08.vision.probe_shadow（影子不点名不重烧）")
        # T16 #128：判官复判（DSH 桌面视觉模型第二意见）。判官为准：confirmed+
        # judge_found 重烧；local_false 只记账不重烧。fail-open：判官不可用/超时/
        # 解析失败 → 采信本地 flipped，绝不 raise。
        reburn, judge_block = self._judge_second_opinion(
            judged_map, conf_map, flipped, tilted, evidence_tag)
        if judge_block is not None:
            flipped = reburn
            # #132 缺口修补：判官抓漏报段（本地正常×判官翻）同样进重烧名单，但上面
            # 的存证循环只遍历本地 flipped、且发生在归并之前——这些帧不补存副本，
            # 下一轮重烧重抽就覆盖原路径，「三类素材都存证」的强负类素材永久丢失
            # （10-15 期五段漏报全部不可再生，实测 0 条证据行）。
            evidence = self._save_judge_found_evidence(
                judge_block, judged_map, conf_map, evidence_tag, frames, evidence)
        # 证据分类归档（#182 AC3/AC4：生产写三类桶目录 + manifest.json）。
        # 只在判官真的给出复判时分类——没有判官就没有「本地翻×判官翻」的类别
        # 依据，fail-open 轮照旧只留扁平证据帧，不伪造正类/强负类标签。
        # #192：_archive_evidence_buckets 自身也把同一道门（判官缺席连空
        # manifest 都不写），这里先挡一次只是省掉无谓的清单读取。
        if isinstance(judge_block, dict) and judge_block.get("ok"):
            try:
                self._archive_evidence_buckets(evidence, frames, judge_block)
            except Exception as e:  # noqa: BLE001 — 存证失败不阻塞主链
                log(f"[evidence] 分类归档/manifest 写失败（不阻塞）: {e}")
        self.ledger.record("s08.vision", {"checked": len(targets), "flipped": flipped,
                                          "tilted": tilted, "judge_failed": judge_fail,
                                          "clip_escalated": clip_escalated,
                                          "clip_layer": clip_layer_status,
                                          "form": form, "calibrated": cal_ok,
                                          "confidence": conf_map,
                                          "spot_check": spot_check,
                                          "low_conf": low_conf,
                                          "gate_auto": gate_auto,
                                          "probe_shadow": probe_rows,
                                          "evidence": evidence,
                                          "judge": judge_block})
        # ADR 0008 决策 7（#189）：判读轮末显式落盘——进程中途崩溃最多丢一个片段
        # 窗口，而不是整轮 5–10 分钟的判读/重烧计算量（此前只靠 run() finally 兜底）。
        self.ledger.flush()
        return flipped, tilted

    def _archive_evidence_buckets(self, evidence, frames, judge_block):
        """#182/#192：判官轮把本轮证据按类别桶归档，并写生产侧 manifest.json。

        桶目录 <slug>/_frames/evidence/<category>/<round>_<sid>.jpg，与离线件
        references/t15-evidence-*/ 同构；manifest 落 <slug>/_frames/evidence/
        manifest.json（rotation_dataset.load_evidence_buckets 第二路扫描口）。

        四类词表（rotation_dataset.EVIDENCE_CATEGORIES：1-confirmed /
        2-judge-found / 3-unfixed / 4-human-flag）的写者一一固定，本函数只写前两类：
        - 1-confirmed：本地判翻 × 判官确认翻，本函数在判官轮写；
        - 2-judge-found：本地判正常 × 判官判翻，本函数在判官轮写；
        - 3-unfixed：「收尾仍翻」才写（写的是「修不好」，不是「还没修好」）。
          写者是 s08 收尾 `_archive_unfixed_evidence`（#194）：s08.still_flipped
          非空时由它写、round 取该段最后一次复检轮次。本函数永不写它——白名单
          里根本没有这个类别，别在这里补（补了就是越权伪造最难正类）；
        - 4-human-flag：只由人审指认回调 _flag_commit → _manifest_human_flag
          写（round=human_flag）。本函数永不写它——把本地判读塞进人审类即伪造。
        类别取自白名单 kind_cat，kind 不在其中（local_false / judge_normal /
        无 kind / 自己编的名称）一律不归档，不往闭合词表里塞无定义类别。

        判官缺席时（judge_block 非 dict 或 ok=False）直接返回 0：不建类别子目录、
        不写 manifest、不新增任何类别——没有判官就没有「本地翻 × 判官翻」的类别
        依据，落一份空清单等于对「本轮无类别证据」作伪证。这类 fail-open 轮的
        可见性由行内 evidence_frames 承担（ADR 0008 决策 2）。调用点
        （_check_frames）已按同一口径挡过一次，这里再挡一次：测试 / 探针 /
        未来调用点直接调本函数，同样拿不到写入。

        已有条目按 (category, sid, round) 去重，重跑同轮不重复追加。
        """
        # AC1（#192）：门在函数自身也关上——判官缺席连空 manifest 都不落盘。
        if not (isinstance(judge_block, dict) and judge_block.get("ok")):
            return 0
        ev_dir = os.path.join(frames, "evidence")
        mf_path, manifest, entries = _load_evidence_manifest(
            ev_dir, [self.date, os.path.basename(os.path.normpath(self.slug))])
        # AC2（#192）：白名单闭集——只认判官轮两类；词表其余两类各有唯一写者
        # （3-unfixed 归 #194 的 s08 收尾，4-human-flag 归人审指认回调）。
        kind_cat = {"confirmed": "1-confirmed", "judge_found": "2-judge-found"}
        rows = {r.get("sid"): r for r in (judge_block.get("rows") or [])
                if isinstance(r, dict)}
        have = {(e.get("category"), e.get("sid"), e.get("round")) for e in entries}
        added = 0
        for ev in evidence or []:
            sid, rnd = ev.get("sid"), ev.get("round")
            jr = rows.get(sid) or {}
            kind = jr.get("kind")
            cat = kind_cat.get(kind)
            if not cat or not sid:
                continue
            if (cat, sid, rnd) in have:
                continue
            src_fp = ev.get("frame")
            if not src_fp or not os.path.exists(src_fp):
                continue
            cat_dir = os.path.join(ev_dir, cat)
            try:
                os.makedirs(cat_dir, exist_ok=True)
                dst = os.path.join(cat_dir, "%s_%s%s" % (
                    rnd or "round0", sid, os.path.splitext(src_fp)[1] or ".jpg"))
                shutil.copy2(src_fp, dst)
                entries.append({
                    "category": cat, "sid": sid, "round": rnd,
                    "frame": dst, "frame_win": hostpath.to_win_path(dst),
                    "source_frame": src_fp, "sha256": _sha256_file(dst),
                    "judge": jr.get("judge"), "conf": jr.get("conf"),
                    "why": jr.get("why") or ev.get("judge_why") or "",
                    "local_verdict": jr.get("local") or ev.get("verdict"),
                    "kind": kind, "synthetic": False, "note": "",
                })
                have.add((cat, sid, rnd))
                added += 1
            except Exception as e:  # noqa: BLE001 — 单帧失败不拖垮整桶
                log(f"[evidence] {sid} 分类归档失败（不阻塞）: {e}")
        manifest["evidence"] = entries
        _write_evidence_manifest(ev_dir, mf_path, manifest)
        log(f"[evidence] 分类桶归档 +{added} 条 → {ev_dir}"
            f"（manifest 共 {len(entries)} 条）")
        return added

    def _archive_unfixed_evidence(self, still_flipped, rounds):
        """#194：3-unfixed 收尾补写——「重烧后仍翻」是最难正类，此前生产侧无写者。

        _check_frames 每轮只给可重烧段留扁平证据帧（round0..N），
        _archive_evidence_buckets 的白名单永不写 3-unfixed（那是本函数的职责）。
        s08 重烧梯队跑完、still_flipped 非空时，收尾把每段「最后一次复检轮」的
        定罪帧拷进 _frames/evidence/3-unfixed/<round>_<sid>.jpg，并按同一 manifest
        写口追加条目（与 1/2 两类同构：category/sid/round/frame/frame_win/
        source_frame/sha256/…）。判官在不在场都不影响本函数——3-unfixed 的判据
        是「收尾仍翻」本身（ADR 0008 决策 6），不要求判官共识。

        rounds 是 s08 重烧梯队记录（[{round, shots, mode}]），同段后轮覆盖前轮，
        与 s08 末尾 build_records 的 {sid: round} 同一口径 → round 取该段最后一次
        复检的轮次。但梯队记录的是「本轮计划重烧的段」，重烧被跳过（rewrite 不可用、
        无可重烧段 break）时声明轮次从没真正复检过——此时退回该段真正最后一次在盘
        的复检帧（round 标签按数字取最大）。条目来源只认在盘证据帧：一轮都没有 /
        帧缺盘一律跳过留日志，宁缺不伪造（凑一条假 3-unfixed 比没有更坏）。
        still_flipped 为空 → 不碰 manifest（不写空清单）；去重键沿用
        (category, sid, round)，重跑同期不重复追加。

        返回新增条目数（不是清单总条数）。
        """
        if not still_flipped:
            return 0
        # 每段最后一次复检轮次（重烧梯队按轮追加，同段后轮覆盖前轮）
        last_round = {sid: rr["round"] for rr in rounds for sid in rr["shots"]}
        frames = os.path.join(self.slug, "_frames")
        ev_dir = os.path.join(frames, "evidence")
        mf_path, manifest, entries = _load_evidence_manifest(
            ev_dir, [self.date, os.path.basename(os.path.normpath(self.slug))])
        evidence = (self.ledger.get("s08.vision") or {}).get("evidence") or []
        # 证据行按 (sid, 轮次) 建索引；只认 "round<数字>" 标签（gate4 / human_flag
        # 等旁路留证不属于 s08 重烧梯队，混进来会把轮次口径搞乱）。
        by_sid = {}
        for ev in evidence:
            m = re.match(r"round(\d+)$", str(ev.get("round") or ""))
            if ev.get("sid") and m:
                by_sid.setdefault(ev["sid"], []).append((int(m.group(1)), ev["round"], ev))
        for rows in by_sid.values():
            rows.sort(key=lambda t: t[0])
        cat = "3-unfixed"
        have = {(e.get("category"), e.get("sid"), e.get("round")) for e in entries}
        cat_dir = os.path.join(ev_dir, cat)
        added = 0
        for sid in sorted(still_flipped):
            # 首选「梯队声明的那一轮」在盘证据帧；该轮实际没复检（重烧被跳过：
            # rewrite 不可用等导致 break）时退回该段真正最后一次复检的那一轮——
            # 声明轮次可能从没落地，账上留一个不存在的 roundN 比用 roundN-1 更假。
            rows = by_sid.get(sid) or []
            rnd_no = last_round.get(sid)
            ev, rnd = None, None
            if rnd_no is not None:
                for num, tag, row in reversed(rows):
                    if num == rnd_no:
                        ev, rnd = row, tag
                        break
            if ev is None and rows:
                _, rnd, ev = rows[-1]
            if ev is None:
                log(f"[evidence] {sid} 无任何轮次在盘定罪帧，3-unfixed 跳过（不伪造条目）")
                continue
            src_fp = ev.get("frame")
            if not src_fp or not os.path.exists(src_fp):
                log(f"[evidence] {sid} {rnd} 无在盘定罪帧，3-unfixed 跳过（不伪造条目）")
                continue
            if (cat, sid, rnd) in have:
                continue  # 重跑同轮不重复追加
            try:
                os.makedirs(cat_dir, exist_ok=True)
                dst = os.path.join(cat_dir, "%s_%s%s" % (
                    rnd, sid, os.path.splitext(src_fp)[1] or ".jpg"))
                shutil.copy2(src_fp, dst)
                entries.append({
                    "category": cat, "sid": sid, "round": rnd,
                    "frame": dst, "frame_win": hostpath.to_win_path(dst),
                    "source_frame": src_fp, "sha256": _sha256_file(dst),
                    "judge": ev.get("judge") or "", "conf": ev.get("conf"),
                    "why": ev.get("judge_why") or "",
                    "local_verdict": ev.get("verdict") or "翻转",
                    "kind": "still_flipped", "synthetic": False, "note": "",
                })
                have.add((cat, sid, rnd))
                added += 1
            except Exception as e:  # noqa: BLE001 — 单帧失败不拖垮整桶
                log(f"[evidence] {sid} 3-unfixed 归档失败（不阻塞）: {e}")
        if added:
            manifest["evidence"] = entries
            try:
                _write_evidence_manifest(ev_dir, mf_path, manifest)
            except Exception as e:  # noqa: BLE001 — 落盘失败不阻塞 s08 收尾
                log(f"[evidence] 3-unfixed manifest 写失败（不阻塞）: {e}")
                return 0
            log(f"[evidence] 3-unfixed 归档 +{added} 条 → {cat_dir}"
                f"（manifest 共 {len(entries)} 条）")
        return added

    def _save_judge_found_evidence(self, judge_block, judged_map, conf_map,
                                   evidence_tag, frames, evidence):
        """#132 缺口修补：判官抓漏报段（judge_found）的定罪帧存证。

        T11 的存证循环只遍历「本地判读判翻」的段，T16 接判官时把 judge_found 加进
        重烧名单，却没有同步扩存证范围——这些段本地判正常、判官判翻，帧从未落副本，
        下一轮重烧重抽即覆盖原路径。10-15 期五段真实漏报全部早于此，实测轮上 0 条
        证据行（judge_found 进名单但 evidence 为空）。

        与本地翻转证据同目录同 tag（_frames/evidence/<tag>/），行上多带
        judge/kind 字段以区分「判官抓漏报」与「本地也判翻」。失败一律降级留痕，
        绝不阻塞判读主链（与 T11 同语义）。返回追加后的 evidence 列表。
        """
        if not isinstance(judge_block, dict) or not judge_block.get("ok"):
            return evidence  # fail-open 轮无判官结论，无从补存
        found = [sid for sid in (judge_block.get("judge_found") or [])]
        if not found:
            return evidence
        # 判官逐段原判（翻转90/翻转180/倾斜），落证据行便于与本地三分类对照
        why_by_sid = {r.get("sid"): r for r in (judge_block.get("rows") or [])
                      if isinstance(r, dict)}
        have = {(e.get("sid"), e.get("round")) for e in evidence}
        for sid in sorted(found):
            if (sid, evidence_tag) in have:
                continue  # 本地也判翻的段已由上面循环存过（confirmed），不重复
            fp = judged_map.get(sid)
            if not fp or not os.path.exists(fp):
                log(f"[evidence] {sid} 判官抓漏报但无罪定帧可存（跳过，不阻塞）")
                continue
            ev_dir = os.path.join(frames, "evidence", evidence_tag)
            dst = os.path.join(ev_dir, os.path.basename(fp))
            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                jr = why_by_sid.get(sid) or {}
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "frame_win": hostpath.to_win_path(dst),
                                 "sha256": _sha256_file(dst),
                                 "verdict": "翻转", "conf": conf_map.get(sid),
                                 "judge": jr.get("judge") or "翻转",
                                 "judge_why": jr.get("why") or "",
                                 "kind": "judge_found"})
                log(f"[evidence] {sid} 判官抓漏报定罪帧已存证 → {dst}")
            except Exception as e:  # noqa: BLE001 — 宁缺证据不停线
                log(f"[evidence] {sid} 判官抓漏报证据帧复制失败（不阻塞）: {e}")
        return evidence

    def _judge_second_opinion(self, judged_map, conf_map, flipped, tilted,
                              evidence_tag):
        """T16 #128：判官复判（DSH 桌面视觉模型第二意见，spec #132）。

        _check_frames 本地判读完成后调用：把本地判定（含置信度与定罪帧路径）交给
        dsh_judge 复核，四象限归并（confirmed/judge_found/local_false/judge_normal），
        重烧名单以判官为准（confirmed+judge_found；local_false 只记账不重烧）。
        每轮经 record_event 落 judge_rounds / judge_disagreements——顶层平键 list
        （绝不并入整块覆盖的 s08.vision：那一块每轮重写，历史轮次会丢；也不能放
        s08.judge.* / ledger.judge.* 嵌套键——_merged_root 对顶层 dict 块是
        「增量整体赢」的浅合并，嵌套 list 的旧盘值不会拼接，跨重启丢历史轮，
        test §6 restart 实证）。顶层 list 事件跨重启 flush 拼接去重，merged 可读回。
        fail-open：判官未启用 → 静默跳过（特性关≠故障，不发告警）；不可用/超时/
        解析失败 → log+notify 后采信本地，绝不 raise。

        返回 (reburn, judge_block)：判官生效 → (重烧名单, 轮块字典)；跳过/fail-open
        → (None, None)，调用方保持本地 flipped 原样。judge_block 非 None 当且仅当
        判官真的给出了复判（调用方据此改写 flipped）。
        """
        # rows 只含有定罪帧的段（判读失败段没有帧，判官无从复核）
        local = {sid: ("翻转" if sid in flipped else
                       "倾斜" if sid in tilted else "正常")
                 for sid in judged_map}
        rows = [{"sid": sid, "verdict": local[sid], "conf": conf_map.get(sid),
                 "frame": judged_map.get(sid) or ""} for sid in sorted(local)]
        if not rows:
            return None, None  # 无可复核段（全判读失败等），判官静默跳过
        try:
            import dsh_judge as dj
        except Exception as e:  # noqa: BLE001 — fail-open
            log(f"[judge] dsh_judge 导入失败（fail-open 采信本地）: {e}")
            return None, None
        if not dj.enabled():
            return None, None  # DRIVER_JUDGE=0：特性显式关闭，不算故障
        res = dj.judge(rows)
        block = {"round": evidence_tag, "judged": [r["sid"] for r in rows],
                 "ok": bool(res.get("ok")), "error": res.get("error") or "",
                 "in_tok": res.get("in_tok") or 0, "out_tok": res.get("out_tok") or 0,
                 "reasoning_tok": res.get("reasoning_tok") or 0,
                 "wall_s": round(res.get("wall_s") or 0.0, 1),
                 "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if not res.get("ok"):
            log(f"[judge] {evidence_tag} 判官不可用（fail-open 采信本地）: {block['error']}")
            self.notify(f"⚠️ 判官复判不可用（{evidence_tag}），本轮采信本地判读: "
                        f"{block['error']}")
            self.ledger.record_event("judge_rounds", block)
            return None, None
        merged = dj.merge(local, res["items"], round_tag=evidence_tag)
        reburn = dj.reburn_list(merged)
        block.update({"confirmed": merged["confirmed"],
                      "judge_found": merged["judge_found"],
                      "local_false": merged["local_false"],
                      "judge_normal": merged["judge_normal"],
                      "reburn": reburn, "summary": dj.summarize(merged),
                      "rows": merged["rows"]})
        self.ledger.record_event("judge_rounds", block)
        disagree = [r for r in merged["rows"] if r.get("kind") != "judge_normal"]
        if disagree:
            self.ledger.record_event("judge_disagreements",
                                     {"round": evidence_tag, "rows": disagree})
        log(f"[judge] {evidence_tag}: {block['summary']} → 重烧名单 {reburn}")
        if merged["judge_found"] or merged["local_false"]:
            self.notify(f"⚖️ 判官复判（{evidence_tag}）与本地有分歧：{block['summary']}；"
                        f"重烧 {reburn}（本地误报只记账不重烧）")
        return reburn, block

    def _judge_fields_for_dataset(self):
        """T16 #128：从台账 judge_rounds（merged，跨重启可读回）聚合每段最新
        判官结论 → rotation_dataset.build_records(judge=...) 的字段源。
        字段名照 t15_replay.py L283-288 规范；vision_verdict 不动。
        取段在判官轮次中的最后一条结论（重烧后以最近轮为准）；
        judge_source 固定 'dsh_judge'（模型名进 dsh_judge.JUDGE_MODEL env 口径，不硬编码）。"""
        out = {}
        rounds = self.ledger.get("judge_rounds", view="merged") or []
        if not isinstance(rounds, list):
            return out
        for rnd in rounds:
            if not isinstance(rnd, dict) or not rnd.get("ok"):
                continue  # fail-open 轮无判官结论，不进数据集
            tag = rnd.get("round") or ""
            for r in rnd.get("rows") or []:
                if not isinstance(r, dict) or not r.get("sid"):
                    continue
                d = out.setdefault(r["sid"], {
                    "judge_verdict": None, "judge_conf": None, "judge_why": "",
                    "judge_flip_rounds": [], "judge_rounds": [],
                    "judge_source": "dsh_judge"})
                # 结论字段后轮覆盖（重烧复检后以最近轮为准）；轮次跨轮累积去重
                d["judge_verdict"] = r.get("judge")
                d["judge_conf"] = r.get("conf")
                d["judge_why"] = r.get("why", "")
                if tag not in d["judge_rounds"]:
                    d["judge_rounds"].append(tag)
                if r.get("kind") in ("confirmed", "judge_found") \
                        and tag not in d["judge_flip_rounds"]:
                    d["judge_flip_rounds"].append(tag)
        return out

    def _judge_gate_note(self):
        """T16 #128：判官复判摘要 → 门④ caption 文案（判官结论+重烧名单+分歧）。
        判官未跑（特性关/无判读）→ 空串；fail-open 轮如实标注「不可用，采信本地」。
        merged 视图跨重启可读回（票7 同一读口约定）。"""
        rounds = self.ledger.get("judge_rounds", view="merged") or []
        if not isinstance(rounds, list) or not rounds:
            return ""
        lines = []
        disagree_sids = []
        for rnd in rounds:
            if not isinstance(rnd, dict):
                continue
            tag = str(rnd.get("round") or "?")
            if not rnd.get("ok"):
                err = str(rnd.get("error") or "不可用")[:50]
                lines.append(f"· 第{tag}轮判官不可用（{err}），已采信本地判读")
                continue
            lines.append(f"· 第{tag}轮判官复判：{rnd.get('summary') or ''}"
                         f"；重烧名单 {rnd.get('reburn') or '无'}")
            for r in rnd.get("rows") or []:
                if isinstance(r, dict) and r.get("kind") in ("confirmed", "judge_found", "local_false") \
                        and r.get("sid") not in disagree_sids:
                    disagree_sids.append(r["sid"])  # noqa: 首见即收，跨轮去重
        if not lines:
            return ""
        note = "\n🔎 判官复判（DSH 视觉模型，重烧以判官为准）：\n" + "\n".join(lines)
        if disagree_sids:
            note += f"\n⚠️ 判官与本地分歧段：{disagree_sids}（明细见台账 judge_disagreements）"
        return note

    def _contact_sheet(self, max_w=2160):
        """把全部段的中帧拼成带段号标签的九宫格对照表（确认门④人审旋转用）。
        返回 png 路径；无成片/失败返回 None（不阻塞主流程）。"""
        try:
            import cv2
        except ImportError:
            log("[九宫格] 无 cv2，跳过（确认门④仅看视频）")
            return None
        vdir = os.path.join(self.d["07_成片"], "1080P")
        vids = sorted(glob.glob(os.path.join(vdir, "*.mp4")))
        if not vids:
            return None
        try:
            tw, th, cols = 270, 480, 6
            rows = (len(vids) + cols - 1) // cols
            from PIL import Image, ImageDraw
            grid = Image.new("RGB", (cols * tw, rows * (th + 26)), "black")
            d = ImageDraw.Draw(grid)
            for i, v in enumerate(vids):
                cap = cv2.VideoCapture(v)
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * 0.5))
                ok, f = cap.read()
                cap.release()
                if not ok:
                    continue
                im = Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)).resize((tw, th))
                grid.paste(im, ((i % cols) * tw, (i // cols) * (th + 26) + 26))
                sid = re.search(r"shot\d+", os.path.basename(v))
                d.text(((i % cols) * tw + 8, (i // cols) * (th + 26) + 6),
                       sid.group(0) if sid else os.path.basename(v), fill="yellow")
            out = os.path.join(self.slug, "_frames", "镜头九宫格.png")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if grid.width > max_w:
                grid = grid.resize((max_w, int(grid.height * max_w / grid.width)))
            grid.save(out)
            return out
        except Exception as e:  # noqa: BLE001 — 九宫格失败不阻塞，仅日志
            log(f"[九宫格] 生成失败（不阻塞）: {e}")
            return None

    def _clamp_srt(self, srt_path, video_path):
        """尾部钳制：cue 不得越过成片末尾（0904 教训：末条 cue 越界 ~2.2s 被片尾裁切吃掉）。"""
        dur = ffprobe_dur(video_path)
        guard = 0.05

        def ts(s):
            s = s.strip().replace(",", ".")
            h, m, rest = s.split(":")
            sec, ms = (rest.split(".") + ["0"])[:2]
            return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms.ljust(3, "0")[:3]) / 1000

        def fmt(t):
            ms = int(round(t * 1000))
            h, r = divmod(ms, 3600000)
            m, r = divmod(r, 60000)
            sec, ms2 = divmod(r, 1000)
            return f"{h:02d}:{m:02d}:{sec:02d},{ms2:03d}"

        blocks = open(srt_path, encoding="utf-8").read().strip().split("\n\n")
        out, dropped, clamped = [], 0, 0
        for b in blocks:
            lines = b.split("\n")
            if len(lines) < 2 or " --> " not in lines[1]:
                out.append(b)
                continue
            a, z = lines[1].split(" --> ")
            if ts(a) >= dur - guard:
                dropped += 1
                continue
            if ts(z) > dur - guard:
                lines[1] = a + " --> " + fmt(dur - guard)
                clamped += 1
            out.append("\n".join(lines))
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(out) + "\n")
        self.ledger.record("s10.subtitle_clamped", clamped)
        self.ledger.record("s10.subtitle_dropped", dropped)
        if dropped or clamped:
            log(f"[s10] 字幕尾部钳制: 越界丢弃 {dropped} 条, 收短 {clamped} 条（成片 {dur:.1f}s）")

    def report_summary(self):
        """给确认门/发布通知附一行质量摘要。数据经 ledger.summary()（票7 Q8 定案：
        台账 module 只产数据，车道措辞等渲染政策留在 driver）。"""
        rep = self.ledger.summary()
        h3p = rep.get("s07", {})
        h3 = rep.get("s08", {})
        parts = []
        dirstat = rep.get("s04", {})
        # 压缩车道（#59）：s04 统计块换新语义（lane/ok/total），旧 v3 设计统计
        # （lateral_design_ratio/shot_types）随中间设计稿失效属预期，不再展示
        if dirstat.get("lane") == "compressed":
            parts.append(f"压缩车道 {dirstat.get('ok', 0)}/{dirstat.get('total', 0)}"
                         + (f"，运镜消毒 {dirstat.get('camera_sanitized')} 段"
                            if dirstat.get("camera_sanitized") else ""))
        elif dirstat.get("lateral_design_ratio", 0) > 1 / 3:
            parts.append(f"导演横移类运镜占 {int(dirstat['lateral_design_ratio'] * 100)}%（超 1/3，旋转高危+单调）")
        if h3p:
            parts.append(f"H3 prompt {h3p.get('ok')}/{h3p.get('total')}"
                         + (f"，运镜消毒 {h3p.get('camera_sanitized')} 段" if h3p.get("camera_sanitized") else ""))
        if h3:
            if h3.get("flip_regen_rounds"):
                # 票7 修复（bug②）：轮次 dict 的键是 mode（strip/rewrite=消毒路线，
                # resample=原样重采样，见 s08_h3 strat）——旧代码读 r["sanitized"]
                #（0911 改键后不存在），翻转重生成期在此 KeyError。
                parts.append("翻转重生成 " + "；".join(
                    f"第{r['round']}轮{len(r['shots'])}段"
                    + ("(消毒)" if r.get("mode") in ("strip", "rewrite") else "")
                    for r in h3["flip_regen_rounds"]))
            vf = h3.get("vision", {}).get("judge_failed")
            if vf:
                parts.append(f"vision 判读失败 {len(vf)} 段（人审九宫格兜底）")
        # T16 #128：判官复判摘要进 report_summary（增量口径，同进程 record_event
        # 即可见；跨重启走 s_gate_review 的 merged 读口）。顶层平键
        # judge_rounds（不与整块覆盖的 s08.vision 混放；嵌套 s08.*/ledger.* 键
        # 会被 _merged_root 的 dict⊕dict「增量赢」吞掉旧 list，跨重启丢轮）。
        # 不放 if h3: 内——s08 未跑/单独复判场景也要能出判官摘要（§10 实测）。
        jr = rep.get("judge_rounds") or []
        if jr:
            ok_rounds = [r for r in jr if isinstance(r, dict) and r.get("ok")]
            fail_rounds = [r for r in jr if isinstance(r, dict) and not r.get("ok")]
            if ok_rounds:
                parts.append("判官复判 " + "；".join(
                    f"第{r.get('round')}轮({r.get('summary') or ''})"
                    for r in ok_rounds))
            if fail_rounds:
                parts.append(f"判官不可用 {len(fail_rounds)} 轮（采信本地）")
        subs = rep.get("s10", {})
        if subs.get("subtitle_clamped") or subs.get("subtitle_dropped"):
            parts.append(f"字幕钳制 收短{subs.get('subtitle_clamped', 0)}/丢弃{subs.get('subtitle_dropped', 0)}")
        return "；".join(parts)

    # ── 装配 + 字幕 + 烧录 ─────────────────────────────────────────────
    def s09_compose(self):
        """阶段8b 读报三段装配 + 翻页过渡 + BGM（compose_final）。
        过渡只在新闻条目之间（2026-09-05 用户定稿）：先建标题映射取边界段列表。"""
        out = self.d["07_成片"]
        newspaper = self._newspaper()
        welcome = os.path.join(self.d["02_TTS语音"], "_welcome", "shot01.wav")
        map_fp = self._build_title_map()  # 新闻边界 + 字幕映射（s09 复用）
        m = json.load(open(map_fp, encoding="utf-8"))
        mapping = m.get("news") if isinstance(m, dict) else m  # 兼容旧列表格式
        bounds = [mapping[0]["shot"]] + [m["shot"] for i, m in enumerate(mapping[1:], 1)
                                         if m["title"] != mapping[i - 1]["title"]]
        log(f"新闻边界: {bounds}")
        bgm = ["--bgm", self.args.bgm, "--bgm-volume", "0.15"] if self.args.bgm else []
        rc, o = run_argv([sys.executable, os.path.join(HERE, "compose_final.py"),
                          os.path.join(self.d["07_成片"], "1080P"), self.d["02_TTS语音"], out,
                          "--intro", INTRO_MP4, "--newspaper", newspaper,
                          "--welcome-audio", welcome,
                          "--news-boundaries", ",".join(bounds)] + bgm,
                         timeout=7200)
        if rc != 0 or not any(os.path.exists(os.path.join(out, f)) for f in ("成片_无BGM.mp4", "成片_最终.mp4")):
            raise RuntimeError(f"compose_final 失败: {o[-300:]}")

    def _wait_whisper(self):
        """字幕车道就绪等待：线程没起（resume 场景）就同步跑一遍。"""
        if not getattr(self, "_ws_started", False):
            self._whisper_thread()
            return
        t0 = time.time()
        while not getattr(self, "_ws_done", True):
            if time.time() - t0 > 7200:
                raise RuntimeError("whisper 字幕车道超时（2h）")
            time.sleep(10)

    def _newspaper(self):
        pats = (glob.glob(f"{DATE_ROOT}/{self.date}/{self.date}_每日新中国_第*期.png")
                or glob.glob(f"{DATE_ROOT}/{self.date}/*第*期.png"))
        if not pats:
            raise RuntimeError(f"当日报纸图未找到: {DATE_ROOT}/{self.date}/")
        return pats[0]

    def _build_title_map(self):
        """新闻标题映射.json（字幕顶部标题 + gen_subs 归属）：shot 音频时间轴 × 概述标题匹配。"""
        os.makedirs(self.d["08_字幕"], exist_ok=True)
        news = []
        md = f"{DATE_ROOT}/{self.date}/3新闻_概述.md"
        if os.path.exists(md):
            for m in re.finditer(r"###\s+\[.+?\]\s+(.+?)\n(.+?)(?=\n###|\n##|\Z)",
                                 open(md, encoding="utf-8").read(), re.S):
                title = m.group(1).strip()
                body = m.group(2).strip()
                if "当日无真实报道" in title or "栏目留空" in title:
                    continue
                news.append({"title": title, "body": body})
        raw = json.load(open(os.path.join(self.d["01_分段"], "分镜清单_raw.json"), encoding="utf-8"))
        # 时间轴唯一事实源 = compose 落盘的 _merged/ 实测元素（2026-09-05 根治字幕漂移）：
        # 旧公式（INTRO_DUR 常量 + Σwav + 0.3）对片头换素材(+2.6s)、新闻间垫片(-1s×N)、backfill 虚加
        # 三路误差全盲，实测漂移开头 +2.6s / 结尾 -5.7s。_merged 缺失时才回退旧估算。
        merged_dir = os.path.join(self.d["07_成片"], "_merged")
        intro_fp = os.path.join(merged_dir, "_intro.mp4")
        news_fp = os.path.join(merged_dir, "_newspaper.mp4")
        if os.path.exists(intro_fp) and os.path.exists(news_fp):
            offset = ffprobe_dur(intro_fp) + ffprobe_dur(news_fp)
        else:
            offset = ffprobe_dur(INTRO_MP4) + ffprobe_dur(os.path.join(
                self.d["02_TTS语音"], "_welcome", "shot01.wav"))
        # 概述 body 前 40 字与 shot text 匹配（分段即每条新闻首段）
        cur_title = news[0]["title"] if news else ""
        cur_body = news[0]["body"] if news else ""
        ni = 0
        mapping = []
        shots_tl = []  # 段本体实测起止（不含垫片）——gen_subs 底部字幕用
        t = offset
        for s in raw:
            sid = s["id"]
            seg_fp = os.path.join(merged_dir, f"{sid}.mp4")
            gap_fp = os.path.join(merged_dir, f"_gap_{sid}.mp4")
            if os.path.exists(seg_fp):
                dur = ffprobe_dur(seg_fp)  # 段长以合成产物实测为准（-shortest 裁切+帧取整）
            else:
                dur = ffprobe_dur(os.path.join(self.d["02_TTS语音"], f"{sid}.wav"))
            gap = ffprobe_dur(gap_fp) if os.path.exists(gap_fp) else 0.0
            while ni < len(news) - 1 and s["text"][:40] not in cur_body and \
                    any(s["text"][:40] in x["body"] for x in news[ni + 1:ni + 3]):
                ni += 1
                cur_title, cur_body = news[ni]["title"], news[ni]["body"]
            shots_tl.append({"shot": sid, "start": round(t, 2), "end": round(t + dur, 2)})
            mapping.append({"shot": sid, "title": cur_title, "start": round(t, 2),
                            "end": round(t + dur + gap, 2)})  # 标题窗含垫片：黑场过渡里自然消失
            t += dur + gap
        fp = os.path.join(self.d["08_字幕"], "新闻标题映射.json")
        json.dump({"news": mapping, "shots": shots_tl},
                  open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        self._title_map_offset = offset
        return fp

    def s10_subs_burn(self):
        """阶段9 字幕（whisper 词级时间轴）+ 顶部标题/底部口播烧录 → 最终成片。"""
        self._wait_whisper()
        map_fp = self._build_title_map()
        srt = os.path.join(self.d["08_字幕"], "字幕_口播.srt")
        src = (os.path.join(self.d["07_成片"], "成片_最终.mp4") if self.args.bgm
               else os.path.join(self.d["07_成片"], "成片_无BGM.mp4"))
        offset = getattr(self, "_title_map_offset", INTRO_DUR + 8.0)
        rc, out = run_argv([sys.executable, os.path.join(HERE, "gen_subs_from_timeline.py"),
                            os.path.join(self.d["01_分段"], "口播台词"),
                            self.d["02_TTS语音"], os.path.join(self.slug, "_wsout"),
                            srt, str(offset), map_fp], timeout=3600)
        if rc != 0 or not os.path.exists(srt):
            raise RuntimeError(f"字幕生成失败: {out[-300:]}")
        self._clamp_srt(srt, src)
        final = os.path.join(self.d["07_成片"], "最终成片.mp4")
        self._burn(src, srt, map_fp, final)
        # 压缩预览版（飞书文件上限 30MB）：720×1280 + 码率上限，两档兜底；发布仍用全质量最终成片
        preview = os.path.join(self.d["07_成片"], "最终成片_预览版.mp4")
        for crf, maxrate in (("30", "1100k"), ("33", "800k")):
            rc, out = run_argv([_resolve_ffmpeg(), "-y", "-i", final, "-vf", "scale=720:1280",
                                "-c:v", "libx264", "-preset", "fast",
                                "-crf", crf, "-maxrate", maxrate, "-bufsize", "2M",
                                "-c:a", "aac", "-b:a", "96k", preview], timeout=3600)
            if rc == 0 and os.path.getsize(preview) < 28 * 1024 * 1024:
                break
        if not os.path.exists(preview):
            shutil.copy2(final, preview)

    def _burn(self, src, srt, map_fp, out_fp):
        """烧顶部新闻标题（drawtext 时间窗）+ 底部口播字幕（subtitles）。
        参数移植自 2026-09-01 compose_1080p.py 验证过的配方。
        #151：宿主翻 Windows——fc-match/fontconfig 不可用（#150 AC3），字幕字体改
        「字体文件存在性」判定（fail-fast 不静默回退）；ffmpeg 选带 drawtext 的
        解释顺序（PATH 首选发行版缺 drawtext，2026-10-19 实测）；argv 直传不经 shell。"""
        title_font = TITLE_FONT_FILE
        if not os.path.isfile(title_font):
            raise RuntimeError(f"标题字体缺失: {title_font}（env DRIVER_TITLE_FONT 可指定）")
        sub_font = SUBTITLE_FONT_NAME
        sub_font_file = os.environ.get("DRIVER_SUB_FONT_FILE") or title_font
        if not os.path.isfile(sub_font_file):
            raise RuntimeError(f"字幕字体文件缺失: {sub_font_file}（env DRIVER_SUB_FONT_FILE 可指定）")
        titles = json.load(open(map_fp, encoding="utf-8"))
        if isinstance(titles, dict):
            titles = titles.get("news") or []  # 兼容旧列表格式
        # 同标题连续段合并为一个时间窗（标题只在新闻边界切换）
        windows = []
        for t in titles:
            if windows and windows[-1]["title"] == t["title"]:
                windows[-1]["end"] = t["end"]
            else:
                windows.append({"title": t["title"], "start": t["start"], "end": t["end"]})
        drawtexts = []
        font_escaped = _filter_escape(title_font)
        for w in windows:
            text = (w["title"].replace("'", r"\'")
                    .replace(":", r"\:").replace(",", r"\,"))
            drawtexts.append(
                f"drawtext=fontfile={font_escaped}:expansion=none:text='{text}':fontcolor=white:fontsize=40:"
                f"borderw=4:bordercolor=black:shadowcolor=black@0.6:shadowx=3:shadowy=3:"
                f"x=(w-text_w)/2:y=40:enable='between(t,{w['start']},{w['end']})'")
        vf = (f"subtitles={_filter_escape(srt)}:force_style='Alignment=2,MarginV=60,FontSize=20,"
              f"FontName={sub_font},Outline=3,Shadow=1,PrimaryColour=&H00FFFFFF,"
              f"OutlineColour=&H00000000,BackColour=&H80000000'")
        if drawtexts:
            vf += "," + ",".join(drawtexts)
        cmd = [_resolve_ffmpeg(), "-y", "-v", "error", "-i", src, "-vf", vf,
               "-c:v", "libx264", "-preset", "fast", "-crf", "24", "-c:a", "copy", out_fp]
        p = run_argv(cmd, timeout=7200)
        if p[0] != 0:
            raise RuntimeError(f"烧录失败: {p[1][-300:]}")

    # ── 确认门（#30 决议1/2 + 2026-09-05 拍板：④⑤合并为一次审核）────────
    def _gate(self, stage_no, title, caption, image=None, file=None, regen_fn=None):
        self.notify(f"🔔 确认门{stage_no}：{title}\n按提示回复；修改意见/超时 = driver 退出叫 agent，处理后重启续跑。")
        # 决议循环（票8 #67 Q2）：gate_confirm 返回 Decision（pass/retry/abort，
        # 政策表住进门内），driver 只执行——retry 重做后重新送审，abort 交回现场。
        # since 每轮入口重取（与旧递归语义一致：重做执行期间的新回复不计入下轮窗口）。
        while True:
            since = int(time.time() * 1000)
            r = self.feishu.gate_confirm(caption=caption, image_path=image, file_path=file,
                                         since_ms=since, timeout_s=self.args.gate_timeout)
            # 门回复留痕（人工回填 rotation-dataset user_flagged 的对照源；分析时对照段号）
            self.ledger.record_event("gate_replies",
                                     {"gate": stage_no, "status": r["status"],
                                      "reply": (r.get("reply") or "")[:300],
                                      "at": time.strftime("%Y-%m-%d %H:%M:%S")})
            if r["action"] == "pass":
                return
            if r.get("status") == "flag":
                # T17 #129 人工指认：存证+真值回填+指名段重烧复判（不停机，同门内重试语义）
                self.notify(f"🚩 收到人工指认: {r.get('shots') or '（段号解析见日志）'}，存证后重烧复判…")
                self._human_flag_loop(r.get("shots") or [], r.get("reply") or "")
                continue
            if r["action"] == "retry" and regen_fn:
                shots = r.get("shots") or []
                if shots:
                    # 镜头级重生成（2026-09-11）：「重生成 shotXX」→ 删除式消毒重烧点名段 → 重新送审
                    self.notify(f"🎬 收到镜头级重生成指令: {shots}，删除式消毒后重烧…")
                else:
                    self.notify("收到重生成指令，执行后重新送审…")
                regen_fn(shots)  # shots 空表 = 兼容旧封面重做（Q3 统一语义）
                continue
            # abort（feedback / timeout / error；retry 但无 regen_fn 同样落到此）→ 交回 agent 现场
            self.notify(f"🚧 确认门{stage_no} 未通过（{r['status']}）：{r['reply'][:200] or '超时无回复'}\n"
                        f"driver 暂停退出，agent 回现场处理后重启续跑（stamps 保留，确认门重进）。\n"
                        f"用户已口头批准时：agent 重启加 --gate-approved（agent 代发的群消息是 bot 身份，轮询读不到）。\n"
                        f"slug: {self.slug}")
            self._flush_exit(4)
            return  # 真路径 os._exit 不返回；return 保桩环境/未来变更下循环必终止

    def _flush_exit(self, code):
        """#48 最小修复：门超时/反馈退出路径——先 flush 台账再硬退出（已拍板不做
        全量线程审计）。sys.exit 抛 SystemExit 后解释器要等全部非 daemon 线程收尾，
        0922 实测门退出后被遗留线程卡住 30+ 分钟不退；os._exit 绕过线程收尾，进程
        秒级消失。ledger.flush 幂等合并（0908 根治，票7 #65 起 ledger.py 承接）且
        自吞异常、同步返回即落盘完成，此处显式落盘后 run().finally 不再执行，
        不会双计；台账写入失败也只落日志，不阻塞退出。"""
        self.ledger.flush()
        self.hb.exit(code, "确认门未通过（超时/修改意见/重生成无执行者）")
        log(f"[gate] 台账已 flush，硬退出 code={code}（绕过非 daemon 线程收尾）")
        os._exit(code)

    def s_gate_review(self):
        """确认门④+⑤ 合并审核（2026-09-05 用户拍板：审一次就行）。
        一次发：审核版成片 + 三平台定制封面（B站横版/抖音竖版/视频号竖版，标题已印）。
        「重生成」= 重做封面再送审；「重生成 shotXX」= 删除式消毒重烧点名镜头后重送审（2026-09-11）。"""
        # 窗口纪律尾部停（#85，conflict 模式）：s11 之后 s12/s13 无 LLM 阶段，确认门
        # 轮询+发布期间 Strata 不再闲占 94% VRAM（幂等 already-off；重生成 shotXX /
        # 重做封面的重试路径经 s08/s11 入口钩子自然重新起停）。
        if strata_window.WINDOW_MODE == "conflict":
            info = strata_window.stop(notify=self.notify)
            self.ledger.record("strata_window.s12_entry_close", info)
        plat_covers = [os.path.join(self.d["09_封面"], f"封面_{p}.png")
                       for p in ("bilibili", "douyin", "kuaishou", "toutiao", "shipinhao")]
        plat_covers = [c for c in plat_covers if os.path.exists(c)]
        if not plat_covers:  # 兜底：平台封面缺失时退回 Krea2 底图
            covers = sorted(glob.glob(os.path.join(self.d["09_封面"], "*.png")))
            plat_covers = covers[:2]
        preview = os.path.join(self.d["07_成片"], "最终成片_预览版.mp4")
        # 九宫格对照表：vision 判读对本风格不可靠（rotation-analysis 实证），旋转终审 = 人眼
        sheet = self._contact_sheet()
        self.ledger.record("gate_sheet", bool(sheet))
        # vision 未判段点名（2026-09-08 根治）：判读失败段此前止步于日志，
        # 人审者不知道哪几段没过机检——点名让人审有重点。
        # 票7 #65：跨重启恢复走 ledger merged 视图（全库唯一读方）。修复 bug①：
        # 旧磁盘回退错读 stages.s08.vision（实际落盘顶层 s08 块），断点续跑时
        # 恢复恒为空——门④的未判段点名只在同进程内生效过。
        judge_failed = self.ledger.get("s08.vision.judge_failed", view="merged") or []
        # tilted 与 judge_failed 同走 merged（票7 review 补全）：旧实现 tilted 无磁盘
        # 回退，重启后倾斜点名同丢——同一 seam 的同一修复，未判段点名与倾斜点名一致
        tilted = self.ledger.get("s08.vision.tilted", view="merged") or []
        # T4 #106：未判段/倾斜段/CLIP 分歧段三张点名名单收口到 _gate_notes 纯函数；
        # T8 #117：置信度门控三级（校准后）随文案可见
        clip_esc = self.ledger.get("s08.vision.clip_escalated", view="merged") or []
        gate = None
        if (self.ledger.get("s08.vision.form", view="merged") == "jev"
                and self.ledger.get("s08.vision.calibrated", view="merged")):
            gate = {"auto": self.ledger.get("s08.vision.gate_auto", view="merged") or 0,
                    "spot": self.ledger.get("s08.vision.spot_check", view="merged") or [],
                    "low": self.ledger.get("s08.vision.low_conf", view="merged") or []}
        unjudged_note, tilted_note, clip_note, gate_note = _gate_notes(
            judge_failed, tilted, clip_esc, gate)
        # T16 #128：判官复判摘要进门④文案（判官结论+重烧名单+分歧），merged 视图
        # 跨重启可读回；判官未跑/不可用 → 空串不占文案（fail-open 静默口径一致）
        judge_note = self._judge_gate_note()
        if getattr(self.args, "gate_approved", False):
            self.notify("✅ 用户已在 agent 会话明确批准发布（--gate-approved），跳过飞书轮询直接进入发布。")
            return
        pack_fp = os.path.join(self.d["09_封面"], "平台标题.json")
        title_note = ""
        if os.path.exists(pack_fp):
            pk = json.load(open(pack_fp, encoding="utf-8"))
            title_note = ("\n各平台标题：\n" + "\n".join(
                f"· {name}：{pk[p]['title']}"
                for p, name in (("bilibili", "B站"), ("douyin", "抖音"), ("kuaishou", "快手"),
                                ("toutiao", "头条"), ("shipinhao", "视频号"))
                if p in pk))
        self._gate("④+⑤", "成片+封面合并审核（审一次）",
                   "成片与五平台定制封面（B站横版→抖音/快手/头条/视频号竖版，标题已印在封面上）如上。" + (
                       "最后一张「镜头九宫格」是全部段的中帧对照，请扫一眼有无横躺/旋转/倾斜的段，"
                       "有问题直接回复「重生成 段号」（如：重生成 shot02，可多个/范围）。" if sheet else ""
                   ) + unjudged_note + tilted_note + clip_note + gate_note +
                   judge_note + title_note +
                   "回复【通过】即发布；对标题/封面字不满意回复「重生成」（重拟标题与封面大字，底图不变）；"
                   "成片要改 = 直接回复修改意见（agent 介入）。"
                   "注意：回复带 shotNN 点名（如「shot02 不行」）= 镜头级重做，不走 agent；"
                   "要 agent 处理请勿带段号。",
                   image=plat_covers + ([sheet] if sheet else []),
                   file=preview if os.path.exists(preview) else None,
                   regen_fn=self._shot_regen_and_rebuild)

    def _shot_regen_and_rebuild(self, shots=None):
        """确认门④重生成分发：带段号 → 镜头级重生成（删除式消毒重烧+重装配+重烧字幕）；
        无段号 → 兼容旧封面重做。"""
        if not shots:
            self._cover_regen()
            return
        still = self._regen_shots_strip_motion(shots)
        if still:
            self.notify(f"🚨 重烧后仍翻转: {still}，需人工处理（回复新的重生成指令或转 agent）")
        # 重装配：段变了，成片/字幕/九宫格全部重建（标题/封面不依赖段画面，不动）
        self.s09_compose()
        self.s10_subs_burn()

    # ── T17 #129 人工指认回路 ────────────────────────────────────────────
    _FLAG_OK_WORDS = ("正常", "ok", "通过", "没问题")
    _FLAG_NUM_RE = re.compile(r"第\s*([0-9０-９]{1,2})\s*段")

    @classmethod
    def _flag_is_ok(cls, verdict):
        """混合结论归一：正常/ok/通过/没问题（大小写不敏感）→ 只回填真值不重烧。"""
        return (verdict or "").strip().lower() in cls._FLAG_OK_WORDS

    def _shots_from_text(self, text):
        """指认文本兜底段号解析：shotNN（复用门解析）→ 「第N段」→ 裸数字最后手段。"""
        t = text or ""
        ids = self.feishu.parse_shot_ids(t)
        if ids:
            return [f"shot{v:02d}" for v in ids]
        out = []
        tr = str.maketrans("０１２３４５６７８９", "0123456789")
        for m in self._FLAG_NUM_RE.finditer(t):
            v = int(m.group(1).translate(tr))
            if 1 <= v <= 99:
                out.append(f"shot{v:02d}")
        if out:
            return sorted(set(out))
        for m in re.finditer(r"(?<![\d.])(\d{1,2})(?![\d.])", t):
            v = int(m.group(1))
            if 1 <= v <= 99:
                out.append(f"shot{v:02d}")
        return sorted(set(out))

    def _freeze_flag_frame(self, src, sid):
        """指认瞬间把工作帧拷进不可变目录（ADR 0008 决策 3：重烧覆盖帧之前固化）。

        目录沿用 #132 起扁平存证口径 evidence/human_flag/<sid>_<时间戳><ext>；
        同秒重名追加 _02/_03 后缀——冻结帧是账，覆盖即失账（sha256 也会失配）。
        失败返回 None（存证降级，不挡重烧）。"""
        try:
            ev_dir = os.path.join(self.slug, "_frames", "evidence", "human_flag")
            os.makedirs(ev_dir, exist_ok=True)
            root, ext = os.path.splitext(
                os.path.join(ev_dir, f"{sid}_{time.strftime('%Y%m%d_%H%M%S')}"))
            ev, n = root + (ext or os.path.splitext(src)[1]), 1
            while os.path.exists(ev):
                n += 1
                ev = f"{root}_{n:02d}{ext or os.path.splitext(src)[1]}"
            shutil.copy2(src, ev)
            return ev
        except Exception as e:  # noqa: BLE001 — 存证失败不挡重烧
            log(f"[flag] {sid} 冻结定罪帧失败（不阻塞）: {e}")
            return None

    def _manifest_human_flag(self, sid, src, verdict, reply):
        """人审桶落生产 manifest（ADR 0008 决策 1/3）：category=4-human-flag、
        round=human_flag；frame 记冻结副本本身（不再往 category 目录拷第二份——
        清单里那条就是盘上那张），sha256 与盘上一致。

        去重键 (category, sid, round) 沿用 _archive_evidence_buckets 同一口径：
        同一段再次指认（human-reflag）先查清单，已记过就直接复用那条、连帧都
        不再拷第二份（AC3），行内回填随即指回同一张帧（AC2「同路径」）。
        返回清单里的权威条目；口径读不到/冻结失败/写失败返回 None。"""
        try:
            from rotation_dataset import HUMAN_FLAG_CATEGORY, HUMAN_FLAG_ROUND
        except Exception as e:  # noqa: BLE001 — 口径读不到就只留扁平存证
            log(f"[flag] 读人审桶口径失败（不阻塞）: {e}")
            return None
        ev_dir = os.path.join(self.slug, "_frames", "evidence")
        try:
            mf_path, manifest, entries = _load_evidence_manifest(
                ev_dir, [self.date, os.path.basename(os.path.normpath(self.slug))])
            key = (HUMAN_FLAG_CATEGORY, sid, HUMAN_FLAG_ROUND)
            for e in entries:
                if (e.get("category"), e.get("sid"), e.get("round")) == key:
                    log(f"[flag] {sid} 人审桶已记过（同 category/sid/round），"
                        f"沿用 {e.get('frame')}")
                    return e
            ev = self._freeze_flag_frame(src, sid)
            if not ev:
                return None
            entry = {
                "category": HUMAN_FLAG_CATEGORY, "sid": sid, "round": HUMAN_FLAG_ROUND,
                "frame": ev, "frame_win": hostpath.to_win_path(ev),
                "source_frame": src, "sha256": _sha256_file(ev),
                "judge": None, "conf": None, "why": (reply or "")[:300],
                "local_verdict": verdict, "kind": "human_flag",
                "synthetic": False, "note": "",
            }
            entries.append(entry)
            manifest["evidence"] = entries
            _write_evidence_manifest(ev_dir, mf_path, manifest)
            log(f"[flag] {sid} 人审桶入 manifest（共 {len(entries)} 条）: {ev}")
            return entry
        except Exception as e:  # noqa: BLE001 — 清单写失败不挡重烧
            log(f"[flag] {sid} 写人审桶 manifest 失败（不阻塞）: {e}")
            return None

    def _flag_commit(self, sid, verdict, reply, who="human"):
        """指认瞬间落账（ADR 0008）：重烧覆盖帧之前固化定罪帧，并把同一份证据一次
        写进三处——生产 manifest（4-human-flag / human_flag）、数据集该行行内
        evidence_frames、台账 human_flags 顶层平键；末尾 flush，指认回调返回时
        账已落盘（AC4：门读顶层平键就能读到本次记录）。

        verdict 非正常类才存帧；返回证据路径（无帧可存/正常时 None）。"""
        flagged = not self._flag_is_ok(verdict)
        ev = None
        entry = None
        if flagged:
            frames = os.path.join(self.slug, "_frames")
            src = None
            for ext in (".jpg", ".png"):
                p = os.path.join(frames, f"{sid}{ext}")
                if os.path.exists(p):
                    src = p
                    break
            if src:
                entry = self._manifest_human_flag(sid, src, verdict, reply)
                ev = (entry or {}).get("frame") or None
        # 顶层平键（T16 教训：嵌套 dict 增量整体赢，重启丢历史；list 拼接去重才跨重启累积）
        self.ledger.record_event("human_flags", {
            "sid": sid, "who": who, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
            "verdict": verdict, "reply": (reply or "")[:300], "evidence": ev})
        # 数据集行的 slug 是 workdir 目录名（rotation_dataset 写入时取 basename），
        # 而 self.slug 是绝对路径——不折叠则键恒不匹配、人工真值只落台账（#139 收口评审）
        ds_slug = os.path.basename(os.path.normpath(self.slug))
        try:  # 真值回填：user_flagged/user_flagged_source 独立字段，模型侧不覆盖
            from rotation_dataset import set_user_flag
            updated, missing = set_user_flag(
                [(self.date, ds_slug, sid, flagged)], source="human-flag")
            if missing:
                log(f"[flag] {sid} 不在数据集（missing={missing}），真值只落台账")
        except Exception as e:  # noqa: BLE001 — 数据集缺失不挡存证与重烧
            log(f"[flag] set_user_flag 异常（忽略，真值只落台账）: {e}")
        if entry:  # 行内证据帧：与 manifest 那条同路径、同 sha256（AC2）
            try:  # 后置写口独立成段：真值回填失败不许连坐行内证据（ADR 0008 决策 4）
                from rotation_dataset import set_evidence_frames
                ef_updated, ef_missing = set_evidence_frames(
                    [(self.date, ds_slug, sid, entry)])
                if ef_missing:
                    log(f"[flag] {sid} 行内证据帧未命中（missing={ef_missing}），"
                        f"证据只落 manifest")
            except Exception as e:  # noqa: BLE001 — 数据集缺失不挡存证与重烧
                log(f"[flag] set_evidence_frames 异常（忽略，证据只落 manifest）: {e}")
        # AC4：指认回调返回前账必须落盘（门/CLI 结束后立刻可读顶层平键）。
        self.ledger.flush()
        return ev

    def _human_flag_loop(self, shots, reply):
        """T17 #129 指认回路：存证+真值回填 → 指名段删除式消毒重烧（内含复判→仍翻
        竖直化换法再烧共两轮）→ 不翻则 s09+s10 重建收口；仍翻再存证后交人。
        段号解析失败也交人——指认无段号无法执行（门语义：不停机等指令，交给 agent 现场）。"""
        shots = shots or self._shots_from_text(reply)
        if not shots:
            self.notify("🚩 收到指认但解析不出段号（示例：「shot03 翻转了」）；转 agent 处理")
            self._flush_exit(4)
            return
        for sid in shots:
            ev = self._flag_commit(sid, "翻转", reply)
            log(f"[flag] {sid} 指认存证: {ev or '无帧可存'}")
        still = self._regen_shots_strip_motion(shots, evidence_tag="human_flag")
        if still:
            for sid in still:  # 复判仍翻：换法重烧后再存一次（证据链含两轮）
                self._flag_commit(sid, "翻转", reply, who="human-reflag")
            self.notify(f"🚨 指认重烧复判仍翻转: {still}，转 agent 处理")
            self._flush_exit(4)
            return
        self.notify(f"✅ 指认段 {shots} 重烧复判通过，重建成片…")
        self.s09_compose()
        self.s10_subs_burn()

    def run_flag_cli(self, spec):
        """T17 #129 CLI 指认入口：--flag-shots "shot03=翻转,shot05=正常"。
        逐条真值回填（翻转者存证）→ 翻转者重烧复判 → 正常者不动画面。
        格式非法 SystemExit；供 DSH 会话直接调用。"""
        items = []
        for part in (spec or "").split(","):
            part = part.strip()
            if not part:
                continue
            sid, sep, verdict = part.partition("=")
            sid, verdict = sid.strip(), (verdict.strip() or "翻转")
            if not sep or not re.fullmatch(r"shot\d{1,2}", sid):
                raise SystemExit(f"--flag-shots 段格式非法（应 shotNN=翻转|正常）: {part!r}")
            items.append((sid, verdict))
        if not items:
            raise SystemExit('--flag-shots 为空（示例: "shot03=翻转,shot05=正常"）')
        flag_shots = [sid for sid, v in items if not self._flag_is_ok(v)]
        for sid, verdict in items:
            ev = self._flag_commit(sid, verdict, spec, who="cli")
            log(f"[flag-cli] {sid}={verdict} 存证: {ev or '（正常/无帧）'}")
        if not flag_shots:
            self.notify(f"✅ CLI 指认回填完毕（全部正常，不重烧）: {items}")
            return
        still = self._regen_shots_strip_motion(flag_shots, evidence_tag="human_flag")
        if still:
            for sid in still:
                self._flag_commit(sid, "翻转", spec, who="cli-reflag")
            self.notify(f"🚨 CLI 指认重烧复判仍翻转: {still}，转 agent 处理")
            self._flush_exit(4)
            return
        self.notify(f"✅ CLI 指认处理完毕: {items}，重建成片…")
        self.s09_compose()
        self.s10_subs_burn()

    def _cover_regen(self):
        """确认门「重生成」：删平台标题缓存 → s09 重拟标题+封面字（Krea2 底图保留不重画）。"""
        pack_fp = os.path.join(self.d["09_封面"], "平台标题.json")
        if os.path.exists(pack_fp):
            os.remove(pack_fp)
        self.s11_cover()

    # ── 阶段10 封面（GPU）+ 确认门⑤ ────────────────────────────────────
    def s11_cover(self):
        """Krea2 数字迷幻封面（读稿提炼参考文本 → --ref-file 传参 → 收割产物）。

        票9 #72：源码移植术拆除——旧实现读 gen_krea2_cover.py 源码文本 re.sub
        打 REF_H/REF_V/HERE 三补丁后写临时副本执行（全仓唯一运行期
        source-patching，且 re.sub repl 模板语义使 ref 含 \\U/\\N 类反斜杠即炸
        PatternError——LLM 中文文本无法排除）。现改 ref 落盘 slug/_cover_ref.txt
        + --ref-file 文件传参（零转义雷、盘上留痕），脚本原地执行 HERE 补丁
        自然消失。gen_krea2_cover.py 只提交 ComfyUI 不搬产物（OUT_DIR 未用），
        driver 按 mtime 快照收割新 PNG：先提交横版(H)后竖版(V)，按完成顺序命名。
        底图流程固定两张：B站=横版、抖音/视频号=竖版；标题打印见 _platform_covers。"""
        h_base = os.path.join(self.d["09_封面"], "海报_krea2数字迷幻_横版.png")
        v_base = os.path.join(self.d["09_封面"], "海报_krea2数字迷幻_竖版.png")
        if os.path.exists(h_base) and os.path.exists(v_base):
            self._covers = [h_base, v_base]
            log("[s11] Krea2 底图已在（断点续跑），跳过生成")
        else:
            # 窗口再开（#85 窗口纪律：conflict 模式 s08 生图前停了 Strata，此处
            # _cover_ref_text 需要 LLM 再拉起——单条视频第二次加载；底图已在的
            # 断点续跑不需要 LLM，不白拉）。coexist 模式下幂等复用在线实例。
            info = strata_window.ensure_online(notify=self.notify)
            self.ledger.record("strata_window.s11_entry_reopen", info)
            ref = self._cover_ref_text()
            os.makedirs(self.d["09_封面"], exist_ok=True)
            ref_fp = os.path.join(self.slug, "_cover_ref.txt")
            with open(ref_fp, "w", encoding="utf-8") as f:
                f.write(ref)
            snapshot = set(glob.glob(os.path.join(WIN_COVER_OUT, "**", "*.png"), recursive=True))
            rc, out = _cover_shell([sys.executable, os.path.join(HERE, "gen_krea2_cover.py"),
                                self.slug, "--ref-file", ref_fp], 10800)  # 横竖两版串行，单版 90min 上限
            log(f"gen_krea2_cover 尾部: {out[-200:]}")
            new = sorted(set(glob.glob(os.path.join(WIN_COVER_OUT, "**", "*.png"), recursive=True)) - snapshot,
                         key=os.path.getmtime)
            # 按图片实际尺寸分类（mtime 顺序不可靠——单任务会落多张中间图）
            from PIL import Image
            h_new = [f for f in new if (lambda s: s[0] > s[1])(Image.open(f).size)]
            v_new = [f for f in new if (lambda s: s[1] > s[0])(Image.open(f).size)]
            if not h_new or not v_new:
                raise RuntimeError(f"封面收割不全：横版 {len(h_new)} 张 / 竖版 {len(v_new)} 张（需各≥1），重试")
            covers = []
            for cls, src in (("横版", h_new[-1]), ("竖版", v_new[-1])):
                dst = os.path.join(self.d["09_封面"], f"海报_krea2数字迷幻_{cls}.png")
                shutil.copy2(src, dst)
                covers.append(dst)
            self._covers = covers
            log(f"封面收割: 横 {Image.open(h_new[-1]).size} / 竖 {Image.open(v_new[-1]).size} → {covers}")
        self._platform_covers()

    def _platform_covers(self):
        """平台定制封面（2026-09-05 用户拍板）：按 platform-title-playbook 为 B站/抖音/视频号
        各拟标题+封面大字（LLM），PIL 叠到 Krea2 底图（B站横版 / 抖音·视频号竖版），
        封面大字与标题互补不重复、按各平台 UI 安全区排版。"""
        pack_fp = os.path.join(self.d["09_封面"], "平台标题.json")
        if not os.path.exists(pack_fp):
            rc, out = run_argv([sys.executable, os.path.join(HERE, "gen_platform_titles.py"),
                                self.script, pack_fp, "--date", self.date], timeout=600)
            log(f"平台标题生成尾部: {out[-300:]}")
            if rc != 0 or not os.path.exists(pack_fp):
                raise RuntimeError(f"平台标题生成失败: {out[-200:]}")
        pack = json.load(open(pack_fp, encoding="utf-8"))
        base = {"bilibili": "海报_krea2数字迷幻_横版.png",  # B站横版，其余平台均竖版（用户拍板）
                "douyin": "海报_krea2数字迷幻_竖版.png",
                "kuaishou": "海报_krea2数字迷幻_竖版.png",
                "toutiao": "海报_krea2数字迷幻_竖版.png",
                "shipinhao": "海报_krea2数字迷幻_竖版.png"}
        self._plat_covers, self._plat_pack = {}, pack
        for plat in ("bilibili", "douyin", "kuaishou", "toutiao", "shipinhao"):
            out_fp = os.path.join(self.d["09_封面"], f"封面_{plat}.png")
            rc_o, out_o = run_argv([sys.executable, os.path.join(HERE, "overlay_cover_text.py"),
                                    "--base", os.path.join(self.d["09_封面"], base[plat]),
                                    "--out", out_fp, "--text", pack[plat]["cover_text"],
                                    "--platform", plat], timeout=300)
            if rc_o != 0 or not os.path.exists(out_fp):
                raise RuntimeError(f"{plat} 封面叠字失败: {out_o[-200:]}")
            self._plat_covers[plat] = out_fp
        log("平台封面: " + " | ".join(
            f"{plat}「{pack[plat]['cover_text']}」→ {os.path.basename(fp)}"
            for plat, fp in self._plat_covers.items()))

    def _cover_ref_text(self):
        """阶段10 动作：读稿提炼 150-280 字中文参考文本（主体+场景+材质+色调+构图）。"""
        import llm_json
        draft = open(self.script, encoding="utf-8").read()
        ref_schema = {"type": "object", "required": ["ref"],
                      "properties": {"ref": {"type": "string"}}}
        obj = llm_json.llm_call_json(
            "你是 Krea2 数字迷幻流海报艺术指导。根据口播稿提炼封面参考文本：150-280 字中文，"
            "必须含 主体+场景+材质+色调+构图，并嵌入当日日期文字元素。只输出 JSON：{\"ref\": \"...\"}",
            draft[:3000], ref_schema, model=os.environ.get("LLM_MODEL", "strata"), budget=800)
        ref = (obj or {}).get("ref", "")
        if not (120 <= len(ref) <= 320):
            raise RuntimeError(f"封面参考文本不合格（{len(ref)} 字）")
        return ref

    # ── 阶段11 发布 + 核验 ──────────────────────────────────────────────
    def _headline(self):
        md = f"{DATE_ROOT}/{self.date}/3新闻_概述.md"
        pri = ["机器人", "具身", "人工智能", "AI", "科技自立", "制造", "工业", "芯片", "半导体", "新能源"]
        best, score = "", -1
        if os.path.exists(md):
            for m in re.finditer(r"###\s+\[.+?\]\s+(.+?)\n", open(md, encoding="utf-8").read()):
                t = m.group(1).strip()
                if "当日无真实报道" in t or "栏目留空" in t:
                    continue
                sc = sum(2 for k in pri if k in t) + max(0, 30 - len(t)) * 0.1
                if sc > score:
                    best, score = t, sc
        return best or "每日新中国新闻简报"

    def s13_publish(self):
        """发布 B站→抖音（45-90s 间隔）+核验；视频号恒降级待发文件夹；出核验报告。
        2026-09-05 起各平台用定制标题/封面/标签（平台标题.json，platform-title-playbook）。"""
        t0 = time.time()
        video = os.path.join(self.d["07_成片"], "最终成片.mp4")  # 发布用全质量成片（预览版仅飞书审核用）
        headline = self._headline()
        pack_fp = os.path.join(self.d["09_封面"], "平台标题.json")
        pack = json.load(open(pack_fp, encoding="utf-8")) if os.path.exists(pack_fp) else {}
        b_title = pack.get("bilibili", {}).get("title") or f"每日新中国 {self.date}：{headline[:24]}"
        d_title = pack.get("douyin", {}).get("title") or headline[:28]
        s_title = pack.get("shipinhao", {}).get("title") or f"每日新中国 {self.date} 正面新闻简报"
        k_title = pack.get("kuaishou", {}).get("title") or headline[:30]
        t_title = pack.get("toutiao", {}).get("title") or headline[:28]
        d_tags = pack.get("douyin", {}).get("tags") or "每日新中国,新闻简报"
        b_tags = pack.get("bilibili", {}).get("tags") or BILI_TAGS
        # B站 10 标签硬保证（2026-09-09）：LLM 产出不足 10 个时用活动+话题模板补齐，超出截断
        _bt = [t.strip() for t in b_tags.split(",") if t.strip()]
        if len(_bt) < 10:
            for t in (BILI_ACTIVITY_TAGS + "," + BILI_TOPIC_TAGS).split(","):
                if t.strip() and t not in _bt:
                    _bt.append(t)
                if len(_bt) >= 10:
                    break
        b_tags = ",".join(_bt[:10])
        # 平台封面自愈（2026-09-06）：s09 被 stamps 跳过时 _plat_covers 未重建，
        # 直接发布会退化成无标题海报底图——从磁盘按命名约定恢复
        cov = dict(self._plat_covers) if getattr(self, "_plat_covers", None) else {}
        for _p in ("bilibili", "douyin", "kuaishou", "toutiao", "shipinhao"):
            if _p not in cov:
                _fp = os.path.join(self.d["09_封面"], f"封面_{_p}.png")
                if os.path.exists(_fp):
                    cov[_p] = _fp
        covers = sorted(glob.glob(os.path.join(self.d["09_封面"], "*.png")))
        h_cover = cov.get("bilibili") or next(
            (c for c in covers if "横" in os.path.basename(c) or "_H" in os.path.basename(c)),
            covers[0] if covers else "")
        v_cover = cov.get("douyin") or next(
            (c for c in covers if "竖" in os.path.basename(c) or "_V" in os.path.basename(c)),
            covers[-1] if covers else "")
        report = [f"# 发布核验报告 {self.date}\n", f"- 成片: {video}",
                  f"- 标题（B站）: {b_title}", f"- 标题（抖音）: {d_title}",
                  f"- 标题（快手）: {k_title}", f"- 标题（头条）: {t_title}",
                  f"- 标题（视频号）: {s_title}\n"]
        results = {}

        def _pub(platform, extra, timeout=3600):
            # #151：sau 在 WSL 侧（登录态/venv），wsl.exe 单层调用；文件参数
            # （成片/封面在 E:）经 win_to_wsl 换 /mnt 口径；argv 数组不经 shell，
            # 标题/标签含空格引号都安全。DISPLAY 隔离保持（env -u DISPLAY）。
            args = ["upload-video", "--account", ACCOUNT, "--file", win_to_wsl(video)]
            args += [a for a in extra if a]  # 空串（条件参数缺省）不进 argv
            argv = (["wsl.exe", "-d", WSL_DISTRO, "--cd", SAU_DIR, "env", "-u", "DISPLAY", SAU, platform]
                    if platform in ("douyin", "kuaishou", "toutiao")
                    else ["wsl.exe", "-d", WSL_DISTRO, "--cd", SAU_DIR, SAU, platform])
            rc, out = run_argv(argv + args, timeout=timeout)
            return rc, out

        if "bilibili" in self.platforms:
            # 2026-09-09 定稿：B站直调 biliup-cli，不再走 sau（幽灵失败 0819+0909 两实证；
            # biliup 直调带 BV 返回 + --tag 单数 + --cover 支持）。核验=有 BV 号，无 BV 视为失败
            bili_bin = "/home/lmr/.social-auto-upload/tools/biliup/linux-x86_64/biliup"
            bili_cookie = "/home/lmr/social-auto-upload/cookies/bilibili_diyi.json"
            # #151：biliup 在 WSL 侧，wsl.exe 单层；成片/封面经 /mnt 口径传入
            b_argv = ["wsl.exe", "-d", WSL_DISTRO, "--cd", SAU_DIR, bili_bin,
                      "-u", bili_cookie, "upload", win_to_wsl(video),
                      "--title", b_title,
                      "--desc", f"每日更新中国新闻简报。本期亮点：{headline}",
                      "--tid", BILI_TID, "--tag", b_tags]
            if h_cover:
                b_argv += ["--cover", win_to_wsl(h_cover)]
            rc, out = run_argv(b_argv, timeout=1800)
            bv = (re.search(r"(BV[0-9A-Za-z]+)", out) or [None, ""])[1]
            ok = rc == 0 and bool(bv)  # 无 BV = 幽灵失败，判失败
            results["bilibili"] = {"ok": ok, "bv": bv}
            report += [f"- B站: {'✅' if ok else '❌'} {bv or out[-120:]}"]
            time.sleep(45)
        if "douyin" in self.platforms:
            d_hash = " ".join("#" + t.strip() for t in d_tags.split(",") if t.strip())
            rc, out = _pub("douyin", [
                "--title", d_title,
                "--desc", f"{d_title} {d_hash}",
                "--thumbnail", win_to_wsl(v_cover) if v_cover else "",
                "--declaration", "内容由AI生成", "--headless"], timeout=3600)
            ok = rc == 0
            results["douyin"] = {"ok": ok}
            report += [f"- 抖音: {'✅ 已提交' if ok else '❌'}（作品管理页需 agent/人工复核状态）{'' if ok else out[-120:]}"]
        if "kuaishou" in self.platforms:
            time.sleep(45)
            k_cover = cov.get("kuaishou") or v_cover
            k_tags = ",".join(t.strip() for t in (pack.get("kuaishou", {}).get("tags") or "科技,涨知识,正能量").split(",")[:3])  # 快手标签 ≤3
            rc, out = _pub("kuaishou", [
                "--title", k_title,
                "--desc", k_title,
                "--tags", k_tags,
                "--thumbnail", win_to_wsl(k_cover) if k_cover else "",
                "--headless"], timeout=3600)
            ok = rc == 0
            results["kuaishou"] = {"ok": ok}
            report += [f"- 快手: {'✅ 已提交' if ok else '❌'}{'' if ok else out[-120:]}"]
        if "toutiao" in self.platforms:
            time.sleep(45)
            t_cover = cov.get("toutiao") or v_cover
            t_tags = pack.get("toutiao", {}).get("tags") or "科技,人工智能,新闻"
            rc, out = _pub("toutiao", [
                "--title", t_title[:30],
                "--desc", headline[:60],
                "--tags", t_tags,
                "--thumbnail", win_to_wsl(t_cover) if t_cover else "",
                "--declaration", "AI生成",
                "--headless"], timeout=3600)
            ok = rc == 0
            results["toutiao"] = {"ok": ok}
            report += [f"- 头条: {'✅ 已提交' if ok else '❌'}（30字硬校验+AI声明）{'' if ok else out[-120:]}"]
        # 视频号恒降级（out of scope，map 决议）：待发文件夹附发布信息（定制标题/标签/封面）
        pending = os.path.join(self.d["10_发布"], "待发视频号")
        os.makedirs(pending, exist_ok=True)
        shutil.copy2(video, pending)
        for c in covers:
            shutil.copy2(c, pending)
        s_cover = self._plat_covers.get("shipinhao") if getattr(self, "_plat_covers", None) else ""
        def _pf(plat):
            return self._plat_covers.get(plat) if getattr(self, "_plat_covers", None) else ""
        with open(os.path.join(pending, "发布信息.txt"), "w", encoding="utf-8") as f:
            f.write("以下三个平台走人工/MatrixMedia 发布（driver 默认只自动发 B站+抖音）\n"
                    "快手也可 `sau kuaishou upload-video --account diyi` 自动发，头条同理\n\n")
            for plat, name, title, tags in (
                    ("shipinhao", "视频号", s_title, pack.get("shipinhao", {}).get("tags", "中国科技,正能量,新闻简报")),
                    ("kuaishou", "快手", k_title, pack.get("kuaishou", {}).get("tags", "科技,涨知识,正能量")),
                    ("toutiao", "今日头条", t_title, pack.get("toutiao", {}).get("tags", "科技,人工智能,新闻"))):
                f.write(f"== {name} ==\n标题：{title}\n标签：{tags}\n"
                        f"封面：{os.path.basename(_pf(plat)) if _pf(plat) else '封面_' + plat + '.png（若缺用竖版海报）'}\n\n")
            f.write("视频：最终成片.mp4\n")
        report += [f"- 视频号/快手/头条: ⏬ 降级待发文件夹 {pending}（标题/标签/封面映射见 发布信息.txt；"
                   "快手/头条也可改 --platforms 走 sau 自动发）"]

        fp = os.path.join(self.d["10_发布"], "发布核验报告.md")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("\n".join(report) + "\n")
        self.notify("📢 发布完成：\n" + "\n".join(report[1:]) +
                    f"\n报告: {fp}\n（B站 BV 核验/抖音状态复核详见报告；确认门闭环结束）"
                    + (f"\n质量台账: {self.report_summary()}" if self.report_summary() else ""))
        # #195：s13 收尾一行结构化打点（阶段/耗时/产物路径/各平台结果）。放在现有
        # 输出之后，不插队、不改格式；日志被 tail 时这行永远在最后。
        log(telemetry_line({
            "stage": "s13",
            "title": "发布+核验",
            "seconds": round(time.time() - t0, 1),
            "artifacts": {"video": video, "report": fp, "pending_dir": pending},
            "platforms": results,
        }))

    # ── 主流程 ─────────────────────────────────────────────────────────
    def plan(self):
        stages = self._stage_list()
        lines = ["执行计划："]
        for no, title, fn, env, gpu in stages:
            st = "SKIP(stamps)" if self.done(no) else ("RUN(strata)" if env else ("RUN(gpu)" if gpu else "RUN"))
            lines.append(f"  {st:<12} {no:<4} {title}")
        lines.append(f"  确认门: ④+⑤ 成片+封面合并审核·审一次（进程内轮询，timeout={self.args.gate_timeout}s）")
        lines.append(f"  车道: {self.lane} | 平台: {self.platforms} + 视频号待发 | 风格: {self.args.style} | BGM: {self.args.bgm or '无'}")
        return "\n".join(lines)

    def _stage_list(self):
        # 双车道（#59）：compressed = s04 单阶段 Strata + s05/s06/s07 虚拟 stamp；
        # legacy = 原 v3 四阶段链（#86 起四阶段端点同切 Strata，结构不动）。
        # 阶段号 s01-s13 顺序两车道一致（断点续跑/外部查询/飞书进度依赖）。
        legacy_s04 = ("s04", "导演（方向卡自动选+整体+逐段，Strata）", self.s04_director, "strata", False)
        compressed_s04 = ("s04", "设计+H3 prompt（Strata 压缩车道，单次调用）",
                          self.s04_design_h3, None, False)
        head = compressed_s04 if self.lane == "compressed" else legacy_s04
        if self.lane == "compressed":
            mid = [("s05", "虚拟 stamp（压缩车道已并入 s04）", self._noop_virtual, None, False),
                   ("s06", "虚拟 stamp（压缩车道已并入 s04）", self._noop_virtual, None, False),
                   ("s07", "虚拟 stamp（压缩车道已并入 s04）", self._noop_virtual, None, False)]
        else:
            mid = [("s05", "摄影师+七项自检（Strata）", self.s05_photographer, "strata", True),
                   ("s06", "美术画质（Strata）", self.s06_art, "strata", True),
                   ("s07", "H3 prompt（Strata）", self.s07_h3prompt, "strata", True)]
        return [
            ("s01", "分段+口播台词", self.s01_segment, None, False),
            ("s02", "TTS 音频先行（欢迎词+正文，GPU）", self.s02_tts, None, False),
            ("s03", "TTS 时长回填", self.s03_backfill, None, False),
            head,
            *mid,
            ("s08", "H3 生成+抽帧竖屏检查（GPU，whisper 并行）", self.s08_h3, None, False),
            ("s09", "读报装配 compose（GPU 后期）", self.s09_compose, None, False),
            ("s10", "字幕+烧录→最终成片", self.s10_subs_burn, None, False),
            ("s11", "Krea2 封面（GPU）", self.s11_cover, None, False),
            ("s12", "确认门④+⑤ 成片+封面合并审核（审一次）", self.s_gate_review, None, False),
            ("s13", "发布+核验（B站→抖音+视频号待发）", self.s13_publish, None, False),
        ]

    def run(self):
        t0 = time.time()
        # 心跳先起（#146）：从这一秒起 --status 就能看到「运行中」而不是「已崩」；
        # 守护线程每 DRIVER_HEARTBEAT_SEC 秒推进 ts，进程被 kill -9 时心跳自然停摆，
        # 查询侧靠「pid 不在 + 心跳过期」判已崩。
        self.hb.start()
        self.notify(f"🚗 driver 启动（车道: {self.lane}）\nslug: {self.slug}"
                    f"\n日期: {self.date} | 风格: {self.args.style} | 平台: {self.platforms}"
                    + (f"\nstop-after: {self.args.stop_after}" if self.args.stop_after else ""))
        try:
            for no, title, fn, env, gpu in self._stage_list():
                if self.args.dry_run:
                    continue
                self.run_stage(no, title, fn, env=self._strata_env() if env == "strata" else None)
                if self.args.stop_after and no == self.args.stop_after:
                    log(f"--stop-after {no}：到此为止（stamp 已写，续跑接得上）")
                    # 半程停止不是崩溃：留退出记录，--status 才会说「上次 --stop-after
                    # 停在 sXX」而不是含糊报「已崩」
                    self.hb.exit(0, f"--stop-after {no} 半程停止")
                    return
        finally:
            self.ledger.flush()
        summary = self.report_summary()
        log(f"全流程完成，用时 {(time.time()-t0)/60:.1f} min" + (f"\n质量摘要: {summary}" if summary else ""))
        # 流水线本体到此结束（s13 stamp 已落）；发布/清卡是收尾钩子，先记退出，
        # 免得收尾钩子卡住时 --status 把已完成的一单显示成还在跑。
        self.hb.exit(0, "全流程完成")
        # 视频号自动发布（2026-09-29 用户拍板"视频完成后可以直接发布"）：driver
        # 不再停在待发文件夹——WMI 拉起 v5 发布脚本（Windows 侧独立进程，脱离
        # interop job；登录态有效则静默续发，过期则飞书叫人扫码+守望续跑）。
        # 幂等：发布成功标记存在即跳过，防重启 driver 重复发。
        if not self.args.dry_run:
            try:
                import comfyui_selfheal
                import dsh_judge as dj
                marker = os.path.join(self.d["10_发布"], "视频号发布完成.json")
                # 发布器与本 driver 同仓（#152）：从脚本自身位置派生，不写死任何
                # 安装副本——driver 从哪份 checkout 跑起，就用那份的发布器。
                v5_local = os.path.join(HERE, "sph_publish_v5.py")
                # 存在性判定用本机口径（WSL 下 /mnt/c/…，Windows 下盘符口径），
                # 拉起 Windows 进程则用盘符口径；转换走 to_win_path，不再手写两份。
                v5_win = dj.to_win_path(v5_local)
                if os.path.exists(marker):
                    log("[sph] 视频号发布标记已在，跳过自动发布（已发过）")
                elif os.path.isfile(v5_local):
                    tag = time.strftime("%Y%m%d%H%M%S")
                    logp = os.path.join(os.environ.get("DRIVER_DATE_ROOT", DATE_ROOT),
                                        "logs", f"sph-{self.date}-{tag}.log")
                    py_win = os.environ.get("DRIVER_WIN_PYTHON",
                                            r"C:\Users\LMR\anaconda3\python.exe")  # noqa: mechanical
                    cmd = (r'cmd /s /c ""' + py_win + r'" '
                           + f'"{v5_win}" {self.date} > "{logp}" 2>&1"')
                    if comfyui_selfheal.launch_detached(cmd):
                        log(f"[sph] 视频号自动发布已拉起（WMI 独立进程）：{logp}")
                        self.notify("📤 视频号自动发布已启动（driver 完成钩子）；"
                                    "登录态有效则自动发，过期会叫人扫码")
                else:
                    log("[sph] 未发现 v5 发布脚本，视频号维持待发文件夹")
            except Exception as e:  # noqa: BLE001 — 自动发布失败不影响已交付
                log(f"[sph] 视频号自动发布拉起异常（忽略）: {e}")
        # 收尾：清理硬件占用（ComfyUI 下电还卡——2026-09-28 用户约定：流程最后
        # 执行完毕必须清，否则僵尸/缓存占 ~13GB 连累次日 9:00 报纸任务）
        if not self.args.dry_run:
            try:
                import comfyui_selfheal
                sd = comfyui_selfheal.shutdown_comfyui(notify=self.notify)
                log(f"[comfy-heal] 收尾清卡: {sd.get('outcome')}"
                    f" (vram_after={sd.get('vram_after')} MiB)")
            except Exception as e:  # noqa: BLE001 — 收尾清理失败不影响已交付内容
                log(f"[comfy-heal] 收尾清理异常（忽略）: {e}")


_JEV_CALIB_PATH = os.path.join(HERE, "..", "references", "jev-calibration.json")


def _load_jev_calibration(path=None):
    """Jev 校准参数（T8 #117）→ (temperature, calibrated, reason)。
    门控红线：文件缺失/损坏/T 越界/校准模型 ≠ 当前判读模型 → calibrated=False
    （置信度只随台账记录，不参与门控——未校准 softmax 虚高）。"""
    fp = path or _JEV_CALIB_PATH
    try:
        j = json.load(open(fp, encoding="utf-8"))
        t = float(j["temperature"])
        if not 0.0 < t <= 100.0:
            return 1.0, False, f"校准 T 越界: {t}"
        if j.get("model") and VISION_MODEL not in (j["model"],):
            return t, False, f"校准模型 {j['model']} ≠ 当前 {VISION_MODEL}"
        return t, True, "ok"
    except Exception as e:  # noqa: BLE001
        return 1.0, False, f"校准参数不可用: {str(e)[:60]}（跑 jev_calibrate.py 产出）"


def _conf_tail(vconf, form):
    """日志尾注：Jev 形态附带置信度（门控状态由入口日志说明，不逐段重复）。"""
    return f"（置信度 {vconf:.2f}）" if (form == "jev" and vconf is not None) else ""


def _gate_notes(judge_failed, tilted, clip_escalated, gate=None):
    """确认门④点名文案（纯函数，T4 #106 三张名单 + T8 #117 置信度门控三级可见）。
    gate = {"auto": 段数, "spot": [sid:conf], "low": [sid:conf]}；
    None/空 = 门控未生效（JSON 形态或未校准），不占文案。"""
    unjudged = ("\n⚠️ 以下段 vision 判读失败未经机检，请重点核对：" + str(judge_failed)
                if judge_failed else "")
    tilted_note = ("\n⚠️ 以下段机检判倾斜（可能为刻意构图，请人审定夺）：" + str(tilted)
                   if tilted else "")
    clip = ("\n⚠️ 以下段 CLIP 兜底层与 VLM 结论分歧（宁误报不漏报，请重点核对）：" + str(clip_escalated)
            if clip_escalated else "")
    gate_note = ""
    if gate and (gate.get("low") or gate.get("spot") or gate.get("auto")):
        gate_note = ("\n⚠️ 置信度门控（校准后三级，reconcile 门控）："
                     f"低置信(<{GATE_LO:.2f})点名 {gate.get('low') or '[]'}；"
                     f"抽检({GATE_LO:.2f}-{GATE_HI:.2f}) {gate.get('spot') or '[]'}；"
                     f"其余 {gate.get('auto', 0)} 段 ≥{GATE_HI:.2f} 自动过")
    return unjudged, tilted_note, clip, gate_note


def urllib_req(body):
    """vision 判读调用（openai 兼容 chat/completions）。T2 #104 起缺省指本地
    llama-server（VISION_API_DEFAULT = vision_common.default_api()）；云端 9router
    保留为显式 env 回退（DRIVER_VISION_API 一条切回）。模型名与备选轮询逻辑不变
    （VISION_MODEL / _BACKUP）。
    返回解析后的 dict；9router 会在 JSON 后粘 `data: [DONE]`，需剥离。"""
    import urllib.request
    api = os.environ.get("DRIVER_VISION_API", VISION_API_DEFAULT)
    req = urllib.request.Request(api, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw.split("data: [DONE]")[0].strip())


def _preflight_notify(text):
    """预检期的飞书通报：尽力而为，凭证缺失只落日志。"""
    try:
        from feishu_gate import FeishuGate
        FeishuGate().send_text(text)
    except Exception as e:  # noqa: BLE001
        log(f"[notify] 发送失败（忽略）: {e}")


def host_banner():
    """首行横幅（#145 AC1）：宿主 / 解释器绝对路径 / Python 版本，与 run_tests.header_line 同口径。"""
    exe = os.path.abspath(sys.executable) if sys.executable else "(unknown)"
    print("宿主=%s 解释器=%s 版本=%s" % (hostpath.detect_host(), exe, platform.python_version()), flush=True)


def ensure_host():
    """启动守卫（#145，声明源=契约文档，文档路径取 host_contract.DOC_REL）。

    实际宿主必须等于契约声明的 current_host（#151 起为 windows）：匹配打通过行；
    不匹配且 DRIVER_HOST_OVERRIDE 非空则打豁免警告放行；否则 stderr 三行原因 +
    文档位置，exit 2——不是 Python 堆栈。守卫落在 --status 分流之后（只读查询
    不受守卫约束，见 main()）。
    """
    host_banner()
    ok, detail = hostpath.host_matches_declaration()
    if ok:
        log("宿主守卫通过: %s" % detail)
        return
    override = os.environ.get("DRIVER_HOST_OVERRIDE", "").strip()
    if override:
        log(f"⚠️ 已豁免宿主守卫（DRIVER_HOST_OVERRIDE，非权威宿主）: {detail}")
        return
    import host_contract as hc
    print(
        f"ERROR: 宿主守卫失败（#145）：{detail}\n"
        f"       driver 必须跑在契约声明的宿主 {hc.current_host()!r} 上；"
        f"口径见 {hc.DOC_REL}。\n"
        f"       确要越权请设 env DRIVER_HOST_OVERRIDE=1（会打警告，不保证产物可用）。",
        file=sys.stderr, flush=True,
    )
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description="读报模式 driver（wayfinder #31）")
    # --script 由「必填」改为「除 --status 外必填」（#146）：--status 是只读查询，
    # 用 --script 或 --workdir 任一指定目标即可，缺目标在下面单独 fail-fast。
    ap.add_argument("--script", default=None, help="口播稿路径")
    ap.add_argument("--res", default="1080P")
    ap.add_argument("--style", default="chinese-celestial-palace", help="风格块（默认天宫）")
    ap.add_argument("--platforms", default="bilibili,douyin,kuaishou,toutiao")
    ap.add_argument("--gate-approved", action="store_true",
                    help="确认门已由用户在 agent 会话明确批准（跳过飞书轮询，不重发包）")
    ap.add_argument("--bgm", default=DEFAULT_BGM,
                    help=f"BGM 文件（默认 Documentary Discovery；传空串禁用）")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--lane", default=None, choices=list(LANES),
                    help="设计车道（默认 compressed=Strata 单次调用直出 H3 prompt；"
                         "legacy=v3 四阶段链。也可用 env DRIVER_LANE。"
                         "切换车道会自动清 s04-s07 stamps）")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stop-after", default=None, metavar="STAGE",
                    help="跑到指定阶段（含）后退出（如 --stop-after s07；测评/半程验收用，"
                         "正常生产不传）。该阶段照常写 stamp，续跑接得上")
    ap.add_argument("--gate-timeout", type=float, default=3600)
    ap.add_argument("--status", action="store_true",
                    help="只读查询本单状态（运行中/卡住/已崩/已完成）后退出："
                         "不改文件、不发通知、不重启；判定依据随结论一起打印（#146）")
    ap.add_argument("--stall-after", type=float, default=None, metavar="SEC",
                    help="「卡住」阈值秒数（默认 300；也可用 env "
                         "DRIVER_STATUS_STALL_SEC，CLI 优先）")
    ap.add_argument("--flag-shots", default=None, metavar="SPEC",
                    help="T17 人工指认（DSH 会话直接调用，不走全程）："
                         "\"shot03=翻转,shot05=正常\"——翻转者存证+重烧复判，"
                         "正常者只回填真值；处理后退出")
    args = ap.parse_args()

    # --status 必须最先分流（#146）：它只读——不能过 preflight（会发飞书叫人、会清
    # ComfyUI 显存）、也不能建 Driver（__init__ 会 makedirs 并在 --fresh 下删 stamp）。
    # 只读查询在这些副作用之前 return，才谈得上「不改任何文件」。
    if args.status:
        driver_status.run_cli(script=args.script, workdir=args.workdir,
                              stall_after=args.stall_after)
        return
    if not args.script:
        sys.exit("ERROR: 缺少 --script <口播稿.md路径>（--status 时可用 --workdir 代替）")

    # 启动守卫（#145）：首行横幅 + 宿主校验，实现见 ensure_host()（--status 已在上面分流）。
    ensure_host()

    if args.stop_after and args.stop_after not in {f"s{i:02d}" for i in range(1, 14)}:
        sys.exit(f"ERROR: --stop-after {args.stop_after!r} 不是合法阶段号（s01-s13）"
                 f"——拼错会静默跑全程，这里 fail-fast")

    # 环境预检（#46，CONTEXT.md「环境预检」词条）：启动即拦截环境故障——2026-09-21 期
    # CRLF 让 s05 连败退出时 s01-s04 已烧 45 分钟，护栏必须在烧钱前叫人。
    # 此时尚未建 Driver：stamps/台账必然未动，slug 目录也不会被创建。
    failures = preflight.check_static(HERE, preflight.COMFY_QUEUE_URL)
    # 车道在 preflight 前解析（失败也走标准叫人通道）。
    # #86：两车道文案链全走 Strata。#85 起启动预检**不再**硬性探活 Strata——
    # s01-s03（分段/TTS）不依赖 LLM，Strata 在线性由 s04 入口的 ensure_online
    # 自愈阶梯接管（离线自动跨界拉起，失败才叫人）；预检拦死会把「Strata 关机」
    # 变成无人值守场景的假故障（无人值守演示 AC：杀 Strata 后 driver 须自愈到底）。
    lane = resolve_lane(args.lane, os.environ.get("DRIVER_LANE"))
    if failures and args.dry_run:
        for f in failures:
            log(f"⚠️ 预检告警（dry-run 只看不拦）: [{f.kind}] {f.detail}")
    elif failures:
        msg = preflight.human_message(failures)
        log(msg)
        if any(f.kind == preflight.KIND_FEISHU_CREDS for f in failures):
            # 凭证缺失时飞书必然发不出（鸡生蛋），只落本地日志
            log("⚠️ 飞书凭证缺失，本条叫人消息无法经飞书送达；修复项已全列上方，修好后重跑。")
        else:
            _preflight_notify(msg)
        # 收尾：预检失败退场前清硬件占用（假活 ComfyUI 会连累次日报纸任务）
        try:
            import comfyui_selfheal
            sd = comfyui_selfheal.shutdown_comfyui()
            if sd.get("killed"):
                log(f"[comfy-heal] 预检退场清卡: ComfyUI 假活进程已清"
                    f" (vram_after={sd.get('vram_after')} MiB)")
        except Exception as e:  # noqa: BLE001
            log(f"[comfy-heal] 预检退场清理异常（忽略）: {e}")
        sys.exit(3)

    drv = Driver(args)
    if getattr(args, "flag_shots", None):
        # T17 #129 CLI 指认：真值回填+存证+（翻转者）重烧复判，处理后即退出，不进主流程
        drv.run_flag_cli(args.flag_shots)
        return
    if args.fresh:
        for f in glob.glob(os.path.join(drv.stamps_dir, "stage_*.json")):
            os.remove(f)
        log("--fresh: stamps 已清空")
    if args.dry_run:
        print(drv.plan())
        return
    drv.run()


if __name__ == "__main__":
    main()
