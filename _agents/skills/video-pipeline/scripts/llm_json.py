#!/usr/bin/env python3
"""llm_json.py — LLM 调用 + JSON 强校验 + 流式自适应（S1+S2 核心）

替代各脚本重复的 llm_call。三个关键能力：
1. **流式接收 + 主动判停**（S2）：边收边拼，JSON 完整可解析即停（零浪费零截断）
2. **finish_reason 自适应**（S1）：若截断(length) → budget ×1.5 重试（封顶 HARD_CAP）
3. **schema 强校验 + 错误反馈重试**：不合格把错误喂回 LLM，不是容忍噪音

用法：
    from llm_json import llm_call_json
    obj = llm_call_json(system, user, DIRECTOR_SHOT_SCHEMA, model="strata")
    # obj = {"id": "shot01", "情绪基调": "建立...", ...} 已通过 schema 校验

端点与方言（#86 文案链全切本地 Strata，原 9router 云端缺省退役）：
- 缺省端点单一事实源 = strata_h3_lane（STRATA_URL env 优先；WSL 内自动经网关连
  Windows 主机，spike §1 定案），LLM_API env 可整体覆盖（driver 车道注入同款）
- 关思维链（流水线质量口径）= 请求级 chat_template_kwargs.enable_thinking=false
  （#83 spike §4 T1 实测唯一有效写法）；9router 专属 thinking 字段禁发（风格红线）
- LLM_THINKING=on 打开思维链（同一字段翻 true，llama.cpp jinja 同款方言）

实测（2026-09-01，9router 时代沿用至今）：
- streaming SSE 可用（289KB/1009块/含 data:[DONE]）
- deepseek-v4-flash 流式 delta 带 reasoning_content（首块），必须拼接兜底
- 导演设计 JSON 完整需 ~2500+ token，max_tokens 1500 会截断 → 自适应翻倍解决
"""
import json
import os
import re
import sys
import time
import urllib.request


def default_api():
    """Strata 缺省端点（#86）。单一事实源 = strata_h3_lane.BASE_URL（STRATA_URL env
    优先，WSL 内经网关动态解析）。只做纯解析：导入顺序无关——本模块常被
    strata_h3_lane 在其自身初始化中途反向 import，import 期取其模块属性会拿到
    半初始化对象，因此解析必须推迟到首用期（api()），不得在模块体缓存。"""
    try:
        import strata_h3_lane
        return strata_h3_lane.BASE_URL + "/v1/chat/completions"
    except Exception:  # noqa: BLE001 — 端点解析失败回退本机直连（STRATA_URL env 永远可覆盖）
        return "http://127.0.0.1:8080/v1/chat/completions"


def api():
    """文案链端点（每次调用期解析）：LLM_API env 优先（driver 车道注入），
    缺省指本地 Strata（#86 起；9router 云端缺省退役，视觉判读是唯一云端例外）。"""
    env = os.environ.get("LLM_API", "")
    return env or default_api()


# LLM_THINKING=on 打开思维链（chat_template_kwargs.enable_thinking=true，同一方言字段）；
# 缺省关（流水线质量口径，#29 结论）
THINKING_ON = os.environ.get("LLM_THINKING", "").lower() in ("on", "1", "true")

# token 台账（2026-09-07）：模块级累计，driver 落 report。线程安全。
import threading as _threading
_STATS_LOCK = _threading.Lock()
STATS = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def record_usage(usage):
    """把一次调用的 usage 累计进 STATS（openai 格式；无 usage 时静默跳过）。"""
    if not usage:
        return
    with _STATS_LOCK:
        STATS["calls"] += 1
        try:
            STATS["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            STATS["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            pass

# max_tokens 安全阀（任何场景足够；截断会触发自适应翻倍，最终封顶这里）
HARD_CAP = 8000
# 初始预算（按环节：导演设计~2500 / 摄影师~3000 / H3~4000；由调用方传合理起点）
DEFAULT_BUDGET = 3000


def _extract_json(text):
    """从 LLM 回复提取 JSON 对象（容忍 ```json 围栏 / 前后杂文 / 推理文本混入）。

    单趟括号配对：只在深度 0 处识别顶层对象边界，跳过字符串/转义。
    推理文本里的孤立花括号不干扰；嵌套内层对象不计入。
    返回最后一个可解析的顶层对象（推理在前、真实 JSON 在后）。
    """
    if not text:
        return None
    t = re.sub(r"```(?:json)?\s*", "", text).strip()
    best = None
    depth = 0
    in_str = False
    esc = False
    start = -1
    for i, c in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        best = json.loads(t[start:i + 1])
                    except Exception:
                        pass
                    start = -1
    return best


def _stream_once(system, user, model, budget):
    """流式调用一次，返回 (content, finish_reason, usage)。"""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.7,
        "max_tokens": budget,
        "stream": True,
        # 关思维链缺省（质量口径）：chat_template_kwargs.enable_thinking 是 Strata
        # （llama.cpp 系）唯一有效写法（#83 spike §4 T1）；9router 专属 thinking 字段禁发
        "chat_template_kwargs": {"enable_thinking": THINKING_ON},
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(api(), data=body,
                                 headers={"Content-Type": "application/json"})
    content_parts = []
    finish_reason = "stop"
    with urllib.request.urlopen(req, timeout=300) as r:
        buf = ""
        while True:
            chunk = r.read(4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="ignore")
            # 逐行解析 SSE data: 块
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return "".join(content_parts), finish_reason, None
                try:
                    evt = json.loads(payload)
                except Exception:
                    continue
                delta = evt.get("choices", [{}])[0].get("delta", {})
                # THINKING_ON: reasoning_content = 思维链，丢弃，只收正文
                # 否则（缺省关思维链）: reasoning_content 是正文兜底，保留（旧 DeepSeek 惯性兼容）
                if delta.get("content"):
                    content_parts.append(delta["content"])
                elif not THINKING_ON and delta.get("reasoning_content"):
                    content_parts.append(delta["reasoning_content"])
                fr = evt.get("choices", [{}])[0].get("finish_reason")
                if fr:
                    finish_reason = fr
                usage = evt.get("usage")
                if usage:
                    return "".join(content_parts), finish_reason, usage
    return "".join(content_parts), finish_reason, None


def llm_call_json(system, user, schema, model="strata",
                  budget=DEFAULT_BUDGET, max_retries=6, verbose=True):
    """调用 LLM 输出 JSON，按 schema 强校验。

    返回：通过校验的 dict。
    失败：抛 ValueError（连续 max_retries 次不合格）。
    """
    cur_budget = budget
    user_prompt = user
    for attempt in range(max_retries):
        try:
            content, finish_reason, usage = _stream_once(system, user_prompt, model, cur_budget)
        except Exception as e:
            # 网络/上游波动（429/502/超时）：指数退避重试（同请求同模型，质量不变）
            wait = min(10 * (2 ** attempt), 120)
            if verbose:
                print(f"  [NET] {e} → {wait}s 后重试")
            time.sleep(wait)
            continue
        record_usage(usage)

        # ① 截断信号：budget 翻倍重试（真截断才放大，不浪费）
        if finish_reason == "length":
            new_budget = min(int(cur_budget * 1.5), HARD_CAP)
            if new_budget > cur_budget:
                if verbose:
                    print(f"  [LEN] 截断(budget={cur_budget}) → 翻倍 {new_budget} 重试")
                cur_budget = new_budget
                continue
            raise ValueError(f"输出超安全阀 {HARD_CAP} 仍截断")

        # ② 提取 JSON
        obj = _extract_json(content)
        if obj is None:
            err_msg = "未找到可解析的 JSON"
        else:
            # ③ schema 校验
            errs = validate_schema(obj, schema)
            err_msg = "; ".join(errs) if errs else ""

        if not err_msg:
            if verbose:
                used = usage.get("completion_tokens", "?") if usage else "?"
                print(f"  [OK] 校验通过 (finish={finish_reason}, budget={cur_budget}, tok={used})")
            return obj

        # ④ 不合格 → 错误喂回 LLM 让它修正（不是容忍）
        if verbose:
            print(f"  [RETRY {attempt+1}] {err_msg[:80]}")
        user_prompt = user + f"\n\n⚠️ 上次输出不合规：{err_msg}\n请严格按要求的 JSON schema 重新输出，不要加任何多余文字。"
        time.sleep(2 ** min(attempt, 4))  # 指数退避 1/2/4/8/16s

    raise ValueError(f"LLM 连续 {max_retries} 次输出不合规，放弃")


# 复用 schema 校验（避免循环 import）
ANSWER_MARKER = "<<<ANSWER>>>"


def llm_call_text(system, user, model="strata", budget=900,
                  max_retries=4, verbose=False, answer_marker=False):
    """自由文本版 LLM 调用（非 JSON 输出，如 POV 场景描述）。

    复用流式 + finish_reason 截断自适应 + 网络指数退避（同 llm_call_json），
    但不做 schema 校验——适合"只要一段文本、不需要结构化"的场景。
    返回 (text, finish_reason)。

    answer_marker=True：要求模型先输出 <<<ANSWER>>> 标记行再写正文，
    返回时按标记切分取正文——low 组合（GLM/DeepSeek）会把整段思维链写进
    content（0907 实测整体方案/交接摘要两处带病），JSON 通道有括号配对
    防护，文本通道用标记法等价防护；模型漏写标记时退回原文（兼容）。
    """
    cur_budget = budget
    user_prompt = user
    if answer_marker:
        user_prompt += (f"\n\n[输出格式硬要求] 第一行只写标记 {ANSWER_MARKER}，"
                        "换行后再写正文；标记之前一个字都不要输出。")
    for attempt in range(max_retries):
        try:
            content, finish_reason, _ = _stream_once(system, user_prompt, model, cur_budget)
        except Exception as e:
            wait = min(10 * (2 ** attempt), 120)
            if verbose:
                print(f"  [NET] {e} → {wait}s 后重试")
            time.sleep(wait)
            continue
        if finish_reason == "length":
            cur_budget = min(cur_budget * 1.5, 8000)
            if verbose:
                print(f"  [截断] budget {cur_budget} 重试")
            continue
        if answer_marker and ANSWER_MARKER in content:
            content = content.split(ANSWER_MARKER)[-1]
        return content.strip(), finish_reason
    raise ValueError(f"重试 {max_retries} 次仍失败")


def validate_schema(obj, schema):
    from schema import validate_schema as _v
    return _v(obj, schema)


def self_test():
    """llm_json 自检：单测 JSON 提取 / schema 校验（不联网）。"""
    # JSON 提取（容忍围栏 + 前后杂文）
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json("前缀 {\"a\": 1} 后缀") == {"a": 1}
    assert _extract_json("无 json") is None
    assert _extract_json('{"a": 1, "b": ') is None  # 截断 JSON
    print("llm_json self_test: OK")


if __name__ == "__main__":
    self_test()
