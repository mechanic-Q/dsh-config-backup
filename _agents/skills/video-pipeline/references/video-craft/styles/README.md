# 风格库（Styles）

> video-pipeline 的风格锁参考库。每个风格文件从 MiniMax H3 官方 skill 或自有变体提炼。
> 结构统一：视觉语言 / 运动语言 / 音频策略 / 负向约束 / H3英文关键词 / 适配说明。

## 风格清单

| 风格 | 文件 | 来源 | 最适合 |
|---|---|---|---|
| 纸质科普 | `paper-collage.md` | 官方 paper-collage-explainer | 叙事/知识/科普（你的默认） |
| 纸艺定格 | `papercraft-stop-motion.md` | 官方 papercraft-stop-motion | 科学/教育立体讲解 |
| 3D 卡通 | `3d-animation.md` | 官方 3d-animation-short | 拟人化/故事剧场 |
| 手绘融合 | `handdrawn-live.md` | 官方 handdrawn-live | POV 段（手绘从实拍手冒出） |
| 极简产品 | `minimalist-product.md` | 官方 minimalist-product | 科技产品测评/新品介绍 |
| 品牌宣传片 | `brand-promo.md` | 官方 brand-promo | 品牌/产品/App/网站宣传短片 |
| 双人游戏开场 | `co-op-game-intro.md` | 官方 co-op-game-intro | 游戏菜单/双人角色开场 |
| 音乐MV排版 | `music-video-subtitle.md` | 官方 music-video-subtitle | 音乐MV/歌词排版/卡点（⚠️与无人声红线冲突） |
| 深蓝信息图 | `infographic.md` | 自有变体 | 数据/科技/航天类新闻 |
| 写实纪录片 | `realistic.md` | 自有变体 | 新闻纪实/科技现场 |
| 中式天宫 | `chinese-celestial-palace.md` | Liblib「Lala罐头的中式天宫」skill | 仙侠/天宫/神话/古典故事 |

## 风格选择规则

1. **默认**：paper-collage（纸质科普，用户认可的 VOX 科普基线）
2. 数据/科技/突破类新闻 → infographic（数字多、逻辑强）
3. 新闻纪实/真实场景 → realistic
4. 仙侠/天宫/神话/古典故事 → chinese-celestial-palace
5. 品牌/产品/App/网站宣传 → brand-promo
6. 游戏/双人角色开场 → co-op-game-intro
7. 音乐MV/歌词排版 → music-video-subtitle（⚠️仅无演唱纯氛围时可用）
8. 未来加新风格：只加一个 `styles/<名>.md`，角色框架（personas.md）不动

## 与脚本的对应

| 风格 | 导演设计脚本 | H3 转译脚本 |
|---|---|---|
| paper-collage | director_llm_paper.py | director_to_h3_llm_paper.py |
| realistic | director_llm_realistic.py | director_to_h3_llm_realistic.py |
| infographic | director_llm_infographic.py | director_to_h3_llm_infographic.py |

未来新风格 = 复制一对脚本 + 加一个 styles/ 文件，主框架不动。

## 版权说明

- 官方风格（paper-collage/papercraft/3d/handdrawn/minimalist）：从 MiniMax H3 官方 skill（GitHub MiniMax-AI/MiniMax-H3，MiniMax H3 Community License）提炼视觉语言，仅用于本地视频制作参考，不对外分发风格文本本身。
- 自有变体（infographic/realistic）：用户自建，无版权问题。
