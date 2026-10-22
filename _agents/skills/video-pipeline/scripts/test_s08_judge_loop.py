#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_s08_judge_loop.py — T16 #128 S8 三轮判读接判官 单测（无框架，assert 直跑）。

四象限路径（判官为准，local={sid:本地判定}，判官 items={sid:judge}）：
  1) 判官一致（confirmed）      —— 本地翻 × 判官翻 → 进重烧名单
  2) 判官推翻本地（local_false）—— 本地翻 × 判官正常 → 只记账，不进重烧名单
  3) 判官抓漏报（judge_found）  —— 本地正常 × 判官翻 → 进重烧名单（驱动循环）
  4) 判官不可用（fail-open）    —— judge 返回 ok=False → 采信本地，不 raise
另测：
  5) 台账轮键跨重启存活（temp report.json，两个 Ledger 实例模拟重启，merged 读回）
  6) disagreements 事件只收分歧行（judge_normal 不进）
  7) _check_frames 判官生效时返回重烧名单（judge_found 驱动 s08_h3 循环；
     local_false 不重烧——不在 _check_frames 返回的 flipped 里）
  8) _judge_fields_for_dataset 聚合 + rotation_dataset.build_records(judge=)
     只写 judge_* 字段、vision_verdict 不受判官覆盖
  9) _judge_gate_note 门④文案（判官结论+重烧名单+分歧；fail-open 轮标注）
 10) report_summary 判官摘要 part
全部密封：dsh_judge.judge 打假桩，不碰真判官/真模型。

直跑：python scripts/test_s08_judge_loop.py
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_TMP_ROOT = os.path.join(HERE, os.pardir, ".tmp_t16_tests")


def _tmpdir(tag):
    """工作区本地临时目录（不碰 %TEMP%，部分受限环境拒绝在那写入）。
    不用 tempfile.mkdtemp：受限沙箱下它创建的目录带排他默认 ACL（连属主都
    列不了目录），后续 makedirs/写文件全被拒——手搓时间戳+pid 目录走普通
    makedirs，继承工作区正常授权。跑完由收尾 shutil.rmtree 统一清理。"""
    os.makedirs(_TMP_ROOT, exist_ok=True)
    d = os.path.join(_TMP_ROOT, "%s_%d_%d" % (tag, int(time.time() * 1000), os.getpid()))
    os.makedirs(d, exist_ok=True)
    return d

import run_pipeline as rp
import dsh_judge as dj
import rotation_dataset as rd
from ledger import Ledger

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("PASS %s" % name)
    else:
        print("FAIL %s %s" % (name, detail))
        FAILS.append(name)


class _Patch(object):
    """临时改模块属性（或 dict 键，如 sys.modules），用完还原。"""

    def __init__(self, target, **kw):
        self.t = target
        self.kw = kw
        self.old = {}
        self.is_dict = isinstance(target, dict)

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = self.t.get(k, _MISSING) if self.is_dict else getattr(self.t, k)
            if self.is_dict:
                self.t[k] = v
            else:
                setattr(self.t, k, v)
        return self.t

    def __exit__(self, *a):
        for k, v in self.old.items():
            if self.is_dict:
                if v is _MISSING:
                    self.t.pop(k, None)
                else:
                    self.t[k] = v
            else:
                setattr(self.t, k, v)
        return False


_MISSING = object()  # dict patch：进面前不存在的键，退出时摘除


def mk_driver(tag):
    """Driver.__new__ 方法级测试台：真 Ledger（temp 目录）+ 假飞书。"""
    d = rp.Driver.__new__(rp.Driver)
    d.stamps_dir = _tmpdir("t16_" + tag)
    d.slug = d.stamps_dir
    d.date = "2026-10-17"
    d._t0 = 0.0
    d.ledger = Ledger(os.path.join(d.stamps_dir, "report.json"),
                      meta={"slug": "t16-test"}, t0=0.0)
    _sent = []

    def _notify(text):
        _sent.append(text)

    d.notify = _notify
    d.notify_sent = _sent
    return d


def fake_judge(items, ok=True, error=""):
    """构造 dsh_judge.judge 的假桩：items=[{sid,judge,conf,why}]。"""
    def _judge(rows, timeout=None, dry=False, expect=None):
        return {"ok": ok, "items": list(items), "error": error,
                "wall_s": 1.2, "in_tok": 111, "out_tok": 22, "reasoning_tok": 3,
                "raw": None}
    return _judge


def judge_items(pairs):
    """pairs: [(sid, judge_label, conf, why)] → items 形态（parse_final 产物口径）。"""
    return [{"sid": s, "judge": j, "conf": c, "local_ok": False, "why": w}
            for s, j, c, w in pairs]


JMAP = {"judged_map": {"shot01": "/x/shot01.jpg", "shot02": "/x/shot02.jpg",
                       "shot03": "/x/shot03.jpg"},
        "conf_map": {"shot01": 0.81, "shot02": 0.66}}


# ── 1. 判官一致（confirmed）：本地翻 × 判官翻 → 重烧 ─────────────────────
d = mk_driver("confirmed")
with _Patch(dj, judge=fake_judge(judge_items([
        ("shot01", "翻转90", 0.9, "画面横躺")]))) as p:
    reburn, block = d._judge_second_opinion(
        JMAP["judged_map"], JMAP["conf_map"], flipped=["shot01", "shot02"],
        tilted=[], evidence_tag="round0")
check("1 confirmed: 重烧含判官确认段", reburn == ["shot01"], reburn)
check("1 confirmed: local_false 为空（judge 只回了 shot01，无推翻）",
      block["local_false"] == [], block["local_false"])
check("1 confirmed: 台账轮块 ok", block["ok"] is True and block["round"] == "round0")
check("1 confirmed: usage 计账", block["in_tok"] == 111 and block["out_tok"] == 22)
check("1 confirmed: summary 文案", "确认翻转 1" in block["summary"], block["summary"])
rnd = d.ledger.get("judge_rounds", view="merged")
check("1 confirmed: judge_rounds 落账", isinstance(rnd, list) and len(rnd) == 1
      and rnd[0]["reburn"] == ["shot01"], rnd)
dis = d.ledger.get("judge_disagreements", view="merged")
check("1 confirmed: confirmed 也是分歧（local 与 judge 一致但属翻转确认）",
      len(dis) == 1 and {r["sid"] for r in dis[0]["rows"]} == {"shot01"}, dis)
check("1 confirmed: 判官一致不追加 notify（重烧消息由 s08 循环发）",
      not any("分歧" in t for t in d.notify_sent), d.notify_sent)

# ── 2. 判官推翻本地（local_false）：不进重烧名单，只记账 ─────────────────
d2 = mk_driver("localfalse")
with _Patch(dj, judge=fake_judge(judge_items([
        ("shot01", "正常", 0.88, "竖直无旋转"),
        ("shot02", "翻转180", 0.91, "上下颠倒")]))):
    reburn, block = d2._judge_second_opinion(
        JMAP["judged_map"], JMAP["conf_map"], flipped=["shot01", "shot02"],
        tilted=[], evidence_tag="round0")
check("2 local_false: 重烧只含判官翻段", reburn == ["shot02"], reburn)
check("2 local_false: 本地翻段被推翻", block["local_false"] == ["shot01"], block["local_false"])
check("2 local_false: judge_found 为空", block["judge_found"] == [], block["judge_found"])

# ── 3. 判官抓漏报（judge_found）：本地正常 × 判官翻 → 进重烧 ─────────────
d3 = mk_driver("found")
with _Patch(dj, judge=fake_judge(judge_items([
        ("shot01", "翻转90", 0.93, "横躺"),
        ("shot02", "正常", 0.85, "竖直"),
        ("shot03", "倾斜", 0.7, "轻微倾斜")]))):
    reburn, block = d3._judge_second_opinion(
        JMAP["judged_map"], JMAP["conf_map"], flipped=["shot02"],
        tilted=["shot03"], evidence_tag="round0")
check("3 judge_found: 漏报段进重烧", reburn == ["shot01"], reburn)
check("3 judge_found: 本地翻段被推翻（不是确认）",
      block["confirmed"] == [] and block["local_false"] == ["shot02"],
      (block["confirmed"], block["local_false"]))
check("3 judge_found: 倾斜段判官也倾斜 → judge_normal 不重烧",
      "shot03" not in reburn and block["judge_normal"] == ["shot03"], reburn)
check("3 judge_found: 分歧 notify 提到漏报", any("分歧" in t for t in d3.notify_sent),
      d3.notify_sent)

# ── 4. 判官不可用（fail-open）：采信本地，绝不 raise ──────────────────────
d4 = mk_driver("failopen")
with _Patch(dj, judge=fake_judge([], ok=False, error="判官超时（900s）")):
    reburn, block = d4._judge_second_opinion(
        JMAP["judged_map"], JMAP["conf_map"], flipped=["shot01"],
        tilted=[], evidence_tag="round0")
check("4 fail-open: 返回 None 采信本地", reburn is None and block is None,
      (reburn, block))
rnd = d4.ledger.get("judge_rounds", view="merged")
check("4 fail-open: 失败轮也落账（可追责）",
      isinstance(rnd, list) and len(rnd) == 1 and rnd[0]["ok"] is False
      and "超时" in rnd[0]["error"], rnd)
check("4 fail-open: notify 告警", any("不可用" in t for t in d4.notify_sent), d4.notify_sent)
# 特性显式关闭：静默跳过，不落账不告警
d4b = mk_driver("disabled")
with _Patch(dj, judge=fake_judge([]), enabled=lambda: False):
    reburn, block = d4b._judge_second_opinion(
        JMAP["judged_map"], JMAP["conf_map"], flipped=["shot01"],
        tilted=[], evidence_tag="round0")
check("4 disabled: 特性关静默跳过", reburn is None and block is None
      and d4b.ledger.get("judge_rounds", view="merged") is None, (reburn, block))

# ── 5. 空判读段：不调判官 ────────────────────────────────────────────────
d5 = mk_driver("empty")
_calls = []


def _spy(rows, timeout=None, dry=False, expect=None):
    _calls.append(rows)
    return {"ok": False, "items": [], "error": "不应被调", "wall_s": 0,
            "in_tok": 0, "out_tok": 0, "reasoning_tok": 0, "raw": None}


with _Patch(dj, judge=_spy):
    reburn, block = d5._judge_second_opinion({}, {}, flipped=[], tilted=[],
                                             evidence_tag="round0")
check("5 empty: 无可复核段不调判官", not _calls and reburn is None and block is None,
      (_calls, reburn, block))

# ── 6. 台账轮键跨重启存活（两个 Ledger 实例模拟重启，merged 读回）─────────
tmp = _tmpdir("t16_restart")
rep = os.path.join(tmp, "report.json")
led1 = Ledger(rep, meta={"slug": "t16-restart"}, t0=0.0)
led1.record_event("judge_rounds", {"round": "round0", "ok": True,
                                       "reburn": ["shot01"], "judged": ["shot01", "shot02"]})
led1.record_event("judge_disagreements", {"round": "round0",
                                              "rows": [{"sid": "shot01", "kind": "confirmed"}]})
led1.flush()
led2 = Ledger(rep, meta={"slug": "t16-restart"}, t0=0.0)  # 模拟重启后的新实例
led2.record_event("judge_rounds", {"round": "round1", "ok": False, "error": "判官超时"})
led2.flush()
rounds = led2.get("judge_rounds", view="merged")
check("6 restart: 两轮跨进程累积读回",
      isinstance(rounds, list) and len(rounds) == 2
      and [r["round"] for r in rounds] == ["round0", "round1"], rounds)
dis = led2.get("judge_disagreements", view="merged")
check("6 restart: 分歧键跨进程读回",
      isinstance(dis, list) and len(dis) == 1 and dis[0]["rows"][0]["sid"] == "shot01", dis)

# ── 7. s08_h3 轮次循环：judge_found 驱动重烧、local_false 不重烧 ─────────
# _check_frames 全链路（抽帧 ffmpeg + 本地 VLM 推理）无法密封，循环行为用桩测：
# _check_frames 桩按「判官为准名单」语义返回（与真实现同一接缝——真实现的
# 判官归并已在 §1-3 直测 _judge_second_opinion 覆盖）。
def mk_loop_driver(check_results, local_false_ever=()):
    """s08_h3 循环测试台：_check_frames 按 check_results 队列吐 (flipped, tilted)；
    其余重依赖（strata/comfy/whisper/烧卡）全部桩平。"""
    d = mk_driver("loop")
    d.d = {n: os.path.join(d.stamps_dir, n) for n in
           ("01_分段", "02_TTS语音", "06_H3prompt", "07_成片", "03_导演设计")}
    for n in d.d.values():
        os.makedirs(n, exist_ok=True)
    json.dump([{"id": "shot01"}, {"id": "shot02"}],
              open(os.path.join(d.d["01_分段"], "分镜清单_raw.json"), "w", encoding="utf-8"))
    for sid in ("shot01", "shot02"):  # prompt 齐段（预检要求）
        json.dump({"id": sid, "prompt": "固定镜头"},
                  open(os.path.join(d.d["06_H3prompt"], sid + ".json"), "w", encoding="utf-8"))
    vdir = os.path.join(d.d["07_成片"], "1080P")
    os.makedirs(vdir, exist_ok=True)
    for sid in ("shot01", "shot02"):  # 产物齐段（完整性核对要求，vdir/1080P 下）
        json.dump({"id": sid, "prompt": "x", "video_seconds": 5},
                  open(os.path.join(vdir, f"daily-x_{sid}_a.mp4"), "w", encoding="utf-8"))
    queue = list(check_results)

    def _fake_check(vdir, only=None, evidence_tag="round0"):
        return queue.pop(0) if queue else ([], [])
    d._check_frames = _fake_check
    burned = []
    d._h3_submit = lambda ids, vdir: burned.extend(ids)
    d._strip_motion_prompt_file = lambda sid, strict=True: None
    d._rewrite_prompt_vertical = lambda sid: True  # 重写可用=True（不可用见 §12）
    d._ws_started = d._ws_done = False
    import threading as _th
    d._whisper_thread = lambda: None
    return d, burned


# 判官生效：round0 本地判翻 shot01+shot02 → 判官推翻 shot02（local_false）
# → 循环只重烧 shot01；round1 复检判官仍翻 → 第 2 轮重烧；round2 后干净收尾。
d7, burned7 = mk_loop_driver([
    (["shot01", "shot02"], []),   # round0 本地全翻（判官改为 [shot01]）
    (["shot01"], []),             # round1 复检仍翻
    ([], []),                     # round2 复检干净
])
# 模拟判官归并发生在 _check_frames 内：第一吐直接给判官为准名单
# （持久队列，不能是 lambda 内列表字面量 .pop(0)——那每次调用新建列表，
#  永远吐第一项，round2 永不干净 → 误触 still_flipped raise，§7 实测翻车）
_q7 = [(["shot01"], []), (["shot01"], []), ([], [])]
d7._check_frames = lambda vdir, only=None, evidence_tag="round0": \
    (_q7.pop(0) if _q7 else ([], []))
with _Patch(rp.strata_window, WINDOW_MODE="coexist",
            ensure_online=lambda notify=None: {"ok": True},
            stop=lambda notify=None: {"ok": True, "already_off": True}), \
        _Patch(sys.modules, comfyui_selfheal=types.SimpleNamespace(
            heal=lambda notify=None, **kw: {"actions": []})), \
        _Patch(d7, notify=lambda t: d7.notify_sent.append(t)), \
        _Patch(rd, append_records=lambda recs, dataset_path=None: None):
    d7.s08_h3()  # append_records 桩平：§7 不许污染真数据集（t16_loop_* 4 条实锤）
check("7 loop: judge_found+confirmed 驱动两轮重烧（local_false 不烧）",
      burned7[:2] == ["shot01", "shot02"] and burned7[2:] == ["shot01", "shot01"],
      burned7)  # 前 2 项=首轮全量提交，后 2 项=判官为准名单驱动的两轮重烧
check("7 loop: 轮次台账", len(d7.ledger.get("s08.flip_regen_rounds", view="merged") or []) == 2
      if isinstance(d7.ledger.get("s08.flip_regen_rounds", view="merged"), list) else False,
      d7.ledger.get("s08.flip_regen_rounds", view="merged"))
check("7 loop: still_flipped 为空（local_false 不计入）",
      not (d7.ledger.get("s08.still_flipped", view="merged") or []),
      d7.ledger.get("s08.still_flipped", view="merged"))
# ADR 0008 决策 7（#189）：s08 收尾在数据集写入之后显式 flush——s08_h3 返回时账已落盘
_rep7 = os.path.join(d7.stamps_dir, "report.json")
_rep7_data = (json.load(open(_rep7, encoding="utf-8"))
              if os.path.exists(_rep7) else {})
check("7 loop: s08 收尾已 flush 台账（盘上已见 segments）",
      (_rep7_data.get("s08") or {}).get("segments") is not None, _rep7)

# ── 8. 数据集 judge_* 字段聚合 + build_records 不覆盖 vision_verdict ─────
d8 = mk_driver("dataset")
d8.ledger.record_event("judge_rounds", {"round": "round0", "ok": True, "rows": [
    {"sid": "shot01", "local": "正常", "judge": "翻转90", "kind": "judge_found",
     "conf": 0.92, "why": "横躺", "round": "round0"},
    {"sid": "shot02", "local": "翻转", "judge": "正常", "kind": "local_false",
     "conf": 0.88, "why": "竖直", "round": "round0"}]})
d8.ledger.record_event("judge_rounds", {"round": "round1", "ok": True, "rows": [
    {"sid": "shot01", "local": "翻转", "judge": "翻转90", "kind": "confirmed",
     "conf": 0.94, "why": "重烧后仍横躺", "round": "round1"}]})
jm = d8._judge_fields_for_dataset()
check("8 dataset: judge_found 段有 judge_verdict",
      jm["shot01"]["judge_verdict"] == "翻转90", jm.get("shot01"))
check("8 dataset: 轮次跨轮累积去重",
      jm["shot01"]["judge_rounds"] == ["round0", "round1"]
      and jm["shot01"]["judge_flip_rounds"] == ["round0", "round1"], jm["shot01"])
check("8 dataset: local_false 段不进 flip_rounds",
      jm["shot02"]["judge_flip_rounds"] == [] and jm["shot02"]["judge_rounds"] == ["round0"],
      jm["shot02"])
# build_records：vision_verdict 用本地名单（shot02 翻转），judge_* 独立并行
pdir = os.path.join(_tmpdir("t16_ds"), "06_H3prompt")
os.makedirs(pdir)
json.dump({"id": "shot01", "prompt": "固定镜头", "video_seconds": 5},
          open(os.path.join(pdir, "shot01.json"), "w", encoding="utf-8"))
json.dump({"id": "shot02", "prompt": "镜头横移", "video_seconds": 6},
          open(os.path.join(pdir, "shot02.json"), "w", encoding="utf-8"))
recs = rd.build_records("slug-x", "2026-10-17", pdir, pdir,
                        vision={"flipped": ["shot02"], "judge_failed": []},
                        regen_rounds={"shot02": 1}, judge=jm)
by = {r["shot"]: r for r in recs}
check("8 dataset: vision_verdict 保持本地判读（判官不覆盖）",
      by["shot02"]["vision_verdict"] == "翻转"
      and by["shot01"]["vision_verdict"] == "正常",
      {k: by[k]["vision_verdict"] for k in by})
check("8 dataset: judge_* 字段落行",
      by["shot01"]["judge_verdict"] == "翻转90"
      and by["shot01"]["judge_source"] == "dsh_judge"
      and by["shot01"]["judge_conf"] == 0.94, by["shot01"])
check("8 dataset: judge_why/judge_rounds 落行",
      by["shot02"]["judge_why"] == "竖直" and by["shot02"]["judge_rounds"] == ["round0"],
      by["shot02"])

# ── 9. _judge_gate_note 门④文案 ─────────────────────────────────────────
d9 = mk_driver("gatenote")
note = d9._judge_gate_note()
check("9 gate_note: 判官未跑 → 空串", note == "", note)
d9.ledger.record_event("judge_rounds", {"round": "round0", "ok": True,
    "summary": "确认翻转 1 / 判官新发现 1 / 本地误报 1 / 一致正常 0",
    "reburn": ["shot01", "shot02"], "rows": [
        {"sid": "shot01", "kind": "confirmed"}, {"sid": "shot02", "kind": "judge_found"},
        {"sid": "shot03", "kind": "local_false"}]})
note = d9._judge_gate_note()
check("9 gate_note: 判官结论+重烧名单进文案",
      "确认翻转 1" in note and "shot01" in note and "判官复判" in note, note)
check("9 gate_note: 分歧段点名", "shot03" in note and "分歧" in note, note)
d9.ledger.record_event("judge_rounds", {"round": "round1", "ok": False,
                                            "error": "判官超时（900s）"})
note = d9._judge_gate_note()
check("9 gate_note: fail-open 轮如实标注", "不可用" in note and "采信本地" in note, note)

# ── 11. 判官抓漏报定罪帧存证（#132 缺口修补）─────────────────────────────
# 本地判正常 × 判官判翻 = judge_found：这些段进了重烧名单。T11 的存证循环只遍历
# 本地 flipped 且发生在判官归并之前，所以这些帧原先从未落副本——下一轮重烧重抽即
# 覆盖原路径，最强负类素材永久丢失（实测 judge_found 进名单但 evidence 恒空）。
def _mk_frames(d, sid, payload):
    frames = os.path.join(d.slug, "_frames")
    os.makedirs(frames, exist_ok=True)
    fp = os.path.join(frames, sid + ".jpg")
    with open(fp, "wb") as f:
        f.write(payload)
    return frames, fp


d11 = mk_driver("judgefound_ev")
_frames11, _src11 = _mk_frames(d11, "shot01", b"\xff\xd8judgefound")
_frames12, _src12 = _mk_frames(d11, "shot02", b"\xff\xd8confirmed")
block11 = {"ok": True, "round": "round0",
           "judge_found": ["shot01"],
           "rows": [{"sid": "shot01", "judge": "翻转180", "kind": "judge_found",
                     "conf": 0.97, "why": "旗帜横倒"},
                    {"sid": "shot02", "judge": "翻转90", "kind": "confirmed",
                     "conf": 0.95, "why": "横躺"}]}
ev11 = d11._save_judge_found_evidence(
    block11, {"shot01": _src11, "shot02": _src12}, {"shot01": 0.95},
    "round0", _frames11, [])
check("11 存证: judge_found 段落证据行", len(ev11) == 1 and ev11[0]["sid"] == "shot01",
      ev11)
check("11 存证: 副本在盘且字节一致",
      os.path.exists(ev11[0]["frame"])
      and open(ev11[0]["frame"], "rb").read() == b"\xff\xd8judgefound", ev11[0])
check("11 存证: 落在本轮 tag 目录（非原帧路径）",
      os.path.join("evidence", "round0") in ev11[0]["frame"]
      and os.path.abspath(ev11[0]["frame"]) != os.path.abspath(_src11), ev11[0])
check("11 存证: 判官原判与判词落行（可回溯）",
      ev11[0]["judge"] == "翻转180" and ev11[0]["why" if False else "judge_why"] == "旗帜横倒"
      and ev11[0]["kind"] == "judge_found", ev11[0])

# 幂等：同一轮重复调用不重复存（confirmed 段已由本地循环存过，也不得重复追加）
ev11b = d11._save_judge_found_evidence(
    block11, {"shot01": _src11, "shot02": _src12}, {"shot01": 0.95},
    "round0", _frames11, list(ev11))
check("11 存证: 同轮重入不重复追加", len(ev11b) == 1, ev11b)
check("11 存证: confirmed 段不由本函数代存（仍 1 条）",
      [e["sid"] for e in ev11b] == ["shot01"], ev11b)

# 无定罪帧 / fail-open 轮：静默跳过，不抛不造行（fail-open 语义）
check("11 存证: 无帧段跳过不炸",
      d11._save_judge_found_evidence(block11, {}, {}, "round0", _frames11, []) == [])
check("11 存证: fail-open 轮（ok=False）不存证",
      d11._save_judge_found_evidence({"ok": False, "judge_found": ["shot01"]},
                                     {"shot01": _src11}, {}, "round0",
                                     _frames11, []) == [])

# ── 12. 竖直化重写不可用 → 不删产物、不空烧（2026-10-20 shot19 实锤）─────
# 现象：第 2 轮 rewrite 要 LLM 改 prompt，Strata 离线时 llm_json 6 次退避后抛错。
# 旧顺序「先删产物 → 重写 → 提交」在重写失败时 prompt 没变、产物已删 → 重烧出
# 同一画面（或被 submit_accel 的「产物已存在」SKIP 成零动作），却对外宣称已重烧。
# 钉住：重写返回 False 时，本轮该段的旧产物必须还在盘上，且不得再提交烧卡。
d12, burned12 = mk_loop_driver([
    (["shot01"], []),   # round0 本地翻 → 第 1 轮（strip）重烧
    (["shot01"], []),   # round1 复检仍翻 → 第 2 轮（rewrite）
])
d12._check_frames = lambda vdir, only=None, evidence_tag="round0": \
    (["shot01"], [])
d12._rewrite_prompt_vertical = lambda sid: False  # 模拟 Strata 离线
_vdir12 = os.path.join(d12.d["07_成片"], "1080P")


def _burn12(ids, vdir):
    """模拟真烧卡：每段落一个产物文件（否则 strip 轮删完就永远没有产物）。"""
    for sid in ids:
        burned12.append(sid)
        open(os.path.join(vdir, "daily-x_%s_reburn.mp4" % sid), "w",
             encoding="utf-8").write("v")
d12._h3_submit = _burn12
with _Patch(rp.strata_window, WINDOW_MODE="coexist",
            ensure_online=lambda notify=None: {"ok": True},
            stop=lambda notify=None: {"ok": True, "already_off": True}), \
        _Patch(sys.modules, comfyui_selfheal=types.SimpleNamespace(
            heal=lambda notify=None, **kw: {"actions": []})), \
        _Patch(rd, append_records=lambda recs, dataset_path=None: None):
    try:
        d12.s08_h3()
        _raised12 = False
    except RuntimeError:
        _raised12 = True
check("12 rewrite 不可用: 该段产物仍在盘（没被空烧删掉）",
      bool(glob.glob(os.path.join(_vdir12, "*shot01*.mp4"))),
      sorted(os.listdir(_vdir12)))
check("12 rewrite 不可用: 第 2 轮没再提交烧卡（burned 只有第 1 轮那次）",
      burned12.count("shot01") == 2,  # 首轮全量 1 次 + strip 轮 1 次
      burned12)
check("12 rewrite 不可用: 仍翻转要抛给人审（不静默放过）", _raised12, _raised12)

# ── 10. report_summary 判官摘要 part ─────────────────────────────────────
d10 = mk_driver("summary")
s = d10.report_summary()
check("10 summary: 判官未跑不出现判官字样", "判官" not in s, s)
d10.ledger.record_event("judge_rounds", {"round": "round0", "ok": True,
    "summary": "确认翻转 1 / 判官新发现 1 / 本地误报 0 / 一致正常 14"})
s = d10.report_summary()
check("10 summary: ok 轮摘要出现", "判官复判" in s and "round0" in s, s)
d10.ledger.record_event("judge_rounds", {"round": "round1", "ok": False,
                                             "error": "判官超时"})
s = d10.report_summary()
check("10 summary: fail-open 轮计数出现", "判官不可用 1 轮" in s, s)

# ── 收尾 ─────────────────────────────────────────────────────────────────
print()
if FAILS:
    print("test_s08_judge_loop: %d 项失败 → %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("test_s08_judge_loop: 全部通过")
sys.exit(0)