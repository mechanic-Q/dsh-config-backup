#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_platform_titles.py — 按 platform-title-playbook 为各平台定制标题/封面字/标签。

输入: 口播稿路径 + 输出 json 路径
输出: {"date", "bilibili": {title, cover_text, tags},
       "douyin": {title, cover_text, tags}, "shipinhao": {title, cover_text, tags}}

手册: references/platform-title-playbook.md（同目录 repo 副本，人工可读）
原则: 标题是「翻译」不是复制——B站信息摘要、抖音点击钩子、视频号转发理由；
      封面大字 6-10 字且与标题互补不重复。
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import llm_json  # noqa: E402

PLAYBOOK = os.path.join(HERE, "..", "references", "platform-title-playbook.md")

TITLES_SCHEMA = {
    "type": "object",
    "required": ["bilibili", "douyin", "kuaishou", "toutiao", "shipinhao"],
    "properties": {
        "bilibili": {
            "type": "object", "required": ["title", "cover_text", "tags"],
            "properties": {"title": {"type": "string"}, "cover_text": {"type": "string"},
                           "tags": {"type": "string"}}},
        "douyin": {
            "type": "object", "required": ["title", "cover_text", "tags"],
            "properties": {"title": {"type": "string"}, "cover_text": {"type": "string"},
                           "tags": {"type": "string"}}},
        "kuaishou": {
            "type": "object", "required": ["title", "cover_text", "tags"],
            "properties": {"title": {"type": "string"}, "cover_text": {"type": "string"},
                           "tags": {"type": "string"}}},
        "toutiao": {
            "type": "object", "required": ["title", "cover_text", "tags"],
            "properties": {"title": {"type": "string"}, "cover_text": {"type": "string"},
                           "tags": {"type": "string"}}},
        "shipinhao": {
            "type": "object", "required": ["title", "cover_text", "tags"],
            "properties": {"title": {"type": "string"}, "cover_text": {"type": "string"},
                           "tags": {"type": "string"}}},
    },
}

SYSTEM = (
    "你是短视频多平台发行策划。同一支新闻简报视频在不同平台的标题是「翻译」不是复制。"
    "严格遵守各平台公式与字数红线，禁止任何两个平台输出相同标题。"
    "封面大字（cover_text）6-10 个字，与该平台标题互补不重复（标题里出现过的主谓短语不要再抄一遍），"
    "口语化、有冲击力，可含空格分两段（如「新产业 新机会」）。"
    "抖音封面字避免政策通报腔；快手封面字大白话、讲给普通人听；"
    "头条封面字克制、可含数字；视频号封面字正面、让转发的人有面子。\n"
    "输出 JSON：{\"bilibili\": {\"title\", \"cover_text\", \"tags\"}, \"douyin\": {...}, "
    "\"kuaishou\": {...}, \"toutiao\": {...}, \"shipinhao\": {...}}，tags 为逗号分隔字符串。"
)


def _news_digest(script_path):
    """概述 md → 新闻标题清单（给 LLM 三要素提炼用）。"""
    txt = open(script_path, encoding="utf-8").read()
    items = [m.group(1).strip() for m in re.finditer(r"###\s+\[.+?\]\s+(.+?)\n", txt)]
    items = [t for t in items if "当日无真实报道" not in t and "栏目留空" not in t]
    return "\n".join(f"- {t}" for t in items[:12]) or txt[:800]


def gen(script_path, out_json, date, model=None):
    playbook = open(PLAYBOOK, encoding="utf-8").read() if os.path.exists(PLAYBOOK) else ""
    digest = _news_digest(script_path)
    user = (f"日期: {date}  系列名: 每日新中国（中国正面新闻简报）\n\n"
            f"今日新闻条目:\n{digest}\n\n"
            f"=== 平台标题手册（必须遵守）===\n{playbook}\n"
            f"=== 任务 ===\n为 B站(bilibili)/抖音(douyin)/快手(kuaishou)/今日头条(toutiao)/视频号(shipinhao) "
            f"各拟一版：\n"
            f"title（B站≤80字带系列名日期；抖音≤55字且前13字是钩子；快手≤40字大白话+实际利益；"
            f"头条≤30字事件+影响面，数字开头加分，禁夸张词；视频号≤30字转发理由）、\n"
            f"cover_text（6-10字封面大字，与标题互补）、tags（逗号分隔字符串。"
            f"B站必须正好 10 个：4 个活动流量标签（哔哩哔哩开学季,开学季,创作激励,涨粉计划，换季需更新）"
            f"+ 6 个内容话题标签（必含 国际局势,国防 + 从今日新闻提炼的 4 个）；"
            f"抖音 3-5 个避时政军事；快手/头条/视频号 3-5 个。")
    obj = llm_json.llm_call_json(SYSTEM, user, TITLES_SCHEMA,
                                 model=model or os.environ.get("LLM_MODEL", "strata"), budget=1600)
    if not obj or not all(k in obj for k in ("bilibili", "douyin", "kuaishou", "toutiao", "shipinhao")):
        raise RuntimeError(f"平台标题 LLM 输出不全: {str(obj)[:200]}")
    obj["date"] = date
    # 硬约束后检：字数红线超了就截断（LLM 偶发超限）
    limits = {"bilibili": 80, "douyin": 55, "kuaishou": 40, "toutiao": 30, "shipinhao": 30}
    for plat, lim in limits.items():
        t = obj[plat].get("title", "").strip()
        if len(t) > lim:
            obj[plat]["title"] = t[:lim]
    # 已有产物合并：保留已审定平台（确认门流程中不 churn），仅补缺失平台
    if os.path.exists(out_json) and not os.environ.get("PLAT_TITLES_FORCE"):
        try:
            old = json.load(open(out_json, encoding="utf-8"))
            for plat, v in old.items():
                if plat in ("bilibili", "douyin", "kuaishou", "toutiao", "shipinhao") and v:
                    obj[plat] = v
        except Exception:
            pass
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    json.dump(obj, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return obj


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="按平台手册生成定制标题/封面字/标签")
    ap.add_argument("script", help="口播稿路径")
    ap.add_argument("out_json", help="输出 json 路径（09_封面/平台标题.json）")
    ap.add_argument("--date", default=None)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", a.script)
    date = a.date or (m.group(1) if m else __import__("time").strftime("%Y-%m-%d"))
    pack = gen(a.script, a.out_json, date, a.model)
    for plat in ("bilibili", "douyin", "kuaishou", "toutiao", "shipinhao"):
        print(f"[{plat}] title: {pack[plat]['title']}")
        print(f"[{plat}] cover : {pack[plat]['cover_text']}  tags: {pack[plat]['tags']}")
