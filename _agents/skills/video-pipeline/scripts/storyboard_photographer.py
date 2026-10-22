#!/usr/bin/env python3
"""storyboard_photographer.py — 阶段4：LLM 摄影师分镜增强（8字段再创作）。

身份：摄影师（不是规则匹配，是摄影知识驱动的镜头语言再创作）。
输入：逐段视觉设计（导演意图）+ 整体视觉方案交接摘要（风格锁）
  - 03_导演设计/shotXX.md   （导演：这段讲什么/情绪/重点）
  - 03_导演设计/00_交接摘要.md（摄影师：风格锁/色彩基线/禁忌）
  - 03_导演设计/00_整体视觉方案.md（全篇场景系统/母题，供连续性参考）
  - 01_分段/分镜清单_raw.json（id/text/est_seconds）
输出：04_分镜增强/分镜清单_enhanced.json（每段 8 字段）

8 字段：
  duration_est / 节奏意图 / info_point / shot_type
  + continuity_handoff / reference_anchors / hook_type / per_second_directives

方法论注入：references/video-craft/{camera-language,camera-movement,shot-grammar}.md

用法：
  storyboard_photographer.py <raw.json> <逐段设计目录> <整体方案目录> <out_json> [--model strata]
  storyboard_photographer.py <raw.json> 03_导演设计 03_导演设计 04_分镜增强/分镜清单_enhanced.json
"""
import json, os, sys, argparse, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# 流水线契约：环节间只传文件（强制）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_contract as pc
import llm_json
import schema

# 枚举已收敛到 schema.py（单一事实源），此处不重复定义
CHAR_PER_SEC = schema.CHAR_PER_SEC  # 单一事实源（schema.py，实测 6.4）
PAD_SEC = 0.75
MAX_SEC = 15.0

SYSTEM_PHOTOGRAPHER = """你是资深电影摄影师，专长景别/机位/运镜/构图/光线/连续性设计。

方法论注入（先读以下摄影知识文件，再据此创作）：
- references/video-craft/camera-language.md  （景别选择决策表、机位高度、构图规则）
- references/video-craft/camera-movement.md  （推/拉/摇/移/手持/环绕语义）
- references/video-craft/shot-grammar.md     （景别×节奏×运镜表、连续性管理、Hook分布）
- references/video-craft/continuity-editing.md（连续性剪辑规则）
- references/video-craft/camera-language.md 的景别决策表优先于模板映射（景别是创作选择）
- references/video-craft/katz-shot-by-shot.md（卡茨 9 域决策表：对话调度/移动调度/机位角度/景深）
- references/video-craft/camera-angle-depth.md（机位角度与景框深度专项决策表）

身份层：
- 你是摄影师，导演已定好"这段讲什么、什么情绪、什么镜头决策、重点是什么"，你负责"怎么拍"的执行细节
- **导演已做的你不做**：不重定景别/节奏/Hook/信息点（导演的【镜头】是唯一镜头权威，你按其意图做秒级执行）
- 镜头语言必须服务导演意图（内容/情绪），不是套模板
- 景别是创作选择不是机械分配：铺垫=远景/全景建立空间、建立=全景/中景、预备=中景、冲击=特写、制动=中景、稳定=全景/远景
- 连续性：每段 in 承接上段 out（画面/主体/光线/空间锚点延续），段间成链
- 秒级指令：0s-镜尾逐秒，五要素（动作/镜头/空间/音频/交接）——**硬性覆盖要求：首条 time 必须以 0 起，末条必须覆盖到段时长的 80% 以上，每镜至少 5 条（≤5s 短段至少 3 条且必须到尾）；覆盖不足 = 校验失败重做**
- 光质随节奏：冲击=硬光高对比、平稳=柔和主光（灯光师归摄影师管，类似风格化）

约束层：
- 口播原文不变（这是镜头设计不是改写）
- 段时长 = 字数÷CHAR_PER_SEC + 0.75s，上限 15s
- 画面内无人声（口播是旁白）
- 竖屏约束：9:16 垂直构图，天空正上，地面正下，不旋转，镜头正立；禁环形/环绕/俯视

输出：**只输出一个 JSON 对象**，字段严格为：
{"id": "段号", "duration_est": 秒数, "per_second_directives": [{"time": "0-1s", "action": "动作", "camera": "镜头", "spatial": "空间", "audio": "音频", "handoff": "交接"}], "reference_anchors": {"subject_position": "主体位置", "lighting_baseline": "光线基线", "fixed_landmarks": ["固定地标"]}, "continuity_handoff": {"in": "承接上段", "out": "交给下段"}}
只输出 JSON，不要解释过程，不要 markdown 围栏。"""


def read_file_utf8(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser(description="LLM 摄影师分镜增强")
    ap.add_argument("raw_json", help="01_分段/分镜清单_raw.json")
    ap.add_argument("design_dir", help="03_导演设计/（shotXX.md 导演设计）")
    ap.add_argument("vision_dir", help="03_导演设计/（交接摘要+整体方案）")
    ap.add_argument("out_json", help="04_分镜增强/分镜清单_enhanced.json")
    ap.add_argument("--model", default="strata")
    ap.add_argument("--max-shots", type=int, default=9999)
    ap.add_argument("--concurrency", type=int, default=4, help="逐镜并行度（默认4；本地服务排队串行，安全值）")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out_json) or ".", exist_ok=True)

    # 契约：上游 02 逐段视觉设计必须已落盘
    pc.check_input(args.raw_json, "阶段3分段产物")
    if not os.path.isdir(args.design_dir):
        print(f"ERROR: 逐段设计目录不存在 {args.design_dir}")
        sys.exit(2)

    shots = json.load(open(args.raw_json, encoding="utf-8"))
    shots = [s for s in shots if int(s["id"][4:]) <= args.max_shots]
    print(f"共 {len(shots)} 段")

    # 读导演/摄影师上下文（交接摘要优先，整体方案全文备查）
    summary_fp = os.path.join(args.vision_dir, "00_交接摘要.md")
    overall_fp = os.path.join(args.vision_dir, "00_整体视觉方案.md")
    vision_ctx = ""
    if os.path.exists(summary_fp):
        vision_ctx = "【整体视觉方案·交接摘要（必须遵守）】\n" + read_file_utf8(summary_fp)
    elif os.path.exists(overall_fp):
        vision_ctx = "【整体视觉方案（摘要）】\n" + read_file_utf8(overall_fp)[:1500]
    else:
        print(f"WARN: 未找到 03_导演设计/ 交接摘要或整体方案（{args.vision_dir}）")

    # 逐段设计上下文：shotXX.json（新）优先，shotXX.md（旧）fallback
    designs = {}
    for f in sorted(os.listdir(args.design_dir)):
        m = re.match(r"shot(\d+)\.(json|md)$", f)
        if m:
            sid = f"shot{int(m.group(1)):02d}"
            fp = os.path.join(args.design_dir, f)
            if f.endswith(".json"):
                try:
                    d = json.load(open(fp, encoding="utf-8"))
                    # JSON 结构 → 可读文本
                    designs[sid] = "\n".join(f"【{k}】{v}" for k, v in d.items() if k != "id")
                except Exception:
                    designs[sid] = read_file_utf8(fp)
            else:
                designs[sid] = read_file_utf8(fp)
    print(f"已读 {len(designs)} 段导演设计")

    # 已生成的跳过（续跑）——兼容旧 8 字段（含 shot_type）与新执行字段
    done = set()
    if os.path.exists(args.out_json):
        try:
            prev = json.load(open(args.out_json, encoding="utf-8"))
            done = {s["id"] for s in prev if s.get("per_second_directives") or s.get("shot_type")}
            print(f"已存在 {len(done)} 段，续跑跳过")
        except Exception:
            done = set()

    # 逐镜并行（每段独立 LLM 调用 + schema 强校验，ThreadPoolExecutor 并发）
    # 每段成功后立即落盘（线程安全增量写）——中途被杀不丢已完成的段（断点续跑）
    results = []
    todo = [s for s in shots if s["id"] not in done]
    write_lock = threading.Lock()

    def process_one(idx_s):
        idx, s = idx_s
        sid = s["id"]
        design = designs.get(sid, "(无导演设计)")
        print(f"[摄影师] {idx + 1}/{len(todo)} {sid} ...")
        user = vision_ctx + "\n\n" + (
            f"### {sid}\n"
            f"台词：{s['text']}\n"
            f"字数：{s['chars']} 预计：{s['est_seconds']}s\n"
            f"导演设计（据此做秒级执行，不重定决策）：\n{design[:1200]}\n"
        )
        try:
            obj = llm_json.llm_call_json(
                SYSTEM_PHOTOGRAPHER, user, schema.PHOTOGRAPHER_SCHEMA,
                model=args.model, budget=3000, verbose=False)
        except ValueError as e:
            print(f"  [FAIL] {sid}: {e}")
            return None
        obj["id"] = sid
        # 时长权威 = s4b 回填的 TTS 实测（音频先行，音频>视频时 compose -shortest 会掐音频）。
        # 2026-09-06 根治：曾用字数估算覆盖回填值 → 短于真实音频的段被掐口播尾巴
        # （0905 实证 shot09 掐 0.55s / shot15 掐 0.14s）。无回填时才退回字数公式。
        dur = float(s.get("duration_est") or round(s["chars"] / CHAR_PER_SEC + PAD_SEC, 1))
        obj["duration_est"] = min(dur, MAX_SEC)
        # 秒级指令覆盖归一（2026-09-05 s4 ⑥ 实证：末条尾值差 0.025s 即 FAIL）：
        # 按归一后段长把末条时间窗拉到段尾，只改时间不改动作内容
        p2s = obj.get("per_second_directives") or []
        if p2s:
            nums = re.findall(r"[\d.]+", str(p2s[-1].get("time", "")))
            if nums and float(nums[-1]) < obj["duration_est"]:
                p2s[-1]["time"] = f"{nums[0]}-{obj['duration_est']}s"
        # 增量落盘（线程安全）：已有文件全部段 + 新完成段
        # 注意：不能按被 --max-shots 截断的 shots 遍历（会丢已有段），须按已有 id 集合保留
        with write_lock:
            by_id = {}
            if os.path.exists(args.out_json):
                try:
                    for prev in json.load(open(args.out_json, encoding="utf-8")):
                        by_id[prev["id"]] = prev
                except Exception:
                    pass
            by_id[sid] = obj
            # 保留：已有文件全部 id + 本次新完成；顺序按 raw 全量（但只保留存在的）
            merged_now = list(by_id.values())
            merged_now.sort(key=lambda x: x.get("id", ""))
            with open(args.out_json, "w", encoding="utf-8") as f:
                json.dump(merged_now, f, ensure_ascii=False, indent=2)
            pc.stamp_output(args.out_json, {"total": len(merged_now), "done": sid})
        return obj

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = [ex.submit(process_one, (i, s)) for i, s in enumerate(todo)]
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.append(r)
            time.sleep(0.5)

    # 合并：新生成 + 已有（兼容旧 8 字段产物）
    # 注意：按 by_id 全量输出（不能按被 --max-shots 截断的 shots，会丢已有段）
    by_id = {s["id"]: s for s in results}
    if os.path.exists(args.out_json):
        try:
            for s in json.load(open(args.out_json, encoding="utf-8")):
                if s["id"] not in by_id and (s.get("per_second_directives") or s.get("shot_type")):
                    by_id[s["id"]] = s
        except Exception:
            pass
    merged = list(by_id.values())
    merged.sort(key=lambda x: x.get("id", ""))

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    pc.stamp_output(args.out_json, {"total": len(merged), "new": len(results)})
    print(f"=== 摄影师分镜增强完成 ===\n{len(merged)} 段 → {args.out_json}（新 {len(results)} 段）")
    print("下一步：跑 storyboard_enhancer.py 做七项自检")


if __name__ == "__main__":
    main()
