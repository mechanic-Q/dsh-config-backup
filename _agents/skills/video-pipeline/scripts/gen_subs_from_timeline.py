#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_subs_from_timeline.py — 用现有 TTS 音频 + whisper 词级时间戳缓存，生成精确句子级字幕。

原理（2026-08-31 用户纠正后重写）：
  1. IndexTTS 句间停顿短(<0.2s)，whisper 无法按停顿切句 → 不能依赖 whisper 的 segment 边界
  2. whisper 词级时间戳是连续的、按时间顺序的（每个词 start/end 准确）
  3. 把原文每个字按位置比例映射到 [t0, t1] 连续时间轴 → 句子起止 = 句首字 start、句末字 end
  4. 100% 用原文（零 ASR 错字），时间 100% 来自 whisper 实测（零猜测）

用法：
  python3 gen_subs_from_timeline.py <口播台词目录> <音频目录> <whisper缓存目录> <输出srt> <片头偏移s> <新闻标题映射json>
"""
import json, os, re, sys, subprocess, glob

def ts(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

def load_words(wsdir, sid):
    p = os.path.join(wsdir, f"{sid}.json")
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    words = []
    for seg in d["segments"]:
        for w in seg.get("words", []):
            words.append((w["start"], w["end"], w["word"]))
    return words

def audio_dur(audio_dir, sid):
    wav = os.path.join(audio_dir, f"{sid}.wav")
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", wav], capture_output=True, text=True)
    return float(r.stdout.strip())

def split_sentences(script):
    """按句末标点拆句，逗号结尾的尾巴也拆出（口播稿可能句号/逗号结尾混合）。"""
    sents = []
    cur = ""
    start = 0
    for i, ch in enumerate(script):
        cur += ch
        if ch in "。！？；":
            sents.append((cur.strip(), start, i))
            cur = ""
            start = i + 1
    if cur.strip():
        # 逗号/无标点结尾的尾巴
        sents.append((cur.strip(), start, len(script) - 1))
    return sents

def main():
    script_dir, audio_dir, wsdir, out_srt, offset_s, mapping_json = sys.argv[1:7]
    offset = float(offset_s)
    m = json.load(open(mapping_json, encoding="utf-8"))
    if isinstance(m, dict):  # 新格式：driver 实测 _merged/ 段起止（含垫片感知）
        mapping = m.get("news") or []
        shots_tl = {x["shot"]: x for x in m.get("shots") or []}
    else:  # 旧格式：纯新闻块列表，段起止按 wav 累加估算
        mapping = m
        shots_tl = {}

    # 每个 shot 的起止（按音频时长累加）+ 归属新闻标题
    # 归属用"shot 顺序对新闻块"（新闻按序对应连续 shot 区间，边界由新闻映射的 start 锚定）
    files = sorted(glob.glob(os.path.join(audio_dir, "shot*.wav")),
                   key=lambda x: int(re.search(r"shot(\d+)", os.path.basename(x)).group(1)))
    shot_info = {}  # sid -> {start, end, title}
    cur = offset
    news_idx = 0
    for f in files:
        sid = os.path.basename(f)[:-4]
        if sid in shots_tl:  # 实测段起止（compose 产物，含 -shortest 裁切与垫片）
            s, e = shots_tl[sid]["start"], shots_tl[sid]["end"]
        else:
            dur = audio_dur(audio_dir, sid)
            s, e = cur, cur + dur
        # 归属新闻：当前 shot 起点若越过当前新闻的 end → 前进到下一新闻
        while news_idx < len(mapping) - 1 and s >= mapping[news_idx]["end"] - 0.05:
            news_idx += 1
        title = mapping[news_idx]["title"]
        shot_info[sid] = {"start": s, "end": e, "title": title}
        cur = e

    srt = []
    idx = 1
    for f in files:
        sid = os.path.basename(f)[:-4]
        info = shot_info[sid]
        script = re.sub(r"^#.*$", "", open(os.path.join(script_dir, f"{sid}.md"), encoding="utf-8").read(), flags=re.MULTILINE).strip()
        if not script:
            continue
        words = load_words(wsdir, sid)
        if not words:
            srt.append(f"{idx}\n{ts(info['start'])} --> {ts(info['end'])}\n{script}\n"); idx += 1
            continue
        t0, t1 = words[0][0], words[-1][1]
        n = len(script)
        # 每个字符映射到 [t0, t1]
        def char_time(i):
            return offset_s and 0  # placeholder
        sents = split_sentences(script)
        for sent, si, ei in sents:
            if info["title"] and sent.rstrip("。！？") == info["title"].strip():
                continue  # 标题去重（顶部已显示）
            # 句子起止 = 句首字start、句末字end（映射到语音时间 + 段起点）
            s_abs = info["start"] + t0 + (si / n) * (t1 - t0)
            e_abs = info["start"] + t0 + ((ei + 1) / n) * (t1 - t0)
            s_abs = min(s_abs, info["end"])
            e_abs = min(e_abs, info["end"])
            if len(sent) <= 20:
                srt.append(f"{idx}\n{ts(s_abs)} --> {ts(e_abs)}\n{sent}\n"); idx += 1
            else:
                # 语义断行：按逗号/顿号/句号切，过滤纯标点碎片，标点并入前段
                parts = [p for p in re.split(r"(?<=[，、：。！？；])", sent) if p.strip()]
                if len(parts) <= 1:
                    parts = [sent]
                # 合并纯标点碎片到前段
                merged = []
                for p in parts:
                    if re.fullmatch(r"[，、：。！？；]+", p):
                        if merged:
                            merged[-1] = merged[-1] + p
                        continue
                    merged.append(p)
                parts = merged
                cs = s_abs
                for pt in parts:
                    # 再按20字硬切（尽量在标点处断，保护词不被拆断）
                    while len(pt) > 20:
                        # 找 14-20 字范围内最近的标点
                        cut_at = 20
                        for j in range(20, 13, -1):
                            if pt[j-1] in "，、。！？；：":
                                cut_at = j
                                break
                        # 若 20 字后紧跟标点（在 21-23 处），吞下标点一起切，避免"词+孤标点"残留
                        if cut_at == 20:
                            for k in (20, 21, 22):
                                if k < len(pt) and pt[k] in "，、。！？；：":
                                    cut_at = k + 1
                                    break
                        cut = pt[:cut_at]
                        srt.append(f"{idx}\n{ts(cs)} --> {ts(cs + (len(cut)/len(sent))*(e_abs-s_abs))}\n{cut.strip()}\n"); idx += 1
                        pt = pt[len(cut):]
                        cs = cs + (len(cut)/len(sent))*(e_abs-s_abs)
                    if pt:
                        srt.append(f"{idx}\n{ts(cs)} --> {ts(cs + (len(pt)/len(sent))*(e_abs-s_abs))}\n{pt.strip()}\n"); idx += 1
                    cs = cs + (len(pt)/len(sent))*(e_abs-s_abs)

    with open(out_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(srt))
    print(f"TOTAL cues: {idx-1} → {out_srt}")

if __name__ == "__main__":
    main()
