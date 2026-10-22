#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clip_probe.py — CLIP 线性探针推理封装（T4 #106）：open_clip ViT-B/32 图像嵌入
+ 线性分类头，输出映射到与 VLM 相同的三级 verdict 契约 + 置信度。

层定位（spec）：平行兜底层，数据飞轮越用越准（训练 = T7 手动脚本，本模块只管
推理与工件格式）。缺席即降级：探针文件缺失 / open_clip 未装 / 嵌入失败一律
返回 None，driver 行为与现状完全一致（AC2）。

两种推理形态（工件字段 `infer` 决定，老工件无该字段 = class3，行为不变）：
  class3       单次嵌入 → 三分类 argmax（旧口径；批次召回仅 0.72，见 T7 实测）
  rotate_search 四向旋转搜索 —— 把同一帧按 0/90/180/270 各嵌入一次，取
                「被判为正立」概率最大的输入 r*；该帧自身旋转 =(360−r*)%360，
                r*≠0 且 margin=p0[r*]−p0[0] ≥ thr 即判翻转（2026-10-22 clean 协议
                实测 recall 0.93 / 留出误报 3.2%，比 class3 的 0.72 高一个档；同一
                份特征、同一批素材，差距只来自推理方式——不是训练）。

探针工件（NPZ）字段：
  W        float32 [n_classes, dim]   线性头权重（class3）
  b        float32 [n_classes]        偏置（class3）
  W4       float32 [4, dim]          四向旋转头权重（rotate_search）
  b4       float32 [4]               四向偏置；类序 = (0°,90°,180°,270°) 的「施加旋转」
  infer    unicode                   "rotate_search" | 缺省(class3)
  thr      float32                   rotate_search 的 margin 阈值（缺省 0.0=最高召回）
  classes  unicode [n_classes]        类名 = 三级 verdict（正常/倾斜/翻转）
  clip_model / pretrained            嵌入模型标识（记录用）

CLI:
  python clip_probe.py status [--probe 探针.npz]     # 层可用性自检
  python clip_probe.py predict --probe x.npz --image f.jpg
"""
import json
import os

import numpy as np

from vision_common import VERDICT_LEVELS

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROBE = os.path.join(HERE, "..", "references", "clip-probe.npz")
CLIP_MODEL_DEFAULT = "ViT-B-32"
PRETRAINED_DEFAULT = "openai"
# 四向搜索的旋转角序列 + PIL 旋转角映射（PIL rotate 逆时针为正；与训练脚本
# clip_train.py --mode rotate4 的 embed 口径必须一致，否则特征分布错位）
ROTS = (0, 90, 180, 270)
ROT_PIL = {0: 0, 90: -90, 180: 180, 270: 90}
# margin 阈值（可用 VISION_JUDGE_PROBE_THR 覆盖；0.0 = 召回优先，留出误报 3.2%；
# 工件自带 thr 字段时以工件为准，环境变量只在老工件/实验时兜底）
PROBE_THR = float(os.environ.get("VISION_JUDGE_PROBE_THR", "0.10"))


def save_probe(path, W, b, classes, clip_model=CLIP_MODEL_DEFAULT,
               pretrained=PRETRAINED_DEFAULT, W4=None, b4=None, infer="", thr=0.0):
    """训练脚本（T7）落盘接口。W4/b4/infer 只有 rotate_search 形态才传。

    rotate_search 工件**不带 W/b**（传 None）：旧版推理读不到 class3 头 →
    "探针损坏" → 层缺席 → 诚实退回 VLM 单路。若写全零头，旧版 argmax 恒为
    第 0 类"正常"，等于静默永远判正常（#184 收口的"失败伪装成正常"）。
    """
    fields = {"classes": np.asarray(classes, dtype="U16"),
              "clip_model": str(clip_model), "pretrained": str(pretrained)}
    if W is not None:
        W = np.asarray(W, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        assert W.ndim == 2 and W.shape[0] == len(classes) == b.shape[0]
        fields.update({"W": W, "b": b})
    else:
        assert W4 is not None, "W 与 W4 不能同时缺席（否则工件无法判读）"
    if W4 is not None:
        W4 = np.asarray(W4, dtype=np.float32)
        b4 = np.asarray(b4, dtype=np.float32)
        assert (W4.ndim == 2 and W4.shape[0] == len(ROTS)
                and b4.shape == (len(ROTS),)), "四向头形状应为 [4, dim] + b4 [4]"
        # 带 class3 头时两者维度必须同源（同一嵌入空间），否则是训练/推理错位
        assert W is None or W4.shape[1] == W.shape[1], "四向头与 class3 头的 dim 不一致"
        fields.update({"W4": W4, "b4": b4, "infer": "rotate_search",
                       "thr": np.float32(thr)})
    np.savez(path, **fields)


def load_probe(path=DEFAULT_PROBE):
    """探针工件 → ClipProbe；文件缺失/损坏 → (None, 原因)。"""
    if not os.path.exists(path):
        return None, f"探针文件缺失: {path}（数据集≥100 段后 T7 训练产出）"
    try:
        z = np.load(path, allow_pickle=False)
        kw = {}
        has4 = "W4" in z
        if has4:
            kw = {"W4": z["W4"], "b4": z["b4"],
                  "infer": str(z["infer"]) if "infer" in z else "rotate_search",
                  "thr": float(z["thr"]) if "thr" in z else PROBE_THR}
        if "W" not in z and not has4:
            return None, f"探针工件既无 class3 头也无四向头，无法判读: {path}"
        p = ClipProbe(W=z["W"] if "W" in z else None,
                      b=z["b"] if "b" in z else None,
                      classes=([str(c) for c in z["classes"]] if "classes" in z
                               else list(VERDICT_LEVELS)),
                      clip_model=str(z["clip_model"]), pretrained=str(z["pretrained"]),
                      **kw)
        return p, ""
    except Exception as e:  # noqa: BLE001
        return None, f"探针文件损坏: {e}"


class ClipProbe:
    """线性探针：图像 → CLIP 嵌入 → 线性头 → softmax → (verdict, confidence)。"""

    def __init__(self, W, b, classes, clip_model=CLIP_MODEL_DEFAULT,
                 pretrained=PRETRAINED_DEFAULT, W4=None, b4=None,
                 infer="", thr=PROBE_THR):
        # W/b 允许为 None（rotate_search 工件不带 class3 头；此时只走四向推理）
        self.W = None if W is None else np.asarray(W, dtype=np.float32)
        self.b = None if b is None else np.asarray(b, dtype=np.float32)
        self.classes = classes
        self.clip_model, self.pretrained = clip_model, pretrained
        self._embedder = None  # 惰性：首个 predict 才拉 open_clip
        self.W4 = None if W4 is None else np.asarray(W4, dtype=np.float32)
        self.b4 = None if b4 is None else np.asarray(b4, dtype=np.float32)
        self.infer = (infer or ("rotate_search" if self.W4 is not None else "class3"))
        self.thr = float(thr)
        self.last = {}  # 最近一次判读的中间量（r*/p0/margin，供台账与排障）

    @property
    def rotation_mode(self):
        """True = 四向旋转搜索形态（生产可据此把它当主判，见 T10 #119）。"""
        return self.W4 is not None

    def predict(self, image_path, embed_fn=None):
        """verdict 映射：argmax 类名必须 ∈ {正常,倾斜,翻转}（训练侧保证），
        非法类名按层失败处理（返回 None）。embed_fn 可注入（测试/换嵌入模型）：
        class3 形态 `embed_fn(path)`；rotate_search 形态 `embed_fn(path, deg)`。"""
        try:
            if self.rotation_mode:
                return self._predict_rot(image_path, embed_fn)
            if self.W is None:
                return None, "探针缺 class3 头（rotate_search 工件只能四向推理）"
            vec = (embed_fn or self._embed)(image_path)
            logits = self.W @ vec + self.b
            e = np.exp(logits - logits.max())
            soft = e / e.sum()
            i = int(soft.argmax())
            verdict = self.classes[i]
            if verdict not in VERDICT_LEVELS:
                return None, f"探针类名越界: {verdict!r}"
            return verdict, float(soft[i])
        except Exception as e:  # noqa: BLE001 — 层失败即降级，绝不炸判读链
            return None, str(e)[:120]

    def _predict_rot(self, image_path, embed_fn=None):
        """四向旋转搜索：p0[r] = 「这帧按 r 度转之后被判为正立」的概率。

        r* = argmax p0 → 该帧自身旋转 =(360−ROTS[r*])%360；r*=0 或 margin 不足
        （朝向证据太弱，多半是内容主导的歧义帧）则判正常，交回 VLM/人审。
        """
        p0 = []
        for deg in ROTS:
            vec = self._embed_any(image_path, deg, embed_fn)
            logits = self.W4 @ vec + self.b4
            e = np.exp(logits - logits.max())
            p0.append(float((e / e.sum())[0]))  # 类 0 = 训练时的「未旋转输入」
        r = int(np.argmax(p0))
        margin = p0[r] - p0[0]
        # frame_rot_deg = 该帧自身的旋转角 ((360−r*)%360)：把画面按 −frame_rot_deg
        # 转回即正立（10-19 shot05 实测 r*=270 → 自身 90°，与 ffmpeg transpose=1
        # 顺时针转正完全对上）
        self.last = {"r_star": ROTS[r], "p0": [round(x, 4) for x in p0],
                     "margin": round(margin, 4), "thr": self.thr,
                     "frame_rot_deg": (360 - ROTS[r]) % 360}
        if r == 0 or margin < self.thr:
            return "正常", float(p0[0])
        return "翻转", float(p0[r])

    def _embed_any(self, image_path, deg, embed_fn=None):
        if embed_fn is not None:
            return embed_fn(image_path, deg)  # 旋转搜索必须两参注入，单参会 TypeError
        return self._embed(image_path, deg)

    def _embed(self, image_path, deg=0):
        # import 必须在函数顶（10-14 实锤：写在惰性 if 块内，同一实例第二次调用
        # 走不到绑定行 → Image/model 未绑定 NameError → 整层"弃判"）
        import torch
        import open_clip
        from PIL import Image
        if self._embedder is None:
            # 注意 pretrained 语义：hf-hub 标签必须传 ''（= 加载标签默认权重）；
            # 传 None 会构建未加载权重的坏模型（10-14 实锤：patch 宽度 7 的畸形网络）
            model, _, preprocess = open_clip.create_model_and_transforms(
                self.clip_model, pretrained=self.pretrained)
            model.eval()

            def _fn(im):
                with torch.no_grad():
                    # 必须 batch 维（10-14 实锤：缺 [1,·,·,·] 会让 ViT 维度解错 → 768/7 畸形报错）
                    return model.encode_image(preprocess(im).unsqueeze(0)).float().numpy().ravel()
            self._embedder = _fn
        im = Image.open(image_path).convert("RGB")
        if deg:  # expand=True：旋转后画布跟着换向，与训练侧 embed_all 一致
            im = im.rotate(ROT_PIL[deg], expand=True)
        return self._embedder(im)


def probe_status(path=DEFAULT_PROBE):
    """层可用性自检（供 smoke / driver 日志）：dict(available, probe, reason)。"""
    p, reason = load_probe(path)
    if p is None:
        return {"available": False, "probe": path, "reason": reason}
    try:
        import open_clip  # noqa: F401
        st = {"available": True, "probe": path,
              "classes": p.classes, "clip_model": p.clip_model,
              "infer": p.infer}
        if p.rotation_mode:
            st["thr"] = p.thr
        return st
    except ImportError as e:
        return {"available": False, "probe": path,
                "reason": f"open_clip 未安装（pip install open_clip_torch）: {e}"}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="CLIP 线性探针推理")
    ap.add_argument("cmd", choices=["status", "predict"])
    ap.add_argument("--probe", default=DEFAULT_PROBE)
    ap.add_argument("--image", default=None)
    a = ap.parse_args()
    if a.cmd == "status":
        print(json.dumps(probe_status(a.probe), ensure_ascii=False))
        return 0
    st = probe_status(a.probe)
    if not st["available"]:
        print(json.dumps(st, ensure_ascii=False))
        return 1
    p, _ = load_probe(a.probe)
    verdict, conf_or_reason = p.predict(a.image)
    # 名实相符（评审）：失败时第二返回值是 reason 不是置信度
    out = ({"verdict": verdict, "confidence": conf_or_reason,
            "detail": p.last} if verdict
           else {"verdict": None, "error": conf_or_reason})
    print(json.dumps(out, ensure_ascii=False))
    return 0 if verdict else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
