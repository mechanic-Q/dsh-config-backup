# Chinese Celestial Palace（中式天宫·仙侠）

> 来源：Liblib「Lala罐头的中式天宫」skill（chinese-celestial-palace-imagery）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 定位：仙侠影视概念图 / 分镜关键帧 / 天宫世界观 / 中式仙侠题材

## 视觉语言

- **空间层级律**（宏大空镜的默认结构，architecture-to-human ratio ≥ 8:1）：前景框（柱/拱门/栏杆，占画幅 10-20%）→ 中景云海/廊道/广场（含极小人物）→ 背景主殿或远山。**人物主镜头（中近景/特写）不受此律约束**，与宏大空镜交替出现形成节奏（2026-09-05 取消人物/近景限制）
- **人物比例**：远景空镜中人物身高 = 画幅高度 1/15 至 1/20（视觉锚点）；**人物主镜头可为中近景/特写**（半身至面部，服装/配饰/情绪特写）。是否用人物、用多近，由每段内容独立判断：台词的信息/情绪落在人身上 → 人物镜头；宏观/环境叙事 → 人物作锚点或纯空镜；不用机械配额
- **对称轴**：宫殿/廊道/祭坛/广场默认严格中轴对称；仅情绪时刻（孤独/转折）可用不对称
- **框景嵌套**：前景柱/月洞门/拱门/龙柱框住中景人物，形成画中画多层结构
- **悬浮元素**：每镜至少一个悬浮/凌空结构（天桥、崖台、浮岛、云台、悬空瀑布），营造"悬于天地之间"的缥缈感
- **纵深**：廊道/桥/阶梯必须延伸到灭点（一点透视）；仰拍巨阶巨柱或俯拍云上行走放大神圣垂直感

## 色彩

- **冷主暖辅**：环境/建筑 = 低饱和冷蓝灰（月白、冰蓝、石青）；人物服装 = 1-2 个高饱和暖色唯一视觉焦点（朱红、翠绿、鎏金）
- **云海**：白雾漫射、超低饱和
- **夜景**：深蓝夜空 + 冷月光 + 少量暖光点
- **比例**：冷色 70% / 暖色 30%（暖色集中于人物服装 + 少量殿顶金饰）；环境去饱和 60-70%，服装饱和 80-90%

## 运动语言

- 云雾缓慢涌动（慢速推近/横移强调神圣纵深）
- 仰拍巨构垂直上升 / 俯拍云海漫游
- 远景空镜：人物极小，动作克制（行走/伫立/跪拜）；
- 人物主镜头：允许完整表演（回眸/抬手/执物/衣袂翻飞），人物成为主体，建筑与云海退为背景纵深
- 丁达尔圣光斜射缓慢扫过画面

## 负向约束

- 禁现代元素（霓虹/电线/现代建筑/屏幕 UI）
- 人物仅限**虚构仙侠角色**：禁真实人物/可辨识公众人物脸；人物画面内不说话（口播是旁白，嘴唇闭合）
- 禁混搭透视（俯视前景 + 仰视背景同时出现）
- 禁高饱和彩色环境（环境必须低饱和冷调）
- 禁可读文字/Logo/水印

## 转译到 H3 prompt 的英文关键词

```
Chinese celestial palace, ethereal xianxia realm in the heavenly court,
vast architecture dwarfing tiny human figures,
when the shot centers on a figure: medium or close shot of a fictional xianxia character in flowing robes, colossal architecture and mist still towering behind,
three-tier spatial layers: foreground columns/archways framing, middle sea of clouds,
background grand palace silhouette at horizon,
strict central-axis symmetry, one-point perspective vanishing to infinity,
at least one floating structure (sky bridge, cloud terrace, floating island),
cold blue-gray low-saturation environment, moon white and ice blue tones,
high-saturation warm accents only on character costumes (vermilion, emerald, gold),
diffused soft light with ethereal mist, no harsh shadows,
descending sacred light beams, subtle rim light on flowing robes,
deep blue night sky with cold moonlight, misty atmospheric depth
```

## 对 video-pipeline 的适配

- 最适合：中式仙侠题材、天宫世界观、神话/古典故事、概念设定类内容
- 与 H3 契合：H3 擅长宏大场景 + 云雾大气 + 对称建筑渲染
- 与 realistic 对比：天宫风 = 风格化的东方幻想，realistic = 现场纪实
- 人物 = 远景视觉锚点或人物主镜头主体（2026-09-05 起允许中近景/特写）；真人红线由「仅虚构仙侠角色」约束保证，与人物远近无关

## 升级路径（ponytail）

纯视觉语言参考。若以后要自动化校验（读导演设计检查是否用了本风格元素），升级为 `styles/check_style.py`。
