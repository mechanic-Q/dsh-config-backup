# -*- coding: utf-8 -*-
import io
P = "scripts/test_evidence_production.py"
s = io.open(P, encoding="utf-8").read()

old = '''print("\\n%s\\n%d 节全部通过：%s" % ("=" * 40, len(SECTIONS), "、".join(SECTIONS)))'''
new = '''# ── 8. 训练链消费：row_material 读到生产桶帧 ──────────────────────────
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


print("\\n%s\\n%d 节全部通过：%s" % ("=" * 40, len(SECTIONS), "、".join(SECTIONS)))'''
assert s.count(old) == 1
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
print("added section 8")
