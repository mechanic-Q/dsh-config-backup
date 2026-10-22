#!/usr/bin/env python3
"""make_srt.py — 从 TTS 音频生成 SRT 字幕（480×864 标准：每行≤20字语义断行）。

用法: make_srt.py <audio_dir> <台词目录> <out.srt>
时间轴：逐段累计（每段音频时长 + 0.3s 余量），段内按句号断行。
"""
import os, sys, re, subprocess

def dur(p):
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",p],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)

def ts(t):
    h = int(t//3600); m = int(t%3600//60); s = t%60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

def wrap(text, max_chars=20):
    """按≤20字语义断行（逗号/顿号/空格处优先）。"""
    # 先按子句（，、；）拆，每子句内再按字数硬断
    parts = re.split(r"(?<=[，、；])", text)
    lines = []
    for p in parts:
        while len(p) > max_chars:
            # 找最靠近 max_chars 的切点（在中间附近断）
            cut = max_chars
            lines.append(p[:cut])
            p = p[cut:]
        if p:
            lines.append(p)
    return lines

def main():
    adir, sdir, out = sys.argv[1], sys.argv[2], sys.argv[3]
    files = sorted([f for f in os.listdir(adir) if f.endswith(".wav")],
                   key=lambda x: int(re.search(r"\d+", x).group()))
    srt = []
    t = 0.0
    idx = 1
    for f in files:
        sid = f.replace(".wav", "")
        text = open(os.path.join(sdir, f"{sid}.md"), encoding="utf-8").read()
        text = re.sub(r"^#.*$", "", text, flags=re.MULTILINE).strip()
        d = dur(os.path.join(adir, f)) + 0.3
        lines = wrap(text)
        # 每行平均分配段时长
        per = d / len(lines)
        for i, ln in enumerate(lines):
            s = t + i * per
            e = s + per
            srt.append(f"{idx}\n{ts(s)} --> {ts(e)}\n{ln}\n")
            idx += 1
        t += d
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(srt))
    print(f"SRT: {len(srt)} 条 → {out}（总时长 {t:.1f}s）")

if __name__ == "__main__":
    main()
