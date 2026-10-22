closes #194

## 改了什么

**`scripts/run_pipeline.py`**
- 新增 `Driver._archive_unfixed_evidence(still_flipped, rounds)`：s08 重烧梯队跑完仍翻（`still_flipped` 非空）时，把每段「最后一次复检轮」在盘的定罪帧拷进 `_frames/evidence/3-unfixed/<round>_<sid>.jpg`，并按**同一 manifest 写口**（`_load_evidence_manifest` / `_write_evidence_manifest`）追加条目。条目字段与 1-confirmed / 2-judge-found 两类同构：`category/sid/round/frame/frame_win/source_frame/sha256/judge/conf/why/local_verdict/kind=still_flipped/synthetic=False/note`，`judge`/`conf`/`why` 缺省留空，不伪造。
- 调用点：s08 收尾 `ledger.record("s08.still_flipped", flipped)` 之后、旋转数据集记录与 `still_flipped` 硬失败 `raise` 之前 —— 硬失败路径下帧与账也已落盘；`try/except` 包裹，存证失败只 log 不阻塞主链（与 1/2 两类归档同语义）。
- `_archive_evidence_buckets` docstring 里的 3-unfixed 说明改为「写者是 `_archive_unfixed_evidence`，本函数永不写它」；白名单与控制流未动。

**`CONTEXT.md`**：证据桶术语从「判官复判产出三桶 + 人审另立一桶 + 归类只认判官轮」改为四类各自写者口径 —— 1/2 只认判官轮（判官缺席不新增、不伪造标签）；3-unfixed 由 s08 收尾按 `still_flipped` 写、**判据是「重烧后仍翻」本身，不要求判官在场**；4-human-flag 只由 `_flag_commit` 写。

**`scripts/test_evidence_production.py` 新增第 11 节**（真实期次布局，11 节全过）：
- 先落 1/2 两类，再跑 3-unfixed：三类共存、既有 1/2 条目 sha256 未被改动；
- `round` 取该段最后一次复检轮（shot03 两轮复检 → `round2_shot03.jpg`）、只复检一轮的段取 `round1`（shot07）；
- 帧在盘、条目带 64 位 sha256 且与 `_sha256_file` 账实一致；`frame_win` / `local_verdict=翻转` / `synthetic=False`；
- 重跑同期幂等 `+0`；`still_flipped` 为空 → 0 条、不写 manifest、不建目录（AC3）；
- `load_evidence_buckets` 双路扫描能读到生产 3-unfixed，`verify_evidence_hashes` 全 ok；
- 声明重烧轮没落地（rewrite 不可用 break）→ 退回最后实际复检轮；一轮证据都没有 / 帧路径为空 → 0 条，不伪造（AC1「帧在盘」的下界）。

## 踩坑

1. **`rounds` 记的是「计划重烧的段」，不是「真复检过的段」**：`rounds.append({round, shots, mode})` 在每轮开头执行，`rewrite` 不可用 / `not reburn` break 时声明轮次从没落地（没有该 `roundN` 标签的证据行）。初版严格按 `rounds` 映射取轮次 → 这类段静默 0 条，等于 AC1 漏写。修法：首选声明轮在盘帧，缺失则退回该段真正最后一次在盘复检帧（`re.match(r"round(\d+)$")` 取数字最大），账上绝不写一个从没跑过的 `roundN`。
2. **ledger 的 `s08.vision` 是整块覆盖，但 `evidence` 字段是调用处「读上一块 + 追加 + 整块写回」累积的**（`_check_frames` 存证循环与判读离线短路都是这个姿势）——照字面把整块视为覆盖，会误判早期轮次帧已丢而放弃写 3-unfixed。
3. **sha256 必须对拷贝后的 dst 计算**：源帧下一轮重烧前随时被覆盖，`verify_evidence_hashes` 只认 dst；`source_frame` 只作溯源记录。
4. `_check_frames` 末尾缺 `ledger.flush`（ADR 0008 决策 7 未做）是票外事项——本票不扩范围；落盘走 manifest 原子写，不依赖 flush。

## 自检

- `python scripts/test_evidence_production.py` → 11 节全部通过
- `python scripts/run_tests.py` → 绿=45 红=0 known-red=0
