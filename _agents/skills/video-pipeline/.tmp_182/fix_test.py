# -*- coding: utf-8 -*-
import io
P = "scripts/test_evidence_production.py"
s = io.open(P, encoding="utf-8").read()

# 用真实期次布局建生产桶（<date>/video/每日新中国b站/流水线/<slug>），与生产扫描口径一致
old = '''tmp = _mktemp("t182_")
slug = os.path.join(tmp, "daily-china-20261020")
frames = os.path.join(slug, "_frames")'''
new = '''tmp = _mktemp("t182_")
# 真实期次布局（与 rotation_groundtruth.frames_of / 生产扫描 glob 同形）：
# <root>/<date>/video/每日新中国b站/流水线/<slug>/_frames
slug = os.path.join(tmp, "2026-10-20", "video", "每日新中国b站", "流水线",
                    "daily-china-20261020")
frames = os.path.join(slug, "_frames")'''
assert s.count(old) == 1
s = s.replace(old, new)

# fail-open 那段也放在真实布局里（避免路径形状不一致干扰判断）
old2 = '''tmp_fail = _mktemp("t182f_")
frames_fail = os.path.join(tmp_fail, "_frames")'''
new2 = '''tmp_fail = _mktemp("t182f_")
frames_fail = os.path.join(tmp_fail, "2026-10-20", "video", "每日新中国b站", "流水线",
                           "daily-china-20261020", "_frames")'''
assert s.count(old2) == 1
s = s.replace(old2, new2)
io.open(P, "w", encoding="utf-8").write(s)
print("test layout fixed")
