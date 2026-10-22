#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reconcile.py — VLM×CLIP 双路结论合成纯函数（T4 #106，spec 规则表）。

输入两路 verdict（与 VLM 相同三级契约：正常/倾斜/翻转；CLIP 另带置信度），
输出最终判定 + 人审点名标记。规则表（宁误报不漏报）：

  VLM＼CLIP   正常            倾斜            翻转            缺席/失败
  正常        采信 正常        采信 VLM+分歧    升级人审点名      VLM 单路
  倾斜        采信 倾斜        采信 倾斜        升级人审点名      VLM 单路
  翻转        采信 翻转+分歧    采信 翻转        采信 翻转        VLM 单路
  judge_fail  judge_fail+CLIP提示 judge_fail+提示 judge_fail+提示   judge_fail

- 一致（同 verdict）→ 采信
- CLIP 翻转 vs VLM 非翻转 → 升级人审点名（VLM 判定照常生效：正常不重烧、
  倾斜照旧只标记）——「宁误报不漏报」
- CLIP 缺席/失败 → VLM 单路（行为与现状完全一致，AC2）
- VLM 失败 → judge_fail 照旧留痕；CLIP 翻转告警附加入点名词（不改变留痕语义）
- CLIP 倾斜 vs VLM 正常 / CLIP 正常 vs VLM 翻转 → 采信 VLM + note 记分歧（报告可见）

置信度门控（T8 #117，作用对象 = 主判 VLM 的置信度，Jev 形态才携带）：
  ≥GATE_HI(0.90) 自动过 / [GATE_LO, GATE_HI) spot_check=True 记录抽检 /
  <GATE_LO(0.60) escalate=True 进九宫格点名。
  红线：calibrated=False（校准参数未落盘）时门控整体停用——未校准 softmax
  虚高是 Jev 生态公认坑，置信度只随台账记录、不得改变 escalate/spot 判定。

输出契约（dict）：
  {"verdict": "正常|倾斜|翻转|judge_fail",
   "source": "both|vlm|clip|none",
   "escalate": bool,          # 进确认门④九宫格点名名单（CLIP 分歧 或 低置信）
   "escalate_clip": bool,     # 升级缘由：CLIP 翻转分歧/附加（机器可读，免文案反推）
   "escalate_low": bool,      # 升级缘由：校准后低置信 <GATE_LO
   "spot_check": bool,        # 中间带置信度，记录抽检（T8）
   "confidence": float|None,  # CLIP 置信度（T4 通道，采纳 CLIP 或升级时有值）
   "note": str}               # 分歧/降级/门控说明（进报告/台账）
"""
from vision_common import VERDICT_LEVELS  # 单一事实源（评审收敛）

# 门控三级阈值（T8 #117 票面定值；唯一定义处——jev_calibrate/run_pipeline 引此）
GATE_HI = 0.90
GATE_LO = 0.60


# T10 #119 换岗：探针（rotate_search 形态）升主判时，VLM 的三级"翻转/倾斜"不再
# 是判据——4B 在四向题上 acc 0.42（恒答"下"），任何旋转体都答错，保留它只会
# 把探针的翻转稀释成"分歧→人审点名"（2026-10-22 现场：生产 20 段全判正常 0.4x）。
# 主判切换后降级语义不变：探针缺席/失败 → VLM 单路（AC2 仍然成立）。
def reconcile(vlm, clip, calibrated=False, clip_primary=False):
    """vlm: {"verdict": 三级或"judge_fail", "confidence": float|None, "err": str|None}
              ——confidence 仅 Jev 形态携带（JSON 形态 None，门控自然停用）
    clip: {"verdict": 三级, "confidence": float} | None（None=层缺席/失败）
    calibrated: 主判置信度是否已校准（references/jev-calibration.json 在位）
    clip_primary: True = 探针升主判（T10 #119）。此时：
        · 探针翻转 → 直接判翻转（不再要求 VLM 同意，也不再降级为人审点名）；
        · VLM 判翻转 → 仍判翻转（两路取"更严"，宁误报不漏报）；
        · 探针正常且 VLM 正常 → 正常（VLM 的置信度门控停用——主判已换岗，
          未校准的 4B 置信度不再有意义）。探针缺席时本参数自动失效。
    """
    v = _norm_vlm(vlm)
    c = _norm_clip(clip)
    if clip_primary and c is not None:
        gated = _gate(None, False)  # 主判换岗：VLM 置信度门控停用
    else:
        gated = _gate(_vconf(vlm), calibrated)
    low, spot, gnote = gated
    if clip_primary and c is not None:
        # 探针主判（T10 #119）：翻转以探针为准；VLM 只增不减（它也判翻转就同判）。
        # 必须排在 judge_fail 分支之前——换岗的核心收益就是「探针在位时 VLM 判读
        # 失败不再整批 judge_fail」，而是照常给出正常/翻转结论。
        if c == "翻转" or v == "翻转":
            return {"verdict": "翻转", "source": "clip" if c == "翻转" else "vlm",
                    "escalate": False, "escalate_clip": False, "escalate_low": False,
                    "spot_check": False, "confidence": _conf(clip),
                    "note": (f"探针主判：翻转(conf {_conf(clip):.2f})" if c == "翻转"
                             else f"VLM 判翻转但探针判 {c}（两路取严，仍记翻转）")}
        return {"verdict": v if v != "judge_fail" else "正常",
                "source": "both" if v != "judge_fail" else "clip",
                "escalate": False, "escalate_clip": False, "escalate_low": False,
                "spot_check": False, "confidence": _conf(clip),
                "note": ("探针主判：正常" if v != "judge_fail"
                         else "探针主判：正常（VLM 判读失败，仅探针）")}
    if v == "judge_fail":
        # VLM 失败：judge_fail 照旧留痕；CLIP 翻转告警附加入点名（不改变留痕语义）
        esc = (c == "翻转")
        return {"verdict": "judge_fail", "source": "none" if c is None else "clip",
                "escalate": esc, "escalate_clip": esc, "escalate_low": False,
                "spot_check": False,
                "confidence": _conf(clip) if esc else None,
                "note": ("VLM 判读失败；CLIP 翻转告警附加人审点名" if esc
                         else "VLM 判读失败" + ("；CLIP 层亦不可用" if c is None else ""))}
    if c is None:
        # CLIP 缺席/失败：VLM 单路（现状行为）；门控照常作用（主判置信度在）
        notes = [n for n in ("CLIP 层缺席，VLM 单路", gnote) if n]
        return {"verdict": v, "source": "vlm", "escalate": low,
                "escalate_clip": False, "escalate_low": low,
                "spot_check": spot,
                "confidence": None, "note": "；".join(notes)}
    if v == c:
        return {"verdict": v, "source": "both", "escalate": low,
                "escalate_clip": False, "escalate_low": low,
                "spot_check": spot,
                "confidence": _conf(clip), "note": gnote}
    if c == "翻转":
        # CLIP 翻转 vs VLM 正常/倾斜 → 升级人审点名（VLM 判定照常生效）
        note = f"分歧：CLIP 翻转({_conf(clip):.2f}) vs VLM {v} → 人审点名"
        if gnote:
            note += "；" + gnote
        return {"verdict": v, "source": "both", "escalate": True,
                "escalate_clip": True, "escalate_low": low,
                "spot_check": spot,
                "confidence": _conf(clip), "note": note}
    # 其余不一致（CLIP 倾斜 vs VLM 正常、CLIP 正常 vs VLM 翻转）→ 采信 VLM + 记分歧
    note = f"分歧：CLIP {c} vs VLM {v}（采信 VLM）"
    if gnote:
        note += "；" + gnote
    return {"verdict": v, "source": "both", "escalate": low,
            "escalate_clip": False, "escalate_low": low,
            "spot_check": spot,
            "confidence": _conf(clip), "note": note}


def _gate(vconf, calibrated):
    """主判置信度 → (低置信点名, 抽检, note)。未校准/无置信度 = 门控停用
    （红线：未校准置信度不得参与门控）。"""
    if vconf is None or not calibrated:
        return False, False, ""
    if vconf < GATE_LO:
        return True, False, f"置信度 {vconf:.2f}<{GATE_LO:.2f} → 人审点名"
    if vconf < GATE_HI:
        return False, True, f"置信度 {vconf:.2f} 抽检（{GATE_LO:.2f}-{GATE_HI:.2f}）"
    return False, False, ""


def _vconf(vlm):
    """主判置信度提取（None = 缺失，与 0.0 区分——0.0 是合法低置信）。"""
    try:
        v = (vlm or {}).get("confidence")
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _norm_vlm(vlm):
    v = str((vlm or {}).get("verdict") or "judge_fail")
    return v if v in VERDICT_LEVELS else "judge_fail"


def _norm_clip(clip):
    if not clip:
        return None
    c = str(clip.get("verdict") or "")
    return c if c in VERDICT_LEVELS else None


def _conf(clip):
    try:
        return float((clip or {}).get("confidence"))
    except (TypeError, ValueError):
        return 0.0
