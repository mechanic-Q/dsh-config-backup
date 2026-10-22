#!/usr/bin/env python3
"""merge_pov_scenes.py — 把 LLM 生成的 POV 场景写回 prompt JSON。

pov_scenes.json: {shotXX: "scene description"}
prompt 目录: shotXX.json（含 mode: pov 的段）
只替换 mode=pov 的段的 integrated_multimodal_description（保留 soundscape/music）。

用法: merge_pov_scenes.py <prompt_dir> <pov_scenes.json>
"""
import json, os, sys, glob

prompt_dir, scenes_file = sys.argv[1], sys.argv[2]
scenes = json.load(open(scenes_file, encoding="utf-8"))

SOUNDSCAPE_POV = ("overall_soundscape: Soft paper rustling, gentle cut-and-place sounds, "
                  "subtle ambient room tone.\n\nnon_diegetic_music: N/A")

updated = 0
for fp in sorted(glob.glob(os.path.join(prompt_dir, "shot*.json"))):
    sid = os.path.basename(fp)[:-5]
    if sid not in scenes:
        continue
    d = json.load(open(fp, encoding="utf-8"))
    if d.get("mode") != "pov":
        print(f"[SKIP] {sid} mode={d.get('mode')}（非 POV，跳过）")
        continue
    scene = scenes[sid].strip()
    d["prompt"] = (
        f"integrated_multimodal_description: [Shot 1] {scene} "
        "The visuals play as off-screen voiceover narration with lips remaining completely closed. "
        "No on-screen text, no subtitles, no captions.\n\n"
        f"{SOUNDSCAPE_POV}"
    )
    d["scene_source"] = "llm"  # 标记来源便于追溯
    json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    updated += 1
    print(f"[OK] {sid} 场景已写回")

print(f"\n完成: 更新 {updated} 个 POV 段")
