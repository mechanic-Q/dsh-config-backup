# Handdrawn + Live-Action Fusion（手绘+实拍融合）

> 来源：MiniMax H3 官方 skill `handdrawn-live-video-generator`（86 行）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 官方定位：15 秒内实拍与手绘发光动画融合的短镜

## 视觉语言

- **实拍场景 + 扁平手绘发光动画**（flat hand-drawn luminous animation in a real space）
- 手绘动画作为独立实体，与实拍物体/手**真实接触**
- 发光线条、手绘轮廓、非真实光源

## 运动语言（严格时序）

1. **0-3 秒**：手绘发光动画出现在实景，与实拍手/物体清晰接触
2. **持续变形**：作为一个单一实体连续变形（continuously morphs as one single entity）
3. **逃逸**：动画从接触点逃逸/离开
4. **手机跟拍**：手持手机摄影机**轻微延迟跟随**（handheld phone camera follows slightly late）

## 音频策略

- 无默认（依赖实景环境音 + 后期叠加）

## 负向约束

- 禁纯 2D 平面动画（必须与实景融合）
- 禁纯实拍（必须有手绘元素）
- 禁平滑数字变形（要"单实体连续变形"的物理感）

## 转译到 H3 prompt 的英文关键词

```
live-action and hand-drawn fusion, flat luminous hand-drawn animation in a real space,
clearly contacts live-action hands/objects in the first 0-3 seconds,
continuously morphs as one single entity, escapes, handheld phone camera follows slightly late
```

## 对 video-pipeline 的适配

- 你的红线是"不用真人形象"，本风格**天然契合**：手绘动画 + 实拍手部（POV 第一人称手部视角）
- 0-3 秒"接触实拍手"正好是 POV 段（idx%5==0）的天然场景——手绘元素从叙述者的手/桌面冒出
- 与 paper-collage 对比：手绘融合更"魔法感/创意感"，适合做"把抽象概念具象化冒出来"

## 升级路径（ponytail）

同 paper-collage。纯视觉语言参考。
