#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_vision_date_root.py — #157 视觉链数据根宿主无关（无框架，assert 直跑）。

现象（票 #157）：两脚本把数据根写死 Windows 盘符，WSL 下 os.path.exists 恒 False，
eval_rows() 静默 0 行、frame_paths() 全 None，退出码仍 0——「看起来应该对」的静默失败。

覆盖：
1. 数据根形态：两脚本的 DATE_ROOT 必须是本机口径（WSL → /mnt/<盘>/…，Windows → <盘>:\\…），
   且等于 dsh_judge.to_host_path(DATE_ROOT_WIN)——复用 #143 收口的单一事实源，不新增宿主判定
2. 帧解析可注入 root：frame_of / frame_paths 用临时夹具（date/slug/_frames）命中，
   不依赖 E: 真实数据；缺帧返回 None 而不是抛异常
3. eval_rows 可注入 dataset + root：临时数据集 + 夹具 → 非 0 行，且路径落在夹具内
4. 反向断言：把 DATE_ROOT 换成旧的写死盘符形态后，同一套解析必须拿不到帧
   —— 这条在 WSL 上必红，是本票的回归锚
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dsh_judge as dj
import vision_blindbench as vb
import vision_bench_review as vr

DATE_ROOT_WIN = dj.win_join("E:", "每日新中国")
SUBDIR = ("video", "每日新中国b站", "流水线")


def check(cond, msg):
    assert cond, msg
    print("ok " + msg)


def shape_ok(p):
    """本机口径形态：WSL 以 /mnt/<盘>/ 开头；Windows 以 <盘>:\\ 开头。"""
    if dj._in_wsl():
        return p.startswith("/mnt/") and len(p.split("/")) > 3
    return len(p) > 3 and p[1] == ":" and p[2] in ("\\", "/")


def frame_dir(root, date, slug):
    return os.path.join(root, date, *SUBDIR, slug, "_frames")


def make_fixture(root):
    """造 date/slug/_frames 夹具：shot01.jpg（中帧）+ shot02_first.jpg（仅首帧）。"""
    d = frame_dir(root, "2026-10-15", "daily-china-20261015")
    os.makedirs(d)
    with open(os.path.join(d, "shot01.jpg"), "wb") as f:
        f.write(b"\xff\xd8mid")
    with open(os.path.join(d, "shot02_first.jpg"), "wb") as f:
        f.write(b"\xff\xd8first")
    return d


# ── 1. 数据根形态（宿主无关 + 复用单一事实源）────────────────────────────
print("宿主=%s in_wsl=%s" % (sys.platform, dj._in_wsl()))
check(shape_ok(vb.DATE_ROOT), "blindbench.DATE_ROOT 为本机口径: %s" % vb.DATE_ROOT)
check(shape_ok(vr.DATE_ROOT), "bench_review.DATE_ROOT 为本机口径: %s" % vr.DATE_ROOT)
check(vb.DATE_ROOT == dj.to_host_path(DATE_ROOT_WIN),
      "blindbench 数据根 == dsh_judge.to_host_path(DATE_ROOT_WIN)")
check(vr.DATE_ROOT == dj.to_host_path(DATE_ROOT_WIN),
      "bench_review 数据根 == dsh_judge.to_host_path(DATE_ROOT_WIN)")
check(dj.to_win_path(vb.DATE_ROOT) == DATE_ROOT_WIN,
      "to_win_path 往返回 Windows 口径（判官 prompt 侧仍需它）")

# ── 2. 帧解析可注入 root（不依赖 E:）────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="t157_")
fx = make_fixture(tmp)
row1 = {"date": "2026-10-15", "slug": "daily-china-20261015", "shot": "shot01"}
row2 = {"date": "2026-10-15", "slug": "daily-china-20261015", "shot": "shot02"}
row3 = {"date": "2026-10-15", "slug": "daily-china-20261015", "shot": "shot09"}

fp1 = vb.frame_of(row1, root=tmp)
check(fp1 is not None and fp1.endswith("shot01.jpg") and fp1.startswith(tmp),
      "frame_of 命中中帧夹具")
fp2 = vb.frame_of(row2, root=tmp)
check(fp2 is not None and os.path.basename(fp2) == "shot02_first.jpg",
      "frame_of 中帧缺 → 退首帧")
check(vb.frame_of(row3, root=tmp) is None, "frame_of 全缺 → None（不抛）")

mid, extra = vr.frame_paths(row1, root=tmp)
check(mid is not None and mid.endswith("shot01.jpg"), "frame_paths 命中中帧夹具")
mid2, extra2 = vr.frame_paths(row2, root=tmp)
check(mid2 is not None and mid2.endswith("shot02_first.jpg"),
      "frame_paths 中帧缺 → 用首帧，extra 里仍在")
check(os.path.join(fx, "shot02_first.jpg") in extra2, "frame_paths extra 含首帧")
mid3, extra3 = vr.frame_paths(row3, root=tmp)
check(mid3 is None and extra3 == [], "frame_paths 全缺 → (None, [])（不抛）")

# ── 3. eval_rows 可注入 dataset + root ─────────────────────────────────
ds = os.path.join(tmp, "ds.jsonl")
with open(ds, "w", encoding="utf-8") as f:
    for r in ({"date": "2026-10-15", "slug": "daily-china-20261015",
               "shot": "shot01", "vision_verdict": "正常"},
              {"date": "2026-10-15", "slug": "daily-china-20261015",
               "shot": "shot09", "vision_verdict": "翻转"},   # 帧缺 → 剔除
              {"date": "2026-10-15", "slug": "daily-china-20261015",
               "shot": "shot02", "vision_verdict": "未判"}):  # 无基线 → 剔除
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
rows = vb.eval_rows(dataset=ds, root=tmp)
check(len(rows) == 1, "eval_rows 夹具上非 0 行（旧口径 WSL 下 0 行）: %d" % len(rows))
check(rows[0][0]["shot"] == "shot01" and rows[0][1].startswith(tmp),
      "eval_rows 返回 (row, 夹具内帧路径)")
check(rows[0][0]["_baseline"] == "正常" and rows[0][0]["_human"] is False,
      "eval_rows 基线口径不变（glm 行）")

# ── 4. 回归锚：旧口径（写死盘符）的双端不对称 ───────────────────────────
# 旧代码把这个 Windows 口径常量直接交给 os.path.join：Windows 上恰好就是本机
# 口径（所以 bug 长期不可见），WSL 上恒 False（0 行 / 全 None）。这条断言把
# 不对称钉死，防止有人把 DATE_ROOT 改回裸盘符还以为双端都没问题。
old_root = DATE_ROOT_WIN
if dj._in_wsl():
    check(not shape_ok(old_root), "WSL：Windows 盘符形态不算本机口径")
    check(vb.frame_of(row1, root=old_root) is None,
          "WSL：写死 Windows 盘符的旧口径拿不到帧（本票修复前即此现象）")
    check(vr.frame_paths(row1, root=old_root) == (None, []),
          "WSL：旧口径下 frame_paths 返回 (None, [])——审核页缩略图全空且不报错")
else:
    check(shape_ok(old_root),
          "Windows：写死盘符恰好就是本机口径——这正是当初只有 WSL 红的原因")
    check(vb.frame_of(row1, root=os.path.join(tmp, "无此目录")) is None,
          "Windows：注入不存在的 root → None（root 注入确实生效，不是回落到常量）")

# ── 5. 渲染层：file:// URL 与缺帧占位（AC2「缩略图不再全空」的真正落点）────
# 只验 frame_paths 拿到路径还不够：审核页是给 Windows 浏览器看的，WSL 侧的
# /mnt/e/… 直接拼 file:/// 会变成 file:////mnt/e/…（Windows 解析成 UNC \mnte…，
# 缩略图仍然空）。这里把「URL 必须是盘符口径」钉死，并验缺帧不再 AttributeError。
check(vr.file_url("/mnt/e/每日新中国/a b/shot01.jpg") == "file:///E:/每日新中国/a b/shot01.jpg",
      "file_url：/mnt/<盘>/… → file:///<盘>:/…（Windows 浏览器可解析）")
check(vr.file_url("E:" + chr(92) + "每日新中国" + chr(92) + "shot01.jpg")
      == "file:///E:/每日新中国/shot01.jpg",
      "file_url：Windows 口径 → 正斜杠 file:///，双端同一条 URL")
check("/mnt/" not in vr.file_url("/mnt/e/每日新中国/shot01.jpg"),
      "file_url：产出里不残留 /mnt/（残留即 file:////mnt/…，缩略图必空）")

row_noframe = {"date": "2026-10-15", "shot": "shot09", "baseline": "正常",
               "local": "正常", "latency_s": 0.1}
html_noframe = vr.cell(row_noframe, None)
check("帧不在盘" in html_noframe and "img" not in html_noframe,
      "cell(frame=None) 出占位、不抛 AttributeError（旧版在 WSL 上直接崩）")

row_ok = dict(row_noframe, shot="shot01")
check(vr.file_url(fp1) in vr.cell(row_ok, fp1),
      "cell 缩略图走 file_url（不是裸路径拼 file:///）")

# 端到端：日常审核页整页产出，路径全部是浏览器可用形态
slug_dir = os.path.join(tmp, "daily-china-20261015")
os.makedirs(os.path.join(slug_dir, "_driver"))
os.rename(fx, os.path.join(slug_dir, "_frames"))
with open(os.path.join(slug_dir, "_driver", "report.json"), "w", encoding="utf-8") as f:
    json.dump({"date": "2026-10-15", "s08": {"vision": {"flipped": ["shot01"]}}},
              f, ensure_ascii=False)
page_fp = os.path.join(tmp, "review.html")
vr.daily_page(slug_dir, page_fp)
page = open(page_fp, encoding="utf-8").read()
check("file:////" not in page, "daily_page 整页无 file:////（UNC 形态）")
check(page.count("file:///") >= 2, "daily_page 缩略图有 file:/// 链接: %d 处"
      % page.count("file:///"))

print("ALL PASS")
