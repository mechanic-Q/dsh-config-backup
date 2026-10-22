# Paper Collage（半色调纸拼贴）

> 来源：MiniMax H3 官方 skill `paper-collage-explainer-generator`（272 行）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 官方定位：叙事/知识/观点/抽象话题的编辑式纸拼贴动画

## 视觉语言

- **半色调纸拼贴**（halftone paper collage）：扁平平涂色块、黑白摄影剪影、选择性彩色卡纸强调
- 暖白奶油色关键线（warm cream keylines）、柔和纸阴影（soft paper shadows）
- 触感定格拼贴组装（tactile stop-motion assembly）
- 分层构图：清晰前/中/后景，强主体层级，可控留白

## 运动语言

- 物体从空色场中组装/拼接出现（assembling from empty color field）
- 触感定格：纸张滑动、弹出、按压、轻响（paper slide/pop/press/rustle）
- **不是**慢推镜头、不是通用漂移、不是平滑数字层移动

## 音频策略（官方默认）

- 保留/生成**纸拼贴声效**：纸滑动/弹出/按压/轻响/轻敲（默认开）
- **默认不加 BGM**（用户要求才加）
- **默认不加口播/字幕**（用户要求才加）

## 负向约束

- 禁光滑 CG 渲染
- 禁塑料表面
- 禁矢量扁平插画
- 禁可读文字/数字/UI/水印/Logo

## 转译到 H3 prompt 的英文关键词

```
premium editorial halftone paper collage, flat bold color fields,
black-and-white photographic cut-outs, selective colored cardstock accents,
warm cream keylines, soft paper shadows, tactile stop-motion assembly,
layered 16:9 composition with clear foreground/midground/background depth,
strong subject hierarchy, controlled negative space
```

## 对 video-pipeline 的适配

- 你的纸质科普风 = 本风格 + 第一人称口播（官方默认无口播，你要叠加 TTS）
- BGM：官方默认无，你的流水线统一后期叠 BGM（0.2-0.33x 铺底）——不冲突，因为官方策略是"不自动加"，你是在后期统一加
- 声效：官方默认纸声效，你的 H3 画面内不发声（off-screen voiceover），纸声效由 BGM 层后期补

## 升级路径（ponytail）

当前是纯视觉语言参考。若未来要自动化校验（读导演设计检查是否用了本风格的视觉元素），升级为 `styles/check_style.py`。
