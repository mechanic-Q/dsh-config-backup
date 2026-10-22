# -*- coding: utf-8 -*-
import io
P = "scripts/run_pipeline.py"
src = io.open(P, encoding="utf-8").read()

anchor = '''    def _save_judge_found_evidence(self, judge_block, judged_map, conf_map,
                                   evidence_tag, frames, evidence):'''
assert src.count(anchor) == 1

new_methods = '''    def _archive_evidence_buckets(self, evidence, frames, judge_block):
        """#182：把本轮证据按三类桶归档，并写生产侧 manifest.json（清单是账）。

        桶目录 <slug>/_frames/evidence/<category>/<round>_<sid>.jpg，与离线件
        references/t15-evidence-*/ 同构；manifest 落 <slug>/_frames/evidence/
        manifest.json（rotation_dataset.load_evidence_buckets 第二路扫描口）。

        分类依据只认判官轮（judge_block.ok）：confirmed=本地翻×判官翻（1-confirmed）、
        judge_found=本地正常×判官翻（2-judge-found）；3-unfixed 只在收尾仍翻时由
        s08 写（本轮不写，避免把「还没修好」误记成「修不好」）。
        已有条目按 (category, sid, round) 去重，重跑同轮不重复追加。
        """
        ev_dir = os.path.join(frames, "evidence")
        mf_path = os.path.join(ev_dir, "manifest.json")
        kind_cat = {"confirmed": "1-confirmed", "judge_found": "2-judge-found"}
        rows = {r.get("sid"): r for r in (judge_block.get("rows") or [])
                if isinstance(r, dict)}
        try:
            manifest = json.load(open(mf_path, encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 无清单/坏清单都从头建
            manifest = {}
        manifest.setdefault("episode", [self.date,
                                        os.path.basename(os.path.normpath(self.slug))])
        entries = manifest.setdefault("evidence", [])
        have = {(e.get("category"), e.get("sid"), e.get("round")) for e in entries}
        added = 0
        for ev in evidence or []:
            sid, rnd = ev.get("sid"), ev.get("round")
            jr = rows.get(sid) or {}
            kind = jr.get("kind")
            cat = kind_cat.get(kind)
            if not cat or not sid:
                continue
            if (cat, sid, rnd) in have:
                continue
            src_fp = ev.get("frame")
            if not src_fp or not os.path.exists(src_fp):
                continue
            cat_dir = os.path.join(ev_dir, cat)
            try:
                os.makedirs(cat_dir, exist_ok=True)
                dst = os.path.join(cat_dir, "%s_%s%s" % (
                    rnd or "round0", sid, os.path.splitext(src_fp)[1] or ".jpg"))
                shutil.copy2(src_fp, dst)
                entries.append({
                    "category": cat, "sid": sid, "round": rnd,
                    "frame": dst, "frame_win": hostpath.to_win_path(dst),
                    "source_frame": src_fp, "sha256": _sha256_file(dst),
                    "judge": jr.get("judge"), "conf": jr.get("conf"),
                    "why": jr.get("why") or ev.get("judge_why") or "",
                    "local_verdict": jr.get("local") or ev.get("verdict"),
                    "kind": kind, "synthetic": False, "note": "",
                })
                have.add((cat, sid, rnd))
                added += 1
            except Exception as e:  # noqa: BLE001 — 单帧失败不拖垮整桶
                log(f"[evidence] {sid} 分类归档失败（不阻塞）: {e}")
        manifest["evidence"] = entries
        os.makedirs(ev_dir, exist_ok=True)
        with open(mf_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log(f"[evidence] 三类桶归档 +{added} 条 → {ev_dir}"
            f"（manifest 共 {len(entries)} 条）")
        return added

'''
src = src.replace(anchor, new_methods + anchor)
io.open(P, "w", encoding="utf-8").write(src)
print("added _archive_evidence_buckets")
