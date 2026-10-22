
import sys, os, json, collections
sys.path.insert(0, r"C:\Users\LMR\.agents\skills\video-pipeline\scripts")
os.environ.setdefault("ROTATION_DATASET_READONLY", "1")
import rotation_dataset as rd
import rotation_groundtruth as gt

rows = rd.load()
print("rows", len(rows))
fl = [r for r in rows if r.get("user_flagged") is True]
nf = [r for r in rows if r.get("user_flagged") is False]
print("user_flagged True", len(fl), "False", len(nf), "None", len(rows)-len(fl)-len(nf))
print("with evidence_frames non-empty:", sum(1 for r in rows if r.get("evidence_frames")))
print("gt_flag rows with evidence_frames:", sum(1 for r in fl if r.get("evidence_frames")))
print("gt verdict dist:", collections.Counter(r.get("ground_truth_verdict") for r in rows))
print("flag source dist:", collections.Counter(r.get("user_flagged_source") for r in fl))
print()
print("--- flagged rows: first in-disk frame (what rotate4_pool would take) ---")
for r in sorted(fl, key=lambda x:(x["date"],x["shot"])):
    fps=[p for p in gt.frames_of(r) if os.path.exists(p)]
    first = fps[0] if fps else None
    base = os.path.basename(first) if first else None
    mt = None
    if first:
        import time
        mt = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(first)))
    print(f'{r["date"]} {r["shot"]:>6} vv={r.get("vision_verdict")!r:8} gt={r.get("ground_truth_verdict")!r:8} n_frames={len(fps)} first={base} mtime={mt} ev_frames={len(r.get("evidence_frames") or [])}')
