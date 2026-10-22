"""strip_motion_text 删除式消毒单测（2026-09-11 用户拍板）。
夹具 = 0911 期真实歪斜段 prompt 原稿备份（06_H3prompt/_backup_0911/）。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

# 路径策略（2026-10-19，#141）：夹具优先用仓内副本 scripts/fixtures/schema_0911/——
# CI（ubuntu）没有任何本机盘，也不能假设 E: 存在；仓内副本保证任何宿主跑同一份字节。
# 本机若没有仓内副本（历史检出），才按运行端回退到真实盘（WSL=/mnt/e/…，Windows=E:/…）。
HERE_DIR = os.path.dirname(os.path.abspath(__file__))
_FIXTURE_DIR = os.path.join(HERE_DIR, "fixtures", "schema_0911")
if os.path.isdir(os.path.join(_FIXTURE_DIR, "_backup_0911")):
    PROMPTS_DIR = _FIXTURE_DIR
else:
    import platform
    if platform.system() == "Windows":
        PROMPTS_DIR = "E:/每日新中国/2026-09-11/video/每日新中国b站/流水线/daily-china-20260911/06_H3prompt"
    else:
        PROMPTS_DIR = "/mnt/e/每日新中国/2026-09-11/video/每日新中国b站/流水线/daily-china-20260911/06_H3prompt"
BACKUP_DIR = os.path.join(PROMPTS_DIR, "_backup_0911")

def _orig(sid):
    """消毒前的原稿（备份）。"""
    return json.load(open(os.path.join(BACKUP_DIR, f"{sid}.json"), encoding="utf-8"))["prompt"]

def _load(sid):
    """当前稿（可能已消毒）。"""
    return json.load(open(os.path.join(PROMPTS_DIR, f"{sid}.json"), encoding="utf-8"))["prompt"]

def test_orbit_family_deleted():
    """shot02 原稿的 slow orbit 家族至少删 3 句，删除后残留 0、补静止安全句。"""
    t = _orig("shot02")
    new, n = schema.strip_motion_text(t)
    assert n >= 3, f"orbit 句应删≥3，实际 {n}"
    assert not schema.motion_sentences(new), "删除后仍有运动句残留"
    assert "stationary" in new

def test_negative_constraint_replaced():
    """否定式约束块（NOT rotated/Absolutely forbidden）必须被整体替换为正向表述——
    2026-09-11 实锤根因：否定句反向诱发旋转。"""
    t = _orig("shot02")
    new, n = schema.strip_motion_text(t)
    assert "NOT rotated" not in new, "否定式约束残留"
    assert "Absolutely forbidden" not in new, "否定式约束残留"
    assert "The camera is held upright and locked off" in new, "正向约束句未注入"
    assert new.count("This is a standard vertical 9:16 portrait video.") == 1, "首句重复"

def test_protected_blocks_intact():
    """时间轴/声景段必须原样保留。"""
    t = _orig("shot02")
    new, _ = schema.strip_motion_text(t)
    for guard in ("overall_soundscape", "non_diegetic_music"):
        assert guard in new, f"保护块丢失: {guard}"
    import re
    assert len(re.findall(r"At \d{2}:\d{2}", new)) == len(re.findall(r"At \d{2}:\d{2}", t)), "时间轴句数量变化"

def test_scene_text_preserved():
    """非运动句（场景/光线/构图）原样保留——shot16 的 central-axis symmetry 句必须在。"""
    t = _orig("shot16")
    new, n = schema.strip_motion_text(t)
    assert n >= 1
    assert "strict central-axis symmetry" in new, "场景构图描述被误删"

def test_lateral_disguised_pushin():
    """「push-in + unfolds left-to-right」伪纵向横移必须命中（shot04/08 实锤形态）。"""
    t = _orig("shot04")
    new, n = schema.strip_motion_text(t)
    assert n >= 1, "伪纵向 push-in 未被识别"
    assert "left-to-right" not in new.replace("Absolutely forbidden", ""), "横向展开语义残留"

def test_clean_prompt_untouched():
    """无运动词的 prompt 应零删除、零改动（含换行格式零扰动）。"""
    t = ("This is a standard vertical 9:16 portrait video. "
         "A static wide shot of a mountain palace under a starry sky. "
         "The golden roof tiles glow softly. overall_soundscape:\nA gentle wind. "
         "non_diegetic_music: N/A")
    new, n = schema.strip_motion_text(t)
    assert n == 0 and new == t

def test_motion_sentences_diagnosis():
    """诊断函数返回的句子都含运动词/画框边缘运动/否定式旋转表述。"""
    t = _orig("shot16")
    hits = schema.motion_sentences(t)
    assert hits, "shot16 原稿应命中 orbit 句"
    assert all(schema._MOTION_VERB_RE.search(h) or schema._NEGATIVE_ROTATION_SENT_RE.search(h)
               for h in hits)

def test_negative_rotation_anywhere_removed():
    """否定式旋转句不受保护名单限制（时间轴句里也删）——0911 重写版实锤：
    LLM 自创 "The camera never rotates, never tilts sideways" 反向诱发。"""
    t = ("At 00:00.000, the camera never rotates, never tilts sideways, and never leaves "
         "the upright portrait orientation. The palace glows softly under moonlight.")
    new, n = schema.strip_motion_text(t)
    assert n >= 1, "否定式旋转句未被删除"
    assert "never rotates" not in new
    assert "palace glows" in new, "正常场景句被误删"

def test_vertical_anchor_idempotent():
    """严格消毒必须幂等：竖屏锚句只注入一次（2026-10-20 shot19 实锤缺陷）。

    原实现 strict=True 无条件追加锚句，干净 prompt 也返回 n_del>=1 →
    调用方 _strip_motion_prompt_file 误判「有诱因被删」→ 每轮重烧重复追加，
    prompt 越滚越长并触发删旧产物（实测连跑两次锚句出现 3 次，1868→2162 字节）。
    """
    t = ("This is a standard vertical 9:16 portrait video. The camera is held upright and locked off. "
         "[Shot 1] A static cart stands parked on a road receding to the top of the frame.")
    once, n1 = schema.strip_motion_text(t, strict=True)
    assert n1 == 1, f"首次严格消毒应注入 1 处锚句，实际 {n1}"
    assert once.count(schema._VERTICAL_ANCHOR_SENT) == 1, "锚句未注入或注入多次"
    twice, n2 = schema.strip_motion_text(once, strict=True)
    assert n2 == 0, f"第二次严格消毒应零改动（幂等），实际 {n2}"
    assert twice == once, "第二次消毒改动了文本（非幂等）"
    # 措辞不同的等价锚句（人工竖直化写法，shot19 实锤）也算「已存在」：
    manual = ("This is a standard vertical 9:16 portrait video. [Shot 1] A cart stands parked. "
              "The composition is built strictly bottom-to-top: the road runs from the bottom "
              "foreground straight up toward a distant vanishing point near the top of the frame.")
    m2, m_n = schema.strip_motion_text(manual, strict=True)
    assert m_n == 0 and m2 == manual, f"人工锚句被误判为缺失（n={m_n}）"


def test_stripped_files_have_no_motion():
    """本期 7 段消毒后的在用 prompt 不得再命中运动词（纯静止构图）。"""
    for sid in ("shot02", "shot04", "shot06", "shot08", "shot14", "shot16", "shot17"):
        remain = schema.motion_sentences(_load(sid))
        assert not remain, f"{sid} 残留运动句: {[s[:80] for s in remain]}"

if __name__ == "__main__":
    for fn in (test_orbit_family_deleted, test_negative_constraint_replaced,
               test_protected_blocks_intact, test_scene_text_preserved,
               test_lateral_disguised_pushin, test_clean_prompt_untouched,
               test_motion_sentences_diagnosis, test_negative_rotation_anywhere_removed,
               test_vertical_anchor_idempotent, test_stripped_files_have_no_motion):
        fn()
        print(f"ok {fn.__name__}")  # #170：✅ 在 cp936 裸跑下不可编码，改 ASCII 记号
    print("全部通过")
