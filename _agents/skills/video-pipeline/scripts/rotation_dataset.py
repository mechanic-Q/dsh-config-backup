#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rotation_dataset.py — 旋转镜头跨期数据集：逐期累积镜头特征 + 旋转结果，供集中分析。

背景（2026-09-05 用户拍板）：横移门白名单制缓行——先多收集几期「运镜歪了」的镜头数据，
看共性，数据够了再集中分析定方案。数据集 = git 内 JSONL，每行一个镜头：

  {date, slug, shot, video_seconds, director_camera, director_camera_class,
   prompt_camera_sentence, prompt_lateral_leak, camera_sanitized, portrait_risk,
   vision_verdict(正常/翻转/未判), vision_backend(9router/llama-server),
   vision_model, user_flagged(null=未知),
   ground_truth_verdict(正常/翻转90/翻转180/倾斜；人工最终判定，#182)}
  （#103：vision_backend/vision_model 为判读归因字段；存量行缺字段按
   9router/glm-4.6v 解读，见 row_backend/row_model）
  （T18 #130：判官复判字段 judge_verdict/judge_conf/judge_why/judge_flip_rounds/
   judge_rounds/judge_source——与本地 vision_verdict 分字段互不覆盖；人审真值
   独立走 user_flagged/user_flagged_source）

CLI:
  rotation_dataset.py append <slug> <date> <records.json>   # driver s9 自动调用
  rotation_dataset.py stats [jsonl路径]                      # 集中分析（几期后跑）
  rotation_dataset.py verify-evidence    # 证据清单哈希对账（#182「清单是账」）
"""
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # 邻居模块单一口径（rotation_groundtruth 同款）

import hostpath            # 跨侧路径换算单一事实源（#143）：生产证据根要用
DATASET = os.path.join(HERE, "..", "references", "rotation-dataset.jsonl")

# ── 测试期只读保护（#148 AC5）─────────────────────────────────────────
# 数据集曾一个文件背三个身份：真值单一事实源 / 每期 append 的运行日志 /
# 测试夹具。夹具已抽成 scripts/fixtures/ 下的期次快照，测试不再读活文件；
# 写侧同样上锁：run_tests.py 给每个测试子进程注入 ROTATION_DATASET_READONLY=1，
# 此时任何「写活数据集」的调用直接抛错（显式传 tempfile 路径不受影响）。
# 生产脚本（run_pipeline / t15_replay / vision_bench_review）不注入该变量，
# 行为不变。
READONLY_ENV = "ROTATION_DATASET_READONLY"
LIVE_PATH = os.path.realpath(DATASET)


class DatasetReadOnlyError(RuntimeError):
    """测试期（ROTATION_DATASET_READONLY=1）试图写生产数据集。"""


def readonly_mode():
    """当前进程是否处于测试期只读模式。"""
    return os.environ.get(READONLY_ENV) == "1"


def _guard_write(dataset_path):
    """写活数据集前拦截：#148——测试不许改写生产数据集。

    显式传 dataset_path（tempfile 夹具）永远放行；只有解析后指向
    references/rotation-dataset.jsonl 的写入在只读模式下被拒。"""
    if not readonly_mode():
        return
    if os.path.realpath(dataset_path) == LIVE_PATH:
        raise DatasetReadOnlyError(
            "测试期禁止写生产数据集（%s=1）：%s\n"
            "测试请用 tempfile 造 dataset_path，或读 scripts/fixtures/ 期次快照。"
            % (READONLY_ENV, dataset_path))

# 宽扫横移措辞（黑名单已知漏网家族——只用于数据标注，不用于拦截）。
# "unfolds/composition ... left to right" 是画面铺展描述不是运镜——动词只收
# 明确的相机移动家族（pan/tracking/drift 等），0908 期 shot18 误报实证。
BROADER_LATERAL = re.compile(
    r"horizontal (?:tracking|camera movement|movement|motion)"
    r"|mov(?:e|ing|es)?\s+laterally"
    r"|(?:drift|slide|glide|glid|mov|moving|pan|track|tracking)[a-z]*\s+(?:slowly\s+)?(?:to the\s+)?(?:left|right)"
    r"|arc (?:movement|motion)",
    re.I)

_CAMERA_SENT_RE = re.compile(
    r"\bcamera\b[^.!?]{0,90}?\b(?:move[sd]?|moving|pan(?:s|ning)?|tilt(?:s|ed|ing)?|"
    r"push(?:es|ing)?|pull(?:s|ed|ing)?|track(?:s|ed|ing)?|drift(?:s|ing)?|glid(?:e|es|ing)|"
    r"slid(?:e|ing)|descend(?:s|ing)?|ascend(?:s|ing)?|ris(?:e|es|ing)|zoom|dolly|crane|sweep(?:s|ing)?|"
    r"orbit(?:s|ing)?|rotat(?:e|es|ing)|circl(?:e|es|ing)|revolv(?:e|es|ing))\b"
    r"|\bstatic (?:shot|camera|wide shot)\b|remains? (?:mostly )?still|holding a stable|zero amplitude",
    re.I)

DIRECTOR_CLASS = [
    ("横移", re.compile(r"横移|左移|右移|平移|横向|滑过|扫过")),
    ("纵向", re.compile(r"前推|推进|上升|下降|俯拍|仰拍|拉升|坠落|升降|摇臂")),
    ("静止", re.compile(r"静止|固定")),
]


def classify_director_camera(shot_field):
    """导演【镜头】字段 → 横移/纵向/静止/其它。"""
    t = shot_field or ""
    for name, pat in DIRECTOR_CLASS:
        if pat.search(t):
            return name
    return "其它" if t.strip() else "未知"


def camera_sentence(prompt):
    """prompt 中第一句相机运动描述（剔除竖屏约束句），无则空串。"""
    for s in re.split(r"(?<=[.!?])\s+|\n", prompt or ""):
        if "vertical 9:16" in s or "NOT rotated" in s:
            continue
        if _CAMERA_SENT_RE.search(s):
            return s.strip()[:200]
    return ""


def lateral_leak(prompt):
    """黑名单漏网家族宽扫（不含约束句）。"""
    body = "\n".join(s for s in re.split(r"(?<=[.!?])\s+|\n", prompt or "")
                     if "vertical 9:16" not in s and "NOT rotated" not in s)
    return bool(BROADER_LATERAL.search(body))


# 判读归因（#103）：新采集行带 vision_backend/vision_model；存量行缺字段按
# 9router/glm-4.6v 解读（T2 端点翻转前的唯一后端）。盲比与 CLIP 重训靠它分源。
BACKEND_LEGACY = "9router"
MODEL_LEGACY = "glm-4.6v"  # 9router 别名 vision 的实名


def classify_backend(api, model):
    """判读端点+模型名 → (backend, model)。9router/20128 → 云端，别名 vision
    还原实名 glm-4.6v；其余视为本地 llama-server，模型名（--alias）原样。"""
    if any(x in (api or "") for x in ("20128", "9router")):
        return BACKEND_LEGACY, (MODEL_LEGACY if model in ("", "vision") else model)
    return "llama-server", (model or "")


def row_backend(row):
    return row.get("vision_backend") or BACKEND_LEGACY


def row_model(row):
    return row.get("vision_model") or MODEL_LEGACY


def build_records(slug, date, prompts_dir, design_dir, vision, regen_rounds,
                  vision_backend=None, vision_model=None, judge=None):
    """从 s9 现场产物构建该期全量镜头记录。

    vision: {"flipped": [sid], "judge_failed": ["sid:err"]}；regen_rounds: {sid: 轮次}
    vision_backend/vision_model：归因字段（#103），不传 = 存量语义（9router/glm-4.6v）
    judge: {sid: {verdict/judge_verdict, conf, why, flip_rounds, rounds, source}}
    （T16 #128/T18 #130 判官复判结论）——键带不带 judge_ 前缀均可（幂等归一），
    只落 judge_* 字段，与本地 vision_verdict 分字段互不覆盖——本地判读与判官
    复判是两条独立证据链；不传 = 无判官轮。
    """
    records = []
    # T11 #121：判翻证据帧挂链——evidence_by_sid 单一解析口，缺盘路径已过滤
    # （台账留痕，清单只用在盘帧）
    ev_map = evidence_by_sid(vision) if isinstance(vision, dict) else {}
    for fp in sorted(glob.glob(os.path.join(prompts_dir, "shot*.json"))):
        p = json.load(open(fp, encoding="utf-8"))
        sid = p["id"]
        d_fp = os.path.join(design_dir, f"{sid}.json")
        director_camera = ""
        try:
            director_camera = json.load(open(d_fp, encoding="utf-8")).get("镜头", "")
        except Exception:
            pass
        if sid in vision.get("flipped", []):
            verdict = "翻转"
        elif any(str(x).startswith(sid) for x in vision.get("judge_failed", [])):
            verdict = "未判"
        else:
            verdict = "正常"
        rec = {
            "date": date, "slug": os.path.basename(slug.rstrip("/")), "shot": sid,
            "video_seconds": p.get("video_seconds"),
            "director_camera": (director_camera or "")[:60],
            "director_camera_class": classify_director_camera(director_camera),
            "prompt_camera_sentence": camera_sentence(p.get("prompt", "")),
            "prompt_lateral_leak": lateral_leak(p.get("prompt", "")),
            "camera_sanitized": bool(p.get("camera_sanitized")),
            "motion_stripped": bool(p.get("motion_stripped")),
            "portrait_risk": bool(p.get("portrait_risk")),
            "regen_rounds": int(regen_rounds.get(sid, 0)),
            "vision_verdict": verdict,  # 本地原判（T18：永不被 judge_* 覆盖）
            "vision_backend": vision_backend or BACKEND_LEGACY,
            "vision_model": vision_model or MODEL_LEGACY,
            "evidence_frames": [d["frame"] for d in ev_map.get(sid, [])],
            # 人机字段三条独立（#182 AC5）：人审三件套不由采集侧重置——
            # append_records 是整行 upsert，重跑同期会把已回填的 user_flagged
            # 冲成 None（实测语义），故此处落占位 None 后由 append_records
            # 用盘上旧行的人字段覆盖回来。
            "user_flagged": None,
            # T16 #128/T18 #130：判官字段独立落行（人机结论分字段）；
            # 键已带 judge_ 前缀原样（防 judge_judge_verdict 双前缀）
            **({(k if k.startswith("judge_") else "judge_" + k): v
                for k, v in (judge or {}).get(sid, {}).items()}),
        }
        records.append(rec)
    return records


def _key(r):
    return (r.get("date"), r.get("slug"), r.get("shot"))


def write_rows(rows, dataset_path=DATASET):
    """整表回写单一写口（#148 AC5）：所有落盘都过这里，守卫一处生效。

    t15_replay 回写 judge_* 字段原先自带 open(rd.DATASET,"w")，绕开了
    append_records/set_user_flag 的守卫——收口到本函数后测试期同样被拒。"""
    _guard_write(dataset_path)
    with open(dataset_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_records(records, dataset_path=DATASET):
    """幂等 upsert（2026-09-08 根治）：同 (date, slug, shot) 覆盖旧行。
    纯 append 曾让重跑同段产生重复行（0907 shot16 三条），透视统计被放大。"""
    _guard_write(dataset_path)          # #148 AC5：测试期禁写生产数据集
    os.makedirs(os.path.dirname(dataset_path), exist_ok=True)
    rows = []
    if os.path.exists(dataset_path):
        for line in open(dataset_path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    merged = {_key(r): r for r in rows}   # 历史重复行顺带收敛（保留最后一条）
    # 人机字段互不覆盖（#182 AC5）：采集行（build_records）永远把人三件套
    # 置 None/缺失，直接 upsert 会把已回填的人工真值冲掉（重跑同期即触发）。
    # 人工结论优先级高于机器采集：新行没给人字段就从盘上旧行把三条搬过来。
    _HUMAN_KEYS = ("user_flagged", "user_flagged_source", "ground_truth_verdict")
    for r in records:
        k = _key(r)
        old_row = merged.get(k)
        if old_row:
            for hk in _HUMAN_KEYS:
                if r.get(hk) is None and old_row.get(hk) is not None:
                    r[hk] = old_row[hk]
        merged[k] = r
    write_rows(list(merged.values()), dataset_path)


def load(dataset_path=DATASET):
    rows = []
    if os.path.exists(dataset_path):
        for line in open(dataset_path, encoding="utf-8"):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def evidence_by_sid(vision, exists=True):
    """s08.vision.evidence 行按 sid 归组（T11 #121 单一解析口——build_records
    数据集挂链与审核页证据桶共用，勿各写一份）：
    {sid: [{frame, round}, ...]}；exists=True 过滤缺盘路径（清单/页面只用
    在盘帧，台账保留全量留痕）。"""
    out = {}
    for e in (vision.get("evidence") or [] if isinstance(vision, dict) else []):
        fp = e.get("frame")
        if not fp:
            continue
        if exists and not os.path.exists(fp):
            continue
        out.setdefault(e.get("sid"), []).append(
            {"frame": fp, "round": e.get("round") or ""})
    return out


# ── 证据桶读取（T18 #130：素材进训练链；#182 接生产）─────────────────
# 单一解析口覆盖两种桶形态：
#   离线件（t15_replay）   references/t15-evidence-*/{1-confirmed,2-judge-found,
#                          3-unfixed}/ + manifest.json（save_evidence 条目）
#   生产件（#182）         <slug>/_frames/evidence/{<category>/} +
#                          _frames/evidence/manifest.json（与离线件同构的桶清单；
#                          分类归档遍在扁平存证之后写，帧进 category 目录、
#                          扁平行仍在台账留痕）
# 四类语义（前三类 2026-10-17 T18 #130 定案；第四类 2026-10-22 ADR 0008 定案）：
#   1-confirmed   本地翻 + 判官翻   = 正类
#   2-judge-found 本地正常 + 判官翻 = 抓漏报（强负类素材）
#   3-unfixed     三轮仍翻          = 最难正类
#   4-human-flag  人工指认翻转      = 人审正类（判官不在场/漏报时，人眼看见的翻
#                 转只落这里；别并进 1-confirmed 冒充判官确认——与判官四象限
#                 不同源，ADR 0008 决策 1 否决了复用 1-confirmed）
EVIDENCE_CATEGORIES = ("1-confirmed", "2-judge-found", "3-unfixed", "4-human-flag")
CATEGORY_NOTES = {
    "1-confirmed": "判官确认翻转（正类）",
    "2-judge-found": "判官推翻本地（本地判正常+判官判翻=抓漏报，强负类素材）",
    "3-unfixed": "三轮仍未修好（最难正类）",
    "4-human-flag": "人工指认翻转（人审正类；与判官四象限不同源）",
}
# 人审桶的 (category, round) 口径（ADR 0008 决策 1/3）：写口
# （run_pipeline._flag_commit）与读取侧共用这两个常量，别各写一份字面量。
# round 固定 human_flag = 同一段重复指认走既有 (category, sid, round) 去重键，
# 不产生重复条目。
HUMAN_FLAG_CATEGORY = "4-human-flag"
HUMAN_FLAG_ROUND = "human_flag"


EVIDENCE_GLOB = os.path.join(HERE, os.pardir, "references", "t15-evidence-*")
PROD_EVIDENCE_MANIFEST = "manifest.json"
# 生产帧根：与 rotation_groundtruth.DATE_ROOT 同源（不 import 它——训练读取侧
# 不该把 gt 拉进链），两侧读同一 env，缺省 Windows 口径经 hostpath 换算。
PROD_FRAMES_ROOT_ENV = "DRIVER_DATE_ROOT"
PROD_FRAMES_ROOT_WIN = r"E:\每日新中国"   # noqa: mechanical — 跨侧口径走 hostpath


def evidence_frame_path(e):
    """行内 evidence_frames 条目 → 帧路径（ADR 0008 单一解析口）。

    行内条目自 ADR 0008 起是 {frame, sha256, round, category} 四字段；存量行仍是
    s08 采集口径的纯路径字符串。读取侧（gt.row_material / row_frames 与本模块
    set_evidence_frames 去重）一律经这里归一，别各解一份。"""
    return (e.get("frame") or "") if isinstance(e, dict) else (e or "")


def sha256_file(fp):
    """帧内容哈希（与 t15_replay.sha256 同算法：流式 sha256 hexdigest）。

    #182「清单是账」的账本口径：证据行落 sha256 后，帧被覆盖/移动可由
    verify_evidence_hashes 逐条对出来。缺盘/读失败返回空串（不抛）。"""
    try:
        h = hashlib.sha256()
        with open(fp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _episode_str(ep):
    """manifest 的 episode 字段（[date, slug] 或字符串）→ "date-slug"（同旧口径）。"""
    if isinstance(ep, list):
        return "-".join(str(x) for x in ep)
    return str(ep or "")


def _bucket_entry(e, episode, source):
    """manifest 证据条目 → 训练候选条目（离线/生产两形态同构的唯一构造点）。"""
    cat = e.get("category") or ""
    fp = e.get("frame") or e.get("frame_win") or ""
    return {
        "episode": episode, "category": cat,
        "category_note": CATEGORY_NOTES.get(cat, ""),
        "sid": e.get("sid") or "", "round": e.get("round") or "",
        "frame": fp, "sha256": e.get("sha256") or "",
        "judge": e.get("judge"), "conf": e.get("conf"),
        "why": e.get("why") or "", "local_verdict": e.get("local_verdict"),
        "source": source,
        "linked": bool(fp) and os.path.exists(fp),
    }


def iter_production_evidence_manifests(frames_root=None):
    """扫生产侧 _frames/evidence/manifest.json 清单路径（#182，只读不写）。

    默认根 = env DRIVER_DATE_ROOT / E:\每日新中国（gt.frames_of 的同一帧根），
    glob 形态与 gt 的 <date>/video/每日新中国b站/流水线/<slug> 期次布局对齐。
    frames_root 供测试注入 tempdir——CI（ubuntu）没有 E: 盘，缺省根 glob
    自然为空，行为与不接生产时一致。"""
    root = frames_root
    if not root:
        root = hostpath.to_host_path(
            os.environ.get(PROD_FRAMES_ROOT_ENV) or PROD_FRAMES_ROOT_WIN)
    pat = os.path.join(root, "*", "video", "*", "流水线", "*",
                       "_frames", "evidence", PROD_EVIDENCE_MANIFEST)
    return sorted(glob.glob(pat))


def load_evidence_buckets(references_dir=None, frames_root=None):
    """读全部证据桶 → 训练候选条目列表（T18 #130 + #182 接生产）。

    两路合并：references/t15-evidence-*/manifest.json（离线件，source="offline"）
    + <slug>/_frames/evidence/manifest.json（生产件，source="production"）。
    每条：{episode, category, category_note, sid, round, frame, sha256,
    judge, conf, why, local_verdict, linked(bool), source}——manifest 为账：
    帧缺盘也保留条目（linked=False 标记），训练端自取在盘子集；
    坏 manifest 跳过不炸。references_dir / frames_root 供测试注入。"""
    root = references_dir or os.path.join(HERE, os.pardir, "references")
    out = []
    for mf in sorted(glob.glob(os.path.join(root, "t15-evidence-*", "manifest.json"))):
        try:
            m = json.load(open(mf, encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏账不炸读取
            continue
        ep = _episode_str(m.get("episode"))
        for e in m.get("evidence") or []:
            if (e.get("category") or "") not in EVIDENCE_CATEGORIES:
                continue
            out.append(_bucket_entry(e, ep, "offline"))
    for mf in iter_production_evidence_manifests(frames_root):
        try:
            m = json.load(open(mf, encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏账不炸读取
            continue
        ep = _episode_str(m.get("episode"))
        for e in m.get("evidence") or []:
            if (e.get("category") or "") not in EVIDENCE_CATEGORIES:
                continue
            out.append(_bucket_entry(e, ep, "production"))
    out.sort(key=lambda x: (x["episode"], x["category"], x["sid"], x["round"]))
    return out


def verify_evidence_hashes(entries):
    """「清单是账」的对账动作（#182）：逐条重算在盘帧哈希并与账比对。

    返回 [{entry, ok, actual, state}]；state：
      ok           帧哈希 == 账
      sha-mismatch 帧被覆盖/改动（账实不符，必须报出来）
      unlinked     帧不在盘（换机/清理，不算账错）
      missing      条目没有 sha256（旧形态行）
    纯只读：不改账不删帧。CLI：rotation_dataset.py verify-evidence。"""
    out = []
    for e in entries:
        fp = e.get("frame") or ""
        claimed = e.get("sha256") or ""
        if fp and not os.path.exists(fp):
            out.append({"entry": e, "ok": False, "actual": "", "state": "unlinked"})
            continue
        if not claimed:
            out.append({"entry": e, "ok": False, "actual": "", "state": "missing"})
            continue
        actual = sha256_file(fp)
        ok = bool(actual) and actual == claimed
        out.append({"entry": e, "ok": ok, "actual": actual,
                    "state": "ok" if ok else "sha-mismatch"})
    return out


# 人工最终判定词表（spec #132：与判官四级词表同源对齐；布尔只表达
# "人说过话"，verdict 表达"人说了什么"——换岗评估要的是后者）。
# 「翻转」是粗类：旧调用方只给布尔（True/False）时无从知道 90/180，宁可
# 记粗类也不猜细分——它必须在本词表内，否则写进数据集的值不在自己的
# 词表里，stats 透视会冒出词表外取值。
GROUND_TRUTH_VERDICTS = ("正常", "翻转", "翻转90", "翻转180", "倾斜")


def _norm_gt_verdict(v, flagged):
    """人工 verdict 归一：合法词表值原样；空值按布尔回落（True→翻转、
    False→正常——翻转细分未知时宁可记大类）；非法值返回 None（不改写）。"""
    s = (v or "").strip()
    if s in GROUND_TRUTH_VERDICTS:
        return s
    if not s:
        return "翻转" if flagged else "正常"
    return None


def set_user_flag(flags, dataset_path=DATASET, source=None, verdicts=None):
    """人审真值回填（T9 #118 + #182 三件套）：[(date, slug, shot, flagged_bool)]
    幂等合并进数据集——只改 user_flagged / user_flagged_source /
    ground_truth_verdict，其余字段原样保留（区别于 append_records 的整行
    upsert：回填发生在采集之后，不许碰采集字段，更不许被模型字段覆盖）。

    verdicts（#182）：{(date, slug, shot): "正常|翻转90|翻转180|倾斜"} 或直接
    一个字符串（对本批全部行生效）。缺省时按布尔回落（True→翻转 / False→正常），
    使旧调用方（t15_replay / _flag_commit）不改签名也能落第三件套。
    返回 (updated 行键列表, missing 键列表)。"""
    _guard_write(dataset_path)          # #148 AC5：测试期禁写生产数据集
    want = {(d, s, shot): bool(f) for d, s, shot, f in flags}
    # 人给的最终判定：整批字符串 或 逐键 dict（键同 want）；非法词表值忽略
    per = verdicts if isinstance(verdicts, dict) else None
    blanket = verdicts if isinstance(verdicts, str) else None
    rows = load(dataset_path)
    updated, missing = [], []
    for r in rows:
        k = (r.get("date"), r.get("slug"), r.get("shot"))
        if k in want:
            r["user_flagged"] = want[k]
            r["user_flagged_source"] = source or "daily-review"
            v = (per or {}).get(k, blanket)
            gv = _norm_gt_verdict(v, want[k])
            if gv:
                r["ground_truth_verdict"] = gv
            updated.append(k)
    missing = sorted(set(want) - set(updated))
    if updated:
        write_rows(rows, dataset_path)
    return updated, missing


def set_evidence_frames(entries, dataset_path=DATASET, stale=None):
    """证据帧行内回填（ADR 0008 决策 4 后置写口）：只补 evidence_frames
    （+ 可选 frame_stale 标记），**不改**该行其它字段，仍过 write_rows 单一写口。

    entries：[(date, slug, shot, 条目)]，条目为 {frame, sha256, round, category}
    （多余键丢弃——行内只留四字段，读取侧 rd.evidence_frame_path 归一）。
    stale：{(date, slug, shot): reason} → 该行标 frame_stale=True 并记原因
    （ADR 0008 决策 5：帧被后续重烧覆盖，行内那张不再是真值帧时留痕不删账）。
    幂等：同帧已在该行（按帧路径比对，兼容存量纯路径字符串）不重复追加。
    返回 (updated 行键列表, missing 键列表)——与 set_user_flag 同构。"""
    _guard_write(dataset_path)          # #148 AC5：测试期禁写生产数据集
    want = {}
    for d, s, shot, e in entries:
        fp = evidence_frame_path(e)
        if not fp:
            continue
        want[(d, s, shot)] = {
            "frame": fp,
            "sha256": (e.get("sha256") or "") if isinstance(e, dict) else "",
            "round": (e.get("round") or "") if isinstance(e, dict) else "",
            "category": (e.get("category") or "") if isinstance(e, dict) else "",
        }
    stale = dict(stale or {})
    rows = load(dataset_path)
    updated = []
    for r in rows:
        k = (r.get("date"), r.get("slug"), r.get("shot"))
        if k in want:
            have = [evidence_frame_path(x) for x in (r.get("evidence_frames") or [])]
            if want[k]["frame"] not in have:
                r.setdefault("evidence_frames", []).append(want[k])
            updated.append(k)
        if k in stale:
            r["frame_stale"] = True
            r["frame_stale_reason"] = str(stale[k])
            if k not in updated:
                updated.append(k)
    missing = sorted((set(want) | set(stale)) - set(updated))
    if updated:
        write_rows(rows, dataset_path)
    return updated, missing


def stats(dataset_path=DATASET):
    """按特征维度透视旋转率——数据攒够几期后跑这个做集中分析。"""
    rows = load(dataset_path)
    if not rows:
        print("数据集为空")
        return
    for r in rows:  # 存量行透视前回填归因缺省（只影响内存态，不回写文件）
        r.setdefault("vision_backend", row_backend(r))
        r.setdefault("vision_model", row_model(r))
    def pivot(key):
        g = {}
        for r in rows:
            g.setdefault(r.get(key), []).append(r)
        print(f"\n== 按 {key} ==")
        for k, rs in sorted(g.items(), key=lambda x: str(x[0])):
            rot = sum(1 for r in rs if r.get("vision_verdict") == "翻转"
                      or r.get("user_flagged") is True)
            print(f"  {str(k):<28} n={len(rs):<3} 旋转={rot:<3} ({rot * 100 // max(len(rs), 1)}%)")
    print(f"共 {len(rows)} 条（{len(set(r['date'] for r in rows))} 期）")
    for k in ("vision_backend", "director_camera_class", "prompt_lateral_leak",
              "camera_sanitized", "motion_stripped", "portrait_risk", "vision_verdict",
              "judge_verdict", "ground_truth_verdict"):
        pivot(k)
    n_gt = sum(1 for r in rows if r.get("ground_truth_verdict"))
    print(f"\n人工真值三件套：ground_truth_verdict 非空 {n_gt} / {len(rows)} 行"
          f"（user_flagged 非空 {sum(1 for r in rows if r.get('user_flagged') is not None)} 行）")
    # 交叉：横移设计 × prompt泄漏
    print("\n== 横移设计 × prompt横移泄漏 ==")
    for cls in ("横移", "纵向", "静止", "其它", "未知"):
        rs = [r for r in rows if r.get("director_camera_class") == cls]
        if not rs:
            continue
        leak = [r for r in rs if r.get("prompt_lateral_leak")]
        rot_l = sum(1 for r in leak if r.get("vision_verdict") == "翻转" or r.get("user_flagged") is True)
        rot_n = sum(1 for r in rs if not r.get("prompt_lateral_leak")
                    and (r.get("vision_verdict") == "翻转" or r.get("user_flagged") is True))
        print(f"  {cls}: 泄漏 {len(leak)} 段转 {rot_l} | 未泄漏 {len(rs) - len(leak)} 段转 {rot_n}")


def verify_evidence_cli(entries=None):
    """verify-evidence 子命令体：对全部证据桶条目做哈希对账并打印（#182）。

    账实不符（sha-mismatch）→ 退出码 1；缺盘/旧行无哈希只列计数不判失败
    （帧可清理，账要留痕；旧离线件之外从无缺哈希的生产行）。"""
    ev = entries if entries is not None else load_evidence_buckets()
    results = verify_evidence_hashes(ev)
    by = {}
    for x in results:
        by[x["state"]] = by.get(x["state"], 0) + 1
    if not results:
        print("证据清单对账（verify_evidence_hashes）：证据清单为空"
              "（离线件 + 生产件均无条目）")
        return 0
    print("证据清单对账（verify_evidence_hashes）：共 %d 条 → %s"
          % (len(results), " / ".join(f"{k}:{v}" for k, v in sorted(by.items()))))
    bad = [x for x in results if x["state"] == "sha-mismatch"]
    for x in bad[:20]:
        e = x["entry"]
        print("  ✗ %s %s/%s 账 %s… 实 %s…" % (
            e.get("episode"), e.get("category"), e.get("sid"),
            (e.get("sha256") or "")[:12], (x["actual"] or "")[:12]))
        print("     %s" % e.get("frame"))
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "stats":
        stats(sys.argv[2] if len(sys.argv) > 2 else DATASET)
    elif len(sys.argv) >= 2 and sys.argv[1] == "verify-evidence":
        sys.exit(verify_evidence_cli())
    else:
        print(__doc__)
        sys.exit(1)
