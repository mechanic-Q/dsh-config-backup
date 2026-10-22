#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""newspaper_date_check.py — 报纸日期三合一校验（T5 #105）：印刷日期 = 当日日期 =
新闻稿日期，三者必须一致；不一致即拦截（exit 2）。vision 调用走与 driver 判读
同一端点契约（DRIVER_VISION_API/DRIVER_VISION_MODEL，缺省本地 llama-server，
显式 env 一条切回云端），换模型不改校验语义。

用法（9:00 报纸任务选图时）：
  python scripts/newspaper_date_check.py --image "E:\\每日新中国\\2026-10-14\\2026-10-14_每日新中国_第N期.png"
  python scripts/newspaper_date_check.py --image <png> --date 2026-10-14 --script-date 2026-10-14 \\
      --out slug/00_口播稿/报纸日期校验.json
退出码：0 三合一通过 / 2 日期不一致（拦截）/ 1 判读失败（重试×2 后） / 3 用法错
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
    "这是一张报纸的图片。请找出报纸上印刷的出版日期（通常在报头/标题下方），"
    "原样转写为 YYYY-MM-DD 格式（只有年月就补齐 01）。\n"
    "只输出 JSON，不要解释：{\"printed_date\": \"YYYY-MM-DD\"}")


def judge_printed_date(api, model, image_path, timeout=180, retries=2):
    """读报纸印刷日期。返回 (date_str|None, err)。重试×2（与判读面同语义）。"""
    b64 = base64.b64encode(open(image_path, "rb").read()).decode()
    body = json.dumps({"model": model, "max_tokens": vc.JUDGE_MAX_TOKENS,
                       "messages": [{"role": "user", "content": [
                           {"type": "text", "text": PROMPT},
                           {"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]},
                      ensure_ascii=False).encode()
    err = ""
    for attempt in range(retries + 1):
        try:
            d = vc.post_chat(api, body, timeout=timeout)
            content = d["choices"][0]["message"]["content"]
            j = json.loads(str(content)[str(content).find("{"):str(content).rfind("}") + 1])
            date = str(j.get("printed_date", "")).strip()
            if date:
                return date, ""
            err = f"空日期: {content[:60]}"
        except Exception as e:  # noqa: BLE001
            err = str(e)[:120]
        time.sleep(2 * (attempt + 1))
    return None, err


def three_way(printed, day, script_day):
    """三合一判定：印刷 = 当日 = 稿件。返回 (ok, 不一致项列表)。"""
    bad = []
    if printed != day:
        bad.append(f"印刷({printed}) ≠ 当日({day})")
    if printed != script_day:
        bad.append(f"印刷({printed}) ≠ 稿件({script_day})")
    return (not bad), bad


def main():
    ap = argparse.ArgumentParser(description="报纸日期三合一校验")
    ap.add_argument("--image", required=True, help="报纸 PNG")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="当日日期")
    ap.add_argument("--script-date", default=None, help="新闻稿日期（缺省=当日）")
    ap.add_argument("--api", default=os.environ.get("DRIVER_VISION_API", vc.default_api()))
    ap.add_argument("--model", default=vc.DEFAULT_MODEL)
    ap.add_argument("--out", default=None, help="结论落盘 JSON 路径（可选）")
    a = ap.parse_args()
    if not os.path.exists(a.image):
        print(f"ERROR 报纸图不存在: {a.image}")
        return 3
    script_day = a.script_date or a.date
    printed, err = judge_printed_date(a.api, a.model, a.image)
    result = {"image": a.image, "day": a.date, "script_date": script_day,
              "printed_date": printed, "backend_model": a.model,
              "api": a.api, "error": err}
    if printed is None:
        result.update({"match": None, "verdict": "判读失败"})
        print(json.dumps(result, ensure_ascii=False))
        _maybe_dump(a.out, result)
        return 1
    ok, bad = three_way(printed, a.date, script_day)
    result.update({"match": ok, "verdict": "三合一通过" if ok else "三合一不一致(拦截)",
                   "inconsistent": bad})
    print(json.dumps(result, ensure_ascii=False))
    _maybe_dump(a.out, result)
    return 0 if ok else 2


def _maybe_dump(path, result):
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())
