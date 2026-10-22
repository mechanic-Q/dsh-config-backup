#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dataset_readonly.py — 旋转数据集写保护自检（#148，无框架，assert 直跑）。

背景：仓库里那份活数据集曾一个文件背三个身份——真值单一事实源 / 每期 append
的运行日志 / 测试夹具。夹具已抽成 scripts/fixtures/ 期次快照，写侧再加一道锁：
run_tests.py 给每个测试子进程注入 ROTATION_DATASET_READONLY=1，此时写活数据集
一律抛 DatasetReadOnlyError（显式传 tempfile 路径不受影响）。

覆盖：
1. 只读标记默认关闭（生产脚本 run_pipeline/t15_replay 行为不变）
2. 只读模式下 append_records / set_user_flag / write_rows 写活数据集全部被拒
3. 被拒时生产数据集字节不变（sha256 前后一致）
4. 只读模式下显式传 tempfile dataset_path 照常可写可读
5. 全仓 test_*.py 不得出现指向活数据集路径的字面量（照 test_strata_copychain 骨架）
6. 期次快照夹具在盘且行数正确（测试依赖它，缺了必须红）
"""
import glob
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rotation_dataset as rd

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def with_env(value):
    """临时设置/清除只读标记，返回还原函数。"""
    old = os.environ.get(rd.READONLY_ENV)

    def restore():
        if old is None:
            os.environ.pop(rd.READONLY_ENV, None)
        else:
            os.environ[rd.READONLY_ENV] = old
    if value is None:
        os.environ.pop(rd.READONLY_ENV, None)
    else:
        os.environ[rd.READONLY_ENV] = value
    return restore


def rejects(fn):
    """调用 fn 必须抛 DatasetReadOnlyError；返回异常文本。"""
    try:
        fn()
    except rd.DatasetReadOnlyError as e:
        return str(e)
    raise AssertionError("应当被拒却写成功了（生产数据集被污染）")


LIVE = rd.DATASET
assert os.path.exists(LIVE), f"生产数据集不在盘：{LIVE}"
before = sha256(LIVE)
row = {"date": "2026-10-18", "slug": "daily-china-20261018", "shot": "shot01",
       "vision_verdict": "正常", "user_flagged": None}


# ── 1. 只读标记默认关闭 ────────────────────────────────────────────────
section("只读标记默认关闭")
restore = with_env(None)
try:
    check(rd.readonly_mode() is False, "未注入 ROTATION_DATASET_READONLY → 生产行为不变")
finally:
    restore()


# ── 2/3. 只读模式下写活数据集全部被拒，且文件未变 ──────────────────────
section("只读模式下写活数据集被拒")
restore = with_env("1")
try:
    check(rd.readonly_mode() is True, "注入 ROTATION_DATASET_READONLY=1 → 进入只读模式")
    msg = rejects(lambda: rd.append_records([row]))
    check("禁止写生产数据集" in msg and rd.READONLY_ENV in msg, "append_records 默认路径被拒且说清原因")
    rejects(lambda: rd.set_user_flag([("2026-10-15", "daily-china-20261015", "shot03", True)]))
    print("  ok: set_user_flag 默认路径被拒")
    rejects(lambda: rd.write_rows([row]))
    print("  ok: write_rows（单一写口）默认路径被拒")
    # 相对路径绕道也必须被认出来（realpath 归一后比对）
    rel = os.path.join(HERE, os.pardir, "references", "rotation-dataset.jsonl")
    rejects(lambda: rd.append_records([row], rel))
    print("  ok: 绕道相对路径同样被拒")
finally:
    restore()

check(sha256(LIVE) == before, "被拒三次后生产数据集字节不变（sha256 一致）")


# ── 4. 显式 tempfile 路径照常可写 ──────────────────────────────────────
section("显式 tempfile 路径不受只读限制")
tmp = tempfile.mkdtemp(prefix="rd_ro_")
ds = os.path.join(tmp, "ds.jsonl")
restore = with_env("1")
try:
    rd.append_records([row], ds)
    rd.append_records([dict(row, shot="shot02", vision_verdict="翻转")], ds)
    updated, missing = rd.set_user_flag(
        [("2026-10-18", "daily-china-20261018", "shot01", True)], ds, source="test")
    check(updated == [("2026-10-18", "daily-china-20261018", "shot01")] and not missing,
          "set_user_flag 在夹具路径上照常回填")
    rows = rd.load(ds)
    check(len(rows) == 2 and [r["shot"] for r in rows] == ["shot01", "shot02"], "夹具两行读回")
    check(rows[0]["user_flagged"] is True, "夹具行 user_flagged 已回填")
    rd.write_rows(rows, ds)
    check(len(rd.load(ds)) == 2, "write_rows 在夹具路径上照常整表回写")
finally:
    restore()


# ── 5. 全仓 test_*.py 不得指向活数据集 ─────────────────────────────────
section("全仓扫描：测试不得指向活数据集路径")
repo = os.path.dirname(HERE)
bad = []
scanned = 0
SELF = os.path.basename(os.path.abspath(__file__))
for path in glob.glob(os.path.join(repo, "**", "test_*.py"), recursive=True):
    rel = os.path.relpath(path, repo).replace("\\", "/")
    if rel.startswith(("graphify-out/", ".git/", ".wt/")):
        continue
    if os.path.basename(path) == SELF:
        continue  # 本文件自带「绕道相对路径也必须被拒」用例，是唯一合法构造活路径处
    scanned += 1
    for i, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        if "rotation-dataset" not in line:
            continue
        # 允许：os.path.join(<tempdir>, "rotation-dataset.jsonl") 这类夹具路径
        if "references" in line or "os.pardir" in line or '".."' in line or "'..'" in line:
            bad.append(f"{rel}:{i}: {line.strip()}")
        if ".DATASET" in line or "rd.DATASET" in line:
            bad.append(f"{rel}:{i}: 引用生产 DATASET 常量 — {line.strip()}")
check(scanned > 10, f"扫描到 {scanned} 个测试文件（>10，确认 glob 没落空）")
check(not bad, "无测试指向活数据集：" + ("；".join(bad) if bad else "全部为夹具路径"))

# t15_replay 是唯一曾经直读活文件的测试，必须落在 fixtures 上
t15 = open(os.path.join(HERE, "test_t15_replay.py"), encoding="utf-8").read()
check("fixtures" in t15 and "t15_rotation_dataset_20261015.jsonl" in t15,
      "test_t15_replay 读 fixtures 期次快照")


# ── 6. 期次快照夹具在盘 ────────────────────────────────────────────────
section("期次快照夹具")
fixture = os.path.join(HERE, "fixtures", "t15_rotation_dataset_20261015.jsonl")
check(os.path.exists(fixture), f"夹具在盘：{os.path.relpath(fixture, repo)}")
frows = [json.loads(l) for l in open(fixture, encoding="utf-8") if l.strip()]
check(len(frows) == 19, f"2026-10-15 期快照 19 行，实得 {len(frows)}")
check(all(r["date"] == "2026-10-15" for r in frows), "快照只含该期行")
check(sorted(r["shot"] for r in frows) == sorted(f"shot{i:02d}" for i in range(1, 20)),
      "快照段号 shot01..shot19 齐全")

print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
