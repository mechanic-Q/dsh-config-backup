# -*- coding: utf-8 -*-
import io, re
P = "scripts/run_pipeline.py"
src = io.open(P, encoding="utf-8").read()

# ── A. _save_judge_found_evidence：补 frame_win/sha256/judge_why/kind──
old1 = '''            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                jr = why_by_sid.get(sid) or {}
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "verdict": "翻转", "conf": conf_map.get(sid),
                                 "judge": jr.get("judge") or "翻转",
                                 "judge_why": jr.get("why") or "",
                                 "kind": "judge_found"})'''
new1 = '''            import hashlib; h = hashlib.sha256(); [h.update(b) for b in iter(lambda: open(fp,"rb"), b"")]
            dst_win = to_win_path(dst)
            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                jr = why_by_sid.get(sid) or {}
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "frame_win": dst_win, "sha256": h.hexdigest(),
                                 "verdict": "翻转", "conf": conf_map.get(sid),
                                 "judge": jr.get("judge") or "翻转",
                                 "judge_why": jr.get("why") or "",
                                 "kind": "judge_found"})'''
assert src.count(old1) == 1, "_save_judge_found anchor"
src = src.replace(old1, new1)

# ── B. _check_frames 本地翻转证据：补 frame_win/sha256───────────────
old2 = '''            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "verdict": "翻转", "conf": conf_map.get(sid),
                                 "form": form})'''
new2 = '''            import hashlib; h = hashlib.sha256(); [h.update(b) for b in iter(lambda: open(fp,"rb"), b"")]
            dst_win = to_win_path(dst)
            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "frame_win": dst_win, "sha256": h.hexdigest(),
                                 "verdict": "翻转", "conf": conf_map.get(sid), "form": form})'''
assert src.count(old2) == 1, "_check_frames copy anchor"
src = src.replace(old2, new2)

io.open(P, "w", encoding="utf-8").write(src)
print("patch_run_pipeline_part1 ok")
