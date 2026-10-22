#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vision_blindbench.py — 模型定档盲比（#102 AC2）：本地三档 vs 存量 glm-4.6v 结论。

评测集 = rotation-dataset.jsonl 中「有基线结论（≠未判）且帧图在盘」的行，
随每日增长。判据（spec 拍板）：翻转 0 漏检 且 总体一致率 ≥98%，选最小达标档；
不达标也要在报告里写明差多少。

T8 #117 扩展 `--form`：json（缺省，#102 原语义）/ jev（Jev 形态投影，附置信度）/
both（同行双形态背靠背 → 迁移一致率 + 延迟对比，AC「Jev↔JSON 一致率 ≥98%、
延迟 ≤70%」）。行记录带 form 字段（存量行视作 json），断点续跑键含 form。

流程：逐档切服务（probe→stop→launch→wait_ready）→ 逐帧顺序判读（延迟口径干净）
→ 与基线比对 → docs/spikes/vision-blindbench-<日期>.md + _test 原始 jsonl（可续跑）。

用法：
  python scripts/vision_blindbench.py                       # 三档全跑（json）
  python scripts/vision_blindbench.py --tiers 4b            # 单档
  python scripts/vision_blindbench.py --limit 5 --dry-run   # 试跑口径
  python scripts/vision_blindbench.py --tiers 4b --form both  # T8 迁移对比
"""
import argparse
import base64
import glob
import json
import os
import sys
import time
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dsh_judge as dj
import vision_common as vc
import vision_server as vs

DATASET = os.path.join(HERE, "..", "references", "rotation-dataset.jsonl")
# 帧根目录（双端同源素材，E: 盘）：拼 Windows 口径绝对路径，读盘前由
# to_host_path 换回本机口径（#157：此前直接把 Windows 盘符交给 os.path.exists，
# WSL 下恒 False → eval_rows() 静默 0 行、退出码仍 0）。单一事实源复用
# dsh_judge.to_host_path，不新增第 5 份宿主判定（#143 收口前按票面临时复用）。
DATE_ROOT_WIN = dj.win_join("E:", "每日新中国")
DATE_ROOT = dj.to_host_path(DATE_ROOT_WIN)
TIER_ORDER = ["2b", "4b", "8b"]
FORMS = ("json", "jev")


norm_verdict = vc.norm_verdict  # 单一事实源（#106 起收敛 vision_common）


def frame_of(row, root=DATE_ROOT):
    """行 → 中帧绝对路径；中帧缺则退首个 <shot>_*.jpg（首/尾帧）；全缺 None。

    root 可注入（#157）：测试用临时目录造 date/slug/_frames 夹具，不依赖 E: 真实
    数据；生产缺省即宿主无关的 DATE_ROOT。
    """
    base = os.path.join(root, row["date"], "video", "每日新中国b站", "流水线",
                        row["slug"], "_frames")
    mid = os.path.join(base, f"{row['shot']}.jpg")
    if os.path.exists(mid):
        return mid
    alt = sorted(glob.glob(os.path.join(base, f"{row['shot']}_*.jpg")))
    return alt[0] if alt else None


def eval_rows(dataset=None, root=None):
    """评测集：有基线（≠未判）+ 帧在盘；user_flagged=True 的人工真值行即使
    glm 未判也纳入（带 human 标记，baseline 取人工「翻转」）——这是判据
    「翻转 0 漏检」里翻转真值的唯一在盘来源（10-14 实况：368 行 glm 基线全正常）。

    dataset / root 可注入（#157）：缺省为仓内数据集 + 宿主无关 DATE_ROOT；
    测试传临时夹具目录即可覆盖「帧路径构建」这条此前无断言的空档。"""
    out = []
    dataset = DATASET if dataset is None else dataset
    root = DATE_ROOT if root is None else root
    for line in open(dataset, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        human = r.get("user_flagged") is True
        if r.get("vision_verdict") not in ("正常", "翻转", "倾斜") and not human:
            continue
        fp = frame_of(r, root=root)
        if fp is None:
            continue
        if human:  # 人工真值优先：glm 未判/误判的行以人标翻转为准
            r = dict(r, _baseline="翻转", _human=True)
        else:
            r = dict(r, _baseline=r["vision_verdict"], _human=False)
        out.append((r, fp))
    return out


def judge_frame(api, model, frame_path, timeout=240, form="json"):
    """单帧判读 → (verdict, elapsed_s, err, conf)。同构 _check_frames 请求体；
    jev 形态走 logprobs 投影（conf = 原始 softmax 置信度，未校准）。"""
    # 缩图预算统一走 judge_body/jev_body(路径)——与 driver 判读面完全同口径
    # （10-14 教训：bench 曾自读原图全分辨率编码，比 driver 配置多算 5 倍
    # token，口径失真）
    body = vc.jev_body(model, frame_path) if form == "jev" \
        else vc.judge_body(model, frame_path)
    t0 = time.time()
    try:
        d = vc.post_chat(api, body, timeout=timeout)
        if form == "jev":
            v, conf = vc.jev_decide(vc.jev_project(d))
            return v, time.time() - t0, "", round(conf, 4)
        verdict = vc.extract_verdict(d["choices"][0]["message"]["content"])
        return norm_verdict(verdict), time.time() - t0, "", None
    except Exception as e:  # noqa: BLE001
        return "", time.time() - t0, str(e)[:120], None


def switch_tier(tier):
    """切档：在线且 alias 对 → 复用；否则停旧→拉新→等就绪。"""
    t, cfg = vs.tier_cfg(tier)
    st = vs.probe()
    if st["online"]:
        if st["alias"] == cfg["alias"]:
            print(f"  [{t}] 服务已在跑 {cfg['alias']}，复用")
            return True
        print(f"  [{t}] 在线但为 {st['alias']}，stop 后重拉")
        vs.stop()
    ok, info = vs.launch(t)
    if not ok:
        print(f"  [{t}] 拉起失败: {info}")
        return False
    rd = vs.wait_ready(t, timeout=600)
    print(f"  [{t}] 就绪={rd['ready']} 载入={rd['elapsed']}s")
    return rd["ready"]


def main():
    ap = argparse.ArgumentParser(description="三档盲比定档")
    ap.add_argument("--tiers", default="2b,4b,8b")
    ap.add_argument("--limit", type=int, default=0, help="每档最多判 N 行（试跑）")
    ap.add_argument("--dry-run", action="store_true", help="只列评测集，不判读")
    ap.add_argument("--form", default="json", choices=FORMS + ("both",),
                    help="判读形态：json（#102 原语义）/ jev（T8 投影）/ "
                         "both（同行双形态：迁移一致率 + 延迟对比）")
    a = ap.parse_args()
    forms = list(FORMS) if a.form == "both" else [a.form]

    rows = eval_rows()
    if a.limit:
        rows = rows[:a.limit]
    base_cnt = Counter(r["_baseline"] for r, _ in rows)
    print(f"评测集：{len(rows)} 行（有效基线分布 {dict(base_cnt)}，"
          f"其中人标真值 {sum(1 for r, _ in rows if r['_human'])} 行）"
          f"｜形态：{'+'.join(forms)}")
    if a.dry_run:
        for r, fp in rows:
            tag = "人标" if r["_human"] else "glm "
            print(f"  {r['date']} {r['shot']} 基线={r['_baseline']}({tag}) {os.path.basename(fp)}")
        return 0

    tag = time.strftime("%Y%m%d")
    raw_fp = os.path.join(HERE, "..", "_test", f"vision-blindbench-{tag}.jsonl")
    done = set()
    if os.path.exists(raw_fp):  # 断点续跑（键含 form；存量行视作 json）
        for line in open(raw_fp, encoding="utf-8"):
            try:
                d = json.loads(line)
                done.add((d["tier"], d.get("form", "json"),
                          d["date"], d["slug"], d["shot"]))
            except Exception:  # noqa: BLE001
                pass
    raw = open(raw_fp, "a", encoding="utf-8")

    api = os.environ.get("DRIVER_VISION_API", vc.default_api())
    per_tier = {}
    for tier in [t.strip().lower() for t in a.tiers.split(",") if t.strip()]:
        print(f"== 档位 {tier} ==")
        if not switch_tier(tier):
            per_tier[tier] = {"error": "服务未就绪"}
            continue
        _, cfg = vs.tier_cfg(tier)
        for i, (r, fp) in enumerate(rows):
            for form in forms:
                if (tier, form, r["date"], r["slug"], r["shot"]) in done:
                    continue
                v, dt, err, conf = judge_frame(api, cfg["alias"], fp, form=form)
                rec = {"tier": tier, "form": form, "date": r["date"],
                       "slug": r["slug"], "shot": r["shot"],
                       "baseline": r["_baseline"], "human": r["_human"],
                       "local": v, "latency_s": round(dt, 2),
                       "user_flagged": r.get("user_flagged"), "err": err}
                if conf is not None:
                    rec["conf"] = conf
                raw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                raw.flush()
                mark = v or "ERR"
                print(f"  [{i + 1}/{len(rows)}][{form}] {r['shot']} "
                      f"基线={r['_baseline']}{'(人标)' if r['_human'] else ''} "
                      f"本地={mark} {dt:.1f}s"
                      + (f" conf={conf:.2f}" if conf else "")
                      + (f" ERR:{err[:60]}" if err else ""))
        # 汇总单源化：统一从文件重读该档（10-14 教训：live 行+文件重读拼接会双计）
        per_tier[tier] = {}
        form_res = {}
        for form in forms:
            res = []
            for line in open(raw_fp, encoding="utf-8"):
                try:
                    d = json.loads(line)
                    if (d["tier"] == tier and d.get("form", "json") == form
                            and not d.get("err")):
                        res.append(d)
                except Exception:  # noqa: BLE001
                    pass
            form_res[form] = res
            per_tier[tier][form] = summarize(tier, res)
            print(f"  → [{form}] {per_tier[tier][form]['summary']}")
        if len(forms) > 1:
            per_tier[tier]["cmp"] = compare_forms(form_res["json"], form_res["jev"])
            print(f"  → [cmp] {per_tier[tier]['cmp']['summary']}")

    report = render_report(rows, per_tier, forms)
    # 缺省 json 保持 #102 文件名；both/jev 加形态后缀（不覆盖历史定档报告）
    suffix = "" if a.form == "json" else f"-{a.form}"
    out_md = os.path.join(HERE, "..", "docs", "spikes",
                          f"vision-blindbench-{tag}{suffix}.md")
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告 → {out_md}")
    print(render_decision(per_tier, forms))
    return 0


def summarize(tier, res):
    """单档指标。口径（10-14 实况：glm 基线全正常，翻转真值只在人标行）：
    - 一致率：对 glm 有效基线行（人标行不计——glm 在这些行本就未判/误判）
    - 翻转漏检：全部翻转真值行（glm 基线翻转 + 人标翻转）本地判非翻转
    - 假翻转：glm 基线正常行本地判翻转"""
    if not res:
        return {"error": "无结果行"}
    glm_rows = [d for d in res if not d.get("human")]
    n = len(glm_rows)
    agree = sum(1 for d in glm_rows if d["local"] == d["baseline"])
    flip_truth = [d for d in res if d["baseline"] == "翻转"]
    miss_flip = sum(1 for d in flip_truth if d["local"] != "翻转")
    false_flip = sum(1 for d in glm_rows
                     if d["baseline"] == "正常" and d["local"] == "翻转"
                     and not d.get("human"))
    misses = [d for d in flip_truth if d["local"] != "翻转"]
    lats = sorted(d["latency_s"] for d in res)
    p50 = lats[len(lats) // 2] if lats else 0
    p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))] if lats else 0
    m = {"tier": tier, "n": len(res), "n_glm": n, "agree": agree,
         "agree_rate": round(agree / n, 4) if n else 0,
         "flip_truth_n": len(flip_truth),
         "flip_miss": miss_flip, "false_flip": false_flip,
         "miss_shots": [f"{d['date']}:{d['shot']}(判{d['local'] or 'ERR'})" for d in misses],
         "err_n": sum(1 for d in res if d.get("err")),
         "lat_p50": round(p50, 2), "lat_p95": round(p95, 2),
         "human_true_n": sum(1 for d in res if d.get("human")),
         "human_true_hit": sum(1 for d in res
                               if d.get("human") and d["local"] == "翻转")}
    m["summary"] = (f"n={len(res)}(glm基线{n}) 一致率={m['agree_rate'] * 100:.2f}% "
                    f"翻转漏检={miss_flip}/{len(flip_truth)} 假翻转={false_flip} "
                    f"延迟p50={m['lat_p50']}s p95={m['lat_p95']}s")
    return m


def compare_forms(json_rows, jev_rows):
    """同行双形态配对（T8 #117 AC）：Jev↔JSON 一致率 + 延迟对比。
    AC 判据：一致率 ≥98%（防迁移回归）、jev 延迟 ≤70% json（目标单帧）。"""
    j = {(d["date"], d["slug"], d["shot"]): d for d in json_rows}
    e = {(d["date"], d["slug"], d["shot"]): d for d in jev_rows}
    keys = sorted(set(j) & set(e))
    if not keys:
        return {"error": "无可配对行（两形态评测集不相交）",
                "summary": "无可配对行"}
    pairs = [(j[k], e[k]) for k in keys]
    agree = sum(1 for a, b in pairs if a["local"] == b["local"])
    agree_rate = agree / len(pairs)
    lat_j = sorted(a["latency_s"] for a, _ in pairs)
    lat_e = sorted(b["latency_s"] for _, b in pairs)

    def p95(xs):
        return xs[min(len(xs) - 1, int(len(xs) * 0.95))]

    def p50(xs):
        return xs[len(xs) // 2]

    ratio_p50 = (p50(lat_e) / p50(lat_j)) if p50(lat_j) else 0
    ratio_mean = (sum(lat_e) / sum(lat_j)) if sum(lat_j) else 0
    disagree = [f"{k[0]}:{k[2]}(json={j[k]['local']},jev={e[k]['local']})"
                for k in keys if j[k]["local"] != e[k]["local"]]
    ok = agree_rate >= 0.98 and ratio_p50 <= 0.70
    gaps = []
    if agree_rate < 0.98:
        gaps.append(f"一致率 {agree_rate * 100:.2f}%")
    if ratio_p50 > 0.70:
        gaps.append(f"延迟比 {ratio_p50 * 100:.0f}%")
    m = {"n": len(pairs), "agree": agree, "agree_rate": round(agree_rate, 4),
         "lat_json_p50": round(p50(lat_j), 2), "lat_jev_p50": round(p50(lat_e), 2),
         "lat_json_p95": round(p95(lat_j), 2), "lat_jev_p95": round(p95(lat_e), 2),
         "ratio_p50": round(ratio_p50, 3), "ratio_mean": round(ratio_mean, 3),
         "disagree": disagree[:10], "ac_pass": ok,
         "summary": (f"配对 n={len(pairs)} 一致率={agree_rate * 100:.2f}% "
                     f"延迟p50 {p50(lat_j):.2f}s→{p50(lat_e):.2f}s "
                     f"(比 {ratio_p50 * 100:.0f}%) "
                     + ("✅ 达标" if ok else f"❌ 未达标（差在 {'、'.join(gaps)}）"))}
    return m


def render_decision(per_tier, forms=("json",)):
    """定档结论：翻转 0 漏检 + 一致率 ≥98% → 最小达标档；不达标写明差多少。
    per_tier[tier] = {form: metrics, cmp?: 对比}。定档锚 = json 形态（#102 判据）；
    jev 形态给同判据迁移结论；cmp 给 T8 双 AC 判定。"""
    lines = ["\n== 定档结论 =="]
    for form in forms:
        lines.append(f"### 形态 {form}")
        passing = []
        for t in TIER_ORDER:
            tt = per_tier.get(t) or {}
            if not tt:
                continue  # 本轮未跑的档不进结论
            if tt.get("error"):
                lines.append(f"- {t}: 不可评（{tt['error']}）")
                continue
            m = tt.get(form) or {}
            if not m or m.get("error"):
                continue
            ok = m["flip_miss"] == 0 and m["agree_rate"] >= 0.98
            gap = ""
            if not ok:
                gaps = []
                if m["flip_miss"]:
                    gaps.append(f"翻转漏检 {m['flip_miss']}（判据 0）")
                if m["agree_rate"] < 0.98:
                    gaps.append(f"一致率 {m['agree_rate'] * 100:.2f}%"
                                f"（差 {98 - m['agree_rate'] * 100:.2f}pp）")
                gap = "；差在 " + "、".join(gaps)
            else:
                passing.append(t)
            lines.append(f"- {t}: {'✅ 达标' if ok else '❌ 未达标'}（{m['summary']}）{gap}")
        if form == "json" and passing:
            pick = passing[0]  # TIER_ORDER 已按小到大
            lines.append(f"\n**定档：{pick}（最小达标档）**；DRIVER_VISION_MODEL 缺省 → "
                         f"{vs.TIERS[pick]['alias']}")
        elif form == "json":
            lines.append("\n**定档：无档达标**（判据：翻转 0 漏检 + 一致率 ≥98%）——"
                         "保持云端 9router 缺省不翻转，漏检明细见报告表；可评估放宽判据或换 mmproj 量化再验。")
    cmps = [(t, (per_tier.get(t) or {}).get("cmp")) for t in TIER_ORDER]
    cmps = [(t, c) for t, c in cmps if c and not c.get("error")]
    if cmps:
        lines.append("### T8 迁移对比（Jev ↔ JSON，AC：一致率 ≥98% 且延迟 ≤70%）")
        for t, c in cmps:
            lines.append(f"- {t}: {c['summary']}｜延迟p95 "
                         f"{c['lat_json_p95']}s→{c['lat_jev_p95']}s")
            if c["disagree"]:
                lines.append(f"  - 分歧行: {', '.join(c['disagree'])}")
    return "\n".join(lines)


def render_report(rows, per_tier, forms=("json",)):
    head = (f"# 盲比定档报告（vision-blindbench {time.strftime('%Y-%m-%d')}）\n\n"
            f"- 评测集：rotation-dataset 存量帧 {len(rows)} 行"
            f"（日期 {min(r[0]['date'] for r in rows)}~{max(r[0]['date'] for r in rows)}，"
            f"基线 = 存量 glm-4.6v 结论；未判行不计）\n"
            f"- 判据：翻转 0 漏检 且 总体一致率 ≥98%，选最小达标档\n"
            f"- 判读形态：{' + '.join(forms)}"
            f"（vision_common 单一事实源，单帧单问，"
            f"json max_tokens={vc.JUDGE_MAX_TOKENS} / jev max_tokens={vc.JEV_MAX_TOKENS}"
            f"+logprobs 投影）\n")
    body = []
    miss_detail = ["\n## 翻转漏检/误报明细\n"]
    for form in forms:
        tbl = [f"\n## 形态 {form}\n",
               "| 档 | n | glm基线 | 一致 | 一致率 | 翻转真值 | 漏检 | 假翻转 | 判读错误 | 延迟p50 | 延迟p95 | 人标命中 |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for t in TIER_ORDER:
            tt = per_tier.get(t) or {}
            if not tt:
                tbl.append(f"| {t} | - | - | - | - | - | - | - | - | - | - | 本轮未跑 |")
                continue
            if tt.get("error"):
                tbl.append(f"| {t} | - | - | - | - | - | - | - | - | - | - | {tt['error']} |")
                continue
            m = tt.get(form) or {}
            if not m or m.get("error"):
                tbl.append(f"| {t} | - | - | - | - | - | - | - | - | - | - | "
                           f"{(m or {}).get('error', '本形态未跑')} |")
                continue
            tbl.append(f"| {t} | {m['n']} | {m['n_glm']} | {m['agree']} | "
                       f"{m['agree_rate'] * 100:.2f}% | {m['flip_truth_n']} | "
                       f"{m['flip_miss']} | {m['false_flip']} | {m['err_n']} | "
                       f"{m['lat_p50']}s | {m['lat_p95']}s | "
                       f"{m['human_true_hit']}/{m['human_true_n']} |")
            if m.get("miss_shots"):
                miss_detail.append(f"- {form}/{t} 漏检: {', '.join(m['miss_shots'])}")
        body.append("\n".join(tbl))
    cmp_rows = []
    for t in TIER_ORDER:
        c = (per_tier.get(t) or {}).get("cmp")
        if c and not c.get("error"):
            cmp_rows.append(f"| {t} | {c['n']} | {c['agree']} | "
                            f"{c['agree_rate'] * 100:.2f}% | {c['lat_json_p50']}s | "
                            f"{c['lat_jev_p50']}s | {c['ratio_p50'] * 100:.0f}% | "
                            f"{c['ratio_mean'] * 100:.0f}% | {c['lat_json_p95']}s | "
                            f"{c['lat_jev_p95']}s |")
    if cmp_rows:
        body.append("\n## Jev ↔ JSON 迁移对比（T8：AC 一致率 ≥98% 且延迟 ≤70%）\n"
                    "（cmp 按行配对取交集；两侧人标行数不同 = 数据集在两次采集间增长，"
                    "配对判定与延迟对比不受影响）\n"
                    "| 档 | 配对 | 一致 | 一致率 | json p50 | jev p50 | 比值p50 | 比值均值 | json p95 | jev p95 |\n"
                    "|---|---|---|---|---|---|---|---|---|---|\n"
                    + "\n".join(cmp_rows))
    return head + "".join(body) + "\n".join(miss_detail) + render_decision(per_tier, forms)


if __name__ == "__main__":
    sys.exit(main())
