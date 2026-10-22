# 工具 - deck_issue_patch 把票正文整篇覆盖掉了

> 文件名索引词：**票正文被覆盖 / body 没了 / 改票正文把内容冲掉**。

## 触发条件

用 `deck_issue_patch`（或任何 `body` 参数）想「先传个占位符试探一下」，
结果**线上票的整篇正文被替换成那个占位符**。

根因：`body` 参数是**全量替换语义**，不是追加、也不是局部编辑。传什么就变成什么。

## 首选姿势（可直接抄）

改票正文的正确顺序——**先读全文、拼装、再写回去**：

```text
1. deck_issue_get(key)                  # 取当前全文
2. 在本地把新段落拼进全文（保留原有内容）
3. deck_issue_patch(key, body=<拼好的完整正文>)
```

只追加一节时的最小可靠写法：`新正文 = 原文 + "\n" + 新段落`。

**已经覆盖了怎么救**：GitHub 保留正文版本历史，用 GraphQL 取回：

```bash
gh api graphql -f query='
  { repository(owner:"OWNER", name:"REPO") { issue(number: N) {
      userContentEdits(last: 10) { nodes { editedAt diff } } } } }'
```

`userContentEdits.nodes[].diff` 就是该版本**完整正文**（REST 没有这个端点）。

## 反面清单（试过不通的）

- ❌ **传占位符「试探」语义**——这个工具没有 dry-run，传了就是真写。
- ❌ 以为 `body` 是 patch 语义、只传新增那段——它会把其余内容全部丢掉。
- ❌ 只靠 `gh issue view` 看当前正文就以为「没变」——覆盖后内容确实变了，只是你可能没细看。

## 验证方式

- 写回后立刻 `deck_issue_get(key)` 或 `gh issue view <N> --json body` 读全文，确认原有段落都在。
- 若已被覆盖，用上面的 GraphQL 取回上一版 `diff` 字段，逐段核对后重新写回。

---
来源：#151 执行（2026-10-19）事故；#177 执行（2026-10-20）改成「先 get 全文拼装再写」并两次安全更新正文。
