## 结论

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
