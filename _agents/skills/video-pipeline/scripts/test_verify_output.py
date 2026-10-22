#!/usr/bin/env python3
"""verify_output 自检测试：构造好段+坏段，断言六项检查正确。

用法：python3 test_verify_output.py（无依赖，仅标准库）
"""
import json, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(__file__))
import verify_output as V


def make_video(path, seconds, w=768, h=432):
    """用 ffmpeg 造一个指定时长/分辨率的无声视频。"""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={w}x{h}:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        capture_output=True, timeout=60)
    return os.path.exists(path) and os.path.getsize(path) > 0


GOOD_PROMPT = """integrated_multimodal_description: [Shot 1] Live-action, cinematic VOX-style tech-explainer. The narrator is a 35-year-old Chinese male tech creator... says in an off-screen voiceover while lips remain completely closed. No on-screen text.

overall_soundscape: Low room tone with soft keyboard clicks.

non_diegetic_music: N/A"""

BAD_PROMPT = "just a short prompt without fields"


def main():
    tmp = tempfile.mkdtemp(prefix="verify_test_")
    results = []

    # 1. 三字段检查
    ok, missing = V.check_prompt_fields(GOOD_PROMPT)
    results.append(("prompt fields good", ok is True and missing == []))
    ok2, missing2 = V.check_prompt_fields(BAD_PROMPT)
    results.append(("prompt fields bad", ok2 is False and len(missing2) == 3))

    # 2. 切点检查
    results.append(("cut point good", V.check_cut_point("这是一段完整的话。")))
    results.append(("cut point bad", not V.check_cut_point("这是一段没说完的话")))

    # 3. ffprobe 时长/分辨率（造真实视频）
    good_vid = os.path.join(tmp, "good.mp4")
    bad_vid = os.path.join(tmp, "bad.mp4")
    if make_video(good_vid, 10.0, 768, 768) and make_video(bad_vid, 30.0, 1920, 1080):
        d1 = V.get_ffprobe_duration(good_vid)
        results.append(("duration probe", d1 is not None and abs(d1 - 10.0) < 0.5))
        r1 = V.get_ffprobe_resolution(good_vid)
        results.append(("resolution good", r1 == (768, 768)))
        r2 = V.get_ffprobe_resolution(bad_vid)
        results.append(("resolution bad short-edge not in allowlist", min(r2) not in (480, 720, 768, 1440)))
    else:
        results.append(("ffmpeg make_video", False))

    # 4. 集成：构造 generated.jsonl（1 好 1 坏）+ storyboard + prompts
    sb = [
        {"id": "good", "text": "这一段是完整的句子。", "est_seconds": 10.0},
        {"id": "bad", "text": "这一段没说完", "est_seconds": 5.0},
    ]
    sb_path = os.path.join(tmp, "sb.json")
    json.dump(sb, open(sb_path, "w", encoding="utf-8"))
    gen = [
        {"id": "good", "status": "success", "outputs": [good_vid]},
        {"id": "bad", "status": "success", "outputs": [bad_vid]},
    ]
    gen_path = os.path.join(tmp, "gen.jsonl")
    with open(gen_path, "w", encoding="utf-8") as f:
        for e in gen:
            f.write(json.dumps(e) + "\n")
    pr_dir = os.path.join(tmp, "prompts")
    os.makedirs(pr_dir)
    json.dump({"id": "good", "prompt": GOOD_PROMPT}, open(os.path.join(pr_dir, "good.json"), "w"))
    json.dump({"id": "bad", "prompt": BAD_PROMPT}, open(os.path.join(pr_dir, "bad.json"), "w"))

    out = os.path.join(tmp, "report.json")
    sys.argv = ["verify_output.py", gen_path, "--prompts", pr_dir,
                "--storyboard", sb_path, "--out", out]
    # 直接调 main（捕获 SystemExit）
    try:
        V.main()
        exit_code = 0
    except SystemExit as e:
        exit_code = e.code

    rep = json.load(open(out, encoding="utf-8"))
    by_id = {s["id"]: s for s in rep["segments"]}
    results.append(("integration: good passes", by_id.get("good", {}).get("pass") is True))
    results.append(("integration: bad fails", by_id.get("bad", {}).get("pass") is False))
    results.append(("integration: exit code 1 (has fail)", exit_code == 1))
    results.append(("integration: failed_ids recorded", rep["failed_ids"] == ["bad"]))

    # 5. 风格锁检查（前 120 字符公共前缀比例）
    res_good = {"_prefix": GOOD_PROMPT[:120]}
    p_bad_prefix = "totally different prompt starting with another style..." + GOOD_PROMPT[:60]
    ratio = len(os.path.commonprefix([res_good["_prefix"], p_bad_prefix[:120]])) / 120.0
    results.append(("style lock mismatch detected", ratio < 0.5))

    # 汇总
    npass = sum(1 for _, ok in results if ok)
    print(f"\n=== verify_output 自检: {npass}/{len(results)} 通过 ===")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
