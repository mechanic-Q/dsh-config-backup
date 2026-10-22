#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_evidence_train_chain.py — T18(#130) 素材进 CLIP 训练链自检（无框架，assert 直跑）。

覆盖（ticket #130 验收对照）：
1. 证据桶读取：load_evidence_buckets 四类（1-confirmed/2-judge-found/3-unfixed/
   4-human-flag，ADR 0008）× 轮次全部可读，帧在盘 linked=True
   （fake episode tempdir 合成，不真跑判官）
2. dataset 行 judge_* 字段：build_records(judge=…) 落字段且 vision_verdict 不被覆盖；
   set_user_flag 独立回填（人机结论分字段、互不覆盖）
3. gt.row_material：工作帧 + 证据桶帧 + 行内 evidence_frames 按行链接
   （date,slug,shot），category+round 逐条保留
4. gt.export --dry-run：不 import torch/open_clip/PIL，列出被选素材
5. clip_train.select_material/dry_run：选择真用 row_material（含桶帧），
   torch 禁 import 下可跑；2:1 哈希切分口径不变
6. 一条命令 stats：判定数（pos+neg）+ 场景数（distinct date+slug）都打印
7. 端到端演练（fake 10-15 期）：manifest 条目 → (date,slug,shot) → 数据集行
   → 训练候选素材，全链可追溯；判官判定与人工真值分字段互不污染
"""
import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rotation_dataset as rd
import rotation_groundtruth as gt
import clip_train as ct

WT = os.path.dirname(HERE)  # worktree 根
_SEQ = [0]


def _mktemp(prefix):
    """workspace 内安全临时目录（弃 tempfile.mkdtemp：沙箱下它建出的目录
    对本进程 ACL 拒绝——WinError 5，cwd 直建目录正常；教训见实现票）。"""
    _SEQ[0] += 1
    d = os.path.join(WT, prefix + str(os.getpid()) + "_" + str(_SEQ[0]))
    os.makedirs(d)
    return d


def _fake_episode(root, date, slug, shots, cat_rounds, user_flags=None):
    """合成最小 10-15 期形态：_frames 工作帧 + t15-evidence-* 四类证据桶 +
    manifest.json（save_evidence 条目形状）+ 数据集行（judge_* 齐全）。

    shots: ["shot01", ...]；cat_rounds: {category: [(sid, round), ...]}
    返回 (refs_dir, ds_path, manifest 条目数)
    """
    frames_dir = os.path.join(root, date, "video", "每日新中国b站", "流水线",
                              slug, "_frames")
    os.makedirs(frames_dir, exist_ok=True)
    # 1x1 最小 JPEG（真实 jpg 字节；dry-run 不开图，但帧要在盘）
    # 最小 JPEG：PIL 现场生成（手拼字节串长度易错——偶数校验不过一次即弃）
    from PIL import Image
    import io as _io
    _buf = _io.BytesIO()
    Image.new("RGB", (8, 8), (127, 127, 127)).save(_buf, format="JPEG")
    jpg = _buf.getvalue()
    for sid in shots:
        with open(os.path.join(frames_dir, sid + ".jpg"), "wb") as f:
            f.write(jpg)

    ev_dir = os.path.join(root, "t15-evidence-" + date.replace("-", ""))
    manifest = {"episode": [date, slug], "human_flips": [], "rounds": [],
                "evidence": [], "judge_calls": [], "negative_control": []}
    n = 0
    import hashlib
    for cat, entries in cat_rounds.items():
        cat_dir = os.path.join(ev_dir, cat)
        os.makedirs(cat_dir, exist_ok=True)
        for sid, rnd in entries:
            dst = os.path.join(cat_dir, rnd + "_" + sid + ".jpg")
            shutil.copy2(os.path.join(frames_dir, sid + ".jpg"), dst)
            with open(dst, "rb") as f:
                sha = hashlib.sha256(f.read()).hexdigest()
            manifest["evidence"].append({
                "category": cat, "sid": sid, "round": rnd,
                "frame": dst, "frame_win": dst,
                "source_frame": os.path.join(frames_dir, sid + ".jpg"),
                "sha256": sha, "judge": "翻转90", "conf": 0.9,
                "why": "测试合成", "local_verdict": "正常",
                "synthetic": False, "note": ""})
            n += 1
    with open(os.path.join(ev_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    ds_path = os.path.join(root, "rotation-dataset.jsonl")
    rows = []
    for sid in shots:
        rows.append({
            "date": date, "slug": slug, "shot": sid,
            "video_seconds": 3.0, "director_camera": "",
            "director_camera_class": "未知", "prompt_camera_sentence": "",
            "prompt_lateral_leak": False, "camera_sanitized": False,
            "motion_stripped": False, "portrait_risk": False,
            "regen_rounds": 0, "vision_verdict": "正常",
            "vision_backend": "llama-server", "vision_model": "qwen3-vl-4b",
            "evidence_frames": [],
            "user_flagged": True if (user_flags or {}).get(sid) else None,
            "judge_verdict": "翻转90", "judge_conf": 0.9, "judge_why": "测试合成",
            "judge_flip_rounds": ["round0"],
            "judge_rounds": [{"round": "round0"}],
            "judge_source": "dsh_judge/test"})
    with open(ds_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return root, ds_path, n


# ── 1. 证据桶读取：四类 × 轮次 ──────────────────────────────────────────
tmp = _mktemp("t18t_")
CATS = {"1-confirmed": [("shot01", "round0"), ("shot01", "synthetic")],
        "2-judge-found": [("shot02", "round0"), ("shot02", "round1")],
        "3-unfixed": [("shot03", "final")],
        "4-human-flag": [("shot04", "human_flag")]}
_, ds_path, n_ev = _fake_episode(tmp, "2026-10-15", "daily-china-20261015",
                                 ["shot01", "shot02", "shot03", "shot04"], CATS,
                                 user_flags={"shot02": True, "shot03": True})
ev = rd.load_evidence_buckets(references_dir=tmp)
assert len(ev) == n_ev == 6, (len(ev), n_ev)
by_cat = {}
for e in ev:
    by_cat.setdefault(e["category"], []).append(e)
# 四类显式口径（ADR 0008 决策 1）：不再用 set(...) == set(EVIDENCE_CATEGORIES)
# 同义反复——词表本身也要钉死，否则少/多一类时两侧一起漂、断言仍绿。
assert rd.EVIDENCE_CATEGORIES == ("1-confirmed", "2-judge-found", "3-unfixed",
                                 "4-human-flag"), rd.EVIDENCE_CATEGORIES
assert set(by_cat) == {"1-confirmed", "2-judge-found", "3-unfixed",
                       "4-human-flag"}, sorted(by_cat)
assert all(e["linked"] for e in ev), [e["frame"] for e in ev if not e["linked"]]
assert all(e["round"] for e in ev), "每条必须带轮次"
assert by_cat["1-confirmed"][0]["sha256"], "sha256 溯源字段必须在"
assert by_cat["4-human-flag"][0]["category_note"], "人审桶必须有口径说明（CATEGORY_NOTES）"
print("ok 1 证据桶读取：四类 × 轮次 × linked/sha256")

# ── 2. dataset 行 judge_* 字段：分字段不覆盖 ────────────────────────────
tmp2 = _mktemp("t18t_")
p_dir = os.path.join(tmp2, "06_H3prompt"); d_dir = os.path.join(tmp2, "03_导演设计")
os.makedirs(p_dir); os.makedirs(d_dir)
json.dump({"id": "shot01", "prompt": "static shot", "video_seconds": 3.0},
          open(os.path.join(p_dir, "shot01.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"镜头": "静止"}, open(os.path.join(d_dir, "shot01.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
recs = rd.build_records(tmp2, "2026-10-16", p_dir, d_dir,
                        {"flipped": [], "judge_failed": []}, {},
                        judge={"shot01": {"verdict": "翻转90", "conf": 0.9, "why": "w",
                                           "flip_rounds": ["round0"],
                                           "rounds": [{"round": "round0"}],
                                           "source": "t14"}})
r0 = recs[0]
assert r0["vision_verdict"] == "正常", r0  # 本地原判不被 judge 覆盖
assert r0["judge_verdict"] == "翻转90" and r0["judge_source"] == "t14"
assert r0["judge_flip_rounds"] == ["round0"] and r0["judge_rounds"]
# 不传 judge → 无 judge_* 字段（向后兼容）
recs2 = rd.build_records(tmp2, "2026-10-16", p_dir, d_dir,
                         {"flipped": [], "judge_failed": []}, {})
assert "judge_verdict" not in recs2[0]
# set_user_flag 独立回填（人真值字段）
ds2 = os.path.join(tmp2, "ds.jsonl")
rd.append_records(recs, ds2)
slug_key = os.path.basename(tmp2.rstrip(os.sep))  # build_records 的 slug = basename(slug)
upd, miss = rd.set_user_flag([("2026-10-16", slug_key, "shot01", True)], ds2, source="bench")
assert upd and not miss
row = rd.load(ds2)[0]
assert row["user_flagged"] is True and row["user_flagged_source"] == "bench"
assert row["vision_verdict"] == "正常" and row["judge_verdict"] == "翻转90"
print("ok 2 judge_* 落行 + vision_verdict 不被覆盖 + user_flagged 独立")

# ── 3. gt.row_material：三源按行链接 ────────────────────────────────────
rows = rd.load(ds_path)
row02 = [r for r in rows if r["shot"] == "shot02"][0]
# 帧根 inject：合成的 episode 在 tmp 下，CI（ubuntu）没有 E: 盘（#141）
mats = gt.row_material(row02, references_dir=tmp, date_root=tmp)
kinds = [m["kind"] for m in mats]
assert "work" in kinds and "evidence" in kinds, (kinds, row02)
ev_mats = [m for m in mats if m["kind"] == "evidence"]
assert {m["category"] for m in ev_mats} == {"2-judge-found"}
assert {m["round"] for m in ev_mats} == {"round0", "round1"}
assert all(os.path.exists(m["path"]) for m in mats)
# 行内 evidence_frames（s08 现场挂链，无桶）→ kind=rowlink
# 帧必须与工作帧不同路径：同途同名帧会被 row_material 去重（seen 口径），
# 那样验不到「行内挂链」这条来源。
row04 = [r for r in rows if r["shot"] == "shot04"][0]
_s08_frame = os.path.join(tmp, "s08_convicted_shot04.jpg")
shutil.copy2(os.path.join(tmp, "2026-10-15", "video", "每日新中国b站", "流水线",
                          "daily-china-20261015", "_frames", "shot04.jpg"), _s08_frame)
row04["evidence_frames"] = [_s08_frame]
mats4 = gt.row_material(row04, references_dir=tmp, date_root=tmp)
assert any(m["kind"] == "rowlink" for m in mats4), mats4
# ADR 0008：行内条目也有四字段 dict 形态（人审指认写的），读取侧同样要认
row04["evidence_frames"] = [{"frame": _s08_frame, "sha256": "0" * 64,
                             "round": "human_flag", "category": "4-human-flag"}]
mats4d = gt.row_material(row04, references_dir=tmp, date_root=tmp)
_links = [m for m in mats4d if m["kind"] == "rowlink"]
assert _links and _links[0]["path"] == _s08_frame, mats4d
assert _links[0]["category"] == "4-human-flag" and _links[0]["round"] == "human_flag", _links
# dict 形态同样不许把 gt.row_frames 打炸（os.path.exists(dict) → TypeError）
assert _s08_frame in gt.row_frames(row04)
# 链接键 = (date, slug, shot)：错键行不给桶帧
bad = dict(row02, shot="shot99")
assert not [m for m in gt.row_material(bad, references_dir=tmp, date_root=tmp)
            if m["kind"] == "evidence"]
print("ok 3 row_material 三源链接（桶/工作帧/行内）+ 键隔离")

# ── 4. gt.export --dry-run：无 torch/open_clip/PIL 可跑 ────────────────
real_import = sys.modules["builtins"].__import__
def _guard(name, *a, **k):
    if name.split(".")[0] in ("torch", "open_clip", "PIL"):
        raise ImportError("dry-run 不许 import " + name)
    return real_import(name, *a, **k)
sys.modules["builtins"].__import__ = _guard
try:
    buf = io.StringIO()
    with redirect_stdout(buf):
        gt.export(os.path.join(tmp, "unused.jsonl"), dataset=ds_path,
                  dry_run=True, references_dir=tmp, date_root=tmp)
    out = buf.getvalue()
finally:
    sys.modules["builtins"].__import__ = real_import
assert "dry-run 素材清单" in out and "共 " in out, out[:400]
n_cand = len([l for l in out.splitlines() if "cat=" in l and "label=" in l])
assert n_cand >= 5, n_cand
assert "cat=2-judge-found" in out, out[:600]
print("ok 4 export --dry-run 无重依赖可跑（%d 条候选）" % n_cand)

# ── 5. clip_train dry_run：禁 torch 可跑（真仓数据集全链路）────────────
def _guard2(name, *a, **k):
    if name.split(".")[0] in ("torch", "open_clip"):
        raise ImportError("选择/清单不许 import " + name)
    return real_import(name, *a, **k)
sys.modules["builtins"].__import__ = _guard2
try:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ct.dry_run()
    out_ct = buf.getvalue()
finally:
    sys.modules["builtins"].__import__ = real_import
assert rc == 0 and "共 " in out_ct, out_ct[:400]
print("ok 5 clip_train dry_run 禁 torch 可跑")

# ── 6. 一条命令 stats：判定数 + 场景数 ──────────────────────────────────
buf = io.StringIO()
with redirect_stdout(buf):
    gt.stats()
s_out = buf.getvalue()
assert "判定数" in s_out and "场景数" in s_out, s_out[:500]
assert "判官复判行" in s_out and "证据桶条目" in s_out, s_out[:800]
print("ok 6 stats 一条命令：判定数 + 场景数 + 判官/证据桶覆盖")

# ── 7. 端到端演练（fake 10-15 期）：存证 → 数据集行 → 训练候选 ──────────
tmp3 = _mktemp("t18t_")
CATS3 = {"2-judge-found": [("shot03", "round0"), ("shot09", "round0")],
         "3-unfixed": [("shot03", "final")]}
refs3, ds3, _ = _fake_episode(tmp3, "2026-10-15", "daily-china-20261015",
                              ["shot03", "shot09"], CATS3,
                              user_flags={"shot03": True, "shot09": True})
# 全链追溯：manifest 条目 → (date,slug,shot) → 数据集行 → row_material 素材
mf = json.load(open(os.path.join(refs3, "t15-evidence-20261015", "manifest.json"),
                    encoding="utf-8"))
entry = [e for e in mf["evidence"] if e["category"] == "3-unfixed"][0]
key = ("2026-10-15", "daily-china-20261015", entry["sid"])
drow = [r for r in rd.load(ds3)
        if (r["date"], r["slug"], r["shot"]) == key][0]
assert drow["judge_verdict"] == "翻转90"  # 行带判官字段
assert drow["vision_verdict"] == "正常"   # 本地原判原样
assert drow["user_flagged"] is True       # 人工真值独立字段
mats7 = gt.row_material(drow, references_dir=refs3, date_root=refs3)
hit = [m for m in mats7 if m["kind"] == "evidence"
       and m["category"] == entry["category"] and m["round"] == entry["round"]]
assert len(hit) == 1, (entry, mats7)
assert os.path.abspath(hit[0]["path"]) == os.path.abspath(entry["frame"])
# 帧字节一致（sha256 溯源闭环）
import hashlib
with open(hit[0]["path"], "rb") as fh:
    sha_row = hashlib.sha256(fh.read()).hexdigest()
assert sha_row == entry["sha256"]
# 且该素材会出现在 dry-run 候选里（数据集行 → 训练候选）
buf = io.StringIO()
with redirect_stdout(buf):
    gt.export(os.path.join(tmp3, "x.jsonl"), dataset=ds3,
              dry_run=True, references_dir=refs3, date_root=refs3)
assert str(hit[0]["path"]) in buf.getvalue()
print("ok 7 端到端：manifest 条目 → 数据集行 → 训练候选素材 + sha256 闭环")

# ── 清理（沙箱对测试进程的 rmtree 会硬杀进程——改走子进程，尽力而为）
print("ALL PASS")
import subprocess as _sp
for _d in (tmp, tmp2, tmp3):
    try:
        _sp.run(["cmd", "/c", "rd", "/s", "/q", _d],
                 stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=30)
    except Exception:
        pass