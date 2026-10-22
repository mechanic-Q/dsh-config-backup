#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vision_bench_review.py — 判读人工审核页（T1 #102 盲比版 / T9 #118 日常版）。

盲比模式（缺省）：读 docs/spikes/vision-blindbench-<tag>.jsonl（vision_blindbench
逐行原始数据），产出单文件 HTML 审核页：按档分组，**分歧行置顶**（人标真值行 →
与 glm 分歧 → 判读错误），每格 = 帧缩略图 + 基线/本地双判定对照，点击开原图；
一致行折叠。帧缩略图走 file:// 直引原图（浏览器按需加载，不膨胀文件）。

日常模式（T9 #118）`--daily <slug目录>`：读 <slug>/_driver/report.json 的
s08.vision（当期 flipped/tilted/judge_failed + 点名/抽检/探针分歧段），生成
「driver 日常判定」审核视图——每格带 翻转/未翻转 标注按钮，人工逐格核对后
「导出结果」下载 JSON。

回填闭环 `--apply 结果.json`：把导出的标注机械写回 rotation-dataset.jsonl
（user_flagged / user_flagged_source，只动真值字段不动采集字段）——
人审结果一键回填，真值飞轮的日常入口。

用法：
  python scripts/vision_bench_review.py                          # 最新盲比 jsonl
  python scripts/vision_bench_review.py --jsonl <路径> --out <html路径>
  python scripts/vision_bench_review.py --daily <slug目录>        # 日常判定视图
  python scripts/vision_bench_review.py --apply daily-review-*.json  # 回填真值
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dsh_judge as dj
import vision_common as vc
import rotation_dataset as rd

# 帧根目录（双端同源素材，E: 盘）：同 vision_blindbench 口径——先拼 Windows 绝对
# 路径，再 to_host_path 换回本机口径（#157：写死盘符时 WSL 下 frame_paths() 对每行
# 都返回 (None, [])，审核页缩略图全空且不报错）。单一事实源 = dsh_judge：
# 路径转换的正式收口在 #143（hostpath 模块），此处按 #157 票面 AC3 临时复用。
DATE_ROOT_WIN = dj.win_join("E:", "每日新中国")
DATE_ROOT = dj.to_host_path(DATE_ROOT_WIN)
TIER_ORDER = ["2b", "4b", "8b"]
TIER_NAME = {"2b": "Qwen3-VL 2B", "4b": "Qwen3-VL 4B（定档）", "8b": "Qwen3-VL 8B（灵敏候选）"}
VERDICT_COLOR = {"翻转": "#c0392b", "倾斜": "#d68910", "正常": "#1e8449"}

# 审核优先级：人标真值 > 与 glm 分歧 > 判读错误 > 一致
P_HUMAN, P_DIFF, P_ERR, P_OK = 0, 1, 2, 3

_CSS = """
body{font-family:"Microsoft YaHei",sans-serif;margin:16px;background:#fafafa}
h1{font-size:20px} h2{font-size:16px;margin:22px 0 8px;border-left:4px solid #2c3e50;padding-left:8px}
.count{color:#888;font-size:13px;font-weight:normal}
.grid{display:flex;flex-wrap:wrap;gap:10px}
.cell{width:200px;background:#fff;border:1px solid #ddd;border-radius:6px;padding:6px}
.cell img{width:100%;border-radius:4px}
.cap{font-size:12px;line-height:1.5;margin-top:4px}
.lat{color:#999} .note{color:#555;font-size:13px}
details{margin:6px 0} summary{cursor:pointer;color:#2c3e50;font-weight:bold}
.mark{margin-top:4px;display:flex;gap:4px}
.mark button{flex:1;border:1px solid #bbb;border-radius:4px;background:#fff;
  cursor:pointer;font-size:12px;padding:2px 0}
.mark button.on-flip{background:#c0392b;color:#fff;border-color:#c0392b}
.mark button.on-ok{background:#1e8449;color:#fff;border-color:#1e8449}
.ev{margin-top:4px;display:flex;flex-wrap:wrap;gap:4px;align-items:flex-end}
.ev a{text-align:center;text-decoration:none}
.ev img{width:62px;border:1px solid #c0392b;border-radius:3px;display:block}
.ev span{font-size:10px;color:#888}
.noframe{height:120px;display:flex;align-items:center;justify-content:center;
  background:#f0f0f0;border-radius:4px;color:#999;font-size:12px}
#export{position:fixed;right:18px;bottom:18px;background:#2c3e50;color:#fff;
  border:none;border-radius:6px;padding:10px 18px;font-size:14px;cursor:pointer;
  box-shadow:0 2px 8px rgba(0,0,0,.3)}
"""


def frame_paths(row, root=DATE_ROOT):
    """行 → (中帧路径, [首/尾帧额外路径])。与 blindbench 同一解析。

    root 可注入（#157）：测试用临时目录造 date/slug/_frames 夹具，不依赖 E: 真实数据。
    """
    base = os.path.join(root, row["date"], "video", "每日新中国b站", "流水线",
                        row["slug"], "_frames")
    mid = os.path.join(base, f"{row['shot']}.jpg")
    extra = sorted(p for p in glob.glob(os.path.join(base, f"{row['shot']}_*.jpg")))
    return (mid if os.path.exists(mid) else (extra[0] if extra else None)), extra


def file_url(p):
    """本机口径路径 → 浏览器 file:// URL。

    审核页在 Windows 浏览器里打开，而帧在 E: 盘：WSL 侧的 /mnt/e/… 直接拼
    file:/// 会变成 file:////mnt/e/…（Windows 解析成 UNC \\mnt\e\…，缩略图全空）。
    先 to_win_path 换回盘符口径再补 file:///，双端产出同一条可用 URL（#157）。
    """
    win = dj.to_win_path(p).replace(chr(92), "/")
    # 盘符口径补成 file:///<盘>:/…；POSIX 绝对路径本身以 / 开头，用 file:// + 路径
    # 得到 file:///tmp/…（不能一律拼 file:///，否则 /tmp/x 会变成 file:////tmp/x）。
    return ("file:///" + win) if not win.startswith("/") else ("file://" + win)


def priority(row, frame):
    if row.get("human"):
        return P_HUMAN
    if row.get("err"):
        return P_ERR
    return P_DIFF if row["local"] != row["baseline"] else P_OK


def cell(row, frame):
    """盲比格。frame 可能为 None（该段帧不在盘）——占位而不是 AttributeError（#157）。"""
    if frame:
        thumb = (f'<a href="{file_url(frame)}"><img src="{file_url(frame)}" '
                 f'loading="lazy" alt="{row["shot"]}"></a>')
    else:
        thumb = '<div class="noframe">帧不在盘</div>'
    base_c = VERDICT_COLOR.get(row["baseline"], "#555")
    if row.get("err"):
        local = f'<span style="color:#777">ERR {row["err"][:40]}</span>'
    else:
        local = f'<b style="color:{VERDICT_COLOR.get(row["local"], "#555")}">{row["local"]}</b>'
    tag = ' <span style="background:#8e44ad;color:#fff;border-radius:3px;padding:0 4px">人标真值</span>' if row.get("human") else ""
    return (f'<div class="cell">{thumb}'
            f'<div class="cap">{row["date"]} {row["shot"]}{tag}<br>'
            f'glm基线 <b style="color:{base_c}">{row["baseline"]}</b> → 本地 {local}'
            f'<br><span class="lat">{row["latency_s"]:.1f}s</span></div></div>')


def section(title, rows, note=""):
    if not rows:
        return ""
    cells = "".join(cell(r, r["_frame"]) for r in rows)
    n = len(rows)
    return (f'<h2>{title} <span class="count">{n}</span></h2>'
            f'{f"<p class=note>{note}</p>" if note else ""}'
            f'<div class="grid">{cells}</div>')


# ── 日常判定视图（T9 #118）─────────────────────────────────────────────
def daily_rows(slug_dir, report, dataset=None):
    """report.json s08.vision → 逐段审核行。返回 (rows, date, slug)。
    行：{shot, final, vlm_conf, probe, probe_conf, probe_agree, reasons,
         frame, flagged(数据集现有真值), spot}"""
    slug = os.path.basename(os.path.normpath(slug_dir))
    m = re.search(r"(\d{8})", slug)
    date = report.get("date") or (
        f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else "")
    vis = (report.get("s08") or {}).get("vision") or {}
    flipped = set(vis.get("flipped") or [])
    tilted = set(vis.get("tilted") or [])
    jf = {str(x).split(":")[0] for x in (vis.get("judge_failed") or [])}
    named = {}  # 点名段 → 缘由
    for x in (vis.get("clip_escalated") or []):
        named.setdefault(str(x).split(":")[0], []).append(f"CLIP分歧:{x}")
    for x in (vis.get("low_conf") or []):
        named.setdefault(str(x).split(":")[0], []).append(f"低置信:{x}")
    named = {k: "；".join(v) for k, v in named.items()}
    spot = {str(x).split(":")[0] for x in (vis.get("spot_check") or [])}
    conf = vis.get("confidence") or {}
    shadow = {r["sid"]: r for r in (vis.get("probe_shadow") or [])}
    ev_map = rd.evidence_by_sid(vis)  # T11 证据帧（单一解析口，缺盘已过滤）
    known = {(r.get("date"), r.get("slug"), r.get("shot")): r.get("user_flagged")
             for r in rd.load(dataset or rd.DATASET)}
    frames_dir = os.path.join(slug_dir, "_frames")
    rows = []
    for fp in sorted(glob.glob(os.path.join(frames_dir, "shot*.jpg"))
                     + glob.glob(os.path.join(frames_dir, "shot*.png"))):
        sid = os.path.splitext(os.path.basename(fp))[0]
        if "_" in sid:  # shotNN_first/_last 变体挂在主段下（首格用中帧）
            continue
        final = ("翻转" if sid in flipped else
                 "倾斜" if sid in tilted else
                 "未判" if sid in jf else "正常")
        sh = shadow.get(sid) or {}
        reasons = ([named[sid]] if sid in named else []) + \
                  (["抽检"] if sid in spot else [])
        rows.append({"shot": sid, "final": final,
                     "vlm_conf": conf.get(sid),
                     "probe": sh.get("probe"), "probe_conf": sh.get("conf"),
                     "probe_agree": (sh.get("probe") == sh.get("final"))
                     if sh.get("probe") else None,
                     "reasons": reasons, "spot": sid in spot,
                     "evidence": ev_map.get(sid, []),
                     "frame": fp,
                     "flagged": known.get((date, slug, sid))})
    return rows, date, slug


def mark_cell(r):
    """带标注按钮的格子（file:// 直引原图 + 翻转/未翻转二选一 + 导出收集）。"""
    fc = VERDICT_COLOR.get(r["final"], "#555")
    conf = f" conf={r['vlm_conf']:.2f}" if r.get("vlm_conf") is not None else ""
    probe = ""
    if r.get("probe"):
        pc = f"({r['probe_conf']:.2f})" if r.get("probe_conf") is not None else ""
        agree = "同" if r.get("probe_agree") else "⚠异"
        probe = f"<br>探针 <b style='color:{VERDICT_COLOR.get(r['probe'], '#555')}'>" \
                f"{r['probe']}</b>{pc} {agree}"
    cur = ""
    if r.get("flagged") is True:
        cur = ' <span style="background:#c0392b;color:#fff;border-radius:3px;padding:0 4px">库:翻转</span>'
    elif r.get("flagged") is False:
        cur = ' <span style="background:#1e8449;color:#fff;border-radius:3px;padding:0 4px">库:未翻</span>'
    reasons = f"<br><span class='lat'>{'；'.join(r['reasons'])}</span>" if r["reasons"] else ""
    ev = ""
    if r.get("evidence"):  # T11：判翻证据帧（打回重烧前的定罪帧，逐轮）
        ev = '<div class="ev">' + "".join(
            f'<a href="{file_url(e["frame"])}" title="证据帧 '
            f'{e["round"]}"><img src="{file_url(e["frame"])}" '
            f'loading="lazy"><span>{e["round"]}</span></a>'
            for e in r["evidence"]) + "</div>"
    return (f'<div class="cell" id="cell-{r["shot"]}">'
            f'<a href="{file_url(r["frame"])}"><img src="{file_url(r["frame"])}" '
            f'loading="lazy" alt="{r["shot"]}"></a>'
            f'<div class="cap">{r["shot"]} → <b style="color:{fc}">{r["final"]}</b>'
            f'{conf}{probe}{cur}{reasons}</div>{ev}'
            f'<div class="mark">'
            f'<button onclick="mark(this,\'{r["shot"]}\',true)">翻转</button>'
            f'<button onclick="mark(this,\'{r["shot"]}\',false)">未翻转</button>'
            f'</div></div>')


_MARK_JS = """
var MARKS = {};
function mark(btn, shot, val){
  MARKS[shot] = val;
  var box = btn.parentNode;
  var bs = box.getElementsByTagName('button');
  for (var i=0;i<bs.length;i++){
    bs[i].className = '';
    if (bs[i] === btn) bs[i].className = val ? 'on-flip' : 'on-ok';
  }
}
function exportMarks(){
  var slug = document.body.getAttribute('data-slug');
  var date = document.body.getAttribute('data-date');
  var marks = Object.keys(MARKS).map(function(k){
    return {date: date, slug: slug, shot: k, flagged: MARKS[k]};
  });
  if (!marks.length){ alert('先点格子下的 翻转/未翻转 按钮'); return; }
  var blob = new Blob([JSON.stringify({slug: slug, date: date, marks: marks},
                                      null, 1)], {type: 'application/json'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'daily-review-' + date + '.json';
  a.click();
}
"""


def daily_page(slug_dir, out, dataset=None):
    """日常判定审核页：点名/翻转/失败/探针分歧置顶，正常段折叠；按钮导出 JSON。"""
    rp_fp = os.path.join(slug_dir, "_driver", "report.json")
    report = json.load(open(rp_fp, encoding="utf-8"))
    rows, date, slug = daily_rows(slug_dir, report, dataset)
    vis = (report.get("s08") or {}).get("vision") or {}
    forms = (vis.get("form"), vis.get("calibrated"))
    parts = [f'<html><head><meta charset="utf-8"><style>{_CSS}</style></head>'
             f'<body data-slug="{slug}" data-date="{date}">']
    parts.append(f"<h1>driver 日常判定审核 — {slug}</h1>"
                 f"<p class=note>数据源：_driver/report.json s08.vision｜形态 "
                 f"{forms[0] or '?'}（校准{'开' if forms[1] else '关'}）｜"
                 "点格子下的 翻转/未翻转 按钮标注真值，完事点右下角「导出结果」，"
                 "再用 --apply 写回数据集。真值库现有标注以「库:*」徽标显示。</p>")

    def bucket(name, pred, note="", collapsed=False):
        rs = [r for r in rows if pred(r)]
        if not rs:
            return
        cells = "".join(mark_cell(r) for r in rs)
        if collapsed:
            parts.append(f'<details><summary>{name}（{len(rs)}）—— {note}</summary>'
                         f'<div class="grid">{cells}</div></details>')
        else:
            parts.append(f'<h2>{name} <span class="count">{len(rs)}</span></h2>'
                         f'{f"<p class=note>{note}</p>" if note else ""}'
                         f'<div class="grid">{cells}</div>')

    named_set = {r["shot"] for r in rows if r["reasons"]}
    bucket("① 机判翻转段", lambda r: r["final"] == "翻转",
           "重烧候选；若画面其实正常 = 假翻转（标「未翻转」回填，防无谓重烧积累）")
    bucket("② 判读失败段", lambda r: r["final"] == "未判", "未经机检，重点核对")
    bucket("③ 点名段（CLIP分歧/低置信/抽检）", lambda r: bool(r["reasons"]),
           "宁误报不漏报的机检点名；确认后标真值")
    bucket("④ 探针分歧段", lambda r: r.get("probe_agree") is False,
           "探针与主判不一致（T9 影子对照）——分歧方向是探针训练数据的最有价值来源")
    bucket("⑤ 倾斜段", lambda r: r["final"] == "倾斜",
           "机检判倾斜（可能刻意构图）；标注按是否翻转记，倾斜本身不进真值库")
    bucket("⑥ 正常段（抽查即可）",
           lambda r: r["final"] == "正常" and r["shot"] not in named_set,
           "负类真值的主要来源：确认正常就标「未翻转」", collapsed=True)
    parts.append(f"<script>{_MARK_JS}</script>"
                 '<button id="export" onclick="exportMarks()">导出结果</button>'
                 "</body></html>")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    n_flagged = sum(1 for r in rows if r.get("flagged") is not None)
    print(f"日常审核页 → {os.path.abspath(out)}（{len(rows)} 段，库内已有真值 "
          f"{n_flagged} 段）")
    print("浏览器打开 → 标注 → 右下角「导出结果」→ "
          "python scripts/vision_bench_review.py --apply 下载的.json")
    return 0


def apply_marks(result_fp, dataset=None):
    """导出的标注 JSON → 机械回填 rotation-dataset.jsonl（只动真值字段）。"""
    d = json.load(open(result_fp, encoding="utf-8"))
    marks = [(m["date"], m["slug"], m["shot"], m["flagged"])
             for m in d.get("marks", []) if isinstance(m.get("flagged"), bool)]
    if not marks:
        print(f"{result_fp} 无可回填标注（flagged 必须是 true/false）")
        return 1
    src = f"daily-review-{d.get('date') or time.strftime('%Y%m%d')}"
    updated, missing = rd.set_user_flag(marks, dataset or rd.DATASET, source=src)
    print(f"回填 {len(updated)} 行（source={src}）→ {dataset or rd.DATASET}")
    if missing:
        print(f"⚠️ {len(missing)} 行数据集无对应行（先跑该期 driver 采集）："
              f"{missing[:5]}")
    print("复核：python scripts/rotation_groundtruth.py stats")
    return 0


def main():
    ap = argparse.ArgumentParser(description="判读人工审核页（盲比/日常判定/回填）")
    ap.add_argument("--jsonl", default=None, help="盲比模式：指定 jsonl")
    ap.add_argument("--out", default=None)
    ap.add_argument("--daily", default=None, metavar="SLUG目录",
                    help="日常模式：driver slug 目录（读 _driver/report.json）")
    ap.add_argument("--apply", default=None, metavar="结果.json",
                    help="回填模式：--daily 导出的标注 JSON 写回数据集")
    ap.add_argument("--dataset", default=None, help="回填目标数据集（缺省仓内）")
    a = ap.parse_args()
    if a.apply:
        return apply_marks(a.apply, a.dataset)
    if a.daily:
        out = a.out or os.path.join(HERE, "..", "_test",
                                    f"daily-review-{os.path.basename(os.path.normpath(a.daily))}.html")
        return daily_page(a.daily, out)

    jsonl = a.jsonl or sorted(glob.glob(os.path.join(HERE, "..", "docs", "spikes",
                                                     "vision-blindbench-*.jsonl")))[-1]
    out = a.out or os.path.join(HERE, "..", "_test",
                                os.path.basename(jsonl).replace(".jsonl", "-review.html"))
    seen = {}
    for line in open(jsonl, encoding="utf-8"):
        d = json.loads(line)
        seen[(d["tier"], d["date"], d["slug"], d["shot"])] = d  # 去重（防历史双计）
    tiers = defaultdict(list)
    for (_, t, _, _), d in seen.items() if False else seen.items():
        tiers[d["tier"]].append(d)

    parts = [f'<html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>']
    parts.append("<h1>盲比判定人工审核页</h1>"
                 f"<p class=note>数据源：{os.path.basename(jsonl)}｜判读口径：temperature=0、"
                 f"长边 896 缩图、max_tokens 200（与 driver 判读面同 prompt 同解析）｜"
                 "点击缩略图看原图；缩略图懒加载，首次打开请下滑等待。</p>")

    for t in TIER_ORDER:
        rows = tiers.get(t) or []
        if not rows:
            continue
        for r in rows:
            r["_frame"], r["_extra"] = frame_paths(r)
        parts.append(f"<hr><h1>{TIER_NAME.get(t, t)}</h1>")
        buckets = defaultdict(list)
        for r in rows:
            buckets[priority(r, r["_frame"])].append(r)
        parts.append(section("① 人标真值行（翻转金标准，重点核对）",
                             sorted(buckets[P_HUMAN], key=lambda r: r["shot"]),
                             "user_flagged=true 的人审翻转行；本地判非翻转 = 漏检。注意中帧可能"
                             "不带旋转（首/尾帧链接在同格说明外的目录，点格内图开原图后可手翻同目录 shotNN_first/_last)。"))
        diff = sorted(buckets[P_DIFF], key=lambda r: (r["local"] != "翻转", r["date"]))
        parts.append(section("② 与 glm 基线分歧（本地判翻转 = 假翻转候选或 glm 漏报，次重点）",
                             diff,
                             "本地判「翻转」的格子：若画面确实正着 = 假翻转（会触发无谓重烧）；"
                             "若画面确实歪/横 = glm 漏报，本地对了。本地判「倾斜/正常」而 glm 翻转的格子同样要看。"))
        parts.append(section("③ 判读错误（服务异常/解析失败）", buckets[P_ERR]))
        ok_rows = buckets[P_OK]
        if ok_rows:
            inner = "".join(cell(r, r["_frame"]) for r in ok_rows)
            parts.append(f'<details><summary>④ 与 glm 一致的行（{len(ok_rows)}，抽查即可）</summary>'
                         f'<div class="grid">{inner}</div></details>')

    parts.append("</body></html>")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    print(f"审核页 → {os.path.abspath(out)}")
    print("浏览器打开即可；重点顺序：① 人标真值行 → ② 各档分歧行（尤其 8B 的 37 个假翻转）→ ④ 抽查")
    return 0


if __name__ == "__main__":
    sys.exit(main())
