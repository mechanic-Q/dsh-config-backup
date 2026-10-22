#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vision_smoke.py — 本地判读服务一键冒烟（#102 AC3）：探活/单帧判读/延迟三项输出。

排障口径（spec 验收14）：区分「服务问题（探活挂）/模型问题（判读挂/畸形）/
判读逻辑问题（verdict 不符预期）」。退出码：0 全过 / 1 有失败项。

用法：
  python scripts/vision_smoke.py                     # 对本地服务（自动档位）
  python scripts/vision_smoke.py --image <帧.jpg>     # 指定测试图（缺省取最新一期 shot01）
  python scripts/vision_smoke.py --api <url> --model <name>   # 探别的端点（含云端回退验尸）
  python scripts/vision_smoke.py --form json         # 显式冒烟 JSON 形态（缺省跟 driver）
"""
import argparse
import base64
import glob
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import vision_common as vc
import vision_server as vs

DATE_ROOT = "/e/每日新中国" if os.path.isdir("/e/每日新中国") else r"E:\每日新中国"


def pick_default_image():
    """默认测试图：最新一期 slug/_frames/shot01.jpg（798 字节以下视作坏帧跳过）。"""
    days = sorted(glob.glob(os.path.join(DATE_ROOT, "2026-*")), reverse=True)
    for d in days[:14]:
        for slug in glob.glob(os.path.join(d, "video", "*", "流水线", "daily-china-*")):
            for pat in ("shot01.jpg", "shot01_first.jpg", "*.jpg"):
                hit = sorted(glob.glob(os.path.join(slug, "_frames", pat)))
                if hit:
                    return hit[0]
    return None


def step_probe(expect_alias=None):
    st = vs.probe(expect_alias=expect_alias)
    print(f"[1/3] 探活 {vs.base_url()} → {json.dumps(st, ensure_ascii=False)}")
    return st["online"] and (expect_alias is None or st["alias"] == expect_alias)


def step_judge(api, model, image, timeout=180, form="jev"):
    # 走 jev_body/judge_body(路径)=缩图口径（评审收敛：与 driver/盲比同预算，冒烟延迟
    # 参考区间才有意义；原图直送口径见 pitfalls #55）。缺省形态跟 driver（T8 起 jev）。
    body = vc.jev_body(model, image) if form == "jev" else vc.judge_body(model, image)
    t0 = time.time()
    try:
        d = vc.post_chat(api, body, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"[2/3] 单帧判读（{form}）→ 失败: {e}")
        return False, 0.0
    dt = time.time() - t0
    content = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
    try:
        if form == "jev":
            verdict, conf = vc.jev_decide(vc.jev_project(d))
            print(f"[2/3] 单帧判读（{form}）→ verdict={verdict!r} conf={conf:.3f}"
                  f"（{dt:.1f}s，usage={d.get('usage', {}).get('completion_tokens')} tok）")
            return True, dt
        verdict = vc.extract_verdict(content)
        print(f"[2/3] 单帧判读（{form}）→ verdict={verdict!r}（{dt:.1f}s，"
              f"{len(content)} 字符，usage={d.get('usage', {}).get('completion_tokens')} tok）")
        return True, dt
    except Exception as e:  # noqa: BLE001
        print(f"[2/3] 单帧判读（{form}）→ 响应畸形（模型问题/端点缺 logprobs）: {e}；"
              f"content[:120]={content[:120]!r}")
        return False, dt


def main():
    ap = argparse.ArgumentParser(description="本地判读服务冒烟（三项：探活/单帧/延迟）")
    ap.add_argument("--api", default=os.environ.get("DRIVER_VISION_API", vc.default_api()))
    ap.add_argument("--model", default=vc.DEFAULT_MODEL)
    ap.add_argument("--image", default=None)
    ap.add_argument("--form", default=vc.judge_mode(), choices=("jev", "json"),
                    help="冒烟形态：缺省跟 DRIVER_VISION_MODE（driver 缺省 jev）")
    ap.add_argument("--no-probe", action="store_true", help="只打判读（探云端等非本地端点用）")
    a = ap.parse_args()

    ok = True
    if not a.no_probe:
        ok = step_probe(expect_alias=a.model if "127.0.0.1" in a.api or
                        vs.base_url().split("//")[-1].split(":")[0] in a.api else None)
        if not ok:
            print("→ 服务问题：先 vision-server-setup.sh / vision_server.py ensure")
    image = a.image or pick_default_image()
    if not image or not os.path.exists(image):
        print(f"[2/3] 无测试图可用（--image 指定）；跳过判读与延迟")
        return 1
    print(f"      测试图: {image}")
    judged, dt = step_judge(a.api, a.model, image, form=a.form)
    ok = ok and judged
    print(f"[3/3] 延迟: {dt:.1f}s（判读端到端；CPU 参考区间 2B≈2-4s / 4B≈3-6s / 8B≈8-20s）")
    print("SMOKE " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
