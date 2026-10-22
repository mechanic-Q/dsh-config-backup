# 3D Animation（Pixar 3D 卡通）

> 来源：MiniMax H3 官方 skill `3d-animation-short-generator`（248 行）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 官方定位：叙事 3D 动画短片（强角色一致性/场景连续性/时间/镜头/表演/音频控制）

## 视觉语言

- **Pixar 风格 3D 卡通渲染**：C4D + Octane 渲染质感，高端动画电影级
- 角色设计：夸张几何简化 + 优秀材质细节；避免 100% 写实人体解剖；高辨识度剪影
- 比例语言：友好风格化比例，2.5-3 头身（可爱/童趣角色），大头小身、紧凑身体、清晰轮廓
- 毛发/头发：强雕塑发团 + 干净块状 + 精细边缘飞发/绒毛
- 皮肤/材质：温暖次表面散射（SSS），耳朵/脸颊/鼻尖/指尖透光，禁塑料硬皮

## 表演语言

- 夸张生动：挤压拉伸、强眉毛/眼角/瞳孔/嘴唇/脸颊变化
- 高能量姿态、清晰动作线、前倾、强预期、快速但可读的时机、弹性身体力学、生动微表情
- 情绪范围：可爱与爆发式表现力平衡；强烈情绪可用戏剧化面部变形但保留角色魅力

## 运动语言

- 弹性：squash-and-stretch、anticipation、overshoot、follow-through、overlap、arcs
- 快速姿势变化 + 清晰喜剧剪影

## 负向约束

- 禁写实实拍
- 禁扁平 2D 动画
- 禁塑料玩具皮肤
- 禁僵硬模特摆姿
- 禁写实解剖僵硬
- 禁无生命表情

## 转译到 H3 prompt 的英文关键词

```
Pixar-inspired 3D cartoon rendering, C4D + Octane renderer look,
high-end animated feature quality, exaggerated geometric simplification,
large readable silhouettes, Q-version proportions, strong sculpted hair clumps,
warm subsurface scattering skin, expressive Disney/Pixar-style performance,
squash and stretch, clear line of action, elastic body mechanics, vivid micro-expressions
```

## 对 video-pipeline 的适配

- 你的内容形态是口播科普，无固定角色——本风格的"角色一致性"部分用不上
- 可借鉴的：**夸张表演语言 + 弹性运动 + 3D 质感**，适合做"拟人化科普"（如把知识点变成 3D 小剧场）
- 与 paper-collage 的对比：3D 更"电影感"，paper-collage 更"编辑感"

## 升级路径（ponytail）

同 paper-collage。纯视觉语言参考。
