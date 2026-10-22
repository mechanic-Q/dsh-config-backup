# -*- coding: utf-8 -*-
import io
P = "scripts/test_evidence_production.py"
s = io.open(P, encoding="utf-8").read()

old = '''print("\\n%s\\n%d 节全部通过：%s" % ("=" * 40, len(SECTIONS), "、".join(SECTIONS)))'''
new = '''# ── 10. 真 merge 产出的 judge_block 能分类（不是手工造字典）──────────
section("真 dsh_judge.merge 的 judge_block 分类")

import dsh_judge as dj   # noqa: E402

# 真 merge：本地正常 × 判官判翻 → judge_found；本地翻 × 判官判翻 → confirmed
local = {"shot01": "翻转", "shot02": "正常", "shot03": "正常"}
items = [{"sid": "shot01", "judge": "翻转90", "conf": 0.95, "why": "横躺"},
         {"sid": "shot02", "judge": "翻转180", "conf": 0.97, "why": "旗帜横倒"},
         {"sid": "shot03", "judge": "正常", "conf": 0.9, "why": "直立"}]
merged = dj.merge(local, items, round_tag="round0")
real_block = {"ok": True, "round": "round0",
              "confirmed": merged["confirmed"],
              "judge_found": merged["judge_found"],
              "local_false": merged["local_false"],
              "judge_normal": merged["judge_normal"],
              "rows": merged["rows"]}
check(real_block["confirmed"] == ["shot01"], real_block["confirmed"])
check(real_block["judge_found"] == ["shot02"], real_block["judge_found"])
# 真 rows 必须带 kind（分类依据），否则生产路径永远分不了类
kinds = {r["sid"]: r["kind"] for r in real_block["rows"]}
check(kinds.get("shot01") == "confirmed" and kinds.get("shot02") == "judge_found",
      "真 merge rows 带 kind: %s" % kinds)

tmp_real = _mktemp(".tmp_182m_")
slug_real = os.path.join(tmp_real, "2026-10-20", "video", "每日新中国b站", "流水线",
                         "daily-china-20261020")
frames_real = os.path.join(slug_real, "_frames")
ev_dir = os.path.join(frames_real, "evidence", "round0")
os.makedirs(ev_dir, exist_ok=True)
f1 = _mk_jpg(os.path.join(ev_dir, "shot01.jpg"), b"\\xff\\xd8c1")
f2 = _mk_jpg(os.path.join(ev_dir, "shot02.jpg"), b"\\xff\\xd8c2")
d_real = Driver.__new__(Driver)
d_real.slug = slug_real
d_real.date = "2026-10-20"
d_real.notify = lambda *a, **k: None
ev_rows = [{"sid": "shot01", "round": "round0", "frame": f1, "verdict": "翻转"},
           {"sid": "shot02", "round": "round0", "frame": f2, "verdict": "翻转"}]
n = d_real._archive_evidence_buckets(ev_rows, frames_real, real_block)
check(n == 2, "真 judge_block 归档 2 条: %s" % n)
mfr = json.load(open(os.path.join(frames_real, "evidence", "manifest.json"),
                     encoding="utf-8"))
by_cat = {e["sid"]: e["category"] for e in mfr["evidence"]}
check(by_cat == {"shot01": "1-confirmed", "shot02": "2-judge-found"},
      "真 merge 四象限 → 分类正确: %s" % by_cat)
# judge_normal 段不归档（一致段无素材价值）
check("shot03" not in by_cat, "judge_normal 段不归档")


print("\\n%s\\n%d 节全部通过：%s" % ("=" * 40, len(SECTIONS), "、".join(SECTIONS)))'''
assert s.count(old) == 1
s = s.replace(old, new)

# 清理列表加新临时目录
old2 = '''for _d in (tmp, tmp_fail, refs):'''
new2 = '''for _d in (tmp, tmp_fail, refs, tmp_real):'''
assert s.count(old2) == 1
s = s.replace(old2, new2)
io.open(P, "w", encoding="utf-8").write(s)
print("added section 10")
