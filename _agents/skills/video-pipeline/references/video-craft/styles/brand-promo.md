# Brand Promo（品牌宣传片）

> 来源：MiniMax H3 官方 skill `brand-promo-video-generator`（188 行）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 定位：品牌/产品/App/网站/店铺宣传短片，15-30s 快节奏产品叙事

## 视觉语言

- **产品为中心**（product-centric）：真实展示产品功能/交互/场景/产出，不用抽象特效掩盖产品
- **干净高级**（clean premium）：可读剪影、logo 留白区（clear space）保护、克制装饰
- **2-5 个有意义的色彩状态**（deliberate color states），颜色绑定叙事含义
- 几何/UI 元素（蒙版、渐变、色场、网格、辉光、粒子、轨迹、排版、图表）只作非具象运动层

## 色彩

- 品牌色系统：主色 + UI 底色 + 文本色 + 功能强调色（2-5 个色彩状态，按叙事切换）
- 高对比、干净现代，禁杂乱多色

## 运动语言

- 产品运动、光标路径、UI 流动、滚动内容、物体边缘、匹配几何体驱动转场
- 每个节拍一个主动作（one primary action per beat），次要层延迟出现
- 2-3 个高能峰值 + 安静制动时刻
- 15s 典型骨架：品牌钩子 → 用户意图/铺垫 → 产品机制 → 能力/场景 → 产出/证明 → 产品回馈 → 最终 logo+CTA

## 音频策略（官方默认）

- 原生品牌片：视频原生音轨（generate_audio=true），品牌安全器乐/UI 音效，不另生成独立音乐轨
- 仅在需要可控口播/独立 BGM/后期混音时才单独生成语音或音乐

## 负向约束

- 禁伪造 HUD、随意玻璃卡片、装饰性文字墙、未核实的指标
- 禁千篇一律的缓动（identical easing everywhere）
- 禁重绘/近似 logo、产品 UI、包装、吉祥物、人物（须用官方/用户授权素材）
- 禁在转场中模糊产品主体

## 转译到 H3 prompt 的英文关键词

```
premium brand promotional video, clean product-centric composition,
real product features and interactions shown clearly,
2-5 deliberate brand color states tied to narrative meaning,
brand-safe instrumental music and UI sound design,
readable silhouettes and preserved logo clear space,
product motion and UI flow driving transitions,
geometric motion layers (color fields, grids, glows, particles),
high contrast, clean modern commercial look,
no fake HUDs, no decorative text walls, no glass cards
```

## 对 video-pipeline 的适配

- 最适合：产品/科技/品牌类选题（新品介绍、App 演示、网站 showcase）
- 与 H3 契合：H3 擅长包装/UI/产品细节渲染
- 与 minimalist-product 对比：brand-promo 更强调完整叙事骨架（钩子→机制→证明→logo），minimalist 更偏单点产品展示
- ⚠️ 音频差异：官方默认视频原生音轨，你的流水线统一后期叠 BGM（off-screen voiceover + BGM 铺底）——不冲突，生成时 non_diegetic_music 仍写 N/A，品牌器乐由 BGM 层后期补

## 升级路径（ponytail）

纯视觉语言参考。若未来要自动化校验（读导演设计检查是否用了本风格元素），升级为 `styles/check_style.py`。
