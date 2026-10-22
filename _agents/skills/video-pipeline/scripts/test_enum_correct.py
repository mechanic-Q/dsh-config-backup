#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_enum_correct.py — 景别死锁枚举级纠偏自检（#47，ADR 0003，assert 直跑风格）。

覆盖（对应 #47 验收标准）：
1. 景别梯契约：SHOT_SCALE_LADDER ⊆ SHOT_TYPES、同级放行对恰为相邻梯位
2. pick_replacement：尺度距离最近 + 平手取更紧 + 前后镜避让
3. detect/apply：0921 实案纠偏 + md 镜像同步 + 语义字段逐字不动（AC3）
4. 台账记录：段号 + 改前/改后值（AC2，模块级 + driver 级）
5. 七项自检联动：候选暴露（枚举可纠才暴露）
6. driver 接线：s05_photographer 纠偏→真复检→通过续跑（AC1）；仍败按协议叫人（AC4）
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import schema
import enum_correct as ec
import storyboard_enhancer as E
import run_pipeline as rp
from ledger import Ledger

# ── 1. 景别梯契约 ─────────────────────────────────────────────────────
assert set(schema.SHOT_SCALE_LADDER) <= set(schema.SHOT_TYPES), "梯上值必须是合法枚举"
assert "运镜" not in schema.SHOT_SCALE_LADDER and "过肩" not in schema.SHOT_SCALE_LADDER, \
    "非尺度档不上梯"
for a, b in E.SHOT_SAME_LEVEL:
    assert abs(schema.SHOT_SCALE_LADDER.index(a) - schema.SHOT_SCALE_LADDER.index(b)) == 1, \
        f"同级放行对 {a}/{b} 应为相邻梯位"

# ── 2. pick_replacement ──────────────────────────────────────────────
# 0921 实案：shot10/11 同为中全景、shot12=中景 → 最近合法档 全景
assert ec.pick_replacement("中全景", "中全景", "中景") == "全景"
# 等距平手取更紧：全景(3)/中景(5) 距 中全景(4) 等距 → 中景
assert ec.pick_replacement("中全景", "中全景", "大全景") == "中景"
# 三连同景别：与前后镜都不重复（同级放行不构成阻碍）→ 最近档 中近景
assert ec.pick_replacement("中景", "中景", "中景") == "中近景"
# 尾段无后镜：只避前镜
assert ec.pick_replacement("特写", "特写", "") == "近景"
# 原值不在梯上（运镜/过肩）：按梯序取首个合法档
assert ec.pick_replacement("运镜", "运镜", "") == "大远景"
# 新值永不等前后镜原值（硬重复禁区）；三连同景别 → 等距取更紧档
assert ec.pick_replacement("远景", "远景", "远景") == "大全景"

# ── 3/4. detect + apply：0921 实案、md 镜像、语义字段、台账 ───────────


def mk_design(dirpath, sid, jb, rhythm="铺垫", extra="", override=None):
    """导演设计 shotXX.json+md 落盘（director_llm 落盘同构）。override 可覆盖/删字段。"""
    d = {"id": sid, "情绪基调": f"{rhythm}，测试基调", "场景": "测试场景", "主体": "测试主体",
         "景别": jb, "运镜": "静止", "镜头": f"{jb}缓推，主体居中", "动作时间轴": "0-3s 测试动作",
         "风格渗透": extra, "信息点": "测试信息点"}
    for k, v in (override or {}).items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    with open(os.path.join(dirpath, f"{sid}.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    with open(os.path.join(dirpath, f"{sid}.md"), "w", encoding="utf-8") as f:
        f.write(f"# {sid}\n\n")
        for k, v in d.items():
            if k != "id":
                f.write(f"【{k}】{v}\n\n")
    return d


def semantic_snapshot(dirpath):
    """除「景别」外的语义字段快照（json 值 + md 正文），用于逐字未变校验（AC3）。"""
    out = {}
    for fn in sorted(os.listdir(dirpath)):
        if fn.endswith(".json"):
            d = json.load(open(os.path.join(dirpath, fn), encoding="utf-8"))
            out[fn] = {k: v for k, v in d.items() if k != "景别"}
        elif fn.endswith(".md"):
            txt = open(os.path.join(dirpath, fn), encoding="utf-8").read()
            out[fn] = "".join(l for l in txt.splitlines(keepends=True)
                              if not l.startswith("【景别】"))
    return out


tmp = tempfile.mkdtemp()
d0921 = os.path.join(tmp, "0921")
os.makedirs(d0921)
mk_design(d0921, "shot10", "中全景")
mk_design(d0921, "shot11", "中全景")   # 0921 期实案：shot10→11 中全景×2
mk_design(d0921, "shot12", "中景")

before = semantic_snapshot(d0921)
cands = ec.detect_conflicts(d0921)
assert cands == [{"prev_id": "shot10", "id": "shot11", "prev": "中全景", "cur": "中全景"}], cands
recs = ec.apply_corrections(d0921, candidates=cands, now="2026-09-22 12:00:00")
assert len(recs) == 1, recs
r = recs[0]
assert r["id"] == "shot11" and r["from"] == "中全景" and r["to"] == "全景", r  # AC2 段号+改前改后
assert r["prev"] == "中全景" and r["next"] == "中景" and r["field"] == "景别"
# 纠偏后无冲突可再探；语义字段逐字未变（AC3）
assert ec.detect_conflicts(d0921) == []
assert semantic_snapshot(d0921) == before, "语义字段被纠偏改写，违反 ADR 0003 权界"
# md 镜像同步：景别行更新且格式与 director_llm 落盘同构
d11 = json.load(open(os.path.join(d0921, "shot11.json"), encoding="utf-8"))
md11 = open(os.path.join(d0921, "shot11.md"), encoding="utf-8").read()
assert d11["景别"] == "全景" and "【景别】全景\n\n" in md11 and "【景别】中全景" not in md11
assert md11 == "# shot11\n\n" + "".join(f"【{k}】{v}\n\n" for k, v in d11.items() if k != "id")

# 三连同景别：只改靠后段一次，第三对被顺带解掉
tmp3 = tempfile.mkdtemp()
d3 = os.path.join(tmp3, "d")
os.makedirs(d3)
mk_design(d3, "shot01", "中景")
mk_design(d3, "shot02", "中景")
mk_design(d3, "shot03", "中景")
recs3 = ec.apply_corrections(d3)  # 缺省=现场重扫
assert len(recs3) == 1 and recs3[0]["id"] == "shot02" and recs3[0]["to"] == "中近景", recs3
assert ec.detect_conflicts(d3) == [], "纠偏后仍应无冲突"

# 候选过期（现状已不冲突）：跳过不写盘
stale = [{"prev_id": "shot10", "id": "shot11", "prev": "中全景", "cur": "中全景"}]
assert ec.apply_corrections(d0921, candidates=stale) == []

# ── 5. 七项自检联动：候选暴露 ─────────────────────────────────────────


def run_enhancer(enhanced, design_dir):
    """真跑 storyboard_enhancer.py 子进程，返回 (report, exit_code)。"""
    tmpd = tempfile.mkdtemp()
    enh = os.path.join(tmpd, "enhanced.json")
    rep = os.path.join(tmpd, "report.json")
    json.dump(enhanced, open(enh, "w", encoding="utf-8"), ensure_ascii=False)
    p = subprocess.run([sys.executable, os.path.join(HERE, "storyboard_enhancer.py"),
                        enh, "--design-dir", design_dir, "--out-report", rep],
                       capture_output=True, text=True, cwd=HERE)
    return json.load(open(rep, encoding="utf-8")), p.returncode


def mk_enhanced(sid, prev_out="收束"):
    """合规 enhanced 条目（秒级指令覆盖 0-镜尾、衔接成环），景别由导演设计决定。"""
    dirs = [{"time": "0.0-1.3s", "action": "动作"}, {"time": "1.3-2.6s", "action": "动作"},
            {"time": "2.6-3.9s", "action": "动作"}]
    return {"id": sid, "text": "测试台词。", "duration_est": 3.9,
            "continuity_handoff": {"in": "承接：上段收束", "out": prev_out},
            "per_second_directives": dirs}


# 枚举硬重复 → ⑦ FAIL + 候选暴露；同级放行 → 不报不暴露
dup_dir = tempfile.mkdtemp()
mk_design(dup_dir, "shot01", "远景", extra="本段是钩子开场，悬念拉起")
mk_design(dup_dir, "shot02", "远景", rhythm="冲击")
rep, code = run_enhancer([mk_enhanced("shot01"), mk_enhanced("shot02")], dup_dir)
assert code == 1 and rep["all_pass"] is False
assert any(f["check"] == "⑦景别交替" and f["id"] == "shot02" for f in rep["failed_items"])
assert rep["correction_candidates"] == [
    {"prev_id": "shot01", "id": "shot02", "prev": "远景", "cur": "远景"}], rep["correction_candidates"]

near_dir = tempfile.mkdtemp()
mk_design(near_dir, "shot01", "中景")
mk_design(near_dir, "shot02", "中近景", rhythm="冲击")
rep, code = run_enhancer([mk_enhanced("shot01"), mk_enhanced("shot02")], near_dir)
assert not any(f["check"] == "⑦景别交替" for f in rep["failed_items"])
assert rep["correction_candidates"] == []

# 旧 md-only 设计（无 json）：⑦ 可报但无候选（无闭合枚举可纠，保持人工路径）
mdonly = tempfile.mkdtemp()
for sid, jb in (("shot01", "中景"), ("shot02", "中景")):
    open(os.path.join(mdonly, f"{sid}.md"), "w", encoding="utf-8").write(
        f"# {sid}\n\n【情绪基调】铺垫，测试\n【信息点】x\n【镜头】{jb}缓推\n")
rep, code = run_enhancer([mk_enhanced("shot01"), mk_enhanced("shot02")], mdonly)
assert any(f["check"] == "⑦景别交替" for f in rep["failed_items"]) and code == 1
assert rep["correction_candidates"] == []
assert ec.detect_conflicts(mdonly) == [], "md-only 无 json 不可纠偏"

# ── 6. driver 接线：纠偏→复检→续跑 / 仍败叫人 ─────────────────────────
_ENHANCER = os.path.join(HERE, "storyboard_enhancer.py")


def mk_driver(tmpdir):
    d = rp.Driver.__new__(rp.Driver)
    d.d = {"01_分段": os.path.join(tmpdir, "01"), "03_导演设计": os.path.join(tmpdir, "03"),
           "04_分镜增强": os.path.join(tmpdir, "04")}
    for p in d.d.values():
        os.makedirs(p, exist_ok=True)
    d.stamps_dir = os.path.join(tmpdir, "_driver")
    os.makedirs(d.stamps_dir, exist_ok=True)
    d._t0 = time.time()
    d.ledger = Ledger(os.path.join(d.stamps_dir, "report.json"), t0=d._t0,
                      meta={"slug": tmpdir, "date": "2026-09-22"})
    return d


def _arg(argv, marker, skip):
    i = next(i for i, a in enumerate(argv) if marker in a)
    return argv[i + 1 + skip]


def _opt(argv, flag):
    return argv[argv.index(flag) + 1]


def real_check(cmd):
    """用 sys.executable 真跑七项自检（#151 起收 argv 列表；兼容旧引号串）。"""
    if isinstance(cmd, str):
        # 旧 sh 串形态：按引号切段
        cmd = cmd.replace("\\", "/").split('" "')
    enh_fp = _arg(cmd, "storyboard_enhancer.py", 0)
    design_dir = _opt(cmd, "--design-dir")
    rep_fp = _opt(cmd, "--out-report")
    # encoding 显式声明 + errors=replace：enhancer 子进程在 Windows 控制台输出 GBK
    # （『七项自检』等中文横幅），text=True 默认按 locale 拿到混合编码时会炸解码线程
    # 导致 stdout=None。2026-10-16 实修。
    p = subprocess.run([sys.executable, _ENHANCER, enh_fp, "--design-dir", design_dir,
                        "--out-report", rep_fp], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out[-300:]


# 6a. AC1/AC2 全链路：摄影师(桩)→自检败→纠偏→真复检过→交接卡(桩)→台账落盘
tmpA = tempfile.mkdtemp()
drvA = mk_driver(tmpA)
mk_design(drvA.d["03_导演设计"], "shot01", "远景", extra="本段是钩子开场，悬念拉起")
mk_design(drvA.d["03_导演设计"], "shot02", "远景", rhythm="冲击")
enh_fp = os.path.join(drvA.d["04_分镜增强"], "分镜清单_enhanced.json")
_saved_sh, _saved_log = rp.sh, rp.log
_logs = []


def sh_flow(cmd, env=None, timeout=None, cwd=None):
    key = cmd if isinstance(cmd, str) else " ".join(str(a) for a in cmd)
    if "storyboard_photographer.py" in key:
        json.dump([mk_enhanced("shot01"), mk_enhanced("shot02")], open(enh_fp, "w",
                   encoding="utf-8"), ensure_ascii=False)
        return 0, ""
    if "storyboard_enhancer.py" in key:
        return real_check(cmd)
    if "gen_handoff.py" in key:
        return 0, ""
    raise AssertionError("意外命令: " + key)


def argv_flow(argv, env=None, timeout=None, cwd=None):
    """#151 起 s05 子进程走 run_argv(argv)；桩转成空格串复用 sh_flow 分流。"""
    return sh_flow(argv, env=env, timeout=timeout, cwd=cwd)


try:
    rp.sh = sh_flow
    _saved_run_argv = rp.run_argv
    rp.run_argv = argv_flow
    rp.log = lambda m: _logs.append(m)
    drvA.s05_photographer()  # 不应抛：纠偏后复检通过，继续走到交接卡
finally:
    rp.sh, rp.log = _saved_sh, _saved_log
    rp.run_argv = _saved_run_argv
d02 = json.load(open(os.path.join(drvA.d["03_导演设计"], "shot02.json"), encoding="utf-8"))
assert d02["景别"] == "大全景", d02["景别"]  # 远景→大全景（等距取更紧）
assert any("枚举级纠偏 shot02" in m and "远景 → 大全景" in m for m in _logs), _logs
ledger = json.load(open(os.path.join(drvA.stamps_dir, "report.json"), encoding="utf-8"))
assert len(ledger["enum_corrections"]) == 1, ledger  # AC2：台账留痕已落盘
assert ledger["enum_corrections"][0]["id"] == "shot02"

# 6b. AC4：纠偏后复检仍败（⑦已解但⑤缺信息点）→ s05_photographer 按协议抛 RuntimeError
tmpB = tempfile.mkdtemp()
drvB = mk_driver(tmpB)
mk_design(drvB.d["03_导演设计"], "shot01", "远景", extra="本段是钩子开场，悬念拉起")
mk_design(drvB.d["03_导演设计"], "shot02", "远景", rhythm="冲击")
mk_design(drvB.d["03_导演设计"], "shot03", "中景", rhythm="建立",
          override={"信息点": None})  # ⑤ 必败项：纠偏救不了
enh_fpB = os.path.join(drvB.d["04_分镜增强"], "分镜清单_enhanced.json")


def sh_flow_b(cmd, env=None, timeout=None, cwd=None):
    key = cmd if isinstance(cmd, str) else " ".join(str(a) for a in cmd)
    if "storyboard_photographer.py" in key:
        json.dump([mk_enhanced(f"shot0{i}") for i in (1, 2, 3)], open(enh_fpB, "w",
                  encoding="utf-8"), ensure_ascii=False)
        return 0, ""
    if "storyboard_enhancer.py" in key:
        return real_check(cmd)
    raise AssertionError("纠偏复检仍败时应先抛，不应走到: " + key[:80])


def argv_flow_b(argv, env=None, timeout=None, cwd=None):
    return sh_flow_b(argv, env=env, timeout=timeout, cwd=cwd)


try:
    rp.sh = sh_flow_b
    _saved_run_argv = rp.run_argv
    rp.run_argv = argv_flow_b
    try:
        drvB.s05_photographer()
        raise AssertionError("纠偏后仍失败必须按现有协议抛 RuntimeError")
    except RuntimeError as e:
        assert "七项自检失败（含枚举级纠偏复检）" in str(e), str(e)
finally:
    rp.sh = _saved_sh
    rp.run_argv = _saved_run_argv
d02b = json.load(open(os.path.join(drvB.d["03_导演设计"], "shot02.json"), encoding="utf-8"))
assert d02b["景别"] == "大全景", "⑦ 已纠偏（败因是⑤），现场已尽力修复留档"
ledgerB = json.load(open(os.path.join(drvB.stamps_dir, "report.json"), encoding="utf-8"))
assert ledgerB["enum_corrections"][0]["id"] == "shot02"

# 6c. 非⑦失败项：纠偏不适用，不触发复检
tmpC = tempfile.mkdtemp()
drvC = mk_driver(tmpC)
os.makedirs(drvC.d["04_分镜增强"], exist_ok=True)
json.dump({"failed_items": [{"check": "④Hook分布", "id": "shot01", "detail": "x"}],
           "correction_candidates": []},
          open(os.path.join(drvC.d["04_分镜增强"], "_自检报告.md"), "w", encoding="utf-8"))
sh_calls_c = []
rp.sh = lambda cmd, env=None, timeout=None, cwd=None: sh_calls_c.append(cmd) or (1, "FAIL")
try:
    rc, out = drvC._enum_recheck("CMD", os.path.join(drvC.d["04_分镜增强"], "_自检报告.md"))
finally:
    rp.sh = _saved_sh
assert rc == 1 and "不适用" in out, (rc, out)
assert sh_calls_c == [], "非⑦失败不得触发复检"

# 6d. ⑦失败但无可纠候选（md-only）→ 返回失败走人工路径
tmpD = tempfile.mkdtemp()
drvD = mk_driver(tmpD)
os.makedirs(drvD.d["04_分镜增强"], exist_ok=True)
json.dump({"failed_items": [{"check": "⑦景别交替", "id": "shot02", "detail": "中景→中景"}],
           "correction_candidates": []},
          open(os.path.join(drvD.d["04_分镜增强"], "_自检报告.md"), "w", encoding="utf-8"))
rc, out = drvD._enum_recheck("CMD", os.path.join(drvD.d["04_分镜增强"], "_自检报告.md"))
assert rc == 1 and "保持人工路径" in out, (rc, out)

print("test_enum_correct: 全部通过")
