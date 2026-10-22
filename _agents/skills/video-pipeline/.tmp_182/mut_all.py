# -*- coding: utf-8 -*-
import io, subprocess, sys, os

def run_mutation(path, old, new, test, label):
    orig = io.open(path, encoding="utf-8").read()
    assert orig.count(old) == 1, label + " anchor"
    io.open(path, "w", encoding="utf-8").write(orig.replace(old, new))
    try:
        p = subprocess.run([sys.executable, test], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        rc = p.returncode
        tail = (p.stdout or "").strip().splitlines()
        print("%s → rc=%s | %s" % (label, rc, tail[-1][:110] if tail else ""))
    finally:
        io.open(path, "w", encoding="utf-8").write(orig)

# 变异2：sha256_file 返回固定串（账不再随内容变）
run_mutation("scripts/rotation_dataset.py",
             '        actual = sha256_file(fp)',
             '        actual = "deadbeef" * 8  # MUTATION',
             "scripts/test_evidence_production.py", "变异2 sha256 校验失效")

# 变异3：append_records 去掉人字段搬运
run_mutation("scripts/rotation_dataset.py",
             '''        if old_row:
            for hk in _HUMAN_KEYS:
                if r.get(hk) is None and old_row.get(hk) is not None:
                    r[hk] = old_row[hk]''',
             '''        if False:  # MUTATION
            pass''',
             "scripts/test_evidence_production.py", "变异3 人字段保护失效")

# 变异4：生产扫描不读生产桶（只读离线）
run_mutation("scripts/rotation_dataset.py",
             '    for mf in iter_production_evidence_manifests(frames_root):',
             '    for mf in []:  # MUTATION',
             "scripts/test_evidence_production.py", "变异4 生产扫描失效")

# 变异5：ground_truth_verdict 不落
run_mutation("scripts/rotation_dataset.py",
             '''            gv = _norm_gt_verdict(v, want[k])
            if gv:
                r["ground_truth_verdict"] = gv''',
             '''            gv = None  # MUTATION''',
             "scripts/test_evidence_production.py", "变异5 verdict 不落")

# 变异6：分类映射错位（judge_found 进 confirmed）
run_mutation("scripts/run_pipeline.py",
             'kind_cat = {"confirmed": "1-confirmed", "judge_found": "2-judge-found"}',
             'kind_cat = {"confirmed": "2-judge-found", "judge_found": "1-confirmed"}',
             "scripts/test_evidence_production.py", "变异6 分类映射错位")
