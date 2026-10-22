# -*- coding: utf-8 -*-
"""#182 真实生产路径探针：用 10-15 期真实帧，跑 _archive_evidence_buckets →
load_evidence_buckets 生产扫描 → 训练链 row_material 读到。

不是单测：单测用合成帧；这里用 E: 盘真实现场帧（真 jpg、真尺寸），
验证「生产跑一期后训练读得到」这条 AC 在真实素材上成立。
"""
import json, os, shutil, sys, tempfile

HERE = os.path.abspath("scripts")
sys.path.insert(0, HERE)

import hostpath
import rotation_dataset as rd
import rotation_groundtruth as gt
from run_pipeline import Driver, _sha256_file

# 真实现场帧（10-15 期，判官确认翻转的 5 段之一）
REAL_FRAME = hostpath.to_host_path(
    r"E:\每日新中国\2026-10-15\video\每日新中国b站\流水线\daily-china-20261015\_frames\shot03.jpg")
print("真实源帧:", REAL_FRAME, "在盘=", os.path.exists(REAL_FRAME))
assert os.path.exists(REAL_FRAME), "真实帧不在盘"

root = tempfile.mkdtemp(prefix="t182probe_")
slug = os.path.join(root, "2026-10-15", "video", "每日新中国b站", "流水线",
                    "daily-china-20261015")
frames = os.path.join(slug, "_frames")
ev_flat = os.path.join(frames, "evidence", "round0")
os.makedirs(ev_flat, exist_ok=True)
real_copy = os.path.join(ev_flat, "shot03.jpg")
shutil.copy2(REAL_FRAME, real_copy)
real_sha = _sha256_file(real_copy)
print("真实帧 sha256:", real_sha[:16], "…  大小:", os.path.getsize(real_copy), "bytes")

d = Driver.__new__(Driver)
d.slug = slug
d.date = "2026-10-15"
d.notify = lambda *a, **k: None

block = {"ok": True, "round": "round0", "rows": [
    {"sid": "shot03", "local": "正常", "judge": "翻转90", "kind": "judge_found",
     "conf": 0.97, "why": "地面在左缘竖条，天空在右，主体横躺"}]}
ev = [{"sid": "shot03", "round": "round0", "frame": real_copy, "verdict": "翻转",
       "conf": 0.97, "judge": "翻转90", "judge_why": "地面在左缘竖条",
       "kind": "judge_found", "sha256": real_sha}]
added = d._archive_evidence_buckets(ev, frames, block)
print("归档条目:", added)
assert added == 1

# 生产桶扫描（frames_root 注入 = 本次探针根，与生产缺省根同形）
buckets = rd.load_evidence_buckets(references_dir=os.path.join(root, "_none"),
                                   frames_root=root)
print("训练侧读到条目:", len(buckets), [b["category"] for b in buckets])
assert len(buckets) == 1 and buckets[0]["category"] == "2-judge-found"
b = buckets[0]
assert b["source"] == "production", b["source"]
assert b["linked"], "帧在盘"
assert b["episode"] == "2026-10-15-daily-china-20261015", b["episode"]
assert b["judge"] == "翻转90" and "横躺" in (b["why"] or ""), b
assert b["sha256"] == real_sha, "哈希与真实帧一致"

# 账实对账（真实帧字节）
res = rd.verify_evidence_hashes(buckets)
print("对账:", res[0]["state"])
assert res[0]["ok"] and res[0]["state"] == "ok"

# 训练链消费：row_material 按 (date,slug,shot) 链接到桶帧
row = {"date": "2026-10-15", "slug": "daily-china-20261015", "shot": "shot03",
       "user_flagged": True, "vision_verdict": "正常", "judge_verdict": "翻转90"}
mats = gt.row_material(row, references_dir=os.path.join(root, "_none"), date_root=root, frames_root=root)
kinds = [m["kind"] for m in mats]
print("训练素材 kinds:", kinds)
ev_mats = [m for m in mats if m["kind"] == "evidence"]
assert ev_mats, "证据桶帧必须进训练候选（AC1）"
assert ev_mats[0]["category"] == "2-judge-found" and ev_mats[0]["round"] == "round0"
assert os.path.exists(ev_mats[0]["path"])
print("桶帧路径:", ev_mats[0]["path"])
print("PROBE OK —— 生产桶 → 训练读取 → row_material 全链在真实帧上成立")

shutil.rmtree(root, ignore_errors=True)
