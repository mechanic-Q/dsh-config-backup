# -*- coding: utf-8 -*-
import io
P = "scripts/test_evidence_production.py"
s = io.open(P, encoding="utf-8").read()

old = '''print("\\n%s\\n%d 节全部通过：%s" % ("=" * 40, len(SECTIONS), "、".join(SECTIONS)))'''
new = '''# ── 9. export 带第三件套 + 生产桶帧可导出 ──────────────────────────────
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


print("\\n%s\\n%d 节全部通过：%s" % ("=" * 40, len(SECTIONS), "、".join(SECTIONS)))'''
assert s.count(old) == 1
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
print("added section 9")
