# 视点决策表（Cinematic Point of View）

> 来源：拉毕格《Directing Film Techniques and Aesthetics》Ch9 Cinematic Point of View
> 用途：video-pipeline 阶段3（导演设计 POV 段判断）+ 阶段5（H3 prompt POV 标记）
> 对齐字段：POV 段（idx%5==0）/ shot_type

## 视点类型决策表

| 触发信号 | 视点类型 | 效果 | 对应你的设计 |
|---|---|---|---|
| 第一人称旁白/口播 | 主观 POV（subjective） | 观众=角色眼睛，看手/操作 | POV 段：第一人称手部视角 |
| 全知叙述/科普讲解 | 全知 POV（omniscient） | 上帝视角，自由穿梭时空 | 非 POV 段：第三人称 |
| 中立展示/让事实说话 | 自隐 POV（self-effacing） | 不评论，观众自行判断 | 新闻事实段（纯展示不评价） |
| 有限/角色视角 | 有限 POV（limited） | 只看到角色知道的部分 | 揭示段（逐步揭开信息） |
| 关注但不介入 | 关怀观察者（concerned observer） | 注册各方张力，选择何时切换 | 导演全局视角（选切点用） |

## POV 规则

1. **POV 是导演的第一选择**—— Oliver Stone："导演最重要的是视点"
2. **前 10-15 镜定调**——开头几段就建立"这是谁的视角"
3. **主观 POV 段**：镜头=角色眼睛；看自己的手（不是别人看我）；第一人称旁白
4. **全知 POV 段**：自由穿梭；可以揭示角色不知道的信息（戏剧反讽）
5. **切换有动机**：视点切换必须由叙事/情感驱动，不是随意切

## 对 video-pipeline 的适配

- 你的 POV 段（idx%5==0 = 20%段）= 拉毕格的主观 POV
- POV 段 H3 prompt："A first-person POV, the narrator's own hands visible in frame" = 拉毕格的"角色看自己的手"
- 非 POV 段 = 全知 POV（第三人称，看场景不进入角色视角）
- 导演设计时：每段判断"这是谁的视点"→ 决定主观还是全知
