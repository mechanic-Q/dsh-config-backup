#!/usr/bin/env python3
"""pixel_sentinel.py — 竖屏翻转像素哨兵（确定性预筛，零 LLM）。

背景：vision(LLM) 对本风格（人物极小+云海+光柱）的几何旋转判定不可靠
（rotation-analysis 实证漏报），且 22 段全量判读耗时 2-3 小时。
本哨兵用纯像素统计做秒级预筛：可疑段才进 vision 复核/人审。

信号（每段抽 3 帧，取中位数）：
  d_bright  上 40% 行亮度 − 下 40% 行亮度（归一 0-255）   天空通常更亮
  d_blue    上 40% 行 (B−R) − 下 40% 行 (B−R)            天空通常偏蓝
  d_side    左右半幅的 blue/bright 差的绝对值（天空被旋到侧面时增大）
判定：
  ok          上下信号方向正常（天空在上）
  suspect     天空信号反转为负（上下颠倒）或侧向分裂（90° 横躺）
  low_conf    信号弱（无法判定）→ 交给 vision 复核

用法：
  pixel_sentinel.py <1080P目录>                    → 全段 JSON（stdout）
  pixel_sentinel.py <视频文件> [--rotate cw|ccw|180]  → 单文件（校准用合成翻转）
"""
import argparse
import glob
import json
import os
import re
import sys

import cv2
import numpy as np

FRAMES_AT = (0.15, 0.5, 0.85)   # 抽帧位置（比例）
BAND = 0.4                       # 上下带高度占比
FRAMES_AT = (0.15, 0.5, 0.85)   # 抽帧位置（比例）
BAND = 0.4                       # 上下带高度占比
OUTLIER_Z = 2.0                  # 鲁棒 z 分数阈值（MAD 法）
LOWCONF_Z = 1.0


def frame_signal(img):
    """单帧 → 信号向量 (d_bright, d_blue, d_side, grad_ratio)。img: BGR ndarray。"""
    h = img.shape[0]
    small = cv2.resize(img, (108, int(108 * h / img.shape[1])), interpolation=cv2.INTER_AREA)
    H = small.shape[0]
    band = max(1, int(H * BAND / 2))
    top, bot = small[:band], small[-band:]
    left, right = small[:, :small.shape[1] // 2], small[:, small.shape[1] // 2:]

    def stats(region):
        b, g, r = region[:, :, 0].astype(np.float32), region[:, :, 1].astype(np.float32), region[:, :, 2].astype(np.float32)
        bright = (0.299 * r + 0.587 * g + 0.114 * b).mean()
        blue = (b - r).mean()
        return bright, blue

    tb, tblu = stats(top)
    bb, bblu = stats(bot)
    lb, lblu = stats(left)
    rb, rblu = stats(right)
    # 梯度方向比：竖直结构（建筑/光柱/人物/纵深线）产生水平梯度(∂x)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    ex, ey = float(np.abs(gx).mean()), float(np.abs(gy).mean())
    grad_ratio = ex / (ex + ey) if (ex + ey) > 0 else 0.5
    return (float(tb - bb), float(tblu - bblu),
            float(max(abs(lblu - rblu), abs(lb - rb))), grad_ratio)


def video_signal(path):
    """视频 → 三帧信号的中位数向量。"""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    sigs = []
    for pos in FRAMES_AT:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * pos))
        ok, frame = cap.read()
        if ok:
            sigs.append(frame_signal(frame))
    cap.release()
    if not sigs:
        return None
    arr = np.array(sigs)
    return tuple(float(np.median(arr[:, i])) for i in range(arr.shape[1]))


def outlier_verdicts(signals):
    """自监督离群检测（2026-09-08 定稿）：不做绝对阈值判定（无真实正样本
    时阈值即猜测），而是对当期全部段的信号向量做鲁棒 z 分数（median/MAD）——
    翻转/横躺段的信号必然偏离同期正常段分布，风格自校准。
    返回 {sid: (verdict, why)}。verdict ∈ ok / outlier / low_conf。"""
    if len(signals) < 6:
        return {sid: ("low_conf", "样本过少(<6)，需 vision 复核") for sid in signals}
    arr = np.array([signals[sid] for sid in sorted(signals)])
    sids = sorted(signals)
    med = np.median(arr, axis=0)
    mad = np.median(np.abs(arr - med), axis=0)
    out = {}
    for i, sid in enumerate(sids):
        z = np.where(mad > 1e-6, np.abs(arr[i] - med) / (1.4826 * mad), 0.0)
        zmax = float(z.max())
        zsig = ["d_bright", "d_blue", "d_side", "grad"][int(z.argmax())]
        if zmax >= OUTLIER_Z:
            out[sid] = ("outlier", f"信号离群 {zsig} z={zmax:.1f}——疑似翻转/横构图，vision 复核")
        elif zmax >= LOWCONF_Z:
            out[sid] = ("low_conf", f"信号偏离 {zsig} z={zmax:.1f}——建议 vision 复核")
        else:
            out[sid] = ("ok", f"与当期分布一致(zmax={zmax:.1f})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="视频文件或含 mp4 的目录")
    args = ap.parse_args()

    if os.path.isfile(args.target):
        sig = video_signal(args.target)
        if sig is None:
            sys.exit(f"无法读取: {args.target}")
        print(json.dumps({"file": os.path.basename(args.target), "signal": sig},
                         ensure_ascii=False))
        return

    files = sorted(glob.glob(os.path.join(args.target, "*.mp4")))
    signals, rows = {}, []
    for fp in files:
        sig = video_signal(fp)
        sid = re.sub(r"^.*?_(shot\d+)_.*$", r"\1", os.path.basename(fp))
        if sig is None:
            rows.append({"id": os.path.basename(fp), "verdict": "unreadable"})
            continue
        signals[sid] = sig
    verdicts = outlier_verdicts(signals)
    for sid, sig in signals.items():
        v, why = verdicts[sid]
        rows.append({"id": sid, "signal": sig, "verdict": v, "why": why})
    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
