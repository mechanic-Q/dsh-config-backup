#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""overlay_cover_text.py — 在 Krea2 底图上叠各平台封面大字（用户红线允许的唯一 PIL 用途）。

底图流程不变：B站 = 横版 Krea2 底图，抖音/视频号 = 竖版 Krea2 底图。
本脚本只做「标题打印」：按平台 UI 安全区排版，白字黑描边+投影。

用法:
  overlay_cover_text.py --base 底图.png --out 封面.png --text "封面大字" --platform bilibili
"""
import argparse
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    os.path.join(HERE, "..", "assets", "fonts", "SmileySans-Oblique.ttf"),
)

# 平台排版规格：相对底图短边的字号比例 + 文本锚点区域（比例坐标）
# - bilibili 横版：封面完整展示，字放左下三分之一（不遮主体，信息卡式）
# - douyin 竖版：信息流右侧图标栏(~15%宽)、底部文案区(~25%高)不可用；字放上部居中
# - kuaishou 竖版：瀑布流卡片按 3:4 居中裁切（1152×2016 时上下各裁 ~240px）→ 字放 22% 高度带内
# - toutiao 竖版：小视频竖版卡片；标题在卡片下方展示 → 字放中上，四周留边防裁
# - shipinhao 竖版：社交转发卡片会裁四周；字放中上(20%-45% 高度带)居中
SPECS = {
    "bilibili":  {"font_ratio": 0.105, "anchor": ("left", 0.06, 0.68), "max_inline": 9},
    "douyin":    {"font_ratio": 0.135, "anchor": ("center", 0.50, 0.20), "max_inline": 5},
    "kuaishou":  {"font_ratio": 0.130, "anchor": ("center", 0.50, 0.22), "max_inline": 5},
    "toutiao":   {"font_ratio": 0.120, "anchor": ("center", 0.50, 0.26), "max_inline": 6},
    "shipinhao": {"font_ratio": 0.125, "anchor": ("center", 0.50, 0.24), "max_inline": 5},
}


def pick_font(size):
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    raise RuntimeError("无可用中文字体（Noto CJK Bold / SmileySans）")


def split_lines(text, max_inline):
    """封面大字断行：空格/·/，优先；否则按 max_inline 硬分。6-10 字通常 1-2 行。"""
    text = text.strip()
    for sep in (" ", "·", "，", ","):
        if sep in text and all(len(x) <= max_inline + 2 for x in text.split(sep)):
            return [x for x in text.split(sep) if x]
    if len(text) <= max_inline:
        return [text]
    mid = (len(text) + 1) // 2
    return [text[:mid], text[mid:]]


def overlay(base_fp, out_fp, text, platform):
    spec = SPECS.get(platform)
    if not spec:
        raise ValueError(f"未知平台: {platform}（可选 {list(SPECS)}）")
    img = Image.open(base_fp).convert("RGB")
    W, H = img.size
    short = min(W, H)
    fsize = max(48, int(short * spec["font_ratio"]))
    font = pick_font(fsize)
    lines = split_lines(text, spec["max_inline"])

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    stroke = max(4, fsize // 14)
    line_h = fsize + int(fsize * 0.35)
    total_h = line_h * len(lines)

    align, ax, ay = spec["anchor"]
    y0 = int(H * ay)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)
        tw = bbox[2] - bbox[0]
        if align == "center":
            x = (W - tw) // 2
        else:
            x = int(W * ax)
        # 白字 + 黑描边 + 投影（描边粗、投影柔，户外/信息流小图都可读）
        draw.text((x + 6, y0 + 8), line, font=font, fill=(0, 0, 0, 150),
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
        draw.text((x, y0), line, font=font, fill=(255, 255, 255, 255),
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y0 += line_h
    shadow = layer.filter(ImageFilter.GaussianBlur(6))
    out = Image.alpha_composite(Image.alpha_composite(img.convert("RGBA"), shadow), layer)
    out.convert("RGB").save(out_fp, "PNG")
    return out_fp, len(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Krea2 底图叠平台封面大字")
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--platform", required=True, choices=list(SPECS))
    a = ap.parse_args()
    fp, n = overlay(a.base, a.out, a.text, a.platform)
    print(f"OK {fp} ({n} 行, platform={a.platform})")
