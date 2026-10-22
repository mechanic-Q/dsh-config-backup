# 工具 - CI 卡住 / 看起来没绿（queued 不动、零 check、run 绿了 PR 仍 pending）

> 文件名索引词：**CI 卡住 / CI 没绿 / check 一直 pending / 合并被拦**。

## 触发条件

`gh pr checks` 或门禁报告 PR 的 check 不是绿的，但你不确定是代码问题还是环境问题。
三种形态**根因完全不同**，先按这张表认形，别急着改代码：

| 现象 | 根因 | 判据 |
|---|---|---|
| **零 check**，连 pending 都没有 | PR 与 base 有**合并冲突**，GitHub 直接不触发 workflow | `gh pr view --json mergeable` 返回 `CONFLICTING` |
| check 长期 `queued`（几十分钟到数小时） | **GitHub Actions 平台降级**，不是你的代码 | 平台状态页 Actions = `degraded_performance`；run 的 `run_started_at == created_at`、`runner_name: null` |
| `gh run list` 已 `completed/success`，但 PR 的 rollup 仍 `IN_PROGRESS` | **状态传播延迟**（实测 1–2 分钟） | 过一两分钟再看 `gh pr checks` 即转绿 |

## 首选姿势（可直接抄）

```bash
# 1. 先认形：这三个字段一起看
gh pr view <PR> --repo <owner>/<repo> --json mergeable,mergeStateStatus,statusCheckRollup \
  --jq '{mergeable, mergeStateStatus, checks: [.statusCheckRollup[] | {name, status, conclusion}]}'

# 2. 看平台是不是在降级（queued 形态的根因）
#    本机 curl 走 schannel 会报 CRYPT_E_REVOCATION_OFFLINE，用 web_fetch 更省事
#    https://www.githubstatus.com/api/v2/components.json → 找 Actions 的 status

# 3. 判 PR 绿一律看 PR 口径，别把 run 口径当 PR 口径
gh pr checks <PR> --repo <owner>/<repo>
```

**为什么**：这三种形态在 `gh pr checks` 的输出里长得几乎一样（都是 pending / 不绿），
但处理方式一是解冲突、二是等平台、三是再等一分钟。认错形会白改代码或白等。

## 反面清单（试过不通的）

- ❌ **用 `merge_gate.py --skip-wait` 绕**：它只跳过「等待」，CI 仍是 `pending` 就照样拦截——平台降级时救不了。
- ❌ **手工点 GitHub 的合并键**：本仓约定「绿灯即合，门禁说了算」（`docs/agents/workflow.md` 第 6 条），绕过门禁等于跳过了三处机器判断。
- ❌ **看到 `queued` 就去 rebase / 重推**：平台降级时重推只会多排一个 queued。
- ❌ **拿 `gh run list` 的 conclusion 当 PR 绿了的证据**：有传播延迟，且它不反映 PR 是否可合并。

## 验证方式

- 门禁第一关打印 `green: CI 绿（head <sha>）` 才叫真绿。
- 平台恢复后 `gh pr checks <PR>` 显示 `pass`（带耗时，如 `1m54s`）。
- 零 check 形态：`mergeable` 转成 `MERGEABLE` 后再 push，workflow 会被触发。

---
来源：#177 执行过程（2026-10-20）——合并 PR #179 时 Actions `degraded_performance`，CI 卡 `queued` 约 1 小时；
同期还撞到「run 已 success 但 PR rollup 仍 IN_PROGRESS」的传播延迟。#151 曾记录「零 check = 合并冲突」，一并归入本卡。
