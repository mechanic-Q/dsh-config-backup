# 工具 - 改完文件 git diff 说 `\ No newline at end of file`

> 文件名索引词：**No newline at end of file / 末尾换行丢了 / diff 里多出这行**。

## 触发条件

用 agent 的 `read` + `write` 工具**全量回写**一个文件后，`git diff` 末尾多出一条
`\ No newline at end of file`——即使你没动最后一行。评审会把它当成「新引入的回退」。

原因：`read` 返回的行列表把文件末尾的空行规范化掉了（`splitlines()` 语义），
`write` 再把它们 join 回去就少一个 `\n`。文件内容看起来没变，diff 却是红的。

## 首选姿势（可直接抄）

**改文件优先用 `edit` 做局部替换**——只碰指定片段，尾部永远不动。

只有当必须全量回写时，才需要收尾补换行。两种核法：

```bash
# 逐文件核（输出非空 = 缺换行）
for f in a.py b.md; do [ -n "$(tail -c 1 "$f")" ] && echo "$f MISSING" || echo "$f ok"; done
```

需要从程序里补时，用**原生文件 IO** 而不是经过工具层：

```js
const fs = await import("node:fs");
const s = fs.readFileSync(p, "utf8");
if (!s.endsWith("\n")) fs.writeFileSync(p, s + "\n", "utf8");
```

## 反面清单（试过不通的）

- ❌ `write` 时在 content 末尾多按一个 `\n` 想「补上」——下一次 `read`+`write` 又会吃掉，反反复复。
- ❌ 只看 diff 的行数判断有没有改坏——末尾换行这类改动不改变行数。
- ❌ 提交前不做检查——这类回退混进 PR 会被评审打回（本仓 #177 就被打回过一次）。

## 验证方式

- `tail -c 1 <file> | od -c` 最后一个字节是 `\n`。
- `git diff` 里不再出现 `\ No newline at end of file`。
- 提交前统一跑一遍上面那条 `for` 循环。

---
来源：#177 执行过程（2026-10-20）——同一会话里撞了 4 次，每次都是 read+write 全量回写导致。
