# Quality Modifiers（画质修饰词链）

> 来源：2026-08-30 中英文提示词调研（/tmp/prompt-research/ 三份报告）+ 各官方指南
> 用途：video-pipeline 导演设计 / H3 转译的运行时参考（agent 按触发信号选词）
> 性质：**锚定内容使用，禁止后贴**——修饰词必须随镜头设计就地配（Veo 3.1：只改出问题的槽位；Runway：过度具体化/堆砌反而出意外）
> 命名建议：这类词英文社区叫 **modifiers / quality modifiers（质量修饰词）**，中文无统一学名，本流水线内部命名「画质修饰词」

## 核心铁律

1. **负面词 > 正向词**：去掉 blurry/lowres 比加上 4K 更有效（中英文社区共识）
2. **权重层级**：主体名词 > 动作动词 > 运镜词 > 美学形容词（cinematic/stunning 权重最低）
3. **一镜一词 / 一速**：运镜和速度限定词各只用一个；风格锚定 ≤2 词，叠两个风格 70% 不一致
4. **去 AI 味**：真实 = 带瑕疵（颗粒/光比/真实纹理），不是更清晰
5. **风格感知**：按 styles/ 分流，尊重各风格的负向约束（realistic 加 grain；paper-collage 加纸艺光影）

## 决策规则表

### 光（Lighting）—— 每段至少 1 个光质锚定词

| 触发信号 | 动作（选 1 个） | 适用风格 |
|---|---|---|
| 情绪高潮 / 氛围 / 叙事重点 | `soft window light` / `golden hour` / `warm tungsten light` | 全部 |
| 冷峻 / 科技 / 科幻 | `cool blue ambient light` / `neon glow` / `hard key light` | cyberpunk / realistic |
| 立体感 / 层次 | `side light` / `chiaroscuro`（明暗对照） | 全部 |
| 特写 / 角色分离 | `rim light`（轮廓光） | 全部 |
| 神秘 / 戏剧 | `low-key lighting` / `backlight`（逆光剪影） | noir / paper |
| 柔和 / 温馨 | `soft diffused light` / `candle warm glow` | 治愈 / 国风 |

### 材质与质感（Material & Texture）—— 按场景匹配

| 触发信号 | 动作（选 1 个） | 反例 |
|---|---|---|
| 产品展示 / 干净商业 | `glossy` / `polished surfaces` | 用 weathered = 廉价 |
| 旧物 / 历史 / 岁月感 | `weathered` / `rusted` / `aged patina` | 用 glossy = 塑料 |
| 奇幻 / 珠宝 / 国风 | `iridescent`（虹彩）/ `crystal` / `translucent` | — |
| 手工 / 绘画感 | `thick impasto`（厚涂）/ `canvas texture` | — |
| 现场感 / 纪实 | `real-world textures` / `organic surfaces` | 过度 CG |

### 景深与镜头（Depth & Lens）—— 控制清晰层次

| 触发信号 | 动作 |
|---|---|
| 聚焦主体 / 虚化背景 | `shallow depth of field`（浅景深） |
| 交代环境 / 空间 | `deep focus` / `wide shot`（全景深） |
| 电影质感（配焦段） | `shot on 85mm lens, f/1.8` |
| 动态 / 速度感 | `motion blur`（运动模糊，配合速度词） |

### 去 AI 味（Anti-AI）—— 当画面"太干净/太完美"

| 触发信号 | 动作 |
|---|---|
| 想真实 / 纪实 / 电影感 | `slightly grainy, film-like` / `35mm film grain`（胶片颗粒） |
| 怕"塑料感" | `subtle imperfections` / `natural skin texture` |
| 画面内文字干扰 | `no subtitles` / `no text overlay`（负面） |

### 负面词基线（Negative baseline）—— 防废片

```
lowres, blurry, soft focus, worst quality, low quality, jpeg artifacts, watermark,
signature, logo, text overlay, extra fingers, mutated hands, bad anatomy, extra limbs,
fused fingers, too many fingers, ugly, deformed, distorted, overexposed, underexposed
```

- 人像追加：`asymmetry, deformed eyes, crossed eyes, double chin, distorted face, unnatural expression, weird teeth, crooked smile`
- 构图追加：`out of frame, cropped, duplicate`

## 使用方式

- **阶段三A（整体方案）**：SYSTEM 要求产出【画面质感基调】块（光质/颗粒/景深策略）
- **阶段三B（逐段设计）**：SYSTEM 要求每段【画质】块 = 光 1 个 + 材质 1 个 + 景深 1 个（从本表按风格选）
- **阶段四（H3 转译）**：enforce_constraints 检查 imd 含 ≥2 个画质锚定词，缺失末尾追加
- **阶段五（验收）**：verify 第 10 项检查 prompt 画质词 + 抽帧 vision 对比

## 已知问题（known issues）

1. 后贴修饰词（prompt 定稿后再统一加）无效——缺少内容上下文，只能贴通用词
2. 正向追 4K/8K 远不如负面去 blurry（调研实测）
3. 修饰词堆叠 >2 个风格参考会互相打架（Veo 3.1 实测 70% 不一致）
4. 自动补词只能末尾追加——插开头会破坏 verify 风格锁前缀检查
