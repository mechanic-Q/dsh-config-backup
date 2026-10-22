#!/usr/bin/env python3
"""tts_batch.py — 阶段6：批量生成口播音频（Index-TTS-2，用户音色）。

V2: 支持从风格化文案目录（03_风格化文案/文案/*.md）读取文本，
    用用户音色（expressive-reference.wav）配音。

用法：
  tts_batch.py <文案目录> <voice_ref.wav> <输出目录> [--start N] [--end M]
"""
import json, os, sys, subprocess, argparse, re

TTS_ENV = "/home/lmr/index-tts/.venv/bin/python"
TTS_MOD = "indextts.cli_v2"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script_dir", help="风格化文案目录（03_风格化文案/文案/，含 shotXX.md）")
    ap.add_argument("voice_ref")
    ap.add_argument("output_dir")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=9999)
    ap.add_argument("--engine", choices=("2", "2.5"), default="2.5", help="IndexTTS engine (default 2.5)")
    ap.add_argument("--no-passion", action="store_true",
                    help="关闭 E2 模仿标准配方（默认开启：本人音色+目标情绪参考 emotion_weight 0.5 + 语速 0.88 + 收紧采样）")
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # 读文案目录（shotXX.md → 文本）
    batch_lines = []
    for f in sorted(os.listdir(args.script_dir)):
        if not f.endswith(".md"):
            continue
        m = re.match(r"shot(\d+)\.md", f)
        if not m:
            continue
        idx = int(m.group(1))
        if idx < args.start or idx > args.end:
            continue
        sid = f"shot{idx:02d}"
        out = os.path.join(os.path.abspath(args.output_dir), f"{sid}.wav")
        if os.path.exists(out):
            continue
        text = open(os.path.join(args.script_dir, f), encoding="utf-8").read()
        # 03_口播台词/ 是纯原文（# 标题 + 文本），去掉标题即可
        text = re.sub(r"^#.*$", "", text, flags=re.MULTILINE).strip()
        if not text:
            continue
        batch_lines.append(json.dumps({
            "text": text, "output": out,
            "voice": os.path.abspath(args.voice_ref),
            **({
                "emotion_audio": "/home/lmr/qmr_content_creator/private/voice/references/passion-emotion-reference.wav",
                "emotion_weight": 0.5, "duration_factor": 0.88,
                "temperature": 0.7, "top_p": 0.7, "top_k": 20,
            } if not args.no_passion else {}),
        }, ensure_ascii=False))

    if not batch_lines:
        print("无待生成段（全部已存在或超出范围）")
        return

    manifest = "/tmp/tts_batch_manifest.jsonl"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("\n".join(batch_lines))

    print(f"待生成 {len(batch_lines)} 段音频 → {args.output_dir}")
    print(f"声线: {args.voice_ref}")
    env = dict(os.environ)
    if args.engine == "2.5":
        # 显式指定 2.5 权重目录，否则 cli fallback 到 engine 2 权重（SKILL.md 实测坑）
        env["INDEXTTS2_MODEL_DIR"] = "/home/lmr/index-tts/checkpoints_2_5"
    cmd = [TTS_ENV, "-m", TTS_MOD, "batch", "--batch-file", manifest,
           "--voice", args.voice_ref, "--engine", args.engine]
    r = subprocess.run(cmd, cwd="/home/lmr/index-tts", env=env, capture_output=True, text=True,
                       timeout=3600)
    print(r.stdout[-800:])
    if r.stderr:
        print("STDERR:", r.stderr[-400:])
    if r.returncode != 0:
        print(f"TTS batch failed: {r.returncode}")
        sys.exit(1)
    print(f"完成: {len(batch_lines)} 段")


if __name__ == "__main__":
    main()
