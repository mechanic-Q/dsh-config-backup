# ADR 0008：证据链闭环——人审证据独立成桶、归档门不收窄也不放宽、真值帧冻结读取

- 状态：已接受（2026-10-22，grill 拍板；实现票待建）
- 上下文：10-19 期暴露一类失败——「识别到了、也重烧修好了，但证据没存成素材」。
  复盘出四个断点，全在判读闭环的落账段，不在判读本身：

  1. **归档门被焊死在判官上**：`scripts/run_pipeline.py:1753` 只在
     `judge_block.get("ok")` 为真时才调 `_archive_evidence_buckets(:1772)`。而
     `dsh_judge` 每轮 rc=1 → `_judge_second_opinion` 走 fail-open → `ok` 恒 False。
     结果：`<slug>/_frames/evidence/manifest.json` 与三类桶**从来没被写过**。
  2. **人审指认不落账**：`run_pipeline.py:2474 _flag_commit` 只做三件事——拷扁平帧
     `_frames/evidence/human_flag/<sid>_<ts>.jpg`、`ledger.record_event("human_flags",…)`、
     `rotation_dataset.set_user_flag(...)`。既不写 manifest，也不写行内 `evidence_frames`。
  3. **读取侧永远取工作帧**：`scripts/rotation_groundtruth.py:55 frames_of()` 把
     `_frames/<shot>.jpg` 与 `_frames/<shot>_*.jpg` 一起 glob 后 `sorted()`，
     字符 `'.'`(0x2E) < `'_'`(0x5F) 使工作帧永远排第一；`scripts/clip_train.py:124
     rotate4_pool()` 取 `fps[0]`。重烧覆盖工作帧后，评测真值变成「已经修好的正立帧」。
  4. **采样与落账时机错位**：数据集只在 s08 末尾 `append_records`（`run_pipeline.py:1268`）
     写一次；gate④ 与人工指认回路**全部发生在它之后**，那批证据帧永远进不了行。

  另有一处空白：`3-unfixed`（三轮仍翻）在生产侧没有任何写入方——`:1780` 的 docstring
  写着「本轮不写」，别处也没人写，`still_flipped` 只留在台账里。

  实测口径（2026-10-22 探针复判）：全库 `user_flagged=True` 30 行中 7 行标签与像素不符
  （10-19 六行 + 2026-10-02 shot08）；而 10-19 的 27 个扁平人审副本**27/27 判翻转**——
  冻结帧一直在盘上，错的只是「读取侧指错路」。

## 决策

1. **人审证据独立成桶 `4-human-flag`**：`rotation_dataset.EVIDENCE_CATEGORIES` 由
   三元组扩为四元组，`CATEGORY_NOTES` 同步。**否决复用 `1-confirmed`**：该类的语义是
   「本地翻 × 判官翻」这条四象限产物，人审不在四象限内；冒名会让日后判官真跑通时
   两类正类不可区分，`stats` 也无法分源。
2. **归档门不收窄也不放宽**：分类依据仍只认判官轮；判官缺席时**不新增类别、不伪造标签**，
   `5-local-only` 被否决——往闭合词表塞一个从未定义过的类别，等于给后人留一个
   没人知道含义的桶。可见性由行内 `evidence_frames` 承担（`build_records` 在 s08 末尾
   本来就会写），类别留给判官，职责不混。
3. **指认即冻结，读取侧优先级反转**：`_flag_commit` 在重烧覆盖之前把定罪帧拷进不可变目录，
   并把 `{frame, sha256, round, category}` 写进行内 `evidence_frames`；读取侧对
   `user_flagged=True` 的行**先取行内证据帧 → 再取证据桶帧 → 最后才回落工作帧**。
   **sha256 是硬条件**：账实不符即跳过该帧并告警，**不回落工作帧**（回落等于把
   「账实不符」静默降级成用脏帧，正是本次要根治的行为）；若该行所有帧都不符，该行
   不进评测池。
   **否决「只落账不改读取」**：账单对了、评测真值仍是错的。**否决硬隔离**：
   10-19 六行会因读侧指错而被判成空真值，实际它们可原地修好。
4. **后置回填写口 `set_evidence_frames`**：走 `write_rows` 单一写口，`_flag_commit` 里
   紧跟 `set_user_flag` 调用。**否决重跑 `append_records`**：那是整行 upsert，会把
   「当时判正常」改写为「现在判翻转」，把采集日志当结论回写。
5. **帧失效要显式标记**：`frame_stale=true` + `frame_stale_reason`；消费侧该行不进评测池，
   但**仍计入 `stats` 判定数**（真值没错，错的是帧）。**否决把 `user_flagged` 改回 null**
   （删真值）。
6. **`3-unfixed` 补写在 s08 收尾**：`still_flipped` 非空时经同一 manifest 写口落桶。
7. **台账在关键路径显式 flush**：`_flag_commit` 末尾、`_check_frames` 末尾、s08 收尾各
   `ledger.flush()`。**否决在 `record_event` 内部按白名单自动 flush**：那是把耐久性策略
   藏进通用存储组件；**否决全量每次 flush**：s08 每段判读一次会把 IO 放大。

## 后果

- 10-19 六行可**原地修复**：行内 `evidence_frames` 指回冻结副本后，标签与像素重新一致，
  `rotate4_pool` 取到正确真值；`2026-10-02 shot08` 无冻结副本，标 `frame_stale` 待人工复核。
- 判官 rc=1 的根因（`dsh_judge` 侧）**不在本 ADR 范围**：证据链的修法不再以「判官能跑通」
  为前提，这正是当初把门焊死在 `ok` 上的设计错误。
- 新增维护面：`EVIDENCE_CATEGORIES` 变四元组，`test_evidence_train_chain.py` 里
  `set(by_cat) == set(rd.EVIDENCE_CATEGORIES)` 的等号断言会随之变红，须同步改。
- 验收口径（四条）：manifest 有条目且 sha256 对得上 / 行内 `evidence_frames` 与 manifest
  同路径 / `row_material` 返回 ≥1 条证据素材 / **重烧覆盖工作帧后重新取仍得冻结帧**。
  第 4 条用夹具必做 + 下一期真期实跑观察一次，两者都要（`_archive_evidence_buckets` 是
  driver 内嵌路径，纯函数测不到）。
