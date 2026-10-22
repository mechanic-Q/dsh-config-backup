#!/usr/bin/env python3
"""strata_h3_lane.py — 压缩车道核心脚本：s04-s07 四阶段压成对本地 Strata 实例的**单次 LLM 调用**，
直出 06_H3prompt/shotXX.json 全量产物（取代 director_llm / storyboard_photographer /
art_director / director_to_h3_llm 四阶段链，SPEC #57 / 票1 #58；#84 起 LLM 后端
KVMem → Strata，git mv 就地改造，无运行时回退——回退靠 git）。

输入 = <slug>/01_分段/分镜清单_raw.json（id/est_seconds/text + s03 回填的 duration_est）+ 风格块
输出 = <slug>/06_H3prompt/shotXX.json（rec 契约：id / prompt / video_seconds / portrait_risk
       / camera_sanitized，下游 s08-s13 无感知——submit_accel 的三字段头特征串、
       vision 判读的 portrait_risk、时长对齐的 video_seconds、s08 梯队的 camera_sanitized 全保留）

接缝设计（唯一新代码接缝，SPEC 测试决策）：
    build_request_body ──► call_strata（唯一网络 IO）──► parse_shots ──► persist_shots
    两端纯函数，网络调用夹中间；测试经 run_lane(caller=...) 注入假 LLM 响应即可全覆盖
    （call_strata 本体另有假 urlopen 桩直测，见 test_strata_h3_lane 7b），不 mock 内部链。

请求形态（#84 改造，2026-10-12 定案）：OpenAI 标准请求体（model / messages / max_tokens /
temperature，非流式——不带 stream 字段），model 取 STRATA_MODEL（默认 strata）；服务端点
STRATA_URL（缺省动态解析：WSL 内经默认路由网关连 Windows 主机，本机直连 127.0.0.1:8080）。
请求级关思维链 chat_template_kwargs.enable_thinking=false（#83 spike §4 实测唯一有效写法，
T1；/no_think 与 9router thinking 字段均无效），不携带 9router 专属 thinking 字段（风格红线）。
chat_template_kwargs 非 OpenAI 标准字段，服务端按未知字段容忍（spike T2）。服务生命周期
归 strata_window（#85 探活→拉起→失败叫人 + 显存窗口），启动命令 = STRATA_START_CMD
（spike §5 powershell 串，--host 0.0.0.0）。

用法：
    python3 strata_h3_lane.py <slug目录> --style <风格名> [--base-url http://127.0.0.1:8080]
                             [--retries 2] [--max-tokens 14000] [--timeout 900] [--pov-ratio 0.2]

失败语义：整单失败（解析失败 / 单镜缺失 / ID 错位）重试 2 次（共 3 次整单调用）；单镜缺失不补洞，
整单重调；连败硬失败退出，**绝不静默回退云端**（回退走旧车道显式开关，归 driver）。
"""
import json, os, sys, re, time, argparse, subprocess, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostpath  # 宿主判定 / 跨侧路径 / 解码的单一事实源（#143）
import schema
import llm_json
# 复用旧车道落盘收口与风格加载（enforce_constraints / load_style 均为纯函数，import 无副作用；
# spec 定案：只有导入有副作用时才抽取到 schema 层——实测无，直接复用）。
# CONSTRAINT 同源导入（enforce_constraints 缺句时注入的就是这条，禁止第三份拷贝漂移）
from director_to_h3_llm import enforce_constraints, load_style, fill_style, _VERT_SENT, CONSTRAINT

DEFAULT_MODEL = os.environ.get("STRATA_MODEL", "strata")   # import 时读定；import 后改 env 不影响
BOARD_DIRS = ("01_分段", "02_逐段视觉设计")   # 01 为现行 driver 权威；02 兼容旧布局（minimal_flow 同款搜索）
OUT_SUBDIR = "06_H3prompt"


def _in_wsl():
    """WSL 判定（#143 起委托 hostpath 单一实现；保留本名供测试与旧调用点 patch）。"""
    return hostpath.is_wsl()


def _wsl_gateway():
    """WSL→Windows 主机 IP 动态发现（默认路由网关，spike §1 定案连通方式）。
    NAT 子网会漂移，勿写死；非 WSL / 发现失败 → None。"""
    if not _in_wsl():
        return None
    try:
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001 — 路由发现失败回退 127.0.0.1（env STRATA_URL 兜底）
        return None
    parts = out.split()
    return parts[2] if len(parts) > 2 and parts[0] == "default" else None


def default_base_url():
    """STRATA_URL 缺省端点：WSL 内经网关 IP 连 Windows 主机 Strata（--host 0.0.0.0，
    spike §1 定案）；Windows 本机直连 127.0.0.1。端口 8080（Strata 默认）。"""
    host = _wsl_gateway() or "127.0.0.1"
    return f"http://{host}:8080"


BASE_URL = os.environ.get("STRATA_URL", "").rstrip("/") or default_base_url()

# SYSTEM 底稿（minimal_flow 形态）+ 四块质量补丁（SPEC 实现决策）：
# ① 人物镜头逐段判断（ADR 0002 内容驱动标准抄录）② 词数密度 250+
# ③ 风格锁自然融入 ④ 竖屏约束句/旁白句/反模板/横移 ≤1/3 配额
SYSTEM_TEMPLATE = """你是 MiniMax H3 视频生成提示词专家兼资深视觉导演。下面是一部新闻纪实竖屏短片的全部分镜段落（id | 时长秒 | POV标记 | 台词）。对**每一个段落**直接产出该镜头的 H3 三字段英文 prompt——导演设计、摄影执行、美术画质全部融入你的思考，跳过中间稿。

每段输出一个 JSON 对象（shots 数组元素，四字段）：
{"id": "shotXX",
 "integrated_multimodal_description": "<英文画面描述>",
 "overall_soundscape": "<英文环境音>",
 "non_diegetic_music": "N/A"}}

【质量补丁① 人物镜头逐段判断（内容驱动，不是配额）】每段先问：这段台词的信息点和情绪落在人身上，还是环境/数据上？
- 注意：新闻纪实≠无人物——本片是仙侠风格演绎，默认安排虚构角色见证/执行台词动作，只有确实无处安放人物的段才写 "No person appears in this shot."（全片无人物段应少于一半）。
- 落在人（个体故事/民生冷暖/情绪转折/冲突对抗）→ 虚构仙侠角色的中近景或特写，由角色执行台词中的动作（人物不说话、嘴唇闭合）；
- 落在宏观（数据/政策/产业/天文地理）→ 宏大空镜 + 虚构角色作为比例锚点（远景小人影），或由角色见证/操作画面中的器物（观测、记录、仰望）；
- 情绪爆点（冲击）→ 允许面部/手部/器物特写。
- 例（宏观天文段也用人物锚点）：台词讲脉冲星粒子尾迹 → "a robed celestial astronomer stands small on a crystal terrace gazing up as the glowing particle trail streams overhead, silhouette dwarfed by the colossal observatory"——人物见证使画面有层次，纯空镜画面单调。
- 标记为 POV 的段：第一人称视角描述（叙述者手部在画面内操作，无脸无全身，如 "The narrator's hands adjust the instrument panel..."）。
- 红线：仅虚构仙侠/古风角色，禁止真实可辨识人物；画面内无人声，任何人物嘴唇闭合、不说话（口播是画外旁白）。
- 每段在描述末尾写明人物判定：确实无人物的段才写 "No person appears in this shot."；有人物写明为虚构角色（背影/侧影/手部，如 "A robed figure seen from behind, lips closed, not speaking."）。

【质量补丁② 词数密度 250+】integrated_multimodal_description 每段 300-500 个英文单词（硬下限 250，不足即不合格），禁止空壳化、省略号、占位符、模板句顶替。场景、主体、景别与运镜、按该段时长展开的动作时间轴（时间戳不得超出该段时长）、材质与光影缺一不可，必须与该段台词强相关：台词讲什么画面演什么，数字与专有事物要有视觉对应物。

【质量补丁③ 风格锁自然融入】下面的风格块必须融入每段的画面与声音描述（措辞自然嵌入句子，禁止整段照抄风格块原文）：
{STYLE_BLOCK}

【质量补丁④ 生成期硬约束】
- 竖屏约束句：integrated_multimodal_description 的第一句必须逐字使用："{_VERT_SENT}"
- 旁白契约句：描述结尾必须逐字包含："{CONSTRAINT}"
- 官方语法：以 [Shot 1] 开头声明整体风格（Cinematic / 3D CG / 水墨等，从风格块选），再写构图与主体；段内切镜用 [Shot 2] At 00:SS.SSS 递增时间戳（不得超出该段时长）。
- 相机运动三要素（每段至少一句，句式模板）：`The camera <pushes in / pulls out / tilts up / tilts down / holds still> with <small/large> amplitude at <slow/fast> speed toward/over ...`——类型、幅度、速度三要素必须同句齐备，不得只写其中一两个。
- 反模板：相邻两段不得同景别+同运镜。
- 横移配额：横移类运镜（truck/track laterally、左右平移、平行扫掠）全片不超过 1/3 段落，多用推近/拉远/仰俯/静止/环绕交替。
- 画面内可见文字用英文双引号原样标出；旁白叙述者用稳定 ID (S1)："The narrator (S1) says in an off-screen voiceover ..."

只输出一个 JSON 对象（不要 markdown 围栏、不要任何解释）：{"shots": [ ...全部段落，按给定顺序，一段不落... ]}，不得省略任何段落，不得用省略号占位。"""


class ContractError(Exception):
    """整单契约失败（单镜缺失/ID 错位/字段缺失）——调用方整单重调，不写任何产物。"""


# 人物主体词（镜头描述含人物 → portrait_risk，宁可多查）。
# 注意：「No person appears in this shot」是否定句不算；叙述者 (S1) 是画外旁白不是画面人物，不算。
_PERSON_RE = re.compile(
    r"\bfigure\b|\bcharacter\b|\bpersons?\b|\brobed\b|\bfirst-person\b|\bpov\b|\bportrait of\b", re.I)
_NO_PERSON_SENT_RE = re.compile(r"no persons? (?:appear|appears|is|are)[^.!]*", re.I)


def build_system(style_block):
    """SYSTEM 提示词 = 底稿 + 四块质量补丁（占位符替换为真实约束句）+ 风格块。纯函数。
    约束句必须真实注入（逐字使用是硬要求，占位符原样发给模型=无法遵守——0924 实测教训）。"""
    t = SYSTEM_TEMPLATE.replace("{_VERT_SENT}", _VERT_SENT).replace("{CONSTRAINT}", CONSTRAINT)
    return fill_style(t, style_block or "")


def _pov_interval(pov_ratio):
    """POV 段间隔（每 1/ratio 段 1 段；0 = 无 POV）。唯一实现，user 行标记与 rec.mode 同源。"""
    return max(1, round(1 / pov_ratio)) if pov_ratio > 0 else 0


def build_request_body(board, style_block, max_tokens=14000, temperature=0.3, pov_ratio=0.2,
                       model=DEFAULT_MODEL):
    """纯函数接缝①：分镜清单 + 风格块 → Strata 请求体（OpenAI 标准）。

    OpenAI 标准字段 model/messages/max_tokens/temperature，非流式（不带 stream）；
    关思维链 chat_template_kwargs.enable_thinking=false（#83 spike §4 T1 定案写法），
    不携带 9router 专属 thinking 字段（spike T5：发了也关不掉，风格红线禁止）。
    缺 model 字段服务端虽容忍（spike T4 补默认名），红线仍要求显式携带——请求形态
    可断言、不受服务端默认值漂移影响。
    时长展示严格用 duration_est（权威字段；缺失的整单校验在 run_lane 预检，
    此处不做静默回退）。
    """
    pov_interval = _pov_interval(pov_ratio)
    lines = []
    for i, s in enumerate(board):
        pov = "POV" if (pov_interval > 0 and i % pov_interval == 0) else "非POV"
        lines.append(f"{s['id']} | {s['duration_est']:.1f}s | {pov} | {s.get('text', '')}")
    seg_lines = "\n".join(lines)
    user = (f"分镜清单（共 {len(board)} 段）：\n{seg_lines}\n\n"
            f"请输出全部 {len(board)} 段的 H3 prompt JSON。")
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": build_system(style_block)},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def call_strata(base_url, body, timeout=900):
    """唯一网络 IO：POST {base}/v1/chat/completions → (content, usage)。非流式（OpenAI
    标准响应：choices[0].message.content + usage；Strata server 兼容该形态，spike §4）。"""
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    msg = resp.get("choices", [{}])[0].get("message", {})
    return (msg.get("content") or ""), (resp.get("usage") or {})


def parse_shots(content):
    """LLM 回复 → shots 列表（容忍 ```json 围栏 / 前后杂文，复用 llm_json 括号配对提取）。
    不可解析或缺 shots 数组 → None（整单失败）。"""
    obj = llm_json._extract_json(content or "")
    if not isinstance(obj, dict):
        return None
    shots = obj.get("shots")
    return shots if isinstance(shots, list) else None


def assemble_prompt(shot):
    """响应 shot dict → 三字段文本。字段头特征串由代码保证（submit_accel 覆盖校验依赖，
    不能靠 LLM 自觉）；enforce_constraints / sanitize_camera_text 均操作此文本格式。"""
    return (f"integrated_multimodal_description:\n{shot.get('integrated_multimodal_description', '')}\n\n"
            f"overall_soundscape:\n{shot.get('overall_soundscape', '')}\n\n"
            f"non_diegetic_music: {shot.get('non_diegetic_music', 'N/A')}")


def portrait_risk(prompt, issues=()):
    """portrait_risk 启发式（宁可多查，命中即 risk → s08 首/中/尾多帧 vision 复核）：
    ① 横移词（schema.lateral_camera_sentences：相机横向行进句）；
    ② 人物词（镜头画面含虚构人物/POV 手部——人物镜头是旋转高危段，ADR 0002 车道换轨后
       逐段判断改由本启发式兜底）；
    ③ 竖屏风险词（环形/环绕/俯瞰/横排，enforce_constraints 告警——旧车道同款防线保留）；
    ④ 横移改写已发生（enforce_constraints 的 PAN_REWRITES 告警）——改写与句级消毒两条
       收口路径都算横移意图命中，不对称漏标会辜负「宁可多查」。"""
    if schema.lateral_camera_sentences(prompt):
        return True
    if _PERSON_RE.search(_NO_PERSON_SENT_RE.sub("", prompt or "")):
        return True
    return any(("竖屏风险词命中" in i or "横移运镜已消毒" in i) for i in issues)


def persist_shots(shots, board, out_dir, pov_ratio=0.2):
    """接缝②（确定性落盘，无网络）：LLM shots + 权威 board → 06_H3prompt/shotXX.json 全量产物。

    只接受「ID 集合完全一致」的整单：单镜缺失 / ID 错位 / 多余段 / 必需字段空缺 →
    ContractError，一个文件都不写（整单重调语义；部分落盘会把缺段伪装成齐段）。
    逐文件先写 .tmp 再原子替换——对账门按文件存在性核对，截断的半截 JSON 会伪装成齐段。
    落盘前收口：enforce_constraints（竖屏句前移/旁白句/横移改写）+ sanitize_camera_text
    （残余横向行进句句级消毒）——生成期约束门行为与旧车道完全一致；词数密度等内容质量
    归 audit_h3/门④（SPEC 质量兜底转移，本车道不做内容级重试）。
    video_seconds 权威 = duration_est（TTS 实测，非 est_seconds）；缺 duration_est 硬失败
    （s03 未回填，先跑 TTS 回填，绝不静默回退预估）。

    返回 {shot_id: 摘要}；契约失败不落盘。
    """
    want_ids = [s["id"] for s in board]
    by_id = {}
    for sh in shots:
        if isinstance(sh, dict) and isinstance(sh.get("id"), str):
            by_id[sh["id"].strip()] = sh
    missing = [i for i in want_ids if i not in by_id]
    if missing:
        raise ContractError(f"单镜缺失 {missing}（{len(by_id)}/{len(want_ids)}）→ 整单重调")
    extra = sorted(set(by_id) - set(want_ids))
    if extra:
        raise ContractError(f"ID 错位/多余段 {extra} → 整单重调")
    for s in board:
        if not isinstance(s.get("duration_est"), (int, float)):
            raise ContractError(f"{s['id']} 缺 duration_est（s03 TTS 回填缺失）→ 先跑 TTS 时长回填")
        sh = by_id[s["id"]]
        for field in ("integrated_multimodal_description", "overall_soundscape"):
            if not isinstance(sh.get(field), str) or not sh.get(field, "").strip():
                raise ContractError(f"{s['id']} 缺 {field} 非空内容 → 整单重调")

    pov_interval = _pov_interval(pov_ratio)
    os.makedirs(out_dir, exist_ok=True)
    summary = {}
    for i, s in enumerate(board):
        sid = s["id"]
        raw_prompt = assemble_prompt(by_id[sid])
        is_pov = pov_interval > 0 and i % pov_interval == 0
        prompt, issues = enforce_constraints(raw_prompt, is_pov)
        # risk 用收口前的原文判横移/人物意图（消毒改写不消除「宁可多查」的标记）
        risk = portrait_risk(raw_prompt, issues)
        # sanitize 零命中也会按空格重拼（换行布局被塌缩）——旧车道只在 n>0 时采用其输出，
        # 此处同款：无横向行进句则保留原文（三字段多行布局零扰动）
        prompt_san, n_san = schema.sanitize_camera_text(prompt)
        if n_san:
            prompt = prompt_san
        rec = {
            "id": sid,
            "prompt": prompt,
            "video_seconds": float(s["duration_est"]),
            "mode": "pov" if is_pov else "t2v",
            "scene_source": "strata_lane",
            "camera_sanitized": n_san > 0,
            # 横构图风险标记（s09 据 portrait_risk 强制首/中/尾多帧 vision 复核 + 旋转数据集特征）
            "portrait_risk": risk,
        }
        fp = os.path.join(out_dir, f"{sid}.json")
        with open(fp + ".tmp", "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        os.replace(fp + ".tmp", fp)
        summary[sid] = f"{len(prompt)}字" + (f"（{len(issues)}修正）" if issues else "") \
                       + ("（运镜消毒）" if n_san else "") + ("[risk]" if risk else "")
    return summary


def load_board(slug):
    """slug 目录 → (board, 板文件路径)。找不到分镜清单 → SystemExit。"""
    for sub in BOARD_DIRS:
        fp = os.path.join(slug, sub, "分镜清单_raw.json")
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as f:
                board = json.load(f)
            if not isinstance(board, list) or not board:
                raise SystemExit(f"分镜清单为空或非数组: {fp}")
            return board, fp
    raise SystemExit(f"找不到分镜清单_raw.json（找过 {[os.path.join(slug, d) for d in BOARD_DIRS]}）")


def run_lane(slug, base_url=BASE_URL, style_name=None, retries=2, max_tokens=14000,
             timeout=900, temperature=0.3, pov_ratio=0.2, out_dir=None, caller=None,
             model=None):
    """压缩车道主流程：整单调用 → 契约校验 → 落盘；整单失败重试 retries 次；连败硬失败。
    caller 参数注入假 LLM（测试接缝），生产为 None 走 call_strata。
    model=None 时用模块默认 DEFAULT_MODEL（strata；STRATA_MODEL env 在 import 时读定，
    driver/CLI 层不重复读 env——保持 build_request_body 纯函数）。
    返回 0 = 成功（全部段落落盘），1 = 连败（未写任何产物）。"""
    board, board_fp = load_board(slug)
    out_dir = out_dir or os.path.join(slug, OUT_SUBDIR)
    # 预检：duration_est 缺失在烧调用前就硬失败（s03 未回填；绝不静默回退预估 est_seconds）
    nodur = [s["id"] for s in board if not isinstance(s.get("duration_est"), (int, float))]
    if nodur:
        raise SystemExit(f"[FAIL] {nodur} 缺 duration_est（s03 TTS 回填缺失）→ 先跑 sync_tts_durations 回填再跑车")
    style_block = load_style(style_name) if style_name else ""
    if style_name:
        print(f"[风格] 已注入: {style_name}（{len(style_block)} 字）")
    print(f"[压缩车道] {len(board)} 段 ← {board_fp} → {out_dir}")

    body = build_request_body(board, style_block, max_tokens=max_tokens,
                              temperature=temperature, pov_ratio=pov_ratio,
                              model=model or DEFAULT_MODEL)
    send = caller or (lambda: call_strata(base_url, body, timeout=timeout))
    want_ids = {s["id"] for s in board}
    last_err = ""
    t0 = time.time()
    for attempt in range(1, retries + 2):
        try:
            content, usage = send()
        except Exception as e:
            last_err = f"调用失败: {e}"
            print(f"[整单 {attempt}/{retries + 1}] {last_err}")
            if attempt <= retries:
                time.sleep(3)
            continue
        llm_json.record_usage(usage)   # token 台账（driver report 同款口径，票3 接线）
        shots = parse_shots(content)
        if shots is None:
            last_err = "回复不可解析（无 shots JSON）"
            print(f"[整单 {attempt}/{retries + 1}] {last_err}｜尾部: {content[-120:]!r}")
        else:
            try:
                summary = persist_shots(shots, board, out_dir, pov_ratio=pov_ratio)
            except ContractError as e:
                last_err = str(e)
                print(f"[整单 {attempt}/{retries + 1}] 契约不合格: {e}")
            except Exception as e:   # 落盘 IO 等意外也走整单重试，不让半截状态逃出重试网
                last_err = f"落盘异常: {e}"
                print(f"[整单 {attempt}/{retries + 1}] {last_err}")
            else:
                wall = time.time() - t0
                comp = usage.get("completion_tokens") or 0
                print(f"[OK] 第{attempt}次整单通过：{len(summary)}/{len(want_ids)} 段落盘 "
                      f"wall={wall:.1f}s prompt_tok={usage.get('prompt_tokens')} "
                      f"completion_tok={comp}"
                      + (f" gen_tps={comp / wall:.1f}" if wall else ""))
                for sid, note in summary.items():
                    print(f"  [{sid}] {note}")
                print(f"RESULT total={len(want_ids)} ok={len(summary)} fail=[]")
                return 0
        if attempt <= retries:
            time.sleep(3)
    print(f"[FAIL] 压缩车道 {retries + 1} 次整单连败，硬失败（不回退云端；"
          f"回退走旧车道显式开关）。末次原因: {last_err}")
    print(f"[FAIL] 注意：{out_dir} 若存在旧产物可能与本次状态混杂，重跑前先清空该目录再整单重调。")
    print(f"RESULT total={len(want_ids)} ok=0 fail=[整单]")
    return 1


def main():
    ap = argparse.ArgumentParser(description="压缩车道：单次 Strata 调用直出 06_H3prompt 全量产物")
    ap.add_argument("slug", help="分支目录（含 01_分段/分镜清单_raw.json）")
    ap.add_argument("--style", default=None, help="风格名（styles/<名>.md，注入 SYSTEM）")
    ap.add_argument("--base-url", default=BASE_URL, help=f"Strata 服务地址（默认 {BASE_URL}，env STRATA_URL 可覆盖）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"模型名（默认 {DEFAULT_MODEL}，env STRATA_MODEL 可覆盖）")
    ap.add_argument("--retries", type=int, default=2, help="整单失败重试次数（默认 2，共 3 次调用）")
    ap.add_argument("--max-tokens", type=int, default=14000, help="单次调用生成预算（默认 14000）")
    ap.add_argument("--timeout", type=int, default=900, help="单次调用超时秒（默认 900）")
    ap.add_argument("--temperature", type=float, default=0.3, help="采样温度（默认 0.3）")
    ap.add_argument("--pov-ratio", type=float, default=0.2, help="POV 段占比（默认 0.2，每 5 段 1 段）")
    ap.add_argument("--out-dir", default=None, help="覆盖输出目录（默认 <slug>/06_H3prompt）")
    args = ap.parse_args()
    sys.exit(run_lane(args.slug, base_url=args.base_url, style_name=args.style,
                      retries=args.retries, max_tokens=args.max_tokens, timeout=args.timeout,
                      temperature=args.temperature, pov_ratio=args.pov_ratio,
                      out_dir=args.out_dir, model=args.model))


if __name__ == "__main__":
    main()
