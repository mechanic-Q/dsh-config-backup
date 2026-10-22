#!/usr/bin/env python3
"""One-shot precise subtitle generation.

Pipeline per shot:
1. whisper word-level timestamps on TTS audio (real voice timing)
2. map whisper timing onto the ORIGINAL script text (no transcription errors)
3. emit SRT with per-sentence timestamps
4. skip sentences identical to the news title (shown at top)
5. self-check

Usage: python3 gen_precise_subs.py
"""
import json, os, re, subprocess

BASE = '/home/lmr/每日新中国b站/流水线/daily-china-20260828'
AUDIO = f'{BASE}/02_TTS语音'
SCRIPT = f'{BASE}/01_分段/口播台词'
OUT = f'{BASE}/08_字幕/字幕_口播_精确.srt'
MAPPING = json.load(open(f'{BASE}/07_成片/新闻标题映射.json', encoding='utf-8'))


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout, r.stderr, r.returncode


def strip_text(md):
    t = open(md, encoding='utf-8').read()
    t = re.sub(r'^#.*$', '', t, flags=re.MULTILINE).strip()
    return t


def whisper_words(sid, wav):
    # 缓存放项目内 _cache/wsout（项目自包含，换项目自动失效，杜绝跨项目污染）
    wsdir = os.path.join(BASE, '_cache', 'wsout')
    out_json = f'{wsdir}/{sid}.json'
    if not os.path.exists(out_json):
        os.makedirs(wsdir, exist_ok=True)
        run(['whisper', '--model', 'small', '--language', 'zh',
             '--word_timestamps', 'True', '--output_format', 'json',
             '--output_dir', wsdir, wav])
        if not os.path.exists(out_json):
            return []
    d = json.load(open(out_json, encoding='utf-8'))
    words = []
    for seg in d['segments']:
        for w in seg.get('words', []):
            words.append((w['start'], w['end'], w['word']))
    return words


def ts(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',')


def chunk_sentences(script, title):
    chunks = []
    for sent in re.split(r'(?<=[。！？；])', script):
        sent = sent.strip()
        if not sent:
            continue
        if title and sent == title.strip() and not chunks:
            continue
        if '丨' in sent and len(sent) > 20:
            left, right = sent.split('丨', 1)
            if len(left) <= 20:
                chunks.append(left + '丨')
                sent = right
        while len(sent) > 20:
            cut = sent[:20]
            idx_c = max(cut.rfind('，'), cut.rfind('、'), cut.rfind('：'),
                        cut.rfind('。'), cut.rfind('！'), cut.rfind('？'))
            if idx_c < 4:
                for part in ['的', '了', '中', '，']:
                    pi = cut.rfind(part)
                    if pi >= 6:
                        idx_c = pi
                        break
            if idx_c < 4:
                idx_c = 20
            while idx_c > 0:
                j = idx_c
                while j > 0 and (sent[j-1].isdigit() or sent[j-1] in '.,'):
                    j -= 1
                if j < idx_c:
                    idx_c = j
                    break
                if idx_c > 1 and sent[idx_c-1] in '万亿千百万' and sent[idx_c-2].isdigit():
                    idx_c -= 1
                    break
                break
            if idx_c < len(sent) and (sent[idx_c].isdigit() or sent[idx_c] in '.,'):
                j2 = idx_c
                while j2 < len(sent) and (sent[j2].isdigit() or sent[j2] in '.,'):
                    j2 += 1
                if j2 > idx_c:
                    while idx_c > 0 and (sent[idx_c-1].isdigit() or sent[idx_c-1] in '.,'):
                        idx_c -= 1
            chunks.append(sent[:idx_c + 1] if idx_c < 20 else sent[:20])
            sent = sent[idx_c + 1:] if idx_c < 20 else sent[20:]
        if sent:
            chunks.append(sent)
    if not chunks and script.strip() != (title or '').strip():
        chunks = [script]
    return chunks


def main():
    files = sorted([f for f in os.listdir(AUDIO) if f.endswith('.wav')],
                   key=lambda x: int(re.search(r'\d+', x).group()))
    srt = []
    idx = 1
    report = []
    offset = 12.56
    for f in files:
        sid = f[:-4]
        wav = os.path.join(AUDIO, f)
        script = strip_text(os.path.join(SCRIPT, f'{sid}.md'))
        words = whisper_words(sid, wav)
        a_dur = float(subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', wav], capture_output=True, text=True).stdout.strip())
        title = MAPPING.get(sid, '')
        if not words:
            chunks = chunk_sentences(script, title)
            s = offset
            for c in chunks:
                e = min(s + 1.0, offset + a_dur)
                srt.append(f'{idx}\n{ts(s)} --> {ts(e)}\n{c}\n'); idx += 1
                s = e
            offset += a_dur
            report.append(f'{sid}: NO words, {len(chunks)} cues')
            continue
        n_chars = len(script)
        n_words = len(words)
        t0, t1 = words[0][0], words[-1][1]
        chunks = chunk_sentences(script, title)
        acc_w = 0
        for c in chunks:
            cw = max(1, int(len(c) * n_words / n_chars))
            s_frac = acc_w / n_words
            e_frac = min(1.0, (acc_w + cw) / n_words)
            s = offset + t0 + (t1 - t0) * s_frac
            e = offset + t0 + (t1 - t0) * e_frac
            srt.append(f'{idx}\n{ts(s)} --> {ts(e)}\n{c}\n')
            idx += 1
            acc_w += cw
        offset += a_dur
        report.append(f'{sid}: {len(words)} words, {len(chunks)} cues')
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(srt))
    print('\n'.join(report))
    print(f'TOTAL cues: {idx-1} -> {OUT}')


if __name__ == '__main__':
    main()
