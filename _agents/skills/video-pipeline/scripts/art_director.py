#!/usr/bin/env python3
"""art_director.py — 美术画室（阶段5，新增）

输入：04_分镜增强/enhanced.json（摄影师：机位/光线基线 → 光质依据）
      03_导演设计/shotXX.json（导演：场景/主体 → 材质/景深依据）
      03_导演设计/00_交接摘要.md（风格锁）
      styles/<风格>.md（风格块 → 风格分流选词）
输出：05_美术画质/画质设计.json（每段 {光质, 材质, 景深, 负面词, 风格锚定}）
校验：schema.ART_SCHEMA 强校验（3 词非空 + 风格锚定）

用法：
  art_director.py <enhanced.json> <design_dir> <overall_summary> <style_name> --out <画质设计.json>
"""
import json, os, sys, re, argparse, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_json
import schema

SYSTEM = """你是资深美术/画质指导。为每个镜头的画面设计画质修饰词（quality modifiers），让 AI 画面更精致、更真实、更有电影质感。

你必须基于镜头已有的画面设计来配词（不能凭空造），并尊重风格块的负向约束。

只输出一个 JSON 对象（不要任何 JSON 以外文字、不要 markdown 围栏），字段严格为：
{"id": "段号", "光质": "光质锚定词（1-2句，基于摄影师光线基线与导演场景，如 丁达尔圣光斜射/冷蓝月白主光/暖色窗光）", "材质": "材质词（1-2句，基于导演主体，如 青石玉润/纸纤维粗糙/金属冷硬）", "景深": "景深词（1-2句，基于机位与主体距离，如 浅景深前景虚化/深景深全景清晰）", "负面词": "负面词基线（blurry, lowres, watermark, 畸形 等，按风格块负向约束）", "风格锚定": "尊重哪个风格（如 天宫=冷蓝仙侠禁现代元素）"}

硬性要求：
1. 画质词必须锚定内容、就地配（禁止 prompt 定稿后统一后贴）
2. 每段至少 1 光质 + 1 材质 + 1 景深（按风格块分流选词）
3. 去 AI 味：真实 = 带瑕疵（颗粒/光比/纹理），不是更清晰
4. 权重纪律：主体名词 > 动作动词 > 运镜词 > 美学形容词
5. 风格感知：尊重风格块负向约束（勿混风格）
"""


def load_style(style_name):
    """读风格块（styles/<名>.md），取负向约束供美术层参考。"""
    if not style_name:
        return ""
    for base in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "video-craft", "styles"),
                 os.path.expanduser("~/references/video-craft/styles")):
        fp = os.path.join(base, f"{style_name}.md")
        if os.path.exists(fp):
            txt = open(fp, encoding="utf-8").read()
            # 取负向约束 + 视觉语言（供选词）
            m = re.search(r"## 负向约束\n(.*?)(?=\n## |\Z)", txt, re.S)
            neg = m.group(1).strip()[:400] if m else ""
            m2 = re.search(r"## 视觉语言\n(.*?)(?=\n## |\Z)", txt, re.S)
            vis = m2.group(1).strip()[:400] if m2 else ""
            return f"视觉语言:\n{vis}\n\n负向约束:\n{neg}"
    return ""


def load_design(design_dir, sid):
    """读导演设计 shotXX.json（新）或 shotXX.md（旧），返回文本。"""
    jfp = os.path.join(design_dir, f"{sid}.json")
    mfp = os.path.join(design_dir, f"{sid}.md")
    if os.path.exists(jfp):
        try:
            d = json.load(open(jfp, encoding="utf-8"))
            return "\n".join(f"【{k}】{v}" for k, v in d.items() if k != "id")
        except Exception:
            pass
    if os.path.exists(mfp):
        return open(mfp, encoding="utf-8").read()
    return "(无导演设计)"


def main():
    ap = argparse.ArgumentParser(description="美术画室（阶段5）：逐段画质词设计")
    ap.add_argument("enhanced_json", help="04_分镜增强/分镜清单_enhanced.json")
    ap.add_argument("design_dir", help="03_导演设计/")
    ap.add_argument("overall_summary", help="03_导演设计/00_交接摘要.md")
    ap.add_argument("--style", default=None, help="风格名（styles/<名>.md）")
    ap.add_argument("--out", default=None, help="输出 json（默认 05_美术画质/画质设计.json）")
    ap.add_argument("--model", default="strata")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=9999)
    args = ap.parse_args()

    shots = json.load(open(args.enhanced_json, encoding="utf-8"))
    style_txt = load_style(args.style)
    overall = open(args.overall_summary, encoding="utf-8").read() if os.path.exists(args.overall_summary) else ""

    # 断点续跑
    out_path = args.out or "05_美术画质/画质设计.json"
    result = {}
    if os.path.exists(out_path):
        result = {s["id"]: s for s in json.load(open(out_path, encoding="utf-8"))}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    def process_shot(s):
        sid = s["id"]
        if sid in result:
            return f"[SKIP] {sid}"
        design = load_design(args.design_dir, sid)
        ph = s
        user = (
            f"### {sid}\n"
            f"导演设计：\n{design[:1200]}\n\n"
            f"摄影师（光线基线/机位/锚点）：\n"
            f"锚点: {json.dumps(ph.get('reference_anchors', {}), ensure_ascii=False)}\n"
            f"秒级指令: {json.dumps(ph.get('per_second_directives', [])[:3], ensure_ascii=False)}\n\n"
            f"整体风格锁：\n{overall[:600]}\n\n"
            f"风格块（选词必须尊重此）：\n{style_txt[:800]}"
        )
        try:
            obj = llm_json.llm_call_json(SYSTEM, user, schema.ART_SCHEMA,
                                         model=args.model, budget=800, verbose=False)
        except ValueError as e:
            return f"[FAIL] {sid}: {e}"
        obj["id"] = sid
        # 落盘（线程安全：每段写独立键，最后合并）
        result[sid] = obj
        return f"[OK] {sid} 光质={obj['光质'][:18]}"

    sem = threading.Semaphore(max(1, args.concurrency))
    def _run(s):
        with sem:
            return process_shot(s)

    todo = [s for s in shots if args.start <= int(re.match(r"shot(\d+)", s["id"]).group(1)) <= args.end]
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = [ex.submit(_run, s) for s in todo]
        for f in as_completed(futs):
            print(f.result())

    # 落盘（按原顺序）
    ordered = [result[s["id"]] for s in shots if s["id"] in result]
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    json.dump(ordered, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n完成: {len(ordered)}/{len(shots)} → {out_path}")


if __name__ == "__main__":
    main()
