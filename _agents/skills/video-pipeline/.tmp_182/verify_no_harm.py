# -*- coding: utf-8 -*-
"""核实：清理 E:\每日新中国\2026-10-20 是否误删了今天真实的生产目录。
今天是 2026-10-20 17:18。若今天跑过生产，该目录原本应存在。
"""
import os, subprocess, datetime

root = r"E:\每日新中国"
print("== 1. 2026-10-20 目录现状 ==")
p = os.path.join(root, "2026-10-20")
print(p, "存在=", os.path.isdir(p))

print("\n== 2. 回收站里是否有 2026-10-20 痕迹 ==")
try:
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$sh=New-Object -ComObject Shell.Application;"
         "$rb=$sh.NameSpace(10);"
         "$rb.Items() | Where-Object {$_.Name -like '*2026-10-20*' -or $_.Name -like '*_182verify*'} | "
         "Select-Object -First 10 Name, Path | Format-List"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    print((ps.stdout or "").strip()[:1500] or "（回收站无匹配项）")
except Exception as e:
    print("回收站查询失败:", e)

print("\n== 3. E: 盘 logs 里今天的记录 ==")
lg = os.path.join(root, "logs")
if os.path.isdir(lg):
    files = sorted(os.listdir(lg))
    today = [f for f in files if "2026-10-20" in f or "20261020" in f]
    print("logs 目录最近 8 项:", files[-8:])
    print("今天相关:", today)
else:
    print("logs 目录不存在")
