#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tts_sentence_timeline.py — 按句 TTS 生成 + 句子级字幕时间轴（方案A，2026-08-31 用户定）

根治"字幕对不上人声"：IndexTTS 无词级时间轴，但 batch concat 支持按句生成并保留每句 wav。
本脚本：
  1. 把每段口播拆成句子
  2. 用 IndexTTS batch concat 逐句生成（句间插 silence_after_ms 静音），输出整段 wav（下游不变）
  3. --keep-temp 保留每句 wav，ffprobe 实测每句时长
  4. 输出 字幕时间轴.json：{shot: [{text, start, end}, ...]}（start/end 全来自 TTS 实测，零 whisper）

用法：
  tts_sentence_timeline.py <口播台词目录> <voice_ref.wav> <输出目录> [--sentence-gap-ms 200] [--start N] [--end M]
  产出：
    <输出目录>/shotXX.wav          — 整段拼接音频（兼容现有 compose 下游）
    <输出目录>/字幕时间轴.json       — 句子级时间轴（给 gen_precise_subs / 烧字幕直接用）
"""
import json, os, sys, subprocess, argparse, re, shutil, glob

TTS_ENV = "/home/lmr/index-tts/.venv/bin/python"
TTS_MOD = "indextts.cli_v2"
MODEL_DIR = "/home/lmr/index-tts/checkpoints_2_5"
PASSION_REF = "/home/lmr/qmr_content_creator/private/voice/references/passion-emotion-reference.wav"


def split_sentences(text):
    """按句末标点拆句，返回非空句子列表。"""
    sents = [s for s in re.split(r'(?<=[。！？；])', text) if s.strip()]
    return sents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script_dir", help="01_分段/口播台词（shotXX.md）")
    ap.add_argument("voice_ref")
    ap.add_argument("output_dir")
    ap.add_argument("--sentence-gap-ms", type=int, default=200, help="句间静音毫秒（默认200，与IndexTTS句间停顿接近）")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=9999)
    ap.add_argument("--no-passion", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    workdir = os.path.abspath(args.output_dir)
    env = dict(os.environ)
    env["INDEXTTS2_MODEL_DIR"] = MODEL_DIR

    # 1. 扫描段
    shots = []
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
        wav_out = os.path.join(workdir, f"{sid}.wav")
        text = re.sub(r"^#.*$", "", open(os.path.join(args.script_dir, f), encoding="utf-8").read(), flags=re.MULTILINE).strip()
        if not text:
            continue
        shots.append((sid, wav_out, text))

    # 2. 逐段：拆句 → manifest → batch concat → keep-temp → 时间轴
    timeline = {}
    for sid, wav_out, text in shots:
        if os.path.exists(wav_out):
            print(f"[SKIP] {sid} 已存在（如需重跑先删 {os.path.basename(wav_out)}）")
            # 已有音频也尝试恢复时间轴
            continue
        sents = split_sentences(text)
        if len(sents) <= 1:
            # 单句段：直接整段生成（无需 concat）
            manifest = os.path.join(workdir, f".{sid}_manifest.jsonl")
            with open(manifest, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"text": text, "voice": os.path.abspath(args.voice_ref)}, ensure_ascii=False) + "\n")
            # 用 batch concat 统一（单行也走 concat，keep-temp 给一个 wav）
            gap_ms = 0
        else:
            gap_ms = args.sentence_gap_ms

        # manifest（多行，每句一行）
        manifest = os.path.join(workdir, f".{sid}_manifest.jsonl")
        lines = []
        for s in sents:
            task = {"text": s, "voice": os.path.abspath(args.voice_ref), "silence_after_ms": gap_ms}
            if not args.no_passion:
                task.update({"emotion_audio": PASSION_REF, "emotion_weight": 0.5,
                             "duration_factor": 0.88, "temperature": 0.7, "top_p": 0.7, "top_k": 20})
            lines.append(json.dumps(task, ensure_ascii=False))
        open(manifest, "w", encoding="utf-8").write("\n".join(lines))

        # 跑 batch concat + keep-temp
        cmd = [TTS_ENV, "-m", TTS_MOD, "batch", "--batch-file", manifest,
               "--voice", os.path.abspath(args.voice_ref), "--engine", "2.5",
               "--concat", "--output", wav_out, "--keep-temp"]
        r = subprocess.run(cmd, cwd="/home/lmr/index-tts", env=env, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            print(f"[FAIL] {sid}: {r.stderr[-300:]}")
            continue
        print(f"[OK] {sid}: {len(sents)} 句 → {os.path.basename(wav_out)}")

        # 3. 从 temp 目录读句子 wav → 实测时长 → 时间轴
        # batch concat 的 temp 目录名 = .<wav文件名>.<random>，在 wav_out 同级
        base_name = os.path.basename(wav_out)
        temp_dirs = [d for d in glob.glob(os.path.join(workdir, f".{base_name}.*"))
                     if os.path.isdir(d) and d != manifest]
        seg_files = []
        for d in temp_dirs:
            for sf in sorted(os.listdir(d)):
                if sf.endswith(".wav"):
                    seg_files.append((int(sf[:4]), os.path.join(d, sf)))
        seg_files.sort()
        if not seg_files:
            print(f"[WARN] {sid}: 未找到句子 wav（temp 目录被清？），时间轴缺失")
            continue
        # 实测每句时长，累加（含句间静音）
        entries = []
        cur = 0.0
        for i, (n, sf) in enumerate(seg_files):
            dur = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",sf],
                                       capture_output=True,text=True).stdout.strip())
            start = cur
            end = start + dur
            entries.append({"text": sents[i] if i < len(sents) else f"<sentence{i+1}>", "start": round(start, 3), "end": round(end, 3)})
            cur = end + gap_ms / 1000.0
        # total = 最后一句 end（不加尾部 gap；wav 实际含尾部 gap，但字幕用不到）
        last_end = entries[-1]["end"]
        timeline[sid] = {"total": round(last_end, 3), "sentences": entries}
        print(f"  时间轴: {len(entries)} 句, 末句止 {last_end:.2f}s")

    # 4. 写时间轴 json
    if timeline:
        tl_path = os.path.join(workdir, "字幕时间轴.json")
        with open(tl_path, "w", encoding="utf-8") as f:
            json.dump(timeline, f, ensure_ascii=False, indent=1)
        print(f"\n时间轴 → {tl_path}: {len(timeline)} 段")
    else:
        print("\n无新生成段（全部已存在），时间轴未更新。删 wav 重跑可重建。")


if __name__ == "__main__":
    main()
