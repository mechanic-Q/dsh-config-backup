# -*- coding: utf-8 -*-
import io
P = "scripts/test_evidence_production.py"
s = io.open(P, encoding="utf-8").read()

old = '''check(after_row["user_flagged_source"] == "human-flag", "来源保留")'''
new = '''check(after_row["user_flagged_source"] == before["user_flagged_source"],
      "来源保留（%s）" % after_row["user_flagged_source"])'''
assert s.count(old) == 1
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
print("fixed")
