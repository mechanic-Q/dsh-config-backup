# 编剧钩子/转折/递进/落点决策表

> 来源：麦基《Story》Ch8 The Inciting Incident / Ch9 Act Design / Ch10 The Scene / Ch12 Composition & Setups-Payoffs / Ch13 Crisis-Climax-Resolution + 特鲁比《The Anatomy of Story》Ch1 Story Space / Ch2 Premise / Ch4 Structure (七步法)
> 用途：video-pipeline 阶段零（编剧环节）加载，5 幕结构：钩子→冲突→原理→类比→落点；前 2-3 秒生死线；黄金三秒
> 对齐字段：hook_sentence / act_structure / turning_point / setup_payoff / ending_value

## 决策表（触发信号= agent 可从稿件文本直接判断）

| 触发信号 | 选什么 | 为什么 | 反例 |
|---|---|---|---|
| 首句是平稳陈述/交代背景（无事件发生） | 改写为"失衡事件"：坏平衡被外部事件打破 | 麦基：Inciting Incident 必须 radically upset 主角生活平衡并 hook 观众（情绪+理性双钩） | 伪失衡=只是心情不好，无外部事件，观众无 MDO（Major Dramatic Question） |
| 首句含数字/反差/设问/反常识断言 | 保留为钩子，且后续紧跟冲突 | 麦基：hook = 让观众问"Why?/What happens next?" | 只抛噱头不接冲突=Cheap Surprise，观众感觉被骗 |
| 首段只描述状态、无人物行动 | 引入"期望 vs 结果"的缺口（Gap） | 麦基：gap between expectation and result 是故事之核，转折点=打开缺口 | 期望=结果=无转折，节奏平 |
| 转折处主角靠运气/巧合/天降过关 | 改为两种合法转折之一：行动(action)或揭示(revelation) | 麦基：场景只能靠行动或揭示两种方式转向 | Cheap Surprise：用巧合蒙混，观众失去洞察快感 |
| 连续两段内容强度持平 | 递进升级：风险/筹码/揭示逐步加大 | 麦基 Progressive Complications；特鲁比：让揭示越来越大直到最深秘密 | 强度先升后降=泄气；重复同强度=拖沓 |
| 悬念段观众与主角知道的信息一样多 | 提前让观众多知道一步（戏剧反讽） | 麦基 Suspense：悬念=观众知道结局在逼近而角色不知（suspense sentence） | 信息全藏=廉价 surprise；信息全给=无悬念 |
| 主角无短板/缺失，开场就完美 | 补"需要"(need)：心理 need + 道德 need | 特鲁比七步法第 1 步：weakness & need 驱动全部成长弧 | 无 need=角色无变化=落点无意义 |
| 对立面与主角各追各的目标 | 让对手与主角争夺同一目标 | 特鲁比：同目标才被迫反复直接冲突 | 不同目标=两条平行线，无 battle |
| 正文含说教式结论/直白口号 | 改为用动作与对立论证主题（moral argument） | 特鲁比：主题=通过行动论证，对白说教=结构没织入主题 | 口播直接喊口号=说教（didacticism） |
| 结尾"一切回到原状" | 落点改为不可逆+永久改变 | 麦基：end change 必须 absolute and irreversible；特鲁比：new equilibrium=永久变化 | 主角没变=观众白看 |
| 结尾是明显对错/善恶二选一 | 升级为两难：两害相权或两善不可兼得 | 麦基：对错选择 trivial，两难(irreconcilable goods / lesser of two evils)才是 Crisis | 明显二选一=无张力，暴露主角深度为零 |
| 素材有铺垫但结尾未回收 | 落点回收全部 setup（payoff） | 麦基 Setups/Playoffs：埋的每一枪必须响 | 埋了不响=断线索，观众记住铺垫=记恨 |
| 类比/意象只出现一次 | 复现并升级为象征系统（symbol web） | 特鲁比：symbol web 让主题在物件层面反复论证 | 一次性意象=装饰，不承担论证 |

## 核心规则

1. **钩子=失衡事件，不是标题党**——首句必须是一个打破平衡的具体事件，并让观众产生"为什么/接下来会怎样"的追问（麦基 MDO）
2. **转折只有两种合法方式：行动或揭示**——其余都是巧合作弊
3. **强度单调递增**——每段比前段风险更大、揭示更深；揭示逐级放大到最深秘密
4. **悬念=观众比角色多知道**——知道结局在逼近，才产生揪心的等待
5. **落点必须不可逆**——主角经历两难选择后发生永久改变，且这个改变不能撤销
6. **主题靠动作论证**——说教是没把主题织进结构的表现

## 对 video-pipeline 阶段零的适配

- 阶段零 5 幕（钩子→冲突→原理→类比→落点）逐幕套本表：钩子=行1-2，冲突=行3-5，原理=行9，类比=行13，落点=行10-12
- 前 2-3 秒生死线 / 黄金三秒：行 1-2 强制应用——首句必须含失衡事件、反差或设问，否则判"钩子不成立"打回重写
- 口播管线无完整剧情时：行 3（期望缺口）、行 5（递进）优先级最高，保证单条口播也有起伏
- 短内容只保最小闭环：钩子(行1-2) → 冲突(行3/5) → 落点(行10-11)，其余行按需启用

## 升级路径（ponytail）

当前为静态决策表。若阶段零接入自动校验（检查 hook_sentence 是否命中失衡/反差/设问模式、段落强度是否递增），升级为 check 脚本。
