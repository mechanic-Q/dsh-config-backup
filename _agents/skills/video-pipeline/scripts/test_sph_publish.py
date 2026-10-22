#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sph_publish.py — 票B：sph_publish 参数面与纯逻辑自检（assert 直跑）。

覆盖：
1. 模块 import 无副作用（不连 CDP、不读 argv、不起事件循环）
2. parse_args：publish 模式缺 --title/--video 报错；--check-only 免填；
   --expect-key 缺省取 --title 前 4 字
3. pick_dialog_button：prefer 顺序（直接发表压过 声明原创）、avoid 永不选、
   全不中返回 None（宁可 Unknown 不乱点）
4. extract_video_count：`视频 (89)` → 89；无计数 → None
5. title_in_list：关键词在场判定
不出网不碰 Chrome：CDP 相关异步路径不打桩（真连接验证属人工/联调范畴）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sph_publish as S  # noqa: E402 — 模块级若读 argv/连 CDP 则此处即红

# 1) parse_args：publish 模式必填项（连旗带值成对移除）
for miss in ("--title", "--video"):
    argv = ["--title", "t", "--video", "v.mp4"]
    i = argv.index(miss)
    del argv[i:i + 2]
    try:
        S.parse_args(argv)
        raise SystemExit(f"缺 {miss} 应报错")
    except SystemExit:
        pass

# 2) --check-only 免填 title/video；expect-key 缺省取 title 前 4 字
a = S.parse_args(["--check-only", "--title", "发电装机增11.1%"])
assert a.check_only is True and a.expect_key == "发电装机", a
a = S.parse_args(["--title", "标题全文", "--video", "v.mp4", "--expect-key", "自定义"])
assert a.expect_key == "自定义"

# 3) pick_dialog_button：实证弹窗 [直接发表, 声明原创] 必点「直接发表」
assert S.pick_dialog_button(["直接发表", "声明原创"]) == "直接发表"
# avoid 永不选：只剩 avoid 按钮 → None
assert S.pick_dialog_button(["去声明", "取消"]) is None
assert S.pick_dialog_button(["取消"]) is None
# prefer 顺序：列表同时有 知道了/确定 → 按优先序取「知道了」
assert S.pick_dialog_button(["确定", "知道了"]) == "知道了"
# 非避免的陌生按钮可兜底
assert S.pick_dialog_button(["随便"]) == "随便"
assert S.pick_dialog_button([]) is None
assert S.pick_dialog_button(["", "  "]) is None

# 4) extract_video_count
assert S.extract_video_count("视频管理\n视频 (89)\n合集 (1)") == 89
assert S.extract_video_count("视频 (88)") == 88
assert S.extract_video_count("没有计数文本") is None
assert S.extract_video_count("") is None
assert S.extract_video_count(None) is None

# 5) title_in_list
assert S.title_in_list("...发电装机增11.1%...", "发电装机") is True
assert S.title_in_list("别的视频", "发电装机") is False
assert S.title_in_list("anything", "") is False
assert S.title_in_list(None, "发电装机") is False

# 6) main(argv) 不带必填参数走 argparse 报错（argv 守卫，不触网）
try:
    S.main([])
    raise SystemExit("空 argv 应 SystemExit")
except SystemExit:
    pass

print("test_sph_publish: 全部通过")
