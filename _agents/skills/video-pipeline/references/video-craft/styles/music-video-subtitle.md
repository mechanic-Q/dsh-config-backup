# Music Video Subtitle（音乐 MV / 歌词排版）

> 来源：MiniMax H3 官方 skill `music-video-subtitle-generator`（204 行）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 定位：AI 音乐 MV / 情绪短片，节拍反应式空间排版（lyric typography）

## 视觉语言

- **歌词排版是设计图层**（typography is a designed visual layer），不是普通字幕条
- 排版是空间中动态的图形主体：可置前景/中景/背景、可被肩膀/手遮挡
- 写实高级时尚质感 + 电影杂志质感：90s/00s 独立杂志、复印纸、胶片扫描、zine 拼贴
- 重颗粒、轻微胶片抖动、半调网点、粗糙印刷边缘、扫描偏移

## 色彩

- 按情绪基调定 LUT 方向（Trap 暗色 / Dark-pop 暗黑流行 / Cyber-grunge 赛博垃圾）
- 全局统一色彩分级（35mm film grain + Movie LUT），掩盖批次色差
- 每镜视觉风格统一，情绪氛围统一

## 运动语言

- **只用硬切**（hard cuts only）：禁淡入淡出、溶解、软过渡
- 图像/排版强烈响应节拍：hi-hat 滚动→微抖/跳帧、军鼓→缩放/硬切/肩部下沉、808 低音→低频压缩/拉伸/偏移
- 快速多特写空间切换（局部背景/隧道/墙壁/灯带/反光地板），场景切换由音乐冲击触发（bass hit/808 drop/军鼓/人声重音/排版爆裂）
- 转场连续性：同方向甩镜/手部遮挡匹配剪辑/节拍硬切

## 歌词与人物（locked lyrics）

- 锁定歌词是唯一文本源：口播/演唱/可见排版必须逐字匹配演唱内容
- 每镜只有一个主排版事件；文字绝不遮挡眼睛/主要表情，关键口型时避开嘴部
- 歌手必须演唱锁定歌词，可见口型、下颌运动、呼吸、面部强调、点头、手势跟随乐句节奏

## 音频策略（官方默认）

- 全局一条 Master Audio 音轨（完整人声 + 律动），分段生成时不用独立断开的片段音频
- 切点落在歌词停顿/呼吸/军鼓/drop，不在元音或歌词中间硬切

## 负向约束

- 禁淡入淡出/溶解（只用硬切）
- 禁光泽 AI 美颜脸（glossy AI beauty face）
- 禁单镜头过度拉伸变形
- 文字禁遮挡眼睛/主要表情/关键口型

## 转译到 H3 prompt 的英文关键词

```
music video aesthetic, cinematic MV, beat-reactive typography,
lyric typography as dynamic spatial layer, realistic high-fashion texture,
film magazine texture, heavy grain, slight film jitter, halftone dots,
rough print edges, scan offsets, hard cuts only, beat-synced scene switches,
dark pop / trap / cyber-grunge mood, unified 35mm film grain and movie LUT,
character singing locked lyrics with visible lip shapes and breath,
typography smashing in and out on bass hits
```

## 对 video-pipeline 的适配

- 最适合：音乐/歌词/情绪类选题、卡点 MV、贴字 MV、多镜头拼接
- 与 H3 契合：H3 擅长文字/UI 清晰渲染 + 节奏感
- 与 paper-collage 对比：MV 是节拍驱动的硬切排版，paper-collage 是编辑式纸拼贴叙事
- ⚠️ 重大差异：本风格核心是"歌词排版 + 演唱"（画面内人物演唱、可见口型），与流水线"画面内无人声 + 不用真人形象"红线冲突——**仅适用**无演唱/无歌词排版的纯氛围 MV 或情绪短片，或用户明确要音乐可视化时；否则不选此风格
- 切点硬切风格可复用：你的流水线切点本来就落句间停顿，MV 的节拍硬切是更激进版本

## 升级路径（ponytail）

纯视觉语言参考。若未来要自动化校验（读导演设计检查是否用了本风格元素），升级为 `styles/check_style.py`。
