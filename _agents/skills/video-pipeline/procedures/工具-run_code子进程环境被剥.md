# 工具 - run_code 里子进程拿不到 HOME / GH_TOKEN / PATH

> 文件名索引词：**gh 未登录 / HOME 为空 / 子进程找不到命令 / spawn ENOENT**。

## 触发条件

在 DSH 的 `run_code` 程序里 `spawn` / `execSync` 一个子进程，它拿到的环境是**空的**：
`process.env` 只有 0 个键，`HOME`、`USERPROFILE`、`PATH`、`GH_TOKEN` 全都没有。
表现是 `gh` 说未登录、`git` 找不到 config、外部命令 `ENOENT`。

**但 `ops_bash` / `pwsh` 工具起的子进程环境是完整的**（`HOME=C:\Users\<你>`、PATH 齐全）。
所以「同一个命令，走 ops_bash 行、走 run_code 不行」——不要怀疑命令本身。

## 首选姿势（可直接抄）

- **要跑外部命令（git / gh / python）就用 `ops_bash` / `pwsh` 工具**，别在 `run_code` 里 spawn。
- 确实要在 `run_code` 里 spawn 时，**显式补环境**：

```js
spawn(cmd, args, {
  env: { ...process.env, HOME: "C:/Users/<你>", USERPROFILE: "C:/Users/<你>", PATH: process.env.PATH || "" },
});
```

- 认证别指望 `GH_TOKEN` 存在：本机 `gh` 走 **keyring**（`gh auth status` 显示 `Logged in ... (keyring)`），
  子进程若继承不到 keyring 环境，改用 `gh auth token` 由父进程取出来再显式传。

## 反面清单（试过不通的）

- ❌ 在 `run_code` 里 `execSync("gh ...")` 然后以为是 gh 坏了——是环境被剥了。
- ❌ 以为 `process.env` 里应该有 PATH——实测 0 个键，连 PATH 都没有。
- ❌ 在 `run_code` 里 spawn `bash` 做管道编排——Windows 上没有 bash，且环境同样被剥；改用 `ops_bash`。

## 验证方式

```js
console.log(Object.keys(process.env).length);   // run_code 里通常是 0
```

```bash
python -c "import os;print(os.environ.get('HOME'), bool(os.environ.get('GH_TOKEN')))"   # ops_bash 里应非空
```

---
来源：#151（2026-10-19）首次记录；#177 执行（2026-10-20）实测复现：`run_code` 的 `process.env` 0 键，
`ops_bash` 侧 `HOME` 正常、`GH_TOKEN` 不存在但 `gh auth status` 走 keyring 已登录。
