# -*- coding: utf-8 -*-
import io, re
P = "scripts/test_evidence_production.py"
s = io.open(P, encoding="utf-8").read()
# 临时目录前缀改用 .gitignore 已覆盖的 .tmp_*
s = s.replace('_mktemp("t182_")', '_mktemp(".tmp_182t_")')
s = s.replace('_mktemp("t182f_")', '_mktemp(".tmp_182f_")')
s = s.replace('_mktemp("t182r_")', '_mktemp(".tmp_182r_")')
s = s.replace('_mktemp("t182n_")', '_mktemp(".tmp_182n_")')
io.open(P, "w", encoding="utf-8").write(s)
print("prefix switched")
