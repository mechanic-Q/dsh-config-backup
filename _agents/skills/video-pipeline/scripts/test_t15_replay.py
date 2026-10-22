
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_t15_replay.py — t15_replay 纯函数测试（不判官、不碰活数据集）。

沿用仓库风格：裸脚本断言直跑，分节打印，末尾汇总。覆盖 Spec 测试决定的
可离线部分：四象限合成、①类合成 sid 防折叠、三类目录布局、manifest 结构、
数据集 patch 字段语义（judge_* 新增、本地原判不动）。

#148：第 5 节原先直接读仓库内那份每期 append 的活数据集，断言「2026-10-15
期 19 行」——数据一增长测试就红，且测试会碰到真值文件。现在改读
scripts/fixtures/t15_rotation_dataset_20261015.jsonl（该期冻结快照）。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import t15_replay as tr
import dsh_judge as dj

# 2026-10-15 期数据集快照（#148）：真值文件每期增长，测试只认冻结快照。
FIXTURE = os.path.join(HERE, "fixtures", "t15_rotation_dataset_20261015.jsonl")

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


# ── 1 四象限合成（dsh_judge.merge 直连） ────────────────────────────────
section("四象限合成")
items = [
    {"sid": "shot01", "judge": "正常", "conf": 0.9, "why": "t"},
    {"sid": "shot02", "judge": "翻转90", "conf": 0.9, "why": "t"},
    {"sid": "shot03", "judge": "翻转90", "conf": 0.9, "why": "t"},
    {"sid": "shot04", "judge": "正常", "conf": 0.9, "why": "t"},
]
local = {"shot01": "翻转90", "shot02": "正常", "shot03": "翻转90", "shot04": "正常"}
m = dj.merge(local, items, round_tag="round0")
check(m["confirmed"] == ["shot03"], "本地翻×判官翻 → confirmed")
check(m["judge_found"] == ["shot02"], "本地正常×判官翻 → judge_found（漏报）")
check(m["local_false"] == ["shot01"], "本地翻×判官正常 → local_false（误报）")
check(m["judge_normal"] == ["shot04"], "双方正常 → judge_normal")
check(dj.reburn_list(m) == ["shot02", "shot03"], "重烧名单=confirmed+judge_found")

# ── 2 ①类合成 sid 防折叠（首跑实证：下划线变体折叠回 shot02 三段互染） ──
section("合成对照 sid 防折叠")
check(tr.sid_norm("shot02rot90") == "shot02", "shot02rot90 归一为 shot02（判官侧）")
check(tr.sid_norm("SHOT3") == "shot03", "大小写与位数宽容")
# 判官 items 里 sid 归一后仍是独立键（parse_final 侧由 dsh_judge 测试覆盖）

# ── 3 三类目录与 manifest 结构（dryrun 落盘逻辑，判官 dry 不真跑） ──────
section("dryrun 落盘结构")
# 只验常量与路径拼接，不真跑 main（main 会 rmtree OUT_DIR）
check(tr.CAT_1 == "1-confirmed" and tr.CAT_2 == "2-judge-found"
      and tr.CAT_3 == "3-unfixed", "三类目录名固定")
check(tr.EPISODE == ("2026-10-15", "daily-china-20261015"), "期锚正确")
check(tr.HUMAN_FLIPS == ["shot03", "shot09", "shot15", "shot18", "shot19"],
      "人工确认 5 段锚正确")
check(tr.MAX_ROUNDS == 3, "三轮判读上限")
# frame_of 必须返回本机口径且在盘；Windows 口径只用于判官 prompt（往返一致，#140）
#
# #141：真帧在 E: 盘（本期素材），CI 的 ubuntu 上没有这块盘。原写法直接断言
# 「本机有 5 段真帧」——那是拿开发机的素材当被测行为，换个宿主必红。改为：
# 有真帧就走完整断言（开发机口径不变）；无真帧则**大声 SKIP**（docs/agents/
# test-conventions.md §5：说明缺什么、跳过哪几节、退出码仍 0），并把 platform
# 无关的那半（路径往返、manifest 形状）用合成帧照验，不把整节一起丢掉。
if tr.frame_of("shot03") is not None:
    fp = tr.frame_of("shot03")
    check(fp is not None and fp.endswith("shot03.jpg"), "frame_of 返回本机口径在盘路径")
    win = dj.to_win_path(fp)
    host = dj.to_host_path(win)
    check(host == fp and os.path.exists(host),
          "转换后的 Windows 路径在盘（to_host_path 往返一致）")
    check(all(tr.frame_of(s) for s in tr.HUMAN_FLIPS), "5 段帧全部在盘")
else:
    print("  SKIP: 本期真帧不在盘（%s 下 %s 的 _frames/）——"
          "跳过「5 段真帧在盘」3 条断言；"
          % (tr.DATE_ROOT_WIN, tr.EPISODE[1]))
    print("        该宿主无 E: 盘（CI/ubuntu 属此列），素材断言在开发机跑。")
    print("        替代：路径往返在合成帧上仍验（下面 3 条）。")
    import tempfile as _tf
    _td = _tf.mkdtemp(prefix="t15_frames_")
    _sid = "shot03"
    _fake = os.path.join(_td, _sid + ".jpg")
    with open(_fake, "wb") as _f:
        _f.write(b"\xff\xd8\xff\xd9")   # 最小 JPEG 头尾，只需在盘
    win = dj.to_win_path(_fake)
    check(os.path.isabs(win) and win.endswith(_sid + ".jpg"),
          "to_win_path 产出绝对 Windows 口径路径")
    host = dj.to_host_path(win)
    check(host == _fake, "to_host_path(to_win_path(p)) 往返一致")
    check(os.path.exists(host), "往返后的本机路径仍在盘")
    import shutil as _sh
    _sh.rmtree(_td, ignore_errors=True)

# ── 4 save_evidence 条目结构（copied=False 落账不落盘） ─────────────────
# #141：本节原来拿 shot09 的真帧当输入，真帧不在盘（CI）就整节崩。验的是
# 「条目结构」而非「素材在不在」，源帧换成临时合成帧，任何宿主同一条断言。
section("证据条目结构")
rec = {"judge": "翻转90", "conf": 0.95, "why": "w", "local": "正常",
       "synthetic": True, "note": "n"}
_ev_src_dir = tempfile.mkdtemp(prefix="t15_src_")
_ev_src = os.path.join(_ev_src_dir, "shot09.jpg")
with open(_ev_src, "wb") as _f:
    _f.write(b"\xff\xd8\xff\xd9")
e = tr.save_evidence(tr.CAT_2, "shot09", "round0", _ev_src, rec,
                     copied=False)
check(e["category"] == tr.CAT_2 and e["sid"] == "shot09" and e["round"] == "round0",
      "条目带 category/sid/round")
check(e["frame"].endswith("round0_shot09.jpg"), "帧名带轮次标识")
check(len(e["sha256"]) == 64, "sha256 溯源字段")
check(e["frame_win"] == dj.to_win_path(e["frame"]), "frame_win 为 Windows 口径")
check(os.path.exists(e["source_frame"]) and e["synthetic"] is True,
      "源帧在盘 + synthetic 旗标")
shutil.rmtree(_ev_src_dir, ignore_errors=True)

# ── 5 数据集 patch 字段语义（对 manifest 现有产物做只读校验） ───────────
section("数据集回写字段语义")
mf = os.path.join(tr.OUT_DIR, "manifest.json")
if os.path.exists(mf):
    man = json.load(open(mf, encoding="utf-8"))
    p = man["dataset_patches"]
    check(sorted(s for s, v in p.items()
                 if "翻" in (v.get("judge_verdict") or "")) == sorted(man["human_flips"]),
          "manifest：判官抓出的翻转段与人工确认一致")
    for sid in man["human_flips"]:
        check(p[sid]["judge_flip_rounds"] == ["round0", "round1", "round2"],
              f"{sid} 三轮判翻轮次完整")
    check(all(v.get("judge_rounds") for v in p.values()), "19 段都有 judge_rounds 台账")
    # 期次快照校验（#148 AC2）：读 scripts/fixtures/ 冻结快照，不读仓库内活数据集——
    # 活文件每期 append，断言「某期 19 行」会随数据增长变红，且跑测试就碰到真值文件。
    rows = [json.loads(l) for l in open(FIXTURE, encoding="utf-8") if l.strip()]
    r15 = [r for r in rows if r["date"] == "2026-10-15"]
    check(all(r["vision_verdict"] == "正常" for r in r15), "本地原判 19/19 未动")
    check(sum(1 for r in r15 if r.get("judge_verdict")) == 19, "judge_verdict 19 行齐全")
    check(sorted(r["shot"] for r in r15 if r.get("user_flagged") is True)
          == sorted(man["human_flips"]), "user_flagged 只回填人工确认 5 段")
else:
    print("  （无 manifest 产物，跳过——先跑 t15_replay.py run）")

print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
