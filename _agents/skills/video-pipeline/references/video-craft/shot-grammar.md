# 镜头语法方法论（Shot Grammar）

> 共享方法论，供 bilibili-video-pipeline 和 OpenMontage 通过 context pointer 加载
> 来源：TE_MAN 六列镜头表（3d-animation-short-generator）+ mv-subtitle 五大衔接锁 + minimalist 节拍表
> 用途：分镜增强 / 导演设计阶段，agent 按此决策

## 一、景别与信息密度

| 节奏意图 | 景别 | 运镜 | 用途 |
|---|---|---|---|
| 铺垫 | 远景/全景 | 缓推 | 交代环境、建立空间 |
| 建立 | 全景 | 横移 | 引入主体、展示关系 |
| 预备 | 中景 | 固定/轻推 | 聚焦动作前状态 |
| 冲击 | 特写/大特写 | 快推/手持 | 情绪高潮、关键信息 |
| 制动 | 中景 | 锁定 | 情绪回落、停留 |
| 稳定 | 全景 | 缓拉 | 收束、回味 |

规则：
- 景别交替，避免连续同景别（中景→中景→中景 = 幻灯片感）
- 特写用于"必须强调"的信息点，不是装饰
- 每镜一般 1 个主要动作，不贪多

## 二、连续性管理（Continuity Handoff）

每镜必须回答三问：
1. **上镜怎么来**——上一镜末帧的画面/道具/视线/姿态/情绪状态怎么延续到本镜
2. **本镜怎么走**——本镜末帧锁定什么状态给下一镜
3. **有无显式断裂**——翻场/跳切/时间跳跃必须显式标记（如 `HARD CUT — time skip: 2h`）

逐镜读 Continuity Handoff 列必须成连续链，不能有状态矛盾。

## 三、空间与身份锚点（Reference Anchors）

四子字段，全部强制：
1. **Fixed Landmarks**（固定地标）——场景卡里的固定物体 + 屏幕相对位置（如 `door-frame: right third`）
2. **Character Positions**（主体位置）——屏幕相对位置（左/中/右，上/中/下，前/中/后景）+ 朝向 + 初始姿态
3. **Exited Character Status**（离场状态）——上镜在但本镜不在的主体，注明离场位置和原因
4. **Lighting Baseline**（光照基线）——继承场景卡的光向 + 本镜修饰（如 `key: warm overhead, fill: cool bounce right`）

同场景下一镜的锚点必须继承或含显式连续性备注。

## 四、Hook 分布管理

受控词表：`visual-joke` / `reversal` / `suspense` / `tender` / `chase` / `reveal` / `callback` / `expression-beat`

分布自检：
- 每镜必须有 Hook Type
- 每连续 3 段至少 1 个 `reveal`/`reversal`/`callback`
- 开场段 + 收尾段必须带强 Hook（visual-joke/reversal/reveal/suspense/tender）

## 五、每秒指令（Per-Second Directives）

每段从 0s 到镜尾逐秒覆盖，每条含五要素：
1. **动作/姿态/表情**（挤压拉伸/预备/过冲/余波）
2. **镜头运动**（推/拉/摇/移/手持/锁定/环绕）
3. **空间位置**（主体在哪、拿什么、什么地标在画面内）
4. **音频线索**（旁白/台词/音效/呼吸/静默——或显式 `silent`）
5. **交接**（这一秒锁定什么状态给下一秒/下一镜）

子秒级关键节拍可用 `2.0-2.5s` 标记，但不能留时间缝隙。

## 六、自检门（分镜增强后强制）

1. **Hook 密度**：每段有 Hook；每 3 段至少 1 个 reveal/reversal/callback；首尾强 Hook
2. **单段时长**：≤15 秒，超长拆段
3. **锚点继承**：同场景下一段 Fixed Landmarks / Lighting 与上段一致或含显式备注
4. **每秒覆盖**：0s 到镜尾每秒有指令且含五要素
5. **连续性成链**：逐段读 Continuity Handoff 成连续链，无状态矛盾
6. **信息点命中**：每段设计能指回稿子的具体句子（事实锚点）

## 七、衔接锁（成片阶段用，mv-subtitle 借鉴）

1. **口型/人声衔接锁**：切点落在句间停顿/强鼓点，严禁元音中途硬切
2. **音乐节奏衔接锁**：剪辑点裁在音乐 1/4 或 1/8 拍
3. **色彩衔接锁**：跨段全局调色 + Film Grain 掩盖跨批次色差
4. **空间转场衔接锁**：上一镜末帧作下一镜首帧 + continuation of action
5. **文字动态衔接锁**：跨镜文字动效连贯（前镜扫出→后镜砸入）

## 升级路径（ponytail）

当前是纯知识参考。若未来要自动化校验（脚本读 enhanced.json 检查六项），升级为 `scripts/shot_grammar_check.py`，本文件保持知识源。
