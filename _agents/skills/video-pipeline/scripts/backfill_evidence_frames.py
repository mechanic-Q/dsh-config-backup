#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_evidence_frames.py — 存量清洗：10-19 六条回填冻结帧、10-02 shot08 标帧失效（#193）。

背景
====
#189 收口的证据链（ADR 0008）要求「定罪帧进数据集行内」。2026-10-19 那六条人工指认
（shot01/05/08/12/14/19）的定罪帧一直只躺在
<期次>/video/每日新中国b站/流水线/daily-china-20261019/_frames/evidence/human_flag/ 里，
数据集行内 evidence_frames 是空的 —— 于是 rotate4_pool 只能退回工作帧，而工作帧早被
复检重抽覆盖（run_pipeline.py:1736 的留证注释说的就是这件事），等于把定罪前的画面
当定罪帧喂进池子。装这类存量是本脚本的活。

反向的一例是 2026-10-02 shot08：该期根本没有 evidence 目录（盘上没有任何冻结副本），
工作帧也已不可信，只能如实标 frame_stale=True（ADR 0008 决策 15）等人工复核；
user_flagged 与原结论一律不改。

用法
====
  python scripts/backfill_evidence_frames.py --dry-run   # 只打印将要写什么，不写盘
  python scripts/backfill_evidence_frames.py             # 写入数据集（唯一写口 set_evidence_frames）

幂等
====
- 回填走唯一写口 rd.set_evidence_frames：同一帧（按帧路径比对，兼容存量纯字符串）不重复追加；
- frame_stale 与原因每次重写都相同；
- 重跑后数据集字节完全一致；只动本脚本点名的六行 + 一行标 stale，其余期次任何行不碰
  （scripts/test_evidence_backfill.py 用前后指纹逐行比对）。

为什么是这一份副本（决策 14「按 sha256 去重选一份代表帧」）
==========================================================
10-19 的 evidence/ 下共 28 份副本（human_flag/ 27 份 + human_flagb/ 1 份），按 sha256
去重后是 16 个不同的帧。逐 shot 分成三组，只有一组真的撑得起「定罪帧」：

  1) 213757 / 215747 那两组（指认前的自动快照 + 首次指认瞬间）——VLM 复判为
     「正常」（shot08 0.41 / shot12 0.46 / shot14 0.43），不是定罪帧；
  2) shotNN_first.jpg 与当期工作帧 sha 相同（shot01/05/08/12/14 逐条核过），是复检
     重抽前的自动快照，带不出一份独有证据；
  3) 230641 与 010052 两轮 sha 完全相同（同一帧冻结了两次），且 2026-10-22 01:07
     的 VLM 复判在这份帧上判出 翻转（shot01 0.50 / shot05 0.48 / shot08 0.38 /
     shot12 0.53 / shot14 0.37）——**这才是定罪帧**，取更早的文件名 230641。

shot19 没有 010052 那一轮（_flag5 只点它五个），它的定罪帧是 22:12:26 判翻（0.39）后
落下的 human_flag/shot19.jpg：台账 report.json 的 s08.vision.evidence 条目在册
（round=human_flag，sha ddb2da3e…），221238 / 222325 / human_flagb 三份副本同 sha。

⇒ 每个 shot 只写一条「代表帧」。把 16 个不同帧全写进去不是更保险，而是有害：其中一半
是 VLM 判过「正常」的帧，写进行内证据帧会被 row_material 当成同一行的正类素材喂给
训练，等于往正类池里掺误标。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hostpath                    # noqa: E402 — 必须在 sys.path.insert 之后
import rotation_dataset as rd      # noqa: E402
import rotation_groundtruth as gt  # noqa: E402

CHANNEL_DIR = "每日新中国b站"
EPISODE_CLEAN = ("2026-10-19", "daily-china-20261019")
EPISODE_STALE = ("2026-10-02", "daily-china-20261002")

# 回填目标：行键 -> evidence/ 下的相对路径（口径见模块 docstring）。
REPRESENTATIVES = {
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot01"): "human_flag/shot01_20261020_230641.jpg",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot05"): "human_flag/shot05_20261020_230641.jpg",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot08"): "human_flag/shot08_20261020_230641.jpg",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot12"): "human_flag/shot12_20261020_230641.jpg",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot14"): "human_flag/shot14_20261020_230641.jpg",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot19"): "human_flag/shot19.jpg",
}

# 清点时逐份算出来的 sha256（2026-10-22）。作用只有一条：真跑时对一次账——盘上副本
# 若被人换过，这里会告警；写入的 sha256 永远取盘上实际值（AC1「与盘上一致」）。
RECORDED_SHA = {
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot01"):
        "e71d6f4a453315640295833c12df9a209a461cb20443a9855b07d4bcf9b19d2a",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot05"):
        "498ead46ddfe2989f0e757231d4436f516d26dc510b965d53a9fd6f1e91f42c1",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot08"):
        "f49282cc3ff8329a374ceca8ceb0a24eb85ee87196247d714c6137833d7eef4f",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot12"):
        "006f2e4b1c6b292bd52844b234e5783186db01818e440c887e185dcb831a15d4",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot14"):
        "393c82cc6e5b3bed1f1cc6c9a42abc3e4ad2d8cab6cea6e023d3192a95e83375",
    (EPISODE_CLEAN[0], EPISODE_CLEAN[1], "shot19"):
        "ddb2da3ea4c4a7ab07de44b7e4e4f7a68838d8795b3488351bff390dad3f24b4",
}

STALE_REASON = ("帧已覆盖、无冻结副本：复检重抽覆盖了 _frames 工作帧，"
                "该期 evidence 目录从未生成 → 等人工复核，勿当真值帧")
STALE_ROWS = {(EPISODE_STALE[0], EPISODE_STALE[1], "shot08"): STALE_REASON}


def _log(line):
    """打印一行中文日志（#170 口径：cp936 裸跑编不出的字符降级转义，不让 print 掀翻进程）。"""
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(enc, "backslashreplace").decode(enc, "replace"), flush=True)


def frames_dir(date, slug, date_root=None):
    """期次 _frames 目录（与 gt.frames_of 同一期次布局）。"""
    root = gt.DATE_ROOT if date_root is None else date_root
    return os.path.join(root, date, "video", CHANNEL_DIR, "流水线", slug, "_frames")


def evidence_file(date, slug, rel, date_root=None):
    """evidence/ 下的相对路径 → 绝对路径。"""
    return os.path.join(frames_dir(date, slug, date_root), "evidence", *rel.split("/"))


def survey(date, slug, date_root=None):
    """一期 evidence/ 下全部副本 → {sha256: [相对路径, ...]}（按 sha 去重，不看文件名）。

    只读不写，用于日志里把「多少份副本 → 多少个不同帧」摆出来，让 AC2 可核对。"""
    base = os.path.join(frames_dir(date, slug, date_root), "evidence")
    groups = {}
    if not os.path.isdir(base):
        return groups
    for tag in sorted(os.listdir(base)):
        tag_dir = os.path.join(base, tag)
        if not os.path.isdir(tag_dir):
            continue
        for fn in sorted(os.listdir(tag_dir)):
            fp = os.path.join(tag_dir, fn)
            if not os.path.isfile(fp):
                continue
            sha = rd.sha256_file(fp)
            if not sha:
                continue
            groups.setdefault(sha, []).append(tag + "/" + fn)
    return groups


def build_entries(date_root=None, log=_log):
    """REPRESENTATIVES + 盘上实际 sha256 → set_evidence_frames 的 entries。

    返回 (entries, missing, mismatched)：缺盘的行不进 entries（该行保持原样），
    mismatched 是与 RECORDED_SHA 对不上账的行键（只告警，仍写入盘上实际 sha）。"""
    entries, missing, mismatched = [], [], []
    for key in sorted(REPRESENTATIVES):
        date, slug, shot = key
        rel = REPRESENTATIVES[key]
        fp = evidence_file(date, slug, rel, date_root)
        if not os.path.exists(fp):
            missing.append(key)
            log("!! 冻结副本缺盘，跳过该行：%s %s -> %s" % (date, shot, fp))
            continue
        sha = rd.sha256_file(fp)
        if not sha:
            missing.append(key)
            log("!! 冻结副本读不出内容，跳过该行：%s" % fp)
            continue
        exp = RECORDED_SHA.get(key, "")
        if exp and sha != exp:
            mismatched.append((key, exp, sha))
            log("!! sha256 与行内记录不符（盘上 %s… / 清点时 %s…）：%s"
                % (sha[:12], exp[:12], rel))
        entries.append((date, slug, shot, {
            "frame": hostpath.to_win_path(os.path.abspath(fp)),
            "sha256": sha,
            "round": rd.HUMAN_FLAG_ROUND,
            "category": rd.HUMAN_FLAG_CATEGORY,
        }))
    return entries, missing, mismatched


def verify(dataset_path=None, date_root=None, log=_log):
    """写后自查：六行有且只有一条在盘 sha 相符的证据帧；stale 行旗标与原因到位。

    返回 (ok, problems)。查的是盘上现状，不看脚本内部状态——所以重跑后照样能自证。"""
    dataset_path = dataset_path or rd.DATASET
    rows = {(r.get("date"), r.get("slug"), r.get("shot")): r for r in rd.load(dataset_path)}
    problems = []
    for key, rel in REPRESENTATIVES.items():
        date, slug, shot = key
        row = rows.get(key)
        if row is None:
            problems.append("%s %s 数据集里没有这一行" % (date, shot))
            continue
        frames = row.get("evidence_frames") or []
        if len(frames) != 1:
            problems.append("%s %s 行内证据帧 %d 条（应为 1 条）" % (date, shot, len(frames)))
            continue
        frame = rd.evidence_frame_path(frames[0])
        if not frames[0].get("sha256"):
            problems.append("%s %s 证据帧缺 sha256" % (date, shot))
        elif frames[0]["sha256"] != rd.sha256_file(frame):
            problems.append("%s %s 证据帧 sha256 与盘上不符" % (date, shot))
        if frames[0].get("round") != rd.HUMAN_FLAG_ROUND:
            problems.append("%s %s 证据帧 round 不是 %s" % (date, shot, rd.HUMAN_FLAG_ROUND))
        if frames[0].get("category") != rd.HUMAN_FLAG_CATEGORY:
            problems.append("%s %s 证据帧 category 不是 %s"
                            % (date, shot, rd.HUMAN_FLAG_CATEGORY))
    for key, reason in STALE_ROWS.items():
        date, slug, shot = key
        row = rows.get(key)
        if row is None:
            problems.append("%s %s 数据集里没有这一行" % (date, shot))
            continue
        if row.get("frame_stale") is not True:
            problems.append("%s %s 没有 frame_stale=True" % (date, shot))
        if (row.get("frame_stale_reason") or "") != reason:
            problems.append("%s %s frame_stale_reason 与预期不符" % (date, shot))
        if row.get("user_flagged") is not True:
            problems.append("%s %s user_flagged 被改动了" % (date, shot))
    for p in problems:
        log("!! " + p)
    return (not problems), problems


def run(dataset_path=None, date_root=None, dry_run=False, log=_log):
    """清洗主流程：清点 → 组条目 → （真跑时）单次写入 → 自查。返回退出码。"""
    dataset_path = dataset_path or rd.DATASET
    entries, missing, mismatched = build_entries(date_root, log)
    log("== 回填目标 %d 行；缺盘 %d 行；sha256 对账不符 %d 行"
        % (len(entries), len(missing), len(mismatched)))

    groups = survey(EPISODE_CLEAN[0], EPISODE_CLEAN[1], date_root)
    copies = sum(len(v) for v in groups.values())
    log("== 10-19 证据副本 %d 份 → 按 sha256 去重 %d 个不同帧（每个 shot 只取 1 份代表帧）"
        % (copies, len(groups)))
    for sha in sorted(groups, key=lambda s: groups[s][0]):
        log("   %s… %s" % (sha[:12], "、".join(groups[sha])))

    if missing:
        log("!! 有冻结副本缺盘，未写入任何内容（先核 10-19 evidence 目录再重跑）")
        return 2

    # 同一帧只留一条证据（AC2）：行内若已有一条 sha256 相同的副本（换了文件名/时间戳
    # 的另一份冻结），就不再追加——否则重跑换个代表帧就会在同一条行上叠第二条。
    rows = {(r.get("date"), r.get("slug"), r.get("shot")): r
            for r in rd.load(dataset_path)}
    fresh = []
    for date, slug, shot, entry in entries:
        row = rows.get((date, slug, shot))
        if row is None:
            fresh.append((date, slug, shot, entry))
            continue
        have = [rd.evidence_frame_path(x) for x in (row.get("evidence_frames") or [])]
        same_sha = any(rd.sha256_file(fp) == entry["sha256"] for fp in have if fp)
        if entry["frame"] in have or same_sha:
            log("== 已在册，跳过：%s %s（行内已有同 sha256 的证据帧）" % (date, shot))
            continue
        fresh.append((date, slug, shot, entry))
    entries = fresh

    if dry_run:
        for date, slug, shot, entry in entries:
            log("[DRY] 回填 %s %s <- %s（sha256 %s…）"
                % (date, shot, os.path.basename(entry["frame"]), entry["sha256"][:12]))
        for key, reason in STALE_ROWS.items():
            log("[DRY] 标帧失效 %s %s：%s" % (key[0], key[2], reason))
        log("[DRY] 未写盘（去掉 --dry-run 才动数据集）")
        return 0

    updated, not_found = rd.set_evidence_frames(entries, dataset_path=dataset_path,
                                                stale=STALE_ROWS)
    log("== 写入完成：命中 %d 行；数据集里找不到 %d 个行键"
        % (len(updated), len(not_found)))
    for key in not_found:
        log("!! 数据集无此键：%s %s %s" % key)

    ok, problems = verify(dataset_path, date_root, log)
    log("== 自查：%s（%d 处问题）" % ("通过" if ok else "未通过", len(problems)))
    return 0 if ok and not not_found else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="存量清洗：回填 10-19 冻结帧、标 10-02 帧失效（#193）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写什么，不写盘")
    ap.add_argument("--dataset", default=None,
                    help="数据集路径（缺省 references 下那份；测试传临时文件）")
    ap.add_argument("--date-root", default=None,
                    help="期次根（缺省 E: 盘帧根；测试传临时目录）")
    a = ap.parse_args(argv)
    return run(dataset_path=a.dataset, date_root=a.date_root, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
