#!/usr/bin/env python3
"""schema.py — 流水线字段契约（单一事实源）

所有脚本 import 本文件，禁止各自硬编码枚举/约束。
改契约只改这里，下游自动同步（graphify 确认 5 脚本 import pipeline_contract，本 schema 被它们共用）。

版本历史：
  2.0  (2026-09-01) 结构化重构：导演/摄影师输出 JSON；导演做决策、摄影师做执行；
       节奏/景别/Hook 枚举唯一源；竖屏约束唯一源。
"""

# ===================== 枚举（唯一源） =====================

# 契约版本（stamp 记录，防新旧产物混用；改 schema 结构时递增）
SCHEMA_VERSION = "2.1"

# 中文口播语速（字/秒）——E2 配方实测值（2026-09-02：578字/90.7s）。预估仅供分段初算，
# H3 时长以 TTS 实测回填为准（sync_tts_durations.py）。单一事实源，各脚本引用勿硬编码。
CHAR_PER_SEC = 6.4

# 节奏意图 6 词（导演【情绪基调】首词必须命中）
RHYTHM_WORDS = ["铺垫", "建立", "预备", "冲击", "制动", "稳定"]

# 景别/镜头类型（导演【镜头】权威；摄影师不重定）。大全景/大远景=标准电影景别，归入远景族；
# 中近景/近景/中全景=标准梯子中间档（2026-09-06 根治：枚举缺中近景 → s4⑥连败叫人，
# 而 SHOT_SAME_LEVEL 放行表早已引用这三个词——枚举表与放行表自相矛盾）
SHOT_TYPES = ["远景", "全景", "中景", "特写", "运镜", "过肩", "大全景", "大远景",
              "中近景", "近景", "中全景"]

# 运镜受控枚举（2026-09-07 结构化）：设计层横移走配额分配（每 4 段 1 段，25%<1/3
# 警戒线），渲染层横移句仍消毒为纵向/推进（旋转防疫不变）。
SHOT_MOVE_TYPES = ["推近", "拉远", "环绕", "静止", "横移", "升降"]

# 景别梯（2026-09-22，ADR 0003 枚举级纠偏的「尺度距离」唯一源）：广→紧排列。
# SHOT_SAME_LEVEL 放行对均为相邻梯位；运镜/过肩非尺度档不上梯，纠偏新值只从梯上取。
# 改梯只改这里，纠偏与自检自动同步。
SHOT_SCALE_LADDER = ["大远景", "远景", "大全景", "全景", "中全景", "中景", "中近景", "近景", "特写"]

# 景别同级放行（中近景↔中景等微调不算重复；完全相同必须报，2026-09-06 根治：
# 枚举缺中近景曾致 s4⑥连败——放行表与枚举表同源后不再自相矛盾）
SHOT_SAME_LEVEL = {
    ("中景", "中近景"), ("中近景", "中景"),
    ("特写", "近景"), ("近景", "特写"),
    ("全景", "中全景"), ("中全景", "全景"),
}


def shot_first_level(txt):
    """【镜头】字段首景别提取（全链路唯一实现：director_llm 归一/交替校验、enhancer ⑥⑦ 同用）。
    2026-09-06 根治：三处各写各的正则导致 shot17「全景，」vs「全景 +」判定不一致 → ⑦漏报。
    返回 '' 表示无有效首词。"""
    parts = [p.strip() for p in _re.split(r"[→➔>·/，,、＋+]", (txt or "").strip()) if p.strip()]
    if not parts:
        return ""
    first = parts[0]
    # 「大远景全景」「远景俯拍」等复合首词：取命中的最长枚举作标准景别（无命中则原样返回）
    cands = [t for t in SHOT_TYPES if first.startswith(t)]
    if cands:
        return max(cands, key=len)
    # 兜底（2026-09-07）：「俯瞰全景」「超广远景俯瞰」等 修饰语+景别 的采样变体——
    # 首词不含分隔符时 startswith 落空。含枚举词即按最长归类（只收敛到标准枚举，
    # ⑥⑦ 判定不受采样措辞影响）；确实无枚举词才原样返回（保持 FAIL 可见）。
    cands = [t for t in SHOT_TYPES if t in first]
    return max(cands, key=len) if cands else first

# Hook 类型（导演【情绪基调】决定；摄影师不重定）
HOOK_TYPES = ["visual-joke", "reversal", "suspense", "reveal", "callback", "tender"]

# 竖屏约束（3 处引用统一：director_to_h3 / check_portrait / prompt 注入）
PORTRAIT_CONSTRAINT = (
    "9:16 vertical composition, sky at the very top, ground at the very bottom, "
    "not rotated, upright camera, no ring-shaped / circular / overhead / "
    "bird's-eye / panoramic views"
)

# H3 prompt 硬约束（enforce_constraints 注入，不靠 LLM）
H3_HARD_CONSTRAINTS = (
    "The visuals play as off-screen voiceover narration while lips remain "
    "completely closed. No on-screen text, no subtitles, no captions."
)

# ===================== 输出 Schema（JSON 强校验） =====================

# 导演逐段设计（阶段3）——决策层
DIRECTOR_SHOT_SCHEMA = {
    "type": "object",
    "required": ["id", "情绪基调", "场景", "主体", "景别", "运镜", "镜头", "动作时间轴", "风格渗透", "信息点"],
    "properties": {
        "id": {"type": "string"},
        # 首词必须命中 RHYTHM_WORDS（程序校验，非仅 prompt 约束）
        "情绪基调": {"type": "string", "rhythm_first_word": True},
        "场景": {"type": "string"},
        "主体": {"type": "string"},
        # 2026-09-07 结构化：景别/运镜从自由文本拆出为枚举（根治采样变体误杀 +
        # 横移配额在生成端分配；【镜头】文本保留为可读描述，首词=景别）
        "景别": {"type": "string", "enum": SHOT_TYPES},
        "运镜": {"type": "string", "enum": SHOT_MOVE_TYPES},
        # 导演唯一镜头权威（景别 + 运镜类别 + 描述）
        "镜头": {"type": "string"},
        "动作时间轴": {"type": "string"},
        "风格渗透": {"type": "string"},
        # 本段要传达的核心信息（给下游/交接卡/校验用，导演决策）
        "信息点": {"type": "string"},
    },
    "additionalProperties": True,  # 容忍 LLM 多给字段，不拒绝
}

# 摄影师输出（阶段4）——执行层（不含决策字段：shot_type/节奏意图/hook_type/info_point 归导演）
PHOTOGRAPHER_SCHEMA = {
    "type": "object",
    "required": ["id", "duration_est", "per_second_directives", "reference_anchors",
                 "continuity_handoff"],
    "properties": {
        "id": {"type": "string"},
        "duration_est": {"type": "number"},
        "per_second_directives": {"type": "array"},     # 秒级指令（执行）
        "reference_anchors": {"type": "object"},        # 光线/空间锚点（执行）
        "continuity_handoff": {"type": "object"},       # 段间衔接（执行）
    },
    "additionalProperties": True,
}

# 交接卡（阶段间传递）——程序生成，非 LLM 自由写
HANDOFF_SCHEMA = {
    "type": "object",
    "required": ["id", "一句话摘要", "情绪节奏", "信息点", "镜头决策", "视觉重点",
                 "衔接_in", "衔接_out", "风格锁", "竖屏约束"],
    "properties": {
        "id": {"type": "string"},
        "一句话摘要": {"type": "string"},
        "情绪节奏": {"type": "string"},
        "信息点": {"type": "string"},
        "镜头决策": {"type": "string"},
        "视觉重点": {"type": "string"},
        "衔接_in": {"type": "string"},
        "衔接_out": {"type": "string"},
        "风格锁": {"type": "string"},
        "竖屏约束": {"type": "string"},
    },
}

# H3 输出（阶段6）——三字段
H3_SCHEMA = {
    "type": "object",
    "required": ["integrated_multimodal_description", "overall_soundscape",
                 "non_diegetic_music"],
    "properties": {
        "integrated_multimodal_description": {"type": "string"},
        "overall_soundscape": {"type": "string"},
        "non_diegetic_music": {"type": "string"},
    },
    "additionalProperties": True,
}

# 美术画质设计（阶段5，新增）——每段画质词，供阶段6 翻译官融入 H3 prompt
ART_SCHEMA = {
    "type": "object",
    "required": ["id", "光质", "材质", "景深", "负面词", "风格锚定"],
    "properties": {
        "id": {"type": "string"},
        "光质": {"type": "string", "min_length": 4},   # 光质锚定词（如 丁达尔圣光散射/冷蓝月白主光）
        "材质": {"type": "string", "min_length": 4},   # 材质词（如 青石玉润/纸纤维粗糙）
        "景深": {"type": "string", "min_length": 4},   # 景深词（如 浅景深/前景虚化）
        "负面词": {"type": "string"},                   # 负面词基线（blurry/lowres/watermark/畸形）
        "风格锚定": {"type": "string"},                 # 尊重哪个风格块的负向约束（勿混）
    },
    "additionalProperties": True,
}

# 创意方向卡（阶段2 前置）——结构化产出，机器读取不再解析 markdown 标题（2026-09-05 决议）
DIRECTIONS_SCHEMA = {
    "type": "object",
    "required": ["directions"],
    "properties": {
        "directions": {
            "type": "array",
            "min_items": 2,
            "items": {
                "type": "object",
                "required": ["方向名", "视觉主线", "美术风格", "最适合表达", "局限"],
                "properties": {
                    "方向名": {"type": "string"},
                    "视觉主线": {"type": "string"},
                    "美术风格": {"type": "string"},
                    "最适合表达": {"type": "string"},
                    "局限": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

# 整体视觉方案（阶段2 A2）——JSON 契约（2026-09-07）：low 组合把思维链写进 content，
# 自由文本通道整体方案/交接摘要两度带推理泄漏落盘；改走 llm_call_json（括号配对提取
# + schema 强校验 + 截断自适应），产物 md 由代码按字段组装。
OVERALL_PLAN_SCHEMA = {
    "type": "object",
    "required": ["视觉主线", "场景系统", "重复母题", "色彩节奏", "节奏曲线", "段间衔接"],
    "properties": {
        "视觉主线": {"type": "string"},
        "场景系统": {"type": "string"},
        "重复母题": {"type": "string"},
        "色彩节奏": {"type": "string"},
        "节奏曲线": {"type": "string"},
        "段间衔接": {"type": "string"},
    },
    "additionalProperties": True,
}

# 交接摘要（s5 收尾，≤15 行硬约束）——五字段 JSON 化后每字段一行天然达标
HANDOFF_SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["方向名", "视觉主线", "风格锁", "色彩基线", "禁忌"],
    "properties": {
        "方向名": {"type": "string"},
        "视觉主线": {"type": "string"},
        "风格锁": {"type": "string"},
        "色彩基线": {"type": "string"},
        "禁忌": {"type": "string"},
    },
    "additionalProperties": True,
}

# ===================== 横向运镜契约（90° 旋转防疫，2026-09-05） =====================
# 数据结论（references/rotation-analysis-20260905.md）：三次跑单 5 个实锤旋转镜头
# （0903旧 shot06/07/16、0904 shot08/19）的 prompt 全部以「横向行进类运镜」为主导指令
# （trucks/tracks laterally、truck right、tracking shot from left to right、平行扫掠）；
# 正常镜头约 1/3 含横向词但多为小幅度/从属描述。横向行进是经验证的旋转高危特征，
# 处理策略：s6 检出 → 重试时禁横移 → 仍命中则句级消毒为纵向/静止（保留场景描述丰富性）。

import re as _re

# 横向行进类运镜词（相机主动横向移动；不含被摄物的左右移动）
LATERAL_CAMERA_RE = _re.compile(
    r"truck(?:s|ed|ing)?(?:\s+\w+){0,3}?\s+(?:laterally|left|right|parallel)"
    r"|track(?:s|ed|ing)?(?:\s+\w+){0,2}?\s+laterally"
    r"|tracking shot from (?:the )?(?:left|right) to (?:the )?(?:left|right)"
    r"|tracking shot moving parallel"
    r"|lateral (?:movement|truck|camera (?:move|motion|travel)|travel|slide|drift)"
    r"|pan(?:s|ning)?\s+(?:left|right|across|horizontally)"
    r"|sweep(?:s|ing)?\s+(?:laterally|across the|from left to right)"
    r"|glid(?:e|ing)\s+(?:smoothly\s+)?(?:past|parallel|along|across)"
    r"|mov(?:e|ing|es)?\s+parallel to the"
    r"|trucks? right|trucks? left"
    r"|横移|左右平移|平行扫过",
    _re.I,
)

# 运镜句判定：命中横向词的句子里，还需含相机语境词（避免误伤被摄物移动，如“货船从左向右行驶”）
_CAMERA_CONTEXT_RE = _re.compile(
    r"\bcamera\b|\bshot\b|\bframing\b|\bcomposition\b|\bviewpoint\b|\bview\b|\blens\b|\bcam\b|运镜|镜头",
    _re.I,
)

# 消毒后的替身运镜句（竖直安全：静止 + 纵向漂移，保留画面丰富性不动场景描述）
_CAMERA_SANITIZE_REPLACEMENT = (
    "The camera stays upright and holds mostly still, with only a slow, "
    "steady vertical drift (a gentle tilt upward), keeping the vertical "
    "architecture, level horizon and gravity-anchored composition stable."
)


def lateral_camera_sentences(prompt):
    """返回 prompt 中「相机横向行进」的句子列表（空的 = 无旋转高危运镜）。"""
    if not prompt:
        return []
    hits = []
    for sent in _re.split(r"(?<=[.!?])\s+|\n", prompt):
        if LATERAL_CAMERA_RE.search(sent) and _CAMERA_CONTEXT_RE.search(sent):
            hits.append(sent.strip())
    return hits


def sanitize_camera_text(prompt):
    """句级消毒：把横向行进运镜句替换为竖直安全运镜句，其余描述原样保留。"""
    if not prompt:
        return prompt, 0
    n = 0
    out = []
    for chunk in _re.split(r"(?<=[.!?])\s+|\n", prompt):
        if LATERAL_CAMERA_RE.search(chunk) and _CAMERA_CONTEXT_RE.search(chunk):
            out.append(_CAMERA_SANITIZE_REPLACEMENT)
            n += 1
        else:
            out.append(chunk)
    return " ".join(out), n


# ---- 删除式运动词消毒（2026-09-11 用户拍板）----
# 背景：H3 对「任何相机运动语义」都可能生成倾斜/旋转画面；替换式消毒（上行）的替身句
# 仍含 vertical drift / tilt upward 运动词，且 LATERAL_CAMERA_RE 不覆盖 orbit/circle 家族。
# 策略：确认门人审判歪后，把该镜头 prompt 里所有运动语义句**整句删除**，只留静止构图。

# 运动语义词（比 LATERAL 更宽：环绕/旋转/升降/推拉/扫掠/漂移 全家族）
_MOTION_VERB_RE = _re.compile(
    r"\borbit(?:s|ed|ing|al)?\b"
    r"|\bcircle(?:s|d|ing)?\s+(?:around|round|above)"
    r"|\b(?:revolves?|revolving)\s+around"
    r"|\brotate(?:s|d|ing)?\b"
    r"|\bspirals?\b"
    r"|\b(?:pans?|panning)\b"
    r"|\btilt(?:s|ed|ing)?\s+(?:up|down|upward|downward)"
    r"|\btrucks?(?:\s|\b)"
    r"|\btracks?\s+(?:left|right|laterally|along|parallel|backward|forward)"
    r"|\bdoll(?:y|ies|ied|ying)\s*(?:in|out|back)?"
    r"|\bcranes?\s+(?:up|down)"
    r"|\bzoom(?:s|ed|ing)?\b"
    r"|\bdrift(?:s|ed|ing)?\s+(?:left|right|sideways|laterally|across|past|along|toward)"
    r"|\bglid(?:e|es|ing)\b"
    r"|\bslid(?:e|es|ing)\s+(?:past|across|through|along|by)"
    r"|\bsweep(?:s|ing)?\b"
    r"|\bswirl(?:s|ing)?\b"
    r"|\bpush(?:es)?[-\s]?(?:in|forward|closer)"
    r"|\bpull(?:s|ed)?\s+(?:back|out)"
    r"|\b(?:moves?|moving|travel(?:s|ing)?)\s+(?:toward|towards|through|across|along|around|past|deeper|forward|backward|closer)"
    r"|\bperspective shift"
    r"|\bshift(?:s|ing)?\s+perspective"
    r"|\bunfolds?\s+(?:the\s+composition\s+)?left-to-right"
    r"|\bleft-to-right\b"
    r"|\bright-to-left\b"
    r"|\b(?:rises?|rising)\s+(?:upward|above|from)"
    r"|\bdescend(?:s|ing)?\b"
    r"|\bfly(?:ing)?\s+through"
    r"|\bsoars?\b"
    r"|\bapproach(?:es|ing)?\b"
    r"|\breveal(?:s|ing)\s+(?:the|a|an)\b.*\bas the camera",
    _re.I,
)

# 静止安全句（删除运动句后在镜头描述末尾补一句，保画面锚点）
_MOTION_STRIP_REPLACEMENT = (
    "The camera remains completely stationary and locked off, "
    "holding the upright 9:16 composition perfectly level and still "
    "for the full duration of the shot."
)


# 否定式竖屏约束块（2026-09-11 实证根因：NOT rotated/Absolutely forbidden 的否定句式
# 反向诱发旋转——rotated/tilted/sideways 内容词被扩散模型强化成画面指令，shot02 三轮
# 对照实验实锤：删运动词无效、删否定块一次转正）。strip_motion_text 将其整体替换为正向表述。
_NEGATIVE_CONSTRAINT_RE = _re.compile(
    r"This is a standard vertical 9:16 portrait video\.[\s\S]*?lying horizontally\.")

# 正向竖屏约束句（替换后注入；与 director_to_h3_llm._VERT_SENT 同一文本）
# 注意：句内不得出现 horizon/rotated/tilted 等横向构图内容词（2026-09-11 实证：
# 这些词即使出现在正向句里也会被模型当画面指令）
_POSITIVE_VERTICAL_SENT = (
    "This is a standard vertical 9:16 portrait video. "
    "The camera is held upright and locked off, with the sky at the very top of the frame, "
    "the ground at the very bottom, the sky-ground boundary perfectly level, and gravity pointing straight down. "
    "The composition is a vertical portrait: tall structures rise upward through the frame, "
    "and the scene recedes from the foreground at the bottom toward a vanishing point at the top."
)


# 画框边缘相对运动（免相机语境）：前景物从画面边缘滑过 = 画框相对横向位移，
# 无需相机语境词即删（shot08 实锤："a stone dragon pillar slides past the right edge"）
_FRAME_EDGE_MOTION_RE = _re.compile(
    r"\b(?:slid|glid)(?:e|es|ing)\w*\s+past\s+(?:the\s+)?(?:right|left)\s+edge"
    r"|\b(?:slid|glid)(?:e|es|ing)\w*\s+(?:out\s+of|into)\s+(?:the\s+)?frame",
    _re.I,
)

# 通用否定式旋转句（2026-09-11 实证：LLM 重写版会自创 "The camera never rotates,
# never tilts sideways" 等否定句，正是反向诱发旋转的元凶）——凡 (never|not|no|avoid|
# forbidden|don't|without) 紧邻 rotated/tilted/sideways/horizontal 的句子一律删。
_NEGATIVE_ROTATION_SENT_RE = _re.compile(
    r"\b(?:never|not|no|avoid|forbidden|prohibit(?:ed)?|don'?t|without)\b"
    r"[\s\S]{0,40}?\b(?:rotat(?:e|es|ed|ing)|tilt(?:s|ed|ing)?|sideway(?:s)?|"
    r"horizont(?:al|ly)|lean(?:s|ing)?|diagonal(?:ly)?)\b",
    _re.I,
)

# ---- 严格模式（2026-09-11 用户追加拍板：发现歪后清理必须非常严格）----
# 横向构图场景语义（第三层根因，shot08 实证：运动词与否定块都清干净后仍翻，
# 残余诱因是场景层的横构图词：bridge/三层横列/horizon/profile/侧光横切/环形/横排）
_HORIZONTAL_COMPO_RE = _re.compile(
    r"\bhorizon\b|\bbridge\b|\bwalkway\b|\bcorridor\b"
    r"|\bprofile\b"
    r"|\bthree-tier\b|\blayers?\b|\blayering\b"
    r"|\bframing\b"
    r"|\bring\b|\bcircular\b|\barena\b|\bcolosseum\b|\bsurround(?:ing|s)?\b"
    r"|\boverhead\b|\baerial\b|bird'?s?\s+eye|\bfrom above\b"
    r"|\bpanoramic\b|\bsprawling\b|\bwide expanse\b"
    r"|\brows?\b|\branks?\b|\bformation\b|\bline of\b"
    r"|\bTyndall\b|\bcutting through\b"
    r"|\bpaths?\b",
    _re.I,
)
# 人物主体词：含主体的句子只做短语级删（保住人物），纯场景句整句删
_SUBJECT_RE = _re.compile(r"\bfigure\b|\bcharacter\b|\brobed\b|\bperson\b|\(S1\)", _re.I)

# 短语级删除清单（句内横构图短语，删短语保句子主体）
_PHRASE_STRIP_RES = [
    _re.compile(r",?\s*standing in profile on a bridge", _re.I),
    _re.compile(r",?\s*in profile\b", _re.I),
    _re.compile(r",?\s*on (?:a|the) (?:stone\s+)?bridge\b", _re.I),
    _re.compile(r",?\s*at (?:the )?horizon\b", _re.I),
    _re.compile(r",?\s*with strong side Tyndall effects cutting through the fog", _re.I),
    _re.compile(r",?\s*leading to distant islands", _re.I),
    _re.compile(r",?\s*hand resting on the (?:railing|balustrade|rail)\b", _re.I),
    _re.compile(r"howling softly across the stone bridge", _re.I),
    _re.compile(r"on the character'?s? side profile", _re.I),
    _re.compile(r",?\s*with cyan light patterns spreading in concentric rings", _re.I),
    _re.compile(r"\bspreading in concentric rings\b", _re.I),
    _re.compile(r",?\s*at least one floating structure \(sky bridge, cloud terrace, floating island\)", _re.I),
]

# 竖直纵深构图锚句（严格清理后注入：给模型显式的自下而上纵深指令）
_VERTICAL_ANCHOR_SENT = (
    "The composition is built strictly bottom-to-top: the scene rises from the bottom foreground "
    "up through stacked upright pillars and clouds toward a tall structure near the top of the frame; "
    "every structure stands vertical and the depth of the scene runs from bottom to top, never left to right."
)


# 竖直锚句「已存在」判据：按语义标志判定而非整串比对——
# 人工/LLM 竖直化重写会写出措辞不同的等价锚句（shot19 手工版：
# "The composition is built strictly bottom-to-top: the road runs from the bottom
# foreground straight up toward a distant vanishing point..."），整串比对会误判为缺失。
_VERTICAL_ANCHOR_RE = _re.compile(r"bottom-to-top", _re.I)


def _is_protected_sentence(sent):
    """保护句判定：正向约束句 / 时间轴标记句 / 旁白台词句 / 声景段不参与删除。
    2026-09-11：否定式约束块（NOT rotated 家族）不在保护名单——它本身是旋转诱因，
    由 strip_motion_text 在分句前整体替换为正向表述。"""
    s = sent.strip()
    if not s:
        return True
    if s.startswith(("This is a standard vertical", "The camera is held upright",
                     "The composition is a vertical portrait",
                     "The composition is built strictly bottom-to-top",
                     "The camera remains completely stationary",
                     "integrated_multimodal_description",
                     "overall_soundscape", "non_diegetic_music")):
        return True
    # 时间轴句（At 00:00.000, ... / [Shot N]）
    if _re.match(r"^At\s+\d{2}:\d{2}", s) or s.startswith("[Shot"):
        return True
    # 声景/音乐行（按行首标记）
    if s.lower().startswith(("the soundscape", "audio:", "music:", "narration", "voiceover", "no dialogue")):
        return True
    return False


def motion_sentences(prompt):
    """返回 prompt 中「运动语义/否定式旋转表述」的句子列表。诊断用。"""
    if not prompt:
        return []
    return [s.strip() for s in _re.split(r"(?<=[.!?])\s+|\n", prompt)
            if (_MOTION_VERB_RE.search(s) and _CAMERA_CONTEXT_RE.search(s)
                or _FRAME_EDGE_MOTION_RE.search(s)
                or _NEGATIVE_ROTATION_SENT_RE.search(s))
            and not _is_protected_sentence(s)]


# 旧版正向约束句（2026-09-11 早期注入、含 horizon 内容词的版本）→ 统一换新句
_LEGACY_POSITIVE_RE = _re.compile(
    r"This is a standard vertical 9:16 portrait video\. The camera is held upright and locked off,"
    r"[\s\S]*?vanishing point at the top\.")


def strip_motion_text(prompt, strict=False):
    """删除式消毒（2026-09-11 用户拍板 + 同日两次根因修正）：
    1) 否定式竖屏约束块（NOT rotated/Absolutely forbidden）整体替换为正向表述——
       否定句是旋转主诱因（shot02 三轮对照实锤）；
    2) 运动语义句整句删除（相机语境 或 画框边缘相对运动），删了至少一句则补静止安全句；
    3) strict=True（发现歪之后的严格清理，用户拍板「所有可能诱因都删」）：
       横向构图场景语义按 整句→子句→短语 三级删除（含人物的句子保主体删短语），
       并注入竖直纵深锚句。
    返回 (新prompt, 改动计数)。保护句（正向约束/时间轴/声景首行）绝不删。
    零改动时返回原文（不重拼，保换行等格式零扰动）。"""
    if not prompt:
        return prompt, 0
    prompt2, neg_n = _NEGATIVE_CONSTRAINT_RE.subn(_POSITIVE_VERTICAL_SENT, prompt, count=1)
    prompt2 = _LEGACY_POSITIVE_RE.sub(_POSITIVE_VERTICAL_SENT, prompt2)
    if strict:  # 短语级先跑（整括号组等），再走整句/子句级
        for rx in _PHRASE_STRIP_RES:
            prompt2 = rx.sub("", prompt2)
    out, n = [], 0
    for chunk in _re.split(r"(?<=[.!?])\s+|\n", prompt2):
        s = chunk.strip()
        # 保护句直通；两类例外不保护：
        # ① 含否定式旋转表述（反向诱发元凶，LLM 会自创进任何句子含时间轴句）
        # ② 严格模式下的 [Shot 长句（内部横构图分片需子句级清理）
        if (_is_protected_sentence(chunk)
                and not _NEGATIVE_ROTATION_SENT_RE.search(chunk)
                and not (strict and s.startswith("[Shot"))):
            out.append(chunk)
            continue
        if ((_MOTION_VERB_RE.search(chunk) and _CAMERA_CONTEXT_RE.search(chunk))
                or _FRAME_EDGE_MOTION_RE.search(chunk)
                or _NEGATIVE_ROTATION_SENT_RE.search(chunk)):
            n += 1
            continue
        if strict and _HORIZONTAL_COMPO_RE.search(chunk):
            if _SUBJECT_RE.search(chunk) or s.startswith("[Shot"):
                cleaned = _strip_horizontal_clauses(chunk)
                if cleaned:
                    out.append(cleaned)
                else:
                    n += 1
            else:
                n += 1
            continue
        out.append(chunk)
    # 2026-10-20 修：锚句注入必须幂等。原实现 strict=True 无条件追加，
    # 导致「零删除」的干净 prompt 也返回 n_del>=1 → 调用方 _strip_motion_prompt_file
    # 误判为「有诱因被删」→ 每轮重烧都重复追加同一句、prompt 越滚越长，
    # 并触发「删旧产物」路径（shot19 实锤：连跑两次锚句出现 3 次，1868→2162 字节）。
    anchor_added = 0
    if strict and not _VERTICAL_ANCHOR_RE.search(prompt2):
        out.append(_VERTICAL_ANCHOR_SENT)
        anchor_added = 1
    if n:
        out.append(_MOTION_STRIP_REPLACEMENT)
    total = neg_n + n + anchor_added
    if not total:
        return prompt, 0
    return " ".join(out), total


def _strip_horizontal_clauses(sent):
    """子句级清理：逗号分片后丢弃命中横向构图语义且不含人物主体的分片。"""
    parts = sent.split(", ")
    kept = [p for p in parts
            if not (_HORIZONTAL_COMPO_RE.search(p) and not _SUBJECT_RE.search(p))]
    return ", ".join(kept)

# ===================== 校验函数 =====================

def validate_schema(obj, schema):
    """按 schema 校验 obj，返回错误列表（空 = 通过）。"""
    if not isinstance(obj, dict):
        return [f"不是对象: {type(obj).__name__}"]
    errs = []
    for req in schema.get("required", []):
        if req not in obj or not obj[req]:
            errs.append(f"缺必填字段: {req}")
    for key, prop in schema.get("properties", {}).items():
        if key not in obj:
            continue
        val = obj[key]
        if prop.get("type") == "string" and not isinstance(val, str):
            errs.append(f"{key} 应为字符串, 实为 {type(val).__name__}")
        if prop.get("type") == "number" and not isinstance(val, (int, float)):
            errs.append(f"{key} 应为数字, 实为 {type(val).__name__}")
        # 数组：min_items + 逐项递归对象 schema（方向卡等嵌套契约）
        if prop.get("type") == "array" and isinstance(val, list):
            if "min_items" in prop and len(val) < prop["min_items"]:
                errs.append(f"{key} 至少 {prop['min_items']} 项, 实为 {len(val)}")
            item_schema = prop.get("items")
            if isinstance(item_schema, dict) and item_schema.get("type") == "object":
                for i, item in enumerate(val):
                    for e in validate_schema(item, item_schema):
                        errs.append(f"{key}[{i}] {e}")
        # 情绪基调首词必须命中节奏枚举（导演决策契约）
        if prop.get("rhythm_first_word") and isinstance(val, str):
            head = val.strip()
            if not any(head.startswith(w) for w in RHYTHM_WORDS):
                errs.append(f"情绪基调首词须为 {RHYTHM_WORDS} 之一, 实为: {head[:12]}")
    return errs


def self_test():
    """schema 自检（非正式测试，快速 sanity check）。"""
    ok_shot = {"id": "s1", "情绪基调": "建立——宏阔静谧", "场景": "x", "主体": "y",
               "镜头": "远景横移", "动作时间轴": "t", "风格渗透": "z", "信息点": "核心信息"}
    bad_shot = {"id": "s2", "情绪基调": "平缓推进", "场景": "x"}  # 缺字段 + 首词非枚举
    assert validate_schema(ok_shot, DIRECTOR_SHOT_SCHEMA) == [], "合规设计应通过"
    errs = validate_schema(bad_shot, DIRECTOR_SHOT_SCHEMA)
    assert any("情绪基调首词" in e for e in errs), "首词非枚举应报错"
    assert any("缺必填字段" in e for e in errs), "缺字段应报错"
    print("schema self_test: OK")
    print("  RHYTHM_WORDS:", RHYTHM_WORDS)
    print("  竖屏约束:", PORTRAIT_CONSTRAINT[:40] + "...")


if __name__ == "__main__":
    self_test()
