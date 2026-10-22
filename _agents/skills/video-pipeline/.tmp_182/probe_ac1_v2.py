# -*- coding: utf-8 -*-
"""#182 AC1 真实字面验证（安全版 v2）。

上一版 v1 的错：在真实生产根下用 makedirs 建了 E:\每日新中国\2026-10-20\
（日期目录本身），清理时 rd /s 删掉了那个日期层级。核实无真实损失（数据集
无该期次、最新真实期 10-19、全盘无引用），但**探针不该在生产根下造日期目录**。
v2 只在 <root>\_182probe\ 一个自建目录里造期次，清理只删这一个，绝不碰
任何日期目录。

验证内容不变：生产缺省根 + 生产归档 → load_evidence_buckets() 缺省参数读到。
"""
import os, shutil, subprocess, sys

sys.path.insert(0, os.path.abspath("scripts"))
import hostpath
import rotation_dataset as rd
from run_pipeline import Driver, _sha256_file

root = hostpath.to_host_path(rd.PROD_FRAMES_ROOT_WIN)
assert os.path.isdir(root), "真实帧根不在盘: %s" % root
print("生产缺省帧根:", root)

# ★ 自建隔离根（第一层就是 _182probe，glob 的 * 命中它，不会碰日期目录）
PROBE_ROOT = os.path.join(root, "_182probe")
assert "_182probe" in PROBE_ROOT
src_real = os.path.join(root, "2026-10-15", "video", "每日新中国b站", "流水线",
                        "daily-china-20261015", "_frames", "shot03.jpg")
assert os.path.exists(src_real), src_real
print("真实源帧:", src_real)

slug = os.path.join(PROBE_ROOT, "video", "每日新中国b站", "流水线", "_182verify")
frames = os.path.join(slug, "_frames")
ev_flat = os.path.join(frames, "evidence", "round0")
try:
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
        {"sid": "shot03", "local": "正常", "judge": "翻转90",
         "kind": "judge_found", "conf": 0.97, "why": "地面在左缘竖条，主体横躺"}]}
    ev = [{"sid": "shot03", "round": "round0", "frame": fp, "verdict": "翻转",
           "conf": 0.97, "judge": "翻转90", "judge_why": "地面在左缘竖条",
           "kind": "judge_found", "sha256": sha}]
    added = d._archive_evidence_buckets(ev, frames, block)
    print("生产归档条目:", added)
    assert added == 1

    # ★ 缺省参数，不注入 frames_root —— 走生产缺省根
    buckets = rd.load_evidence_buckets()
    prod = [b for b in buckets if b["source"] == "production"]
    print("缺省参数读到生产条目:", len(prod))
    for b in prod:
        print("  ", b["episode"], "|", b["category"], "|", b["sid"], "|",
              b["round"], "| linked=", b["linked"])
    assert len(prod) == 1, "AC1：生产跑一期后 load_evidence_buckets() 必须读到"
    b = prod[0]
    assert b["episode"] == "2026-10-20-_182verify", b["episode"]
    assert b["category"] == "2-judge-found" and b["sid"] == "shot03"
    assert b["linked"] and b["sha256"] == sha
    off = [x for x in buckets if x["source"] == "offline"]
    print("同时保留离线件:", len(off), "条（两路合并，非替代）")
    assert len(off) == 22, len(off)
    res = rd.verify_evidence_hashes(prod)
    print("对账:", res[0]["state"])
    assert res[0]["ok"]
    print("\nAC1 OK —— 生产缺省根 + 生产归档 → load_evidence_buckets() 缺省参数读到，episode 正确")
finally:
    # 只删自建隔离根；先核实目标确为 _182probe
    assert os.path.basename(PROBE_ROOT) == "_182probe"
    if os.path.isdir(PROBE_ROOT):
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", PROBE_ROOT],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
    print("已清理自建探针根:", PROBE_ROOT, "剩余=", os.path.isdir(PROBE_ROOT))
    # 复查：日期目录一个没动
    left = sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)) and d[:2] == "20")
    print("日期目录仍在:", left[-3:], "（共 %d 个）" % len(left))
    assert os.path.isdir(os.path.join(root, "2026-10-19")), "10-19 必须还在"
