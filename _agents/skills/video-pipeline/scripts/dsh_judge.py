#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh_judge.py — 第二意见判官（DSH 本地 agent）客户端（T14，#126）。

S8 判读的复判层：本地 VLM（llama-server，三分类 正常/倾斜/翻转）先判一次，本模块把
它的判定与定罪帧交给 DSH 跑一次 headless 任务，让 DSH 用 read_image 独立再看一眼，
回答两件事：
  1) judge     —— 判官自己的独立判定（正常 / 翻转90 / 翻转180 / 倾斜）
  2) local_ok  —— 本地 VLM 判得对不对（翻转/非翻转结论与判官是否一致）

driver 的重烧口径（2026-10-16 父票 #132 拍板：重烧名单以判官为准）：
  · 本地判翻转 且 判官判翻转 → 确认翻转，重烧（正类素材）
  · 本地判正常 且 判官判翻转 → 本地漏报，重烧（强负类/漏报素材）
  · 本地判翻转 且 判官判正常 → 本地误报，只记账不重烧（假阳性素材）
  · 判官不可用/超时/解析失败 → fail-open（票 #132 拍板：复判不阻塞生产）：
    采信本地判读，只记 error

运行位置（跨端铁律）：driver 跑在 WSL，DSH 跑在 Windows。本模块经 WSL interop 调
Windows 侧 dsh CLI，故：
  · argv 用 WSL 口径的 EXE（/mnt/e/...），CLI 路径用 Windows 口径（app.asar 内）；
  · 写进 prompt 的帧路径必须是 Windows 口径 —— 判官进程在 Windows 侧，看不到
    /mnt/e/... 这类路径，用 to_win_path() 转换；
  · subprocess 一律显式 encoding=utf-8 + errors=replace（PR #124 铁律）。

调用形态（实测：19 段一次 34.0s / in 13919 tok / out 4076 tok）：
  env ELECTRON_RUN_AS_NODE=1
  argv [EXE, --expose-internals, CLI, --profile, PROFILE, headless, --json, prompt]
  stdout 为换行分隔 run event：type=final 的 text 是最终回答，status 带 usage 计费。

判官运行体（profile）随仓进件：dsh-profile/judgeprobe/（cordis.patch.yml 指定
agent-default-model），按 README 装到 ~/.dsh/profiles/ 即可复现本通道。

CLI（验收⑤：一条命令查可用性 + 运行配置 + 模型）:
  dsh_judge.py status      # 判官可用性自检（不动模型）
  dsh_judge.py selftest    # 真跑一次两字符问答，验通道
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NL = chr(10)
WIN_SEP = chr(92)

# 宿主判定 / 跨侧路径转换 / 子进程解码的单一事实源（#143；本文件只留一行委托）
import hostpath  # noqa: E402 — 必须在 sys.path.insert 之后


def win_join(*parts):
    """拼 Windows 口径路径（源内不出现反斜杠字面量，避开跨层转义）。"""
    return WIN_SEP.join(str(p).strip(WIN_SEP) for p in parts if str(p))


def log(msg, dry=False):
    """与 driver 的 [clip]/[vision] 同款前缀行，便于日志检索。"""
    mark = " dry" if dry else ""
    print(f"[judge{mark}] {msg}", flush=True)


def _env_float(name, default):
    """env 浮点读取：空/垃圾值回落 default，不炸导入（判官配置错不该炸 driver）。"""
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        log("env %s=%r 不是数字，回落 %.1fs" % (name, os.environ.get(name), default))
        return default


# ── 判官运行体（DSH 桌面版）双端路径 ─────────────────────────────────────
# 缺省按本仓装机实测口径；换机用 env 覆盖，不必改源码（#126 评审定案）。
EXE_WIN = os.environ.get("DRIVER_JUDGE_EXE_WIN") or win_join(
    "E:", "AI", "deepseek-harness", "DeepSeek Harness.exe")
EXE_WSL = os.environ.get("DRIVER_JUDGE_EXE_WSL") or "/mnt/e/AI/deepseek-harness/DeepSeek Harness.exe"
CLI_WIN = os.environ.get("DRIVER_JUDGE_CLI") or win_join(
    "E:", "AI", "deepseek-harness", "resources", "app.asar", "dsh",
    "node_modules", "@deepseek-ai", "dsh-desktop-host", "lib", "cli.js")

PROFILE = os.environ.get("DRIVER_JUDGE_PROFILE") or "judgeprobe"
TIMEOUT = _env_float("DRIVER_JUDGE_TIMEOUT", 900.0)
ENABLE_ENV = "DRIVER_JUDGE"   # 0/false/no 关闭判官层：单路本地，行为回退到 T12 前
JUDGE_MODEL = os.environ.get("DRIVER_JUDGE_MODEL") or "glm-5.3-flash"

# 判官四标签契约单一源在 vision_common.JUDGE_LEVELS（#126 评审定案：不在本文件自立词表）
from vision_common import JUDGE_LEVELS as _JUDGE_LEVELS
LABEL_OK, LABEL_ROT90, LABEL_ROT180, LABEL_TILT = _JUDGE_LEVELS


def _in_wsl():
    """WSL 判定（#143 起委托 hostpath 单一实现；保留本名供测试与旧调用点 patch）。"""
    return hostpath.is_wsl()


def exe_path():
    return EXE_WSL if _in_wsl() else EXE_WIN


def enabled():
    return (os.environ.get(ENABLE_ENV) or "1").strip() not in ("0", "false", "no", "")


def _profile_model():
    """从仓内判官 profile 的 cordis.patch.yml 读真实 agent-default-model。
    读不到返回 None（status 回落 env 显示名 DRIVER_JUDGE_MODEL）。"""
    try:
        with open(os.path.join(HERE, os.pardir, "dsh-profile", PROFILE,
                               "cordis.patch.yml"), encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("model:"):
                    return s.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def available():
    """判官可用性（不动模型）：只查运行体在盘。CLI 在 app.asar 内，纯 python 查不到，
    交给真实调用报错。返回 (bool, 原因)。模型名优先取 profile 实配，env 只作显示回落。"""
    if not enabled():
        return False, f"{ENABLE_ENV}=0（判官层关闭）"
    exe = exe_path()
    if not os.path.exists(exe):
        return False, f"判官运行体缺失: {exe}"
    pm = _profile_model()
    model = pm if pm else JUDGE_MODEL
    src = "profile" if pm else "env"
    return True, f"profile={PROFILE} model={model} ({src})"


def to_win_path(p):
    """WSL 口径路径 → Windows 口径（判官 read_image 用）；已是 Windows 口径的原样返回。"""
    return hostpath.to_win_path(p)


def to_host_path(p):
    """Windows 口径路径 → 本机口径（WSL 换回 /mnt/<盘>/…；本机口径原样返回）。

    to_win_path 的反向：判官侧/manifest 存的是 Windows 口径，本地读盘
    （os.path.exists / PIL / 复制）之前先换回宿主口径（#140）。
    """
    return hostpath.to_host_path(p)


def judge_cmd(prompt):
    """判官 argv（测试与真实调用共用同一形态）。"""
    return [exe_path(), "--expose-internals", CLI_WIN, "--profile", PROFILE,
            "headless", "--json", prompt]


def judge_env():
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    return env


# ── 判定归一 ─────────────────────────────────────────────────────────────
def norm_judge(v):
    """判官自由文本 → 正常 / 翻转90 / 翻转180 / 倾斜。

    「倾/斜/tilt」先于「90」判断：判官答「倾斜90度」是倾斜不是翻转90。
    """
    t = (v or "").strip().lower()
    if "倾" in t or "tilt" in t:
        return LABEL_TILT
    if "180" in t or "倒" in t or "upside" in t:
        return LABEL_ROT180
    if "90" in t or "横" in t or "侧" in t:
        return LABEL_ROT90
    if "翻" in t or "rotat" in t:
        return LABEL_ROT90
    return LABEL_OK


def is_flip(label):
    """该判定是否属于「翻转」大类：判官细分（翻转90/翻转180）与本地三分类（翻转）。"""
    # 显式集合成员判断，不靠 '翻' 字子串巧合（#126 评审定案）。
    return (label or "").strip() in {LABEL_ROT90, LABEL_ROT180, "翻转"}


def build_prompt(rows):
    """rows: [{sid, verdict(本地判定), conf, frame(任意口径路径)}] → 判官提示词。

    形态沿用实测跑通的模板（19 段 34s）：先声明第二意见身份，再列本地判定，再要求
    逐段 read_image 独立判定，最后只要一个 JSON 数组。
    """
    out = [
        "你是视频分镜质检复核员（第二意见）。本地 VLM 已先判过一轮，你要复核它判得对不对。",
        "逐段用 read_image 工具读取下面列出的原始帧（竖屏 1080x1920，取自最终成片），",
        "独立判断每段画面主体是否被旋转。四种可能：正常（天地正立）／翻转90（横躺，",
        "天地在左右）／翻转180（倒立）／倾斜（整体斜向但未翻转）。注意人物可能很小、画面是云海/殿堂/军阵等风格化",
        "场景：以地平线、天空、建筑与地面的竖直方向为准，不要以人物朝向为准。",
        "",
        "本地 VLM 首轮判定（段号=判定(置信度)）：",
    ]
    for r in rows:
        conf = r.get("conf")
        cs = ("(%.2f)" % conf) if isinstance(conf, (int, float)) else "(无置信度)"
        out.append("%s=%s%s" % (r["sid"], r.get("verdict") or "未判", cs))
    out += ["", "请逐段核对下列原始帧（read_image 一张张读，用给出的绝对路径，不要改写路径）："]
    for r in rows:
        out.append("%s: %s" % (r["sid"], to_win_path(r["frame"])))
    example = json.dumps([{"shot": "shot01", "judge": "正常|翻转90|翻转180|倾斜",
                           "local_ok": True, "conf": 0.95, "why": "20字内"}],
                          ensure_ascii=False)
    out += [
        "",
        "只输出一个 JSON 数组，不要输出其它任何文字，共 %d 个元素：" % len(rows),
        example,
        "judge 填你的独立判定；local_ok 填本地 VLM 判得对不对（它的翻转/非翻转结论与",
        "你的判定是否一致，一致 true 否则 false）；why 用 20 字内说明依据。",
    ]
    return NL.join(out)


def parse_final(final, expect=()):
    """判官最终回答 → items 列表。容忍围栏与前后杂字；失败抛 ValueError。

    返回 [{sid, judge(归一标签), conf, local_ok, why}]；expect 非空时缺段即失败
    （漏判是通道故障而不是无声降级）。
    """
    t = (final or "").strip()
    a, b = t.find("["), t.rfind("]")
    if a < 0 or b <= a:
        raise ValueError("判官回答里找不到 JSON 数组: %r" % t[:160])
    items = json.loads(t[a:b + 1])
    if not isinstance(items, list):
        raise ValueError("判官回答不是 JSON 数组")
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("shot") or it.get("sid") or "").strip()
        if not sid:
            continue
        m = re.match("^shot0*([0-9]+)$", sid, re.I)
        if m:
            sid = "shot%02d" % int(m.group(1))
        label = norm_judge(it.get("judge") or it.get("verdict"))
        try:
            conf = round(float(it.get("conf")), 4)
        except (TypeError, ValueError):
            conf = None
        out.append({"sid": sid, "judge": label, "conf": conf,
                    "local_ok": it.get("local_ok"), "why": str(it.get("why") or "")[:60]})
        seen.add(sid)
    if not out:
        raise ValueError("判官回答解析出 0 条")
    missing = [s for s in expect if s not in seen]
    if missing:
        raise ValueError("判官漏判 %d 段: %s" % (len(missing), missing[:6]))
    return out


def _collect_events(stdout, sink):
    """解析 dsh 事件流：final 文本返回，usage/error 经 sink 累积到调用方状态。

    judge() 与 _run_channel() 共用 —— 两处各自手写解析必然漂移（#126 评审定案）。
    """
    final, err_ev = "", ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        typ = ev.get("type")
        if typ == "final":
            final = ev.get("text") or ""
        elif typ in ("status", "step_end"):
            u = ev.get("usage") or {}
            sink["in_tok"] += int(u.get("inputTokens") or 0)
            sink["out_tok"] += int(u.get("outputTokens") or 0)
            sink["reasoning_tok"] += int(u.get("reasoningTokens") or 0)
        elif typ in ("error", "turn_end"):
            rk = ev.get("reason") if typ == "turn_end" else ev
            if isinstance(rk, dict) and rk.get("kind") == "error":
                err_ev = str((rk.get("error") or {}).get("message") or rk)[:200]
    return final, err_ev


def judge(rows, timeout=None, dry=False, expect=None):
    """跑一次判官，返回 {ok, items, error, wall_s, in_tok, out_tok, reasoning_tok, raw}。
    ok=False 时 items 空、error 有值 —— 调用方 fail-open（复判不阻塞生产），不炸主链。
    """
    base = {"items": [], "wall_s": 0.0, "in_tok": 0, "out_tok": 0,
            "reasoning_tok": 0, "raw": ""}
    ok, reason = available()
    if not ok:
        base.update({"ok": False, "error": reason})
        return base
    if not rows:
        base.update({"ok": False, "error": "无待判段"})
        return base
    if dry:
        log("dry：跳过判官真跑（%d 段）" % len(rows), dry=True)
        base.update({"ok": False, "error": "dry"})
        return base
    prompt = build_prompt(rows)
    t0 = time.time()
    try:
        r = subprocess.run(judge_cmd(prompt), capture_output=True, text=True,
                           timeout=timeout or TIMEOUT, env=judge_env(),
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        base.update({"ok": False, "wall_s": round(time.time() - t0, 1),
                     "error": "判官超时（%.0fs）" % (timeout or TIMEOUT)})
        return base
    except Exception as e:   # noqa: BLE001 — 通道故障一律 fail-open
        base.update({"ok": False, "wall_s": round(time.time() - t0, 1),
                     "error": "判官起不来: %s: %s" % (type(e).__name__, e)})
        return base
    base["wall_s"] = round(time.time() - t0, 1)
    final, err_ev = _collect_events(r.stdout, base)
    base["raw"] = (final or "")[:4000]
    if not final.strip():
        base.update({"ok": False, "error": err_ev or ("判官无输出（rc=%s）" % r.returncode)})
        return base
    try:
        exp = expect if expect is not None else [x["sid"] for x in rows]
        base.update({"ok": True, "items": parse_final(final, expect=exp), "error": ""})
    except Exception as e:   # noqa: BLE001 — 判官答非所问 = 本轮无第二意见
        base.update({"ok": False, "error": "判官回答解析失败: %s" % e})
    return base


# ── 与本地判读合成（#132 拍板：重烧名单以判官为准）──────────────────────
def merge(local, items, round_tag="judge0"):
    """本地判读 × 判官判定 → 差异清单。

    local: {sid: verdict}（本地三分类，缺 = 未判）；items: judge() 的 items。
    返回 {confirmed, judge_found, local_false, judge_normal, rows}：
      confirmed    本地翻 且 判官翻   → 判官确认（正类素材），重烧
      judge_found  本地非翻 且 判官翻 → 本地漏报（强负类素材），升级重烧
      local_false  本地翻 且 判官非翻 → 本地误报（假阳性素材），只记账不烧
      judge_normal 两边都非翻         → 一致，正常放行
    重烧名单 = confirmed + judge_found（判官为准）。
    """
    out = {"confirmed": [], "judge_found": [], "local_false": [], "judge_normal": [],
           "rows": []}
    for it in items or []:
        sid = it["sid"]
        lv = (local or {}).get(sid) or "未判"
        lf, jf = is_flip(lv), is_flip(it["judge"])
        if lf and jf:
            kind = "confirmed"
        elif jf:
            kind = "judge_found"
        elif lf:
            kind = "local_false"
        else:
            kind = "judge_normal"
        out[kind].append(sid)
        out["rows"].append({"sid": sid, "local": lv, "judge": it["judge"], "kind": kind,
                            "conf": it.get("conf"), "why": it.get("why", ""),
                            "round": round_tag})
    return out


def reburn_list(merged):
    """判官为准的重烧名单（confirmed + judge_found）。"""
    return sorted(set(merged["confirmed"]) | set(merged["judge_found"]))


def summarize(merged):
    """一行摘要（日志/台账用）。"""
    return ("确认翻转 %d / 判官新发现 %d / 本地误报 %d / 一致正常 %d"
            % (len(merged["confirmed"]), len(merged["judge_found"]),
               len(merged["local_false"]), len(merged["judge_normal"])))


# ── CLI ─────────────────────────────────────────────────────────────────
def _run_channel(prompt, timeout=300.0):
    """真跑一次判官通道（selftest 用），返回 (rc, final_text, wall_s)。

    任何通道故障（超时/起不来）返回失败元组而不抛异常 —— 自检本身就是可用性
    探针，探针先炸等于没检。
    """
    t0 = time.time()
    try:
        p = subprocess.run(judge_cmd(prompt), capture_output=True, text=True,
                           timeout=timeout, env=judge_env(),
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 1, "", round(time.time() - t0, 1)
    except Exception as e:   # noqa: BLE001 — 通道故障 = selftest 失败而非崩溃
        log("selftest 起不来: %s: %s" % (type(e).__name__, e))
        return 1, "", round(time.time() - t0, 1)
    txt, _err = _collect_events(p.stdout, {"in_tok": 0, "out_tok": 0, "reasoning_tok": 0})
    return p.returncode, txt, round(time.time() - t0, 1)


def main():
    arg = (sys.argv[1] if len(sys.argv) > 1 else "status").strip()
    if arg == "status":
        ok, reason = available()
        print("判官可用: %s —— %s" % (ok, reason))
        print("EXE: %s" % exe_path())
        print("CLI: %s" % CLI_WIN)
        return 0 if ok else 1
    if arg == "selftest":
        ok, reason = available()
        print("判官可用: %s —— %s" % (ok, reason))
        if not ok:
            return 1
        rc, txt, wall = _run_channel("回答两个字符：OK", timeout=300.0)
        print("rc=%s wall=%ss final=%r" % (rc, wall, txt.strip()[:80]))
        return 0 if rc == 0 and "OK" in txt else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())