#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_vision_chain.py — T1(#102) 本地判读链纯逻辑自检（无框架，assert 直跑；
llama-server 连通冒烟 = vision_smoke.py，env 门控可选，不在此处）。

覆盖：
1. vision_common.extract_verdict：干净 JSON / 散文夹 JSON / 畸形（抛异常）
2. vision_blindbench.norm_verdict：与 driver 判读面同一分类口径
3. vision_server.TIERS：三档 alias/模型文件名齐备且互异
4. blindbench.summarize 表驱动：一致/漏检/假翻转/人标行口径（glm 行才计一致率）
5. vision_common.judge_body：请求体同构（单帧单问 + base64 image_url + max_tokens）
6. vision_common Jev 投影（T8 #117）：jev_body 请求形状 / jev_project 三候选
   提取 + 榜外地板 + 全缺席拒答 / jev_decide softmax 数学 + 校准温度 + 平票定序 /
   judge_mode env 解析（json 显式回退）
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import vision_common as vc
import vision_server as vs
import vision_blindbench as vb


# ── 1. extract_verdict ──────────────────────────────────────────────────
assert vc.extract_verdict('{"verdict": "正常"}') == "正常"
assert vc.extract_verdict('好的，结论：{"verdict": "翻转"} 以上。') == "翻转"
assert vc.extract_verdict('前缀{"verdict":"倾斜"}后缀') == "倾斜"
for bad in ("", "完全没花括号", '{"verdict": 翻转}', "{}"):
    try:
        vc.extract_verdict(bad)
        raise AssertionError(f"畸形应抛异常: {bad!r}")
    except Exception:
        pass
print("ok 1 extract_verdict")


# ── 2. norm_verdict 与判读面同口径 ──────────────────────────────────────
assert vb.norm_verdict("正常") == "正常"
assert vb.norm_verdict("翻转") == "翻转"
assert vb.norm_verdict("画面翻转了") == "翻转"
assert vb.norm_verdict("rotated") == "翻转"
assert vb.norm_verdict("轻微 tilt") == "倾斜"
assert vb.norm_verdict("竖直正常") == "正常"
assert vb.norm_verdict("") == "正常"
assert vb.norm_verdict(None) == "正常"
print("ok 2 norm_verdict")


# ── 3. TIERS 完整性 ─────────────────────────────────────────────────────
assert sorted(vs.TIERS) == ["2b", "4b", "8b"]
aliases = set()
for t, cfg in vs.TIERS.items():
    assert cfg["alias"] and cfg["model"] and cfg["mmproj"], cfg
    assert cfg["alias"] not in aliases, "alias 必须互异"
    aliases.add(cfg["alias"])
assert vs.tier_cfg("4B")[0] == "4b"  # 大小写归一
try:
    vs.tier_cfg("99x")
    raise AssertionError("未知档位应退出")
except SystemExit:
    pass
print("ok 3 TIERS")


# ── 4. summarize 表驱动（四象限 + 人标口径）────────────────────────────
def row(baseline, local, human=False, lat=1.0, err=""):
    return {"tier": "t", "date": "d", "slug": "s", "shot": "x", "baseline": baseline,
            "human": human, "local": local, "latency_s": lat, "err": err}

# 象限①一致（glm 正常→正常）；②漏检（翻转真值→判非翻转）；③假翻转；④判读错误
res = [
    row("正常", "正常"), row("正常", "正常"),
    row("翻转", "正常"),                    # glm 基线翻转被漏检
    row("翻转", "翻转", human=True),        # 人标翻转真值、本地判翻转：命中真值，
                                            # 不计 glm 假翻转、不计一致率（对齐 eval_rows）
    row("正常", "", err="503"),             # 判读错误
]
m = vb.summarize("t", res)
assert m["n"] == 5 and m["n_glm"] == 4
assert m["agree"] == 2 and m["agree_rate"] == 0.5, m
assert m["flip_truth_n"] == 2 and m["flip_miss"] == 1, m
assert m["false_flip"] == 0, m  # 人标行的翻转不算假翻转
assert m["human_true_n"] == 1 and m["human_true_hit"] == 1, m
assert m["err_n"] == 1, m
# 全一致 + 0 漏检 → summary 达标样态
m2 = vb.summarize("t", [row("正常", "正常"), row("翻转", "翻转", human=True)])
assert m2["agree_rate"] == 1.0 and m2["flip_miss"] == 0
print("ok 4 summarize 口径")


# ── 5. judge_body 同构 ──────────────────────────────────────────────────
body = json.loads(vc.judge_body("qwen3-vl-4b", "QUJD"))
assert body["model"] == "qwen3-vl-4b" and body["max_tokens"] == vc.JUDGE_MAX_TOKENS
content = body["messages"][0]["content"]
assert content[0]["type"] == "text" and content[0]["text"] == vc.JUDGE_PROMPT
assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"
print("ok 5 judge_body")


# ── 6. Jev 投影（T8 #117）──────────────────────────────────────────────
def _lp_resp(top):
    """构造带 logprobs 的 chat 响应（与 llama-server 实测结构同构）。"""
    return {"choices": [{"message": {"content": "正"},
                         "logprobs": {"content": [{"token": "正", "logprob": 0.0,
                                                   "top_logprobs": top}]}}]}

# jev_body 请求形状：单 token + logprobs 开关 + JEV prompt
jb = json.loads(vc.jev_body("m", "QUJD"))
assert jb["max_tokens"] == vc.JEV_MAX_TOKENS == 1
assert jb["logprobs"] is True and jb["top_logprobs"] == vc.JEV_TOP_LOGPROBS
assert jb["messages"][0]["content"][0]["text"] == vc.JEV_PROMPT
assert "只答一个字" in vc.JEV_PROMPT and "JSON" not in vc.JEV_PROMPT
# 正文共享：两形态只有输出指令不同（措辞改动同步走的锚点）
assert vc.JUDGE_PROMPT.replace(
    '只输出 JSON，不要解释：{"verdict": "正常"} 或 {"verdict": "倾斜"} '
    '或 {"verdict": "翻转"}', "") == \
    vc.JEV_PROMPT.replace("只答一个字：正（正常）、倾（倾斜）、翻（翻转）", "")

# jev_project：三候选齐 → VERDICT 顺序 z；榜外候选地板；全缺席拒答
r = vc.jev_project(_lp_resp([{"token": "翻", "logprob": -3.0},
                             {"token": "正", "logprob": -0.1},
                             {"token": "倾", "logprob": -6.0}]))
assert r == [-0.1, -6.0, -3.0], r  # [正, 倾, 翻] 固定顺序
r = vc.jev_project(_lp_resp([{"token": "正", "logprob": -0.1},
                             {"token": "倾", "logprob": -6.0}]))
assert r[0] == -0.1 and r[1] == -6.0 and r[2] == -16.0, r  # 翻榜外 → 榜尾-10
for bad in ({"choices": [{"message": {"content": "正"}}]},            # 无 logprobs
            {"choices": []},                                          # 空结构
            _lp_resp([{"token": "倒", "logprob": -1.0}])):            # 全候选缺席
    try:
        vc.jev_project(bad)
        raise AssertionError(f"应抛 ValueError: {bad}")
    except ValueError:
        pass

# jev_decide：argmax + 平票取先（固定顺序红线）+ 校准温度
v, c = vc.jev_decide([-0.1, -6.0, -3.0])
assert (v, 0.9 < c < 1.0) == ("正常", True), (v, c)
v, c = vc.jev_decide([-3.0, -6.0, -0.1])
assert v == "翻转" and abs(c - 0.947) < 0.01, (v, c)
v, c_uniform = vc.jev_decide([0.0, 0.0, 0.0])
assert v == "正常" and abs(c_uniform - 1 / 3) < 1e-9, (v, c_uniform)  # 平票 → 序首（正常）
v_hot, c_hot = vc.jev_decide([-0.1, -6.0, -3.0], temperature=10.0)
assert c_hot < c, (c, c_hot)  # 升温摊平置信度
# 与 numpy softmax 交叉验证
import math as _m
z = [-0.06, -6.93, -2.89]
e = [_m.exp(x - max(z)) for x in z]
expect = e[0] / sum(e)
assert abs(vc.jev_decide(z)[1] - expect) < 1e-12

# judge_mode env 解析
_saved_mode = os.environ.pop(vc.MODE_ENV, None)
assert vc.judge_mode() == "jev"  # 缺省 jev
os.environ[vc.MODE_ENV] = "json"
assert vc.judge_mode() == "json"  # 显式回退
os.environ[vc.MODE_ENV] = "JSON"
assert vc.judge_mode() == "json"  # 大小写归一
os.environ[vc.MODE_ENV] = "nonsense"
assert vc.judge_mode() == "jev"   # 非法值不炸（归一回缺省）
os.environ.pop(vc.MODE_ENV, None)
if _saved_mode:
    os.environ[vc.MODE_ENV] = _saved_mode
print("ok 6 Jev 投影（body/project/decide/mode）")


# ── 7. blindbench.compare_forms（T8 迁移对比配对数学）──────────────────
def _frow(date, shot, local, lat):
    return {"date": date, "slug": "s", "shot": shot, "local": local,
            "latency_s": lat, "err": ""}

jr = [_frow("d", "shot01", "正常", 4.0), _frow("d", "shot02", "翻转", 6.0)]
er = [_frow("d", "shot01", "正常", 2.0), _frow("d", "shot02", "正常", 3.0)]
c = vb.compare_forms(jr, er)
assert c["n"] == 2 and c["agree"] == 1 and c["agree_rate"] == 0.5, c
assert c["lat_json_p50"] == 6.0 and c["lat_jev_p50"] == 3.0
assert abs(c["ratio_p50"] - 0.5) < 1e-9 and c["ac_pass"] is False
assert c["disagree"] == ["d:shot02(json=翻转,jev=正常)"], c["disagree"]
# 全一致 + 延迟达标 → AC 过
c2 = vb.compare_forms(jr, [_frow("d", "shot01", "正常", 1.0),
                           _frow("d", "shot02", "翻转", 1.0)])
assert c2["ac_pass"] is True and c2["agree_rate"] == 1.0
# 评测集不相交 → 可配对零
c3 = vb.compare_forms(jr, [_frow("x", "shot09", "正常", 1.0)])
assert c3.get("error") and "不相交" in c3["error"]
print("ok 7 compare_forms 配对/一致率/延迟比")

print("ALL PASS")
