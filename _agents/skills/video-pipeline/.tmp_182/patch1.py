# -*- coding: utf-8 -*-
import io
P = "scripts/rotation_dataset.py"
src = io.open(P, encoding="utf-8").read()

old = """import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))"""
new = """import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # 邻居模块单一口径（rotation_groundtruth 同款）

import hostpath            # 跨侧路径换算单一事实源（#143）：生产证据根要用"""
assert src.count(old) == 1, "import anchor not unique"
src = src.replace(old, new)
io.open(P, "w", encoding="utf-8").write(src)
print("patch1 ok")
