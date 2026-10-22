# Panda Cartoon（真实CG卡通·可爱熊猫风）

> 来源：用户指定风格（2026-08-26），真实感 CG 渲染 + 卡通可爱化熊猫主角
> 用途：导演设计 / H3 prompt 的风格锁参考
> 关键词：真实CG卡通、可爱、明亮鲜艳、充满希望、吸引眼球

## 视觉语言

- **真实CG卡通渲染**（realistic CG cartoon）：Pixar/梦工厂级别高质感 3D 渲染，光线、材质、景深写实，但造型卡通可爱化——不是照片写实、不是扁平 2D、不是低质贴图
- **可爱熊猫主角**：圆滚滚的卡通熊猫（圆润身材、大眼睛、黑眼圈、粉红脸颊），作为全片视觉母题，憨态可掬、行动灵活
- **明亮鲜艳色调**（用户硬性要求）：高饱和蓝天、翠绿竹林、暖金黄阳光、彩虹/云朵/爱心等乐观符号，整体明亮多彩、充满希望，禁冷灰压抑
- 分层构图：清晰前/中/后景，柔和景深，发光元素点缀

## 运动语言

- 卡通弹性动作（squash & stretch）：熊猫蹦跳、抱头惊讶、鼓掌、望远镜观察、比心
- 可爱化演绎科技：熊猫背书包、举放大镜、戴小头盔、开小火箭
- 手绘流畅感：3D 渲染但动作夸张生动、表情丰富（大眼睛忽闪、嘴型可爱）

## 音频策略

- 画面内无人声（off-screen voiceover），嘴闭合
- BGM 后期叠加，明亮轻快（非 diegetic N/A）

## 负向约束

- 禁写实真人、禁照片写实摄影、禁真人动物
- 禁 3D 恐怖谷、禁低质灰暗渲染、禁扁平矢量插画
- 禁冷色压抑、禁恐怖/惊悚元素
- 禁可读文字/数字/UI/水印/Logo

## 转译到 H3 prompt 的英文关键词

```
realistic CG cartoon animation, adorable chubby panda mascot with big
glossy eyes, black eye patches and rosy pink cheeks, Pixar-style high-end
3D rendering with soft lighting and depth of field, bright vibrant
cheerful color palette (high-saturation blue sky, lush emerald bamboo,
warm golden sunlight, rainbow accents), cute elastic squash-and-stretch
motion, expressive playful performance, optimistic hopeful atmosphere,
clear layered composition, no real humans, no photorealistic animals,
no flat 2D vector art, no dark gloomy colors
```

## 对 video-pipeline 的适配

- 口播科普形态，无固定真实角色——本风格的"熊猫主角"作为拟人化母题贯穿全片（类似 niulai 的小牛）
- 熊猫作为"会思考的守望者"化身，适合航天/科技/未来题材的可爱化表达
- 与 niulai-cartoon 对比：niulai 是手绘"野生感"，panda 是"真实CG电影感+可爱"
