# -*- coding: utf-8 -*-
import io
P = "scripts/rotation_groundtruth.py"
s = io.open(P, encoding="utf-8").read()
old = '''        export(a.out, a.synthetic, a.dataset, dry_run=a.dry_run)'''
new = '''        export(a.out, a.synthetic, a.dataset, dry_run=a.dry_run,
               frames_root=a.frames_root)'''
assert s.count(old) == 1
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
import py_compile; py_compile.compile(P, doraise=True)
print("cli wired")
