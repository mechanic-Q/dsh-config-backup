#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_evidence_backfill.py — 存量清洗脚本的自检（#193，无框架，assert 直跑）。

背景：scripts/backfill_evidence_frames.py 是一次性数据操作，它要改活数据集
（references/ 下那份）。活数据集没有任何测试兜底，所以这套自检必须在**临时目录
合成的 episode + 临时数据集**上把五条验收钉死：写什么（代表帧 / 四字段 / sha256
与盘一致）、按什么去重（sha256 不是文件名）、标什么（帧失效原因）、下游怎么读
（rotate4_pool 取冻结帧、stale 行不进池但仍算判定数）、以及重跑是否同一结果。

可移植性（#141 口径）：不碰 E: 盘、不碰活数据集——CI（ubuntu）没有媒体树、
也没有「每日新中国」期次目录，全部在 tempfile 里造。脚本的期次根由 date_root
注入，数据集由 dataset 注入，两条口都是既有实现（run/build_entries 已留）。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backfill_evidence_frames as bf  # noqa: E402 — 被测脚本本身
import clip_train as ct                # noqa: E402 — 下游读取侧（rotate4_pool）
import rotation_dataset as rd          # noqa: E402 — sha256 / 四字段词表
import rotation_groundtruth as gt      # noqa: E402 — 判定数口径（load_labeled）

SECTIONS = []

CLEAN_DATE, CLEAN_SLUG = bf.EPISODE_CLEAN
STALE_DATE, STALE_SLUG = bf.EPISODE_STALE
CLEAN_SHOTS = ("shot01", "shot05", "shot08", "shot12", "shot14", "shot19")


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def write_bytes(path, data):
    """按需建父目录后写字节（帧内容必须互不相同——sha256 校验看的是内容）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def frames_dir(root, date, slug):
    return os.path.join(root, date, "video", "每日新中国b站", "流水线", slug, "_frames")


def mk_dataset(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def row_key(r):
    return (r.get("date"), r.get("slug"), r.get("shot"))


tmp = tempfile.mkdtemp(prefix="backfill193_")
ds = os.path.join(tmp, "dataset.jsonl")

# ── 合成 10-19 一期：工作帧 + evidence/ 下六轮副本（形状照抄真盘） ──────────
clean_fd = frames_dir(tmp, CLEAN_DATE, CLEAN_SLUG)
ev_dir = os.path.join(clean_fd, "evidence", "human_flag")
ev_dirb = os.path.join(clean_fd, "evidence", "human_flagb")
work_bytes = {}
for s in CLEAN_SHOTS:
    work_bytes[s] = ("WORK-" + s).encode()
    write_bytes(os.path.join(clean_fd, s + ".jpg"), work_bytes[s])
    if s != "shot19":                       # 真盘里 shot19 没有 _first
        write_bytes(os.path.join(clean_fd, s + "_first.jpg"),
                    ("WORKFIRST-" + s).encode())
for s in CLEAN_SHOTS:
    # 判翻前的自动快照（VLM 判过正常的那些），sha 与定罪帧不同
    write_bytes(os.path.join(ev_dir, s + "_20261020_213757.jpg"),
                ("PREFLAG-" + s).encode())
    # 定罪帧：同一帧被两轮冻结，文件名不同、字节相同（shot19 只判过一轮，裸文件名那份）
    conv = ("CONVICT-" + s).encode()
    if s != "shot19":
        write_bytes(os.path.join(ev_dir, s + "_20261020_230641.jpg"), conv)
        write_bytes(os.path.join(ev_dir, s + "_20261022_010052.jpg"), conv)
    # 复检重抽前的自动快照，与工作帧同字节（带不出独有证据）
    write_bytes(os.path.join(ev_dir, s + "_first.jpg"), work_bytes[s])
# 同期一条没被点名的行也要有工作帧（否则那条行会被 frames_of 直接丢掉，验不到「不碰它」）
write_bytes(os.path.join(clean_fd, "shot03.jpg"), b"WORK-shot03")
# shot19 的定罪帧还留在裸文件名上；human_flagb/ 那份与它同 sha
write_bytes(os.path.join(ev_dir, "shot19.jpg"), ("CONVICT-shot19").encode())
write_bytes(os.path.join(ev_dirb, "shot19.jpg"), ("CONVICT-shot19").encode())

# ── 合成 10-02 一期：只有工作帧，evidence/ 目录根本不存在（无冻结副本） ────
stale_fd = frames_dir(tmp, STALE_DATE, STALE_SLUG)
write_bytes(os.path.join(stale_fd, "shot08.jpg"), b"STALEWORK-shot08")

# ── 合成一期「别的期次」：清洗绝不许碰它 ────────────────────────────────
other_date, other_slug = "2026-09-08", "daily-china-20260908"
other_fd = frames_dir(tmp, other_date, other_slug)
write_bytes(os.path.join(other_fd, "shot03.jpg"), b"OTHER-shot03")

rows = []
for s in CLEAN_SHOTS:
    rows.append({"date": CLEAN_DATE, "slug": CLEAN_SLUG, "shot": s,
                 "user_flagged": True, "user_flagged_source": "human-flag",
                 "ground_truth_verdict": "翻转", "vision_verdict": "正常",
                 "evidence_frames": []})
# 同期一条没被点名的行：回填只认点名行，不许把这个期次整体填满
rows.append({"date": CLEAN_DATE, "slug": CLEAN_SLUG, "shot": "shot03",
             "user_flagged": False, "vision_verdict": "正常"})
rows.append({"date": STALE_DATE, "slug": STALE_SLUG, "shot": "shot08",
             "user_flagged": True, "user_flagged_source": "bench-review-20261014",
             "ground_truth_verdict": None, "vision_verdict": "正常"})
rows.append({"date": other_date, "slug": other_slug, "shot": "shot03",
             "user_flagged": False, "vision_verdict": "正常"})

# 预置一条「同 sha 但不同文件名」的旧证据（shot12 早被人拿 010052 那份手工补过）：
# 重跑不许在同一条行上叠第二条（AC2「同一帧只留一条证据」按 sha256 成立）。
pre_fp = os.path.join(ev_dir, "shot12_20261022_010052.jpg")
for r in rows:
    if r["shot"] == "shot12" and r["date"] == CLEAN_DATE:
        r["evidence_frames"] = [{"frame": pre_fp, "sha256": rd.sha256_file(pre_fp),
                                 "round": rd.HUMAN_FLAG_ROUND,
                                 "category": rd.HUMAN_FLAG_CATEGORY}]
mk_dataset(ds, rows)

target_keys = {(CLEAN_DATE, CLEAN_SLUG, s) for s in CLEAN_SHOTS}
target_keys.add((STALE_DATE, STALE_SLUG, "shot08"))

# 清洗前的基线：这些行之后必须原样；点名行此刻还没有冻结帧可用
before_rows = {row_key(r): json.dumps(r, ensure_ascii=False) for r in load_rows(ds)}
tr0, te0, pos0 = ct.rotate4_pool(dataset=ds, date_root=tmp,
                                 references_dir=tmp, frames_root=tmp)

# ── 1. 代表帧 → 四字段条目，sha256 与盘上一致（AC1） ────────────────────
section("六条写四字段证据帧，sha256 与盘上一致")
entries, missing, mismatched = bf.build_entries(tmp, log=lambda s: None)
check(len(entries) == 6, f"六条全部组出条目（实 {len(entries)} 条）")
check(not missing, "盘上副本齐全，没有缺盘的行")
check(len(mismatched) == 6, "合成盘与脚本里记的清点 sha 不同 → 对账告警逐条响（只告警不改写入）")
# 换成「清点记录与盘上一致」的账，重跑组条目：告警该消失，写入的仍取盘上实际 sha
bf.RECORDED_SHA = {(d, s, shot): e["sha256"] for d, s, shot, e in entries}
entries, missing, mismatched = bf.build_entries(tmp, log=lambda s: None)
check(len(entries) == 6 and not mismatched, "账实一致时不告警，条目照旧六条")
for date, slug, shot, e in entries:
    check(set(e) == {"frame", "sha256", "round", "category"},
          f"{shot} 条目只带 frame/sha256/round/category 四个键")
    check(os.path.exists(e["frame"]), f"{shot} 证据帧路径在盘上（{os.path.basename(e['frame'])}）")
    check(rd.sha256_file(e["frame"]) == e["sha256"], f"{shot} sha256 与盘上实际字节一致")
    check(e["round"] == rd.HUMAN_FLAG_ROUND and e["category"] == rd.HUMAN_FLAG_CATEGORY,
          f"{shot} round={rd.HUMAN_FLAG_ROUND} / category={rd.HUMAN_FLAG_CATEGORY}")
by_shot = {shot: os.path.basename(e["frame"]) for _d, _s, shot, e in entries}
for s in ("shot01", "shot05", "shot08", "shot12", "shot14"):
    check(by_shot[s] == s + "_20261020_230641.jpg",
          f"{s} 取两轮同帧里更早的那份（{by_shot[s]}）")
check(by_shot["shot19"] == "shot19.jpg", f"shot19 取裸文件名那份（{by_shot['shot19']}）")

# ── 2. 按 sha256 去重，不按文件名/时间戳（AC2） ─────────────────────────
section("同帧多副本按 sha256 去重，同一行只留一条")
groups = bf.survey(CLEAN_DATE, CLEAN_SLUG, tmp)
for s in ("shot01", "shot05", "shot08", "shot12", "shot14"):
    sha = rd.sha256_file(os.path.join(ev_dir, s + "_20261020_230641.jpg"))
    files = groups.get(sha, [])
    check(len(files) >= 2,
          f"{s} 定罪帧有两份副本（{len(files)} 份），sha256 把它们并成一组")
    check(not any("_first" in x for x in files),
          f"{s} 与工作帧同字节的 _first 快照没混进这组")
sha19 = rd.sha256_file(os.path.join(ev_dir, "shot19.jpg"))
check(sorted(groups.get(sha19, [])) == ["human_flag/shot19.jpg", "human_flagb/shot19.jpg"],
      "shot19 跨目录（human_flag/ + human_flagb/）同 sha 也只算一帧")

rc = bf.run(dataset_path=ds, date_root=tmp, log=lambda s: None)
check(rc == 0, f"清洗退出码 0（实 {rc}）")
rows_after = {row_key(r): r for r in load_rows(ds)}
for s in CLEAN_SHOTS:
    fr = rows_after[(CLEAN_DATE, CLEAN_SLUG, s)].get("evidence_frames") or []
    check(len(fr) == 1, f"{s} 行内只有一条证据帧（实 {len(fr)} 条）")
    check(rd.sha256_file(rd.evidence_frame_path(fr[0])) == fr[0]["sha256"],
          f"{s} 行内 sha256 与盘上一致")
check(rd.evidence_frame_path(rows_after[(CLEAN_DATE, CLEAN_SLUG, "shot12")]
                             ["evidence_frames"][0]) == pre_fp,
      "shot12 保留了手工补的那条（同 sha 不同文件名，不叠第二条）")
check(not (rows_after[(CLEAN_DATE, CLEAN_SLUG, "shot03")].get("evidence_frames")),
      "同期没被点名的行没有被填证据帧")

# ── 3. 10-02 shot08：标帧失效 + 原因，人工结论不动（AC3） ───────────────
section("无冻结副本的那条标 frame_stale，人工结论保持不变")
stale_row = rows_after[(STALE_DATE, STALE_SLUG, "shot08")]
check(stale_row.get("frame_stale") is True, "frame_stale 为 True")
check(stale_row.get("frame_stale_reason") == bf.STALE_REASON,
      "frame_stale_reason 写明「帧已覆盖、无冻结副本」")
check(stale_row.get("user_flagged") is True, "user_flagged 仍为 True（人工指认不撤回）")
check(stale_row.get("ground_truth_verdict") is None, "ground_truth_verdict 仍为空（原结论不变）")
check(stale_row.get("vision_verdict") == "正常", "vision_verdict 仍为「正常」（不被清洗覆盖）")
check(not (stale_row.get("evidence_frames") or []),
      "没有凭空给 stale 行塞证据帧")

# ── 4. 下游：六条取冻结帧、stale 行不进池但仍是判定数（AC4） ─────────────
section("rotate4_pool 取到冻结帧，stale 行不进池但仍计入判定数")
pos0_by = {(it["date"], it["shot"]): it["path"] for it in pos0}
check(len(pos0_by) == 7, f"清洗前七个点名行都在 pos 里（实 {len(pos0_by)} 条）")
for s in CLEAN_SHOTS:
    if s == "shot12":
        continue                      # 预置了手工补的证据帧，清洗前就该取到它
    p0 = pos0_by[(CLEAN_DATE, s)].replace(os.sep, "/")
    check("/evidence/" not in p0,
          f"清洗前 {s} 只能退回工作帧（{os.path.basename(p0)}，定罪帧躺在 evidence/ 里没进行内）")

tr, te, pos = ct.rotate4_pool(dataset=ds, date_root=tmp,
                              references_dir=tmp, frames_root=tmp)
pos_by = {(it["date"], it["shot"]): it["path"] for it in pos}
pool = tr + te
pool_keys = {(it["date"], it["shot"]) for it in pool}
for s in CLEAN_SHOTS:
    p = pos_by.get((CLEAN_DATE, s), "")
    check("/evidence/human_flag/" in p.replace(os.sep, "/"),
          f"{s} 取到复查后仍存在的冻结帧（{os.path.basename(p)}）")
    stored = rd.evidence_frame_path(rows_after[(CLEAN_DATE, CLEAN_SLUG, s)]
                                   ["evidence_frames"][0])
    check(p == stored, f"{s} 取的正是行内那条证据帧（{os.path.basename(stored)}）")
    if s != "shot12":                 # shot12 保留手工补的那份，见 §2
        check(os.path.basename(p) == by_shot[s],
              f"{s} 取的代表帧是 {by_shot[s]}")
    check((CLEAN_DATE, s) not in pool_keys, f"{s} 作为真值不进训练/留出池")
check((STALE_DATE, "shot08") not in pos_by, "frame_stale 那条不进 pos")
check(not any(it["shot"] == "shot08" and it["date"] == STALE_DATE for it in pool),
      "frame_stale 那条也不进池（两侧都不出现）")
check((other_date, "shot03") in pool_keys, "别的期次的普通行照旧进池")
check((CLEAN_DATE, "shot03") in pool_keys, "同期的普通行照旧进池（取工作帧）")

pos_l, neg_l = gt.load_labeled(ds)
judged_keys = {(r["date"], r["shot"]) for r in pos_l + neg_l}
check((STALE_DATE, "shot08") in judged_keys,
      f"stale 那条仍在判定数里（判定数 {len(pos_l) + len(neg_l)} 行，只按 user_flagged 算）")

# ── 5. 可重入：重跑同一结果、其它期次行一字不动（AC5） ───────────────────
section("清洗可重入，且不触碰点名之外的行")
with open(ds, "rb") as f:
    bytes1 = f.read()
dry_rc = bf.run(dataset_path=ds, date_root=tmp, dry_run=True, log=lambda s: None)
with open(ds, "rb") as f:
    check(dry_rc == 0 and f.read() == bytes1, "--dry-run 不写盘（字节不变）")
rc2 = bf.run(dataset_path=ds, date_root=tmp, log=lambda s: None)
with open(ds, "rb") as f:
    bytes2 = f.read()
check(rc2 == 0, f"重跑退出码 0（实 {rc2}）")
check(bytes2 == bytes1, "重跑后数据集逐字节相同（同一结果）")

after_rows = {row_key(r): json.dumps(r, ensure_ascii=False) for r in load_rows(ds)}
untouched = [k for k in before_rows if k not in target_keys]
check(untouched, "确实存在点名之外的行（否则这条断言在自证空气）")
for k in untouched:
    check(after_rows[k] == before_rows[k],
          f"{'/'.join(map(str, k))} 一字未动")

ok, problems = bf.verify(ds, tmp, log=lambda s: None)
check(ok, f"写后自查通过（问题 {problems}）")

print(f"\n{len(SECTIONS)} 节全部通过："
      + " / ".join(SECTIONS))
