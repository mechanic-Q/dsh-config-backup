#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宿主契约文档的一致性自检（票 #144）。

无框架直跑：python3 scripts/test_host_contract.py

覆盖票面 6 条 AC（拆成下面 8 组检查）：
1. 文档存在，且只有一个机读声明块（AC6 单一事实源）
2. 解析器返回文档声明；缺块/坏 JSON/坏契约都报错并指向文档（AC6）
3. 13 阶段齐、与 run_pipeline._stage_list 一致；每行有宿主/跨侧依赖/产物目录（AC1）
4. 三个 shell 各一句用途+禁区，Git Bash 明确禁止跑 pipeline（AC2）
5. 产物目录契约覆盖全部目录：谁写、谁读、能否清理（AC3）
6. 单列宿主声明（#151 两列合一）+ 理由与实测数据（AC4）
7. AGENTS.md 一行指针；SKILL.md 只留指针、不再复述宿主细节（AC5）
8. markdown 表格与机读声明逐格一致（AC6 不出现两套口径）
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import host_contract as hc

# 这些是「driver 在哪跑」的宿主细节标记：收敛后只准出现在契约文档里。
HOST_DETAIL_MARKERS = (
    "run_all.sh",
    "Git Bash",
    "WSL pgrep",
    "//wsl.localhost",
    "WSL 侧 driver 经 interop 起 Windows 侧 DSH 桌面宿主",
    "WSL 经网关",
    "WSL 侧自动探",
    "Windows 主机 8080",
    "Windows 主机 8100",
)

SLUG_DIRS = [
    "00_口播稿", "01_分段", "02_TTS语音", "03_导演设计", "04_分镜增强",
    "05_美术画质", "06_H3prompt", "07_成片", "08_字幕", "09_封面", "10_发布",
    "_driver", "_wsout",
]
EXTERNAL_DIRS = ["DATE_ROOT", "COMFYUI_OUT", "KREA2_ROOT", "TEMP", "SAU_ROOT"]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def table(text, header_prefix):
    """抓取以 header_prefix 开头的 markdown 表格，返回单元格列表的列表。"""
    rows, active = [], False
    for line in text.splitlines():
        if line.startswith(header_prefix):
            active = True
            continue
        if not active:
            continue
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows


def ids(cell):
    """单元格里所有反引号包裹的标识。"""
    return re.findall(r"`([^`]+)`", cell)


# ── 1. 文档与解析器 ──────────────────────────────────────────────────────
assert os.path.isfile(hc.DOC_PATH), "契约文档不存在: %s" % hc.DOC_PATH
assert os.path.normpath(hc.DOC_PATH) == os.path.normpath(
    os.path.join(ROOT, hc.DOC_REL)), "DOC_PATH 必须在仓库根的 docs 下"
text = read(hc.DOC_PATH)
assert text.count("```json host-contract") == 1, \
    "机读声明块必须唯一（标记 json host-contract）"
assert "scripts/host_contract.py" in text, "文档必须写明解析器入口"

contract = hc.load()
assert contract["contract"] == "host-contract"
assert contract["version"] == 2
assert hc.load() == contract, "load() 必须幂等返回同一份声明"

# ── 2. 坏文档必须报错并指向文档 ─────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="hostcontract_")
bad = os.path.join(tmp, "no-block.md")
with open(bad, "w", encoding="utf-8") as f:
    f.write("# 没有机读声明\n")
try:
    hc.load(bad)
except hc.HostContractError as e:
    assert bad in str(e), "缺声明块的报错必须带文档路径: %s" % e
else:
    raise AssertionError("缺声明块必须报 HostContractError")

bad_json = os.path.join(tmp, "bad-json.md")
with open(bad_json, "w", encoding="utf-8") as f:
    f.write("```json host-contract\n{不是 JSON\n```\n")
try:
    hc.load(bad_json)
except hc.HostContractError as e:
    assert bad_json in str(e), "坏 JSON 的报错必须带文档路径: %s" % e
else:
    raise AssertionError("坏 JSON 必须报 HostContractError")

bad_kind = os.path.join(tmp, "bad-kind.md")
with open(bad_kind, "w", encoding="utf-8") as f:
    f.write('```json host-contract\n{"contract": "other"}\n```\n')
try:
    hc.load(bad_kind)
except hc.HostContractError as e:
    assert bad_kind in str(e)
else:
    raise AssertionError("契约名不符必须报 HostContractError")

# 非 UTF-8 文档也必须落成 HostContractError（CLI 依赖 exit 2，不许漏成 traceback）
bad_bytes = os.path.join(tmp, "not-utf8.md")
with open(bad_bytes, "wb") as f:
    f.write(b"\xff\xfe\x00\x00")
try:
    hc.load(bad_bytes)
except hc.HostContractError as e:
    assert bad_bytes in str(e)
else:
    raise AssertionError("非 UTF-8 文档必须报 HostContractError")

# ── 3. 宿主与 driver 声明 ────────────────────────────────────────────────
hosts = hc.hosts()
assert set(hosts) == {"wsl", "windows"}, "宿主集合必须恰好是 wsl/windows: %s" % sorted(hosts)
for hid, h in hosts.items():
    assert h["label"].strip(), hid
    assert h["evidence"].strip(), hid
assert hc.current_host() == "windows", hc.current_host()
assert hc.is_current_host("windows") is True
assert hc.is_current_host("wsl") is False

drv = hc.driver()
assert drv["current"] == hc.current_host()
assert "Windows" in drv["current_why"]
assert len(drv["evidence"]) >= 4, "宿主理由至少 4 条实测"
for ev in drv["evidence"]:
    assert ev["fact"].strip() and ev["source"].strip(), ev
    assert re.match(r"(#\d+|references/|docs/)", ev["source"]), ev["source"]

assert ("当前执行宿主（`%s`）" % hc.current_host()) in text
assert drv["current_why"] in text

ev_rows = table(text, "| # | 实测 ")
assert len(ev_rows) == len(drv["evidence"]), \
    "实测数据表 %d 行 ≠ 声明 %d 条" % (len(ev_rows), len(drv["evidence"]))
for row, ev in zip(ev_rows, drv["evidence"]):
    assert len(row) == 3, row
    assert row[1] == ev["fact"], "实测列漂移: %r" % row[1]
    assert row[2] == ev["source"], "出处列漂移: %r" % row[2]

# ── 4. 13 阶段 + 与 driver 实际阶段表一致 ────────────────────────────────
stages = hc.stages()
stage_ids = [s["id"] for s in stages]
assert stage_ids == ["s%02d" % i for i in range(1, 14)], stage_ids

rp = read(os.path.join(HERE, "run_pipeline.py"))
seen = set(re.findall(r'\("(s\d{2})",', rp))
assert seen == set(stage_ids), \
    "文档阶段与 run_pipeline._stage_list 不一致: 多 %s 少 %s" % (
        sorted(seen - set(stage_ids)), sorted(set(stage_ids) - seen))

svc_ids = {s["id"] for s in hc.services()}
art_ids = {a["id"] for a in hc.artifact_dirs()}
kinds = set(hc.syntax_kinds())
assert kinds, "syntax_kinds 不能为空"

stage_rows = table(text, "| 阶段 ")
assert len(stage_rows) == 13, "阶段表必须 13 行: %d" % len(stage_rows)
for row, st in zip(stage_rows, stages):
    assert len(row) == 5, "阶段行必须 5 列（#151 两列合一）: %r" % row
    assert row[0] == "`%s`" % st["id"], row[0]
    assert row[1] == st["name"], "阶段名漂移: %r" % row[1]
    assert st["host"] == hc.current_host(), "%s 宿主必须等于 driver 当前宿主" % st["id"]

    svc = ids(row[2])
    assert svc == st["services"], "%s 跨侧依赖漂移: %s" % (st["id"], svc)
    for sid in st["services"]:
        assert sid in svc_ids, "%s 引用了未声明服务 %s" % (st["id"], sid)

    syn = ids(row[3])
    assert syn == st["syntax"], "%s 语法漂移: %s" % (st["id"], syn)
    for k in st["syntax"]:
        assert k in kinds, "%s 用了未声明语法 %s" % (st["id"], k)
    assert st["syntax"], st["id"]

    assert ids(row[4]) == st["artifacts"], "%s 产物目录漂移: %r" % (st["id"], row[4])
    for aid in st["artifacts"]:
        assert aid in art_ids, "%s 引用了未声明目录 %s" % (st["id"], aid)

# 12 个 slug 目录（除 _driver）必须至少被一个阶段声明为产物
covered = set()
for st in stages:
    covered |= set(st["artifacts"])
missing = set(SLUG_DIRS) - {"_driver"} - covered
assert not missing, "阶段表未覆盖这些产物目录: %s" % sorted(missing)

# ── 5. 三个 shell ──────────────────────────────────────────────────────
shells = hc.shells()
shell_map = {s["id"]: s for s in shells}
assert set(shell_map) == {"wsl-bash", "git-bash", "windows-native"}, sorted(shell_map)
for shell in shells:
    assert shell["host"] in hosts, shell["id"]
    assert shell["usage"].strip(), shell["id"]
    assert shell["forbidden"].strip(), shell["id"]
    assert "\n" not in shell["usage"] + shell["forbidden"], "用途/禁区各一句: %s" % shell["id"]
assert "禁止" in shell_map["git-bash"]["forbidden"]
assert "pipeline" in shell_map["git-bash"]["forbidden"], "Git Bash 必须明确禁止跑 pipeline"

shell_rows = table(text, "| shell ")
assert len(shell_rows) == 3, "shell 表必须 3 行: %d" % len(shell_rows)
for row in shell_rows:
    assert len(row) == 3, row
    sid = ids(row[0])[0]
    assert sid in shell_map, "shell 表出现未声明项: %s" % sid
    assert row[1] == shell_map[sid]["usage"], "%s 用途漂移: %r" % (sid, row[1])
    assert row[2] == shell_map[sid]["forbidden"], "%s 禁区漂移: %r" % (sid, row[2])

# ── 6. 产物目录契约 ─────────────────────────────────────────────────────
artifacts = hc.artifact_dirs()
art_map = {a["id"]: a for a in artifacts}
assert set(art_map) == set(SLUG_DIRS + EXTERNAL_DIRS), \
    "目录契约不齐: 多 %s 少 %s" % (
        sorted(set(art_map) - set(SLUG_DIRS + EXTERNAL_DIRS)),
        sorted(set(SLUG_DIRS + EXTERNAL_DIRS) - set(art_map)))
for aid, a in art_map.items():
    assert a["writer"], "%s 缺 writer" % aid
    assert a["readers"], "%s 缺 readers" % aid
    assert isinstance(a["cleanable"], bool), aid
    assert a["clean_rule"].strip(), "%s 缺清理规则" % aid
    assert a["path"].strip(), "%s 缺路径口径" % aid
    assert "\n" not in a["clean_rule"], aid

for aid in SLUG_DIRS:
    if aid == "_driver":
        assert art_map[aid]["writer"] == ["driver"], art_map[aid]["writer"]
        continue
    wrote = [sid for sid in stage_ids if sid in art_map[aid]["writer"]]
    assert wrote, "%s 没有任何阶段写者" % aid
assert art_map["_wsout"]["writer"] == ["s08"], art_map["_wsout"]["writer"]
assert "new_daily.py" in art_map["00_口播稿"]["writer"], art_map["00_口播稿"]["writer"]

art_rows = table(text, "| 目录 ")
assert len(art_rows) == len(SLUG_DIRS) + len(EXTERNAL_DIRS), \
    "目录表 %d 行 ≠ 契约 %d 条" % (len(art_rows), len(SLUG_DIRS) + len(EXTERNAL_DIRS))
for row in art_rows:
    assert len(row) == 5, row
    aid = ids(row[0])[0]
    assert aid in art_map, "目录表出现未声明项: %s" % aid
    a = art_map[aid]
    expect_path = "`%s`" % a["path"]
    assert row[1] == expect_path, "%s 路径口径漂移:\n  表: %r\n  声明: %r" % (aid, row[1], expect_path)
    assert ids(row[2]) == a["writer"], "%s writer 漂移: %r" % (aid, row[2])
    assert ids(row[3]) == a["readers"], "%s readers 漂移: %r" % (aid, row[3])
    flag = "✅ " if a["cleanable"] else "❌ "
    assert row[4] == flag + a["clean_rule"], "%s 清理规则漂移: %r" % (aid, row[4])

# ── 7. 服务表 ─────────────────────────────────────────────────────────
services = hc.services()
svc_map = {s["id"]: s for s in services}
for s in services:
    assert s["side"] in hosts, "%s 运行侧非法: %s" % (s["id"], s["side"])
    assert s["endpoint"].strip() and s["usage"].strip() and s["access"].strip(), s["id"]
    assert "\n" not in s["usage"] + s["access"], s["id"]

svc_rows = table(text, "| 服务 ")
assert len(svc_rows) == len(services), "服务表 %d 行 ≠ 声明 %d 条" % (len(svc_rows), len(services))
for row in svc_rows:
    assert len(row) == 5, row
    sid = ids(row[0])[0]
    assert sid in svc_map, "服务表出现未声明项: %s" % sid
    s = svc_map[sid]
    assert row[1] == "`%s`" % s["side"], "%s 运行侧漂移: %r" % (sid, row[1])
    assert s["endpoint"] in row[2], "%s 端点数不符: %r" % (sid, row[2])
    assert row[3] == s["usage"], "%s 用途漂移: %r" % (sid, row[3])
    assert row[4] == s["access"], "%s 访问方式漂移: %r" % (sid, row[4])

# ── 8. AGENTS.md 一行指针；SKILL.md 只留指针 ───────────────────────────
agents = read(os.path.join(ROOT, "AGENTS.md"))
assert agents.count(hc.DOC_REL) == 1, "AGENTS.md 必须恰好一行指针到 %s" % hc.DOC_REL
assert "宿主" in agents, "指针行要说明它是什么"

skill = read(os.path.join(ROOT, "SKILL.md"))
assert hc.DOC_REL in skill, "SKILL.md 必须指向契约文档"
for marker in HOST_DETAIL_MARKERS:
    assert marker not in skill, "SKILL.md 仍在复述宿主细节: %r" % marker

# ── 9. 文档是唯一声明源：只有解析器能读它 ───────────────────────────────
readers = sorted(os.path.normpath(p) for p in glob.glob(os.path.join(HERE, "*.py"))
                 if hc.DOC_REL in read(p))
assert readers == [os.path.normpath(hc.__file__)], \
    "除解析器外还有脚本直接读契约文档: %s" % readers

# ── 10. CLI（排障/守卫手工核对入口） ───────────────────────────────────
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def cli(*args):
    p = subprocess.run([sys.executable, hc.__file__] + list(args),
                       capture_output=True, text=True, encoding="utf-8", env=env)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


rc, out, err = cli("current")
assert rc == 0 and out == hc.current_host(), (rc, out, err)
rc, out, err = cli("json")
assert rc == 0 and json.loads(out)["contract"] == "host-contract", (rc, out[-200:], err)
rc, out, err = cli("stage", "s08")
assert rc == 0 and json.loads(out)["id"] == "s08", (rc, out, err)
rc, out, err = cli("bogus")
assert rc == 2, "未知子命令必须 exit 2: %s" % rc
assert "host-contract" in err or "host_contract" in err, err

shutil.rmtree(tmp, ignore_errors=True)

print("test_host_contract: 全部通过")
