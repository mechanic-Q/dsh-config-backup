# -*- coding: utf-8 -*-
import io, subprocess, sys

def run_mutation(path, old, new, label):
    orig = io.open(path, encoding="utf-8").read()
    assert orig.count(old) == 1, label + " anchor"
    io.open(path, "w", encoding="utf-8").write(orig.replace(old, new))
    try:
        p = subprocess.run([sys.executable, "scripts/test_evidence_production.py"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        tail = (p.stdout or "").strip().splitlines()
        print("%s → rc=%s | %s" % (label, p.returncode, tail[-1][:100] if tail else ""))
    finally:
        io.open(path, "w", encoding="utf-8").write(orig)

# 变异7：export 不带 ground_truth_verdict
run_mutation("scripts/rotation_groundtruth.py",
             '                   "ground_truth_verdict": r.get("ground_truth_verdict"),',
             '                   # MUTATION removed', "变异7 export 缺第三件套")

# 变异8：row_material 不透传 frames_root（生产桶帧进不了训练）
run_mutation("scripts/rotation_groundtruth.py",
             '        for m in row_material(r, references_dir, date_root, frames_root):',
             '        for m in row_material(r, references_dir, date_root):',
             "变异8 row_material 丢 frames_root")
