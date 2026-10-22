#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cover_recheck.py — Krea2 封面 vision 复检（T5 #105，自检门⑦的机器预检）：
色调符合预期 + 主体清晰 + 无变形乱码 + 元素到位。走与 driver 判读同一端点契约
（缺省本地 llama-server，显式 env 切回云端）；CPU 端点与 Strata 窗口无显存牵连，
s11 流程与产物零变化（本工具是确认门⑤前的辅助信号，结论落盘供人审参考）。

用法：
  python scripts/cover_recheck.py --image 09_封面/海报_krea2数字迷幻_1536x768.png \\
      --expect "参考文本：暮色云海上的仙侠宫殿，紫金色调，主峰居中……" \\
      --out 09_封面/封面复检.json
退出码：0 复检通过 / 2 有问题（issue 列表见落盘）/ 1 判读失败
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import vision_common as vc

PROMPT = (
    "这是一张 AI 生成的视频封面图。对照使用者的预期描述做四项复检：\n"
    "1) 色调符合预期 2) 主体清晰（无糊化/多人混叠）3) 无变形乱码（文字/肢体/结构）\n"
    "4) 元素到位（预期描述中的关键元素都在）\n"
    "预期描述：{EXPECT}\n"
    '只输出 JSON，不要解释：{"tone_ok": true/false, "subject_ok": true/false, '
    '"no_artifact": true/false, "elements_ok": true/false, '
    '"issues": ["问题1"]}（issues 列出所有不达标项，全过则空数组）')


def recheck(api, model, image_path, expect, timeout=240, retries=2):
    """封面四项复检。返回 (result_dict, err)。"""
    b64 = base64.b64encode(open(image_path, "rb").read()).decode()
    # 四项布尔 + issues 数组比三级判定长，保底 500 token（低于 JUDGE_MAX_TOKENS 时取 500）
    body = json.dumps({"model": model, "max_tokens": max(vc.JUDGE_MAX_TOKENS, 500),
                       "messages": [{"role": "user", "content": [
                           {"type": "text", "text": PROMPT.replace("{EXPECT}", expect)},
                           {"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]},
                      ensure_ascii=False).encode()
    err = ""
    for attempt in range(retries + 1):
        try:
            d = vc.post_chat(api, body, timeout=timeout)
            content = str(d["choices"][0]["message"]["content"])
            j = json.loads(content[content.find("{"):content.rfind("}") + 1])
            keys = ("tone_ok", "subject_ok", "no_artifact", "elements_ok")
            issues = [str(x) for x in (j.get("issues") or [])]
            for k in keys:
                if k not in j:
                    raise ValueError(f"缺字段 {k}")
            passed = all(bool(j[k]) for k in keys) and not issues
            return {"pass": passed,
                    "tone_ok": bool(j["tone_ok"]), "subject_ok": bool(j["subject_ok"]),
                    "no_artifact": bool(j["no_artifact"]), "elements_ok": bool(j["elements_ok"]),
                    "issues": issues}, ""
        except Exception as e:  # noqa: BLE001
            err = str(e)[:120]
        time.sleep(2 * (attempt + 1))
    return None, err


def main():
    ap = argparse.ArgumentParser(description="Krea2 封面 vision 复检（自检门⑦机器预检）")
    ap.add_argument("--image", required=True)
    ap.add_argument("--expect", required=True, help="预期描述（封面参考文本核心句）")
    ap.add_argument("--api", default=os.environ.get("DRIVER_VISION_API", vc.default_api()))
    ap.add_argument("--model", default=vc.DEFAULT_MODEL)
    ap.add_argument("--out", default=None, help="结论落盘 JSON（缺省与图同目录 封面复检.json）")
    a = ap.parse_args()
    if not os.path.exists(a.image):
        print(f"ERROR 封面图不存在: {a.image}")
        return 3
    res, err = recheck(a.api, a.model, a.image, a.expect)
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.image)), "封面复检.json")
    result = {"image": a.image, "backend_model": a.model, "api": a.api, "error": err}
    if res is None:
        result.update({"verdict": "复检失败（人审兜底不受影响）"})
        rc = 1
    else:
        result.update(res)
        result["verdict"] = "复检通过" if res["pass"] else "复检有问题（见 issues）"
        rc = 0 if res["pass"] else 2
    print(json.dumps(result, ensure_ascii=False))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"结论已落盘 → {out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
