# dsh-profile/judgeprobe — 判官运行体（T14 #126）

`scripts/dsh_judge.py` 驱动的第二意见判官，跑在 **Windows 侧 DSH 桌面宿主**
headless 模式上。本目录是它使用的 DSH profile，随仓进件，换机可复现。

## 构成

- `package.json` —— profile 清单：bundles = dsh-base + dsh-headless +
  dsh-our-free-model（无 GUI、带本地模型代理）。
- `cordis.patch.yml` —— 把 agent-default-model 指到 provider=buddy /
  model=glm-5.3-flash（带视觉，能 read_image；2026-10-16 起桌面宿主主用模型，
  deepseek-v4.1-flash 已不再由凭据面板下发）。以本文件与 cordis.patch.yml 实配
  为准，换模型时两处同步改。
- `cordis.yml` —— 根配置，保持空列表即可（树由 bundles + patch 合成）。
- `pnpm-workspace.yaml` —— nodeLinker=hoisted。

## 安装（一次性）

profile 依赖机器上已装好的 DSH 桌面版 node_modules（symlink 到 desktop
profile 的 node_modules），所以要装在用户目录下：

1. `mkdir -p ~/.dsh/profiles/judgeprobe`
2. 把本目录四个文件拷进去
3. Windows 下还需要让 node_modules 指到桌面版 profile：
   `cmd /c mklink /D "%USERPROFILE%\.dsh\profiles\judgeprobe\node_modules" "%USERPROFILE%\.dsh\profiles\desktop\node_modules"`
4. 自检：`python scripts/dsh_judge.py status`（可用性+配置），
   `python scripts/dsh_judge.py selftest`（真跑一次两字符问答）

## 环境变量

- `DRIVER_JUDGE`：0/false/no 关闭判官层（默认开）
- `DRIVER_JUDGE_PROFILE`：换 profile 名（默认 judgeprobe）
- `DRIVER_JUDGE_TIMEOUT`：单次判读超时秒（默认 900）
- `DRIVER_JUDGE_MODEL`：仅用于 status 显示与台账记录（真实模型由 profile 决定）

跨端铁律：driver 跑 WSL、判官跑 Windows；帧路径一律经 to_win_path() 转
Windows 口径后再进 prompt，判官进程看不到 /mnt/... 路径。
