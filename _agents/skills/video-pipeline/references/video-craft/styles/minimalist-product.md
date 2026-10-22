# Minimalist Product Ad（极简产品广告）

> 来源：MiniMax H3 官方 skill `minimalist-product-ad-generator`（397 行）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 官方定位：电商/产品发布/创业者的极简产品广告

## 视觉语言

- **极简**：干净、留白、单一产品焦点、Apple 风格
- 无多余装饰、无杂乱背景、产品是绝对主角

## 核心机制（值得借鉴的流程机制）

- **产品事实表**：先锁产品事实（规格/卖点/目标人群），叙事脊柱基于事实，不编造
- **独立锚点照片**：3 张独立产品锚点图（不拼 4 宫格，避免视频模型复现面板布局）
- **精确节拍文字分镜表**（Beat Content Table）：时间段/镜头/镜头目的/视觉主导/画面运镜/款式状态/文案内容/文字颜色/文字效果/转场/节奏意图——这是全官方 skill 里最细的分镜表
- **节奏密度**：5s→3-4 节拍、10s→5-7、15s→6-9
- **节奏意图词**：铺垫、建立、预备、冲击、制动、稳定（你已借鉴）

## 运镜语言

- 慢推/环绕展示产品
- 单一主体居中，镜头运动服务产品展示

## 负向约束

- 禁杂乱背景
- 禁多产品同时出现
- 禁文字堆砌

## 转译到 H3 prompt 的英文关键词

```
minimalist product advertisement, Apple-style clean composition,
single product focus, generous negative space, precise beat storyboard,
slow push-in or orbit around the product, studio lighting, premium material feel
```

## 对 video-pipeline 的适配

- 你的内容形态是口播科普，非产品广告——但"精确节拍文字分镜表"和"产品事实表（先锁事实再创意）"与你的"事实保真校验"同构
- 若未来做"科技产品测评/新品介绍"类视频，直接启用本风格

## 升级路径（ponytail）

同 paper-collage。纯视觉语言参考。
