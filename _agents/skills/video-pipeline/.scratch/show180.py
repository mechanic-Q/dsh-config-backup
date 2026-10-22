# -*- coding: utf-8 -*-
import io, json, sys
b = json.load(io.open(".scratch/m180.json", encoding="utf-8"))["body"]
print("LEN", len(b))
i = b.find("Decisions")
print("IDX", i)
print("---TAIL---")
print(b[-1500:] if i < 0 else b[i-100:])
