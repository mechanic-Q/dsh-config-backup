#!/usr/bin/env python3
"""extract_h3_frames.py — H3 生成后抽帧（竖屏翻转视觉检查的前置）

用法：
  extract_h3_frames.py <H3视频目录> <输出帧目录> [--frame-time 1]

对每段抽 1 帧（默认第 1 秒），输出 <帧目录>/<sid>.jpg。
之后由 vision 工具（vision_analyze / glance）逐帧判断：
  "画面是否有90度旋转？原本横屏的场景被旋转成竖版摆放（地平线/建筑/人物侧躺）？"

退出码：0=全部抽帧成功；1=有抽帧失败。
"""
import os, sys, subprocess, glob

def main():
    vdir, outdir = sys.argv[1], sys.argv[2]
    ft = 1
    if "--frame-time" in sys.argv:
        ft = float(sys.argv[sys.argv.index("--frame-time") + 1])
    os.makedirs(outdir, exist_ok=True)

    vids = sorted(glob.glob(os.path.join(vdir, "*_shot*.mp4"))) + sorted(glob.glob(os.path.join(vdir, "shot*.mp4")))
    if not vids:
        print(f"[FAIL] 无 H3 视频: {vdir}")
        sys.exit(2)

    failed = 0
    for v in vids:
        sid = os.path.basename(v).replace(".mp4", "")
        # 归一化 shotXX
        import re
        m = re.search(r"shot\d+", sid)
        short = m.group(0) if m else sid
        out = os.path.join(outdir, f"{short}.jpg")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(ft), "-i", v,
                            "-frames:v", "1", "-q:v", "3", out],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and os.path.exists(out):
            print(f"[OK] {short}: {out}")
        else:
            print(f"[FAIL] {short}: 抽帧失败 {r.stderr.strip()[:100]}")
            failed += 1
    print(f"\n=== {len(vids)} 段抽帧完成，失败 {failed} ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
