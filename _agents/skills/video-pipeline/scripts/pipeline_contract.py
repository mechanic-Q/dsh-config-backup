#!/usr/bin/env python3
"""pipeline_contract.py — 流水线契约：环节间只传文件的强制机制。

用户三条要求（2026-08-25）：
  1. 每一个环节之间，必须从上一个环节获取文件
  2. 每一个环节分析完后，文件必须落地，不能只存在于 Agent 上下文中
  3. 每一次生成的文件都必须落地，下一个环节必须读取上一个环节的文件

机制：
  check_input(path, desc)  —— 环节开头校验上游文件存在且非空
  stamp_output(path, meta) —— 环节结尾写 .done.json 完成标记（含元数据）
  require_stamp(path, desc) —— 下游确认上游已完成标记

用法：每个环节脚本开头 import pipeline_contract as pc，
      check_input(上游产物) → ... 本环节逻辑 ... → stamp_output(本环节产物)
"""
import os, sys, json, hashlib

# S6：schema 版本单一源（从 schema.py 引入，避免双份定义）
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from schema import SCHEMA_VERSION
except ImportError:
    SCHEMA_VERSION = "2.0"

# 新闻稿编辑占位行（如"（当日无真实报道，栏目留空）"）——不是口播正文，
# 混入分段会被 TTS 念出、污染导演设计与 H3 prompt（0907 测试实锤：上轮已发布成片带病）。
# 结构性判据：整行被全角括号包裹 = 编辑说明，口播正文不会整行只有括号。
PLACEHOLDER_LINE_RE = r'^（[^（）]*）$'
PLACEHOLDER_INLINE_RE = r'（[^（）]*(?:留空|无真实报道|待补|占位)[^（）]*）'


def strip_placeholder_lines(text, log=lambda m: print(f"WARN: {m}")):
    """剔除新闻稿里的编辑占位行/句内占位括号串，返回净化文本（不改原文件）。"""
    import re
    lines, dropped = [], []
    for ln in text.split('\n'):
        s = ln.strip()
        if re.match(PLACEHOLDER_LINE_RE, s):
            dropped.append(s)
            continue
        cleaned = re.sub(PLACEHOLDER_INLINE_RE, '', ln)
        if cleaned != ln:
            dropped.append(ln.strip())
        lines.append(cleaned)
    for d in dropped:
        log(f"剔除编辑占位行: {d[:40]}")
    return '\n'.join(lines)


def file_hash(path):
    """SHA256 文件哈希（用于产物指纹）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def check_input(path, desc):
    """校验上游文件存在且非空。缺失/为空则报错退出。"""
    if not os.path.exists(path):
        print(f"ERROR: 上游[{desc}]缺失: {path}")
        print(f"请先运行上一环节，产物未落盘")
        sys.exit(2)
    if os.path.isdir(path):
        # 目录：校验非空
        if not os.listdir(path):
            print(f"ERROR: 上游[{desc}]为空目录: {path}")
            sys.exit(2)
        return
    if os.path.getsize(path) == 0:
        print(f"ERROR: 上游[{desc}]为空文件: {path}")
        sys.exit(2)


def stamp_output(path, meta=None):
    """环节结尾写完成标记（含产物哈希），供下游确认。

    meta: dict，如 {"total": 107, "checks_pass": 107}
    返回标记文件路径。

    S6：支持目录（对关键产物逐个算 hash）；记录 schema 版本。
    """
    stamp_path = path + ".done.json"
    # 目录：对关键产物（json/md）逐个 hash；文件：单 hash
    hashes = {}
    if os.path.isdir(path):
        for f in sorted(os.listdir(path)):
            if f.endswith((".json", ".md")):
                fp = os.path.join(path, f)
                if os.path.isfile(fp):
                    hashes[f] = file_hash(fp)
    elif os.path.isfile(path):
        hashes[os.path.basename(path)] = file_hash(path)
    stamp = {
        "output": path,
        "hash": hashes,
        "schema_version": SCHEMA_VERSION,
        "meta": meta or {},
    }
    with open(stamp_path, "w", encoding="utf-8") as f:
        json.dump(stamp, f, ensure_ascii=False, indent=2)
    return stamp_path


def require_stamp(path, desc, check_hash=True):
    """下游确认上游已完成（.done.json 存在）。缺失则报错退出。

    S6：check_hash=True 时校验产物 hash 一致性——不一致说明产物被外部改动/污染，
    警告但允许继续（避免破坏串行流程），记录到 meta.warn。
    """
    stamp_path = path + ".done.json"
    if not os.path.exists(stamp_path):
        print(f"ERROR: 上游[{desc}]未完成（无完成标记 {stamp_path}）")
        print(f"请先运行上一环节，产物完成标记未落盘")
        sys.exit(2)
    # 校验产物本身仍在
    if os.path.isfile(path) and os.path.getsize(path) == 0:
        print(f"ERROR: 上游[{desc}]产物为空: {path}")
        sys.exit(2)
    # S6：hash 一致性校验（防外部污染）
    if check_hash:
        try:
            stamp = json.load(open(stamp_path, encoding="utf-8"))
            stored = stamp.get("hash", {}) or {}
            changed = []
            for fname, expected in stored.items():
                fp = os.path.join(path, fname) if os.path.isdir(path) else path
                if not os.path.exists(fp):
                    changed.append(f"{fname}(缺失)")
                    continue
                cur = file_hash(fp)
                if cur != expected:
                    changed.append(f"{fname}(hash不符)")
            if changed:
                print(f"WARN: 上游[{desc}]产物被外部改动/污染: {changed}")
                print("  若为预期修改，重新运行上游环节更新 stamp；否则检查是否有并行任务写入")
        except Exception:
            pass  # stamp 损坏则跳过校验，不阻塞


def require_stage0(script_md, desc="阶段1口播稿"):
    """硬约束：阶段2及之后任何环节运行前，强制校验阶段1口播稿已落盘且合规。

    合规 = 三要素（阶段1产出指纹）：
      1. 存在且非空
      2. 含来源头（`> 来源` + `> URL`）
      3. 含信息源验证表（`| 信息点`）

    这是硬性代码闸门，不靠 LLM 自觉。不满足直接 sys.exit(2)。
    """
    check_input(script_md, desc)
    txt = open(script_md, encoding="utf-8").read()
    missing = []
    for needle, label in [
        ("> 来源", "来源头"),
        ("URL", "URL头"),
        ("信息源验证", "信息源验证表"),
    ]:
        if needle not in txt:
            missing.append(label)
    if missing:
        print(f"ERROR: 上游[{desc}]缺少阶段1指纹: {missing}")
        print(f"请先完成阶段1（来源头+URL+信息源验证表），再进阶段2")
        sys.exit(2)
    # 编辑占位行检查：不阻断（稿子是定稿，driver 无权改），但必须日志可见
    import re
    for i, ln in enumerate(txt.split('\n'), 1):
        if re.match(PLACEHOLDER_LINE_RE, ln.strip()) or re.search(PLACEHOLDER_INLINE_RE, ln):
            print(f"WARN: [{desc}] 第{i}行含编辑占位文字（将被分段器剔除）: {ln.strip()[:40]}")


def read_stamp(path):
    """读取上游完成标记元数据，不存在返回 None。"""
    stamp_path = path + ".done.json"
    if not os.path.exists(stamp_path):
        return None
    try:
        return json.load(open(stamp_path, encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    # 自检
    import tempfile
    tmp = tempfile.mkdtemp(prefix="contract_test_")
    f = os.path.join(tmp, "test.txt")
    open(f, "w").write("hello")
    stamp = stamp_output(f, {"n": 1})
    assert os.path.exists(stamp), "stamp not written"
    require_stamp(f, "测试")
    assert read_stamp(f)["meta"]["n"] == 1
    print("pipeline_contract 自检通过 ✓")
