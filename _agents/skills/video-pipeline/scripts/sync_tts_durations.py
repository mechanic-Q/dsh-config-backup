#!/usr/bin/env python3
"""sync_tts_durations.py — 阶段7→8 时长回填（系统性修复语速预估偏差）

问题：duration_est/video_seconds 按 4.5字/s 预估，但 E2 配方实测 ~6.4字/s
→ H3 视频比 TTS 音频长 3-5s/段 → compose -shortest 裁掉视频尾部动作节拍。

修复：TTS 完成后，用实测音频时长回填：
1. enhanced.json 各段 duration_est = 实测音频时长（+0.3s 余量，封顶15s）
2. 06_H3prompt/*.json 各段 video_seconds 同步
回填后需重跑阶段6（At 时间码随新时长重算）+ 阶段8 生成。

用法：
  sync_tts_durations.py <enhanced.json> <02_TTS语音目录> [--prompts 06_H3prompt目录] [--pad 0.3]
"""
import json, os, sys, re, argparse, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_contract as pc


def audio_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("enhanced_json")
    ap.add_argument("audio_dir")
    ap.add_argument("--prompts", default=None, help="06_H3prompt 目录（同步 video_seconds）")
    ap.add_argument("--pad", type=float, default=0.3, help="音频后余量秒（默认0.3）")
    ap.add_argument("--write", action="store_true", help="实际写回（默认 dry-run 只报告）")
    args = ap.parse_args()

    pc.check_input(args.enhanced_json, "阶段4分镜增强")
    enh = json.load(open(args.enhanced_json, encoding="utf-8"))
    changed = 0
    for s in enh:
        sid = s["id"]
        ap_ = None
        for cand in (f"{sid}.wav", f"{sid}.mp3", f"audio_{sid}.wav", f"audio_{sid}.mp3"):
            p = os.path.join(args.audio_dir, cand)
            if os.path.exists(p):
                ap_ = p
                break
        if ap_ is None:
            print(f"[WARN] {sid}: 无音频文件，保留原 duration_est")
            continue
        ad = audio_dur(ap_)
        if ad is None:
            print(f"[WARN] {sid}: ffprobe 失败")
            continue
        new_dur = round(min(ad + args.pad, 15.0), 1)
        old = s.get("duration_est")
        if old != new_dur:
            print(f"{sid}: duration_est {old} → {new_dur}（音频实测 {ad:.1f}s）")
            s["duration_est"] = new_dur
            changed += 1
            # 秒级指令时间字符串按比例缩放（"0-4.6s" → "0-3.5s"），与实测时长一致
            scale = new_dur / old if old else 1
            for d in s.get("per_second_directives", []):
                t = d.get("time", "")
                def _sc(m):
                    return f"{round(float(m.group(1)) * scale, 1)}-{round(float(m.group(2)) * scale, 1)}s"
                t2 = re.sub(r"([\d.]+)-([\d.]+)s", _sc, t)
                if t2 != t:
                    d["time"] = t2
            if args.prompts:
                pj = os.path.join(args.prompts, f"{sid}.json")
                if os.path.exists(pj):
                    rec = json.load(open(pj, encoding="utf-8"))
                    rec["video_seconds"] = new_dur
                    rec["duration_synced"] = True
                    if args.write:
                        json.dump(rec, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        else:
            print(f"{sid}: 一致（{old}s）")
    if args.write and changed:
        with open(args.enhanced_json, "w", encoding="utf-8") as f:
            json.dump(enh, f, ensure_ascii=False, indent=2)
        pc.stamp_output(args.enhanced_json, {"synced": changed, "source": "tts_measured"})
        print(f"\n✅ 已回填 {changed} 段（enhanced.json + prompts video_seconds）")
        print("下一步：重跑阶段6（At 时间码重算）→ 重跑阶段8 H3 生成 → compose_final")
    elif changed:
        print(f"\n[dry-run] 将变更 {changed} 段，确认后加 --write")
    else:
        print("\n无需变更")


if __name__ == "__main__":
    main()
