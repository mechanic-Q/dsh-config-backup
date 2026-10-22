# -*- coding: utf-8 -*-
import io
P = "scripts/rotation_groundtruth.py"
s = io.open(P, encoding="utf-8").read()

# 1) _export_rows：带 ground_truth_verdict + frames_root 透传
old = '''def _export_rows(pos, neg, synthetic=False, references_dir=None, date_root=None):'''
new = '''def _export_rows(pos, neg, synthetic=False, references_dir=None, date_root=None,
                 frames_root=None):'''
assert s.count(old) == 1, "sig"
s = s.replace(old, new)

old = '''        for m in row_material(r, references_dir, date_root):
            yield {"label": "翻转" if r.get("user_flagged") else "正常",
                   "frames": [os.path.abspath(m["path"])], "synthetic": False,
                   "date": r["date"], "shot": r["shot"],
                   "source": r.get("user_flagged_source"),
                   "vision_verdict": r.get("vision_verdict"),
                   "judge_verdict": r.get("judge_verdict"),'''
new = '''        for m in row_material(r, references_dir, date_root, frames_root):
            yield {"label": "翻转" if r.get("user_flagged") else "正常",
                   "frames": [os.path.abspath(m["path"])], "synthetic": False,
                   "date": r["date"], "shot": r["shot"],
                   "source": r.get("user_flagged_source"),
                   # 人工最终判定（#182 第三件套）：布尔只说"人说过话"，
                   # 这条说"人说了什么"（正常/翻转/翻转90/翻转180/倾斜）。
                   "ground_truth_verdict": r.get("ground_truth_verdict"),
                   "vision_verdict": r.get("vision_verdict"),
                   "judge_verdict": r.get("judge_verdict"),'''
assert s.count(old) == 1, "row"
s = s.replace(old, new)

# 2) export 签名 + 调用
old = '''def export(out, synthetic=False, dataset=None, dry_run=False, references_dir=None,
           date_root=None):'''
new = '''def export(out, synthetic=False, dataset=None, dry_run=False, references_dir=None,
           date_root=None, frames_root=None):'''
assert s.count(old) == 1, "export sig"
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
print("patched 1")
