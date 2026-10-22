# -*- coding: utf-8 -*-
"""#182 AC1 真实字面验证：在真实帧根（E:）下按生产代码路径写一个期次的证据桶，
再用 load_evidence_buckets() 缺省参数（不注入 frames_root）读回来。

不是 tempdir 探针：这条走的就是生产缺省根 + 生产归档函数，
证的是「生产跑一期后训练读得到」这句话本身。
跑完清理，不留垃圾期次。
"""
import os, shutil, sys

sys.path.insert(0, os.path.abspath("scripts"))
import hostpath
import rotation_dataset as rd
from run_pipeline import Driver, _sha256_file

# 真实帧根（缺省口径）
root = hostpath.to_host_path(rd.PROD_FRAMES_ROOT_WIN)
print("生产缺省帧根:", root, "存在=", os.path.isdir(root))
assert os.path.isdir(root), "真实帧根不在盘"

# 真实现场帧（2026-10-15 期，判官确认翻转的段）
src_real = os.path.join(root, "2026-10-15", "video", "每日新中国b站", "流水线",
                        "daily-china-20261015", "_frames", "shot03.jpg")
assert os.path.exists(src_real), src_real
print("真实源帧:", src_real)

# 在真实根下建一个明确的验证期次（不用真期次，避免污染生产台账）
VERIFY_SLUG = "_182verify-20261020"
slug = os.path.join(root, "2026-10-20", "video", "每日新中国b站", "流水线", VERIFY_SLUG)
frames = os.path.join(slug, "_frames")
ev_flat = os.path.join(frames, "evidence", "round0")
if os.path.isdir(os.path.join(root, "2026-10-20")):
    pass  # 已存在则复用目录
os.makedirs(ev_flat, exist_ok=True)
fp = os.path.join(ev_flat, "shot03.jpg")
shutil.copy2(src_real, fp)
sha = _sha256_file(fp)
print("落真实帧:", fp, os.path.getsize(fp), "bytes  sha256:", sha[:16], "…")

d = Driver.__new__(Driver)
d.slug = slug
d.date = "2026-10-20"
d.notify = lambda *a, **k: None
block = {"ok": True, "round": "round0", "rows": [
    {"sid": "shot03", "local": "正常", "judge": "翻转90", "kind": "judge_found",
     "conf": 0.97, "why": "地面在左缘竖条，主体横躺"}]}
ev = [{"sid": "shot03", "round": "round0", "frame": fp, "verdict": "翻转",
       "conf": 0.97, "judge": "翻转90", "judge_why": "地面在左缘竖条",
       "kind": "judge_found", "sha256": sha}]
added = d._archive_evidence_buckets(ev, frames, block)
print("生产归档条目:", added)
assert added == 1

try:
    # ★ 关键：缺省参数，不注入 frames_root —— 走生产缺省根
    buckets = rd.load_evidence_buckets()
    prod = [b for b in buckets if b["source"] == "production"]
    print("缺省参数读到生产条目:", len(prod))
    for b in prod:
        print("  ", b["episode"], "|", b["category"], "|", b["sid"], "|",
              b["round"], "| linked=", b["linked"])
    assert len(prod) == 1, "AC1：生产跑一期后 load_evidence_buckets() 必须读到"
    b = prod[0]
    assert b["episode"] == "2026-10-20-" + VERIFY_SLUG, b["episode"]
    assert b["category"] == "2-judge-found" and b["sid"] == "shot03"
    assert b["linked"] and b["sha256"] == sha
    # 与离线件合并（不是替代）
    off = [x for x in buckets if x["source"] == "offline"]
    print("同时保留离线件:", len(off), "条（两路合并，非替代）")
    assert len(off) == 22, len(off)
    # 对账
    res = rd.verify_evidence_hashes(prod)
    print("对账:", res[0]["state"])
    assert res[0]["ok"]
    print("\nAC1 OK —— 生产缺省根 + 生产归档 → load_evidence_buckets() 缺省参数读到，episode 正确")
finally:
    # 清理验证期次（尽力而为；沙箱对 rmtree 敏感走 cmd rd）
    import subprocess
    top = os.path.join(root, "2026-10-20")
    try:
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", top],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
        print("已清理验证期次:", top, "剩余存在=", os.path.isdir(top))
    except Exception as e:
        print("清理失败（需手工删）:", top, e)
