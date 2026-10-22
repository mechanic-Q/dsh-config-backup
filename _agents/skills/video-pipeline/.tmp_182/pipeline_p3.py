# -*- coding: utf-8 -*-
import io, os, json, hashlib

P = "scripts/run_pipeline.py"
src = io.open(P, encoding="utf-8").read()

new_helper = `def _classify_evidence_bucket(slug, evidence_rows, frames_base):
    """将本期证据按 category/round 写入三类目录 + manifest.json（#182 AC3）。"""
    if not isinstance(evidence_rows, list) or not evidence_rows:
        return
    ev_dir = os.path.join(frames_base, "evidence")
    mf_path = os.path.join(ev_dir, "manifest.json")
    
    by_cat = {}
    for e in evidence_rows:
        cat = ""
        if e.get("kind") == "judge_found":
            cat = "2-judge-found"
        elif e.get("form") == "jev" or e.get("verdict") == "翻转":
            cat = "1-confirmed"
        else:
            continue
        by_cat.setdefault(cat, []).append(e)
    
    episode = slug.split("_daily-china-")[0] or slug.split("/")[-1]
    for cat, rows in by_cat.items():
        cat_dir = os.path.join(ev_dir, cat)
        os.makedirs(cat_dir, exist_ok=True)
        entries = []
        for r in rows:
            fp_in = r.get("frame") or ""
            sid = r.get("sid") or ""
            rnd = r.get("round") or ""
            fname = f"{rnd}_{sid}.jpg"
            dst_path = os.path.join(cat_dir, fname)
            if fp_in and os.path.exists(fp_in):
                shutil.copy2(fp_in, dst_path)
            sha = r.get("sha256") or hashlib.sha256(open(fp_in,"rb") if fp_in else b"").hexdigest()
            frame_win = to_win_path(dst_path) if fp_in else ""
            entries.append({
                "category": cat, "sid": sid, "round": rnd,
                "frame": dst_path, "frame_win": frame_win,
                "source_frame": fp_in, "sha256": sha,
                "judge": r.get("judge"), "conf": r.get("conf"),
                "why": r.get("judge_why") or r.get("why") or "",
                "local_verdict": r.get("verdict"),
                "kind": r.get("kind", ""), "synthetic": False, "note": ""
            })
        with open(mf_path, "w", encoding="utf-8") as f:
            json.dump({"episode": [episode, os.path.basename(slug)],
                       "human_flips": [], "rounds": [], "evidence": entries,
                       "judge_calls": [], "negative_control": []},
                      f, ensure_ascii=False, indent=2)

`
idx = src.rfind('if __name__ == "__main__":')
assert idx != -1, 'No if anchor'
dst = src[:idx].rstrip('\n') + '\n\n' + new_helper + '\n\n' + src[idx:]
io.open(P, 'w', encoding='utf-8').write(dst)
console.log('patch_run_pipeline_part3 ok');
