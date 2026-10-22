## Agent skills

### Issue tracker

GitHub Issues（mechanic-Q/video-pipeline，通过 gh CLI 操作；wayfinder 地图 #28 及其子任务也在此追踪）。See `docs/agents/issue-tracker.md`.

### Triage labels

默认五角色标签：needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix（标签名=角色名）。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根目录 `CONTEXT.md` + `docs/adr/`（懒创建，不存在时静默跳过）。See `docs/agents/domain.md`.

### Workflow conventions

改动分类与落地路径：docs 直推 main / 实现票 feat+PR（中文详记踩坑）/ README 里程碑才动 / graphify 开工先查 `graphify-out/`；实现票合 PR 用自动门禁 `python scripts/merge_gate.py <PR 编号>`（绿灯即合，#153）。See `docs/agents/workflow.md`.

### 测试写法

`scripts/test_*.py` 的骨架 / 分节 / docstring / 收尾汇总行 / 无静默 skip / 编码 / 不碰活数据集 / 跑器：见 `docs/agents/test-conventions.md`。

### 宿主契约

driver 与各阶段在哪个宿主跑、用什么 shell 语法、产物落在哪：见 `docs/host-contract.md`（唯一事实源；运行时守卫自 #145 起、测试从现在起，读同一份声明）。
