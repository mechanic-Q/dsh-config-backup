
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t15_replay.py — 离线复现 10-15 期判读闭环（T15 #127，Spec #132 交付顺序第 1 步）。

流水线零改动：只读真实历史期产物（_frames/shotNN.jpg），把「本地先判 + 判官复判 +
三类存证 + 数据集回写」整条链在仓库脚本层跑通一遍，产出基准报告素材。

本地原判（数据集 vision_verdict，当日 2026-10-16 04:41 现场留痕）：19/19 全「正常」
——与 Spec 记载一致（5 漏报 0 误报）。本脚本不重跑本地模型，直接取留痕原判。

轮次模拟（Spec 轮次语义，重烧按判官为准 Q8）：
  round0   全量 19 段判官复判
  round1/2 重烧名单复判——离线无 GPU 不真重烧，帧为原始现场帧；「三轮仍未修好」
           语义由「round0/1/2 三轮判读后仍判翻」承载，manifest 明标无重烧帧
  synthetic ①类补位——10-15 期本地 0 误翻，四象限的「判官确认翻转」（本地翻×
           判官翻）结构上为空。按仓库既有判官基准手段（合成对照标签由构造保证），
           取 shot02 旋转 90/180 + 本地判定=翻转让判官确认，得真①类素材；同批
           帧连跑两轮结论必须一致（Spec 测试决定）；原图作负对照。

三类素材（每条带轮次，manifest 含 sha256 溯源）：
  1-confirmed   判官确认翻转（正类；本期由合成对照承载）
  2-judge-found 判官推翻本地判读（本地漏报，强负类；本期真实主类）
  3-unfixed     三轮仍未修好（最难正类；离线形态见上）

数据集回写：19 行 +judge_* 字段（judge_verdict/conf/why/flip_rounds/rounds/source），
本地原判 vision_verdict 一字不动；5 段人工确认翻转走 set_user_flag 回填
user_flagged=true（T9 既有通道，只动真值字段）。

CLI:
  python scripts/t15_replay.py plan     # 只打印四象限计划（不判官不写盘）
  python scripts/t15_replay.py run      # 真跑：判官 → 存证 → 回写 → manifest
  python scripts/t15_replay.py dryrun   # 判官 dry，验落盘逻辑
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dsh_judge as dj
import rotation_dataset as rd

REPO = os.path.dirname(HERE)
# 帧根目录（双端同源素材，E: 盘）：统一拼 Windows 口径绝对路径，读盘前由
# to_host_path 换回本机口径（#140：此前直接把 Windows 路径丢给 os.path.exists，
# WSL 下恒 False → frame_of 返回 None，test_t15_replay 唯一宿主红）。
DATE_ROOT_WIN = dj.win_join("E:", "每日新中国")
EPISODE = ("2026-10-15", "daily-china-20261015")
HUMAN_FLIPS = ["shot03", "shot09", "shot15", "shot18", "shot19"]  # 人工确认 5 段
OUT_DIR = os.path.join(REPO, "references", "t15-evidence-20261015")
MAX_ROUNDS = 3  # 三轮判读（round0 全量 + round1/2 重烧段），对齐 Spec「三轮仍不一致交人」

CAT_1, CAT_2, CAT_3 = "1-confirmed", "2-judge-found", "3-unfixed"


def sid_norm(s):
    return "shot%02d" % int(re.match(r"shot0*([0-9]+)", s, re.I).group(1))


def frame_of(sid):
    """段号 → 中帧绝对路径（本机口径，读盘/PIL 用；判官 prompt 侧再 to_win_path）。"""
    win = dj.win_join(DATE_ROOT_WIN, EPISODE[0], "video", "每日新中国b站",
                      "流水线", EPISODE[1], "_frames", sid + ".jpg")
    fp = dj.to_host_path(win)
    return fp if os.path.exists(fp) else None


def local_verdicts():
    """本地原判（数据集 vision_verdict 留痕）——不重判、不改写。"""
    rows = {r["shot"]: r for r in rd.load()
            if r["date"] == EPISODE[0] and r["slug"] == EPISODE[1]}
    return rows, {sid: r.get("vision_verdict") or "未判" for sid, r in rows.items()}


def sha256(fp):
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_evidence(category, sid, round_tag, frame_src, rec, copied):
    """素材落盘：帧复制进 category 子目录（roundTag 前缀），条目带绝对路径+哈希。"""
    cat_dir = os.path.join(OUT_DIR, category)
    os.makedirs(cat_dir, exist_ok=True)
    dst = os.path.join(cat_dir, f"{round_tag}_{sid}.jpg")
    if copied:
        shutil.copy2(frame_src, dst)
    return {
        "category": category, "sid": sid, "round": round_tag,
        "frame": dst, "frame_win": dj.to_win_path(dst),
        "source_frame": frame_src, "sha256": sha256(frame_src),
        "judge": rec.get("judge"), "conf": rec.get("conf"), "why": rec.get("why"),
        "local_verdict": rec.get("local"), "synthetic": rec.get("synthetic", False),
        "note": rec.get("note", ""),
    }


def judge_one_round(tag, sids, local, dry):
    """跑一轮判官 → (res, merged, call)。round0 与重烧轮都报本地原判（对照锚不变）。"""
    rows_in = [{"sid": s, "verdict": local.get(s, "未判"), "frame": frame_of(s)}
               for s in sids]
    res = dj.judge(rows_in, dry=dry)
    call = {"round": tag, "n": len(rows_in), "ok": res["ok"], "wall_s": res["wall_s"],
            "in_tok": res["in_tok"], "out_tok": res["out_tok"], "error": res["error"]}
    if not res["ok"]:
        return res, None, call
    merged = dj.merge(local, res["items"], round_tag=tag)
    return res, merged, call


def synthetic_confirmed(dry):
    """①类补位：shot02 合成 90/180 翻转 + 本地判定=翻转 → 判官确认（正类）。

    同批帧连跑两次判官（Spec 测试决定：结论必须一致）；原图作负对照（判翻即
    判官不可信，整批作废记账）。sid 用 shot02rot90/shot02rot180——下划线变体
    会被 sid_norm 折叠回 shot02（首跑实证：三条同名段互相污染判官输出）。
    """
    from PIL import Image
    syn_dir = os.path.join(OUT_DIR, CAT_1)
    os.makedirs(syn_dir, exist_ok=True)
    src_frame = frame_of("shot02")
    im = Image.open(src_frame)
    frames = {}
    for deg in (90, 180):
        # 生成源直接落存证目录归档名（copied=False 复用字节），避免产生
        # 第二份临时下划线文件让 source_frame 在清理后悬空。
        fp = os.path.join(syn_dir, f"synthetic_shot02rot{deg}.jpg")
        if not os.path.exists(fp):
            im.transpose(getattr(Image, f"ROTATE_{deg}")).save(fp)
        frames[f"shot02rot{deg}"] = (fp, f"翻转{deg}")
    rows_in = [{"sid": "shot02rot90", "verdict": "翻转90",
                "frame": frames["shot02rot90"][0]},
               {"sid": "shot02rot180", "verdict": "翻转180",
                "frame": frames["shot02rot180"][0]},
               {"sid": "shot02", "verdict": "正常", "frame": src_frame}]  # 负对照
    entries, calls, runs = [], [], []
    for run_tag in ("synthetic-a", "synthetic-b"):
        res = dj.judge(rows_in, dry=dry)
        calls.append({"round": run_tag, "n": len(rows_in), "ok": res["ok"],
                      "wall_s": res["wall_s"], "in_tok": res["in_tok"],
                      "out_tok": res["out_tok"], "error": res["error"]})
        if not res["ok"]:
            if dry:
                return [], calls
            return [{"category": CAT_1, "round": run_tag,
                     "error": f"判官失败: {res['error']}"}], calls
        runs.append({it["sid"]: it for it in res["items"]})
    a, b = runs
    for sid in ("shot02rot90", "shot02rot180", "shot02"):
        if dj.is_flip(a[sid]["judge"]) != dj.is_flip(b[sid]["judge"]):
            return [{"category": CAT_1, "round": "synthetic",
                     "error": f"两轮判官结论不一致: {sid} {a[sid]['judge']} vs {b[sid]['judge']}"}], calls
    for sid, (fp, local_label) in frames.items():
        it = a[sid]
        if not dj.is_flip(it["judge"]):
            entries.append({"category": CAT_1, "sid": sid, "round": "synthetic",
                            "error": "合成翻转未被确认", "judge": it["judge"]})
            continue
        rec = {"judge": it["judge"], "conf": it.get("conf"), "why": it.get("why"),
               "local": local_label, "synthetic": True,
               "note": "构造样本（shot02 旋转），标签由构造保证；同批帧两轮判官结论一致"}
        entries.append(save_evidence(CAT_1, sid, "synthetic", fp, rec, copied=False))
    neg = a["shot02"]
    if dj.is_flip(neg["judge"]):
        entries.append({"category": CAT_1, "sid": "shot02", "round": "synthetic",
                        "error": "负对照异常：原图被判翻", "judge": neg["judge"]})
    return entries, calls


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if mode == "plan":
        rows, local = local_verdicts()
        flips = sorted(s for s, v in local.items() if dj.is_flip(v))
        print("本地原判翻转:", flips or "无（0/19 —— 与当日现场一致：本地漏报 5 段）")
        print("人工确认翻转:", HUMAN_FLIPS)
        print("轮次计划: round0 全量 19 段 → 重烧名单=判官判翻段 → round1..%d 只判重烧段"
              % (MAX_ROUNDS - 1))
        return 0

    dry = mode == "dryrun"
    global OUT_DIR
    if dry:
        # dryrun 也 rmtree OUT_DIR：隔离到 -dryrun 目录，防止误毁真实存证
        # （教训：一次 dryrun 曾把真跑的 22 张证据帧全删——帧可由源重拷，但
        #  manifest 里的判官真实判词/Token 台账不可再生）。
        OUT_DIR = OUT_DIR + "-dryrun"
    rows, local = local_verdicts()
    sids = sorted(rows)
    assert len(sids) == 19, "期产物应为 19 段，实得 %d" % len(sids)
    missing = [s for s in sids if not frame_of(s)]
    if missing:
        print("缺帧:", missing)
        return 1
    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    manifest = {"episode": list(EPISODE), "human_flips": HUMAN_FLIPS,
                "rounds": [], "evidence": [], "judge_calls": [],
                "negative_control": []}
    patches = {}   # sid → judge_* 字段
    burn = list(sids)            # round0 = 全量
    final_flips = set()
    for round_no in range(MAX_ROUNDS):
        tag = f"round{round_no}"
        res, merged, call = judge_one_round(tag, burn, local, dry)
        manifest["judge_calls"].append(call)
        if not res["ok"]:
            if dry:
                print(f"[{tag}] dry 跳过判官（落盘逻辑验证）")
                break
            print(f"[{tag}] 判官失败: {res['error']}")
            return 1
        print(f"[{tag}] {dj.summarize(merged)}")
        # 存证：判官判翻段——①类（本地也翻）进 1-confirmed，②类（本地漏报）进 2-judge-found
        kind_cat = {"confirmed": CAT_1, "judge_found": CAT_2}
        for r in merged["rows"]:
            if r["kind"] not in kind_cat:
                continue
            rec = {"judge": r["judge"], "conf": r.get("conf"), "why": r.get("why"),
                   "local": r["local"], "synthetic": False,
                   "note": "" if round_no == 0 else "离线无重烧，帧仍为原始现场帧"}
            manifest["evidence"].append(
                save_evidence(kind_cat[r["kind"]], r["sid"], tag, frame_of(r["sid"]),
                              rec, copied=not dry))
        # 数据集 patch：judge_* 字段（轮次与分歧全量留台账）
        for r in merged["rows"]:
            p = patches.setdefault(r["sid"], {})
            p["judge_verdict"] = r["judge"]
            p["judge_conf"] = r.get("conf")
            p["judge_why"] = r.get("why", "")
            if dj.is_flip(r["judge"]):
                p.setdefault("judge_flip_rounds", []).append(tag)
            p.setdefault("judge_rounds", []).append(
                {"round": tag, "local": r["local"], "judge": r["judge"],
                 "kind": r["kind"], "conf": r.get("conf"), "why": r.get("why", "")})
        burn = dj.reburn_list(merged)
        final_flips = set(burn)
        manifest["rounds"].append({"round": tag, "judged": len(merged["rows"]),
                                   "reburn": burn, "summary": dj.summarize(merged)})
        if not burn:
            print(f"[{tag}] 重烧名单清空，闭环收敛")
            break
        if dry:
            break
    # ③ 三轮仍未修好：收敛后仍判翻段（离线形态：原始帧 + 三轮判读记录）
    for sid in sorted(final_flips):
        p = patches[sid]
        rec = {"judge": p["judge_verdict"], "conf": p["judge_conf"], "why": p["judge_why"],
               "local": local.get(sid, "未判"), "synthetic": False,
               "note": "离线复现不烧卡：round1/2 无真实重烧帧，证据=判官三轮复判仍翻的"
                       "原始现场帧；真机三轮素材归 T16 生产回合"}
        manifest["evidence"].append(
            save_evidence(CAT_3, sid, "final", frame_of(sid), rec, copied=not dry))
    # ①类补位 + 同批帧两轮一致性（Spec 测试决定）
    syn_entries, syn_calls = synthetic_confirmed(dry)
    manifest["judge_calls"].extend(syn_calls)
    for e in syn_entries:
        (manifest["negative_control"] if e.get("error") else manifest["evidence"]).append(e)
    if any(e.get("error") for e in syn_entries) and not dry:
        print("①类合成确认失败:", [e.get("error") for e in syn_entries if e.get("error")])
        return 1

    if not dry:
        # 人工真值回填（T9 通道：只动 user_flagged/user_flagged_source）
        updated, missing_k = rd.set_user_flag(
            [(EPISODE[0], EPISODE[1], s, True) for s in HUMAN_FLIPS],
            source="t15-offline-replay-20261016")
        print(f"回填 user_flagged=true: {len(updated)} 行（missing={missing_k}）")
        # 数据集回写 judge_* 字段（本地原判 vision_verdict 不动）
        ds_rows = rd.load()
        for r in ds_rows:
            if (r["date"], r["slug"]) != EPISODE:
                continue
            p = patches.get(r["shot"])
            if not p:
                continue
            r["judge_verdict"] = p.get("judge_verdict")
            r["judge_conf"] = p.get("judge_conf")
            r["judge_why"] = p.get("judge_why", "")
            r["judge_flip_rounds"] = p.get("judge_flip_rounds", [])
            r["judge_rounds"] = p.get("judge_rounds", [])
            r["judge_source"] = "dsh_judge/deepseek-v4.1-flash (T14 #126)"
        # 走单一写口（#148 AC5）：测试期只读模式下这里会被拒，不再有绕过守卫的裸 open
        rd.write_rows(ds_rows)
        print(f"数据集回写: {len(patches)} 行 judge_* 字段")

    manifest["dataset_patches"] = patches
    mf = os.path.join(OUT_DIR, "manifest.json")
    with open(mf, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("manifest →", mf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
