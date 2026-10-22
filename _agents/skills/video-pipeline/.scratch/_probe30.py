
# -*- coding: utf-8 -*-
import sys, os, json, time
sys.path.insert(0, r"C:\Users\LMR\.agents\skills\video-pipeline\scripts")
os.environ.setdefault("ROTATION_DATASET_READONLY", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import rotation_dataset as rd
import rotation_groundtruth as gt
import clip_probe

p, reason = clip_probe.load_probe()
print("probe:", p.infer if p else reason, "thr=", getattr(p, "thr", None))
rows = rd.load()
fl = [r for r in rows if r.get("user_flagged") is True]
res = []
for r in sorted(fl, key=lambda x: (x["date"], x["shot"])):
    fps = [q for q in gt.frames_of(r) if os.path.exists(q)]
    if not fps:
        res.append((r, None, None)); continue
    fp = fps[0]
    v, c = p.predict(fp)
    res.append((r, fp, (v, c)))
agree = dis = nodisk = 0
lines = []
for r, fp, vc in res:
    if fp is None:
        nodisk += 1
        lines.append("%s %s NODISK" % (r["date"], r["shot"])); continue
    v = vc[0]
    # 行标签语义：user_flagged=True => 人工确认翻转
    ok = (v == "翻转")
    if ok: agree += 1
    else: dis += 1
    lines.append("%s %s first=%s probe=%s conf=%.3f margin=%s => %s" % (
        r["date"], r["shot"], os.path.basename(fp), v, vc[1] or 0,
        p.last.get("margin") if p.last else None,
        "AGREE(翻转)" if ok else "DISAGREE(判正常)"))
print("\n".join(lines))
print("\nSUMMARY flagged=True n=%d  agree(probe判翻转)=%d  disagree(probe判正常)=%d  nodisk=%d" % (len(res), agree, dis, nodisk))
