#!/usr/bin/env python3
"""compose_final.py — 阶段7：拼接完整视频（视频段 + 口播音频 + BGM）。

流程：
  1. 按 shot01..shotNN 顺序收集 480P 视频段
  2. 收集对应口播音频（02_TTS语音/shotXX.wav）
  3. ffmpeg 逐段合并（视频+音频）→ 4. concat 全部段 → 5. 叠 BGM（mixkit 322/580，0.2-0.33x + fade）

用法：
  compose_final.py <video_dir> <audio_dir> <output_dir> [--bgm <path>] [--bgm-volume 0.25]
"""
import json, os, sys, subprocess, argparse, glob, re, tempfile


def _txt(x):
    """子进程输出统一收敛成 str（None→""，bytes 容错解码）。"""
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    return x


def _tail(x, n=300):
    """子进程输出尾巴统一收敛成 str。

    2026-10-20 实锤（10-19 期 s09）：Windows 侧 `r.stderr` 偶发为 None（同命令手动直跑
    正常，未定位到触发条件），末行 `r.stderr[-300:]` 直接 TypeError，把整期装配冲掉，
    而且真正的 ffmpeg 失败行永远看不到。这里 None→""、bytes→容错解码，保证 ff() 只
    用 rc 表达失败，不因取尾巴而崩。
    """
    return _txt(x)[-n:]


def ff(cmd, timeout=300):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.returncode, _tail(r.stdout), _tail(r.stderr)


SFX_CACHE = os.path.join(tempfile.gettempdir(), "page_flip.wav")  # #151 双宿主临时目录
# 内置真实翻书录音（CC0，supaluci「Page Turn, Big Book」，2026-09-05 用户选定）；
# 资产缺失时回退 legacy 合成纸声。许可与来源见 assets/sfx/README.md
SFX_ASSET = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "assets", "sfx", "page_flip_bigbook.wav"))

def _make_flip_sfx(rate, layout, gap=1.0):
    """翻页过渡音效：优先内置真实录音（assets/sfx），缺失时回退 ffmpeg 合成纸声。
    输出钳制到垫片时长 gap（末尾 0.12s 淡出，避免 1s 垫片截断自然衰减产生咔哒）。"""
    try:
        if os.path.exists(SFX_ASSET):
            r, _, _ = ff(["ffmpeg", "-y", "-i", SFX_ASSET,
                          "-af", f"atrim=0:{gap:.2f},afade=t=out:st={max(0.0, gap - 0.15):.2f}:d=0.12",
                          "-ar", str(rate), "-ac", str(2 if layout == "stereo" else 1),
                          "-c:a", "pcm_s16le", SFX_CACHE])
            if r == 0 and os.path.exists(SFX_CACHE):
                return SFX_CACHE
        # 兜底：legacy 合成（白噪+棕噪模拟纸声）——#151 改 argv 直调（原 bash -c 两侧不通用）
        raw = os.path.join(tempfile.gettempdir(), "page_flip_raw.wav")
        if not os.path.exists(raw):
            r = ff(["ffmpeg", "-y",
                    "-f", "lavfi", "-i", "anoisesrc=color=white:duration=0.25:amplitude=0.7",
                    "-f", "lavfi", "-i", "anoisesrc=color=brown:duration=0.5:amplitude=0.9",
                    "-filter_complex",
                    "[0:a]highpass=f=1500,lowpass=f=9000,afade=t=in:d=0.03,afade=t=out:st=0.12:d=0.13[wh];"
                    "[1:a]lowpass=f=900,afade=t=in:st=0.08:d=0.1,afade=t=out:st=0.28:d=0.22[br];"
                    "[wh][br]amix=inputs=2:duration=longest:normalize=0,volume=1.1[aout]",
                    "-map", "[aout]", "-c:a", "pcm_s16le", raw], timeout=60)
            if r[0] != 0 or not os.path.exists(raw):
                return None
        ch = 2 if layout == "stereo" else 1
        rc, _, se = ff(["ffmpeg", "-y", "-i", raw,
                        "-af", "afade=t=out:st=0.55:d=0.45",
                        "-ar", str(rate), "-ac", str(ch), SFX_CACHE])
        if rc == 0:
            return SFX_CACHE
        return None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_dir")   # 480P 视频目录
    ap.add_argument("audio_dir")   # 口播音频目录
    ap.add_argument("output_dir")  # 07_成片 输出
    ap.add_argument("--bgm", default=None)
    ap.add_argument("--bgm-volume", type=float, default=0.05, help="BGM 音量（铺底，默认 0.05——用户红线：BGM 不得盖人声，报纸/主体同音量）")
    ap.add_argument("--max-shots", type=int, default=9999)
    # 读报模式三段装配（SKILL.md 读报标准 2026-08-29 定稿）
    ap.add_argument("--intro", default=None, help="片头视频（/mnt/e/每日新中国/每日新中国片头.mp4），放最前")
    ap.add_argument("--newspaper", default=None, help="当日报纸图 PNG（报纸段底图，日期三合一校验后）")
    ap.add_argument("--welcome-audio", default=None, help="欢迎词音频（用户音色'欢迎来到每日新中国…'），报纸段配音")
    ap.add_argument("--news-boundaries", default=None,
                    help="逗号分隔的新闻起始段 id（如 shot01,shot05）。只有这些段淡入、其前段淡出+垫黑场；"
                         "同一条新闻的多段镜头之间不加过渡。缺省=每段都当边界（旧行为）")
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # 收集视频段（按 shotNN 排序，兼容带项目前缀名如 icehouse-20260812_shot01_00006_.mp4）
    vids = {}
    for f in glob.glob(os.path.join(args.video_dir, "*_shot*.mp4")) + glob.glob(os.path.join(args.video_dir, "shot*.mp4")):
        m = re.search(r"shot(\d+)", os.path.basename(f))
        if m:
            vids[m.group(0)] = f
    shot_ids = sorted(vids.keys(), key=lambda x: int(re.search(r"\d+", x).group()))
    shot_ids = [s for s in shot_ids if int(s.replace("shot", "")) <= args.max_shots]
    print(f"收集到 {len(shot_ids)} 个视频段")

    # 读报三段装配：片头视频段 + 报纸配音段（最前），主体新闻接后（SKILL.md 读报标准）
    merged_dir = os.path.join(args.output_dir, "_merged")
    # 清掉上一轮残留的垫片文件（否则陈旧垫片会被读报装配循环按"存在即插入"塞回，
    # 2026-09-05 验收实测：旧全段垫片残留导致同新闻段间出现黑场）
    for old in glob.glob(os.path.join(merged_dir, "_gap_*.mp4")):
        os.remove(old)
    os.makedirs(merged_dir, exist_ok=True)
    concat_list = []
    missing_audio = []

    # --- ① 片头视频段（保留原片头音轨，无 BGM）---
    if args.intro and os.path.exists(args.intro):
        intro_out = os.path.join(merged_dir, "_intro.mp4")
        # 片头缩放对齐竖版（1080×1944 → 1088×1920 保持 9:16）
        rc, so, se = ff(["ffmpeg", "-y", "-i", args.intro,
                         "-vf", "scale=1088:1920:force_original_aspect_ratio=increase,crop=1088:1920,fps=24",
                         "-c:v", "libx264", "-preset", "fast", "-crf", "24",
                         "-c:a", "aac", "-b:a", "160k", intro_out])
        if rc == 0:
            concat_list.append(f"file '{intro_out}'")
            print(f"[片头] 已装配: {intro_out}")
        else:
            print(f"[FAIL] 片头装配: {se[-200:]}")

    # --- ② 报纸配音段（报纸图 + 欢迎词音频，缩放对齐竖版）---
    # 2026-09-05 用户定稿：报纸必须铺满画面但整页不出画 → 模糊放大垫底 + 整页满宽居中
    # （老问题：黑边 pad 被用户多次打回；装配后有检查点闸门）
    if args.newspaper and os.path.exists(args.newspaper):
        news_out = os.path.join(merged_dir, "_newspaper.mp4")
        np_vf = ("[0:v]scale=1088:1920:force_original_aspect_ratio=increase,crop=1088:1920,"
                 "boxblur=luma_radius=24:luma_power=2,eq=brightness=-0.08[bg];"
                 "[0:v]scale=1088:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=24,format=yuv420p[v]")
        if args.welcome_audio and os.path.exists(args.welcome_audio):
            # 报纸图 + 欢迎词音频 → 竖版视频段（音频时长=视频时长）
            rc, so, se = ff(["ffmpeg", "-y", "-loop", "1", "-i", args.newspaper,
                             "-i", args.welcome_audio,
                             "-filter_complex", np_vf, "-map", "[v]", "-map", "1:a",
                             "-c:v", "libx264", "-preset", "fast", "-crf", "24",
                             "-c:a", "aac", "-b:a", "160k",
                             "-shortest", news_out])
        else:
            # 无欢迎词：报纸图静态 5s（静音）
            rc, so, se = ff(["ffmpeg", "-y", "-loop", "1", "-i", args.newspaper,
                             "-t", "5", "-filter_complex", np_vf, "-map", "[v]",
                             "-c:v", "libx264", "-preset", "fast", "-crf", "24",
                             "-an", news_out])
        if rc == 0:
            concat_list.append(f"file '{news_out}'")
            print(f"[报纸段] 已装配: {news_out}")
            # 检查点（老毛病，2026-09-05）：报纸必须铺满——失败模式=黑边/纯黑带（旧 pad 版本）
            # 判据：四角与顶部平均亮度 ≥60（黑边≈16，奶油模糊垫底≈150+）；std 不可用（纸底色天然平坦）
            try:
                from PIL import Image
                _fc = os.path.join(tempfile.gettempdir(), "_np_check.jpg")  # #151
                ff(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", news_out,
                    "-frames:v", "1", _fc])
                im = Image.open(_fc).convert("L")
                W, H = im.size
                corner = max(4, W // 20)
                regions = [im.crop((0, 0, corner, corner)), im.crop((W - corner, 0, W, corner)),
                           im.crop((0, H - corner, corner, H)), im.crop((W - corner, H - corner, W, H)),
                           im.crop((0, 0, W, max(4, H // 25)))]
                means = [sum(r.getdata()) / (r.size[0] * r.size[1]) for r in regions]
                np_ok = min(means) >= 60
                print(f"[报纸检查点] 四角/顶部亮度: {[round(m) for m in means]} → "
                      f"{'✓ 铺满' if np_ok else '✗ 黑边未铺满'}")
                if not np_ok:
                    sys.exit("报纸段未铺满画面（黑边检查点拦截）")
            except SystemExit:
                raise
            except Exception as _e:
                print(f"[报纸检查点] 跳过（{_e}）")
        else:
            print(f"[FAIL] 报纸段装配: {se[-200:]}")

    # 逐段合并视频+音频（如果音频存在）
    # 新闻间停顿（2026-09-03 定稿）：每段尾部补 1s 黑屏静音垫片——两条新闻之间留呼吸，
    # 上条新闻的标题字幕在垫片里自然消失，下条新闻从黑场进入。
    GAP_S = float(os.environ.get("SEG_GAP_S", "1.0"))
    pad_list = []
    PAD_SIZE = None
    PAD_FPS = "30/1"
    PAD_RATE = "44100"
    PAD_LAYOUT = "mono"
    # 新闻边界（2026-09-05 用户定稿）：过渡只出现在新闻条目之间，同条新闻的段间无过渡
    # B = 新闻起始段集合；未提供参数时退化为"每段都是边界"（旧行为，兼容非读报模式）
    if args.news_boundaries is not None:
        B = set(s.strip() for s in args.news_boundaries.split(",") if s.strip())
    else:
        B = set(shot_ids)
    for idx, sid in enumerate(shot_ids):
        v = vids[sid]
        a = os.path.join(args.audio_dir, f"{sid}.wav")
        out = os.path.join(merged_dir, f"{sid}.mp4")
        next_sid = shot_ids[idx + 1] if idx + 1 < len(shot_ids) else None
        fade_in = sid in B
        fade_out = (next_sid in B) if next_sid else True
        # 淡入/淡出只在新闻边界（需要重编码；同条新闻内的中间段 -c:v copy 零损）
        _pd = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", v], capture_output=True, text=True, timeout=30)
        try:
            _vdur = float((_txt(_pd.stdout) or "0").strip().splitlines()[0])
        except Exception:
            _vdur = 0.0
        _fades = []
        if fade_in and _vdur > 0.8:
            _fades.append("fade=t=in:d=0.35")
        if fade_out and _vdur > 0.8:
            _fades.append(f"fade=t=out:st={max(0.0, _vdur - 0.35):.3f}:d=0.35")
        _fade_vf = ",".join(_fades)
        if os.path.exists(a):
            # 视频+音频合并（音频对齐视频时长，不足循环/超出裁剪）
            cmd = ["ffmpeg", "-y", "-i", v, "-i", a]
            if _fade_vf:
                cmd += ["-vf", _fade_vf, "-c:v", "libx264", "-preset", "fast", "-crf", "18"]
            else:
                cmd += ["-c:v", "copy"]
            cmd += ["-c:a", "aac", "-shortest",
                    "-map", "0:v:0", "-map", "1:a:0", out]
        else:
            # 无音频：仅视频（保持静音）
            missing_audio.append(sid)
            cmd = ["ffmpeg", "-y", "-i", v, "-c:v", "copy", "-an", out]
        rc, so, se = ff(cmd)
        if rc != 0:
            print(f"[FAIL] {sid} merge: {se[-200:]}")
            continue
        concat_list.append(f"file '{out}'")
        # 新闻边界垫片：下一段是新新闻时，本段后跟 1s 纯黑过渡 + 翻书页声（2026-09-05 定稿）
        # 同条新闻的段间不加任何过渡
        if next_sid in B and GAP_S > 0:
            pad = os.path.join(merged_dir, f"_gap_{sid}.mp4")
            if PAD_SIZE is None:
                import json as _json
                _p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                     "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", out],
                                    capture_output=True, text=True, timeout=30)
                _d = _json.loads(_txt(_p.stdout) or "{}")
                _s = (_d.get("streams") or [{}])[0]
                PAD_SIZE = f"{_s.get('width', 1088)}x{_s.get('height', 1920)}"
                PAD_W = _s.get("width", 1088)
                PAD_FPS = _s.get("r_frame_rate", "24/1")
                _pa = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                                      "-show_entries", "stream=sample_rate,channels", "-of", "json", out],
                                     capture_output=True, text=True, timeout=30)
                _ad = _json.loads(_txt(_pa.stdout) or "{}")
                _as = (_ad.get("streams") or [{}])[0]
                PAD_RATE = _as.get("sample_rate", "44100")
                PAD_CH = _as.get("channels", 1)
                PAD_LAYOUT = "stereo" if str(PAD_CH) == "2" else "mono"
            # 视觉：纯黑垫片（淡入淡出在镜头侧做，见下方 merge 步骤）；音频：纸张翻动声（合成音效，缺则静音）
            # 注意：音效用 apad 补齐 1s，不能靠 -shortest（音效只有 0.6s 会把视频截掉）
            sfx = _make_flip_sfx(PAD_RATE, PAD_LAYOUT, GAP_S)
            if sfx:
                sfx_args = ["-i", sfx, "-filter_complex", "[1:a]apad=pad_dur=" + str(GAP_S) + "[aout]", "-map", "0:v", "-map", "[aout]"]
            else:
                sfx_args = ["-f", "lavfi", "-i", f"anullsrc=r={PAD_RATE}:cl={PAD_LAYOUT}:d={GAP_S}", "-map", "0:v", "-map", "1:a"]
            prc, _, pse = ff(["ffmpeg", "-y",
                              "-f", "lavfi", "-i",
                              f"color=c=black:s={PAD_SIZE}:d={GAP_S}:r={PAD_FPS},"
                              f"format=yuv420p",
                              *sfx_args,
                              "-t", str(GAP_S),
                              "-c:v", "libx264", "-preset", "fast", "-crf", "24",
                              "-c:a", "aac", pad])
            if prc == 0:
                pad_list.append(pad)
            else:
                print(f"[WARN] {sid} 垫片生成失败: {pse[-120:]}（跳过，段间无停顿）")
    print(f"合并完成 {len(concat_list)} 段" + (f"，{len(missing_audio)} 段无音频: {missing_audio[:5]}" if missing_audio else ""))

    if not concat_list:
        print("无可拼接段")
        sys.exit(1)

    # concat 全部段（非读报模式：段间也插黑屏垫片；读报模式主体垫片在 _main_shots.txt 阶段插）
    # ⚠️ concat 条目里的路径按 _concat.txt 所在目录解析——output_dir 传相对路径时会双重嵌套
    # （2026-09-03 修：条目统一转绝对路径）
    list_file = os.path.join(args.output_dir, "_concat.txt")
    out_list = []
    for entry in concat_list:
        p = entry[len("file '"):-1] if entry.startswith("file '") else entry
        ap = os.path.abspath(p)
        out_list.append(f"file '{ap}'")
    if not (args.intro and os.path.exists(args.intro)):
        interleaved = []
        for i, entry in enumerate(out_list):
            interleaved.append(entry)
            is_shot = "shot" in entry and "_gap_" not in entry
            is_last_shot = i == len(out_list) - 1 or (
                i + 1 < len(out_list) and "shot" not in out_list[i + 1])
            if is_shot and not is_last_shot and pad_list:
                # 找到本段对应的垫片
                import re as _re
                m = _re.search(r"shot(\d+)", entry)
                if m:
                    pad = os.path.join(merged_dir, f"_gap_shot{int(m.group(1)):02d}.mp4")
                    if os.path.exists(pad):
                        interleaved.append(f"file '{pad}'")
        out_list = interleaved
    with open(list_file, "w", encoding="utf-8") as f:
        f.write("\n".join(out_list))
    final = os.path.join(args.output_dir, "成片_无BGM.mp4")
    rc, so, se = ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                     "-i", list_file, "-c", "copy", final])
    if rc != 0:
        print(f"[FAIL] concat: {se[-200:]}")
        sys.exit(1)
    print(f"拼接完成: {final}")

    # BGM 叠加（方案A：片头段不叠 BGM，报纸/主体段各叠——读报三段结构铁律）
    # 读报模式（--intro/--newspaper）：片头段无 BGM（保留原音效），报纸段起 BGM 贯穿
    # 非读报模式：整片叠 BGM（原逻辑）
    if args.bgm and os.path.exists(args.bgm):
        final_bgm = os.path.join(args.output_dir, "成片_最终.mp4")

        if args.intro and os.path.exists(args.intro):
            # ===== 读报模式：分三段处理 =====
            # 段时长：片头 = intro_dur，报纸 = news_dur（用 ffprobe 实测），主体 = 其余
            import subprocess as _sp

            def _dur(p):
                rp = _sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", p], capture_output=True, text=True, timeout=60)
                return float(_txt(rp.stdout).strip() or 0)

            intro_p = os.path.join(merged_dir, "_intro.mp4")
            news_p = os.path.join(merged_dir, "_newspaper.mp4")
            intro_dur = _dur(intro_p) if os.path.exists(intro_p) else 0
            news_dur = _dur(news_p) if os.path.exists(news_p) else 0
            print(f"[BGM分段] 片头 {intro_dur:.1f}s(无BGM) | 报纸 {news_dur:.1f}s")

            # 报纸段：叠 BGM（从 0 起，长度 news_dur）。报纸/主体 BGM 同音量（用户要求一致）
            news_bgm = os.path.join(args.output_dir, "_news_bgm.mp4")
            news_bgm_vol = args.bgm_volume
            ff(["ffmpeg", "-y", "-stream_loop", "-1", "-i", args.bgm, "-t", str(news_dur),
                "-af", f"volume={news_bgm_vol},afade=t=in:d=0.5,afade=t=out:st={max(0, news_dur-1)}:d=1",
                "-c:a", "libmp3lame", os.path.join(args.output_dir, "_news_bgm_cut.mp3")])
            ff(["ffmpeg", "-y", "-i", news_p, "-i", os.path.join(args.output_dir, "_news_bgm_cut.mp3"),
                "-filter_complex", "[0:a]volume=1.0[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
                "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2", news_bgm])

            # 主体段：shot 段 concat（不含片头/报纸，含段间垫片），叠 BGM
            # 先拼 _main_src（含垫片）→ 实测 main_dur → 裁 BGM 到位（避免先裁后拼的时长错位）
            main_src = os.path.join(args.output_dir, "_main_src.mp4")
            shot_list = os.path.join(args.output_dir, "_main_shots.txt")
            shot_segs = []
            for sid in shot_ids:
                if os.path.exists(os.path.join(merged_dir, f"{sid}.mp4")):
                    shot_segs.append(f"file '{os.path.abspath(os.path.join(merged_dir, f'{sid}.mp4'))}'")
                    pad = os.path.join(merged_dir, f"_gap_{sid}.mp4")
                    if sid != shot_ids[-1] and os.path.exists(pad):
                        shot_segs.append(f"file '{os.path.abspath(pad)}'")
            with open(shot_list, "w", encoding="utf-8") as f:
                f.write("\n".join(shot_segs))
            ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", shot_list, "-c", "copy", main_src])
            main_dur = _dur(main_src)
            main_bgm = os.path.join(args.output_dir, "_main_bgm.mp4")
            ff(["ffmpeg", "-y", "-stream_loop", "-1", "-i", args.bgm, "-t", str(main_dur),
                "-af", f"volume={args.bgm_volume},afade=t=in:d=0.5,afade=t=out:st={max(0, main_dur-2)}:d=2",
                "-c:a", "libmp3lame", os.path.join(args.output_dir, "_main_bgm_cut.mp3")])
            # 视频重编码 30fps（H3 原生 24fps，与 intro/news 30fps 对齐，否则 concat 混帧率时间轴错位）
            ff(["ffmpeg", "-y", "-i", main_src, "-i", os.path.join(args.output_dir, "_main_bgm_cut.mp3"),
                "-filter_complex", "[0:a]volume=1.0[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
                "-map", "0:v", "-map", "[aout]", "-vf", "fps=24", "-c:v", "libx264", "-preset", "fast", "-crf", "24",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", main_bgm])

            # 三段重拼：片头(无BGM) + 报纸(有BGM) + 主体(有BGM)
            # 条目统一绝对路径（concat 相对路径按 list 文件所在目录解析，会双重嵌套）
            seg_list = os.path.join(args.output_dir, "_seg_concat.txt")
            segs = []
            if os.path.exists(intro_p):
                segs.append(intro_p)
            if os.path.exists(news_bgm):
                segs.append(news_bgm)
            if os.path.exists(main_bgm):
                segs.append(main_bgm)
            with open(seg_list, "w", encoding="utf-8") as f:
                f.write("\n".join(f"file '{os.path.abspath(s)}'" for s in segs))
            ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", seg_list, "-c", "copy", final_bgm])
            print(f"BGM 叠加完成(片头无BGM): {final_bgm}")

            # LOUDCHECK：主体段混合后人声主导（≤3dB 抬升）。只测主体段（片头静音区会拉低整片 mean 造成误报）
            def _mean_vol(p):
                rp = _sp.run(["ffmpeg", "-i", p, "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120)
                for ln in _txt(rp.stderr).split('\n'):
                    if 'mean_volume' in ln:
                        return float(ln.split(':')[1].strip().replace(' dB', ''))
                return None

            v_mean = _mean_vol(main_src)      # 主体段人声-only（无BGM）
            m_mean = _mean_vol(main_bgm)      # 主体段混合后
            if v_mean is not None and m_mean is not None:
                delta = m_mean - v_mean
                print(f"[LOUDCHECK] 人声-only {v_mean:.1f}dB | 混合后 {m_mean:.1f}dB | 差 {delta:+.1f}dB")
                if delta > 3.0:
                    print(f"[LOUDCHECK] BGM 抬升 {delta:+.1f}dB > 3dB，自动降 BGM ×0.7 重混")
                    # 重混报纸段+主体段：全部 BGM ×0.7
                    ff(["ffmpeg", "-y", "-i", os.path.join(args.output_dir, "_news_bgm_cut.mp3"),
                        "-af", "volume=0.7", "-c:a", "libmp3lame",
                        os.path.join(args.output_dir, "_news_bgm_cut2.mp3")])
                    ff(["ffmpeg", "-y", "-i", os.path.join(args.output_dir, "_main_bgm_cut.mp3"),
                        "-af", "volume=0.7", "-c:a", "libmp3lame",
                        os.path.join(args.output_dir, "_main_bgm_cut2.mp3")])
                    # 重混报纸段
                    ff(["ffmpeg", "-y", "-i", news_p, "-i", os.path.join(args.output_dir, "_news_bgm_cut2.mp3"),
                        "-filter_complex", "[0:a]volume=1.0[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
                        "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2", news_bgm])
                    # 重混主体段
                    ff(["ffmpeg", "-y", "-i", main_src, "-i", os.path.join(args.output_dir, "_main_bgm_cut2.mp3"),
                        "-filter_complex", "[0:a]volume=1.0[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
                        "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2", main_bgm])
                    # 三段重拼
                    ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", seg_list, "-c", "copy", final_bgm])
                    m2 = _mean_vol(main_bgm)  # 主体段重混后（对比 main_src）
                    if m2 is not None:
                        print(f"[LOUDCHECK] 重混后 混合 {m2:.1f}dB | 差 {m2 - v_mean:+.1f}dB")
                    else:
                        print("[LOUDCHECK] 重混后响度读取失败")
            else:
                print("[LOUDCHECK] 响度读取失败")
        else:
            # ===== 非读报模式：整片叠 BGM（原逻辑）=====
            # 先取成片时长
            rc, so, se = ff(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration", "-of", "csv=p=0", final])
            dur = float(so.strip() or 0)
            # BGM 循环铺满全片 + fade（-stream_loop -1 循环，-t 裁剪到成片时长）
            bgm_cut = os.path.join(args.output_dir, "_bgm_cut.mp3")
            ff(["ffmpeg", "-y", "-stream_loop", "-1", "-i", args.bgm, "-t", str(dur),
                "-af", f"volume={args.bgm_volume},afade=t=in:d=0.5,afade=t=out:st={max(0, dur-2)}:d=2",
                "-c:a", "libmp3lame", bgm_cut])
            # 混合
            rc, so, se = ff(["ffmpeg", "-y", "-i", final, "-i", bgm_cut,
                             "-filter_complex", "[0:a]volume=1.0[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
                             "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", final_bgm])
            if rc == 0:
                # 硬检测：混音后人声必须保持主导（BGM 不得盖过人声）
                # 判据：混合后 mean_volume - 人声-only mean_volume ≤ 3dB（铺底容差）
                # 超限自动降 BGM（×0.7）重混一次，再超则 WARN（防回归，用户红线）
                import subprocess as _sp

                def _mean_vol(p):
                    rp = _sp.run(["ffmpeg", "-i", p, "-af", "volumedetect", "-f", "null", "-"],
                                 capture_output=True, text=True, timeout=120)
                    for ln in _txt(rp.stderr).split('\n'):
                        if 'mean_volume' in ln:
                            return float(ln.split(':')[1].strip().replace(' dB', ''))
                    return None

                v_mean = _mean_vol(final)          # 人声-only
                m_mean = _mean_vol(final_bgm)      # 混合后
                if v_mean is not None and m_mean is not None:
                    delta = m_mean - v_mean
                    print(f"[LOUDCHECK] 人声-only {v_mean:.1f}dB | 混合后 {m_mean:.1f}dB | 差 {delta:+.1f}dB")
                    if delta > 3.0:
                        print(f"[LOUDCHECK] BGM 抬升 {delta:+.1f}dB > 3dB，自动降 BGM ×0.7 重混")
                        bgm_cut2 = os.path.join(args.output_dir, "_bgm_cut2.mp3")
                        ff(["ffmpeg", "-y", "-i", bgm_cut, "-af", "volume=0.7",
                            "-c:a", "libmp3lame", bgm_cut2])
                        rc2, _, _ = ff(["ffmpeg", "-y", "-i", final, "-i", bgm_cut2,
                                        "-filter_complex", "[0:a]volume=1.0[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
                                        "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", final_bgm])
                        if rc2 == 0:
                            m2 = _mean_vol(final_bgm)
                            if m2 is not None:
                                print(f"[LOUDCHECK] 重混后 混合 {m2:.1f}dB | 差 {m2 - v_mean:+.1f}dB")
                            else:
                                print("[LOUDCHECK] 重混后响度读取失败")
                        else:
                            print("[LOUDCHECK] 重混失败，保留原版（人工检查）")
                print(f"BGM 叠加完成: {final_bgm}")
            else:
                print(f"[FAIL] bgm mix: {se[-200:]}")
    else:
        print(f"无 BGM（或文件不存在）: {args.bgm}")
        print(f"最终成片: {final}")


if __name__ == "__main__":
    main()
