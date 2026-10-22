## 实施记录 / 验收核验（2026-10-22）

**已落地**：子票 #190–#195 全部合并（PR #196 / #197 / #198 / #199 / #200 / #201，另含 ecb1572 数据集清洗）。本票收尾 PR #202（squash 后 `3b864d7`）补上 ADR 0008 决策 7 的最后两处 `ledger.flush()`——判读轮末（`_check_frames` 返回前）与 s08 收尾（数据集写入之后），至此三处 flush 点（人工指认回调 / 判读轮末 / s08 收尾）齐备。`SKILL.md` 证据桶口径「三类 → 四类」按 docs 直推 main 单独提交（`2a78f56`）。

**验收核验（已过：夹具 + 活数据复核）**：

| 验收条目 | 结果 |
| --- | --- |
| manifest 有条目且 sha256 与盘上一致 | 2026-10-19 六条人工点名行 6/6 通过 |
| 行内 `evidence_frames` 与 manifest 同路径 | 6/6 同 basename、sha256 一致 |
| `row_material` 对该行返回 ≥1 条证据素材 | 6/6（均为 `rowlink / 4-human-flag / human_flag`） |
| 重烧覆盖工作帧后重取仍得冻结帧 | `test_clip_train.py` §6 既有硬断言，绿 |

补充口径核对：活数据集 507 行，人工真值判定 49（30 正 / 19 负，frame_stale 行照常计数）；`rotate4_pool` pos=29（30 条人工翻转 − 1 条 frame_stale，符合决策 8）；新增两条 flush 断言（`test_vision_evidence.py` 判读轮末、`test_s08_judge_loop.py` §7 收尾），`test_vision_shortcircuit.py` 的 `FakeLedger` 同步补 `flush()` 桩；全量 `python scripts/run_tests.py` 46 绿 / 0 红（本地与远端 CI 双绿，合并后主干复查绿）。

**未完成（本票保持 open）**：验收要求「下一期真期实跑观察一次」尚未发生——2026-10-22 只有报纸产物、无视频期次；Further Notes 明令本次不重跑整期 driver，故本会话补不了。**下一步**：下一期视频流水线实跑、s08 判读完成后，按上表四条在当期真数据上核对，通过即关票（届时无需再改代码，只需在评论附期次与核对行号）。
