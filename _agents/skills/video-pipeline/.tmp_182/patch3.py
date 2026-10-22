# -*- coding: utf-8 -*-
import io
P = "scripts/rotation_dataset.py"
src = io.open(P, encoding="utf-8").read()

# ── A. set_user_flag：扩 ground_truth_verdict（人说了什么，区别于人说过话）──
old = '''def set_user_flag(flags, dataset_path=DATASET, source=None):
    """人审真值回填（T9 #118）：[(date, slug, shot, flagged_bool)] 幂等合并进
    数据集——只改 user_flagged / user_flagged_source，其余字段原样保留
    （区别于 append_records 的整行 upsert：回填发生在采集之后，不许碰采集字段）。
    返回 (updated 行键列表, missing 键列表)。"""
    _guard_write(dataset_path)          # #148 AC5：测试期禁写生产数据集
    want = {(d, s, shot): bool(f) for d, s, shot, f in flags}
    rows = load(dataset_path)
    updated, missing = [], []
    for r in rows:
        k = (r.get("date"), r.get("slug"), r.get("shot"))
        if k in want:
            r["user_flagged"] = want[k]
            r["user_flagged_source"] = source or "daily-review"
            updated.append(k)
    missing = sorted(set(want) - set(updated))
    if updated:
        write_rows(rows, dataset_path)
    return updated, missing'''
new = '''# 人工最终判定词表（spec #132：与判官四级词表同源对齐；布尔只表达
# "人说过话"，verdict 表达"人说了什么"——换岗评估要的是后者）
GROUND_TRUTH_VERDICTS = ("正常", "翻转90", "翻转180", "倾斜")


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
    return updated, missing'''
assert src.count(old) == 1, "A anchor"
src = src.replace(old, new)

# ── B. build_records：整行 upsert 不得冲掉人字段（#182 AC5）──
old = '''            "evidence_frames": [d["frame"] for d in ev_map.get(sid, [])],
            "user_flagged": None,  # 九宫格/成片人审纠偏时手工回填 true/false'''
new = '''            "evidence_frames": [d["frame"] for d in ev_map.get(sid, [])],
            # 人机字段三条独立（#182 AC5）：人审三件套不由采集侧重置——
            # append_records 是整行 upsert，重跑同期会把已回填的 user_flagged
            # 冲成 None（实测语义），故此处落占位 None 后由 append_records
            # 用盘上旧行的人字段覆盖回来。
            "user_flagged": None,'''
assert src.count(old) == 1, "B anchor"
src = src.replace(old, new)

# append_records：upsert 前保人字段
old = '''    merged = {_key(r): r for r in rows}   # 历史重复行顺带收敛（保留最后一条）
    for r in records:
        merged[_key(r)] = r
    write_rows(list(merged.values()), dataset_path)'''
new = '''    merged = {_key(r): r for r in rows}   # 历史重复行顺带收敛（保留最后一条）
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
    write_rows(list(merged.values()), dataset_path)'''
assert src.count(old) == 1, "C anchor"
src = src.replace(old, new)

# ── D. stats：ground_truth_verdict 可读 ──
old = '''    for k in ("vision_backend", "director_camera_class", "prompt_lateral_leak",
              "camera_sanitized", "motion_stripped", "portrait_risk", "vision_verdict",
              "judge_verdict"):
        pivot(k)'''
new = '''    for k in ("vision_backend", "director_camera_class", "prompt_lateral_leak",
              "camera_sanitized", "motion_stripped", "portrait_risk", "vision_verdict",
              "judge_verdict", "ground_truth_verdict"):
        pivot(k)
    n_gt = sum(1 for r in rows if r.get("ground_truth_verdict"))
    print(f"\\n人工真值三件套：ground_truth_verdict 非空 {n_gt} / {len(rows)} 行"
          f"（user_flagged 非空 {sum(1 for r in rows if r.get('user_flagged') is not None)} 行）")'''
assert src.count(old) == 1, "D anchor"
src = src.replace(old, new)

# ── E. CLI：verify-evidence 校验动作 ──
old = '''if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "stats":
        stats(sys.argv[2] if len(sys.argv) > 2 else DATASET)
    else:
        print(__doc__)
        sys.exit(1)'''
new = '''def verify_evidence_cli(entries=None):
    """verify-evidence 子命令体：对全部证据桶条目做哈希对账并打印（#182）。

    账实不符（sha-mismatch）→ 退出码 1；缺盘/旧行无哈希只列计数不判失败
    （帧可清理，账要留痕；旧离线件之外从无缺哈希的生产行）。"""
    ev = entries if entries is not None else load_evidence_buckets()
    results = verify_evidence_hashes(ev)
    by = {}
    for x in results:
        by[x["state"]] = by.get(x["state"], 0) + 1
    print("证据清单对账（verify_evidence_hashes）：共 %d 条 → " % len(results) +
          " / ".join(f"{k}:{v}" for k, v in sorted(by.items())) if results else
          "证据清单为空（离线件 + 生产件均无条目）")
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
        sys.exit(1)'''
assert src.count(old) == 1, "E anchor"
src = src.replace(old, new)

# ── F. docstring 同步（模块头描述三件套与两形态）──
old = '''  {date, slug, shot, video_seconds, director_camera, director_camera_class,
   prompt_camera_sentence, prompt_lateral_leak, camera_sanitized, portrait_risk,
   vision_verdict(正常/翻转/未判), vision_backend(9router/llama-server),
   vision_model, user_flagged(null=未知)}'''
new = '''  {date, slug, shot, video_seconds, director_camera, director_camera_class,
   prompt_camera_sentence, prompt_lateral_leak, camera_sanitized, portrait_risk,
   vision_verdict(正常/翻转/未判), vision_backend(9router/llama-server),
   vision_model, user_flagged(null=未知),
   ground_truth_verdict(正常/翻转90/翻转180/倾斜；人工最终判定，#182)}'''
assert src.count(old) == 1, "F anchor"
src = src.replace(old, new)

old = '''CLI:
  rotation_dataset.py append <slug> <date> <records.json>   # driver s9 自动调用
  rotation_dataset.py stats [jsonl路径]                      # 集中分析（几期后跑）'''
new = '''CLI:
  rotation_dataset.py append <slug> <date> <records.json>   # driver s9 自动调用
  rotation_dataset.py stats [jsonl路径]                      # 集中分析（几期后跑）
  rotation_dataset.py verify-evidence    # 证据清单哈希对账（#182「清单是账」）'''
assert src.count(old) == 1, "G anchor"
src = src.replace(old, new)

io.open(P, "w", encoding="utf-8").write(src)
import py_compile; py_compile.compile(P, doraise=True)
print("patch3 ok")
