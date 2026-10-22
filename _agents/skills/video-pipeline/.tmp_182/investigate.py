# -*- coding: utf-8 -*-
"""事故排查：E:\每日新中国\2026-10-20 是我建的空目录，还是今天真实的生产期次？
证据面：
  A. 数据集里有没有 2026-10-20 的期次（跑过生产就会落行）
  B. driver 台账/标记里有没有今天的
  C. E: 盘其它位置有没有 2026-10-20 的引用
  D. 我探针建的目录结构 vs 生产真实结构
"""
import os, json, glob

root = r"E:\每日新中国"

print("== A. 数据集里的期次 ==")
ds = "references/rotation-dataset.jsonl"
dates = {}
if os.path.exists(ds):
    for line in open(ds, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        dates[r.get("date")] = dates.get(r.get("date"), 0) + 1
for d in sorted(dates):
    print("  ", d, dates[d], "行")
print("  有 2026-10-20 吗:", "2026-10-20" in dates)

print("\n== B. E: 盘 2026-10-19（最近一期）结构 ==")
prev = os.path.join(root, "2026-10-19")
if os.path.isdir(prev):
    for dirpath, dirnames, filenames in os.walk(prev):
        depth = dirpath[len(prev):].count(os.sep)
        if depth <= 2:
            print("  " * depth + os.path.basename(dirpath) + "/")
            if depth <= 1:
                for f in filenames[:3]:
                    print("  " * (depth + 1) + f)
        if depth >= 2:
            dirnames[:] = []
else:
    print("  2026-10-19 不存在")

print("\n== C. 全盘找 2026-10-20 引用（E: 根下前两层）==")
hits = []
for d in os.listdir(root):
    if "2026-10-20" in d:
        hits.append(os.path.join(root, d))
print("  目录名命中:", hits or "无")

print("\n== D. 探针建的目录结构（我已删，看父目录现状）==")
print("  E:\\每日新中国\\2026-10-20 存在=", os.path.isdir(os.path.join(root, "2026-10-20")))
print("  E:\\每日新中国\\2026-10-19 存在=", os.path.isdir(os.path.join(root, "2026-10-19")))
print("  E:\\每日新中国\\2026-10-17 存在=", os.path.isdir(os.path.join(root, "2026-10-17")))

print("\n== E. 最近修改的 E: 日期目录（按 mtime 倒序前 6）==")
ds_list = []
for d in os.listdir(root):
    p = os.path.join(root, d)
    if os.path.isdir(p) and d[:2] == "20":
        try:
            ds_list.append((os.path.getmtime(p), d))
        except OSError:
            pass
for mt, d in sorted(ds_list, reverse=True)[:6]:
    import datetime
    print("  ", d, datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S"))
