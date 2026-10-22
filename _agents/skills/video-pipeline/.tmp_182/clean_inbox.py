# -*- coding: utf-8 -*-
import io
P = "procedures/_inbox.md"
lines = io.open(P, encoding="utf-8").read().splitlines(True)
keep = [l for l in lines if "#182" not in l or "探针绝不在真实生产根下造日期目录" not in l]
print("removed", len(lines) - len(keep), "line(s)")
io.open(P, "w", encoding="utf-8").write("".join(keep))
