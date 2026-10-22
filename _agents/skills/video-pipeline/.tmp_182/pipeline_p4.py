# -*- coding: utf-8 -*-
"""#182 实施：run_pipeline.py 证据桶接生产（重写前一轮草稿）。"""
import io

P = "scripts/run_pipeline.py"
src = io.open(P, encoding="utf-8").read()

# ── A. 本地判翻存证块：改用模块级 _sha256_file + hostpath.to_win_path ──
old = '''            ev_dir = os.path.join(frames, "evidence", evidence_tag)
            dst = os.path.join(ev_dir, os.path.basename(fp))
            import hashlib; h = hashlib.sha256(); [h.update(b) for b in iter(lambda: open(fp,"rb"), b"")]
            dst_win = to_win_path(dst)
            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "frame_win": dst_win, "sha256": h.hexdigest(),
                                 "verdict": "翻转", "conf": conf_map.get(sid), "form": form})'''
new = '''            ev_dir = os.path.join(frames, "evidence", evidence_tag)
            dst = os.path.join(ev_dir, os.path.basename(fp))
            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "frame_win": hostpath.to_win_path(dst),
                                 "sha256": _sha256_file(dst),
                                 "verdict": "翻转", "conf": conf_map.get(sid), "form": form})'''
assert src.count(old) == 1, "A"
src = src.replace(old, new)

# ── B. judge_found 存证块 ──
old = '''            ev_dir = os.path.join(frames, "evidence", evidence_tag)
            dst = os.path.join(ev_dir, os.path.basename(fp))
            import hashlib; h = hashlib.sha256(); [h.update(b) for b in iter(lambda: open(fp,"rb"), b"")]
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
new = '''            ev_dir = os.path.join(frames, "evidence", evidence_tag)
            dst = os.path.join(ev_dir, os.path.basename(fp))
            try:
                os.makedirs(ev_dir, exist_ok=True)
                shutil.copy2(fp, dst)
                jr = why_by_sid.get(sid) or {}
                evidence.append({"sid": sid, "round": evidence_tag, "frame": dst,
                                 "frame_win": hostpath.to_win_path(dst),
                                 "sha256": _sha256_file(dst),
                                 "verdict": "翻转", "conf": conf_map.get(sid),
                                 "judge": jr.get("judge") or "翻转",
                                 "judge_why": jr.get("why") or "",
                                 "kind": "judge_found"})'''
assert src.count(old) == 1, "B"
src = src.replace(old, new)

# ── C. _check_frames 里的分类调用点：改用方法 ──
old = '''        # 证据分类归档（#182 AC3/AC4：生产写入三类桶目录 + manifest.json）
        if isinstance(judge_block, dict) and judge_block.get("ok"):
            try:
                _classify_evidence_bucket(self.slug, evidence, frames)
            except Exception as e:  # noqa: BLE001 — 存证失败不阻塞主链
                log(f"[evidence] 分类归档与 manifest 写失败（不阻塞）: {e}")
'''
new = '''        # 证据分类归档（#182 AC3/AC4：生产写三类桶目录 + manifest.json）。
        # 只在判官真的给出复判时分类——没有判官就没有「本地翻×判官翻」的类别
        # 依据，fail-open 轮照旧只留扁平证据帧，不伪造正类/强负类标签。
        if isinstance(judge_block, dict) and judge_block.get("ok"):
            try:
                self._archive_evidence_buckets(evidence, frames, judge_block)
            except Exception as e:  # noqa: BLE001 — 存证失败不阻塞主链
                log(f"[evidence] 分类归档/manifest 写失败（不阻塞）: {e}")
'''
assert src.count(old) == 1, "C"
src = src.replace(old, new)

# ── D. 删掉上一轮追加的坏 helper（从 "# ── 证据桶" 到文件末尾的 _classify_evidence_bucket 定义）
start = src.find("def _classify_evidence_bucket(slug, evidence_rows, frames_base):")
assert start != -1, "D: old helper not found"
# 该 helper 一直延伸到文件末尾（上一轮 append 在文件尾）
src = src[:start].rstrip() + "\n"

io.open(P, "w", encoding="utf-8").write(src)
print("patch A-D ok")
