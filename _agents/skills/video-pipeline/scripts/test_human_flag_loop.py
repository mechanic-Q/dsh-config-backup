# -*- coding: utf-8 -*-
"""T17 #129 人工指认回路测试（裸脚本，直跑即测；全部桩平，不碰真飞书/真数据集）。

覆盖：classify flag 分类、_ACTION、_shots_from_text、_flag_is_ok、
_flag_commit 存证与回填、human_flags 平键跨重启、_human_flag_loop 收口与交人、
run_flag_cli 解析与短路、CLI 接线烟测。
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_pipeline as rp
import rotation_dataset as rd

# 真写口留底：t13 验真实 set_evidence_frames，而前面小节会把它桩成记录器
_REAL_SET_EVIDENCE_FRAMES = rd.set_evidence_frames
import feishu_gate as fg
from feishu_gate import FeishuGate

WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp_t17_test")
os.makedirs(WORK, exist_ok=True)

PASS = []
FAIL = []


def section(name):
    print()
    print("== " + name + " ==")


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  PASS " + name)
    except AssertionError as e:
        FAIL.append(name)
        print("  FAIL " + name + ": " + str(e))
    except Exception as e:
        FAIL.append(name)
        print("  FAIL " + name + ": " + type(e).__name__ + ": " + str(e))


def mk_driver(**inst_attrs):
    """Driver.__new__ 裸实例：只填指认回路用到的属性，其余方法按需打桩。"""
    d = rp.Driver.__new__(rp.Driver)
    d.slug = os.path.join(WORK, "fake-slug")
    d.date = "2026-10-17"
    os.makedirs(os.path.join(d.slug, "_frames"), exist_ok=True)
    d.__dict__.update(inst_attrs)
    return d


class FakeLedger:
    def __init__(self):
        self.events = []

    def record_event(self, path, item):
        self.events.append((path, item))

    def get(self, path, view="merged"):
        return {}

    def record(self, *a, **k):
        pass

    def flush(self):
        pass


def patch_set_user_flag(recorder):
    """_flag_commit 函数内 from rotation_dataset import set_user_flag——
    每次调用解析模块属性，桩模块属性即生效。"""
    rd.set_user_flag = recorder


def patch_set_evidence_frames(recorder):
    """同 patch_set_user_flag：函数内 import 解析模块属性，桩即生效。
    不桩的话 _flag_commit 会拿默认 DATASET（活数据集）——测试期被
    ROTATION_DATASET_READONLY 守卫拒掉，写口本身验不到（见 t13 的临时库口径）。"""
    rd.set_evidence_frames = recorder


def recorder_frames(saves):
    """set_evidence_frames 桩：记下调用并把 (updated, missing) 报成全命中。"""
    def _rec(entries, dataset_path=None, stale=None):
        saves.append(list(entries))
        return (len(entries), [])
    return _rec


# 凭证桩（同 test_feishu_gate 惯用法）：本测试只验指认回路的纯逻辑（classify /
# _decision / 存证 / 回填），不该依赖本机 ~/.dsh/dsh-im-connect/channels.json ——
# 无该文件的宿主（CI runner）上 FeishuGate() 会在构造器里抛「FEISHU_APP_ID/SECRET
# 缺失」，与所测目标无关。
fg._load_env = lambda: {"FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b",
                        "FEISHU_DAILY_NEWS_CHAT_ID": "c"}
g = FeishuGate()

# ── §1 classify flag 分类 ────────────────────────────────────────────
FLAG_CASES = [
    ("第3段翻转了", "flag"),
    ("第 3 段 翻转", "flag"),
    ("画面翻转了", "flag"),
    ("shot03 翻转了", "flag"),
    ("重生成，画面翻转了", "flag"),
    ("shot05 横躺了", "flag"),
    ("shot07 旋转", "flag"),
]
NOREG_CASES = [
    ("重生成 shot02", "shot_regen"),
    ("重来 1-4", "shot_regen"),
    ("shot03 不行", "shot_regen"),
    ("重做 3.5", "regen"),
    ("标题别重生成", "regen"),
    ("同意发布", "feedback"),
    ("标题有点歪", "feedback"),
    ("第 3 集不错", "feedback"),
    ("通过", "pass"),
    ("", "feedback"),
]


def t1():
    for text, want in FLAG_CASES + NOREG_CASES:
        got = g.classify(text)
        assert got == want, "classify(" + repr(text) + ")=" + repr(got) + " want " + repr(want)


def t2():
    from feishu_gate import _ACTION
    assert _ACTION.get("flag") == "retry", _ACTION
    d = g._decision("flag", "第3段翻转了", ["shot03"])
    assert d["action"] == "retry" and d["shots"] == ["shot03"], d
    d2 = g._decision("flag", "画面翻转了")
    assert d2["action"] == "retry" and "shots" not in d2, d2


def t3():
    d = mk_driver(feishu=g)
    f = d._shots_from_text
    assert f("shot03 翻转") == ["shot03"], f("shot03 翻转")
    assert f("第 3 段翻转了") == ["shot03"], f("第 3 段翻转了")
    assert f("第12段倒了") == ["shot12"], f("第12段倒了")
    assert f("第３段") == ["shot03"], f("第３段")
    assert f("3、5 都翻了") == ["shot03", "shot05"], f("3、5 都翻了")
    assert f("什么都没有") == [], f("什么都没有")


def t4():
    d = mk_driver()
    for w in ("正常", "OK", "ok", "通过", "没问题", " 正常 "):
        assert d._flag_is_ok(w), w
    for w in ("翻转", "横躺", "倒", ""):
        assert not d._flag_is_ok(w), w

# ── §5 _flag_commit ──────────────────────────────────────────────────
def t5():
    d = mk_driver(ledger=FakeLedger(), notify=lambda *a, **k: None)
    fr = os.path.join(d.slug, "_frames")
    src = os.path.join(fr, "shot01.jpg")
    io.open(src, "wb").write(b"fake-jpeg")
    calls = []

    def fake_suf(flags, dataset_path=None, source=None):
        calls.append((list(flags), source))
        return (len(flags), [])

    patch_set_user_flag(fake_suf)
    patch_set_evidence_frames(recorder_frames([]))
    ev = d._flag_commit("shot01", "翻转", "shot01 翻转了")
    assert ev and os.path.exists(ev), ev
    path, item = d.ledger.events[-1]
    assert path == "human_flags", path
    assert item["sid"] == "shot01" and item["who"] == "human", item
    assert item["verdict"] == "翻转" and item["evidence"] == ev, item
    assert calls and [tuple(x) for x in calls[0][0]] == [
        (d.date, os.path.basename(d.slug), "shot01", True)], calls  # 数据集键=basename
    assert calls[0][1] == "human-flag", calls
    ev2 = d._flag_commit("shot02", "正常", "shot02 正常", who="cli")
    assert ev2 is None, ev2
    path2, item2 = d.ledger.events[-1]
    assert path2 == "human_flags" and item2["verdict"] == "正常", item2
    assert item2["evidence"] is None, item2
    assert [tuple(x) for x in calls[-1][0]] == [
        (d.date, os.path.basename(d.slug), "shot02", False)], calls
    ev3 = d._flag_commit("shot09", "翻转", "x")
    assert ev3 is None and d.ledger.events[-1][1]["evidence"] is None


def t6():
    from ledger import Ledger
    rep = os.path.join(WORK, "rep_" + str(int(time.time() * 1000)) + ".json")
    lg = Ledger(rep)
    lg.record_event("human_flags", {"sid": "shot01", "who": "human", "verdict": "翻转"})
    lg.flush()
    lg2 = Ledger(rep)
    lg2.record_event("human_flags", {"sid": "shot03", "who": "cli", "verdict": "翻转"})
    merged = lg2.get("human_flags", view="merged")
    sids = [e["sid"] for e in merged]
    assert sids == ["shot01", "shot03"], sids


def t7():
    d = mk_driver(ledger=FakeLedger())
    calls = {"regen": [], "s09": 0, "s10": 0, "notify": []}

    def fake_regen(shots, evidence_tag="gate4"):
        calls["regen"].append((list(shots), evidence_tag))
        return []

    d.__dict__["_regen_shots_strip_motion"] = fake_regen
    d.__dict__["s09_compose"] = lambda: calls.__setitem__("s09", calls["s09"] + 1)
    d.__dict__["s10_subs_burn"] = lambda: calls.__setitem__("s10", calls["s10"] + 1)
    d.__dict__["notify"] = lambda m: calls["notify"].append(m)
    patch_set_user_flag(lambda flags, dataset_path=None, source=None: (len(flags), []))
    patch_set_evidence_frames(recorder_frames([]))
    fr = os.path.join(d.slug, "_frames")
    io.open(os.path.join(fr, "shot03.jpg"), "wb").write(b"x")
    d._human_flag_loop(["shot03"], "shot03 翻转了")
    assert calls["regen"] == [(["shot03"], "human_flag")], calls["regen"]
    assert calls["s09"] == 1 and calls["s10"] == 1, calls
    assert d.ledger.events[0][0] == "human_flags", d.ledger.events


def t8():
    d = mk_driver(ledger=FakeLedger())

    def bad_regen(shots, evidence_tag="gate4"):
        return [shots[0]]

    def boom(name):
        def f():
            raise AssertionError(name + " 不应被调用")
        return f

    d.__dict__["_regen_shots_strip_motion"] = bad_regen
    d.__dict__["s09_compose"] = boom("s09")
    d.__dict__["s10_subs_burn"] = boom("s10")
    d.__dict__["notify"] = lambda m: None

    def exit4(code):
        raise SystemExit(code)

    d.__dict__["_flush_exit"] = exit4
    patch_set_user_flag(lambda flags, dataset_path=None, source=None: (len(flags), []))
    patch_set_evidence_frames(recorder_frames([]))
    try:
        d._human_flag_loop(["shot04"], "shot04 翻转了")
        raise AssertionError("未交人退出")
    except SystemExit as e:
        assert e.code == 4, e.code
    paths = [p for p, _ in d.ledger.events]
    assert paths == ["human_flags", "human_flags"], paths
    assert d.ledger.events[-1][1]["who"] == "human-reflag", d.ledger.events[-1]

def t9():
    d = mk_driver(ledger=FakeLedger())
    calls = {"regen": [], "notify": []}

    def fake_regen(shots, evidence_tag="gate4"):
        calls["regen"].append((list(shots), evidence_tag))
        return []

    d.__dict__["_regen_shots_strip_motion"] = fake_regen
    d.__dict__["s09_compose"] = lambda: None
    d.__dict__["s10_subs_burn"] = lambda: None
    d.__dict__["notify"] = lambda m: calls["notify"].append(m)
    patch_set_user_flag(lambda flags, dataset_path=None, source=None: (len(flags), []))
    patch_set_evidence_frames(recorder_frames([]))
    fr = os.path.join(d.slug, "_frames")
    io.open(os.path.join(fr, "shot03.jpg"), "wb").write(b"x")
    d.run_flag_cli("shot03=翻转, shot05=正常")
    assert calls["regen"] == [(["shot03"], "human_flag")], calls["regen"]
    assert [p for p, _ in d.ledger.events] == ["human_flags", "human_flags"], d.ledger.events
    d2 = mk_driver(ledger=FakeLedger())

    def no_regen(*a, **k):
        raise AssertionError("全正常不应重烧")

    d2.__dict__["_regen_shots_strip_motion"] = no_regen
    d2.__dict__["notify"] = lambda m: None
    patch_set_user_flag(lambda flags, dataset_path=None, source=None: (len(flags), []))
    d2.run_flag_cli("shot05=正常")
    for bad in ("shot3x=翻转", "shot03", ""):
        try:
            mk_driver(ledger=FakeLedger()).run_flag_cli(bad)
            raise AssertionError("未拒非法输入: " + repr(bad))
        except SystemExit:
            pass


def t10():
    here = os.path.dirname(os.path.abspath(__file__))
    src = io.open(os.path.join(here, "run_pipeline.py"), encoding="utf-8").read()
    assert 'ap.add_argument("--flag-shots"' in src, "argparse 缺 --flag-shots"
    assert "drv.run_flag_cli(args.flag_shots)" in src, "main 缺短路"
    assert src.index("drv.run_flag_cli") < src.index("drv.run()"), "短路必须在 drv.run() 之前"


def _fresh_slug(tag):
    """每次跑用独立 slug 目录：manifest 是累积账，共用 slug 会让上一轮遗留
    条目干扰「条目数 = 1」的断言（真人和 CLI 指认都是同段追加一次）。"""
    d = mk_driver(ledger=FakeLedger(), notify=lambda *a, **k: None)
    d.slug = os.path.join(WORK, tag + "_" + str(int(time.time() * 1000)))
    os.makedirs(os.path.join(d.slug, "_frames"), exist_ok=True)
    return d


def _manifest(d):
    return json.load(io.open(os.path.join(d.slug, "_frames", "evidence",
                                         "manifest.json"), encoding="utf-8"))


def t11():
    """ADR 0008 AC1/AC2/AC3：指认即落账——manifest 人审桶条目（帧/哈希与盘上
    冻结帧一致）+ 行内同路径四字段 + 重复指认不追加、仍指回首帧。"""
    d = _fresh_slug("flag_mf")
    fr = os.path.join(d.slug, "_frames")
    src = os.path.join(fr, "shot07.jpg")
    io.open(src, "wb").write(b"frame-seven-bytes")
    patch_set_user_flag(lambda flags, dataset_path=None, source=None: (len(flags), []))
    saves = []
    patch_set_evidence_frames(recorder_frames(saves))

    ev = d._flag_commit("shot07", "翻转", "shot07 翻转了")
    assert ev and os.path.exists(ev), ev
    mf = _manifest(d)
    hits = [e for e in mf["evidence"]
            if e.get("category") == "4-human-flag" and e.get("sid") == "shot07"]
    assert len(hits) == 1, hits                      # AC1：条目存在且只有一条
    e0 = hits[0]
    assert e0["round"] == "human_flag", e0            # AC1：轮次口径
    assert e0["frame"] == ev, (e0["frame"], ev)       # AC1：frame = 盘上冻结帧
    assert e0["sha256"] == rd.sha256_file(ev), e0     # AC1：哈希与冻结帧一致
    assert len(e0["sha256"]) == 64, e0["sha256"]
    assert e0["source_frame"] == src, e0              # 冻结副本溯源到被覆盖前的工作帧
    assert e0["kind"] == "human_flag" and e0["local_verdict"] == "翻转", e0
    assert mf["episode"] == [d.date, os.path.basename(d.slug)], mf.get("episode")

    # AC2：行内条目 = 同一条目路径、四字段齐（多余键在写口投影掉，见 t13）
    assert len(saves) == 1, saves
    tup, = saves[0]
    assert (tup[0], tup[1], tup[2]) == (d.date, os.path.basename(d.slug), "shot07"), tup
    entry = tup[3]
    # 四字段齐（写口本身会把多余键投影掉——那一步由 t13 在真实写口上验；
    # 这里 set_evidence_frames 是记录器桩，收到的是 13 字段的 manifest 条目）
    assert {"frame", "sha256", "round", "category"} <= set(entry), entry
    assert entry["frame"] == e0["frame"] and entry["sha256"] == e0["sha256"], entry
    assert entry["round"] == "human_flag" and entry["category"] == "4-human-flag", entry

    # AC3：同一 (category, sid, round) 再指认（human-reflag）不追加条目，
    # 且连冻结副本都不再拷第二份——行内仍指回首帧（同路径硬要求）
    n_files = len(os.listdir(os.path.join(fr, "evidence", "human_flag")))
    ev2 = d._flag_commit("shot07", "翻转", "又看了一遍，确实翻了", who="human-reflag")
    assert ev2 == ev, (ev2, ev)
    hits2 = [e for e in _manifest(d)["evidence"]
             if e.get("category") == "4-human-flag" and e.get("sid") == "shot07"]
    assert len(hits2) == 1, hits2
    assert hits2[0]["frame"] == ev, hits2[0]
    assert len(os.listdir(os.path.join(fr, "evidence", "human_flag"))) == n_files, \
        "重复指认不该再拷一份冻结帧"
    assert saves[-1][0][3]["frame"] == ev, saves[-1]


def t12():
    """ADR 0008 AC4：指认回调返回时台账已 flush——新开 Ledger 只读盘上文件
    就能读到本次 human_flags 记录（不靠内存里的 _inc）。"""
    from ledger import Ledger
    d = _fresh_slug("flag_flush")
    rep = os.path.join(WORK, "rep_flush_" + str(int(time.time() * 1000)) + ".json")
    d.ledger = Ledger(rep)
    io.open(os.path.join(d.slug, "_frames", "shot08.jpg"), "wb").write(b"x8")
    patch_set_user_flag(lambda flags, dataset_path=None, source=None: (len(flags), []))
    patch_set_evidence_frames(recorder_frames([]))
    d._flag_commit("shot08", "翻转", "shot08 翻转了")
    assert os.path.exists(rep), "回调返回时台账文件必须已存在（flush 未发生？）"
    sids = [e["sid"] for e in Ledger(rep).get("human_flags", view="merged")]
    assert "shot08" in sids, sids


def t13():
    """ADR 0008 AC6：后置写口 set_evidence_frames 只补 evidence_frames（四字段
    投影、同帧幂等、兼容存量纯路径行、可标 stale），该行其它字段原值不变。"""
    rd.set_evidence_frames = _REAL_SET_EVIDENCE_FRAMES   # 前面的小节把它桩掉了
    ds = os.path.join(WORK, "ds_evframes_" + str(int(time.time() * 1000)) + ".jsonl")
    base = {"date": "2026-10-17", "slug": "daily-china-20261017", "shot": "shot11",
            "video_seconds": 3.0, "vision_verdict": "正常",
            "user_flagged": None, "user_flagged_source": None,
            "ground_truth_verdict": None,
            "judge_verdict": "翻转90", "judge_conf": 0.9, "evidence_frames": []}
    rd.write_rows([dict(base)], ds)
    key = ("2026-10-17", "daily-china-20261017", "shot11")
    fr_dir = os.path.join(WORK, "frames_t13_" + str(int(time.time() * 1000)))
    f1 = os.path.join(fr_dir, "f1.jpg")
    old_s08 = os.path.join(fr_dir, "old_s08.jpg")
    ent = {"frame": f1, "sha256": "a" * 64, "round": "human_flag",
           "category": "4-human-flag", "note": "多余键应被投影掉", "judge": "x"}
    upd, miss = rd.set_evidence_frames([(key[0], key[1], key[2], ent)], ds)
    assert upd == [key] and not miss, (upd, miss)
    row = rd.load(ds)[0]
    assert row["evidence_frames"] == [{
        "frame": ent["frame"], "sha256": ent["sha256"],
        "round": "human_flag", "category": "4-human-flag"}], row["evidence_frames"]
    for k, v in base.items():                       # AC6：其它字段一个都不许动
        if k == "evidence_frames":
            continue                                # 本写口唯一该变的字段
        assert row[k] == v, (k, row.get(k), v)

    # 幂等：同帧再写不追加
    rd.set_evidence_frames([(key[0], key[1], key[2], ent)], ds)
    assert len(rd.load(ds)[0]["evidence_frames"]) == 1

    # 存量行纯路径字符串形态并存（读取侧 rd.evidence_frame_path 归一）
    rd.write_rows([dict(base, evidence_frames=[old_s08])], ds)
    rd.set_evidence_frames([(key[0], key[1], key[2], ent)], ds)
    frames = rd.load(ds)[0]["evidence_frames"]
    assert len(frames) == 2, frames
    assert rd.evidence_frame_path(frames[0]) == old_s08
    assert rd.evidence_frame_path(frames[1]) == ent["frame"]

    # 帧被后续重烧覆盖 → 标 stale 留痕（ADR 0008 决策 5），不删账
    rd.set_evidence_frames([], ds, stale={key: "重烧覆盖工作帧"})
    row = rd.load(ds)[0]
    assert row["frame_stale"] is True, row
    assert row["frame_stale_reason"] == "重烧覆盖工作帧", row
    assert len(row["evidence_frames"]) == 2, "标 stale 不许删已记的帧"

    # 未命中键如实报 missing（不静默）
    upd2, miss2 = rd.set_evidence_frames(
        [("2026-01-01", "nope", "shot01", ent)], ds)
    assert upd2 == [] and miss2 == [("2026-01-01", "nope", "shot01")], (upd2, miss2)


if __name__ == "__main__":
    section("T17 人工指认回路")
    CASES = [
        ("1 classify flag 分类+零回归", t1),
        ("2 _ACTION/_decision", t2),
        ("3 _shots_from_text", t3),
        ("4 _flag_is_ok", t4),
        ("5 _flag_commit 存证/回填", t5),
        ("6 平键跨重启", t6),
        ("7 loop 不翻收口", t7),
        ("8 loop 仍翻交人", t8),
        ("9 run_flag_cli", t9),
        ("10 CLI 接线", t10),
        ("11 指认即落账 manifest+行内+去重", t11),
        ("12 回调返回前已 flush", t12),
        ("13 行内写口只补 evidence_frames", t13),
    ]
    for name, fn in CASES:
        check(name, fn)
    print()
    print("总计: " + str(len(PASS)) + " PASS / " + str(len(FAIL)) + " FAIL")
    if FAIL:
        print("FAIL: " + repr(FAIL))
        sys.exit(1)
    print("ALL PASS")