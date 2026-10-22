# 证据链闭环：人审证据独立成桶、归档门不收窄也不放宽、真值帧冻结读取

> ADR 0008（docs/adr/0008-evidence-chain-closure.md）的实现规格。2026-10-22 grill 拍板，用户对 Q1–Q17 全数采纳。

## Problem Statement

判读闭环每天都产出「哪一段翻了」的结论，但结论**落账那一段**是断的，一共断了四处：

1. **账单从来没有**。判官（DSH 二判）每轮 rc=1，`_judge_second_opinion` 走 fail-open，`judge_block.ok` 恒为 False；归档函数 `_archive_evidence_buckets` 被 `if isinstance(judge_block, dict) and judge_block.get("ok")` 这道门焊在后面，于是三类证据桶目录与 `manifest.json` **一次都没写过**。
2. **行里也没有**。人工指认回调 `_flag_commit` 只做三件事：拷扁平帧到 `_frames/evidence/human_flag/`、`ledger.record_event("human_flags", …)`、`rotation_dataset.set_user_flag(...)`。它既不写 manifest 也不写行内 `evidence_frames`；而台账的 `record`/`record_event` **本身不落盘**（只有 `flush()` 才原子写），进程一崩这段留痕就丢。
3. **训练链取错帧**。`frames_of()` 用 `sorted()` 拼 `_frames/<shot>.jpg` + `_frames/<shot>_*.jpg`，字符 `.`(0x2E) < `_`(0x5F) 使**工作帧永远排第一**；`rotate4_pool()` 取 `fps[0]`。于是「指认翻转 → 重烧覆盖工作帧」之后，标签说翻转、像素却是重烧后的正立画面。
4. **最难的一类无人写**。`3-unfixed`（重烧后仍翻）在 `_archive_evidence_buckets` 的 docstring 里被声明「由 s08 收尾按 still_flipped 写」，但生产侧没有任何代码写它。

实测口径（2026-10-22，探针在盘复判而非转述）：

- 数据集 507 行；人工标注 30 行（True 30 / False 19 / None 458）；行内 `evidence_frames` **非空 0 行**；`ground_truth_verdict` 只有 6 行（全为 10-19）。
- 按当前选帧逻辑逐条探针复判这 30 行：**23 条一致、7 条不符**——10-19 六条（shot01/05/08/12/14/19）+ 2026-10-02 shot08（conf 0.903）。
- 10-19 的 `_frames/evidence/human_flag/` 有 27 个 jpg，**27/27 探针判翻转**。定罪帧一直在盘上且都是真翻转。

用户视角的痛点：我说这段翻了，训练链却喂它一张「已经烧正」的画面；而当时定罪的那一帧就在旁边。**真值没错，是账没有、链指错了。**

## Solution

把「识别到」之后的落账段补齐，让判读闭环能自答四问——账单有没有、行里有没有、训练链取不取得到、重烧之后取的还是不是那一帧：

- **人审证据独立成桶**：新增 `4-human-flag` 类别，人工指认翻转即刻落 manifest + 行内 `evidence_frames`，不等判官。
- **归档门不收窄也不放宽**：`1/2/3` 只认判官轮；判官缺席时不新增类别、不伪造标签（不把「本地判翻」塞进 `1-confirmed` 冒充判官共识）。可见性由行内 `evidence_frames` 承担。
- **真值帧冻结**：指认瞬间把定罪帧拷进不可变目录并记 sha256；读取侧对 `user_flagged=True` 的行按「行内证据帧 → 证据桶帧 → 工作帧」取真值，**sha256 不符即弃帧**，不回落工作帧。
- **帧失效显式标记**：帧已被覆盖且无冻结副本可回填的行，标 `frame_stale` 并写明原因；该行不进评测池、但**仍计入** stats 判定数。
- **`3-unfixed` 补写**：s08 收尾 `still_flipped` 非空时写，补上声明了却没人写的那一类。
- **存量清洗**：10-19 六条按 sha256 去重后回填行内 `evidence_frames` 指向冻结副本，原地修好；2026-10-02 shot08 无冻结副本，标 `frame_stale` 待人工复核。
- **关键路径显式 flush**：台账保持纯存储，落账点自己负责把台账拍到盘上。

## User Stories

1. 作为当班 agent，我要在人工指认翻转的**当下**就把定罪帧冻结落账，这样重烧覆盖工作帧之后仍然拿得回那一刻的像素。
2. 作为当班 agent，我要指认后立刻看到 manifest 里出现一条 `4-human-flag` 条目，这样「指认过」不再只存在于聊天记录里。
3. 作为当班 agent，我要指认后该行行内 `evidence_frames` 非空，这样读取侧不必依赖我是否记得去改台账。
4. 作为训练链使用者，我要 `rotate4_pool` 对人工点名翻转的行取到**冻结的定罪帧**，而不是取到重烧后的正立帧。
5. 作为训练链使用者，我要在被问「这行的真值帧是哪张」时拿到唯一确定答案，而不是「按文件名排序的第一张」。
6. 作为数据守护者，我要 sha256 作为硬条件：账上写的帧和盘上那张不一致时**弃帧告警**，绝不悄悄回落到可能已被覆盖的工作帧。
7. 作为数据守护者，我要一整行都没有可用帧时不进评测池，而不是拿一张错帧凑数。
8. 作为数据守护者，我要 `frame_stale` 显式挂在行上并写明原因，这样「真值是对的、帧是坏的」不会和「真值本身错了」混为一谈。
9. 作为数据守护者，我要 stale 行仍计入 stats 判定数，这样换岗门槛不会被坏帧稀释。
10. 作为判读链维护者，我要判官缺席时**不新增类别、不伪造标签**，这样证据桶词表保持闭合、统计口径不被污染。
11. 作为判读链维护者，我要 `1-confirmed` 等三个类别仍只由判官轮写入，这样「本地判翻 / 判官判翻 / 两者一致」仍可区分。
12. 作为判读链维护者，我要 `3-unfixed` 在 s08 收尾按 `still_flipped` 真实补写，这样「重烧后仍翻」这一类终于有素材。
13. 作为判读链维护者，我要 `_archive_evidence_buckets` 的 docstring 与实现一致，不让下一个人再被「说了却没写」误导。
14. 作为当班 agent，我要人工指认、判读收尾这类关键落账点显式 flush 台账，这样中途崩溃不再丢证据。
15. 作为当班 agent，我要台账组件保持纯存储语义（不内置「哪些键该立刻落盘」的白名单），这样存储策略不藏进通用组件。
16. 作为数据守护者，我要 10-19 那六条标注**原地修好**而不是剔除，因为定罪帧就在盘上、真值也是对的。
17. 作为数据守护者，我要重复拷贝按 sha256 去重，这样同一张帧的 27 份副本不会变成 27 条证据。
18. 作为数据守护者，我要 2026-10-02 shot08 这种没有冻结副本的旧标注被标 `frame_stale` 等人工复核，而不是假装它没问题。
19. 作为下游读者，我要 manifest 的人审条目 `round` 字段是 `human_flag`（与证据目录名同源），这样人审证据与判官轮次可区分且不制造近似重复条目。
20. 作为下游读者，我要行内 `evidence_frames` 里的条目带 `{frame, sha256, round, category}`，这样每一张帧都能追溯到「哪一轮、哪一类、是不是盘上那张」。
21. 作为实现者，我要所有对数据集的写入仍走 `write_rows` 单一写口，这样真值单一事实源不被旁路写坏。
22. 作为实现者，我要 `set_evidence_frames` 是**后置**写口（只补字段、不整行 upsert），这样「当时判正常」不会被改写成「现在判翻转」。
23. 作为实现者，我要有一条纯函数级的单一解析点承担「优先级 + sha256 + stale」，这样 5 个消费方共享同一口径，而不是各改各的。
24. 作为实现者，我要 `frames_of` 仍保持**纯物理列举**语义，这样既有的存在性判断与导出路径不被动到。
25. 作为验收者，我要重烧覆盖工作帧之后重新取帧仍得到冻结帧——这条端到端断言是本次唯一的硬验收。
26. 作为验收者，我要 manifest 条目、行内字段、训练素材三处指向**同一路径**，这样「账、行、链」三问是同一条链而不是三条。
27. 作为下一个接手的人，我要这次的四问写进 CONTEXT.md 与 ADR 0008，这样「证据链闭环」有定义可查。
28. 作为下一个接手的人，我要测试落点复用既有缝（指认回路、rotate4 素材池、生产桶归档），这样不新增第三个同职责测试文件。

## Implementation Decisions

**模块分工**（按职责称模块，不绑行号）：

- `rotation_dataset`：类别词表与数据集写口。
- `rotation_groundtruth`：读取侧解析点与训练素材口径。
- `clip_train`：`rotate4_pool` 素材池。
- `run_pipeline`：driver 侧落账点（人工指认回调、归档函数、s08 收尾）。
- `ledger`：**不改**。保持纯存储，只在调用点调 `flush()`。

**Schema / 契约变更**：

1. `EVIDENCE_CATEGORIES` 由三元组扩为四元组，新增 `4-human-flag` = 人工指认翻转（正类）。`CATEGORY_NOTES` 同步补一条人审口径的说明。
2. manifest 条目在既有形状（`category / sid / round / frame / sha256 / …`）之上承载人审来源：人审条目 `round = "human_flag"`，与证据扁平目录名 `human_flag` 同源；**不用时间戳**做轮次（避免同帧多次指认产出近似重复条目）。去重键沿用 `(category, sid, round)`。
3. 行内 `evidence_frames` 的元素由「路径字符串」升级为 `{frame, sha256, round, category}`。为兼容存量，读取侧必须同时接受纯字符串形态（视为无 sha256 的弱证据，需现算比对）。
4. 行新增两个字段：`frame_stale`（bool）与 `frame_stale_reason`（字符串）。与人审真值一样，它们属于人审回填字段，**不被模型结论覆盖**。
5. 新增后置写口 `set_evidence_frames(keys, entries)`：过 `write_rows` 单一写口，只补/覆盖 `evidence_frames`（及必要的 stale 标记），不做整行 upsert。人工指认回调里紧跟 `set_user_flag` 调用。

**读取侧契约**：

6. 新增一个纯函数作为**单一解析点**：输入一行，输出该行的真值帧（或「无可用帧」）。语义顺序固定：`user_flagged=True` 时按「行内证据帧 → 证据桶帧 → 工作帧」；命中候选后做 sha256 校验，不符则弃该帧并告警（**不回落工作帧**）；整行无可用则返回空。
7. 该解析点是 sha256 校验的唯一实施处；`frames_of` 保持纯物理列举不动，`row_material` / `row_frames` / `rotate4_pool` / `jev_calibrate` / `stats` / `export` 的物理列举语义不变，只有 `rotate4_pool` 改调解析点取真值帧。
8. `rotate4_pool` 的池语义保持：人工点名的行进 `pos`（评测真值、不进训练），其余进 `pool` 按整期日期 md5%3 切分；变化只在「取哪一帧」。`frame_stale` 行直接从两个池中剔除。
9. `frame_stale` 行的计数口径：`stats()` 的判定数（人工真值 pos+neg 行）**照常计入**；证据行覆盖统计同样保留该行。

**写侧契约**：

10. 人工指认回调在**冻结帧之后、重烧之前**落账三件事：manifest 追加 `4-human-flag` 条目（含 sha256）、行内 `evidence_frames` 追加（含 sha256）、台账 flush。冻结帧保存沿用既有扁平目录 `_frames/evidence/human_flag/`（本次不挪目录，避免动确认门④的既有路径约定）。
11. 归档门保持现状语义：`_archive_evidence_buckets` 仍在判官成功时写 `1-confirmed` / `2-judge-found`；判官缺席时**什么都不写**。docstring 必须与实现一致（删除或改写「本轮不写 3-unfixed」里与实现冲突的说法）。
12. `3-unfixed` 由 s08 收尾在 `still_flipped` 非空时补写：条目来源是该轮已在盘上的定罪帧（与 `1/2` 同构），`round` 取该段最后一次复检的轮次。
13. 台账 flush 点固定三处：人工指认回调末尾、帧检查（判读轮）末尾、s08 收尾数据集写入之后。**否决**在 `record_event` 内部做白名单自动 flush，也**否决**每次写入都 flush。

**存量清洗**：

14. 10-19 六条（shot01/05/08/12/14/19）：在 `_frames/evidence/human_flag/` 里按 sha256 去重选一份代表帧，回填行内 `evidence_frames`（含 sha256、`round="human_flag"`、`category="4-human-flag"`）。同一 shot 的多个副本 sha256 相同则只留一条。
15. 2026-10-02 shot08：盘上无冻结副本且工作帧已不可信，标 `frame_stale=true` + 原因（帧已覆盖、无冻结副本），保留 `user_flagged=True` 与原结论。
16. 清洗是一次性数据操作，必须可重入（重跑得到同一结果），且不触碰其它期次的行。

## Testing Decisions

**什么算好测试**：只断言外部行为——盘上产物（manifest 条目、行内字段）与消费结果（`rotate4_pool` 给出的路径），不断言内部函数调用顺序、不断言私有变量。

**三处既有缝，不新开缝**：

1. **写侧**：driver 的人工指认回调（`test_human_flag_loop.py` §5 已有裸实例 + `patch_set_user_flag` 桩）。新增小节断言：指认后 manifest 出现 `4-human-flag` 条目且 sha256 与盘上帧一致；行内 `evidence_frames` 落盘且与 manifest 同路径；台账在回调结束前已 flush。
2. **读侧**：`clip_train.rotate4_pool(dataset=…, date_root=…)`（`test_clip_train.py:73` 已在用可注入缝）。新增小节断言：工作帧被重烧覆盖后，`pos` 里该行取到的仍是冻结副本；sha256 不符时该行整体从池中消失；`frame_stale` 行不出现。
3. **归档门**：`Driver._archive_evidence_buckets`（`test_evidence_production.py` 既有先例）。新增断言：`judge_block.ok` 为 False 时**不写 manifest**（门保持关）；判官成功时三类与四类桶词表一致。

**夹具**：沿用现有约定——临时期次布局 `<root>/<date>/video/每日新中国b站/流水线/<slug>/_frames`，不碰活数据集（`ROTATION_DATASET_READONLY=1`）。

**既有断言会变红**：`test_evidence_train_chain.py` 里 `set(by_cat) == set(rd.EVIDENCE_CATEGORIES)` 这类等号断言会因四元组而红，须同步改（不是放宽，是把 3 类改成 4 类的显式口径）。

**验收四条**（夹具必做 + 下一期真期实跑观察一次，两者都要）：

- manifest 有条目且 sha256 与盘上一致；
- 行内 `evidence_frames` 与 manifest 指向同一路径；
- `row_material` 对该行返回 ≥1 条证据素材；
- **重烧覆盖工作帧后重新取帧，仍得冻结帧。**

## Out of Scope

- **判官 rc=1 本次不修**：证据链修法不得以判官能跑通为前提（单开票）。
- `run_driver_bg.py` 与 s13 打点（热身票，属工作纪律改进入口）。
- 视频号 10-19 补发与 `sph_publish_v5.py` 相关动作（用户明令禁止重跑该脚本；本期视频号既未发布也未建草稿，兜底件保留待手发）。
- 8.4 其余五条环境改进候选（派生缓存新鲜度窄规则、视频号结果 JSON、预检子集、pipeline-stages 文档、SKILL 重复说明）。
- 10-19 与 2026-10-02 之外的存量标注回填（本次只清已实测确认的两批）。
- 判读模型准确率、阈值与校准（属换岗门槛与探针线，与本票无关）。

## Further Notes

- 决策全文与推翻记录见 ADR 0008；术语见 CONTEXT.md 的「证据桶 / 真值帧冻结 / 帧失效 / 证据链闭环」四条。
- 边界事实（不要再重新勘察）：`_archive_evidence_buckets` 的 manifest 写入在函数末尾**无条件**执行，所以门一开文件必存在；去重键 `(category, sid, round)` 意味着同一帧同轮重复归档不会膨胀。
- 10-19 的 27 个 human_flag 副本含字节完全相同的重复（copy2 保留源 mtime），去重必须按 sha256 而非文件名/时间。
- 票切分（`/to-tickets` 展开）：① 四类词表 + 指认即落账（含 `set_evidence_frames`）② 读取侧优先级 + sha256 + stale ③ 归档门语义收紧 ④ 存量清洗 ⑤ `3-unfixed` 收尾补写 + 热身票。依赖：②④ 依赖 ①。
- 本次不重跑整期 driver；验证走夹具 + 下一期实跑观察。
