#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rotation_groundtruth.py — 旋转真值库收集与 CLIP 训练清单导出（#108 前置）。

真值单一事实源 = references/rotation-dataset.jsonl 的 user_flagged 字段
（true=人工确认翻转 / false=人工确认未翻转 / null=未知），标注来源记录在
user_flagged_source（如 bench-review-20261014 = 盲比审核页人工复核）。

CLI:
  rotation_groundtruth.py stats                    # 真值进度 + 判定数/场景数（换岗门槛一条命令）
  rotation_groundtruth.py export --out 清单.jsonl   # 导出 CLIP 训练清单
      每行 {label: 翻转|正常, frames: [帧绝对路径], synthetic: bool,
            date, shot, source}
      默认只导人工确认行；--synthetic 追加「确认正常帧旋转 90/180/270」的
      合成翻转样本（H3 翻转 = 90° 族，与真实失败模式同分布；标签由构造保证）
  rotation_groundtruth.py export --dry-run         # 只列被选素材清单不写文件
      （T18 #130：dry-run 不 import PIL/open_clip/torch——裸机器可跑；
       每行打印 row/category/round/帧路径/label + 判定数/场景数汇总）

T18 #130 素材进训练链（ADR 0008 起四类桶）：
- 证据桶（references/t15-evidence-*/ 离线件 + <slug>/_frames/evidence/ 生产件，
  rotation_dataset.load_evidence_buckets）按 (date,slug,shot) 链接数据集行——
  四类×轮次区分，正类/强负类/最难正类/人审正类都进候选清单；桶帧随行进
  export/dry-run（训练读取路径真的读证据桶）。
- 行内 evidence_frames 兼容纯路径字符串（T11 s08 现场挂链）与 ADR 0008 的
  {frame, sha256, round, category} 四字段形态，统一经 rd.evidence_frame_path 归一。
- export 每行带 vision_verdict/judge_verdict 独立字段；label 只由 user_flagged
  （人工真值）决定——人机结论分字段、互不覆盖。

读取侧真值帧（#191 / ADR 0008 决策 3、7）：truth_frame() 是「行 → 训练/评测
真值帧」的单一解析点——user_flagged=True 按「行内证据帧 → 证据桶帧 → 工作帧」
优先级取帧并对每个候选做 sha256 硬校验（账实不符即弃帧并告警，且不回落工作帧）；
frame_stale 行一律返回空（消费侧不进评测池）。frames_of 仍是纯物理列举，
row_material / row_frames 等物理列举消费方行为不变。
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hostpath
import rotation_dataset as rd

# 帧根目录（双端同源素材，E: 盘）：只写一处 Windows 口径常量，落到当前宿主的
# 实际口径由 hostpath.to_host_path 换算（#169 收口，与 vision_blindbench /
# vision_bench_review 的 DATE_ROOT 同源）。旧的 os.path.exists 本地判断已在
# #143 定性：/proc/net/route 在任何 Linux（含容器/CI）都有，只证明「不是
# Windows」不证明「是 WSL」——裸 Linux 会把帧根判到 /mnt/e 而那里既无 E: 盘
# 也无挂载点，frames_of() 静默返回空（与 #157 修的「全空且不报错」同病）。
DATE_ROOT_WIN = r"E:\每日新中国"
DATE_ROOT = hostpath.to_host_path(DATE_ROOT_WIN)
GOAL = 100
SCENE_GOAL = 50  # T10 换岗门槛（#119）：真值 ≥100 判定 且 ≥50 场景
# T18 #130：证据桶三类 → 行内训练语义（桶的「强负类素材」是素材语义，
# 行标签仍只由人工真值决定——见 export label 口径）
EVIDENCE_CATEGORIES = rd.EVIDENCE_CATEGORIES
CATEGORY_NOTES = rd.CATEGORY_NOTES


def frames_of(row, date_root=None):
    """行 → 全部在盘帧（中帧 + 首/尾变体），绝对路径。

    date_root 可覆盖帧根（默认 DATE_ROOT=E: 盘）：测试要在临时目录合成的
    episode 上验同一套口径，且 CI（ubuntu）根本没有 E: 盘——注入根让同一份
    逻辑在任何宿主可验，不必依赖真素材在盘（#141）。"""
    base = os.path.join(date_root or DATE_ROOT, row["date"], "video", "每日新中国b站",
                        "流水线", row["slug"], "_frames")
    out = sorted(glob.glob(os.path.join(base, f"{row['shot']}.jpg"))
                 + glob.glob(os.path.join(base, f"{row['shot']}_*.jpg")))
    return out


def load_labeled(dataset=None):
    pos, neg = [], []
    for r in rd.load(dataset or rd.DATASET):
        if r.get("user_flagged") is True:
            pos.append(r)
        elif r.get("user_flagged") is False:
            neg.append(r)
    return pos, neg


def evidence_by_row(rows=None, references_dir=None, frames_root=None):
    """数据集行 → 证据桶条目（T18 #130 + #182）：按 (date, slug, shot) 链接全部
    证据桶条目——离线件（references/t15-evidence-*）+ 生产件
    （<slug>/_frames/evidence/manifest.json）。返回 {(date,slug,shot): [条目,...]}；
    条目即 rotation_dataset.load_evidence_buckets 的形状（category/sid/round/frame/…）。
    帧缺盘的条目也保留（manifest 为账），调用方按 linked 自取。
    frames_root 透传生产桶扫描根（#182）：测试注入 tempdir，生产缺省走
    DRIVER_DATE_ROOT / E: 盘。"""
    out = {}
    for e in rd.load_evidence_buckets(references_dir, frames_root):
        out.setdefault((e["episode"][:10], "daily-china-" + e["episode"][-8:], e["sid"]),
                       []).append(e)
    return out


def row_material(r, references_dir=None, date_root=None, frames_root=None):
    """行 → 训练候选素材（T18 #130 结构化口径）：

    [{path, kind, category, round, label_hint}]——kind=work(工作帧) /
    evidence-<category>（1-confirmed / 2-judge-found / 3-unfixed /
    4-human-flag）/ rowlink(行内 evidence_frames)。桶帧按 (date,slug,shot) 链接本行，
    category+round 逐条保留；缺盘帧不进候选。
    frames_root 透传生产桶扫描根（#182）：生产件与离线件同口消费。"""
    mats = [{"path": fp, "kind": "work", "category": "", "round": "",
             "label_hint": ""} for fp in frames_of(r, date_root)]
    linked = evidence_by_row([r], references_dir, frames_root).get(
        (r["date"], r["slug"], r["shot"]), [])
    for e in linked:
        if e["linked"]:
            mats.append({"path": e["frame"], "kind": "evidence",
                         "category": e["category"], "round": e["round"],
                         "label_hint": e["category_note"]})
    # 行内 evidence_frames（T11 s08 现场挂链；ADR 0008 起人审指认也写这里，
    # 条目为 {frame, sha256, round, category} 四字段，存量行仍是纯路径字符串
    # ——统一经 rd.evidence_frame_path 归一，别对 dict 直接 os.path.exists）——
    # 与桶帧去重后并入，kind=rowlink
    seen = {m["path"] for m in mats}
    for e in (r.get("evidence_frames") or []):
        fp = rd.evidence_frame_path(e)
        if fp and fp not in seen and os.path.exists(fp):
            cat = (e.get("category") or "") if isinstance(e, dict) else ""
            rnd = (e.get("round") or "") if isinstance(e, dict) else ""
            mats.append({"path": fp, "kind": "rowlink", "category": cat, "round": rnd,
                         "label_hint": CATEGORY_NOTES.get(cat) or "s08 现场定罪帧"})
            seen.add(fp)
    return mats


def row_frames(r):
    """行 → 全部在盘帧：中帧 + 首/尾变体（frames_of）+ 判翻证据帧
    （evidence_frames，T11——打回重烧前的定罪帧，同帧同行标签进训练清单）。
    行内条目兼容两种形态（ADR 0008）：四字段 dict 或存量纯路径字符串。"""
    out = frames_of(r)
    for e in (r.get("evidence_frames") or []):
        fp = rd.evidence_frame_path(e)
        if fp and os.path.exists(fp):
            out.append(fp)
    return out


# ── 读取侧真值帧：单一解析点（#191 / ADR 0008 决策 3、7）───────────────
#
# 为什么必须收成一处：sha256 校验若散落在每个消费方，迟早出现「训练侧校验了、
# 评测侧没校验」的错配；ADR 0008 决策 7 因此把这个口定为 sha256 的唯一实施处，
# 其余物理列举消费方（row_material/row_frames/export/stats/jev_calibrate）保持
# 「列举盘上有哪些帧」的原语义，不做真值判定。
TRUTH_WARN_PREFIX = "⚠️ [真值帧] "


def _warn(msg):
    """真值帧告警 → stderr；cp936 控制台下不可编码字符降级转义（测试约定 §9）。"""
    line = TRUTH_WARN_PREFIX + msg
    try:
        print(line, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stderr, "encoding", None) or "utf-8"
        print(line.encode(enc, "backslashreplace").decode(enc, "replace"),
              file=sys.stderr, flush=True)


def _norm_path(fp):
    """帧路径比较口径（Windows 大小写不敏感；两侧都过 abspath+normcase）。"""
    return os.path.normcase(os.path.abspath(fp)) if fp else ""


def _bucket_sha(entries, fp):
    """同一帧路径在桶条目里记的 sha256（找不到返回空串）。

    ADR 0008 让账（manifest）成为 sha 的来源：行内 evidence_frames 四字段自带
    sha；存量纯路径字符串没有，就按路径回查该行的桶条目拿账上 sha——「无 sha256
    时现算比对」（#191 AC）。查不到只能降级为「在盘即用」（弱证据）。"""
    n = _norm_path(fp)
    for e in entries or []:
        if _norm_path(e.get("frame")) == n and e.get("sha256"):
            return e["sha256"]
    return ""


def truth_frame(row, references_dir=None, date_root=None, frames_root=None,
                buckets=None):
    """行 → 真值帧绝对路径；"" 表示该行没有可用帧（#191 唯一解析点）。

    优先级（ADR 0008 决策 3，仅 user_flagged=True 行）：
      1. 行内 evidence_frames（人审指认写入的冻结副本，四字段或存量纯路径字符串）
      2. 该行的证据桶条目（references/t15-evidence-* 离线件 + <slug>/_frames/
         evidence/manifest.json 生产件；按 (date,slug,shot) 链接）
      3. 工作帧（frames_of 首帧，重烧后的当班帧）

    sha256 是**硬条件**：候选帧在账上（行内条目或桶条目）记了 sha256 而现算不符，
    该帧弃用并告警，且**不回落工作帧**——重烧后的工作帧正是被推翻的那一版，回落
    等于把定罪前的画面当定罪帧喂进评测池。证据帧全部不可用（sha 不符/缺盘）时
    整行返回 ""，调用方据此把该行排除出池。
    user_flagged 为假（false/null）的行直接取工作帧首帧（与 #191 之前的选材口径
    一致，不做 sha 校验：没有账可对）。frame_stale 行无论旗标如何一律返回 ""——那是显式
    标记，只影响评测池，stats() 的判定数口径不动（ADR 0008 决策 5）。

    buckets: 批量调用方可预取 evidence_by_row(rows) 透传，避免逐行重扫 manifest；
    None 时本行现扫（单行调用方的默认姿势）。references_dir / date_root /
    frames_root 覆盖证据桶根、工作帧根与生产桶扫描根（#141 可移植口径）。"""
    if row.get("frame_stale"):
        return ""
    work = [p for p in frames_of(row, date_root) if os.path.exists(p)]
    if not row.get("user_flagged"):
        return work[0] if work else ""

    key = (row.get("date"), row.get("slug"), row.get("shot"))
    if buckets is None:
        entries = evidence_by_row([row], references_dir, frames_root).get(key, [])
    else:
        entries = buckets.get(key, [])
    entries = entries or []

    cands = []  # [(path, 账上 sha256, 来源说明)]，按优先级顺序
    seen = set()
    for e in (row.get("evidence_frames") or []):
        fp = rd.evidence_frame_path(e)
        if not fp or _norm_path(fp) in seen:
            continue
        seen.add(_norm_path(fp))
        exp = (e.get("sha256") or "") if isinstance(e, dict) else ""
        cands.append((fp, exp or _bucket_sha(entries, fp), "行内证据帧"))
    for e in entries:
        fp = e.get("frame") or ""
        if not fp or _norm_path(fp) in seen:
            continue
        seen.add(_norm_path(fp))
        cands.append((fp, e.get("sha256") or "",
                      "证据桶 " + (e.get("category") or "")))

    for fp, exp, src in cands:
        if not os.path.exists(fp):
            _warn(f"{key[0]} {key[2]} {src}缺盘，弃用：{fp}")
            continue
        if exp:
            actual = rd.sha256_file(fp)
            if actual != exp:
                _warn(f"{key[0]} {key[2]} {src} sha256 与账不符"
                      f"（账 {exp[:12]}… / 实 {actual[:12] or '缺盘'}…），弃用：{fp}")
                continue
        return fp
    if cands:
        # 证据存在过但全部不可用 → 整行无可用帧，**不回落工作帧**（ADR 0008 决策 3）
        _warn(f"{key[0]} {key[2]} 证据帧全部不可用（{len(cands)} 个候选）"
              " → 该行不进池，不回落工作帧")
        return ""
    return work[0] if work else ""


def rd_load_flip(r):
    """行的判官判定是否属翻转大类（T18 #130：与 dsh_judge.is_flip 同口径，
    不 import dsh_judge（subprocess/路径常量无谓进链），集合成员直判）。"""
    return (r.get("judge_verdict") or "").strip() in {"翻转90", "翻转180", "翻转"}


def stats():
    """一条命令出换岗门槛判定（T18 #130）：判定数（人工真值 pos+neg 行）+ 场景数
    （distinct date+slug）+ 判官证据行覆盖。"""
    pos, neg = load_labeled()
    rows = rd.load()
    unknown = sum(1 for r in rows if r.get("user_flagged") is None)
    judged = len(pos) + len(neg)
    print(f"判定数（人工真值 翻转正类 {len(pos)} + 确认负类 {len(neg)}）：{judged}"
          f"/{GOAL}（目标，正负都算）；未标注 {unknown} 行")
    # 场景多样性（T9 #118 / T10 #119 换岗门槛第二轴：看场景数不看行数——
    # 场景 = distinct (date, slug) 组合，即真值分布过的期数）
    scenes = sorted({(r["date"], r["slug"]) for r in pos + neg})
    print(f"场景数：{len(scenes)}/{SCENE_GOAL}（T10 换岗门槛：判定数 ≥{GOAL} 且 "
          f"场景数 ≥{SCENE_GOAL}）")
    for d, s in scenes:
        print(f"  {d} {s}")
    src = {}
    for r in pos + neg:
        src[r.get("user_flagged_source") or "（未记来源）"] = \
            src.get(r.get("user_flagged_source") or "（未记来源）", 0) + 1
    for k, v in sorted(src.items()):
        print(f"  {k}: {v}")
    miss = [(r["date"], r["shot"]) for r in pos + neg if not frames_of(r)]
    if miss:
        print(f"  ⚠️ {len(miss)} 行帧不在盘：{miss[:5]}")
    # T18 #130：判官证据行覆盖（judge_* 字段与证据桶素材，换岗评估参考面）
    j_rows = [r for r in rows if r.get("judge_verdict")]
    print(f"判官复判行：{len(j_rows)}（judge_verdict 非空）；判翻 "
          f"{sum(1 for r in j_rows if rd_load_flip(r))} 行")
    ev = rd.load_evidence_buckets()
    print(f"证据桶条目：{len(ev)}（在盘 {sum(1 for e in ev if e['linked'])}）——" +
          " / ".join(f"{c}:{sum(1 for e in ev if e['category'] == c)}"
                     for c in EVIDENCE_CATEGORIES))


def _export_rows(pos, neg, synthetic=False, references_dir=None, date_root=None,
                 frames_root=None):
    """label 决策 + 素材选择（T18 #130 单一事实口，export 与 dry-run 共用）。

    label 只由 user_flagged（人工真值）决定；vision_verdict / judge_verdict
    按行独立附字段——人机结论分字段、互不覆盖。素材 = row_material（工作帧
    + 证据桶帧 + 行内定罪帧），每帧一行（同帧同行标签，证据帧带桶轮次）。
    synthetic 样本仅在非 dry-run 路径生成（需要 PIL）。
    date_root 透传给 row_material（默认 E: 盘帧根）：测试在临时目录合成的
    episode 上验同一条链，不因宿主没有 E: 盘就少素材（#141）。"""
    for r in pos + neg:  # 确认负类也进清单（探针需要负类）
        for m in row_material(r, references_dir, date_root, frames_root):
            yield {"label": "翻转" if r.get("user_flagged") else "正常",
                   "frames": [os.path.abspath(m["path"])], "synthetic": False,
                   "date": r["date"], "shot": r["shot"],
                   "source": r.get("user_flagged_source"),
                   # 人工最终判定（#182 第三件套）：布尔只说"人说过话"，
                   # 这条说"人说了什么"（正常/翻转/翻转90/翻转180/倾斜）。
                   "ground_truth_verdict": r.get("ground_truth_verdict"),
                   "vision_verdict": r.get("vision_verdict"),
                   "judge_verdict": r.get("judge_verdict"),
                   "judge_source": r.get("judge_source"),
                   "material_kind": m["kind"],
                   "evidence_category": m["category"], "evidence_round": m["round"],
                   "evidence_note": m["label_hint"]}


def export(out, synthetic=False, dataset=None, dry_run=False, references_dir=None,
           date_root=None, frames_root=None):
    """导出 CLIP 训练清单（或 dry-run 列出被选素材）。

    dry-run：只打印每条候选（row/category/round/帧路径/label）+ 汇总，
    不写文件、不 import PIL——裸机器可跑（T18 #130 验收⑤）。
    references_dir / date_root 覆盖证据桶根与工作帧根（缺省走仓内 references/
    与 DATE_ROOT）；测试与异地复算都靠这两个口，不在函数里读死常量（#141）。
    frames_root 透传生产桶扫描根（#182）：生产件与离线件同口导出。
    """
    pos, neg = load_labeled(dataset)
    n = 0
    cats = {}
    if dry_run:
        print(f"== dry-run 素材清单（label=人工真值；{len(pos)} 正 + {len(neg)} 负 行）==")
        for row in _export_rows(pos, neg, False, references_dir, date_root,
                                frames_root):
            n += 1
            cat = row["evidence_category"] or "-"
            cats[cat] = cats.get(cat, 0) + 1
            print(f"  {row['date']} {row['shot']} [{row['material_kind']}"
                  f"{(('/' + row['evidence_round']) if row['evidence_round'] else '')}]"
                  f" cat={cat} label={row['label']} vision={row['vision_verdict']}"
                  f" judge={row['judge_verdict']}\n    {row['frames'][0]}")
        print(f"共 {n} 条候选素材；按类：" +
              " / ".join(f"{k}:{v}" for k, v in sorted(cats.items())))
        return
    import io
    from PIL import Image
    with open(out, "w", encoding="utf-8") as f:
        for row in _export_rows(pos, neg, synthetic, references_dir, date_root,
                                frames_root):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
        if synthetic:
            for r in neg:  # 确认正常帧 → 旋转 90/180/270 = 合成翻转正类
                for fp in frames_of(r, date_root):
                    im = Image.open(fp)
                    for deg in (90, 180, 270):
                        f.write(json.dumps({
                            "label": "翻转", "synthetic": True, "rotate_deg": deg,
                            "source_frame": os.path.abspath(fp),
                            "date": r["date"], "shot": r["shot"],
                            "source": "synthetic-rotate"},
                            ensure_ascii=False) + "\n")
                        n += 1
    print(f"清单 → {out}（{n} 行；synthetic={synthetic}）")
    print("T7 训练脚本按行取帧/按 rotate_deg 现场旋转即可，勿预生成落盘（省空间）")


def main():
    ap = argparse.ArgumentParser(description="旋转真值库")
    ap.add_argument("cmd", choices=["stats", "export"])
    ap.add_argument("--out", default=os.path.join(HERE, "..", "_test", "clip-train-manifest.jsonl"))
    ap.add_argument("--synthetic", action="store_true",
                    help="追加确认正常帧的 90/180/270 旋转合成翻转样本")
    ap.add_argument("--frames-root", default=None,
                    help="生产证据桶扫描根（#182；缺省读 DRIVER_DATE_ROOT，"
                         "未设则用与 gt 同源的帧根）")
    ap.add_argument("--dry-run", action="store_true",
                    help="export 只列被选素材不写文件（不 import PIL/torch，T18 #130）")
    ap.add_argument("--dataset", default=None, help="数据集路径（缺省仓内单一事实源）")
    a = ap.parse_args()
    if a.cmd == "stats":
        stats()
    else:
        export(a.out, a.synthetic, a.dataset, dry_run=a.dry_run,
               frames_root=a.frames_root)


if __name__ == "__main__":
    main()
