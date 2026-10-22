# -*- coding: utf-8 -*-
import io
P = "scripts/rotation_dataset.py"
src = io.open(P, encoding="utf-8").read()

old_start = "# ── 证据桶读取（T18 #130：素材进训练链）"
old_end = "def set_user_flag(flags, dataset_path=DATASET, source=None):"
i = src.index(old_start)
j = src.index(old_end)
assert i != -1 and j != -1 and i < j

new_block = '''# ── 证据桶读取（T18 #130：素材进训练链；#182 接生产）─────────────────
# 单一解析口覆盖两种桶形态：
#   离线件（t15_replay）   references/t15-evidence-*/{1-confirmed,2-judge-found,
#                          3-unfixed}/ + manifest.json（save_evidence 条目）
#   生产件（#182）         <slug>/_frames/evidence/{<category>/} +
#                          _frames/evidence/manifest.json（与离线件同构的桶清单；
#                          分类归档遍在扁平存证之后写，帧进 category 目录、
#                          扁平行仍在台账留痕）
# 三类语义（2026-10-17 T18 #130 定案）：
#   1-confirmed   本地翻 + 判官翻   = 正类
#   2-judge-found 本地正常 + 判官翻 = 抓漏报（强负类素材）
#   3-unfixed     三轮仍翻          = 最难正类
EVIDENCE_CATEGORIES = ("1-confirmed", "2-judge-found", "3-unfixed")
CATEGORY_NOTES = {
    "1-confirmed": "判官确认翻转（正类）",
    "2-judge-found": "判官推翻本地（本地判正常+判官判翻=抓漏报，强负类素材）",
    "3-unfixed": "三轮仍未修好（最难正类）",
}
EVIDENCE_GLOB = os.path.join(HERE, os.pardir, "references", "t15-evidence-*")
PROD_EVIDENCE_MANIFEST = "manifest.json"
# 生产帧根：与 rotation_groundtruth.DATE_ROOT 同源（不 import 它——训练读取侧
# 不该把 gt 拉进链），两侧读同一 env，缺省 Windows 口径经 hostpath 换算。
PROD_FRAMES_ROOT_ENV = "DRIVER_DATE_ROOT"
PROD_FRAMES_ROOT_WIN = r"E:\\每日新中国"   # noqa: mechanical — 跨侧口径走 hostpath


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

    默认根 = env DRIVER_DATE_ROOT / E:\\每日新中国（gt.frames_of 的同一帧根），
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


'''
src = src[:i] + new_block + src[j:]
io.open(P, "w", encoding="utf-8").write(src)
import py_compile; py_compile.compile(P, doraise=True)
print("patch2 ok")
