
# -*- coding: utf-8 -*-
import sys, os, glob, time
sys.path.insert(0, r"C:\Users\LMR\.agents\skills\video-pipeline\scripts")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import clip_probe
p, reason = clip_probe.load_probe()
base = r"E:\每日新中国\2026-10-19\video\每日新中国b站\流水线\daily-china-20261019\_frames\evidence\human_flag"
fs = sorted(glob.glob(os.path.join(base, "*.jpg")))
print("human_flag jpgs:", len(fs))
for f in fs:
    v, c = p.predict(f)
    mt = time.strftime("%m-%d %H:%M:%S", time.localtime(os.path.getmtime(f)))
    print("%-34s %-4s %.3f  mtime=%s" % (os.path.basename(f), v, c or 0, mt))
