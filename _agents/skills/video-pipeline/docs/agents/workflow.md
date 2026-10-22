# Workflow Conventions

改动如何分类、走哪条落地路径。2026-09-25 用户拍板（来源：会话口述约定）。

## 六条约定

1. **docs 直推 main**：纯文档改动（CONTEXT.md、references/pitfalls.md、docs/adr/、docs/agents/ 等）不建分支、不开 PR，直接 commit 到 main。审查往返对文档没有收益。

2. **实现票走 feat 分支 + PR**：一切代码改动（scripts/、assets/、测试）走分支 + PR 流程；commit message 与 PR 描述必须**中文详记踩坑**——实证教训（现象、根因、修法、期次）写全，好让 pitfalls 能从 git 历史直接捞。
   - 分支名 `feat/<票号>-<短语>`。`implement` 技能最后一步只到 commit，**push 与开 PR 要自己补上**。
   - 开 PR 用命令，不要手填网页表单：`gh pr create --title "<中文标题>" --body-file <临时文件>`。正文**首行必须是 `closes #<票号>`**，缺这行票不会自动关，账实就对不上了；其后按「改了什么 / 踩坑」两节写。Windows 下 `--body-file` 传本机临时文件路径，不要用 bash 的进程替换。
   - **票里的 AC 与代码对不上时，停下来做三件事**：在票上评论差在哪、不要顺手扩大范围、不要改票据而不留痕。票的价值在于可验证；执行者现场改 AC，票就退化成自我解释。数字类 AC（调用点几处、影响几行、几个平台）最容易过期，尤其要核。
   - 反例：PR #64 把 README 同步也走了 PR——两个约定同时违反，不再犯。

3. **README 只在里程碑动**：README/主页 hero 不随普通票同步，攒到里程碑（车道收口、整期功能落地这类节点）才更新，避免噪音。

4. **graphify 开工先查一把**：动 graphify 相关工作前，先查 `graphify-out/` 现状（GRAPH_REPORT.md / graph.html / graph.json / manifest.json / cache），在已有产物上增量，不盲目重跑。

5. **数据集随期入库，不留未提交的诚实记录**（2026-10-18 #148 收口）：`references/rotation-dataset.jsonl` 是真值单一事实源，不是运行日志也不是测试夹具。
   - **哪一步跑完产生**：s08 跑完，`run_pipeline.py` 自动把当期镜头行追加进数据集（唯一写口 `rotation_dataset.write_rows`）。
   - **谁提交**：当班 agent 收尾前提交，commit 用 `data(references):` 前缀，说明写**期次 + 判读结论**。判读服务不可用就如实写「未判」——**不得为干净工作树丢弃诚实记录**，也不得改写结论凑整齐。
   - **怎么校验「已提交」**：`git status references/rotation-dataset.jsonl` 无未提交改动，且 `git show --stat HEAD` 里该文件新增行数 == 当期镜头数。
   - **测试永不读写活数据集**：夹具是 `scripts/fixtures/` 下的期次冻结快照；跑器给每个测试子进程注入 `ROTATION_DATASET_READONLY=1`，此时写活路径一律抛 `DatasetReadOnlyError`（显式传 tempfile 路径不受限）。

6. **绿灯即合，门禁说了算**（2026-10-19 #153）：实现票 PR 开出、CI 绿后，由门禁脚本合并，**不由人点合并键**——合并不是判断点，判断已由「票做得对不对、代码合不合规矩、跑不跑得绿」三处机器检查完成。
   - **怎么合**：`python scripts/merge_gate.py <PR 编号>`。三关全过才 squash 合并：远端 CI 在最新 commit 上绿 → 本地全量自检（`python scripts/run_tests.py`）绿 → PR 无冲突。任何一关红即拦截退出非 0，不合。
   - **合并后**：门禁在 main 上复跑一遍 runner，红了打印 `git revert --no-edit <短哈希> && git push`——**不自动 revert**，避免误伤。
   - **例外两处**（不走本门禁）：#139 `run_pipeline.py` 语义冲突解、#147 删本地 worktree——都发生在 PR 之前或之外。
   - `--skip-wait`：不等远端 CI（CI 未就绪视为拦截），只在本地全量已是最强证据时用。

## 快速判断

接到改动先问一句：这是 docs 还是实现？是里程碑吗？graphify 吗？——三问答完，落地路径就定了。实现票合 PR 时用第 6 条门禁。
