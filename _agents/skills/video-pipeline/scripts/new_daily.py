#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""new_daily.py — 读报单开工脚本：从当日《每日新中国》产物目录一键建项目 + 口播稿（含阶段1指纹）。

根治（2026-09-06）：直接读报模式仍要求口播稿带「来源头 / URL 头 / 信息源验证表」阶段1指纹，
3新闻_概述.md 原件没有 → 每天都得手工补齐+按铁律建 slug 目录（0905 首跑即因此在 s2 连败叫人）。
本脚本把这套手工步骤固化：

  python3 scripts/new_daily.py [YYYY-MM-DD] [--force]
  → /mnt/e/每日新中国/<日期>/video/每日新中国b站/流水线/daily-china-<YYYYMMDD>/00_口播稿/口播稿.md
  → 打印 driver 启动命令

来源头 = 报纸图文件名里的期号 + 第一条新闻 URL；验证表 = 1新闻_链接.md 全部条目；正文 = 3新闻_概述.md。
"""
import argparse
import glob
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_contract as pc

DATE_ROOT = "/mnt/e/每日新中国"
STYLE = "chinese-celestial-palace"


def parse_links(txt):
    """1新闻_链接.md → [(来源, 标题, URL)]"""
    items = []
    for m in re.finditer(r"###\s+\[(.+?)\]\s+(.+?)\n\s*URL[：:]\s*(\S+)", txt):
        items.append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
    return items


def build(date, force=False):
    day = os.path.join(DATE_ROOT, date)
    digest_fp = os.path.join(day, "3新闻_概述.md")
    links_fp = os.path.join(day, "1新闻_链接.md")
    for fp in (digest_fp, links_fp):
        if not os.path.exists(fp):
            sys.exit(f"ERROR: 缺文件 {fp}")
    papers = glob.glob(os.path.join(day, f"{date}_每日新中国_第*期.png"))
    issue = (re.search(r"第(\d+)期", os.path.basename(papers[0])).group(1) if papers else "?")
    if not papers:
        print("WARN: 当日报纸图未找到（driver s10 会再校验）")

    links = parse_links(open(links_fp, encoding="utf-8").read())
    if not links:
        sys.exit("ERROR: 1新闻_链接.md 未解析出任何条目")
    digest = open(digest_fp, encoding="utf-8").read()
    body = re.sub(r"^#\s+.*\n", "", digest, count=1).strip()  # 去掉首行标题，正文原样保留
    # 编辑占位行（"（当日无真实报道，栏目留空）"等）在源头剔除——进分段会被 TTS 念出
    body = pc.strip_placeholder_lines(body)

    slug = os.path.join(day, "video", "每日新中国b站", "流水线", f"daily-china-{date.replace('-', '')}")
    out = os.path.join(slug, "00_口播稿", "口播稿.md")
    if os.path.exists(out) and not force:
        print(f"已存在，跳过（--force 覆盖）: {out}")
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    table = "\n".join(f"| {t} | {s} | {u} |" for s, t, u in links)
    content = (
        f"# {date} 新闻概述\n\n"
        f"> 来源：每日新中国 {date} 第{issue}期（央视新闻/新华社/人民日报）\n"
        f"> URL：{links[0][2]}\n"
        f"> 日期：{date}\n"
        f"> 风格：{STYLE}\n\n"
        f"## 信息源验证记录\n\n| 信息点 | 来源 | URL |\n|---|---|---|\n{table}\n\n"
        f"## 口播稿\n\n{body}\n"
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"口播稿已生成（{len(links)} 条新闻，第{issue}期）: {out}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="读报单开工：建项目目录 + 带阶段1指纹的口播稿")
    ap.add_argument("date", nargs="?", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--force", action="store_true", help="覆盖已存在的口播稿")
    a = ap.parse_args()
    fp = build(a.date, a.force)
    here = os.path.dirname(os.path.abspath(__file__))
    print("\n下一步（driver 一条命令到发布；后台跑用 run_driver_bg.py，日志与退出码可追）：")
    print(f'  cd {os.path.dirname(here)} && python scripts/run_driver_bg.py --script "{fp}"')
    print("  前台跑（要盯输出时）：python scripts/run_pipeline.py --script "
          f'"{fp}"')
