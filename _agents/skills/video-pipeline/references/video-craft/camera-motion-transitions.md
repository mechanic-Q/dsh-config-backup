# 运镜与转场决策表（Pan/Crane/Tracking/Transitions）

> 来源：卡茨《Film Directing Shot by Shot》Ch17 The Pan + Ch18 Crane + Ch19 Tracking + Ch20 Tracking Choreography + Ch21 Transitions
> 用途：video-pipeline 阶段二（摄影师分镜增强）加载
> 对齐字段：shot_type / 节奏意图 / info_point / per_second_directives(camera) / reference_anchors(subject_position)

## Pan 摇镜决策表（Ch17）

| 触发信号（可从输入判断） | 选什么 | 为什么 | 反例 |
|---|---|---|---|
| 节奏意图=建立/大空间交代 | 全景摇（panoramic pan） | 摇覆盖空间最快，track 同距离需 10 倍时间 | 无故事动机的纯扫视 |
| 主体小幅移动需保持构图 | 低调跟摇 reframe | 隐形维持构图平衡 | 明显甩摇=暴露失误感 |
| 信息点 A→B 需引导 | 导视摇（pan to lead the eye） | 摇镜天然强迫视线跟随 | 导向无叙事意义的点=误导 |
| 主体穿行多景深层面 | 深度摇（pan to emphasize depth） | 前/中/背景三层同动 | 单层摇=扁平 |
| 两个移动主体路径交叉 | 交叉摇（cross-pan） | 注意力在主体间接力传递 | 无交叉硬摇=观众晕 |
| 两元素需暗示关联 | 逻辑连接摇（logical connection pan） | 摇自动提出"两者有何联系" | 关联不成立=误导叙事 |
| 节奏意图=速度/紧张 | 长焦摇（telephoto pan） | 长焦放大动感（180°/10° vs 广角 180°/90°） | 广角摇=动感被稀释 |

## Crane 升降决策表（Ch18）

| 触发信号 | 选什么 | 为什么 | 反例 |
|---|---|---|---|
| 开场/once-upon-a-time 交代 | 升降穿入空间（penetrating move） | 从一般到具体，深度幻觉确立空间真实感 | 已明确的环境再升降=浪费时长 |
| 聚焦关键小物件/心理张力 | 快速垂直俯冲（rapid descent） | 俯冲到钥匙/脸=信息点+张力（Notorious） | 无 info_point 硬冲=炫技 |
| 段落=主观 POV | 模仿主体路径升降（follow-the-look） | 信息随主体揭示=主观认同 | 固定机位俯仰=旁观 |
| 两元素需讽刺/概念对比 | 升降串联 A→B（crane comparison） | 单镜完成概念对比，强于切镜 | 切镜对比=解释性削弱 |
| 需越过障碍揭示 | 升越围栏/墙（go over obstacle） | 垂直维度是 crane 独有能力 | 强行横移绕障=复杂化 |

## Tracking 跟踪决策表（Ch19-20）

| 触发信号 | 选什么 | 为什么 | 反例 |
|---|---|---|---|
| 跟随对话/移动主体 | 平行跟（parallel tracking，同速） | 同速=静态构图等价，背景流动 | 同速全程=镜头无发展 |
| 表现输/落后 | 机位快于主体（pass） | 3 平面运动=景深+动态强于平行跟 | 赢家也用机快=反了 |
| 表现赢/领先 | 主体快于机位 | 主体压过镜头=胜利感 | 同上 |
| 强调顿悟/关键反应 | 推近（push-in to CU） | 切在视线上的推近=顿悟惯例 | 无情绪点硬推=突兀 |
| 强调失去/孤立 | 拉远（pullback） | 主体缩小=孤立（站台送别） | 拉远配强调=削弱 |
| 单镜内改角度 | 主体走近机位（被迫 tilt up） | 移主体代替移机位，角度免费变化 | 全靠移机位=低效 |
| 统一群体/整体评价 | 环绕（circular track） | 环绕=去个体化，评价全局（Untouchables） | 需个体情绪时环绕=抽离 |
| 镜头内换主体 | 交叉点接力（cross-pan on track） | A 出画点=B 入画点，单镜换主体 | 硬切方向反转=迷失 |
| 揭示环境与主体关系 | 近景开→拉远揭空间（或反向） | 主体反应先于环境=悬念；环境先于主体=铺垫 | 开头即全景=无问答节奏 |

## Transitions 转场决策表（Ch21）

| 触发信号 | 选什么 | 为什么 | 反例 |
|---|---|---|---|
| 同一时空的镜头连接 | 硬切（cut） | 现在时，默认转场 | 切太多=碎 |
| 时间跳跃（小时级） | 切+蒙太奇（montage by cuts） | 现代惯例：蒙太奇用切不用叠化 | 30 年代式叠化蒙太奇=过时 |
| 抒情/时间流逝/段落软化 | 叠化（dissolve 0.5s+） | 叠化=诗意、时间桥 | 叠化救烂剪辑=创可贴 |
| 素材跳变需隐形缝合 | 软切（soft cut，1-10 帧叠化） | 亚隐形平滑，不减速 | 纯硬切=跳 |
| 意识模糊/昏迷/POV 失焦 | 焦点虚转场（focus in/out） | 虚焦掩护叠化=一体感 | 滥用=矫情 |
| 同一主体图形匹配 | 匹配剪辑（match shot，图形元素对位） | 图形连贯掩盖时空跳（Breathless 昼夜） | 无匹配点硬配=观众困惑 |
| 同场景内大小景别过渡 | 动作划（action wipe，前景物挡镜切） | 前景物扫过镜头时切=隐形剪辑点 | 主体动而非前景物=普通切 |
| 幕间/章节分隔 | 淡入淡出（fade to black） | fade=分隔，cut/dissolve=连接 | 场内用 fade=节奏断裂 |
| 结尾/超现实时刻 | 白转/定格（freeze frame） | 定格=句号（400 Blows）；白=空灵 | 定格过用=60 年代陈词 |

## 核心规则

1. **摇=转头，track=移动身体**——pan 不产生透视变化（近似静态），track/crane 才有
2. **不同速产生发展**——同速 track 是静态等价物；变速产生 3 平面景深和叙事推进
3. **机位在动作圈内=动感强化；圈外=全景扫视**——pan 的冲击力取决于圈内外
4. **先动主体，再动机位**——移主体比移机位便宜且灵活（Ch20 第一原则）
5. **fade 分隔、cut/dissolve 连接**——转场的功能二分法
6. **动作划=前景物挡镜，不是主体动**——区分 action wipe 与普通 cut on action

## 对 video-pipeline 的适配

- 阶段二 photographers 按"节奏意图"查表：建立→panoramic/crane 穿入；信息点→push-in；收尾→pullback/fade
- 口播管线转场默认硬切；段落边界用 fade to black；info_point 高潮可配 soft cut 或 push-in
- `per_second_directives.camera`：AI 生成视频运镜指令直接取本表"选什么"列（如 `slow pan left` / `push in` / `crane down`）
- 场景切换（B-roll 段间）：优先 action wipe 思路——生成段尾前景遮挡物，剪辑处自然衔接
- POV 段（idx%5==0）：crane follow-the-look 或 track 同速平行=主观感

## 升级路径（ponytail）

当前为纯决策表。若要程序化校验 shot_type 与运镜指令一致性（如 shot_type=medium 却配 crane down），升级为 check 脚本。
