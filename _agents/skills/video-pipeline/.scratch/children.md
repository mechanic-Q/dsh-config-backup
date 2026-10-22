=========== #190 ===========
TITLE: 人审指认即落账：四类证据桶 + 指认回调写 manifest/行内/台账
BODY:
## Parent

#189

## What to build

当有人在确认门④或 CLI 指认某段翻转，这次判断要在**当下**变成可追溯的账：证据桶词表新增 `4-human-flag`，定罪帧冻结并记 sha256，`manifest.json` 追加条目，数据集该行行内 `evidence_frames` 追加同一张帧，台账在回调返回前已经落盘。判官在不在场都不影响这条链。

## Acceptance criteria

- [x] 指认一段后 `_frames/evidence/manifest.json` 出现 `category=4-human-flag`、`round=human_flag` 的条目，且 `frame` 与 `sha256` 与盘上冻结帧一致
- [x] 同一行行内 `evidence_frames` 出现**同路径**条目，`{frame, sha256, round, category}` 四字段齐
- [x] 同一 `(category, sid, round)` 重复指认不产生重复条目（沿用既有去重键）
- [x] 指认回调返回时台账已 flush：`human_flags` 顶层平键可读到本次记录
- [x] `EVIDENCE_CATEGORIES` 为四元组，`CATEGORY_NOTES` 补齐人审口径说明
- [x] 新写口只补 `evidence_frames` 字段，**不改**该行其它字段（`vision_verdict` / `user_flagged` / `judge_*` 原值不变），且仍过 `write_rows` 单一写口
- [x] 既有类别等号断言（证据训练链测试里 `set(by_cat) == set(EVIDENCE_CATEGORIES)` 一类）同步改为四类口径并全绿
- [x] 全量测试跑器绿

## 阶段判定（闸门）

标签 `ready-for-agent`、无 `needs-triage`、无既有实施记录与评论 → 直接进实施阶段，不需要先做诊断。

## 已查阅范围（先查后问，未复用旧笔记另行新查）

仓内既有材料已足够，未新起外部检索：

- `docs/adr/0008-evidence-chain-closure.md`（决策 1 四类桶 / 3 指认即冻结与读取优先级 / 4 后置回填写口 / 5 frame_stale / 7 flush 三处）
- `CONTEXT.md` 第 43-48 行四条术语（人工指认 / 证据桶四类 / 真值帧冻结 / 证据链闭环）
- `scripts/rotation_dataset.py`（`EVIDENCE_CATEGORIES` / `CATEGORY_NOTES` / `write_rows` 单一写口 / `set_user_flag` / `load_evidence_buckets` 的类别过滤 / `sha256_file`）
- `scripts/run_pipeline.py`（`_archive_evidence_buckets` 与去重键口径、`_flag_commit`、`_sha256_file`、现有 `ledger.flush` 点）
- `scripts/ledger.py`（`record_event` 只进内存增量、`flush` 才原子落盘）
- `scripts/rotation_groundtruth.py`（`row_material` / `row_frames` 对行内 `evidence_frames` 的消费方式）
- 相关测试：`test_human_flag_loop.py`、`test_evidence_train_chain.py`、`test_evidence_production.py`、`test_mechanical.py`、`run_tests.py`

## 实施方案

1. 词表与口径（`rotation_dataset.py`）：`EVIDENCE_CATEGORIES` 扩四元组、`CATEGORY_NOTES` 补 `4-human-flag`、新增 `HUMAN_FLAG_CATEGORY` / `HUMAN_FLAG_ROUND` 常量与 `evidence_frame_path` 单一解析口（行内条目两种形态归一）。
2. 后置写口 `set_evidence_frames`（ADR 决策 4）：只补 `evidence_frames`（四字段投影，多余键丢弃）+ 可选 `frame_stale`，过 `_guard_write` 与 `write_rows`，按帧路径幂等，返回 `(updated, missing)`。
3. 指认回路（`run_pipeline.py`）：`_flag_commit` 依次落「冻结帧 → manifest 条目（`4-human-flag` / `human_flag`，去重键沿用 `(category, sid, round)`，重复指认复用原条目原帧）→ 台账 `human_flags` → 真值回填 → 行内 `evidence_frames` → `ledger.flush()`」；抽出 `_load_evidence_manifest` / `_write_evidence_manifest` 与分类归档共用，分类归档控制流不变（留给 #192）。
4. 读取侧归一（`rotation_groundtruth.py`）：`row_material` / `row_frames` 改走 `rd.evidence_frame_path`，rowlink 条目带 category/round 与桶 note。
5. 测试：训练链测试改四类显式口径（CATS 补人审桶、`n_ev` 5→6、dict 形态 rowlink），指认回路测试补 t11/t12/t13。

## 自查（对抗式）

除单测外，另用真实 `Driver` + 真实 `Ledger` + 临时数据集跑了一遍端到端探针（`4-human-flag` 条目 1 条、frame 与 sha256 与盘上一致、行内同路径、重复指认仍 1 条、新开 Ledger 读盘可读、`load_evidence_buckets` 读到 `category_note`、`row_material` 出 `evidence/4-human-flag` 与 legacy rowlink、其它字段原值不变）。据此补了两处实现：

- 真值回填与行内证据帧拆成两个独立 try：此前 set_user_flag 抛错会连坐行内证据写入。
- 重复指认走「先查清单复用原条目」而不是重拷冻结帧：否则同秒会在 `evidence/human_flag/` 堆 `_02` 多份、行内帧路径漂移，破坏 AC2「同路径」。

`python scripts/run_tests.py`：**44 PASS / 0 FAIL**（`test_mechanical` 违规数不增）。

## 落地

PR #196（`feat/190-human-flag-evidence`），首行 `closes #190`，门禁 `python scripts/merge_gate.py 196`。

## 不在本票范围

ADR 0008 决策 7 的另两处 flush（`_check_frames` 末尾、s08 收尾）本票未动——本票只补「指认回调末尾」那一处；另两处随 #191/#192 一并处理。
COMMENTS:
- 已落地：PR #196 经门禁合并（main `862bbbc`，squash），#190 自动关闭。

## 落账链路（指认一次 → 三处账 + 一次 flush）

`_flag_commit` 现在的顺序：冻结定罪帧 → 写 `manifest.json`（`4-human-flag` / `human_flag`，`frame` 记冻结副本本身、`sha256` 对盘上那张）→ 台账 `human_flags` 顶层平键 → 数据集真值回填（`set_user_flag`）→ 行内 `evidence_frames`（`set_evidence_frames`，四字段 `{frame, sha256, round, category}`）→ `ledger.flush()`。

- 去重键沿用 `(category, sid, round)`：重复指认（`human-reflag`）先查清单，命中即复用原条目——不重拷帧、行内路径不漂移。
- 词表扩四元组 `("1-confirmed","2-judge-found","3-unfixed","4-human-flag")`，`CATEGORY_NOTES` 补 `人工指认翻转（人审正类；与判官四象限不同源）`；`load_evidence_buckets` 的类别过滤自动放行第四类。
- 读取侧 `row_material` / `row_frames` 走 `rd.evidence_frame_path` 归一行内两种形态（存量纯路径 / 四字段 dict）。
- 分类归档与会话共用 `_load_evidence_manifest` / `_write_evidence_manifest`，`_archive_evidence_buckets` 控制流未动（归档门口径留给 #192）。

## 验收自查

| AC | 证据 |
|---|---|
| 1 manifest 条目 frame/sha256 与盘上一致 | `test_human_flag_loop` t11 + 端到端探针（`sha256 == rd.sha256_file(frame)`，64 位） |
| 2 行内同路径 + 四字段齐 | t11（`frame == manifest frame`、`sha256` 同） |
| 3 重复指认不重复 | t11（两次指认后 manifest 仍 1 条、行内幂等） |
| 4 回调返回前已 flush | t12（真实 `Ledger` 落盘后新开 `Ledger` 读盘可读 `shot08`） |
| 5 四元组 + 人审口径 | `test_evidence_train_chain` 显式断言 + `category_note` 非空 |
| 6 只补 evidence_frames、仍过 write_rows | t13（四键投影、多余键丢弃、其它字段原值不变、`frame_stale` 可标、missing 如实报） |
| 7 类别等号断言改四类 | `set(by_cat) == {四类}` + `rd.EVIDENCE_CATEGORIES == (四元组)` |
| 8 跑器绿 | `python scripts/run_tests.py` → 44 PASS / 0 FAIL，`test_mechanical` 违规数不增 |

## 对抗式自查抓到的两处返工

1. 真值回填与行内证据帧原在同一 `try`：`set_user_flag` 抛错会连坐行内证据。拆成两段独立 `try`。
2. 重复指认原会重拷冻结帧（同秒堆 `_02`/`_03`、行内路径漂移，直接破坏 AC2「同路径」）。改为先查清单复用原条目。

## 下一步

另两处 flush（`_check_frames` 末尾、s08 收尾）不在本票范围，随 #191 / #192 处理；#191/#192/#193 现已被解锁（#190 是本票链的前置）。
=========== #191 ===========
TITLE: 读取侧真值帧：优先级 + sha256 硬校验 + stale 剔除
BODY:
## Parent

#189

## What to build

训练链取帧时，人工点名的行拿到的是**当时定罪的那一帧**，而不是按文件名排序碰巧第一、可能已被重烧覆盖的工作帧。账实不符就弃帧告警，绝不静默回落。没有可用帧的行干脆不进池。

## Acceptance criteria

- [x] 存在单一解析点承担「行内证据帧 → 证据桶帧 → 工作帧」优先级；`user_flagged=True` 时先取证据帧
- [x] sha256 与账上不一致 → 弃该帧并告警，**不回落工作帧**；整行无可用帧 → 返回空
- [x] `rotate4_pool` 对该行取到冻结帧路径；把工作帧覆盖重写后再取，仍是冻结帧（本次唯一硬验收）
- [x] `frame_stale` 行不出现在 `pool` / `pos` 任何一边
- [x] `frames_of` 保持纯物理列举语义，其余物理列举消费方（导出/统计/校准）行为不变
- [x] 纯字符串形态的存量 `evidence_frames` 仍可消费（无 sha256 时现算比对）
- [x] 对应测试小节全绿


## 落地

PR #197（`feat/191-truth-frame-resolve`），首行 `closes #191`，门禁 `python scripts/merge_gate.py 197` → squash 合并 main `705d6a4`。

状态：**已交付并关闭**（`truth_frame` 单一解析点 + `rotate4_pool` 改经它取帧 + 5 节测试）。遗留：无。

COMMENTS:
- 已落地：PR #197 经门禁合并（main `705d6a4`，squash），#191 随 `closes` 自动关闭。

## 落点：读取侧唯一解析点

`scripts/rotation_groundtruth.py` 新增 `truth_frame(row, references_dir=None, date_root=None, frames_root=None, buckets=None)`；`scripts/clip_train.py` 的 `rotate4_pool` 是它唯一的读取侧调用方（`scripts/clip_train.py:164`）。

候选顺序：行内 `evidence_frames` → 该行证据桶条目 → 工作帧。每个候选必须「在盘 + 账上 sha256 相等」；证据存在过但全部不可用 → 整行空串，**不回落**重烧后的工作帧（那一版正是被推翻的画面）。`frame_stale` → 空串；未点名行 → 工作帧首帧（选材口径不变）。存量纯路径字符串按路径回查该行桶条目 sha256、现算比对。

`buckets` 参数：`rotate4_pool` 先预取一次 `gt.evidence_by_row(rows)` 再透传，避免逐行重扫 manifest（507 行里 30 条点名行 = 30 次全扫）。

不动：`frames_of` / `row_material` / `row_frames` / `stats` / `export` / `jev_calibrate` 的物理列举语义。

## 验收自查（对抗式）

| AC | 证据 |
| --- | --- |
| 单一解析点承担优先级、点名先取证据帧 | 候选顺序实现；grep 确认 `rotate4_pool` 是唯一读取侧调用方 |
| sha 不符 → 弃帧告警、不回落；整行无可用帧 → 空 | `test_clip_train.py` §7（账 `0`×64 → 行两侧都不出现，stderr 同现 sha256 与帧路径） |
| 覆盖重写工作帧后仍取冻结帧（唯一硬验收） | §6：先取一次得 `FROZEN-CONVICTED-FRAME`，`write_bytes(work6, "WORK-AFTER-REBURN-DIFFERENT")` 后重取，内容与 sha 均不变 |
| `frame_stale` 两侧都不出现 | §8（点名+证据齐全、未点名+工作帧在盘，两行都不在 `tr+te+pos`） |
| `frames_of` 纯物理列举、其余消费方不变 | §10；其余消费方零改动 |
| 纯字符串形态存量证据仍可消费 | §9（shot01 账实一致取证据帧；shot02 写账后篡改 → 整行消失；shot03 取桶帧；shot04 桶账不符且无工作帧 → 整行无可用帧） |
| 对应测试小节全绿 | `test_clip_train.py` 10 节；全量 44 文件 |

## 踩坑

1. **逐行 `evidence_by_row` = 每次全扫 manifest**：改成 `rotate4_pool` 预取 `buckets` 透传。
2. **`evidence_by_row` 键口径**是 `(episode[:10], "daily-china-" + episode[-8:], sid)`：slug 不合格的期次链接不上，点名行回落工作帧（与改前一致，不炸）；本票未改该口径，已在 `truth_frame` docstring 写明。
3. **告警前缀 ⚠️ 在 cp936 编不出**：直接 print 会让裸跑以 `UnicodeEncodeError` 结束（测试约定 §9）→ `_warn` per-call 降级转义（真实编码 + backslashreplace），并加 gbk 流测试。
4. **合成 episode 的既有调用**必须把 `references_dir`/`frames_root` 指向临时树，否则去扫仓内 `references/` 与本机 E: 盘。
5. **第 5 节真数据集 `rpos >= 20` 没变红**：30 条真值仍全在——25 条无桶行回落工作帧，5 条取 10-15 期离线桶的定罪帧（19 条账 sha 全 MATCH）。

## 验证

- `python scripts/test_clip_train.py` → **10 节全部通过**；裸跑（cp936、无 `PYTHONIOENCODING`）退出码 0
- `python scripts/run_tests.py` → **绿=44 红=0 known-red=0**
- `python scripts/merge_gate.py 197` → 三关全过（CI 绿 / 本地 0 / CLEAN），主干复查绿

=========== #192 ===========
TITLE: 归档门语义收紧：判官缺席不写、不伪造类别、docstring 与行为一致
BODY:
## Parent

#189

## What to build

「判官缺席就不归档」这道门保持**关闭**，但不再靠一句 docstring 自证：函数行为、文档、测试三者一致，且明确不把本地判读伪装成判官类。可见性由行内证据帧承担，不靠往闭合词表里塞无定义类别。

## Acceptance criteria

- [x] `judge_block.ok=False` 时**不写** manifest，且不新增任何类别
- [x] 判官成功时归档的类别集合 ⊂ 四类词表，且**不含** `4-human-flag`（人审只由指认回调写）
- [x] 函数 docstring 与实际行为一致，`3-unfixed` 的归属写清楚（谁写、何时写）
- [x] 测试把这道门两个方向都钉住

## 实施记录（2026-10-22）

已落地并合并：PR #198 / `8e34ff5`（`fix(#192): 归档门在函数自身也关上，类别只走白名单`）。四道 AC 全部满足，收口方式如下。

**AC1 · 判官缺席不写 manifest、不新增类别** —— `scripts/run_pipeline.py:_archive_evidence_buckets` 顶部加同一道门：`judge_block` 非 dict 或 `ok=False` → 直接 `return 0`，位置在建类别目录 / 读清单 / 写清单之前。此前该函数自身没有任何守卫，只在调用点 `_check_frames` 挡过一次；直接调用（测试 / 探针 / 未来调用点）时会走到末尾无条件 `_write_evidence_manifest`，落一份空清单——等于对「本轮无类别证据」作伪证，`judge_block=None` 时更直接 `AttributeError`。调用点的门保留，只是省掉无谓的清单读取。

**AC2 · 归档类别 ⊂ 四类词表、不含 4-human-flag** —— 类别走白名单闭集：`kind_cat` 只映射 `confirmed→1-confirmed` / `judge_found→2-judge-found`，其余 kind（`local_false` / `judge_normal` / 无 kind / 自编名称）一律不归档，不往闭合词表里塞无定义类别；`4-human-flag` 永不由本函数写。

**AC3 · docstring 与行为一致、3-unfixed 归属写清** —— docstring 重写，逐类交代「谁写、何时写」：1-confirmed / 2-judge-found 由本函数在判官轮写；`3-unfixed` 生产侧**当前还没有写者**（如实写明，不假装已实现）——#194 落地后由 s08 收尾在 `s08.still_flipped` 非空时写、`round` 取最后一次复检轮次；`4-human-flag` 只由 `_flag_commit → _manifest_human_flag` 写（`round=human_flag`）；判官缺席时可见性由行内 `evidence_frames` 承担（ADR 0008 决策 2）。

**AC4 · 测试把这道门两个方向都钉住** —— `scripts/test_evidence_production.py` `1：原 fail-open 断言传的是**空证据列表**、且不查清单是否落盘，「没东西可写」冒充「挡住了」（旧实现在 `ok=False` 时仍会写出空 manifest，旧断言照样绿）。改为证据与 rows 都非空，对 `ok=False` / `None` / 非 dict 三种缺席形态各断言三件事（`added=0`、manifest 不存在、类别子目录不存在）；另加白名单用例：喂进 `local_false` / `human_flag` / `unfixed` / 自编类别，断言只归档 2 条、类别恰为判官轮两类、⊆ `EVIDENCE_CATEGORIES`、不含 `4-human-flag`。

**对抗式自查** —— 新断言做了变异验证：撤掉函数自身的门 → `added=1` 且 manifest 落盘（红）；把 `4-human-flag` 塞进 `kind_cat` → `added=3`（红）；还原后 10 节全绿。全量 `run_tests.py` 44 绿 0 红；`check_mechanical.py` 新增违规 0；门禁三关全过（远端 CI 绿 → 本地全量绿 → CLEAN），main 复查绿。

**遗留给别的票** —— `SKILL.md:436` 仍写「三类素材证据桶」（词表扩到四元组后文案未同步），不在本票 AC 内，未顺手扩大范围。

COMMENTS:
- 已执行完毕并合并：PR #198 / `8e34ff5`（squash 到 main，门禁三关全过 + main 复查绿）。四道 AC 全部满足，四个复选框已勾，实施记录回填正文「实施记录（2026-10-22）」一节。

要点：
1. **AC1 门真的关上了** —— `_archive_evidence_buckets` 此前**自身没有守卫**，只有调用点 `_check_frames` 挡过一次；直接调用（测试/探针/未来调用点）时 `ok=False` 照样走到末尾无条件 `_write_evidence_manifest`，落一份空清单，`judge_block=None` 更直接 `AttributeError`。现在函数顶部同一道门（非 dict / `ok=False` → `return 0`，在建目录/读清单/写清单之前）。
2. **AC2 白名单闭集** —— 只写 `1-confirmed` / `2-judge-found`，`local_false` / `judge_normal` / 自编名称一律不归档，`4-human-flag` 永不由本函数写。
3. **AC3 docstring 如实** —— 逐类写清「谁写、何时写」；`3-unfixed` 明写「生产侧当前还没有写者」，归 #194 的 s08 收尾（`s08.still_flipped` 非空时、`round` 取最后一次复检轮次）。
4. **AC4 旧测试是假绿** —— 原 fail-open 断言传空证据列表 + 只断言返回值，而函数在 `ok=False` 时仍会写出空 manifest。改成非空证据 + 查「manifest 未落盘」，并补白名单对抗用例；两个变异（撤门 → `added=1`；往 `kind_cat` 塞 `4-human-flag` → `added=3`）各自打红验证，还原后 10 节全绿。

遗留（不在本票 AC，未扩范围）：`SKILL.md:436` 仍写「三类素材证据桶」，四类词表扩成四元组后文案未同步。
=========== #193 ===========
TITLE: 存量清洗：10-19 六条回填冻结帧 + 10-02 一条标帧失效
BODY:
## Parent

#189

## What to build

盘上已经攒下的错账要治病不治命：10-19 那六条标注的定罪帧一直在 `human_flag/` 目录里，把行内 `evidence_frames` 指回去即可原地修好；2026-10-02 那条没有冻结副本，如实标成帧失效等人工复核。

## Acceptance criteria

- [ ] 10-19 六条（shot01/05/08/12/14/19）行内 `evidence_frames` 指向冻结副本，`sha256` 与盘上一致
- [ ] 27 个副本按 **sha256** 去重（不按文件名/时间戳），同一帧只留一条证据
- [ ] 2026-10-02 shot08 标 `frame_stale=true` + 原因；`user_flagged` 与原结论保持不变
- [ ] 清洗后 `rotate4_pool`：六条取到冻结帧、stale 那条不进池但仍在 stats 判定数里
- [ ] 清洗可重入（重跑得同一结果），且不触碰其它期次任何行

COMMENTS:
- ## 结论

清洗做完了。10-19 六条已回填冻结帧，2026-10-02 shot08 已如实标帧失效；五条验收逐条核过，数据集变更已入库。

- 脚本 + 自检：PR [#199](https://github.com/mechanic-Q/video-pipeline/pull/199) → main f25b7e5（scripts/backfill_evidence_frames.py + scripts/test_evidence_backfill.py，5 节全部通过）
- 数据：main ecb1572（data(references): 单亲提交，7 行变更；507 行里其余一行未动）

## 做了什么

六条（shot01/05/08/12/14/19）行内 evidence_frames 各补一条四字段条目 {frame, sha256, round=human_flag, category=4-human-flag}，帧指向 _frames/evidence/human_flag/，sha256 取盘上实际字节（脚本写前写后各核一次，与盘上一致）。2026-10-02 shot08 标 frame_stale=True + frame_stale_reason「帧已覆盖、无冻结副本…」，user_flagged / ground_truth_verdict / vision_verdict 一字未动。

## 27 份副本如何落成 6 条证据（AC2 的口径）

盘上 28 份副本（human_flag/ 27 + human_flagb/ 1）按 sha256 去重成 16 个不同帧，逐 shot 分成三组，只有一组撑得起「定罪帧」：

| 组 | 内容 | 判决 |
| --- | --- | --- |
| 213757 / 215747 | 指认前的自动快照 + 首次指认瞬间 | VLM 复判为**正常**（shot08 0.41 / shot12 0.46 / shot14 0.43）→ 不是定罪帧 |
| shotNN_first.jpg | 与当期工作帧同 sha（shot01/05/08/12/14 逐条核过） | 复检重抽前的自动快照，无独有证据 |
| 230641 / 010052 | 两轮名不同、字节相同（同一帧冻了两次） | 2026-10-22 01:07 的 VLM 复判正是在这份帧上判出**翻转**（shot01 0.50 / shot05 0.48 / shot08 0.38 / shot12 0.53 / shot14 0.37）→ **定罪帧**，取更早的文件名 |

shot19 只判过一轮，定罪帧是 human_flag/shot19.jpg：台账 report.json 的 s08.vision.evidence 在册（round=human_flag，sha ddb2da3e…），221238 / 222325 / human_flagb 三份副本同 sha，按 sha 去重只算一帧。

因此 AC2 的「按 sha256 去重、同一帧只留一条证据」落实为两件事：去重键是 **sha256 而非文件名/时间戳**；每行只留**一份代表帧**（决策 14 原话）。把 16 个不同帧全写进行内不是更保险而是有害——其中一半是 VLM 判过「正常」的帧，会被 row_material 当成同一行的正类素材喂给训练，等于往正类池里掺误标。

## 验收自查（对抗式）

- **AC1** 六条 evidence_frames 指向冻结副本、sha256 与盘上一致 → 真跑后逐条重算盘上 sha 全等（e71d6f4a… / 498ead46… / f49282cc… / 006f2e4b… / 393c82cc… / ddb2da3e…）；测试第 1 节也钉了这条。
- **AC2** 去重按 sha256、同一帧只留一条 → 测试第 2 节断言定罪帧两副本同组、_first 快照没混进组、shot19 跨目录（human_flag/ + human_flagb/）同 sha 只算一帧；写入后每行恰好 1 条。
- **AC3** 10-02 shot08 标 frame_stale + 原因，user_flagged 与原结论不变 → 测试第 3 节逐字段断言（含 vision_verdict 仍为「正常」、ground_truth_verdict 仍为空、没凭空塞证据帧）。
- **AC4** 六条取到冻结帧、stale 那条不进池但仍在判定数里 → 实测 rotate4_pool：pos 29 条里六条全部指向 evidence/human_flag/；stale 那条在 pos 与训练/留出池两侧都不出现，而 gt.load_labeled 里仍在（判定数 49 行，只按 user_flagged 算）。
- **AC5** 可重入且不碰其它期次 → --dry-run 字节不变；重跑字节完全一致；git diff --numstat 只有 **7 行**变更（6 回填 + 1 标 stale），其余 500 行一个字没动。测试第 5 节用前后逐行指纹钉住。

## 踩坑（已写进 git 历史）

- set_evidence_frames 的 want 是**按行键的 dict**：同一次调用里同一行放两条会塌成最后一条。所以脚本一次调用一行只写一条；「一行多条证据」这种存量只能靠多次调用逐条累积。
- 幂等是按**帧路径**比对的，同 sha 不同文件名（010052 与 230641）重跑会叠出第二条 → 脚本加了 sha 级预去重：行内已有同 sha 副本就跳过（测试里预置了一条手工补过的 shot12 来钉这条）。
- 合成 episode 里忘了给「同期非点名行」造工作帧，该行会被 frames_of 直接丢掉，「不碰其它行
=========== #194 ===========
TITLE: 3-unfixed 收尾补写：s08 收尾按 still_flipped 落最难一类
BODY:
## Parent

#189

## What to build

「重烧后仍翻」这一类在文档里被声明由 s08 收尾写，实际生产侧从来没写过。补上写点，让最难的那一类终于有素材。

## Acceptance criteria

- [ ] `still_flipped` 非空时，收尾在 manifest 写入 `3-unfixed` 条目，帧在盘且带 sha256
- [ ] 条目 `round` 取该段最后一次复检的轮次
- [ ] `still_flipped` 为空时不写任何 `3-unfixed` 条目
- [ ] 夹具跑一轮「重烧后仍翻」验到条目出现；不影响既有 1/2 两类归档

---

**状态：已交付**（PR [#200](https://github.com/mechanic-Q/video-pipeline/pull/200) → main 3e754e2，merge gate 三关全过；逐条验收见下方结论评论）

COMMENTS:
- ## 结论

做完了。生产侧终于有了 `3-unfixed` 的写者：s08 收尾按 `still_flipped` 落「修不好」的最难正类，四类证据桶各有唯一写者。

- 代码 + 自检：PR [#200](https://github.com/mechanic-Q/video-pipeline/pull/200) → main `3e754e2`（merge gate 三关全过；本地 `run_tests.py` 绿=45 红=0）
- 落点：`scripts/run_pipeline.py` 新增 `Driver._archive_unfixed_evidence(still_flipped, rounds)`，在 `record("s08.still_flipped")` 之后、dataset 记录与硬失败 `raise` 之前调用，try/except 不阻塞主链
- 写口：沿用 `_load_evidence_manifest` / `_write_evidence_manifest` 单一 manifest 写口，条目字段与 1-confirmed / 2-judge-found 同构；帧落 `_frames/evidence/3-unfixed/<round>_<sid>.jpg`
- 文档：`CONTEXT.md` 证据桶术语改为四类/各写者口径（1/2 只认判官轮、3-unfixed 不要求判官在场、4-human-flag 只由 `_flag_commit` 写）；ADR 0008 决策 6 由此兑现

### 验收逐条

- [x] `still_flipped` 非空时，收尾在 manifest 写入 `3-unfixed` 条目，帧在盘且带 sha256 —— 第 11 节断言 `added==2`、`os.path.exists`、64 位 `sha256 == _sha256_file(frame)`，`verify_evidence_hashes` 全 ok
- [x] 条目 `round` 取该段最后一次复检的轮次 —— shot03 两轮复检 → `round2_shot03.jpg`；shot07 只复检一轮 → `round1_shot07.jpg`
- [x] `still_flipped` 为空时不写任何 `3-unfixed` 条目 —— 0 条、不写 manifest、不建目录
- [x] 夹具跑一轮「重烧后仍翻」验到条目出现；不影响既有 1/2 两类归档 —— 先落 1/2 再跑 3-unfixed，三类共存且既有条目 sha256 一字未动；重跑幂等 `+0`

### 票外但已记下的两个发现

1. 重烧梯队的 `rounds` 记的是「本轮计划重烧的段」，不是「真复检过的段」（`rewrite` 不可用 / `not reburn` break 时声明轮次从没落地）。条目优先取声明轮在盘帧，缺失则退回该段真正最后一次复检轮，账上不写没跑过的 `roundN`。
2. ADR 0008 决策 7 要求的三处 `ledger.flush` 仍缺 `_check_frames` 末尾与 s08 收尾两处（`_flag_commit` 那处已有）；本票不扩范围，`_check_frames` 加 flush 会破 `test_vision_shortcircuit.py` 的 FakeLedger（无 `flush()`）。

=========== #195 ===========
TITLE: 热身票：run_driver_bg.py 脱离终端启动 + s13 收尾打点
BODY:
## Parent

#189

## What to build

让 driver 能在不经交互终端的情况下被拉起并观察：一条命令后台启动、日志可追、退出码可查；s13 收尾打出可解析的阶段/耗时/产物打点。这是工作纪律的改进，与证据链无依赖。

## Acceptance criteria

- [ ] `run_driver_bg.py` 脱离终端启动 driver，日志落到稳定路径且能持续读取
- [ ] 能查到该次运行的退出码/存活状态
- [ ] s13 收尾打印结构化打点（阶段、耗时、产物路径），不打断既有输出
- [ ] 不与「严禁并行跑两期」冲突（单实例守卫或明确文档化）

---

**状态：已交付**（PR [#201](https://github.com/mechanic-Q/video-pipeline/pull/201) → main `d61a8e3`，merge gate 三关全过；逐条验收见下方结论评论）

COMMENTS:
- ## 结论

做完了。driver 终于有了「一条命令后台起、日志可追、退出码可查」的唯一入口，s13 也有了可解析的收尾打点。

- PR [#201](https://github.com/mechanic-Q/video-pipeline/pull/201) → main `d61a8e3`（merge gate 三关全过：远端 CI green / 本地 `run_tests.py` 46 文件全绿 / 可合并且无冲突；合完在 main 复跑 runner 也绿）
- 新增 `scripts/run_driver_bg.py`（580 行 + 内部 `--supervise` 角色）、`scripts/test_run_driver_bg.py`（412 行，6 节裸 assert）
- 改 `scripts/driver_status.py`（认 `bg_run.json` + `_bg_is_current`）、`scripts/run_pipeline.py`（`telemetry_line()` + s13 收尾一行）、`scripts/new_daily.py`（下一步提示）、`SKILL.md`（后台启动条目与守卫口径）

### 验收逐条

- [x] 脱离终端启动 driver，日志落到稳定路径且能持续读取 —— 每轮独立 `<slug>/_driver/bg-<run_id>.log`，记录 `<slug>/_driver/bg_run.json`。Windows 走 WMI `Win32_Process.Create`（每次一个唯一 ps1，用完删）；POSIX 走 `start_new_session`。**真机冒烟**（不入库的临时脚本）：`launch_detached` 返回后 WMI 子进程仍活着并成功落文件（真脱离，0.9s 返回）；`supervise` 用真子进程跑，退出码 7 → record `exited/7`、子进程 stdout 全进了日志。
- [x] 能查到该次运行的退出码/存活状态 —— `run_driver_bg.py --status`（退出码/pid/日志尾）与 `run_pipeline.py --status`（四态，且认得 bg 记录：心跳没写的退出码从记录补）。测试断言两种入口都读到退出码；真机冒烟 `--status` 读到 7。启动器自身退出码只代表启动成败（见到 `running` + driver pid 活着才算成功）。
- [x] s13 收尾打印结构化打点，不打断既有输出 —— `[telemetry] {json}` 一行（stage/title/seconds/artifacts{video,report,pending_dir}/platforms），放在既有 notify 之后，现有输出一条未动。测试断言 s13 全程恰好一行 `[telemetry]`，并核对 `artifacts.report` 真实存在、platforms 带 B站 BV。
- [x] 不与「严禁并行跑两期」冲突 —— 两层守卫：① 同 slug（心跳 pid 未 exited 且活着 / bg 记录 running 且 driver 活着）；② 机器级锁 `~/.video-pipeline/driver_bg.lock`（`O_CREAT|O_EXCL`；僵尸按 pid 探活接管，探不准就拒绝）。边界写进 `SKILL.md`：锁只覆盖经本工具的启动，手工前台跑 `run_pipeline.py` 不持锁，不提供 `--allow-parallel`。

### 踩坑（详版在 PR 正文）

1. 不能复用 `comfyui_selfheal.launch_detached`：它的 ps1 是 comfy 专用单一硬编码路径，共用会互相覆盖 → 每次生成 run_id 唯一 ps1。
2. WMI 起的进程没有控制台，supervisor 里 `print` 会炸 → 日志一律 `lf.write`。
3. #151 老坑（后台 spawn 偶发日志 0 字节静默死）：验活窗口内必须见到 `running` 记录 + driver pid 活着，否则明确报错，不假装启动成功。
4. 启动器退出码 ≠ driver 退出码：别接 `| tail`（#194 吞码）。
5. staleness：同 slug 先后台跑一轮、再手工前台跑一轮时，旧 bg 记录会把新运行判成「已崩」→ `_bg_is_current()`（pid 优先、时间兜底），测试有一例守着。

### 票外发现（未扩范围）

- 没有真的在临时 slug 上跑一次完整 driver：那会走 preflight 失败路径里的 `shutdown_comfyui()`，可能掐掉正在用的 ComfyUI（「多期不能并行」的老账）。所以「真脱离」由 WMI 冒烟 + `supervise` 真子进程两条独立证据覆盖，pipeline 本体仍由 `test_run_pipeline.py` 覆盖。
