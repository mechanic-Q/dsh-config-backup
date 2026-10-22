# cc-safety-net · DSH 适配补丁（v2）

**做了什么**：修 DSH 适配层的两个盲区，让"命令"这一概念在更多调用形态下真正送检。

| 版本 | 改动 | 效果 |
|---|---|---|
| v1 | 工具→方言映射加 `ops_bash` / `wsl` / `ssh_exec` → `posix` | 这三个工具的 `command` 按 shell 文法解析（此前走 unknown 路由按 PowerShell 解析，POSIX 规则基本失效） |
| v2 | `ops_bash` 只给 `script_path` 时读取脚本内容当命令；`terminal_send.text` 作为命令送检（方言 `auto`） | 脚本文件里的危险命令、以及直接敲进终端的命令也被拦 |

文件：`C:/Users/LMR/.dsh/profiles/desktop/node_modules/cc-safety-net/dist/chunks/index-1x1d9s1m.js`
原始文件（1313 B，sha256 `d03a015d…`）备份在本目录；补丁后 1704 B，sha256 `70c76fd2…`。

## 三处改动（都在同一个 chunk 里）

```js
// 1) 引入 readFileSync
import{readFileSync as rf}from"node:fs";
import{Z,x,it,l}from"./index-z14g58a6.js";

// 2) 方言映射
var et=new Map([["bash","posix"],["pwsh","powershell"],["ops_bash","posix"],["wsl","posix"],["ssh_exec","posix"],["terminal_send","auto"]])

// 3) 抽取器：多了两种命令来源
let p=t,a=p.command;
if((typeof a!=="string"||a.trim()==="")&&n==="ops_bash"&&typeof p.script_path==="string"&&p.script_path.trim()!=="")
  try{a=rf(f(o,p.script_path),"utf8")}catch{return void 0}
if((typeof a!=="string"||a.trim()==="")&&n==="terminal_send"&&typeof p.text==="string")a=p.text;
```

`script_path` 读不到时返回 `undefined`（放行）——不放行的理由不成立：同进程同用户都读不到，bash 执行时也读不到。

## 实测（`node verify.js`，A/B 用原始文件同目录对照）

| 调用形态 | 补丁前 | 补丁后 |
|---|---|---|
| `ops_bash` / `wsl` / `ssh_exec` + `command: "git reset --hard HEAD~3"` | allow | **BLOCKED** |
| `ops_bash` + `script_path` 指向含 `git reset --hard HEAD~3` 的脚本 | allow | **BLOCKED** |
| `ops_bash` + `script_path` 指向含 `cat ~/.ssh/id_rsa` 的脚本 | allow | **BLOCKED** |
| `ops_bash` + `script_path`（相对路径，按会话 cwd 解析） | allow | **BLOCKED** |
| `terminal_send` + `text: "rm -rf /"` | allow | **BLOCKED** |
| `rm -rf ./build` / `git status` / 不存在的 `script_path` | allow | allow |

`bash` / `pwsh` 两个官方工具的行为未变（本来就受检）。

## 升级后要重打

`pnpm install / update` 会把该文件还原成发布版，补丁静默消失：

```bash
node "C:/Users/LMR/.dsh/patches/cc-safety-net/patch.js"   # 幂等，v1/v2 状态都能升上来
```

脚本自己会断 pnpm 硬链接（写新文件→删旧→改名），不污染 store 里其他项目共用的那份。判断是否已打：文件 1704 B / sha256 以 `70c76fd2` 开头。

## 回滚

```bash
node "C:/Users/LMR/.dsh/patches/cc-safety-net/restore.js`
```

## 验证

```bash
node "C:/Users/LMR/.dsh/patches/cc-safety-net/verify.js"
```

5 个工具 × 5 条命令 + 5 条 script_path 用例 + 3 条 terminal_send 用例。

## 仍然覆盖不到（别当沙箱用）

- `run_code`：参数是 TypeScript，静态不可判（代码里 `child_process` 起的进程不受检）。
- `ssh_exec` 只检查**远端要执行的命令行**，不检查远端主机上的实际权限与别名展开。
- cmd.exe 语法（`del /s /q`）：引擎不解析 cmd。
- 一切规则匹配都是"看命令行"，不是隔离：不改进程权限、不看网络、不拦解释器内部的破坏行为。
- 补丁是进程加载期生效的：改完要**重启 DSH** 才作用于当前会话。
