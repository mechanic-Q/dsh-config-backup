# 工具 - run_code 里写含引号/反引号的脚本报怪错

> 文件名索引词：**Identifier cannot follow number / 缺少表达式 / 模板字面量报错 / SyntaxError unterminated**。

## 触发条件

在 DSH 的 `run_code`（TypeScript 程序体）里想跑一段**自身含引号**的 bash / python / powershell 脚本，
报的错却完全不指向真实原因：

| 你写的 | 报错 | 真实原因 |
|---|---|---|
| `String.raw` + 含反引号的脚本 | `Identifier cannot follow number` | 脚本里的**反引号**提前闭合了外层模板字面量 |
| `bash -c "powershell ... \"... $_.CommandLine ...\""` | PowerShell 报「缺少表达式」 | 双引号内的 `$_` 被 **bash 先展开**成 `/usr/bin/bash`，PowerShell 收到的是垃圾 |
| `python -c "... 三引号 ..."` | `SyntaxError: unterminated triple-quoted string literal` | 三引号被外层引号吃掉 |

**共同特征**：报错行号指向脚本里某个无关位置，或报的是「语法错误」但那段语法本身没问题。

## 首选姿势（可直接抄）

**脚本超过两三行、或含引号 / 反引号 / `$` / 非 ASCII，一律先写文件再执行**——一条判据，不用记转义规则。

```text
1. 用 write 工具把脚本落到临时文件（内容原样，不经 shell）
2. 再用一条极短命令执行它：python "C:/Users/<你>/AppData/Local/Temp/x.py"
```

必须内联时的两条硬规则：
- **bash 里给 PowerShell 传 `$_` / `$env:` 这类变量：外层用单引号**，别用双引号（`bash -c '... "... $_ ..."'` → bash 不展开，实测对照成立）。
- **`run_code` 里别用 `String.raw` 包含反引号的代码**：改用普通字符串加 `\n` 拼接，或写文件。

## 反面清单（试过不通的）

- ❌ 照报错去改那段「有语法错误的代码」——它往往是对的，错在**传进来的那一层**。
- ❌ 层层加反斜杠转义——三层引号之后没人能读懂，下次改还是错；换成写文件立刻消失。
- ❌ 在 bash 双引号里用 `$` 开头的 PowerShell 变量——bash 会先吃一遍。

## 验证方式

- 内联版对照实验：同一条命令分别用单引号 / 双引号包，看输出里 `$_` 是否被替换成 `/usr/bin/bash`。
- 写文件版：`python <路径>` 能正常跑出预期输出，且脚本内容与编辑器里看到的一致。

---
来源：#177 执行过程（2026-10-20）——查 PR 状态时连撞两次；`$_` 被 bash 展开已做单引号对照实测。
