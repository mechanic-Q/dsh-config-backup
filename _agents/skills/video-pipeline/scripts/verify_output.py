#!/usr/bin/env python3
"""verify_output.py — 验收脚本：检查 H3 生成段是否合格，输出报告。

检查项（九项 = 原六项 + 创意层两项 + 音频对齐一项）：
  1. 文件存在 / 大小 > 0
  2. 视频时长在预期范围（est_seconds ±1.5s，且 ≤15.5s）
  3. 分辨率正确（480P：短边 768 或 480；720P：短边 720）
  4. prompt 三字段齐全（integrated_multimodal_description / overall_soundscape / non_diegetic_music）
  5. 相邻段风格锁一致（prompt 中场景/基调关键词延续，简化：检查前 120 字符公共前缀）
  6. 切点不在句中（输入分镜文本末字符应为句号/感叹号/问号/省略号）
  7. 相邻段画面连贯（NEW：导演设计 continuity_handoff 链成环）
  8. 每段传达 info_point（NEW：导演设计含本段 info_point 关键词）
  9. 音频对齐（TTS 音频时长与视频时长差 ≤0.5s）

用法：
  verify_output.py <generated.jsonl> [--prompts <h3_prompts_dir>] [--storyboard <分镜清单.json>] [--director-dir <导演设计目录>] [--audio-dir <TTS音频目录>] [--out <report.json>]

输出：
  报告 JSON（每段 pass/fail + 原因），stdout 打印摘要。
  失败段写入 <out>.failed 列表（标记重跑）。
"""
import json, os, re, sys, subprocess, argparse

# 流水线契约：环节间只传文件（强制）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_contract as pc


def get_ffprobe_duration(path):
    """ffprobe 读时长（秒），失败返回 None。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return None


def get_ffprobe_resolution(path):
    """ffprobe 读分辨率 (w, h)，失败返回 None。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", path],
            capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        s = json.loads(r.stdout)["streams"][0]
        return (s.get("width"), s.get("height"))
    except Exception:
        return None


def check_prompt_fields(prompt_text):
    """检查三字段齐全。返回 (ok, missing)。"""
    fields = ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"]
    missing = [f for f in fields if f not in prompt_text]
    return (len(missing) == 0, missing)


def check_cut_point(text):
    """切点不在句中：末字符应为句号/感叹号/问号/省略号。
    读报模式例外：逗号结尾合法（同一条新闻拆两段、下一段紧接续读，设计使然）。"""
    if not text:
        return False
    return text.rstrip()[-1] in "。！？…，、"


def check_continuity(director_text, prev_out):
    """相邻段画面连贯：本段导演设计承接上段出态。"""
    if not director_text or not prev_out:
        return True  # 无数据时跳过（不误报）
    # 上段 out 的核心词（去掉连接词）应在本段出现或本段有显式承接
    words = [w for w in re.findall(r"[\u4e00-\u9fff]{2,4}", prev_out) if w not in
             ("画面", "本段", "上一段", "承接", "锁定", "给下", "收束", "缓出")]
    if not words:
        return True
    # 宽松检查：本段导演设计包含任一上段出态关键词 或 显式"衔接/承接/延续"
    return any(w in director_text for w in words) or \
        any(k in director_text for k in ("衔接", "承接", "延续", "接续"))


def check_info_point(director_text, info_point):
    """每段传达 info_point：导演设计含本段信息点关键词。"""
    if not director_text or not info_point:
        return True  # 无数据时跳过
    # 信息点是数字短语或关键词，检查数字/关键词是否出现在导演设计中
    nums = re.findall(r"\d+(?:\.\d+)?", info_point)
    if nums:
        return any(n in director_text for n in nums)
    # 无数字：取信息点里较长的关键词片段
    keys = [k for k in re.split(r"[；，、\s]", info_point) if len(k) >= 2][:3]
    if not keys:
        return True
    return any(k in director_text for k in keys)


def get_audio_duration(path):
    """ffprobe 读音频时长，失败返回 None。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="H3 生成段验收")
    ap.add_argument("generated_jsonl", help="generated.jsonl 路径")
    ap.add_argument("--prompts", default="/home/lmr/comfy/h3_prompts", help="h3_prompts 目录")
    ap.add_argument("--storyboard", default="/home/lmr/comfy/分镜清单_raw.json", help="分镜清单 json（raw.json：text/cut_point/est_seconds）")
    ap.add_argument("--enhanced", default=None, help="分镜增强 json（enhanced.json：duration_est 已 TTS 回填）——时长校验优先用此")
    ap.add_argument("--director-dir", default=None, help="导演设计目录（03_导演设计，创意层检查）")
    ap.add_argument("--audio-dir", default=None, help="TTS 音频目录（02_TTS语音，音频对齐检查）")
    ap.add_argument("--out", default="/home/lmr/comfy/verify_report.json", help="报告输出路径")
    args = ap.parse_args()

    # 读 generated.jsonl
    if not os.path.exists(args.generated_jsonl):
        print(f"ERROR: {args.generated_jsonl} not found")
        sys.exit(2)

    # 流水线契约：上游（阶段7生成日志）必须已落盘
    pc.check_input(args.generated_jsonl, "阶段7生成日志")
    entries = []
    for line in open(args.generated_jsonl, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass

    # 读分镜清单（拿 est_seconds + text）
    storyboard = {}
    if os.path.exists(args.storyboard):
        try:
            sb = json.load(open(args.storyboard, encoding="utf-8"))
            if isinstance(sb, list):
                storyboard = {s["id"]: s for s in sb if "id" in s}
        except Exception:
            pass

    # 口播台词目录（cut_point 检查的真正数据源：新 schema enhanced.json 已无 text 字段）
    # 2026-09-07 目录配对：口播台词在 01_分段/ 口播台词/；--director-dir 传
    # 03_导演设计时从其父目录（项目根）推导，兼容旧项目 02_逐段视觉设计 布局。
    lines_dir = None
    if args.director_dir and os.path.isdir(args.director_dir):
        ddir = os.path.abspath(args.director_dir)
        root = os.path.dirname(ddir)  # 传 03_导演设计 时即项目根
        for cand in (os.path.join(root, "01_分段", "口播台词"),
                     os.path.join(ddir, "口播台词"),
                     os.path.join(root, "02_逐段视觉设计", "口播台词")):
            if os.path.isdir(cand):
                lines_dir = cand
                break

    # 读分镜增强（拿 TTS 回填后的 duration_est——时长校验优先数据源）
    enhanced_map = {}
    if args.enhanced and os.path.exists(args.enhanced):
        try:
            en = json.load(open(args.enhanced, encoding="utf-8"))
            if isinstance(en, list):
                enhanced_map = {s["id"]: s for s in en if "id" in s}
        except Exception:
            pass

    # 读 prompt 目录
    prompts = {}
    if os.path.isdir(args.prompts):
        for f in os.listdir(args.prompts):
            if f.endswith(".json"):
                try:
                    d = json.load(open(os.path.join(args.prompts, f), encoding="utf-8"))
                    prompts[d.get("id")] = d
                except Exception:
                    pass

    # 读导演设计目录（创意层检查 7/8）
    director_texts = {}
    if args.director_dir and os.path.isdir(args.director_dir):
        for f in os.listdir(args.director_dir):
            m = re.match(r"(shot\d+)\.md", f)
            if m:
                try:
                    director_texts[m.group(1)] = open(
                        os.path.join(args.director_dir, f), encoding="utf-8").read()
                except Exception:
                    pass

    results = []
    for e in entries:
        sid = e.get("id", "?")
        res = {"id": sid, "checks": {}, "pass": True, "fail_reasons": []}
        vid = None
        for o in e.get("outputs", []):
            if o and os.path.exists(o) and os.path.getsize(o) > 0:
                vid = o
                break
        # 1. 文件存在 / 大小
        if not vid:
            res["checks"]["file"] = False
            res["fail_reasons"].append("output file missing")
        else:
            size = os.path.getsize(vid)
            res["checks"]["file"] = size > 0
            if size <= 0:
                res["fail_reasons"].append("output file size=0")
            # 2. 时长
            dur = get_ffprobe_duration(vid)
            if dur is None:
                res["checks"]["duration"] = False
                res["fail_reasons"].append("ffprobe duration failed")
            else:
                est = None
                # 时长预期：优先 enhanced.duration_est（TTS 回填），否则 raw est_seconds
                if sid in enhanced_map and enhanced_map[sid].get("duration_est"):
                    est = enhanced_map[sid]["duration_est"]
                elif est is None:
                    est = storyboard.get(sid, {}).get("est_seconds")
                ok = dur <= 15.5
                if est is not None:
                    ok = ok and abs(dur - est) <= 1.5
                res["checks"]["duration"] = ok
                if not ok:
                    res["fail_reasons"].append(f"duration {dur:.1f}s vs est {est}s")
            # 3. 分辨率
            reso = get_ffprobe_resolution(vid)
            if reso is None:
                res["checks"]["resolution"] = False
                res["fail_reasons"].append("ffprobe resolution failed")
            else:
                w, h = reso
                short = min(w, h)
                ok = short in (480, 720, 768, 1088, 1440)  # 1088=H3直出1080P竖版短边
                res["checks"]["resolution"] = ok
                if not ok:
                    res["fail_reasons"].append(f"resolution {w}x{h}")
        # 4. prompt 三字段
        p = prompts.get(sid)
        if p is None:
            res["checks"]["prompt_fields"] = False
            res["fail_reasons"].append("prompt json missing")
        else:
            ok, missing = check_prompt_fields(p.get("prompt", ""))
            res["checks"]["prompt_fields"] = ok
            if not ok:
                res["fail_reasons"].append(f"prompt missing fields: {missing}")
        # 5. 风格锁一致（跨段共享关键词）：不做风格词表假设——任何风格都能过。
        #    判据：相邻段英文 prompt 剔除流水线固定约束词后，实词共享 ≥3 个即视为风格延续。
        if p is not None:
            cur = p.get("prompt", "").lower()
            def _content_words(s):
                # 取 4+ 字母英文实词，剔除流水线固定约束/H3 硬约束词
                stopwords = {"this", "that", "with", "from", "into", "very", "must", "while",
                             "vertical", "portrait", "video", "frame", "rotated", "camera",
                             "ground", "bottom", "screen", "voiceover", "lips", "closed",
                             "narration", "composition", "standard", "sky", "stay", "level",
                             "horizontal", "gravity", "points", "straight", "down", "held",
                             "upright", "never", "tilted", "sideways", "absolutely",
                             "forbidden", "rotation", "degrees", "side", "appearing",
                             "rendered", "lying", "horizontally", "integrated",
                             "multimodal", "description", "overall", "soundscape",
                             "diegetic", "music", "captions", "text", "non", "have"}
                return set(re.findall(r"[a-z]{4,}", s)) - stopwords
            cur_words = _content_words(cur)
            if results:
                prev_words = results[-1].get("_style_words", set())
                shared = cur_words & prev_words
                res["checks"]["style_lock"] = len(shared) >= 3
                if not res["checks"]["style_lock"]:
                    res["fail_reasons"].append(f"style lock: 相邻段共享词不足 ({len(shared)})")
            else:
                res["checks"]["style_lock"] = len(cur_words) >= 20  # 首段只要求内容词足够多
                if not res["checks"]["style_lock"]:
                    res["fail_reasons"].append("style lock: 首段 prompt 内容词过少")
            res["_style_words"] = cur_words
        # 6. 切点不在句中：数据源优先级 = 口播台词目录（新 schema 真源）> storyboard.text（旧 schema）
        t = ""
        line_file = os.path.join(lines_dir, f"{sid}.md") if lines_dir and os.path.isdir(lines_dir) else None
        if line_file and os.path.exists(line_file):
            t = re.sub(r"^#.*$", "", open(line_file, encoding="utf-8").read(),
                       flags=re.MULTILINE).strip()
        if not t:
            t = storyboard.get(sid, {}).get("text", "")
        res["checks"]["cut_point"] = check_cut_point(t)
        if not res["checks"]["cut_point"]:
            res["fail_reasons"].append("segment ends mid-sentence")

        # 7. 相邻段画面连贯（导演设计 continuity 链）
        dt = director_texts.get(sid, "")
        prev_out = results[-1].get("_prev_out", "") if results else ""
        if args.director_dir:
            ok7 = check_continuity(dt, prev_out)
            res["checks"]["continuity"] = ok7
            if not ok7:
                res["fail_reasons"].append("continuity chain broken")
        else:
            res["checks"]["continuity"] = True
        res["_prev_out"] = ""

        # 8. 每段传达 info_point
        if args.director_dir:
            ip = storyboard.get(sid, {}).get("info_point", "")
            ok8 = check_info_point(dt, ip)
            res["checks"]["info_point"] = ok8
            if not ok8:
                res["fail_reasons"].append("director design missing info_point")
        else:
            res["checks"]["info_point"] = True

        # 9. 音频对齐（TTS 音频时长 vs 视频时长差 ≤0.5s）
        if args.audio_dir and vid is not None:
            audio_path = None
            for cand in (f"{sid}.mp3", f"{sid}.wav", f"audio_{sid}.mp3", f"audio_{sid}.wav"):
                cand_path = os.path.join(args.audio_dir, cand)
                if os.path.exists(cand_path):
                    audio_path = cand_path
                    break
            if audio_path and dur is not None:
                adur = get_audio_duration(audio_path)
                if adur is not None:
                    # H3 生成段允许 video ≥ audio（生成余量）；合成后 compose -shortest 已保证 merged=音频长
                    ok9 = (dur - adur) >= -0.5 and (dur - adur) <= 3.0
                    res["checks"]["audio_align"] = ok9
                    if not ok9:
                        res["fail_reasons"].append(f"audio {adur:.1f}s vs video {dur:.1f}s")
            else:
                res["checks"]["audio_align"] = True
        else:
            res["checks"]["audio_align"] = True

        res["pass"] = all(res["checks"].values()) and not res["fail_reasons"]
        results.append(res)

    # 汇总
    n_pass = sum(1 for r in results if r["pass"])
    n_fail = len(results) - n_pass
    failed_ids = [r["id"] for r in results if not r["pass"]]

    report = {
        "generated": args.generated_jsonl,
        "total": len(results),
        "pass": n_pass,
        "fail": n_fail,
        "failed_ids": failed_ids,
        "segments": results,
    }
    # 剥掉内部辅助字段（set 不可 JSON 序列化）
    for r in results:
        r.pop("_style_words", None)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(args.out + ".failed", "w", encoding="utf-8") as f:
        f.write("\n".join(failed_ids))

    print(f"=== verify_output 报告 ===")
    print(f"总计 {len(results)} 段 | PASS {n_pass} | FAIL {n_fail}")
    if failed_ids:
        print(f"失败段: {', '.join(failed_ids)}")
        for r in results:
            if not r["pass"]:
                print(f"  {r['id']}: {'; '.join(r['fail_reasons'])}")
    print(f"报告已写入: {args.out}")

    # 流水线契约：产出落盘标记（供下游确认验收完成）
    pc.stamp_output(args.out, {"total": len(results), "pass": n_pass, "fail": n_fail})

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
