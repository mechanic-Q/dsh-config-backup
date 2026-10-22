#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_evidence_production.py — #182 证据桶接生产自检（无框架，assert 直跑）。

背景：spec #132 写了、代码里没有的三项残留——生产侧证据桶不写三类目录与
manifest、证据行无 sha256、ground_truth_verdict 全仓 0 命中。本测试钉住
「生产跑一期 → 训练读得到」这条链的每一环，全部用 tempdir 合成，不碰真
判官 / E: 盘 / 活数据集（#141/#148）。

覆盖：
1. 生产桶目录 + manifest：_archive_evidence_buckets 写 <slug>/_frames/evidence/
   {1-confirmed,2-judge-found}/<round>_<sid>.jpg 与 manifest.json，条目字段与
   离线件同构（category/sid/round/frame/sha256/judge/why/local_verdict）
2. load_evidence_buckets 双路扫描：生产 manifest（frames_root 注入）与离线件
   一并读出，source 区分 production/offline，episode 正确
3. sha256 账实一致：条目哈希 == 在盘帧哈希；帧被改写后 verify_evidence_hashes
   报 sha-mismatch（「清单是账」在生产侧成立）
4. 分类语义：本地翻×判官翻 → 1-confirmed；本地正常×判官翻 → 2-judge-found；
   同轮重入不重复追加（幂等）
5. ground_truth_verdict 三件套：set_user_flag 落第三字段、词表归一、
   非法值不改写；stats 可读
5b. 归档门两个方向（#192）：判官缺席（ok=False / None / 非 dict）时连空 manifest
   都不落盘、不建类别子目录；判官在场时类别 ⊆ 四类词表、恰为判官轮两类，
   且不含 4-human-flag（白名单挡掉 local_false / human_flag / unfixed / 自编名）
6. 人机字段互不覆盖：append_records 整行 upsert 不冲掉已回填的人工三件套
   （重跑同期实证语义）
7. CLI verify-evidence：账实不符退出码 1，全对退出码 0
8. 训练链 row_material / export 消费生产桶帧（判官轮两类）
9. 真 dsh_judge.merge 的 judge_block 分类（不是手工造字典）
10. 3-unfixed 收尾补写（#194）：still_flipped 非空才写、round 取最后一次复检轮、
    帧在盘带 sha256、不影响既有 1/2 两类、重跑幂等、空名单不碰 manifest、
    缺帧不伪造条目；写出的条目能被训练链读取口读到且账实一致
"""
import hashlib
import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rotation_dataset as rd

WT = os.path.dirname(HERE)
_SEQ = [0]
SECTIONS = []


def _mktemp(prefix):
    """workspace 内安全临时目录（tempfile 在受限沙箱下 ACL 异常，见 #147 教训）。"""
    _SEQ[0] += 1
    d = os.path.join(WT, prefix + str(os.getpid()) + "_" + str(_SEQ[0]))
    os.makedirs(d, exist_ok=True)
    return d


def section(name):
    SECTIONS.append(name)
    print("\n== %s ==" % name)


def check(cond, msg):
    assert cond, msg
    print("  ok: %s" % msg)


def _mk_jpg(path, payload):
    with open(path, "wb") as f:
        f.write(payload)
    return path


# ── 1. 生产桶目录 + manifest ────────────────────────────────────────────
section("生产桶目录 + manifest 同构")

from run_pipeline import Driver, _sha256_file   # noqa: E402 — 被测对象

tmp = _mktemp(".tmp_182t_")
# 真实期次布局（与 rotation_groundtruth.frames_of / 生产扫描 glob 同形）：
# <root>/<date>/video/每日新中国b站/流水线/<slug>/_frames
slug = os.path.join(tmp, "2026-10-20", "video", "每日新中国b站", "流水线",
                    "daily-china-20261020")
frames = os.path.join(slug, "_frames")
ev_flat = os.path.join(frames, "evidence", "round0")
os.makedirs(ev_flat, exist_ok=True)
src1 = _mk_jpg(os.path.join(ev_flat, "shot01.jpg"), b"\xff\xd8confirmed")
src2 = _mk_jpg(os.path.join(ev_flat, "shot02.jpg"), b"\xff\xd8judgefound")

d = Driver.__new__(Driver)
d.slug = slug
d.date = "2026-10-20"
d.notify = lambda *a, **k: None

judge_block = {"ok": True, "round": "round0", "rows": [
    {"sid": "shot01", "local": "翻转", "judge": "翻转90", "kind": "confirmed",
     "conf": 0.95, "why": "横躺"},
    {"sid": "shot02", "local": "正常", "judge": "翻转180", "kind": "judge_found",
     "conf": 0.97, "why": "旗帜横倒"},
]}
evidence = [
    {"sid": "shot01", "round": "round0", "frame": src1, "verdict": "翻转",
     "conf": 0.95, "form": "jev", "sha256": _sha256_file(src1)},
    {"sid": "shot02", "round": "round0", "frame": src2, "verdict": "翻转",
     "conf": 0.97, "judge": "翻转180", "judge_why": "旗帜横倒",
     "kind": "judge_found", "sha256": _sha256_file(src2)},
]
added = d._archive_evidence_buckets(evidence, frames, judge_block)
check(added == 2, "归档 2 条（confirmed + judge_found）: %s" % added)

mf_path = os.path.join(frames, "evidence", "manifest.json")
check(os.path.exists(mf_path), "生产 manifest 落在 _frames/evidence/manifest.json")
mf = json.load(open(mf_path, encoding="utf-8"))
check(mf["episode"] == ["2026-10-20", "daily-china-20261020"], mf["episode"])
check(len(mf["evidence"]) == 2, len(mf["evidence"]))
cats = {e["category"] for e in mf["evidence"]}
check(cats == {"1-confirmed", "2-judge-found"}, cats)
for e in mf["evidence"]:
    check(os.path.exists(e["frame"]), "帧在盘: %s" % e["frame"])
    check(len(e["sha256"]) == 64, "条目带 64 位 sha256: %s" % e["sid"])
    check(e["frame_win"], "条目带 Windows 口径 frame_win（判官/跨侧可用）")
    check(os.path.join("evidence", e["category"]) in e["frame"],
          "帧落在 category 子目录: %s" % e["category"])

# 幂等：同轮重入不重复追加
added2 = d._archive_evidence_buckets(evidence, frames, judge_block)
mf2 = json.load(open(mf_path, encoding="utf-8"))
check(added2 == 0 and len(mf2["evidence"]) == 2,
      "同轮重入不重复追加（added=%s, 共 %s 条）" % (added2, len(mf2["evidence"])))

# ── 归档门两个方向 + 类别白名单（#192）────────────────────────────────
# 门要挡的是「写」这个动作：证据与 rows 都非空，不让「没东西可写」冒充「挡住了」。
tmp_fail = _mktemp(".tmp_182f_")
frames_fail = os.path.join(tmp_fail, "2026-10-20", "video", "每日新中国b站", "流水线",
                           "daily-china-20261020", "_frames")
ev_flat_fail = os.path.join(frames_fail, "evidence", "round0")
os.makedirs(ev_flat_fail, exist_ok=True)
src_fail = _mk_jpg(os.path.join(ev_flat_fail, "shot01.jpg"), b"\xff\xd8failopen")
evidence_fail = [{"sid": "shot01", "round": "round0", "frame": src_fail,
                  "verdict": "翻转", "conf": 0.9, "judge_why": "本地判翻"}]
rows_fail = [{"sid": "shot01", "local": "翻转", "judge": "翻转90",
              "kind": "confirmed", "conf": 0.95, "why": "横躺"}]
d_fail = Driver.__new__(Driver)
d_fail.slug = tmp_fail
d_fail.date = "2026-10-20"
mf_fail = os.path.join(frames_fail, "evidence", "manifest.json")
for label, blk in (("ok=False", {"ok": False, "rows": rows_fail}),
                   ("判官缺席 None", None),
                   ("非 dict 回填（判官报错留字符串）", "judge-error")):
    got = d_fail._archive_evidence_buckets(evidence_fail, frames_fail, blk)
    check(got == 0, "%s：不归档（added=%s）" % (label, got))
    check(not os.path.exists(mf_fail), "%s：不写 manifest（空清单也不许落盘）" % label)
    check(not os.path.isdir(os.path.join(frames_fail, "evidence", "1-confirmed")),
          "%s：不建类别子目录" % label)

# 判官在场：白名单闭集。喂进四类词表里其余两类 + 判官自己的非证据类，
# 只有 1-confirmed / 2-judge-found 能落盘；4-human-flag 只归指认回调。
tmp_ok = _mktemp(".tmp_182ok_")
frames_ok = os.path.join(tmp_ok, "2026-10-20", "video", "每日新中国b站", "流水线",
                         "daily-china-20261020", "_frames")
ev_flat_ok = os.path.join(frames_ok, "evidence", "round0")
os.makedirs(ev_flat_ok, exist_ok=True)
_ok_kinds = [("shot01", "confirmed"), ("shot02", "judge_found"),
             ("shot03", "local_false"), ("shot04", "human_flag"),
             ("shot05", "unfixed"), ("shot06", "自己编的类别")]
evidence_ok = []
for sid, _k in _ok_kinds:
    fp = _mk_jpg(os.path.join(ev_flat_ok, sid + ".jpg"), b"\xff\xd8" + sid.encode())
    evidence_ok.append({"sid": sid, "round": "round0", "frame": fp,
                        "verdict": "翻转", "conf": 0.9})
judge_ok = {"ok": True, "round": "round0", "rows": [
    {"sid": sid, "local": "翻转", "judge": "翻转90", "kind": k,
     "conf": 0.95, "why": "白名单用例"} for sid, k in _ok_kinds]}
d_ok = Driver.__new__(Driver)
d_ok.slug = tmp_ok
d_ok.date = "2026-10-20"
added_ok = d_ok._archive_evidence_buckets(evidence_ok, frames_ok, judge_ok)
mf_ok_path = os.path.join(frames_ok, "evidence", "manifest.json")
mf_ok = json.load(open(mf_ok_path, encoding="utf-8"))
cats_ok = {e["category"] for e in mf_ok["evidence"]}
check(added_ok == 2, "判官轮只归档白名单两类（added=%s）" % added_ok)
check(cats_ok <= set(rd.EVIDENCE_CATEGORIES),
      "归档类别落在四类词表内（%s ⊆ %s）" % (sorted(cats_ok), list(rd.EVIDENCE_CATEGORIES)))
check(cats_ok == {"1-confirmed", "2-judge-found"}, "类别恰为判官轮两类: %s" % sorted(cats_ok))
check(rd.HUMAN_FLAG_CATEGORY not in cats_ok,
      "不含 %s（人审类只由指认回调写）" % rd.HUMAN_FLAG_CATEGORY)
check({e["sid"] for e in mf_ok["evidence"]} == {"shot01", "shot02"},
      "local_false/human_flag/unfixed/自编类别一条都没进清单: %s"
      % sorted(e["sid"] for e in mf_ok["evidence"]))


# ── 2. load_evidence_buckets 双路扫描 ───────────────────────────────────
section("load_evidence_buckets 双路扫描（生产 + 离线）")

refs = _mktemp(".tmp_182r_")
off_dir = os.path.join(refs, "t15-evidence-20261015")
os.makedirs(os.path.join(off_dir, "3-unfixed"), exist_ok=True)
off_fp = _mk_jpg(os.path.join(off_dir, "3-unfixed", "final_shot05.jpg"), b"\xff\xd8unfixed")
json.dump({"episode": ["2026-10-15", "daily-china-20261015"],
           "evidence": [{"category": "3-unfixed", "sid": "shot05", "round": "final",
                         "frame": off_fp, "sha256": _sha256_file(off_fp),
                         "judge": "翻转90", "local_verdict": "翻转"}]},
          open(os.path.join(off_dir, "manifest.json"), "w", encoding="utf-8"),
          ensure_ascii=False)

buckets = rd.load_evidence_buckets(references_dir=refs, frames_root=tmp)
check(len(buckets) == 3, "离线 1 条 + 生产 2 条 = %s" % len(buckets))
srcs = {b["source"] for b in buckets}
check(srcs == {"offline", "production"}, srcs)
prod = [b for b in buckets if b["source"] == "production"]
check(all(b["episode"] == "2026-10-20-daily-china-20261020" for b in prod),
      "生产条目 episode 正确: %s" % prod[0]["episode"])
check(all(b["linked"] for b in buckets), "全部条目帧在盘 linked=True")
check({b["category"] for b in prod} == {"1-confirmed", "2-judge-found"},
      "生产三类目录由生产写入（不再是只有 t15 离线件）")
# 无生产产物时（CI/裸机）：只读离线件，不炸
only_off = rd.load_evidence_buckets(references_dir=refs, frames_root=_mktemp(".tmp_182n_"))
check(len(only_off) == 1 and only_off[0]["source"] == "offline",
      "无生产桶时只读离线件（不炸）: %s" % len(only_off))


# ── 3. sha256 账实一致 / 改动可报 ───────────────────────────────────────
section("sha256 账实一致 + 改动报错")

ok_results = rd.verify_evidence_hashes(buckets)
check(all(r["ok"] for r in ok_results), "初始全部账实一致")
check({r["state"] for r in ok_results} == {"ok"}, "state 全为 ok")

# 篡改生产帧 → 必须报 sha-mismatch
prod_entry = [b for b in buckets if b["source"] == "production"][0]
with open(prod_entry["frame"], "wb") as f:
    f.write(b"\xff\xd8tampered")
after = rd.verify_evidence_hashes([prod_entry])
check(after[0]["state"] == "sha-mismatch", after[0]["state"])
check(after[0]["actual"] != prod_entry["sha256"], "实算哈希与账不同（帧被改动）")
# 缺盘 → unlinked（不是账错）；旧条目无哈希 → missing
check(rd.verify_evidence_hashes([dict(prod_entry, frame="/nonexistent/x.jpg")])[0]["state"]
      == "unlinked", "缺盘帧标 unlinked（不算账错）")
check(rd.verify_evidence_hashes([dict(prod_entry, sha256="")])[0]["state"] == "missing",
      "无哈希条目标 missing")


# ── 4. 分类语义（四象限映射）───────────────────────────────────────────
section("分类语义：confirmed / judge_found 落对桶")
by_sid = {e["sid"]: e["category"] for e in mf["evidence"]}
check(by_sid == {"shot01": "1-confirmed", "shot02": "2-judge-found"}, by_sid)


# ── 5. ground_truth_verdict 三件套 ─────────────────────────────────────
section("ground_truth_verdict：落字段 / 词表 / 非法值")

ds = os.path.join(tmp, "ds.jsonl")
rows = [{"date": "2026-10-20", "slug": "daily-china-20261020", "shot": "shot%02d" % i,
         "vision_verdict": "正常", "user_flagged": None}
        for i in (1, 2, 3)]
rd.append_records(rows, ds)

upd, miss = rd.set_user_flag(
    [("2026-10-20", "daily-china-20261020", "shot01", True)],
    ds, source="human-flag", verdicts="翻转90")
check(upd and not miss, "回填命中: %s" % (upd,))
r1 = [r for r in rd.load(ds) if r["shot"] == "shot01"][0]
check(r1["ground_truth_verdict"] == "翻转90", r1.get("ground_truth_verdict"))
check(r1["user_flagged"] is True and r1["user_flagged_source"] == "human-flag",
      "布尔与来源同时落（三件套齐）")

# 逐键 dict
rd.set_user_flag([("2026-10-20", "daily-china-20261020", "shot02", True)], ds,
                 source="cli", verdicts={("2026-10-20", "daily-china-20261020",
                                          "shot02"): "翻转180"})
r2 = [r for r in rd.load(ds) if r["shot"] == "shot02"][0]
check(r2["ground_truth_verdict"] == "翻转180", r2.get("ground_truth_verdict"))

# 缺省按布尔回落：True→翻转 / False→正常
rd.set_user_flag([("2026-10-20", "daily-china-20261020", "shot03", False)], ds,
                 source="cli")
r3 = [r for r in rd.load(ds) if r["shot"] == "shot03"][0]
check(r3["ground_truth_verdict"] == "正常", r3.get("ground_truth_verdict"))

# 非法词表值不改写（保留上一版）
rd.set_user_flag([("2026-10-20", "daily-china-20261020", "shot01", True)], ds,
                 source="cli", verdicts="横躺")
r1b = [r for r in rd.load(ds) if r["shot"] == "shot01"][0]
check(r1b["ground_truth_verdict"] == "翻转90", "非法值不改写已有结论")

# stats 可读第三字段
buf = io.StringIO()
with redirect_stdout(buf):
    rd.stats(ds)
out = buf.getvalue()
check("ground_truth_verdict" in out and "人工真值三件套" in out,
      "stats 透视含 ground_truth_verdict + 三件套计数")


# ── 6. 人机字段互不覆盖（整行 upsert 不冲掉人工结论）──────────────────
section("append_records 不冲掉人工三件套")

before = [r for r in rd.load(ds) if r["shot"] == "shot01"][0]
check(before["user_flagged"] is True and before["ground_truth_verdict"] == "翻转90",
      "回填后人工三件套在位")
# 模拟重跑同期：采集行（build_records 形状）user_flagged=None 整行 upsert
replay = dict(before, user_flagged=None, vision_verdict="翻转", regen_rounds=2)
replay.pop("user_flagged_source", None)
replay.pop("ground_truth_verdict", None)
rd.append_records([replay], ds)
after_row = [r for r in rd.load(ds) if r["shot"] == "shot01"][0]
check(after_row["user_flagged"] is True, "重跑后 user_flagged 未被冲成 None")
check(after_row["user_flagged_source"] == before["user_flagged_source"],
      "来源保留（%s）" % after_row["user_flagged_source"])
check(after_row["ground_truth_verdict"] == "翻转90", "人工 verdict 保留")
check(after_row["vision_verdict"] == "翻转", "模型字段照常更新（人机分字段）")


# ── 7. CLI verify-evidence 退出码 ──────────────────────────────────────
section("CLI verify-evidence 退出码")

buf = io.StringIO()
with redirect_stdout(buf):
    rc_ok = rd.verify_evidence_cli([b for b in buckets if b["source"] == "offline"])
check(rc_ok == 0, "全对 → 退出码 0: %s" % rc_ok)
buf = io.StringIO()
with redirect_stdout(buf):
    rc_bad = rd.verify_evidence_cli([prod_entry])
check(rc_bad == 1, "账实不符 → 退出码 1: %s" % rc_bad)
check("sha-mismatch" in buf.getvalue() or "✗" in buf.getvalue(),
      "输出指出不符条目")


# ── 8. 训练链消费：row_material 读到生产桶帧 ──────────────────────────
section("训练链 row_material 读到生产桶帧")

import rotation_groundtruth as gt   # noqa: E402

row = {"date": "2026-10-20", "slug": "daily-china-20261020", "shot": "shot02",
       "user_flagged": True, "vision_verdict": "正常", "judge_verdict": "翻转180"}
mats = gt.row_material(row, references_dir=refs, date_root=tmp, frames_root=tmp)
ev_mats = [m for m in mats if m["kind"] == "evidence"]
check(bool(ev_mats), "证据桶帧进训练候选（AC1）: kinds=%s" % [m["kind"] for m in mats])
check(ev_mats[0]["category"] == "2-judge-found", ev_mats[0]["category"])
check(ev_mats[0]["round"] == "round0", ev_mats[0]["round"])
check(os.path.exists(ev_mats[0]["path"]), "桶帧在盘: %s" % ev_mats[0]["path"])
# 键隔离：错期次的行不给桶帧
bad = dict(row, date="2026-10-21")
check(not [m for m in gt.row_material(bad, references_dir=refs, date_root=tmp,
                                      frames_root=tmp) if m["kind"] == "evidence"],
      "键隔离：错 (date,slug,shot) 不给桶帧")


# ── 9. export 带第三件套 + 生产桶帧可导出 ──────────────────────────────
section("export 带 ground_truth_verdict + 生产桶帧")

out_mf = os.path.join(tmp, "manifest-out.jsonl")
gt.export(out_mf, synthetic=False, dataset=ds, references_dir=refs,
          date_root=tmp, frames_root=tmp)
exported = [json.loads(l) for l in open(out_mf, encoding="utf-8") if l.strip()]
check(bool(exported), "导出非空: %d 行" % len(exported))
check(all("ground_truth_verdict" in r for r in exported),
      "每行都带 ground_truth_verdict 字段（AC4 export 可读）")
hit = [r for r in exported if r["shot"] == "shot01"]
check(hit and hit[0]["ground_truth_verdict"] == "翻转90",
      "导出值 == 人工回填值: %s" % (hit[0]["ground_truth_verdict"] if hit else None))
check(all(r["label"] == "翻转" for r in exported if r["shot"] == "shot01"),
      "label 仍只由 user_flagged 决定（人机分字段）")
ev_rows = [r for r in exported if r["material_kind"] == "evidence"]
check(bool(ev_rows), "生产桶帧进了导出清单（AC1 端到端）: %d 行" % len(ev_rows))
check(ev_rows[0]["evidence_category"] in ("1-confirmed", "2-judge-found"),
      ev_rows[0]["evidence_category"])


# ── 10. 真 merge 产出的 judge_block 能分类（不是手工造字典）──────────
section("真 dsh_judge.merge 的 judge_block 分类")

import dsh_judge as dj   # noqa: E402

# 真 merge：本地正常 × 判官判翻 → judge_found；本地翻 × 判官判翻 → confirmed
local = {"shot01": "翻转", "shot02": "正常", "shot03": "正常"}
items = [{"sid": "shot01", "judge": "翻转90", "conf": 0.95, "why": "横躺"},
         {"sid": "shot02", "judge": "翻转180", "conf": 0.97, "why": "旗帜横倒"},
         {"sid": "shot03", "judge": "正常", "conf": 0.9, "why": "直立"}]
merged = dj.merge(local, items, round_tag="round0")
real_block = {"ok": True, "round": "round0",
              "confirmed": merged["confirmed"],
              "judge_found": merged["judge_found"],
              "local_false": merged["local_false"],
              "judge_normal": merged["judge_normal"],
              "rows": merged["rows"]}
check(real_block["confirmed"] == ["shot01"], real_block["confirmed"])
check(real_block["judge_found"] == ["shot02"], real_block["judge_found"])
# 真 rows 必须带 kind（分类依据），否则生产路径永远分不了类
kinds = {r["sid"]: r["kind"] for r in real_block["rows"]}
check(kinds.get("shot01") == "confirmed" and kinds.get("shot02") == "judge_found",
      "真 merge rows 带 kind: %s" % kinds)

tmp_real = _mktemp(".tmp_182m_")
slug_real = os.path.join(tmp_real, "2026-10-20", "video", "每日新中国b站", "流水线",
                         "daily-china-20261020")
frames_real = os.path.join(slug_real, "_frames")
ev_dir = os.path.join(frames_real, "evidence", "round0")
os.makedirs(ev_dir, exist_ok=True)
f1 = _mk_jpg(os.path.join(ev_dir, "shot01.jpg"), b"\xff\xd8c1")
f2 = _mk_jpg(os.path.join(ev_dir, "shot02.jpg"), b"\xff\xd8c2")
d_real = Driver.__new__(Driver)
d_real.slug = slug_real
d_real.date = "2026-10-20"
d_real.notify = lambda *a, **k: None
ev_rows = [{"sid": "shot01", "round": "round0", "frame": f1, "verdict": "翻转"},
           {"sid": "shot02", "round": "round0", "frame": f2, "verdict": "翻转"}]
n = d_real._archive_evidence_buckets(ev_rows, frames_real, real_block)
check(n == 2, "真 judge_block 归档 2 条: %s" % n)
mfr = json.load(open(os.path.join(frames_real, "evidence", "manifest.json"),
                     encoding="utf-8"))
by_cat = {e["sid"]: e["category"] for e in mfr["evidence"]}
check(by_cat == {"shot01": "1-confirmed", "shot02": "2-judge-found"},
      "真 merge 四象限 → 分类正确: %s" % by_cat)
# judge_normal 段不归档（一致段无素材价值）
check("shot03" not in by_cat, "judge_normal 段不归档")


# ── 11. 3-unfixed 收尾补写（#194）──────────────────────────────────────
section("3-unfixed 收尾补写：still_flipped → manifest 最难正类")

import types as _types   # noqa: E402 — 只为本节 ledger 桩

tmp_u = _mktemp(".tmp_194_")
slug_u = os.path.join(tmp_u, "2026-10-20", "video", "每日新中国b站", "流水线",
                      "daily-china-20261020")
frames_u = os.path.join(slug_u, "_frames")
ev_u = os.path.join(frames_u, "evidence")
for _tag in ("round0", "round1", "round2"):
    os.makedirs(os.path.join(ev_u, _tag), exist_ok=True)
f0_1 = _mk_jpg(os.path.join(ev_u, "round0", "shot01.jpg"), b"\xff\xd8r0c1")
f0_2 = _mk_jpg(os.path.join(ev_u, "round0", "shot02.jpg"), b"\xff\xd8r0c2")
f1_3 = _mk_jpg(os.path.join(ev_u, "round1", "shot03.jpg"), b"\xff\xd8r1u3")
f1_7 = _mk_jpg(os.path.join(ev_u, "round1", "shot07.jpg"), b"\xff\xd8r1u7")
f2_3 = _mk_jpg(os.path.join(ev_u, "round2", "shot03.jpg"), b"\xff\xd8r2u3")

# 既有 1/2 两类先落账（同一期同一 manifest）——后写 3-unfixed 不许动它们
d_u = Driver.__new__(Driver)
d_u.slug = slug_u
d_u.date = "2026-10-20"
d_u.notify = lambda *a, **k: None
ev_two = [{"sid": "shot01", "round": "round0", "frame": f0_1, "verdict": "翻转", "conf": 0.95},
          {"sid": "shot02", "round": "round0", "frame": f0_2, "verdict": "翻转", "conf": 0.95}]
judge_two = {"ok": True, "round": "round0", "rows": [
    {"sid": "shot01", "local": "翻转", "judge": "翻转90", "kind": "confirmed",
     "conf": 0.95, "why": "横躺"},
    {"sid": "shot02", "local": "正常", "judge": "翻转180", "kind": "judge_found",
     "conf": 0.95, "why": "旗帜横倒"},
]}
check(d_u._archive_evidence_buckets(ev_two, frames_u, judge_two) == 2,
      "先落既有 1/2 两类（1-confirmed + 2-judge-found）")
mf_u = os.path.join(ev_u, "manifest.json")
before_two = {(e["category"], e["sid"], e["round"]): e["sha256"]
              for e in json.load(open(mf_u, encoding="utf-8"))["evidence"]}

# 收尾证据行：shot03 两轮（round1→round2）、shot07 只有 round1
ledger_u = {"s08.vision": {"evidence": [
    {"sid": "shot03", "round": "round1", "frame": f1_3, "verdict": "翻转",
     "conf": 0.90, "judge_why": "第一轮仍翻"},
    {"sid": "shot07", "round": "round1", "frame": f1_7, "verdict": "翻转", "conf": 0.88},
    {"sid": "shot03", "round": "round2", "frame": f2_3, "verdict": "翻转",
     "conf": 0.91, "judge_why": "竖直化重写后仍翻"},
]}}
d_u.ledger = _types.SimpleNamespace(get=lambda k, *a, **kw: ledger_u.get(k))
rounds_u = [{"round": 1, "shots": ["shot03", "shot07"], "mode": "strip"},
            {"round": 2, "shots": ["shot03"], "mode": "rewrite"}]
added_u = d_u._archive_unfixed_evidence(["shot03", "shot07"], rounds_u)
check(added_u == 2, "still_flipped 两段 → 3-unfixed +2 条: %s" % added_u)

mf_u_doc = json.load(open(mf_u, encoding="utf-8"))
by_key = {(e["category"], e["sid"], e["round"]): e for e in mf_u_doc["evidence"]}
check({e["category"] for e in mf_u_doc["evidence"]}
      == {"1-confirmed", "2-judge-found", "3-unfixed"},
      "三类共存、既有 1/2 两类仍在: %s" % sorted({e["category"] for e in mf_u_doc["evidence"]}))
check(all(by_key[k]["sha256"] == v for k, v in before_two.items()),
      "既有 1/2 条目 sha256 未被 3-unfixed 写入改动")
check(by_key[("3-unfixed", "shot03", "round2")]["frame"].endswith(
      os.path.join("3-unfixed", "round2_shot03.jpg")),
      "shot03 取最后一次复检轮 round2: %s" % by_key[("3-unfixed", "shot03", "round2")]["frame"])
check(by_key[("3-unfixed", "shot07", "round1")]["frame"].endswith(
      os.path.join("3-unfixed", "round1_shot07.jpg")),
      "shot07 只复检一轮 → round1: %s" % by_key[("3-unfixed", "shot07", "round1")]["frame"])
for _k in (("3-unfixed", "shot03", "round2"), ("3-unfixed", "shot07", "round1")):
    _e = by_key[_k]
    check(os.path.exists(_e["frame"]), "帧在盘: %s" % _e["frame"])
    check(len(_e["sha256"]) == 64 and _e["sha256"] == _sha256_file(_e["frame"]),
          "条目带 64 位 sha256 且账实一致: %s" % _e["sid"])
    check(_e["frame_win"] and _e["local_verdict"] == "翻转" and _e["synthetic"] is False,
          "条目与 1/2 两类同构（frame_win/local_verdict/synthetic）")

# 幂等：重跑同期不重复追加
check(d_u._archive_unfixed_evidence(["shot03", "shot07"], rounds_u) == 0, "重跑同轮 +0（幂等）")
check(len(json.load(open(mf_u, encoding="utf-8"))["evidence"]) == 4,
      "重跑后清单仍 4 条（2 既有 + 2 新增）")

# 训练链读取口读到生产 3-unfixed，且 sha256 对账通过
refs_u = _mktemp(".tmp_194r_")
buckets_u = rd.load_evidence_buckets(references_dir=refs_u, frames_root=tmp_u)
unf = [b for b in buckets_u if b["category"] == "3-unfixed"]
check(len(unf) == 2 and all(b["source"] == "production" for b in unf),
      "load_evidence_buckets 读到 2 条生产 3-unfixed: %s" % len(unf))
check(all(b["linked"] for b in unf), "读出的 3-unfixed 条目帧都在盘")
check(all(r["ok"] for r in rd.verify_evidence_hashes(buckets_u)),
      "全部条目账实一致（3-unfixed 不破坏对账）")

# still_flipped 为空：不写任何 3-unfixed 条目（连 manifest/目录都不碰）
tmp_u2 = _mktemp(".tmp_194e_")
slug_u2 = os.path.join(tmp_u2, "2026-10-20", "video", "每日新中国b站", "流水线",
                       "daily-china-20261020")
d_u2 = Driver.__new__(Driver)
d_u2.slug = slug_u2
d_u2.date = "2026-10-20"
d_u2.ledger = _types.SimpleNamespace(get=lambda k, *a, **kw: {})
check(d_u2._archive_unfixed_evidence([], rounds_u) == 0, "still_flipped 空 → 0 条")
check(not os.path.exists(os.path.join(slug_u2, "_frames", "evidence", "manifest.json")),
      "still_flipped 空 → 不写 manifest")
check(not os.path.isdir(os.path.join(slug_u2, "_frames", "evidence", "3-unfixed")),
      "still_flipped 空 → 不建 3-unfixed 目录")

# 声明轮次没落地（重烧被跳过：rewrite 不可用等）→ 退回该段真正最后一次复检轮，
# 不写一个从没跑过的 round2
ledger_short = {"s08.vision": {"evidence": [
    {"sid": "shot09", "round": "round1", "frame": f1_7, "verdict": "翻转"}]}}
d_u3 = Driver.__new__(Driver)
d_u3.slug = slug_u
d_u3.date = "2026-10-20"
d_u3.ledger = _types.SimpleNamespace(get=lambda k, *a, **kw: ledger_short.get(k))
check(d_u3._archive_unfixed_evidence(
      ["shot09"], [{"round": 1, "shots": ["shot09"], "mode": "strip"},
                   {"round": 2, "shots": ["shot09"], "mode": "rewrite"}]) == 1,
      "声明重烧轮没落地 → 退回最后实际复检轮 +1 条")
_ev3 = {(e["category"], e["sid"], e["round"]) for e in
        json.load(open(mf_u, encoding="utf-8"))["evidence"]}
check(("3-unfixed", "shot09", "round1") in _ev3
      and ("3-unfixed", "shot09", "round2") not in _ev3,
      "条目轮次是真正复检过的 round1，不是声明却没跑的 round2")

# 证据行缺帧路径 / 一轮证据都没有：宁缺不伪造
d_u4 = Driver.__new__(Driver)
d_u4.slug = slug_u
d_u4.date = "2026-10-20"
ledger_none = {"s08.vision": {"evidence": [
    {"sid": "shot10", "round": "round1", "frame": "", "verdict": "翻转"}]}}
d_u4.ledger = _types.SimpleNamespace(get=lambda k, *a, **kw: ledger_none.get(k))
check(d_u4._archive_unfixed_evidence(["shot10"], [{"round": 1, "shots": ["shot10"]}]) == 0,
      "证据行无帧路径 → 不写假条目（0 条）")
check(d_u4._archive_unfixed_evidence(["shot11"], [{"round": 1, "shots": ["shot11"]}]) == 0,
      "完全没有证据行 → 不写假条目（0 条）")
check(not any(e["sid"] in ("shot10", "shot11") for e in
              json.load(open(mf_u, encoding="utf-8"))["evidence"]),
      "缺帧段没进 manifest")

print("\n%s\n%d 节全部通过：%s" % ("=" * 40, len(SECTIONS), "、".join(SECTIONS)))

# ── 清理（沙箱对 rmtree 敏感：走子进程尽力而为）────────────────────────
import subprocess as _sp   # noqa: E402
for _d in (tmp, tmp_fail, refs, tmp_real, tmp_u, refs_u, tmp_u2):
    try:
        _sp.run(["cmd", "/c", "rd", "/s", "/q", _d],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=30)
    except Exception as e:  # noqa: BLE001 — 清理失败不影响结论，但要留痕
        print("清理未完成（需手工删）: %s -> %s" % (_d, e))
