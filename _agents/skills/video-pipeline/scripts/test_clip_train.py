#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_clip_train.py — rotate4 训练素材选择的自检（#108/#119/#191，无框架，assert 直跑）。

背景：rotate4 探针的召回数字只在「真实翻转帧从不参训」时才有意义（否则是同义
反复）。训练集自然组成（人放行池按日期留出）与泄漏边界必须钉死，否则一次
手滑就会把 29 个真值帧放进 pool，指标立刻虚高、上线后全崩。

可移植性（#141 口径）：选材逻辑在**临时目录合成的 episode** 上验，不依赖本机
E: 盘真素材——CI（ubuntu）没有媒体树也要能跑。真数据集的存在性另有独立断言，
帧不在盘时打 SKIP 明示，不当成通过。

覆盖：
1. 人工点名翻转帧（user_flagged）永不出现在 pool（训练+留出）
2. pool / 留出的日期切分是 md5(整期日期)%3 == HELD_OUT_MOD 且两半互斥、并集=全池
3. 帧不在盘的行被丢弃（不许拿空气训练）
4. 留出比例落在 1/3 附近（切分没退化成全留出或全训练）
5. 真数据集：真值与训练池零重叠（本机有帧时才断言）
6. #191 点名行优先取冻结证据帧：工作帧被重烧覆盖后再取，仍取冻结帧（sha 硬验收）
7. #191 sha256 与账不符 → 该行整行消失（不回落工作帧）且告警打到 stderr
8. #191 frame_stale 行不出现在 pool / pos 任何一边
9. #191 纯路径字符串证据仍可消费（无 sha 时按账上 sha 现算比对）；桶帧作候选
10. #191 frames_of 保持纯物理列举语义（证据帧不掺进物理列举，真值判定只在 truth_frame）
"""
import hashlib
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import clip_train as ct  # noqa: E402 — 必须在 sys.path.insert 之后
import rotation_dataset as rd  # noqa: E402 — sha256 与真值帧四字段词表
import rotation_groundtruth as gt  # noqa: E402 — frames_of/truth_frame 同源对照

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def mk_episode(root, date, slug, shots):
    """合成一期：<root>/<date>/video/每日新中国b站/流水线/<slug>/_frames/<shot>.jpg"""
    fd = os.path.join(root, date, "video", "每日新中国b站", "流水线", slug, "_frames")
    os.makedirs(fd, exist_ok=True)
    for s in shots:
        with open(os.path.join(fd, f"{s}.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff\xd9")  # 只要 exists()，内容无关


def frames_dir(root, date, slug):
    """工作帧目录（与 mk_episode 同一期次布局）。"""
    return os.path.join(root, date, "video", "每日新中国b站", "流水线", slug, "_frames")


def write_bytes(path, data):
    """按需建父目录后写字节（#191 的帧内容必须互不相同——sha256 校验要看内容）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def mk_dataset(root, rows, name="dataset.jsonl"):
    """合成行集 → 临时数据集文件（绝不写活数据集）。"""
    p = os.path.join(root, name)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def mk_manifest(root, date, slug, entries):
    """合成离线证据桶清单：<root>/t15-evidence-<yyyymmdd>/manifest.json（同 #130 形状）。"""
    ev_dir = os.path.join(root, "t15-evidence-" + date.replace("-", ""))
    os.makedirs(ev_dir, exist_ok=True)
    m = {"episode": [date, slug], "human_flips": [], "rounds": [],
         "evidence": entries, "judge_calls": [], "negative_control": []}
    with open(os.path.join(ev_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


tmp = tempfile.mkdtemp(prefix="cliptrain_")
dates = ["2026-09-0%d" % i for i in range(1, 7)]  # 6 个日期，md5%3 的分档会撞
rows = []
for i, d in enumerate(dates):
    slug = f"daily-china-{d.replace('-', '')}"
    mk_episode(tmp, d, slug, ["shot01", "shot02"])
    rows.append({"date": d, "slug": slug, "shot": "shot01",
                 "user_flagged": i % 3 == 0})                     # 真值行
    rows.append({"date": d, "slug": slug, "shot": "shot02",
                 "user_flagged": False})                          # 人放行行
# 帧不在盘的两行（一条真值 + 一条放行）：都必须被丢掉
rows.append({"date": "2026-09-30", "slug": "daily-china-20260930", "shot": "shot99",
             "user_flagged": True})
rows.append({"date": "2026-09-30", "slug": "daily-china-20260930", "shot": "shot98",
             "user_flagged": False})
ds = os.path.join(tmp, "dataset.jsonl")
with open(ds, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

# references_dir / frames_root 也指到临时树：#191 起 rotate4_pool 会对点名行扫
# 证据桶（evidence_by_row），不注入就会去扫仓内 references/ 与本机 E: 盘——
# 合成 episode 上没有桶件，注入空根既快又不掺真账（#141 可移植口径）。
tr, te, pos = ct.rotate4_pool(dataset=ds, date_root=tmp,
                               references_dir=tmp, frames_root=tmp)

# ── 1. 真值不参训 ──────────────────────────────────────────────────────
section("翻转真值不进训练池")
check(len(pos) == 2, f"人工点名且帧在盘 = {len(pos)} 条（缺帧那条被丢掉）")
pos_keys = {(it["date"], it["shot"]) for it in pos}
pool_keys = {(it["date"], it["shot"]) for it in tr + te}
check(not (pos_keys & pool_keys), f"pool {len(pool_keys)} 条与真值 {len(pos_keys)} 条零重叠")
check(all(it["date"] != "2026-09-30" for it in tr + te + pos),
      "帧不在盘的行（2026-09-30）不进 pool 也不进真值")

# ── 2. 日期哈希切分 ────────────────────────────────────────────────────
section("日期哈希切分互斥且穷尽")


def held(d):
    return int(hashlib.md5(str(d).encode()).hexdigest()[:8], 16) % 3 == ct.HELD_OUT_MOD


check(all(held(it["date"]) for it in te), "留出集每条都满足 md5(日期)%3==HELD_OUT_MOD")
check(not any(held(it["date"]) for it in tr), "训练集没有任何一条来自留出期")
check(len(tr) + len(te) == len(pool_keys), f"两半并集 = 全池（{len(tr)} + {len(te)}）")
check(len({it["date"] for it in tr} & {it["date"] for it in te}) == 0, "train/test 日期互斥")

# ── 3. 字段完整 ────────────────────────────────────────────────────────
section("素材字段与帧在盘")
check(all(it.get("date") and it.get("shot") and it.get("path") for it in tr + te + pos),
      f"全部 {len(tr) + len(te) + len(pos)} 条都带 date/shot/path")
check(all(os.path.exists(it["path"]) for it in tr + te + pos), "每条路径都真的在盘")

# ── 4. 留出比例 ────────────────────────────────────────────────────────
section("留出比例未退化")
ratio = len(te) / max(1, len(tr) + len(te))
check(0 < ratio < 1, f"留出占比 {ratio:.0%}（0%/100% 说明切分退化）")

# ── 5. 真数据集（本机有帧才断言）──────────────────────────────────────
rtr, rte, rpos = ct.rotate4_pool()
if not (rtr and rte and rpos):
    print(f"\n!! SKIP(缺帧)：真数据集训练帧 {len(rtr)} / 留出 {len(rte)} / 真值 {len(rpos)}"
          " —— 第 5 节需要本机媒体树在盘；本宿没有时字段与切分逻辑已由合成 episode 覆盖")
else:
    section("真数据集零重叠")
    check(len(rpos) >= 20, f"真值 {len(rpos)} 条（人工点名翻转）")
    check(not ({(i["date"], i["shot"]) for i in rpos}
               & {(i["date"], i["shot"]) for i in rtr + rte}), "真值与训练池零重叠")
    check(len(rtr) > len(rte), f"训练 {len(rtr)} 条 > 留出 {len(rte)} 条（1/3 期留出）")

# ── 6. 冻结帧优先（#191 唯一硬验收）───────────────────────────────────
section("点名行取冻结证据帧（工作帧重烧覆盖后仍取冻结帧）")
r6 = tempfile.mkdtemp(prefix="truth191a_")
d6, s6 = "2026-10-16", "daily-china-20261016"
fd6 = frames_dir(r6, d6, s6)
work6 = os.path.join(fd6, "shot01.jpg")
frozen6 = os.path.join(r6, "t15-evidence-20261016", rd.HUMAN_FLAG_CATEGORY,
                       "human_flag_shot01.jpg")
write_bytes(work6, b"WORK-BEFORE-REBURN")
write_bytes(frozen6, b"FROZEN-CONVICTED-FRAME")
sha6 = rd.sha256_file(frozen6)
row6 = {"date": d6, "slug": s6, "shot": "shot01", "user_flagged": True,
        "evidence_frames": [{"frame": frozen6, "sha256": sha6,
                             "round": rd.HUMAN_FLAG_ROUND,
                             "category": rd.HUMAN_FLAG_CATEGORY}]}
ds6 = mk_dataset(r6, [row6])
kw6 = dict(dataset=ds6, date_root=r6, references_dir=r6, frames_root=r6)
tr6, te6, pos6 = ct.rotate4_pool(**kw6)
check(len(pos6) == 1 and pos6[0]["path"] == frozen6,
      f"点名行取行内冻结帧：{os.path.basename(frozen6)}")
check(not ({(i["date"], i["shot"]) for i in pos6}
           & {(i["date"], i["shot"]) for i in tr6 + te6}), "冻结帧行不进训练池")
# 硬验收：把工作帧覆盖重写（模拟打回重烧），再取一次——仍是冻结帧
write_bytes(work6, b"WORK-AFTER-REBURN-DIFFERENT")
tr6b, te6b, pos6b = ct.rotate4_pool(**kw6)
check(len(pos6b) == 1 and pos6b[0]["path"] == frozen6,
      "工作帧被覆盖重写后再取，仍是冻结帧（唯一硬验收）")
check(rd.sha256_file(pos6b[0]["path"]) == sha6, "取到的确实是冻结副本内容（sha 对得上）")

# ── 7. sha256 不符 → 整行消失 ─────────────────────────────────────────
section("sha256 与账不符整行消失且告警")
r7 = tempfile.mkdtemp(prefix="truth191b_")
d7, s7 = "2026-10-16", "daily-china-20261016"
fd7 = frames_dir(r7, d7, s7)
work7 = os.path.join(fd7, "shot01.jpg")
frozen7 = os.path.join(r7, "t15-evidence-20261016", rd.HUMAN_FLAG_CATEGORY,
                       "human_flag_shot01.jpg")
write_bytes(work7, b"WORK-7")
write_bytes(frozen7, b"FROZEN-7")
row7 = {"date": d7, "slug": s7, "shot": "shot01", "user_flagged": True,
        "evidence_frames": [{"frame": frozen7, "sha256": "0" * 64,
                             "round": rd.HUMAN_FLAG_ROUND,
                             "category": rd.HUMAN_FLAG_CATEGORY}]}
buf7 = io.StringIO()
with redirect_stderr(buf7):
    tr7, te7, pos7 = ct.rotate4_pool(dataset=mk_dataset(r7, [row7]), date_root=r7,
                                     references_dir=r7, frames_root=r7)
check(not [i for i in tr7 + te7 + pos7 if (i["date"], i["shot"]) == (d7, "shot01")],
      "账实不符的点名行 pool / pos 两侧都不出现（不回落工作帧）")
warn7 = buf7.getvalue()
check("sha256" in warn7 and frozen7 in warn7,
      "告警打到 stderr 且点名到帧（账/实两值同现）")
# 告警前缀带 U+26A0/U+FE0F，cp936 控制台编不出：裸跑（无 PYTHONIOENCODING）不许崩
raw7 = io.BytesIO()
sink7 = io.TextIOWrapper(raw7, encoding="gbk", write_through=True)
old7, sys.stderr = sys.stderr, sink7
try:
    gt._warn("cp936 降级测试")
finally:
    sys.stderr = old7
sink7.flush()
warn_txt = raw7.getvalue().decode("gbk")
check("真值帧" in warn_txt and "cp936" in warn_txt and "\\u26a0" in warn_txt,
      "cp936 流向告警降级转义（编不出的字符写成 \\u26a0，不抛 UnicodeEncodeError）")

# ── 8. frame_stale 行两侧都不出现 ─────────────────────────────────────
section("frame_stale 行不进 pool / pos")
r8 = tempfile.mkdtemp(prefix="truth191s_")
d8, s8 = "2026-10-16", "daily-china-20261016"
fd8 = frames_dir(r8, d8, s8)
frozen8 = os.path.join(r8, "t15-evidence-20261016", rd.HUMAN_FLAG_CATEGORY,
                       "human_flag_shot01.jpg")
write_bytes(frozen8, b"FROZEN-8")
write_bytes(os.path.join(fd8, "shot01.jpg"), b"WORK-01")
write_bytes(os.path.join(fd8, "shot02.jpg"), b"WORK-02")
write_bytes(os.path.join(fd8, "shot03.jpg"), b"WORK-03")
rows8 = [
    # stale + 点名 + 账上 sha 全对：stale 优先级最高，仍不许进 pos
    {"date": d8, "slug": s8, "shot": "shot01", "user_flagged": True, "frame_stale": True,
     "evidence_frames": [{"frame": frozen8, "sha256": rd.sha256_file(frozen8),
                          "round": rd.HUMAN_FLAG_ROUND,
                          "category": rd.HUMAN_FLAG_CATEGORY}]},
    # stale + 未点名 + 工作帧在盘：不许进 pool
    {"date": d8, "slug": s8, "shot": "shot02", "user_flagged": False, "frame_stale": True},
    # 对照：未 stale 的未点名行照常进池
    {"date": d8, "slug": s8, "shot": "shot03", "user_flagged": False},
]
tr8, te8, pos8 = ct.rotate4_pool(dataset=mk_dataset(r8, rows8), date_root=r8,
                                 references_dir=r8, frames_root=r8)
keys8 = {(i["date"], i["shot"]) for i in tr8 + te8 + pos8}
check((d8, "shot01") not in keys8, "stale 点名行不进 pos（证据帧齐全也不进）")
check((d8, "shot02") not in keys8, "stale 未点名行不进 pool")
check((d8, "shot03") in keys8, "未 stale 的行照常进池（剔除是精准的）")

# ── 9. 纯路径字符串证据 + 桶候选 ─────────────────────────────────────
section("纯路径字符串证据可消费、桶帧可作候选")
r9 = tempfile.mkdtemp(prefix="truth191c_")
d9, s9 = "2026-10-17", "daily-china-20261017"
fd9 = frames_dir(r9, d9, s9)
ev9 = os.path.join(r9, "t15-evidence-20261017", rd.HUMAN_FLAG_CATEGORY)
fa = os.path.join(ev9, "human_flag_shot01.jpg")
fb = os.path.join(ev9, "human_flag_shot02.jpg")
fc = os.path.join(ev9, "human_flag_shot03.jpg")
fdd = os.path.join(ev9, "human_flag_shot04.jpg")
for fp, data in ((fa, b"FROZEN-A-9"), (fb, b"FROZEN-B-9"),
                 (fc, b"FROZEN-C-9"), (fdd, b"FROZEN-D-9")):
    write_bytes(fp, data)
sha_a, sha_b, sha_c = rd.sha256_file(fa), rd.sha256_file(fb), rd.sha256_file(fc)
for sid in ("shot01", "shot02", "shot03", "shot05"):
    write_bytes(os.path.join(fd9, sid + ".jpg"), b"WORK-" + sid.encode())
mk_manifest(r9, d9, s9, [
    {"category": rd.HUMAN_FLAG_CATEGORY, "sid": "shot01",
     "round": rd.HUMAN_FLAG_ROUND, "frame": fa, "sha256": sha_a},
    {"category": rd.HUMAN_FLAG_CATEGORY, "sid": "shot02",
     "round": rd.HUMAN_FLAG_ROUND, "frame": fb, "sha256": sha_b},
    {"category": rd.HUMAN_FLAG_CATEGORY, "sid": "shot03",
     "round": rd.HUMAN_FLAG_ROUND, "frame": fc, "sha256": sha_c},
    {"category": rd.HUMAN_FLAG_CATEGORY, "sid": "shot04",
     "round": rd.HUMAN_FLAG_ROUND, "frame": fdd, "sha256": "0" * 64},
])
# 写账之后篡改 fb：账上 sha_b 是改前内容的 → 账实不符
write_bytes(fb, b"TAMPERED-B-9")
rows9 = [
    # ① 行内纯路径字符串（无 sha）+ 桶账上 sha 一致 → 可用（无 sha 时现算比对）
    {"date": d9, "slug": s9, "shot": "shot01", "user_flagged": True, "evidence_frames": [fa]},
    # ② 同形态但账实不符 → 整行消失
    {"date": d9, "slug": s9, "shot": "shot02", "user_flagged": True, "evidence_frames": [fb]},
    # ③ 无行内条目，桶条目作候选（阶段 2）
    {"date": d9, "slug": s9, "shot": "shot03", "user_flagged": True},
    # ④ 桶账 sha 不符且工作帧缺盘 → 整行无可用帧
    {"date": d9, "slug": s9, "shot": "shot04", "user_flagged": True},
    # ⑤ 未点名行即使带 evidence_frames 也取工作帧（优先级只对点名行生效）
    {"date": d9, "slug": s9, "shot": "shot05", "user_flagged": False, "evidence_frames": [fc]},
]
buf9 = io.StringIO()
with redirect_stderr(buf9):
    tr9, te9, pos9 = ct.rotate4_pool(dataset=mk_dataset(r9, rows9), date_root=r9,
                                     references_dir=r9, frames_root=r9)
paths9 = {(i["date"], i["shot"]): i["path"] for i in tr9 + te9 + pos9}
check(paths9.get((d9, "shot01")) == fa, "纯路径字符串证据仍可消费（按桶账上 sha 现算比对）")
check((d9, "shot02") not in paths9, "字符串证据账实不符 → 整行消失（不回落工作帧）")
check(paths9.get((d9, "shot03")) == fc, "行内无条目时证据桶帧作候选（优先级第 2 层）")
check((d9, "shot04") not in paths9, "桶账不符且无工作帧 → 整行无可用帧")
check(paths9.get((d9, "shot05")) == os.path.join(fd9, "shot05.jpg"),
      "未点名行仍取工作帧（真值优先级只对点名行生效）")
check(pos9 and all(i["path"].startswith(r9) for i in pos9), "真值帧路径落在注入的帧根内")

# ── 10. frames_of 纯物理列举不变 ──────────────────────────────────────
section("frames_of 保持纯物理列举语义")
r10 = tempfile.mkdtemp(prefix="truth191d_")
d10, s10 = "2026-10-18", "daily-china-20261018"
fd10 = frames_dir(r10, d10, s10)
write_bytes(os.path.join(fd10, "shot01.jpg"), b"W10")
write_bytes(os.path.join(fd10, "shot01_90.jpg"), b"W10-VARIANT")
frozen10 = os.path.join(r10, "t15-evidence-20261018", rd.HUMAN_FLAG_CATEGORY,
                        "human_flag_shot01.jpg")
write_bytes(frozen10, b"F10")
row10 = {"date": d10, "slug": s10, "shot": "shot01", "user_flagged": True,
         "evidence_frames": [frozen10]}
fps10 = gt.frames_of(row10, date_root=r10)
check(len(fps10) == 2 and fps10 == sorted(fps10),
      f"只列举工作帧（中帧+变体 {len(fps10)} 个，按 sorted 顺序）")
check(frozen10 not in fps10, "证据帧不掺进 frames_of（真值判定只在 truth_frame）")
check(gt.truth_frame(row10, date_root=r10, references_dir=r10, frames_root=r10)
      == frozen10, "同一行 truth_frame 取冻结帧——两条通道语义分离")

print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
