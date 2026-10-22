# -*- coding: utf-8 -*-
import io, subprocess, sys
P = "scripts/run_pipeline.py"
orig = io.open(P, encoding="utf-8").read()
io.open(P + ".bak", "w", encoding="utf-8").write(orig)

# 变异：归档函数第一行直接 return（等价于「没实现」）
marker = '''        ev_dir = os.path.join(frames, "evidence")
        mf_path = os.path.join(ev_dir, "manifest.json")
        kind_cat = {"confirmed": "1-confirmed", "judge_found": "2-judge-found"}'''
assert orig.count(marker) == 1
mutated = orig.replace(marker, '''        return 0  # MUTATION
        ev_dir = os.path.join(frames, "evidence")
        mf_path = os.path.join(ev_dir, "manifest.json")
        kind_cat = {"confirmed": "1-confirmed", "judge_found": "2-judge-found"}''')
io.open(P, "w", encoding="utf-8").write(mutated)

p = subprocess.run([sys.executable, "scripts/test_evidence_production.py"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=300)
io.open(P, "w", encoding="utf-8").write(orig)
import os; os.remove(P + ".bak")
print("变异1 rc=", p.returncode)
tail = (p.stdout or "")[-1200:]
print(tail)
