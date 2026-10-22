# -*- coding: utf-8 -*-
import os
root = r"E:\每日新中国"
print("root exists:", os.path.isdir(root))
if os.path.isdir(root):
    dirs = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    print("date dirs:", dirs)
    for d in dirs:
        p = os.path.join(root, d, "video")
        print(" ", d, "video=", os.path.isdir(p))
