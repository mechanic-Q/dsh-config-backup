#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vision_common.py — 本地判读视觉链共享契约（#101/#102）。

单一事实源：判读 prompt / verdict 解析 / 端点缺省解析 / 送判图缩图预算。
消费者：run_pipeline 判读面（T2 起）、vision_smoke、vision_blindbench、
vision_server、（T4 起）clip_probe、（T8 起）jev_calibrate——改判读措辞
只改这里，盲比基准同步走。

协议：OpenAI 兼容 chat/completions + base64 image_url；本地 llama-server 与
云端 9router 同构，切换只动 env（DRIVER_VISION_API / DRIVER_VISION_MODEL）。

判读形态（T8 #117）：缺省 Jev 形态（候选词单字 + logprobs 投影出三分类分布），
DRIVER_VISION_MODE=json 显式回退 JSON 形态（云端端点不支持 logprobs 时用）。
"""
import json
import os
import sys

# #171：本文件允许包式导入（python -c "import scripts.vision_common"），
# 裸 import hostpath 只在脚本直跑时可用——与其余脚本一致，把本目录塞进
# sys.path，两种姿势都不炸。
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hostpath  # 宿主判定 / 跨侧路径 / 解码的单一事实源（#143）

# 端口约定（ADR 0007）：本地判读服务 llama-server Windows 侧 CPU 常驻 @8100，
# host 0.0.0.0（WSL driver 经网关 IP 访问，同 strata_h3_lane 口径）。
VISION_PORT = int(os.environ.get("VISION_JUDGE_PORT", "8100"))

# 判读 prompt 正文（形态无关部分；JSON/Jev 两形态只换输出指令——措辞改动
# 两形态同步生效，盲比/校准/司机判读面永远同口径）。
_JUDGE_BODY = (
    "这是一张竖屏（9:16）竖直构图视频的截图。按重力常识做三级检查：\n"
    "1) 翻转：人物横躺/柱子水平横放/天空出现在画面左侧或右侧 → 翻转\n"
    "2) 倾斜：画面整体或主体明显斜向（地平线、建筑中轴、人物纵轴 "
    "偏离水平/垂直 15° 以上，画面呈对角线构图）→ 倾斜\n"
    "3) 以上都没有（天空在上方、竖直结构竖直、地平线水平）→ 正常\n"
    "注意：画面内的斜线元素（斜拉的桥索、斜飘的衣带、透视产生的斜线）不算倾斜，"
    "只有整个构图/重力方向歪了才算。\n")

# JSON 形态（T2 #104~T7 现行）：判读面零改动的锚点，2026-09-05 单帧单问版 +
# 09-11 三级判定拍板输出指令，逐字保留。
JUDGE_PROMPT = _JUDGE_BODY + (
    "只输出 JSON，不要解释：{\"verdict\": \"正常\"} 或 {\"verdict\": \"倾斜\"} "
    "或 {\"verdict\": \"翻转\"}")

# ── Jev 形态（T8 #117：候选词 logits 投影）─────────────────────────────
# 红线（票面）：候选顺序固定（正常→倾斜→翻转）；候选池只放三类。
JEV_PROMPT = _JUDGE_BODY + "只答一个字：正（正常）、倾（倾斜）、翻（翻转）"
# 候选单字 → 三级 verdict 映射（下标顺序 = VERDICT_LEVELS，固定不得重排）
JEV_CANDIDATES = ("正", "倾", "翻")
# 单 token 出分布；logprobs 请求宽度过余量（10-14 实测：三候选稳定在 top-3，
# 拒答变体（'正常'/'倒'等）也在 top-20 内，榜尾即界外——界外候选按低概率地板处理）
JEV_MAX_TOKENS = 1
JEV_TOP_LOGPROBS = 20

# 判读形态 env（T8 AC：显式回退 JSON 形态）。jev（缺省）| json。
# json 形态行为与 T7 及之前完全一致（无置信度、无门控）。
MODE_ENV = "DRIVER_VISION_MODE"


def judge_mode():
    """判读形态解析：DRIVER_VISION_MODE（jev 缺省/json 回退），非法值视作 jev
    并留痕语义由调用方日志承担（此处只做归一）。"""
    m = (os.environ.get(MODE_ENV) or "jev").strip().lower()
    return "json" if m == "json" else "jev"

# max_tokens 预算（0914 实证教训：reasoning 模型会给 reasoning_tokens 吃光预算
# → content 空串，glm-4.6v 时代为此给 600）。T2 #104 复核（10-14 实测）：
# Qwen3-VL 非 reasoning，verdict JSON 实测 8-15 completion tokens（服务端
# print_timing），200 = 10 倍余量；env 可覆写
JUDGE_MAX_TOKENS = int(os.environ.get("DRIVER_VISION_MAX_TOKENS", "200"))

# 三级 verdict 契约（判读/盲比/CLIP/reconcile 共用；judge_fail 是留痕态非判定态）
VERDICT_LEVELS = ("正常", "倾斜", "翻转")

# 判官四标签契约（T14 #126：第二意见判官的独立词表，dsh_judge 消费）。
# 与 VERDICT_LEVELS 不同源不同粒度：翻转90/翻转180 是判官对本地「翻转」的细分；
# merge 只按 翻/非翻 对齐（is_flip）。改词表两处一起评审。
JUDGE_LEVELS = ("正常", "翻转90", "翻转180", "倾斜")

# 定档模型缺省名（llama-server --alias；盲比报告产出后同源更新）
DEFAULT_MODEL = os.environ.get("DRIVER_VISION_MODEL", "qwen3-vl-4b")

# 送判图长边上限（10-14 实测：1080×1920 原生分辨率喂 Qwen3-VL ≈1800 图 token，
# CPU prefill 16s/帧（服务端 prompt eval 2046 tok @128 tok/s）；896 长边 token
# 降 ~4.6 倍且朝向粗判不受影响——判读是天空/地平线方向判断，不是细粒度 OCR。
# 0/负值 = 原图直送。pitfalls #55）
IMG_MAX_SIDE = int(os.environ.get("DRIVER_VISION_IMG_MAX_SIDE", "896"))


def norm_verdict(raw):
    """判读分类口径（与 run_pipeline 判读面同一套字串规则，#106 起单一事实源）：
    翻/rotat→翻转，倾/tilt→倾斜，其余→正常。"""
    v = str(raw or "")
    if "翻" in v or "rotat" in v.lower():
        return "翻转"
    if "倾" in v or "tilt" in v.lower():
        return "倾斜"
    return "正常"


def extract_verdict(content):
    """响应 content → verdict 串。解析失败抛异常（与 run_pipeline 判读面
    同语义：异常 → 重试/留痕，不静默放行）。"""
    txt = str(content)
    j = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    return str(j.get("verdict", ""))


def image_b64(path, max_side=None):
    """图文件 → base64。max_side（缺省 IMG_MAX_SIDE）给定时先把长边缩到该值
    再转 JPEG——缩图失败（PIL 缺失/坏图）原样回退，绝不因预处理炸判读链。"""
    import base64
    data = open(path, "rb").read()
    ms = IMG_MAX_SIDE if max_side is None else max_side
    if ms and ms > 0:
        try:
            import io
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            im.load()
            if max(im.size) > ms:
                r = ms / max(im.size)
                im = im.convert("RGB").resize(
                    (max(1, int(im.width * r)), max(1, int(im.height * r))))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=88)
                data = buf.getvalue()
        except Exception:  # noqa: BLE001 — 预处理失败原图直送
            pass
    return base64.b64encode(data).decode()


def post_chat(api, body, timeout=180):
    """POST chat/completions → 解析 dict。9router 会在 JSON 后粘 `data: [DONE]`
    需剥离；本地 llama-server 无此尾巴，split 兼容两种。HTTP/网络异常向上抛
    （重试语义归调用方——判读面/工具各自的重试策略不同，不在此收编）。"""
    import urllib.request
    req = urllib.request.Request(api, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw.split("data: [DONE]")[0].strip())


def judge_body(model, image_ref, max_tokens=None):
    """JSON 形态判读请求体（与 run_pipeline._check_frames 同构：单帧单问 + base64）。
    image_ref = 图路径（自动走 image_b64 缩图预算）或现成 base64 串。
    temperature=0：判读是三级分类，采样噪声直接变判定抖动（10-14 实测同帧
    同模型两次判读 正常/翻转 分歧——llama-server 缺省 temp 1.0）。"""
    b64 = image_b64(image_ref) if os.path.exists(str(image_ref)) else str(image_ref)
    return json.dumps({"model": model, "temperature": 0,
                       "max_tokens": max_tokens or JUDGE_MAX_TOKENS,
                       "messages": [{"role": "user", "content": [
                           {"type": "text", "text": JUDGE_PROMPT},
                           {"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]},
                      ensure_ascii=False).encode()


def jev_body(model, image_ref):
    """Jev 形态判读请求体：候选词单字 + 单 token + logprobs（一次 prefill 出
    三分类分布，省 JSON 形态的整段 decode）。top_logprobs 宽度给足——榜尾即
    分辨率下界，界外候选按低概率地板处理（见 jev_project）。"""
    b64 = image_b64(image_ref) if os.path.exists(str(image_ref)) else str(image_ref)
    return json.dumps({"model": model, "temperature": 0,
                       "max_tokens": JEV_MAX_TOKENS,
                       "logprobs": True, "top_logprobs": JEV_TOP_LOGPROBS,
                       "messages": [{"role": "user", "content": [
                           {"type": "text", "text": JEV_PROMPT},
                           {"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]},
                      ensure_ascii=False).encode()


def jev_project(resp):
    """chat/completions 响应 dict → 三候选 logprobs（logits 语义，VERDICT 顺序
    [正, 倾, 翻] 对应 z 值）。仅取 top_logprobs 里与 JEV_CANDIDATES 精确相等的
    单字 token；榜外候选按「榜尾 logprob - 10」地板（真值在榜外 = 质量 < 榜尾
    的 e^-10，排序不受影响）。三候选全部缺席 = 模型拒答/端点不支持，抛
    ValueError（调用方按判读失败重试/留痕，err 自带回退指引）。

    10-14 实测（llama-server b11603 + Qwen3-VL-4B）：top_logprobs 是预采样
    分布（temperature 0/1 上报值逐位一致、非 one-hot），z 值跨调用稳定 ±0.2。
    """
    try:
        lp = resp["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("响应无 logprobs（端点不支持；DRIVER_VISION_MODE=json 可回退）")
    tops = {str(t.get("token")): float(t.get("logprob") or 0.0) for t in lp}
    found = [tops.get(c) for c in JEV_CANDIDATES]
    if all(v is None for v in found):
        raise ValueError(f"top_logprobs 无候选词（拒答/形态破坏）: {list(tops)[:5]}")
    floor = min(v for v in found if v is not None) - 10.0
    return [v if v is not None else floor for v in found]


def softmax3(z3, temperature=1.0):
    """三候选 logits → softmax 概率（jev_decide / jev_calibrate 共用的唯一实现）。"""
    import math
    t = max(float(temperature), 1e-6)
    m = max(z3)
    e = [math.exp((v - m) / t) for v in z3]
    s = sum(e)
    return [v / s for v in e]


def jev_decide(z3, temperature=1.0):
    """三候选 logits → (verdict, 置信度)。softmax(z/T) 后取 argmax；
    temperature 来自校准（jev_calibrate 拟合，未校准 = 1.0 原始 softmax——
    门控红线：未校准置信度不得参与门控，只记录）。"""
    probs = softmax3(z3, temperature)
    i = max(range(len(probs)), key=lambda k: probs[k])  # 平票取先 = 固定顺序红线
    return VERDICT_LEVELS[i], probs[i]


def _in_wsl():
    """WSL 判定（#143 起委托 hostpath 单一实现；保留本名供旧 import 与 patch）。"""
    return hostpath.is_wsl()


def wsl_gateway():
    """WSL→Windows 主机 IP（默认路由网关，spike #83 §1 定案连通方式）。"""
    if not _in_wsl():
        return None
    try:
        with open("/proc/net/route", encoding="ascii") as f:
            for line in f:
                cols = line.split()
                if len(cols) > 2 and cols[1] == "00000000":
                    gw = cols[2]
                    return ".".join(str(int(gw[i:i + 2], 16))
                                    for i in (6, 4, 2, 0))
    except OSError:
        pass
    return None


def default_api():
    """本地判读端点缺省：WSL 内经网关连 Windows 主机，否则 127.0.0.1。
    （T2 起 run_pipeline.urllib_req 缺省改指此端点；9router 留显式 env 回退。）"""
    host = wsl_gateway() or "127.0.0.1"
    return f"http://{host}:{VISION_PORT}/v1/chat/completions"
