closes #182

## 背景

spec #132 写了、代码里没有的三项残留（票面实测命令可复跑）：生产证据桶没接进训练读取路径、证据行没有 sha256、`ground_truth_verdict` 全仓 0 命中。

## 改了什么

### 1. 生产证据桶接进训练读取路径

生产侧 `_archive_evidence_buckets` 把本轮证据按判官四象限归档：

```
<slug>/_frames/evidence/1-confirmed/<round>_<sid>.jpg
<slug>/_frames/evidence/2-judge-found/<round>_<sid>.jpg
<slug>/_frames/evidence/manifest.json      ← 清单是账
```

与离线件 `references/t15-evidence-*/` **同构**，`load_evidence_buckets` 双路合并读取，条目带 `source=production|offline` 区分。

分类依据**只认判官轮**（`judge_block.ok`）：`confirmed`→1-confirmed、`judge_found`→2-judge-found；fail-open 轮不分类（没有判官就没有类别依据，不伪造标签）；`3-unfixed` 本轮不写（避免把「还没修好」误记成「修不好」）。

### 2. 证据行带 sha256

本地翻转存证与 judge_found 存证两条路径都落 `sha256`（`_sha256_file`，与 `t15_replay.sha256` 同算法流式读取）与 `frame_win`。

新增 `verify_evidence_hashes` 逐条重算对账，state ∈ `ok / sha-mismatch / unlinked / missing`；CLI 入口 `rotation_dataset.py verify-evidence`，账实不符退出码 1。

### 3. ground_truth_verdict

人工真值三件套补齐。词表 `GROUND_TRUTH_VERDICTS` = 正常/翻转/翻转90/翻转180/倾斜，「翻转」是粗类（旧调用方只给布尔时无从知道 90/180，宁可记粗类也不猜）；`set_user_flag` 支持逐键 dict 或整批字符串，非法词表值不改写已有结论；`stats` 增加透视与三件套计数；`export` 每行带该字段。

### 4. 顺带修掉一个 AC5 相关的真洞

`append_records` 是整行 upsert、`build_records` 无条件写 `user_flagged: None` → **同键重跑会把人工真值冲掉**。改为 `_HUMAN_KEYS` 从盘上旧行搬回。

## 验收对照

| AC | 怎么验的 |
| --- | --- |
| ① 生产跑一期后 `load_evidence_buckets()` 读到、episode 正确 | **真实探针**：E: 盘真实帧（2026-10-15 期 shot03，155KB）经生产归档 → 缺省参数读到，episode/linked/sha 全对，同时保留 22 条离线件 |
| ② 条目带 sha256、与帧实际哈希一致、帧被改动能报出 | 测试 §3：篡改帧后 `state=sha-mismatch`；CLI 退出码 1 |
| ③ 三类目录由生产写入 | 测试 §1/§4/§10：真 `dsh_judge.merge` 产出的 judge_block 分类正确 |
| ④ `ground_truth_verdict` 全仓可检索 | 数据集行可落、`set_user_flag` 可回填、`stats` 可读、`export` 每行带 |
| ⑤ 人机字段互不覆盖 | 测试 §6：整行 upsert 重跑后人工三件套保留、模型字段照常更新 |
| ⑥ 裸脚本 assert 风格 + 真实探针 | 新增 `test_evidence_production.py`（10 节）+ 真实 E: 盘探针 |

## 测试

新增 `scripts/test_evidence_production.py`（10 节，裸 assert 直跑，tempdir 合成 + 真 `dsh_judge.merge` 产出的 judge_block，不碰 E: 盘与活数据集）。

**8 个变异全部让测试转红**（归档函数 `return` / sha 校验失效 / 人字段保护失效 / 生产扫描失效 / verdict 不落 / 分类映射错位 / export 缺字段 / row_material 丢 frames_root）——测试钉住了行为，不是摆设。

全量 `run_tests.py`：41/41 绿（原 40 + 新增 1），148.8s。机械检查 0 新增违规。

## 踩坑记录

**探针差点造成事故**：第一版 AC1 探针在真实生产根 `E:\每日新中国` 下 `makedirs` 出了 `2026-10-20\...`，收尾 `rd /s /q` 把那个日期层级删了。核实无真实数据损失（数据集无该期次、最新真实期 10-19、全盘无 10-20 引用、回收站无痕迹），但**探针绝不该在生产根下造日期目录**。已改成自建隔离根 `<root>\_182probe\`，清理只删这一个，删前核实 basename。教训已记 `procedures/_inbox.md`。
