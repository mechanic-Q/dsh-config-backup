# Papercraft Stop-Motion（纸艺定格）

> 来源：MiniMax H3 官方 skill `papercraft-stop-motion-explainer`（470 行）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 官方定位：科学/教育/科普的触感纸艺定格讲解

## 视觉语言

- **纸艺定格**：手工纸拼贴、纸板剪裁、微型立体布景、停格木偶运动、立体书布景、纸道具、物理阴影
- 分层立体场景：**4-7 层**前/中/后/远背景（foreground/midground/background/far-background）
- 前景遮挡物（纸叶/纸云/纸框/窗帘/道具/剪影）制造深度
- 可见的纸材质：纸纤维、折痕、接缝、撕裂/切割边缘、纸板厚度、拉片/滑轨/旋转盘
- 宏观微距摄影 + 轻微景深 + 2.5D 视差

## 运动语言

- 逐帧手作感：小步进运动、微小停顿、轻微回弹、铰链接合手势、滑动纸机构、翻页揭示、拉片揭示、纸片沉降
- 背景元素也是纸机构：滑轨上的纸云、轨道上的纸月/纸盘、视差移动的纸景、飘落的纸叶、摇摆的纸灯、开合的纸门
- **不是**平滑 CG 运动、不是塑料 3D 表面、不是扁平矢量背景

## 音频策略

- 纸运动声效：翻纸、剪刀剪、纸板滑动、纸沙沙、木点击、轻弹、撕胶带、木偶关节轻敲
- BGM 匹配主题文化（如中秋→克制的中式传统乐：古筝/琵琶/竹笛/轻打击/软弦乐+足够的静默）
- 口播下 BGM 压低（ducking）

## 负向约束

- 禁光滑塑料 3D
- 禁光泽 CG 渲染
- 禁写实照片
- 禁扁平矢量插画
- 禁金属科幻表面/玻璃材质/赛博霓虹/数字故障特效
- 禁高速摄影机环绕
- 禁融化/液体变形

## 转译到 H3 prompt 的英文关键词

```
handmade papercraft stop-motion, layered paper diorama, cardboard cutouts,
visible paper fibers, folds, seams, cut edges, physical shadows,
macro miniature photography, depth of field, 2.5D parallax,
multi-plane foreground/midground/background/far-background,
stop-motion motion language: stepped movement, tiny pauses, rebounds, hinged joints
```

## 对 video-pipeline 的适配

- 与 paper-collage 的区别：papercraft 更强调**立体分层布景**和**微型模型**；paper-collage 更强调**平面半色调编辑感**
- 你的纸质科普风：paper-collage 为主，papercraft 作为"立体场景升级版"备选

## 升级路径（ponytail）

同 paper-collage。纯视觉语言参考，未来可升级自动化校验。
