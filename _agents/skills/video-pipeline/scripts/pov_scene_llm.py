#!/usr/bin/env python3
"""pov_scene_llm.py — 用 LLM（本地 Strata，#86 起）为 POV 段生成语义相关的第一人称画面描述。

替代 gen_prompts_auto.py 里的关键词模板 POV 生成（用户要求：风格化应交给 LLM，不用关键词硬编码）。

用法：
  pov_scene_llm.py <enhanced.json> <pov_list.json> --out pov_scenes.json [--model strata]
    pov_list.json: [{id, text, info_point}] POV 段列表
    输出: {shotXX: "scene description"}

每条 prompt 要求 LLM 输出：
  - 第一人称桌面视角（从"我"的视角看到自己的手）
  - 纸质拼贴风（handmade paper collage, layered hand-cut paper, rough edges, hard paper-cut shadows）
  - 画面与台词内容语义相关（不是统一剪拼贴）
  - 无真人脸/全身（only hands and paper workbench, no face）
"""
import json, os, sys, argparse, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_json

SYSTEM = """你是视频分镜设计师。为一段中文口播台词设计"第一人称视角"（POV）画面。

硬性要求：
1. 视角必须是第一人称：观众看到的是叙述者自己的双手在工作台上操作（over-the-shoulder 桌面视角），绝对不要出现任何人的脸、全身、或"从第三人称看叙述者"
2. 视觉风格：handmade paper collage stop-motion style, layered hand-cut paper with visible fibers, rough edges, hard paper-cut shadows（纸质拼贴立体场景）
3. 画面必须与台词的**语义内容**相关（台词讲什么，画面就演什么），不能是通用的"剪纸张"画面
4. 画面是一个 8-12 秒的微动作场景，描述要有画面感（动作 + 物体 + 材质）
5. 只用英文输出一段场景描述（80-150 词），不要编号、不要解释、不要换行以外的格式

示例输入：台词"把650W的电源纸盒放在秤上，指针猛地掉下去"
示例输出：A first-person desktop POV over a paper craft workbench, the narrator's own hands placing a small paper power-supply box labeled 650W onto a tiny paper balance scale, the scale needle visibly dropping, torn paper scraps and a glue stick nearby, handmade paper collage stop-motion style, layered hand-cut paper with visible fibers, rough edges and hard paper-cut shadows. No face, no person, only hands and the paper workbench."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pov_list")
    ap.add_argument("--out", default="pov_scenes.json")
    ap.add_argument("--model", default="strata")
    ap.add_argument("--concurrency", type=int, default=4, help="POV 段并行度（默认4）")
    args = ap.parse_args()

    povs = json.load(open(args.pov_list, encoding="utf-8"))
    result = {}
    # 断点续跑：已存在的不重生成
    if os.path.exists(args.out):
        result = json.load(open(args.out, encoding="utf-8"))

    print(f"共 {len(povs)} 个 POV 段，已生成 {len(result)} 个")
    write_lock = threading.Lock()

    def process_one(p):
        sid = p["id"]
        if sid in result:
            return f"[SKIP] {sid}"
        user = f"台词：{p['text']}\n\n请为这段台词设计第一人称 POV 画面。"
        try:
            scene, _fr = llm_json.llm_call_text(SYSTEM, user, model=args.model,
                                                budget=500, verbose=False)
        except ValueError as e:
            return f"[FAIL] {sid}: {e}"
        # 线程安全落盘（每段保存，断点续跑）
        with write_lock:
            result[sid] = scene
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        return f"[OK] {sid}: {scene[:60]}..."

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = [ex.submit(process_one, p) for p in povs]
        for fut in as_completed(futs):
            print(fut.result())

    print(f"\n完成: {len(result)}/{len(povs)} → {args.out}")


if __name__ == "__main__":
    main()
