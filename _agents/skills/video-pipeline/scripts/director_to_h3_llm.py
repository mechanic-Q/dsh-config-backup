#!/usr/bin/env python3
"""director_to_h3_llm.py — 导演设计 → H3 三字段 prompt（LLM 转换版）。

流程：读 03_导演设计/shotXX.md → LLM 转英文三字段 → 校验约束 → 写回 06_H3prompt/shotXX.json

约束校验（写回前强制）：
  - 三字段齐全
  - off-screen voiceover + lips remain completely closed（无则自动插入）
  - non_diegetic_music: N/A
  - paper collage 风格标记（大小写不敏感）
  - POV 段（idx%5==0）要求第一人称描述

用法：director_to_h3_llm.py <设计目录> <输出目录> --start N --end M [--pov-ratio 0.2]
"""
import json, os, sys, re, argparse, glob
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_json
import schema

# （#86 拆除）本模块原有自备 HTTP 客户端 llm_call + API 端点常量——全仓无调用点的
# 死代码（9router 云端缺省）一并退役；LLM 通道统一走 llm_json（本地 Strata 缺省端点）。

SYSTEM = """你是 MiniMax H3 视频生成提示词专家。把中文视觉导演设计转成 H3 官方三字段英文 prompt。

必须遵守：
1. integrated_multimodal_description: 用英文详细描述画面（场景/主体/镜头/动作时间轴/材质风格），保留导演设计的全部创意细节和动作时间轴结构；若输入含【美术画质设计】，其光质/材质/景深词必须融入画面描述（不是另起段落）；负面词只用于指导"避免什么"，不作为正面描述写入
2. overall_soundscape: 英文环境音描述（贴合风格块的声音设计）
3. non_diegetic_music: N/A
4. 画面内无人声：结尾必须写 "The visuals play as off-screen voiceover narration while lips remain completely closed. No on-screen text, no subtitles, no captions."
5. POV 段（标记为第一人称）：开头写第一人称 POV 描述（叙述者手部在画面内操作，无脸无全身），贴合风格块
6. 非 POV 段：写 "No person appears in this shot"
7. 风格块中的"H3英文关键词"必须融入 integrated_multimodal_description 与 overall_soundscape
8. **官方分段语法（必守）**：integrated_multimodal_description 必须按镜头分段，使用 [Shot N] 语法——
   - 首镜：`[Shot 1] <风格声明>, <构图/主体>...`（首镜不加时间戳）
   - 后续镜：`[Shot 2] At 00:SS.SSS, the camera cuts to ...`（时间戳严格递增，来自摄影师秒级指令的 time 切点）
   - 切镜用 `the camera cuts to` / `the shot cuts to` / `the shot transitions to`
   - 镜头变化若只是距离/角度微调，用相机运动而非切镜
9. **风格声明（必守）**：`[Shot 1]` 开头必须声明整体风格（Cinematic / live-action / 2D-animated / 3D CG / claymation / watercolor / vintage film 等，从风格块选），再写构图与主体
10. **相机运动三要素（必守）**：相机运动必须写 类型+幅度+速度，如 `The camera pushes in with small amplitude at slow speed toward ...`；类型用 Push In/Pull Out/Pan/Tilt/Truck/Tracking/Static/Arc/POV，幅度用 with small/large amplitude，速度用 at slow/fast speed；自然嵌入句子，不堆叠标签
11. **叙述者 ID（必守）**：旁白叙述者用稳定 ID (S1)，写法 `The narrator (S1) says in an off-screen voiceover ...`，跨镜头保持 (S1) 不变；说话人首次出现时用视觉/音频语境建立稳定身份
12. **画面内文字（必守）**：任何画面中实际可见的文字（横幅/招牌/标签/字幕/霓虹灯）用英文双引号标出，原样保留：`A red neon sign reading "营业中" glows above the doorway.`；若画面不应出现可读文字（风格块要求），写 "no readable on-screen text"
13. **只输出一个 JSON 对象**（不要任何 JSON 以外文字、不要 markdown 围栏），严格三字段。**integrated_multimodal_description 必须填充完整真实的画面描述（350-500 个英文单词，信息完整但禁止注水堆砌；禁止用省略号/占位符/"..."替代）**——长度达标即止，写完即收，输出越长生成越慢：
   {"integrated_multimodal_description": "A detailed cinematic description of the scene, subject, camera movement, action timeline and material style, 350-500 words in English — complete but concise. Never use ellipsis or placeholders.", "overall_soundscape": "A detailed English description of the ambient sound for this shot, 60-100 words.", "non_diegetic_music": "N/A"}
14. 【竖屏方向约束·必守】这是竖屏 9:16 视频。描述画面时必须确保：天空在画面正上方、地面/人群在正下方、地平线水平、重力向下、镜头正立（不侧放）。**禁止**横向构图、禁止把画面旋转 90°、禁止俯视鸟瞰、禁止倾斜地平线、禁止天空出现在画面侧面、禁止把竖直物体（柱子/人）横着摆放。场景设计优先用**竖直纵深构图**（道路/赛道/阶梯从前景向画面顶部灭点延伸、两侧物体竖直堆叠），**避免环形/圆形环绕式场景**（如 ring-shaped arena、circular colosseum、看台环绕全景）——这类场景在竖屏里容易生成成横构图旋转 90°。**integrated_multimodal_description 的第一句（紧接 [Shot 1] 风格声明后）必须是竖屏约束句**，不是结尾！竖屏句在结尾时模型注意力弱、对横构图场景会 90° 旋转：
   "This is a standard vertical 9:16 portrait video. The camera is held upright and locked off, with the sky at the very top of the frame, the ground at the very bottom, the sky-ground boundary perfectly level, and gravity pointing straight down. The composition is a vertical portrait: tall structures rise upward through the frame, and the scene recedes from the foreground at the bottom toward a vanishing point at the top."

{STYLE_BLOCK}"""

CONSTRAINT = ("The visuals play as off-screen voiceover narration while lips remain completely closed. "
              "No on-screen text, no subtitles, no captions.")

# 竖屏方向约束（P0，2026-08-31 用户纠正：环形/环绕场景会生成横构图旋转90°）
# 唯一源在 schema.py（S6：单一事实源），此处引用
PORTRAIT_CONSTRAINT = schema.PORTRAIT_CONSTRAINT
# 竖屏风险词（环形/环绕/俯视 → 易生成横构图旋转90°）
PORTRAIT_RISK_TERMS = ["ring-shaped arena", "circular colosseum", "circular arena", "ring arena",
                       "from above", "aerial view", "bird's eye", "overhead view", "top view",
                       "panoramic shot", "surrounding stands", "stands rising around", "colosseum",
                       # 2026-09-05 追加（horizontal-composition 实证：shot15 静景横构图仍旋转）：
                       # 水平长条/成排元素 → 命中段标记 portrait_risk，s9 强制多帧 vision 复核
                       "bench", "terrace", "rack", "shelf", "platform", "long table",
                       "in a row", "rows of", "aligned", "colonnade", "corridor", "bridge"]

# 相机运动句检测（2026-09-05 shot15 实证新失败模式：导演【镜头】运镜在转译时整句丢失，
# 横移门/消毒都无从触发）。约束句（vertical 9:16 / NOT rotated）自带 camera+tilt 字样，
# 必须先剔除再匹配，否则恒真。
_CAMERA_MOVE_RE = re.compile(
    r"\b(?:the )?camera\b[^.!?]{0,90}?\b(?:move[sd]?|moving|pan(?:s|ning)?|tilt(?:s|ed|ing)?|"
    r"push(?:es|ing)?|pull(?:s|ed|ing)?|track(?:s|ed|ing)?|drift(?:s|ing)?|glid(?:e|es|ing)|"
    r"slid(?:e|ing)|descend(?:s|ing)?|ascend(?:s|ing)?|ris(?:e|es|ing)|zoom|dolly|crane|sweep(?:s|ing)?|"
    r"orbit(?:s|ing)?|arc(?:s|ing)?|revolv(?:e|es|ing)|circl(?:e|es|ing))\b"
    r"|\borbit(?:s|ing)?\b[^.!?]{0,60}?\bcamera\b"
    r"|\bstatic (?:locked )?(?:shot|camera|wide shot)\b|remains? (?:mostly )?still|holding a stable"
    r"|zero amplitude",
    re.I)


def _prompt_has_camera_motion(prompt):
    """prompt（剔除竖屏约束句后）是否含相机运动/静止描述句。"""
    body = "\n".join(
        s for s in re.split(r"(?<=[.!?])\s+|\n", prompt or "")
        if "vertical 9:16" not in s and "NOT rotated" not in s)
    return bool(_CAMERA_MOVE_RE.search(body))


def infer_h3_mode(refs):
    """参考资产 → 官方模式（S6b：定向适配）。
    无参考 → T2VA；首帧图 → I2VA；尾帧图 → L2VA；首尾两图 → FL2VA；其他多资产 → Ref2VA。
    返回 (mode, ref_desc)。
    """
    if not refs:
        return "t2v", ""
    images = [r for r in refs if r["type"] == "image"]
    pos = {r["pos"] for r in images}
    if len(images) == 2 and pos == {"first", "last"}:
        return "fl2va", f"{len(refs)} 个参考（首尾帧 {[r['path'] for r in images]}）"
    if images and "first" in pos:
        return "i2va", f"首帧参考 {images[0]['path']}"
    if images and "last" in pos:
        return "l2va", f"尾帧参考 {images[0]['path']}"
    return "ref2va", f"{len(refs)} 个参考资产（多资产/角色/视频/音频）"


def load_style(style_name):
    """读 styles/<名>.md 全文作为风格块注入 system prompt。空/None 返回空串。"""
    if not style_name:
        return ""
    for base in (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "references", "video-craft", "styles"),
                 os.path.expanduser("~/references/video-craft/styles")):
        sp = os.path.join(base, f"{style_name}.md")
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as f:
                return f.read()
    print(f"WARN: 风格文件不存在 {style_name}（references/video-craft/styles/{style_name}.md），无风格块运行")
    return ""


def fill_style(template, style_block):
    return template.replace("{STYLE_BLOCK}", style_block)


def load_style(style_name):
    """读 styles/<名>.md 全文作为风格块注入 system prompt。空/None 返回空串。"""
    if not style_name:
        return ""
    for base in (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "references", "video-craft", "styles"),
                 os.path.expanduser("~/references/video-craft/styles")):
        fp = os.path.join(base, f"{style_name}.md")
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as f:
                return f.read()
    print(f"WARN: 风格文件不存在 {style_name}（references/video-craft/styles/{style_name}.md），无风格块运行")
    return ""


def fill_style(template, style_block):
    return template.replace("{STYLE_BLOCK}", style_block)


def enforce_constraints(prompt, is_pov):
    """校验 + 强制约束。返回 (修正后prompt, 问题列表)。"""
    issues = []
    p = prompt
    if "integrated_multimodal_description" not in p:
        issues.append("缺 imd 字段")
    if "overall_soundscape" not in p:
        issues.append("缺 soundscape 字段")
    if "non_diegetic_music: N/A" not in p:
        p = p.rstrip() + "\n\nnon_diegetic_music: N/A"
        issues.append("music 补 N/A")
    has_vo = "voiceover narration" in p and ("lips remain completely closed" in p or "lips remaining completely closed" in p)
    if not has_vo:
        p = p.replace("overall_soundscape:", f"{CONSTRAINT}\n\noverall_soundscape:")
        issues.append("补 off-screen 约束")
    if is_pov and "first-person pov" not in p.lower():
        issues.append("POV 段缺第一人称标记")
    # 竖屏方向约束（P0）：约束句必须位于 desc 开头（模型注意力最强处），
    # 强制从 LLM 输出中提取竖屏句并移到开头；缺则注入。命中风险词 → 警告。
    # 注：竖屏句在结尾时 H3 对横构图场景（太空俯瞰/平台横排）仍会 90° 旋转——开头约束 + 抽帧拦截双保险
    vert_sentence = ""
    m = re.search(r"This is a standard vertical 9:16 portrait video[^\n]*", p)
    if m:
        vert_sentence = m.group(0)
        # 从原位置删除（可能在 desc 内或 desc 后）
        p = p.replace(vert_sentence, "")
        # 清理可能残留的重复空行
        p = re.sub(r"\n{3,}", "\n\n", p)
    else:
        # 取 PORTRAIT_CONSTRAINT 的竖屏句部分
        vm = re.search(r"This is a standard vertical 9:16 portrait video[^\n]*", PORTRAIT_CONSTRAINT)
        vert_sentence = vm.group(0) if vm else PORTRAIT_CONSTRAINT
    # 插入到 desc 开头（integrated_multimodal_description: 之后、[Shot 1] 之前）
    # —— 竖屏句必须在 desc 最前（注意力最强），[Shot 1] 紧随其后，官方格式允许
    if "integrated_multimodal_description:" in p:
        p = p.replace("integrated_multimodal_description:", f"integrated_multimodal_description:\n{vert_sentence}", 1)
    else:
        p = f"integrated_multimodal_description:\n{vert_sentence}\n\n" + p
    if "vertical 9:16" not in p.lower():
        issues.append("补竖屏方向约束")
    pl = p.lower()
    # 整词匹配（"rack" 曾误命中 "tracking"；短词必须加词边界）
    hit = [t for t in PORTRAIT_RISK_TERMS if re.search(r"\b" + re.escape(t) + r"\b", pl)]
    if hit:
        issues.append(f"⚠️竖屏风险词命中: {hit}（环形/环绕/俯视场景易生成横构图旋转90°，需人工复核）")
    # 横移运镜消毒（2026-09-05 验收教训）：横移/左右 tracking 词诱发 H3 输出横构图/90°旋转
    # （翻转的 shot06/07/16 全是横移段）。竖屏语境改写为纵向/推进运镜。
    PAN_REWRITES = [
        (r"camera (?:executes |begins |performs )?(?:a |an )?(?:smooth |steady |slow[- ]speed )?horizontal (?:tracking|truck|pan)(?: shot|ning)?", "a slow vertical tilt-down"),
        (r"camera (?:tracks?|trucks?) (?:right|left)", "camera slowly pushes forward"),
        (r"camera pans? left[- ]to[- ]right", "camera slowly tilts down"),
        (r"camera pans? right[- ]to[- ]left", "camera slowly tilts up"),
        (r"moving horizontally (?:to the right|to the left|from left to right|from right to left)", "moving vertically downward"),
        (r"panning (?:from )?(?:left|right) to (?:right|left)", "tilting downward"),
        (r"(?:steady |smooth |slow )?(?:right|left)[- ]to[- ]right (?:pan|sweep|scroll|tracking)", "a slow vertical tilt"),
    ]
    pan_hits = 0
    for pat, rep in PAN_REWRITES:
        new = re.sub(pat, rep, p, flags=re.I)
        if new != p:
            pan_hits += 1
            p = new
    if pan_hits:
        issues.append(f"横移运镜已消毒为纵向/推进（{pan_hits} 处）")
    return p, issues


_VERT_SENT = ("This is a standard vertical 9:16 portrait video. "
              "The camera is held upright and locked off, with the sky at the very top of the frame, "
              "the ground at the very bottom, the sky-ground boundary perfectly level, and gravity pointing straight down. "
              "The composition is a vertical portrait: tall structures rise upward through the frame, "
              "and the scene recedes from the foreground at the bottom toward a vanishing point at the top.")


def _imd_ok(prompt, must_contain=None, video_seconds=None):
    """内容级校验：imd 不能是空壳/占位符/过短/指令模板复制/风格样板顶替场景正文。

    2026-09-05 验收教训：约束前缀 500+ 字会撑过总长校验，shot06（正文=风格样板）与
    shot16（[Shot] 正文为空）漏网 → H3 拿不到具体场景画出 90° 横躺画面。
    现在剥掉约束样板后单独校验 [Shot] 场景正文长度与原创性。
    2026-09-07 测试新增两道门：[Shot N] 编号唯一递增、时间戳不得超出视频时长。"""
    m = re.search(r"integrated_multimodal_description:\s*(.+)", prompt, re.S)
    if not m:
        return False, "缺 imd 内容"
    desc = m.group(1).strip()
    # 占位符（... 或 极短）
    if re.fullmatch(r"\.{3,}", desc.strip()):
        return False, "imd 是占位符(...)"
    # 过短（正常 H3 画面描述应 ≥200 字英文；中文天宫段通常 3000+）
    if len(desc) < 200:
        return False, f"imd 过短({len(desc)}字)"
    # 场景正文 = 从 [Shot 标记 到 overall_soundscape 之前（剥掉约束前缀，只数正文）
    mi = re.search(r"\[Shot\b", desc)
    ms = re.search(r"overall_soundscape:", desc)
    body = desc[mi.start():ms.start()] if (mi and ms and mi.start() < ms.start()) else desc
    # 精确移除已知约束句（不按行/不按通配——防误伤单行布局的正文）
    body_clean = body.replace(_VERT_SENT, "")
    body_clean = body_clean.replace(schema.PORTRAIT_CONSTRAINT, "")
    body_clean = body_clean.strip()
    if len(body_clean) < 300:
        return False, f"场景正文过短({len(body_clean)}字，剥约束后)——疑似风格样板/空壳顶替"
    # 整段照抄风格声明（styles/<名>.md 原文）= 没有本段场景 → 打回
    if "figure height 1/15 to 1/20 of frame" in body_clean and len(body_clean) < 1200:
        return False, "场景正文是风格声明照抄（未写本段具体场景）"
    # 指令模板复制（LLM 把 SYSTEM 示例说明当输出复制）——识别 system prompt 说明句特征
    TEMPLATE_HINTS = [
        "at least 300 words in english",
        "never use ellipsis or placeholders",
        "a detailed cinematic description of the scene, subject, camera movement",
        "a detailed english description of the ambient sound",
        "this is a standard vertical 9:16 portrait video",
    ]
    # 若 desc 主体（去掉首行竖屏句后）是指令模板说明 → 不合格
    desc_lower = desc.lower()
    # 去掉竖屏句（开头约束句）
    desc_body = re.sub(r"this is a standard vertical 9:16 portrait video[^\n]*", "", desc_lower, flags=re.I)
    # 统计模板特征命中数：≥2 个说明这是复制指令而非真实描述
    hits = [h for h in TEMPLATE_HINTS if h in desc_body]
    if len(hits) >= 2:
        return False, f"imd 是指令模板复制({hits})"
    # [Shot N] 编号唯一且递增（官方语法）。0907 测试实锤：qwen 输出 shot04/12/19
    # 出现两个 [Shot 1]（分段错乱），旧校验不查编号 → 带病过门
    nums = [int(n) for n in re.findall(r"\[Shot (\d+)\]", prompt)]
    if len(nums) != len(set(nums)):
        return False, f"[Shot] 编号重复({nums}，须递增重编号)"
    if nums != sorted(nums):
        return False, f"[Shot] 编号非递增({nums})"
    # 时间戳不得超出视频时长：H3 按时间轴排布动作节拍，超时戳=结尾动作落在视频外被截断
    # （0907 实锤 shot17：prompt 写到 00:10 但 video_seconds=6.98）
    if video_seconds:
        over = [f"{float(s[:2])*60+float(s[3:]):.1f}s"
                for s in re.findall(r"(?:At|By) (\d\d:\d\d\.\d\d\d)", prompt)
                if float(s[:2]) * 60 + float(s[3:]) > video_seconds + 0.5]
        if over:
            return False, f"时间戳超视频时长({','.join(over)} > {video_seconds}s)，按 {video_seconds}s 内重排节拍"
    return True, ""


def _design_keywords(design_text):
    """从导演设计【主体】+【场景】提 2 字碎片（去标点/常用套话），供 _imd_ok 场景正文命中校验。"""
    text = ""
    for key in ("主体", "场景"):
        m = re.search(rf"【{key}】\s*([^【]+)", design_text or "")
        if m:
            text += m.group(1)
    text = re.sub(r"[，。：；、（）()：:—\-\s\d%s+/a-zA-Z]" % "\"'", "", text)
    stopwords = {"画面","中央","整体","远处","前景","背景","上方","下方","两侧","周围","唯一","以及","其中","如同","质感","冷青","低饱"}
    grams = set()
    for i in range(len(text) - 1):
        g = text[i:i + 2]
        if g not in stopwords:
            grams.add(g)
    return sorted(grams)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("design_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=9999)
    ap.add_argument("--pov-ratio", type=float, default=0.2)
    ap.add_argument("--durations", default=None, help="摄影师增强清单 json（时长+8字段全量注入）")
    ap.add_argument("--overall", default=None, help="03_导演设计 目录或 md 文件（全篇一致性上下文）")
    ap.add_argument("--handoff", default=None, help="03_导演设计/交接卡 目录（S4：本镜交接卡，含风格锁/竖屏/衔接，优先于整体方案全文）")
    ap.add_argument("--art", default=None, help="05_美术画质/画质设计.json（阶段5 美术层：每段 光质/材质/景深/负面词，融入 prompt）")
    ap.add_argument("--style", default=None, help="风格名（styles/<名>.md，注入 system prompt）")
    ap.add_argument("--model", default="strata", help="LLM model name")
    ap.add_argument("--concurrency", type=int, default=4, help="S5：逐镜并行度（默认4；本地服务排队串行，安全值）")
    ap.add_argument("--force", action="store_true",
                    help="忽略已有合格产物全量重生成（默认增量：已有产物且通过离线门校验的段直接跳过）")
    ap.add_argument("--reference", action="append", default=None,
                    help="参考资产，格式 类型:位置:路径，可重复。类型=image/video/audio/subject；"
                         "位置=first/last/style/character。自动切换官方模式（无→T2VA，首帧→I2VA，尾帧→L2VA，首尾→FL2VA，多资产→Ref2VA）")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    style_block = load_style(args.style)
    if args.style:
        print(f"[风格] 已注入: {args.style}（{len(style_block)} 字）")
    pov_interval = max(1, round(1 / args.pov_ratio)) if args.pov_ratio > 0 else 0

    durs = {}
    if args.durations:
        shots = json.load(open(args.durations, encoding="utf-8"))
        durs = {s["id"]: s.get("duration_est", s.get("est_seconds", 12)) for s in shots}

    # 摄影师 8 字段全量加载（id → 字段dict），逐镜注入 user 消息
    photo = {}
    if args.durations:
        for s in json.load(open(args.durations, encoding="utf-8")):
            if s.get("shot_type"):
                photo[s["id"]] = s

    # 整体视觉方案（全篇一致性上下文）
    overall_ctx = ""
    if args.overall:
        if os.path.isdir(args.overall):
            for cand in ("00_整体视觉方案.md", "00_交接摘要.md"):
                fp = os.path.join(args.overall, cand)
                if os.path.exists(fp):
                    overall_ctx = open(fp, encoding="utf-8").read()
                    break
        elif os.path.exists(args.overall):
            overall_ctx = open(args.overall, encoding="utf-8").read()

    # S4：交接卡目录（本镜交接卡，含风格锁/竖屏/衔接，优先于整体方案全文）
    handoff_dir = args.handoff if args.handoff and os.path.isdir(args.handoff) else None
    if handoff_dir:
        print(f"[交接卡] 使用交接卡目录: {handoff_dir}（替代整体方案全文，token -60%）")

    # S5b：美术画质设计（阶段5）——05_美术画质/画质设计.json
    art_map = {}
    if args.art and os.path.exists(args.art):
        for s in json.load(open(args.art, encoding="utf-8")):
            art_map[s.get("id")] = s
        print(f"[美术层] 已载入画质设计 {len(art_map)} 段 → 融入 H3 prompt")

    # S6b：参考资产 → 官方模式自动判断（无→T2VA，首帧→I2VA，尾帧→L2VA，首尾→FL2VA，多资产→Ref2VA）
    refs = []
    if args.reference:
        for r in args.reference:
            parts = r.split(":", 2)
            if len(parts) == 3:
                refs.append({"type": parts[0], "pos": parts[1], "path": parts[2]})
    h3_mode, ref_desc = infer_h3_mode(refs)
    if h3_mode != "t2v":
        print(f"[参考模式] 切换到官方 {h3_mode.upper()} 写法（{ref_desc}）")
    else:
        print("[参考模式] 无参考资产 → T2VA（默认）")

    # S5：逐镜并行（ThreadPoolExecutor + Semaphore 并发限流）
    # 每镜独立（无跨镜状态，源码实证），并发不改变每镜 prompt 内容
    def process_shot(n, style_block, overall_ctx, photo, durs, handoff_dir,
                     pov_interval, args, h3_mode, refs, art_map):
        sid = f"shot{n:02d}"
        # 导演设计：shotXX.json（S2 新标准）优先，shotXX.md（旧）fallback
        j_fp = os.path.join(args.design_dir, f"{sid}.json")
        md_fp = os.path.join(args.design_dir, f"{sid}.md")
        move_en = ""
        if os.path.exists(j_fp):
            try:
                d = json.load(open(j_fp, encoding="utf-8"))
                design = "\n".join(f"【{k}】{v}" for k, v in d.items() if k != "id")
                # 运镜枚举 → 安全英文映射（2026-09-07 结构化）：「横移」设计概念在渲染层
                # 一律落地为画面内横向展开+相机纵向安全运镜（旋转防疫 0905 定案）
                mv = d.get("运镜", "")
                move_en = {
                    "推近": "slow push-in",
                    "拉远": "slow pull-out",
                    "环绕": "slow orbit",
                    "静止": "static locked shot",
                    "横移": "slow push-in (the composition itself unfolds left-to-right within the frame)",
                    "升降": "slow vertical tilt",
                }.get(mv, "")
            except Exception:
                design = open(md_fp, encoding="utf-8").read() if os.path.exists(md_fp) else ""
        elif os.path.exists(md_fp):
            design = open(md_fp, encoding="utf-8").read()
        else:
            return f"[MISS] {sid} 导演设计不存在"
        is_pov = (pov_interval > 0 and (n - 1) % pov_interval == 0)

        # ===== 汇聚：交接卡 + 导演设计 + 摄影师镜头语言 → 一条 user 消息 =====
        parts = []
        hc = {}  # 交接卡内容（无交接卡时为空，供构图竖直化扫描）
        if handoff_dir:
            hf = os.path.join(handoff_dir, f"{sid}.json")
            if os.path.exists(hf):
                hc = json.load(open(hf, encoding="utf-8"))
                parts.append(f"【镜头交接卡（本镜：情绪/镜头/信息点/衔接/风格锁/竖屏，必须遵守）】\n"
                             f"一句话摘要: {hc.get('一句话摘要','')}\n"
                             f"情绪节奏: {hc.get('情绪节奏','')} | 信息点: {hc.get('信息点','')}\n"
                             f"镜头决策: {hc.get('镜头决策','')}\n"
                             f"衔接: 入={hc.get('衔接_in','')} 出={hc.get('衔接_out','')}\n"
                             f"风格锁: {hc.get('风格锁','')}\n"
                             f"竖屏约束: {hc.get('竖屏约束','')}")
            else:
                parts.append(f"【全篇整体视觉方案（保持全片一致）】\n{overall_ctx}")
        elif overall_ctx:
            parts.append(f"【全篇整体视觉方案（保持全片一致，风格/母题/色彩以此为准）】\n{overall_ctx}")
        if sid in photo:
            ph = photo[sid]
            parts.append(
                f"【摄影师镜头设计（镜头语言以此为准）】\n"
                f"景别/运镜: {ph.get('shot_type','')} | 节奏: {ph.get('节奏意图','')} | "
                f"Hook: {ph.get('hook_type','')} | 信息点: {ph.get('info_point','')}\n"
                f"锚点: {json.dumps(ph.get('reference_anchors', {}), ensure_ascii=False)}\n"
                f"秒级指令: {json.dumps(ph.get('per_second_directives', []), ensure_ascii=False)}\n"
                f"交接: 入={ph.get('continuity_handoff',{}).get('in','')} 出={ph.get('continuity_handoff',{}).get('out','')}"
            )
        parts.append(f"【导演逐段设计（内容与情绪以此为准）】\n{design}")
        # S5b：美术画质设计（阶段5）——光质/材质/景深/负面词，融入画面描述
        if sid in art_map:
            a = art_map[sid]
            parts.append(f"【美术画质设计（质感以此为准，必须融入 integrated_multimodal_description）】\n"
                         f"光质: {a.get('光质','')}\n材质: {a.get('材质','')}\n景深: {a.get('景深','')}\n"
                         f"负面词: {a.get('负面词','')}\n风格锚定: {a.get('风格锚定','')}")
        # S6b：官方模式前缀（I2VA/FL2VA/L2VA 的对齐指令；T2VA/Ref2VA 无前缀）
        mode_prefix = ""
        if h3_mode == "i2va":
            mode_prefix = ("本段使用官方 I2VA 写法：输出开头必须有\n"
                           "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n"
                           "然后以 <Picture 1> 首帧为起点展开（保留其构图/主体/场景锚点），再描述后续动作。\n\n")
        elif h3_mode == "fl2va":
            mode_prefix = ("本段使用官方 FL2VA 写法：输出开头必须有\n"
                           "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark; Picture 2 (from Shot N) aligns with the {dur}-second mark.\n"
                           "然后描述首帧到尾帧的连续路径（单镜为主，少切镜）。\n\n")
        elif h3_mode == "l2va":
            mode_prefix = ("本段使用官方 L2VA 写法：输出开头必须有\n"
                           "How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the {dur}-second mark.\n"
                           "然后从合理的前置状态出发，逐步收敛到尾帧参考。\n\n")
        elif h3_mode == "ref2va":
            mode_prefix = ("本段使用官方 Ref2VA 六段式：subject_definitions / summary / retention_analysis / "
                           "detailed_description / overall_soundscape / non_diegetic_music，参考标签 <Subject N>/<Picture N>/<Video N>/<Audio N> 跨段一致。\n\n")
        # 高危构图词输入侧竖直化（2026-09-08 根治②）：俯瞰/环形/横排等词是翻转的
        # 场景级根因，输出侧告警只留痕不改写——在转译时直接要求改写为竖直纵深
        _risk_hit = [t for t in PORTRAIT_RISK_TERMS
                     if t in (design + json.dumps(hc, ensure_ascii=False) if hc else design)]
        scene_hint = ("\n\n【构图竖直化（本段含高危构图词：" + "、".join(_risk_hit) + "）】"
                      "转译时必须把这些横构图语义改写为竖直纵深构图：地面/通道从前景延伸到"
                      "画面顶部灭点，竖直结构沿垂直方向堆叠，原场景语义与信息点保留。"
                      if _risk_hit else "")
        move_hint = (f"\n\n【本段运镜类别（导演分配）】{move_en}。主运镜句必须采用此英文短语（可加幅度/速度副词），"
                    "不得替换为其他运镜类型。"
                    if move_en else "") + scene_hint
        user = mode_prefix + "\n\n".join(parts) + move_hint + ("\n\n请综合以上全部信息转成 H3 三字段 prompt："
                                     "镜头/景别/运镜与内容/情绪以导演设计为准（导演的【镜头】是唯一镜头权威），"
                                     "秒级动作节奏、光线基线、空间锚点、段间交接以摄影师的秒级指令/锚点/交接为准，"
                                     "风格/母题/色彩以整体方案为准。"
                                     if (overall_ctx or sid in photo) else
                                     "\n\n请转成 H3 三字段 prompt。")

        # 内部重试：LLM 可能输出空壳/占位符（...），_imd_ok 拦截后重新调用（最多 3 次）
        obj = None
        last_why = ""
        retry_user = user
        sanitized = False
        for _try in range(3):
            try:
                obj = llm_json.llm_call_json(
                    fill_style(SYSTEM, style_block), retry_user, schema.H3_SCHEMA,
                    model=args.model, budget=4000, verbose=False)
            except ValueError as e:
                return f"[FAIL] {sid}: {e}"
            # dict → 文本三字段（enforce_constraints 操作文本格式，产物保持原有格式）
            prompt = (f"integrated_multimodal_description:\n{obj.get('integrated_multimodal_description','')}\n\n"
                      f"overall_soundscape:\n{obj.get('overall_soundscape','')}\n\n"
                      f"non_diegetic_music: {obj.get('non_diegetic_music','N/A')}")
            prompt, issues = enforce_constraints(prompt, is_pov)
            ok, why = _imd_ok(prompt, video_seconds=durs.get(sid))
            # 横向行进运镜门（90° 旋转高危，2026-09-05 三跑单数据定案）：与 IMD 门同级触发重试
            if ok and schema.lateral_camera_sentences(prompt):
                ok = False
                why = "横向行进运镜（trucks/tracks laterally 等）——90°旋转高危"
            # 运镜句缺失门（2026-09-05 shot15 实证新失败模式）：导演【镜头】有运镜意图，
            # 但 prompt 里一句相机运动都没有 → 横移门/消毒全无从触发，且横构图静景仍会旋转
            dm = re.search(r"【镜头】\s*([^【]+)", design or "")
            if ok and dm and not re.search(r"静止|固定镜头|固定机位", dm.group(1)) \
                    and not _prompt_has_camera_motion(prompt):
                ok = False
                why = "运镜句缺失（导演【镜头】设计未转译进 prompt）"
            if ok:
                break
            # 失败原因喂回下次重试；2026-09-07 熔断换轨：连续不合格时逐级降约束
            # （实测连败退化 96字→8字：同一反例清单反复施压会越喂越崩）
            last_why = why
            if _try == 0:
                retry_user = (user + f"\n\n⚠️你上次的输出不合格：{why}。"
                              "重写要求：integrated_multimodal_description 必须用 ≥300 字符描写"
                              "本段【具体场景/主体/动作】（不得照抄风格声明原文），"
                              "镜头运动只允许纵向（tilt up/down）、缓慢前推或静止，"
                              "严禁任何横向行进（truck/track laterally/pan sideways/平行扫掠），"
                              "且必须写出至少一句相机运动描述（The camera ...）如实转译导演【镜头】的运镜意图。")
            elif _try == 1:
                retry_user = (user + f"\n\n⚠️上一次输出不合格：{why}。忽略之前的所有禁令，"
                              "只按以下正向要求重写一次："
                              "\n1. 直接输出三字段 JSON，不要任何解释；"
                              "\n2. integrated_multimodal_description 用 350-500 个英文单词，按顺序写："
                              "竖屏约束句 → [Shot 1] 场景与主体 → 相机运动"
                              f"（{move_en or 'a slow push-in'}）→ 按时间轴展开的动作 → 结尾旁白句；"
                              "\n3. 不要省略号、不要占位符、不要复述约束。")
            else:
                retry_user = (user + f"\n\n⚠️连续两次不合格：{why}。严格按此骨架填充"
                              "（尖括号替换为真实描述，不要输出尖括号本身）："
                              '\n{"integrated_multimodal_description": "[Shot 1] Cinematic Chinese celestial palace… '
                              "<场景与主体约120词> The camera <运镜句> <按时间轴的动作约150词> The visuals play as "
                              'off-screen voiceover narration while lips remain completely closed.", '
                              '"overall_soundscape": "<环境音约60词>", "non_diegetic_music": "N/A"}')
            # 失败预览默认常开（0907 教训：连败重试退化 96字→8字，无预览不可考）
            preview = re.sub(r"\s+", " ", str(obj.get("integrated_multimodal_description", "")))[:160]
            print(f"  [IMD] {sid} 第{_try+1}次不合格({why})｜输出: {preview}")
            
        else:
            # 3 次仍带横移 → 句级消毒兜底（只动运镜句，场景/光线/声景保留），不再整段报废
            prompt, nfix = schema.sanitize_camera_text(prompt)
            if not nfix:
                return f"[FAIL] {sid}: imd 3次不合格 ({last_why})"
            sanitized = True
            issues.append(f"运镜消毒({nfix}句)")
        rec = {
            "id": sid, "prompt": prompt,
            "video_seconds": durs.get(sid, 12),
            "mode": h3_mode if h3_mode != "t2v" else ("pov" if is_pov else "t2v"),
            "reference": refs if h3_mode != "t2v" else None,
            "scene_source": "director_llm",
            "camera_sanitized": sanitized,
            # 横构图场景风险词命中（s9 据此强制首/中/尾多帧 vision 复核 + 旋转数据集特征）
            "portrait_risk": any("竖屏风险词命中" in i for i in issues),
        }
        json.dump(rec, open(os.path.join(args.out_dir, f"{sid}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        status = "OK" + (f"({len(issues)}修正)" if issues else "")
        return f"[{status}] {sid} [{rec['mode']}] {len(prompt)}字"

    # 工作清单 = 设计目录实际存在的镜头（杜绝 probe-to-9999 的 [MISS] 噪音）；
    # --start/--end 变为可选数字过滤（默认覆盖全部）
    id_set = set()
    for fp in (glob.glob(os.path.join(args.design_dir, "shot*.json"))
               + glob.glob(os.path.join(args.design_dir, "shot*.md"))):
        m = re.match(r"shot(\d+)", os.path.basename(fp))
        if m:
            id_set.add(int(m.group(1)))
    ids = sorted(n for n in id_set if n >= args.start and n <= args.end)
    if not ids:
        print(f"设计目录无镜头可处理: {args.design_dir}")
        sys.exit(1)

    # 增量模式（默认）：已有产物且离线全门通过 → 跳过，失败段单补。
    # 0906 教训：重跑全量 20 段把已合格的 18 段重掷骰子，shot12/16 采样翻车连败——
    # 好段不该陪跑（SKILL.md「H3 段级产物存在即跳过」即此语义）。--force 才全量重生成。
    if not args.force:
        redo = []
        for n in ids:
            sid = f"shot{n:02d}"
            fp = os.path.join(args.out_dir, f"{sid}.json")
            if not os.path.exists(fp):
                redo.append(n)
                continue
            try:
                p = json.load(open(fp, encoding="utf-8")).get("prompt", "")
                ok, why = _imd_ok(p, video_seconds=durs.get(f"shot{n:02d}"))
                if ok and not schema.lateral_camera_sentences(p):
                    print(f"[SKIP] {sid} 已有合格产物")
                    continue
                print(f"[REDO] {sid} 现有产物不合格({why or '横移运镜'}) → 重生成")
            except Exception as e:
                print(f"[REDO] {sid} 现有产物不可读({e}) → 重生成")
            redo.append(n)
        ids = redo
        if not ids:
            print("全部段落已有合格产物，无需重生成（--force 强制重跑）")
            return

    def run_pass(todo, style_block, overall_ctx, photo, durs, handoff_dir,
                 pov_interval, args, h3_mode, refs, art_map):
        """跑一轮（并发限流），返回 {n: 结果串}。"""
        res = {}
        sem = threading.Semaphore(max(1, args.concurrency))

        def _run(n, *a):
            with sem:
                return process_shot(n, *a)

        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
            futs = {ex.submit(_run, n, style_block, overall_ctx, photo, durs,
                              handoff_dir, pov_interval, args, h3_mode, refs, art_map): n
                    for n in todo}
            for fut in as_completed(futs):
                r = fut.result()
                print(r)
                res[futs[fut]] = r
        return res

    pargs = (style_block, overall_ctx, photo, durs, handoff_dir, pov_interval,
             args, h3_mode, refs, art_map)
    res = run_pass(ids, *pargs)
    # 自愈：首轮失败段立即原样补跑一轮（LLM 采样失败多为瞬时，第二轮通过率高）
    failed = {n: r for n, r in res.items() if r.startswith(("[MISS]", "[FAIL]"))}
    if failed:
        print(f"\n[自愈] 首轮失败 {len(failed)} 段，立即补跑: {sorted(failed)}")
        res2 = run_pass(sorted(failed), *pargs)
        for n, r in res2.items():
            res[n] = r
        failed = {n: r for n, r in res.items() if r.startswith(("[MISS]", "[FAIL]"))}

    done = len(ids) - len(failed)
    # 失败段移除残留旧产物（若本轮之前有）：对账门按文件存在性核对，
    # 占位旧文件会把「缺段」伪装成 20/20 缺 []，误导排障方向（0906 实证）
    for n in failed:
        stale = os.path.join(args.out_dir, f"shot{n:02d}.json")
        if os.path.exists(stale):
            os.remove(stale)
    print(f"\n完成: {done}/{len(ids)} 段 → {args.out_dir}")
    print(f"RESULT total={len(ids)} ok={done} fail={sorted(failed)}")
    if failed:
        sys.exit(1)



if __name__ == "__main__":
    main()
