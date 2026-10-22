# 宿主 - Windows 原生跑 driver 报 WinError 2 / PowerShell 静默失败

## 触发条件

在 **Windows 原生宿主**（不是 WSL）跑 video-pipeline，出现下列任一条：

- s08 提交 H3 时连败两次后 driver 退场，栈尾是
  `FileNotFoundError: [WinError 2] 系统找不到指定的文件。`
  完整栈里能看到 `subprocess.py ... _execute_child`，而 `submit_accel` 报的是
  `RuntimeError: submit_accel shotNN 失败: ...`（**吞掉了前面的真因**，要单独复跑才看得见）。
- 任何脚本里出现 `pgrep` / `pkill` / `/mnt/c/...` / `/dev/null` / `nohup setsid`。
- PowerShell 命令「跑了、没报错、但什么都没返回」或返回乱码 `������ʱ��`。

一眼认出：#151 把 driver 整体从 WSL 翻到 Windows 原生时，**只翻了一部分**——
同文件里 `netstat.exe` / `taskkill.exe` / `nvidia-smi` 已经是 Windows 口径，
紧挨着的 `pgrep/pkill/curl -o /dev/null` 却还是 POSIX 残留。

## 首选姿势（可直接抄）

### 1) 进程查找/击杀：按宿主分支

```python
if os.name == "nt":
    # Get-CimInstance 取 CommandLine，按参数过滤；PID 逐行输出后再 taskkill
    lines = ps(r"""Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
        ForEach-Object { if ($_.CommandLine -like '*--n-gpu-layers*') { Write-Host $_.ProcessId } }""").split()
else:
    lines = _sp.run(["pgrep", "-af", "llama-server"], capture_output=True, text=True).stdout.splitlines()
```

杀：Windows 走 `taskkill.exe /F /T /PID <pid>`；WSL 走 `pkill -f 'llama-server.*--n-gpu-layers'`。

### 2) 探活：curl 的 sink 两端不同

```python
if os.name == "nt":
    _curl, _sink = "curl.exe", "NUL"          # /dev/null 在 Windows 不成立
else:
    _curl, _sink = "/mnt/c/Windows/System32/curl.exe", "/dev/null"
code = _sp.run([_curl, "-s", "-m", "8", "-o", _sink, "-w", "%{http_code}", url],
               capture_output=True, encoding="utf-8", errors="replace").stdout.strip()
```

### 3) 调 PowerShell：argv 直调，**绝不转义**

```python
def _ps_utf8(cmd):
    # 不设 OutputEncoding，中文异常按 cp936 吐字节，utf-8 解出全是替换字符
    return ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$OutputEncoding = [Console]::OutputEncoding; ") + cmd

if os.name == "nt":
    full = [PS, "-NoProfile", "-Command", _ps_utf8(cmd)]   # shell=False，不转义
else:
    escaped = cmd.replace('$', r'\$').replace('"', r'\"')  # bash 吃一层引号，才需要转义
    full = f'{PS} -NoProfile -Command "{escaped}"'
```

为什么：`cmd.exe` 不吃 `\$`，`\$body` 变成**字面量变量名**，
Invoke-WebRequest 拿不到请求体却**不报错**——静默失败。

## 反面清单（试过不通的）

- 在 Windows 下继续用 `shell=True` + `$\to\$ ` 转义：不报错、返回空，最费时间的一种错。
  只有 POSIX shell 会先吃掉一层引号，所以转义逻辑必须和宿主绑定，不能共用。
- 用「子进程崩不崩」判断编码注入有没有生效：脚本自己 `reconfigure(errors="replace")` 时不会崩，
  只是把 `✅` 变成 `?`、把中文异常变成 `����`。**判据是信息有没有丢**，不是进程活没活。
- `curl ... -w '%{http_code}'` 返回 `000` 时当成「服务在线但无响应」：就是**真离线**（进程都没了）。
  此时 driver 已 kill 掉 ComfyUI，必须先 `comfyui_selfheal.heal()` 拉起（本次约 87s，outcome=restarted-ok）。
- 只看 driver 日志判断 s08 失败原因：driver 把子进程真因包了一层，必须**单独复跑**
  `python scripts/submit_accel.py 1 1 "<slug>\\06_H3prompt" --script "<slug>\\00_口播稿\\口播稿.md" --res 1080P`
  才看得到完整栈。

## 验证方式

1. `python -m py_compile scripts/submit_accel.py` 零输出；带 `-W error::SyntaxWarning` 也不许告警。
2. `python scripts/check_mechanical.py` 的新增违规数 = 0（旧的 pgrep/curl 指纹可缩减）。
3. 三种 PowerShell 形态实测全通：`$x=41+1` → 42；内嵌双引号 JSON 原样返回；`$_` 异常变量取到 Message。
4. 直接复跑 `submit_accel.py` 单段：应越过守卫走到探活，而不是 WinError 2 立即崩。

---
来源：#151 video-pipeline Windows 宿主翻转（2026-10-20）；同日 s08 连败两次后修复落地。
