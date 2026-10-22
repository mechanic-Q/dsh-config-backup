#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gen_krea2_cover.py — 票9 #72 Q6/Q7/Q9：gen_krea2_cover 参数面自检（assert 直跑）。

覆盖：
1. 模块 import 无副作用（旧模块级 argv[1] 读取/makedirs/submit 已收进 main(argv)）
2. --ref-file：H/V 两版同用文件文本（driver 单 ref 现状语义，构图差异靠 preset/尺寸）
3. 无 --ref-file：回落硬编码 REF_H/REF_V（手工「一键生成」路径不破，Q7）
4. 不出网不碰 ComfyUI：submit 打桩
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gen_krea2_cover as G  # 模块级 argv 读取仍在则此处 IndexError = 红

# 1. import 无副作用 + Q7 默认样本保留
assert callable(G.main), "argv 读取与 submit 调用须收进 main(argv)"
assert G.REF_H and G.REF_V, "Q7：硬编码样本文本保留作默认"

calls = []
_real_submit = G.submit
G.submit = lambda ref, preset, w, h, tag: calls.append((ref, preset, w, h, tag))
try:
    root = tempfile.mkdtemp()
    # 2. --ref-file：文件文本进 H/V 两版，preset/尺寸不变
    ref_fp = os.path.join(root, "ref.txt")
    ref_text = "当日参考文本'带引号'\"双引号\"\n第二行"
    with open(ref_fp, "w", encoding="utf-8") as f:
        f.write(ref_text + "\n")  # 尾部换行应被 strip
    G.main(["gen_krea2_cover.py", root, "--ref-file", ref_fp])
    assert len(calls) == 2, calls
    (rh, ph, wh, hh, th), (rv, pv, wv, hv, tv) = calls
    assert (th, tv) == ("H", "V"), calls
    assert rh == rv == ref_text, "H/V 同文本（driver 单 ref 现状语义）"
    assert (ph, wh, hh) == ("SDXL_1344x768", 1536, 768), calls
    assert (pv, wv, hv) == ("SDXL_768x1344", 768, 1344), calls
    # 3. 无 --ref-file：回落硬编码默认（手工路径）
    calls.clear()
    G.main(["gen_krea2_cover.py", root])
    assert len(calls) == 2, calls
    assert calls[0][0] == G.REF_H and calls[1][0] == G.REF_V, "缺省回落 REF_H/REF_V"
    # makedirs 行为保留（09_封面 目录在 root 下建出）
    assert os.path.isdir(os.path.join(root, "09_封面"))
    # 空 ref 文件须响亮拒绝（防 driver 写盘失败后静默用错文本）
    empty = os.path.join(root, "empty.txt")
    open(empty, "w").close()
    try:
        G.main(["gen_krea2_cover.py", root, "--ref-file", empty])
        raise AssertionError("空 --ref-file 应 ValueError")
    except ValueError:
        pass
    # 4. root 位置参数守卫（#74 review 补钉）：漏 root 直打 flag 不得静默
    # 把 flag 当 root makedirs（scripts/--ref-file/ 游离目录实证）
    for bad_argv in (["gen_krea2_cover.py"], ["gen_krea2_cover.py", "--ref-file", ref_fp]):
        try:
            G.main(bad_argv)
            raise AssertionError(f"缺 root 应 SystemExit: {bad_argv}")
        except SystemExit:
            pass
finally:
    G.submit = _real_submit

print("test_gen_krea2_cover: 全部通过（票9 #72 参数面）")
