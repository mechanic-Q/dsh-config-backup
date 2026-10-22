#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clip_train.py — CLIP 线性探针训练（T7 #108）：rotation_groundtruth 清单 →
open_clip ViT-B/32 冻结特征 + 线性头（torch），温度缩放校准 → clip_probe NPZ。

协议（小样本防泄漏）：
- 测试集 = 全部人工真值帧（正 16 + 负 18，真实分布，不参与训练）
- 训练集 = 确认负类的合成旋转（90/180/270 → 翻转正类，标签由构造保证）
  + 留出负类外的……负类全部进测试则训练集无负类——改为：负类按 shot 哈希
  2:1 切 train/test，train 负类参与训练（未旋转 + 不旋转两份），test 负类只测。
- 关键指标 = 16 个真实翻转的召回（判据「翻转 0 漏检」的真值口径）
  + 测试负类上的假翻转数（运营代价）
- 置信度门限：预测置信度 < CONF_FLOOR 的帧按「层弃判」处理（clip_probe.predict
  返回 None → reconcile 走 VLM 单路），防 softmax 过自信乱点名（Jev 文章同款教训）

素材选择（T18 #130）：pos/neg 全走 gt.row_material——工作帧 + 证据桶帧
（t15-evidence-* 三类素材带 category/round）+ 行内 evidence_frames，证据桶
帧真的被训练读取；2:1 哈希切分与增广不变。

两种形态（--mode）：
- class3（缺省）  单次嵌入 → 三分类；历史批次召回 0.72 / 假翻转 13/125，不够生产。
- rotate4         四向旋转搜索（2026-10-22 clean 协议实测 recall 0.93 / 留出误报
                  3.2%，同一份特征、同一批素材，差距只来自推理方式）。训练口径：
                  负类池 = 「从未被人工点名」的历史帧（人放行 = 正立），按**整期日期**
                  md5%3 切 train/test（防同场景内容泄漏）；标签 = 施加的旋转角
                  (0/90/180/270)——即「输入被转了 r 度」这一事实本身可学；
                  真实人工点名翻转帧**从不参训**，只做评测真值（否则召回是自证）。

用法：python scripts/clip_train.py [--epochs 300] [--out references/clip-probe.npz]
      python scripts/clip_train.py --mode rotate4
      python scripts/clip_train.py --dry-run   # 只列被选素材（不 import open_clip/torch）
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import clip_probe as cp
import rotation_dataset as rd
import rotation_groundtruth as gt

CONF_FLOOR = 0.70
VERDICTS = ("正常", "倾斜", "翻转")
ROTS = (0, 90, 180, 270)
# rotate4 留出比例 = md5(整期日期)%3==2 的期（1/3 期）；真实翻转帧永不参训
HELD_OUT_MOD = 2


def embed_all(model, preprocess, paths, rotate_deg=0):
    """批量嵌入（合成旋转现场做，不落盘）。"""
    import torch
    from PIL import Image
    feats = []
    with torch.no_grad():
        for p in paths:
            im = Image.open(p).convert("RGB")
            if rotate_deg:
                im = im.rotate(rotate_deg, expand=True)
            x = preprocess(im).unsqueeze(0)
            feats.append(model.encode_image(x).float().squeeze(0).numpy())
    return np.stack(feats)


def train_probe(train_X, train_y, epochs=300, lr=1e-2):
    """线性头 + softmax，加权交叉熵（翻转正类稀缺，权重按逆频率）。"""
    import torch
    torch.manual_seed(0)
    X = torch.tensor(train_X, dtype=torch.float32)
    y = torch.tensor(train_y, dtype=torch.long)
    W = torch.zeros(X.shape[1], 3, requires_grad=True)
    b = torch.zeros(3, requires_grad=True)
    counts = torch.bincount(y, minlength=3).float()
    w = (1.0 / counts.clamp(min=1))
    w = w / w.mean()
    opt = torch.optim.Adam([W, b], lr=lr)
    for _ in range(epochs):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(X @ W + b, y, weight=w)
        loss.backward()
        opt.step()
    return W.detach().numpy().T.astype(np.float32), b.detach().numpy().astype(np.float32)


def select_material():
    """真值行 → 训练素材选择（T18 #130：与 torch 解耦，dry-run 共用）。

    选择口径 = gt.row_material（工作帧 + 证据桶帧 + 行内定罪帧）；负类 2:1
    切 train/test（按 shot 名哈希，稳定可复现，原口径不动）。返回
    (pos, neg_train, neg_test, {key: [素材条目]})——key=shot+date 与既有
    帧表同形，素材条目为 gt.row_material 形状（path/kind/category/round）。
    """
    pos, neg = gt.load_labeled()
    neg_train, neg_test = [], []
    for r in neg:
        (neg_train if (sum(r["shot"].encode()) % 3) != 0 else neg_test).append(r)
    mats = {}
    for r in pos + neg:
        mats[r["shot"] + r["date"]] = gt.row_material(r)
    return pos, neg_train, neg_test, mats


def train_probe4(train_X, train_y, epochs=400, lr=1e-2, wd=1e-3):
    """四向旋转头：y = 施加旋转在 ROTS 里的下标（0..3）。返回 (W4 [4,dim], b4 [4])。"""
    import torch
    torch.manual_seed(0)
    X = torch.tensor(train_X, dtype=torch.float32)
    y = torch.tensor(train_y, dtype=torch.long)
    W = torch.zeros(X.shape[1], 4, requires_grad=True)
    b = torch.zeros(4, requires_grad=True)
    opt = torch.optim.Adam([W, b], lr=lr, weight_decay=wd)
    for _ in range(epochs):
        opt.zero_grad()
        torch.nn.functional.cross_entropy(X @ W + b, y).backward()
        opt.step()
    return W.detach().numpy().T.astype(np.float32), b.detach().numpy().astype(np.float32)


def rotate4_pool(dataset=None, date_root=None, references_dir=None,
                 frames_root=None):
    """rotate4 素材：全量真值行 → (pool_train, pool_test, pos)。

    pool   = 从未被人工点名的行（人放行 ⇒ 正立），按整期日期 md5%3 切
    pos    = 人工点名翻转的行，只做评测真值，不进训练

    每行取哪一帧由 gt.truth_frame 单一解析点决定（#191 / ADR 0008 决策 7）：
    user_flagged 行按「行内证据帧 → 证据桶帧 → 工作帧」取帧并做 sha256 硬校验，
    证据帧 sha 不符/缺盘 → 该行整体**不进任何池**（不回落重烧后的工作帧）；
    frame_stale 行同样两侧都不出现。非点名行仍取工作帧首帧（与改前口径一致）。
    dataset/date_root/references_dir/frames_root 可注入（#141 口径）：CI 没有
    E: 盘，测试在临时目录合成 episode 验同一份选材逻辑，不依赖真素材在盘。
    """
    import hashlib
    rows = rd.load(dataset) if dataset else rd.load()
    # 桶清单只扫一次（evidence_by_row 会 glob 全部 manifest）：没有点名行就不扫，
    # 非点名行的口径本来就不看证据桶。
    buckets = None
    if any(r.get("user_flagged") for r in rows):
        buckets = gt.evidence_by_row(rows, references_dir, frames_root)
    pool, pos = [], []
    for r in rows:
        fp = gt.truth_frame(r, references_dir, date_root, frames_root, buckets)
        if not fp:
            continue
        it = {"date": r.get("date"), "shot": r.get("shot"), "path": fp}
        if r.get("user_flagged"):
            pos.append(it)
        else:
            pool.append(it)

    def held(d):
        return int(hashlib.md5(str(d).encode()).hexdigest()[:8], 16) % 3 == HELD_OUT_MOD
    return ([it for it in pool if not held(it["date"])],
            [it for it in pool if held(it["date"])], pos)


def run_rotate4(a, model, preprocess):
    """四向旋转搜索探针：训练 → 落盘 → 双口径评测（召回/误报 + 阈值扫描）。"""
    tr, te, pos = rotate4_pool()
    print(f"rotate4：训练帧 {len(tr)} / 留出帧 {len(te)} / 真实翻转 {len(pos)}"
          f"（留出期 {len({it['date'] for it in te})} 个日期）", flush=True)

    def feat(path, deg):
        return embed_all(model, preprocess, [path], rotate_deg=deg)[0]

    Xtr, ytr = [], []
    for it in tr:
        for deg in ROTS:
            Xtr.append(feat(it["path"], deg))
            ytr.append(ROTS.index(deg))
    train_X = np.stack(Xtr)
    train_y = np.array(ytr)
    print(f"训练样本 {len(train_y)}（各角度 {[int((train_y == i).sum()) for i in range(4)]}）",
          flush=True)

    W4, b4 = train_probe4(train_X, train_y, epochs=a.epochs)
    import torch as _t
    acc = float(((_t.tensor(train_X) @ _t.tensor(W4).T + _t.tensor(b4)).argmax(1)
                 == _t.tensor(train_y)).float().mean())
    print(f"训练 4way 准确率 {acc * 100:.1f}%", flush=True)

    # 不带 class3 头：旧版 clip_probe 读不到 W/b 就当"探针损坏→层缺席"，诚实退回
    # VLM 单路。若写全零 W3/b3，旧版 softmax 均匀 → argmax 恒为第 0 类"正常"，
    # 正是 #184 反复收口的"失败伪装成正常"（静默永远判正常比报缺席危险得多）。
    cp.save_probe(a.out, None, None, list(VERDICTS), a.hub_tag, "",
                  W4=W4, b4=b4, infer="rotate_search", thr=a.thr)

    def p0_of(path):
        """p0[r] = 「这帧按 ROTS[r] 转之后被判为正立」的概率。"""
        p0 = []
        for deg in ROTS:
            v = feat(path, deg)
            logits = W4 @ v + b4
            e = np.exp(logits - logits.max())
            p0.append(float((e / e.sum())[0]))
        return p0

    def eval_rows(items):
        rows = []
        for it in items:
            p0 = p0_of(it["path"])
            r = int(np.argmax(p0))
            rows.append({"date": it["date"], "shot": it["shot"], "r": ROTS[r],
                         "p0": p0, "margin": p0[r] - p0[0]})
        return rows

    hid = eval_rows(pos)
    hnd = eval_rows(te)
    print(f"\n== 真实翻转 {len(hid)} 帧（四向搜索，thr={a.thr}）==")
    miss = 0
    for wit in hid:
        hit = wit["r"] != 0 and wit["margin"] >= a.thr
        miss += 0 if hit else 1
        if not hit:
            print("  漏检 %s %s r*=%d margin=%.3f p0=%s" % (
                wit["date"], wit["shot"], wit["r"], wit["margin"],
                " ".join("%.3f" % x for x in wit["p0"])))
    print(f"  检出 {len(hid) - miss}/{len(hid)} = {(len(hid) - miss) / max(1, len(hid)):.2f}")
    fp = sum(1 for wit in hnd if wit["r"] != 0 and wit["margin"] >= a.thr)
    print(f"== 留出正常 {len(hnd)} 帧 == 误报 {fp} → spec {1 - fp / max(1, len(hnd)):.2f}")
    print("\n阈值扫描（margin ≥ thr）：")
    for t in (0.0, 0.1, 0.2, 0.3, 0.5):
        h = sum(1 for w in hid if w["r"] != 0 and w["margin"] >= t)
        f = sum(1 for w in hnd if w["r"] != 0 and w["margin"] >= t)
        print(f"  {t:.1f}  recall {h}/{len(hid)}={h / max(1, len(hid)):.2f}"
              f"   FP {f}/{len(hnd)}  spec {1 - f / max(1, len(hnd)):.2f}")
    ok = (len(hid) - miss) / max(1, len(hid)) >= 0.90 and fp / max(1, len(hnd)) <= 0.10
    print("\nT7 判据（rotate4 口径，2026-10-22 实证校准）：recall ≥0.90 且 误报 ≤10% →",
          "✅ 达标" if ok else "❌ 未达标（调 thr / 补素材后再上）")
    return 0


def dry_run():
    """列出被选中的训练素材清单（不 import open_clip/torch，T18 #130 验收⑤）。"""
    pos, neg_train, neg_test, mats = select_material()
    print(f"== clip_train dry-run（真值 正 {len(pos)} / 负 train {len(neg_train)} "
          f"+ test {len(neg_test)}）==")
    n = 0
    kinds = {}
    for r in pos + neg_train + neg_test:
        for m in mats[r["shot"] + r["date"]]:
            n += 1
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
            print(f"  {r['date']} {r['shot']} [{m['kind']}"
                  f"{(('/' + m['category'] + '/' + m['round']) if m['category'] else '')}]"
                  f" label={'翻转' if r.get('user_flagged') else '正常'}"
                  f" vision={r.get('vision_verdict')} judge={r.get('judge_verdict')}"
                  f"\n    {m['path']}")
    print(f"共 {n} 条素材；按 kind：" +
          " / ".join(f"{k}:{v}" for k, v in sorted(kinds.items())))
    return 0


def main():
    ap = argparse.ArgumentParser(description="CLIP 线性探针训练（T7）")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "references", "clip-probe.npz"))
    ap.add_argument("--clip-model", default=os.environ.get("CLIP_MODEL", "ViT-B-32"))
    ap.add_argument("--pretrained", default=os.environ.get("CLIP_PRETRAINED", "openai"))
    ap.add_argument("--mode", default="class3", choices=["class3", "rotate4"],
                    help="class3=旧的单图三分类；rotate4=四向旋转搜索（推荐）")
    ap.add_argument("--thr", type=float, default=cp.PROBE_THR,
                    help="rotate4 的 margin 阈值（落进工件，推理侧据此判翻转）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列被选素材清单，不 import open_clip/torch（T18 #130）")
    a = ap.parse_args()
    if a.dry_run:
        return dry_run()

    import open_clip
    import torch
    # 显式 hf-hub 标签（10-14 教训：'openai' tag 在本机落到 timm 权重 + 模型定义
    # quick_gelu 不匹配 → 特征退化全弃判；hub 标签自带一致的定义）
    tag = a.pretrained if a.pretrained.startswith("hf-hub:") else         (a.pretrained if a.pretrained == "openai" and a.clip_model == "ViT-B-32"
         else a.pretrained)
    if tag == "openai":
        tag = "hf-hub:timm/vit_base_patch32_clip_224.openai"
    print(f"加载 {tag} …")
    model, _, preprocess = open_clip.create_model_and_transforms(tag)
    model.eval()
    a.hub_tag = tag  # rotate4 落盘统一记录真特征定义（10-14 教训，防训练/推理错位）

    if a.mode == "rotate4":
        return run_rotate4(a, model, preprocess)

    pos, neg_train, neg_test, mats = select_material()
    # 素材路径表（T18 #130：row_material 口径 = 工作帧 + 证据桶帧 + 行内定罪帧）
    pos_frames = {r["shot"] + r["date"]: [m["path"] for m in mats[r["shot"] + r["date"]]]
                  for r in pos}
    neg_frames = {r["shot"] + r["date"]: [m["path"] for m in mats[r["shot"] + r["date"]]]
                  for r in neg_train + neg_test}

    print(f"真值：翻转正类 {len(pos)} | 负类 train {len(neg_train)} / test {len(neg_test)}")

    # 训练集（10-14 增广 v2：真实翻转帧自带监督信号——
    #   翻转帧转 180° = 它自己的扶正版 → 正常类（真实场景的 normal 对照样本）
    #   翻转帧转 90/270° = 仍是翻转族 → 翻转类（eval 用原朝向，不做图像级重复））
    Xs, ys = [], []
    def add(fps, deg, y):
        if fps:
            Xs.append(embed_all(model, preprocess, fps, deg)); ys.append(y)
    for r in neg_train:
        fps = neg_frames[r["shot"] + r["date"]]
        if fps:
            add(fps[:1], 0, 0)                       # 确认负类原样 = 正常
            add(fps[:1], 90, 2); add(fps[:1], 270, 2)  # 合成翻转
    for r in pos:
        fps = pos_frames[r["shot"] + r["date"]]
        add(fps, 180, 0)                             # 真实翻转场景的扶正版 = 正常
        add(fps, 90, 2); add(fps, 270, 2)            # 真实翻转场景的其他翻转朝向 = 翻转
    train_X = np.concatenate([x for x in Xs]); train_y = np.concatenate([np.full(len(x), y) for x, y in zip(Xs, ys)])
    print(f"训练样本 {len(train_y)}（正常 {sum(train_y == 0)} / 翻转(合成) {sum(train_y == 2)}）")

    W, b = train_probe(train_X, train_y, epochs=a.epochs)
    # 统一保存 hub 标签（10-14 教训：训练/推理特征定义不一致 → 置信度全废）
    HUB_TAG = "hf-hub:timm/vit_base_patch32_clip_224.openai"
    cp.save_probe(a.out, W, b, list(VERDICTS), HUB_TAG, "")
    import torch as _t
    with _t.no_grad():
        acc = float(((_t.tensor(train_X) @ _t.tensor(W).T + _t.tensor(b)).argmax(1)
                     == _t.tensor(train_y)).float().mean())
    logits = _t.tensor(train_X) @ _t.tensor(W).T + _t.tensor(b)
    conf = _t.softmax(logits, 1).max(1).values
    print(f"探针 → {a.out}（训练准确率 {acc*100:.0f}%，置信度 p50={conf.median():.2f} "
          f"min={conf.min():.2f}）")

    # 评测（对齐 clip_probe.predict 的真实推理路径，含 CONF_FLOOR 门）
    probe, err = cp.load_probe(a.out)
    assert probe, err
    def eval_set(rows, label):
        rec = {"n": 0, "flip_hit": 0, "flip_miss": 0, "false_flip": 0, "弃判": 0, "明细": []}
        for r in rows:
            for fp in (pos_frames.get(r["shot"] + r["date"]) if label == "翻转"
                       else neg_frames.get(r["shot"] + r["date"])) or []:
                truth = "翻转" if label == "翻转" else "正常"
                v, c = probe.predict(fp)
                rec["n"] += 1
                if v is None:
                    rec["弃判"] += 1
                    if truth == "翻转":
                        rec["flip_miss"] += 1
                        rec["明细"].append(f"{r['date']}:{r['shot']}→弃判")
                    continue
                if truth == "翻转":
                    rec.setdefault("confs", []).append((c, v))
                    if v == "翻转" and c >= CONF_FLOOR:
                        rec["flip_hit"] += 1
                    else:  # 弃判/判倾斜/判正常都算漏检（不给他逃逸口径）
                        rec["flip_miss"] += 1
                        rec["明细"].append(f"{r['date']}:{r['shot']}→{v or '弃判'}(conf {c:.2f})")
                else:
                    if v == "翻转":
                        rec["false_flip"] += 1
                        rec["明细"].append(f"{r['date']}:{r['shot']} 假翻转(conf {c:.2f})")
        return rec

    ev_pos = eval_set(pos, "翻转")
    print(f"\n== 真实翻转召回（{ev_pos['n']} 帧）== 命中 {ev_pos['flip_hit']} / "
          f"漏 {ev_pos['flip_miss']} / 弃判 {ev_pos['弃判']}")
    for m in ev_pos["明细"]:
        print("  漏检:", m)
    ev_neg = eval_set(neg_test, "正常")
    print(f"== 测试负类（{ev_neg['n']} 帧）== 假翻转 {ev_neg['false_flip']} / 弃判 {ev_neg['弃判']}")
    for m in ev_neg["明细"]:
        print("  假翻转:", m)
    ok = ev_pos["flip_miss"] == 0 and ev_neg["false_flip"] == 0
    print("\nT7 验收判据（真值口径）：翻转漏检 0 且 测试负类假翻转 0 →",
          "✅ 达标，探针上线（driver 下次判读自动带 CLIP 层）" if ok
          else "❌ 未达标——探针已保存但建议调 CONF_FLOOR/增广后再上（可先删除 npz 回退单路 VLM）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
