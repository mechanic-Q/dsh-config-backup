# 测试写法约定（Test Conventions）

仓库里的测试是**裸脚本**：直接 `python scripts/test_xxx.py` 跑，`assert` 失败即红，不用 pytest / unittest（全仓 0/37 个测试文件 import 它们）。这套写法此前只存在于 37 个文件的实际做法里，没有出处——#147 因为不知道「收尾必须打汇总行」返工过一轮。本文件把它写下来。

- **性质**：约定，不是 lint。没有任何检查器强制下文全部条款；违反不会被机械拦（例外见 §6/§7，那两条有 `check_mechanical.py` 与 `test_dataset_readonly.py` 兜着）。
- **适用范围**：`scripts/test_*.py`。新文件按本文写；**存量 37 个文件不要求回改**（见文末采用率表，本票明确不回改）。
- **数字口径**：本文所有计数截至 **#162**（2026-10-19），现场用命令核过，核法见文末「数字怎么核」。数字会随文件增长过期，别照抄，重跑一遍附录命令。

## 1. 骨架：shebang + coding + HERE + sys.path.insert

新文件直接抄这段开头：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_<被测对象>.py — <一句话说明>（#<票号>，无框架，assert 直跑）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rotation_dataset as rd   # noqa: E402 — 必须在 sys.path.insert 之后
```

**为什么不用相对 import**：这些文件是被 `python scripts/test_x.py` 直接执行的脚本，不是包的一部分，`__package__` 是 `None`，`from . import x` 直接 ImportError。`sys.path.insert(0, HERE)` 把「脚本自己所在的目录」放进搜索路径，于是 `import rotation_dataset` 一定能拿到**同目录这一份**，不管从哪个 cwd 启动。

**为什么 HERE 而不是 `os.getcwd()`**：cwd 由调用者决定（跑器以仓库根为 cwd，人手工跑可能在任意目录）。#152 的整条事故就是「脚本以为自己在某个固定路径」——一律从 `__file__` 派生，见 pitfalls #60。

反面清单：

- ❌ `from . import run_pipeline` / `from scripts import x` —— 裸脚本没有包上下文。
- ❌ `sys.path.insert(0, os.getcwd())` —— cwd 会变，且跑器下是仓库根，拿不到同目录邻居。
- ❌ 用 `os.chdir()` 把 cwd 掰到脚本目录 —— 会把调用者的相对路径语义一起改掉。
- ❌ 写死 `C:\Users\<名>\...` 或 `/mnt/c/...` —— 换宿主静默失效，且 `check_mechanical.py` 规则 4（path-literal）会判新增违规。
- ❌ `import` 写在 `sys.path.insert` **之前** —— 顺序反了就 ImportError；`# noqa: E402` 只是给读代码的人看的记号（仓里没有 flake8），不是必需的。

## 2. 分节：SECTIONS + section() + check()

```python
SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")
```

用法是「一个节 = 一次 `section()` 调用 + 若干 `check()`」，节之间用注释横线隔开：

```python
# ── 3. 编码声明 ────────────────────────────────────────────────
section("编码声明")
check("coding: utf-8" in read(SELF), "首两行有 coding 声明")
```

**这是「新文件推荐」，不是强制。** 存量 37 个里三件套齐全的只有 6 个（`test_dataset_readonly` / `test_install_location` / `test_mechanical` / `test_repo_hygiene` / `test_run_tests` / `test_t15_replay`），剩下 31 个是「顺序 `check` 到底」或自造 PASS/FAIL 列表。两者的差别只在**失败时好不好定位**：分节的输出能一眼看出红在哪一节，不分节只能看最后一个断言。新文件没有理由放弃这个便宜。

反面清单：

- ❌ 改 `check` 签名搞第四种变体。存量已有 4 种（`check(cond, msg)` ×6、`check(cond, msg, detail="")` ×1、`check(name, cond, detail="")` ×2、`check(name, fn)` ×1），别再发明第 5 种。
- ❌ `check` 里 `try/except: pass` 吞异常 —— 失败会假绿，且 `check_mechanical.py` 规则 5（except-pass）会抓。
- ❌ 只 `print` 不 `assert` —— 退出码永远是 0，跑器看到的绿是假的。
- ❌ `section()` 写在 `if` 里却**没有**在 else 分支打印跳过原因 —— 节数会变但没人知道为什么（正解见 §5）。

## 3. docstring：票号 + 「无框架，assert 直跑」+ 覆盖清单

```python
"""test_dataset_readonly.py — 旋转数据集写保护自检（#148，无框架，assert 直跑）。

背景：<为什么存在这条测试，一到三句，写清它防的是什么事故>

覆盖：
1. 只读标记默认关闭（生产脚本行为不变）
2. 只读模式下三种写口全部被拒
3. 被拒时生产数据集字节不变（sha256 前后一致）
"""
```

三个要素各有用处：

- **票号**：`git log` 只到 commit，票号能回到「为什么要有这条测试」。
- **「无框架，assert 直跑」**：一句话挡掉「为什么不用 pytest」的重复提问（21/37 写了「无框架」，其中 18 个是精确的这一句）。
- **`覆盖：` 逐条列被测点**：它是这份测试的**清单**，不是 changelog。

**`覆盖：` 条目数与 section 数不要求一一对应。** 票里写的是「应与 section 数对应」，但存量事实不支持这条：17 个文件有 `覆盖：` 却一次 `section()` 都没调；反过来 2 个文件分了节却没写 `覆盖：`；即使三件套齐全的 6 个里也有对不上的（`test_dataset_readonly` 6 条 / 5 节，`test_install_location` 7 条 / 8 节）。所以这里写的是：**新文件建议对齐**，真正的硬要求只有一条——**列出来的每条都真的被断言过**。

反面清单：

- ❌ 写 `pytest` / `unittest` 风格（fixture、`Test` 类、`def test_` 前缀的函数）—— 跑器不装这两个包，文件会直接 ImportError。
- ❌ 把 `覆盖：` 写成「本次改了什么」的 changelog —— 被测点是稳定清单，改动记录归 commit message。
- ❌ 列了 8 条覆盖但只断言了 3 条 —— 清单比代码长，是最容易骗过评审的形态。
- ❌ docstring 里贴大段路径字面量 —— `check_mechanical.py` 规则 4 豁免 docstring，但 `test_dataset_readonly` 的活数据集扫描**不豁免**（见 §7）。

## 4. 收尾：末尾必打「N 节全部通过」

文件最后一行（或 `main()` 的最后一句）：

```python
print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
```

这一行是**给人看的**：跑器只转述退出码（`PASS`/`FAIL` + 退出码），它不知道你跑了几节。没有这行，人无法从日志判断「是真的 9 节都过了，还是文件在中途静默结束了」。#147 的 `test_repo_hygiene.py` 初版就漏了这行，评审指出后补（commit `28c80a7`）——这是本文件存在的最直接原因。

格式变体是允许的，只要**节数 + 结论**都在：`test_install_location.py:266` 用 `print(f"\\ntest_install_location: {len(SECTIONS)} 节全部通过")`（不打节名列表）。`test_human_flag_loop.py` 用的是另一套（`总计: N PASS / M FAIL` + `ALL PASS`），那套自带计数，也算合格。

反面清单：

- ❌ 硬编码节数：`print("5 节全部通过")` —— 加一节就过期，且过期了没人会发现。
- ❌ 收尾行前面 `sys.exit(0)` —— 后面的节全被吞掉，日志还说「全过」。
- ❌ 只在全绿时打收尾行、有失败就静默 `sys.exit(1)` —— 失败时更需要那行说明「跑到第几节挂的」。
- ❌ 用 `return` 从模块级退出 —— 模块级 `return` 是语法错误；要么写 `main()`，要么直接让 `assert` 抛。

## 5. 无静默 skip：环境不具备就大声说，不许装绿

```python
if shutil.which("git") is None:
    print("\n!! SKIP(no git)：第 2/3/4 节需要 git，本机 PATH 里没有")
else:
    section("真实产生点被仓库内 .gitignore 命中")
    ...
```

三条要求，缺一不可：

1. **带原因打印**：为什么跳过、跳过了哪几节、缺的是什么（`git` / `bash` / 产物文件）。
2. **不要 `section()` 记进 `SECTIONS`**：跳过的节不进汇总行，于是「N 节全部通过」里的 N 会**变小**——这就是让跳过可见的机制。
3. **退出码仍是 0**：跳过不是失败（环境限制不是代码错），但**必须在日志里显眼**。用大写 `SKIP` 或 `!!` 开头，跑器不会替你标黄。

存量范例：`test_repo_hygiene.py:106`、`test_install_location.py:111/189/207`、`test_t15_replay.py:116`（缺 manifest 产物时打印「跳过——先跑 t15_replay.py run」）。

反面清单：

- ❌ `if not cond: return` / `sys.exit(0)` —— 跑器看到绿，人看到一片空白，这是最坏的形态。
- ❌ `except ImportError: pass` 假装模块可用 —— 同上，而且 `check_mechanical.py` 规则 5 会抓。
- ❌ 跳过了还 `section()` 一下 —— 汇总行把没跑的算成跑了。
- ❌ 用「注释掉那几节」代替跳过 —— 下次没人知道它们曾经存在。
- ⚠️ `test_s08_judge_loop.py:393` 的 `sys.exit(0)` 是**合格**的：它在 `print("...全部通过")` 之后才退，属于收尾而非跳过。判据是「退出前有没有把结果说出来」，不是「有没有 `sys.exit`」。

## 6. 编码：subprocess 与 open 一律显式 `encoding="utf-8", errors="replace"`

```python
p = subprocess.run([sys.executable, script, "--check"],
                   capture_output=True, text=True, timeout=120,
                   encoding="utf-8", errors="replace")

with open(path, encoding="utf-8", errors="replace") as f:
    text = f.read()
```

这是 Windows cp936 铁律（PR #124：子进程 GBK 输出用 UTF-8 解，炸线程）。两个参数分工不同：

- `encoding="utf-8"`：**规定**怎么解。不写就跟着 locale 走，Windows 上是 cp936。
- `errors="replace"`：解不动时**降级**成 `�` 而不是抛 `UnicodeDecodeError`。测试里读的是「别人的输出」，里面可能混进任何字节；让它红在断言上，不要红在解码上。

这条是**有机械门禁的**：`check_mechanical.py` 规则 2（subprocess-text：`text=True` 无 `encoding=`）与规则 3（open-encoding：文本模式 `open()` 无 `encoding=`）会判新增违规，退出码 1。存量违规进了 `scripts/mechanical_baseline.txt`（条数以文件实数为准，用 `grep -vc '^#' scripts/mechanical_baseline.txt` 查；其中相当一部分在测试文件里），**只减不增**——写死数字的旧文案已经漂移过一次（#171）。

反面清单：

- ❌ `subprocess.run(..., text=True)` 不带 `encoding=` —— 规则 2 抓，且 PR #124 就是这个形态。
- ❌ `open(path)` / `open(path, "w")` 文本模式不带 `encoding=` —— 规则 3 抓；写侧还会在 cp936 下**写坏**非 ASCII 内容。
- ❌ 只给 stdout 配编码、`stderr=PIPE` 单独解码时又忘了 —— `capture_output=True` 一份设置管两路，分开解码时要各带一份。
- ❌ 二进制模式加 `encoding=` —— `open(p, "rb", encoding=...)` 直接 ValueError。
- ⚠️ `errors="replace"` 目前只有 6/37 个文件写全（机械规则只管 `encoding`，不管 `errors`）。**新文件写全**；读子进程输出这类「内容不可控」的地方尤其要写。

**写侧编码也要对齐（#177 实测的静默糊化）。** 上面只管**读**侧：`encoding="utf-8"` 规定怎么解，但 Windows 下 Python 子进程的 stdout 是**管道**，默认按 locale（cp936）**写**。读 utf-8 读 cp936 字节 → 中文整片变 U+FFFD，而 `errors="replace"` 让这件事**静默发生**（比抛异常更难发现：`merge_gate` 的「输出尾部」就是这么糊掉的）。两条一起用：

- **写侧**：给子进程注入 `PYTHONIOENCODING=utf-8`（`run_tests.py:119` 姿势）。只管得住 Python 子进程。
- **读侧**：`gh` / `git` / `ffmpeg` 等外部工具不认这个变量，仍按控制台代码页吐字节 → 解码要容错：先按 `utf-8` 解、不行再按 `gbk`、都不行才 `utf-8/replace`。`hostpath.decode_output()` 是仓内唯一实现（#143 定），新代码直接 import 它，别另写一份。

**写侧不是可选项，但理由别想当然。** 实测（#177 评审打回后更正）：

- 普通 Python 子进程打印 GBK 编不出的字符（`✅`）会**自己在 print 处抛 UnicodeEncodeError、rc=1**；注入后 rc=0。
- 但像 `run_tests.py` 那样调过 `reconfigure(errors="replace")` 的子进程**不会崩**——它把 `✅` 变成 **`?`**。`?` 一旦写进字节，读侧无论怎么容错都变不回来。

所以两侧不是「二选一」：**注入保真**（`✅` 还是 `✅`），**读侧容错保底**（cp936 字节能解出中文）。只有读侧的实现在 `run_tests.py` 上会得到 `全部通过 ?`，正是票面禁止的「用 `?` 丢信息」。判据要能区分三件事：「读侧能解 cp936 字节」（去掉注入照样绿，证明不了什么）、「子进程没崩」（`reconfigure` 过的子进程本来就不崩）、「信息没丢」（只有注入才绿）。

判据是「**调用方拿到的文本可读**」，不是「我按 utf-8 解了」——两侧任一缺失都会静默糊化。反面清单：

- ❌ 只修打印侧不修解码侧 —— 消息能打出来了，但尾部仍是替换符糊，拦截原因只修一半（#177 的两层缺陷）。
- ❌ 只注入 `PYTHONIOENCODING` 就以为万无一失 —— `gh`/`git`/`ffmpeg` 不看它，仍要读侧容错。
- ❌ 桩打在业务函数层返回 `str` 去「验解码口径」 —— 字节→文本那一步根本没走到，测了个寂寞（#169 同款教训：桩要打在实际边界上）。验解码要起**真子进程**：`test_merge_gate.py` 第 12 节两例，一例 Python 子进程打 GBK 编不出的字符验**写侧**，一例子进程直接吐 cp936 字节验**读侧回退**。

## 7. 不碰活数据集：只读 `scripts/fixtures/` 期次快照

```python
FIXTURE = os.path.join(HERE, "fixtures", "t15_rotation_dataset_20261015.jsonl")
rows = [json.loads(l) for l in open(FIXTURE, encoding="utf-8") if l.strip()]
```

`references/rotation-dataset.jsonl` 是**真值单一事实源**，每期 append（见 `docs/agents/workflow.md` 约定 5）。测试读它有两个后果：断言「某期 19 行」会随数据增长变红；跑测试就碰到了真值文件。所以：

- **读**：`scripts/fixtures/` 下该期的冻结快照（目前只有 `t15_rotation_dataset_20261015.jsonl`）。
- **写**：显式传 `tempfile` 路径。跑器给每个测试子进程注入 `ROTATION_DATASET_READONLY=1`，此时写活路径一律抛 `DatasetReadOnlyError`。

这条有**两道门禁**，第二道比第一道狠：

1. 跑器注入 `ROTATION_DATASET_READONLY=1`（`run_tests.py:116`）。
2. `test_dataset_readonly.py:140-154` 用 `glob` 扫**全仓** `**/test_*.py`，逐行找 `rotation-dataset` 字面量——**注释和 docstring 也照抓**，出现即判红（`references`、`..`、`.DATASET` 等形态另有专门匹配）。

反面清单：

- ❌ 在任何 `test_*.py` 里写 `references/rotation-dataset.jsonl` 字面量 —— **注释里也不行**。#147 的反向断言清单第一版差点栽在这（见 `references/pitfalls.md` 第 670 行记录；实际落库的 `test_repo_hygiene.py` 只留了「不能写死该路径」的说明，没真写路径）。
- ❌ `rd.load(rd.DATASET)` / `rd.DATASET` —— `test_dataset_readonly.py:153` 专门抓这个形态。
- ❌ 测试里 `append_records` 往真值文件补数据「顺便修一下」—— 写口是 s08 的活，测试只验不写。
- ❌ 断言「某期有 N 行」时读活文件 —— 数据一增长就红，红的是数据不是代码。

## 8. 跑器：`python scripts/run_tests.py`

```
python scripts/run_tests.py                  # 全跑（发现 scripts/test_*.py）
python scripts/run_tests.py t15_replay       # 只跑名字里含 t15_replay 的
python scripts/run_tests.py -k vision        # 关键字再过滤
python scripts/run_tests.py --list           # 只列会被执行的文件
python scripts/run_tests.py --timeout 600    # 单文件超时（秒，默认 300）
```

语义（细节见 `scripts/run_tests.py` 模块 docstring）：

- **退出码 = 失败文件数**（0 全绿；用法错误 2）。所以 `echo $?` 给 1 就是 1 个文件红，给 3 就是 3 个。
- **首行**打印宿主 / 解释器绝对路径 / Python 版本 —— 双端对表先看这行。
- **`KNOWN_RED`**（`run_tests.py:45`，当前为空 `{}`）：名单内失败降级 `SKIP(known-red)` 且**不计失败**；名单内转绿打 `XPASS` 提醒移出。除这个名单外，跑器没有任何跳过路径。
- 子进程一律注入 `PYTHONIOENCODING=utf-8` 与 `ROTATION_DATASET_READONLY=1`。

反面清单：

- ❌ `pytest scripts/` / `python -m unittest` —— 没装，且这些文件不是那个形态。
- ❌ 新测试放到 `scripts/` 之外 —— 跑器只发现 `scripts/test_*.py`，放别处永远不会被跑。
- ❌ 把真红塞进 `KNOWN_RED` 让它变绿 —— 名单是「已知且已确认暂不修」的白名单，不是消红工具；空了才是常态。
- ❌ 手工逐个 `python scripts/test_x.py` 跑完就说「全绿」 —— 会漏掉新加的、也会漏掉只在另一侧（Windows / WSL）才红的。**双端各跑一次**。

## 9. cp936 裸跑：真的会 print 到 stdout 的字符必须可被 locale 编码

跑器给子进程注入 `PYTHONIOENCODING=utf-8`，所以**跑器全绿 ≠ 裸跑不崩**：`python scripts/test_x.py` 直接跑时 stdout 跟 Windows 控制台走（cp936），`print` 遇到 GBK 编不出的字符（🚨 U+1F6A8、✅ U+2705、⚠️、❌、U+FFFD、⊆ …）直接 `UnicodeEncodeError` 掀翻进程——不是断言红，是 traceback。#170 实测 38 个文件里 7 个含此类字符、3 个真的崩（另 4 个字符只在不打印的分支里所以 rc=0）。

**判据只有一条：这个字符真的会 `print` 到 stdout 吗？** 按字符表批量改是错的——emoji 出现在断言字符串、错误消息、从不进入的分支里都无害。

安全姿势（按侵入度从低到高）：

```python
# a) ASCII 记号：逐文件打印的进度记号用 ok / FAIL，别用 ✅❌
print(f"ok {fn.__name__}")

# b) per-call 降级打印：消息内容不可控（断言消息带 !r、生产叫人消息带 emoji）时，
#    try 正常打，UnicodeEncodeError 就 backslashreplace→decode 转义重打（#170 log() 同款）
line = f"  ok: {msg}"
try:
    print(line)
except UnicodeEncodeError:
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "backslashreplace").decode(enc, "replace"))
```

反面清单：

- ❌ 「跑器绿了就行」—— `PYTHONIOENCODING=utf-8` 是跑器的拐杖，约定是「裸脚本可直跑」，注入不能替代约定。
- ❌ 按字符表全仓扫出 emoji 就批量替换 —— 只改「真的会 print」的那几处，别动无害分支。
- ❌ 靠全局改 `sys.stdout.reconfigure(encoding="utf-8")` —— 生产模块入口这么做会把测试里对 stdout 的替换（`test_run_pipeline` 惯例）一起污染；生产代码用 per-call 降级，测试文件的 `check()` 同理。
- ❌ 为了不崩把断言从精确值比较退成弱断言（如 `"\\ufffd" in out` 改 `len(out) == 4`）—— #170 明令：只改「怎么打印」，断言强度不降。
- ❌ **拿 `ascii` 当降级目标编码，或用 `replace` 洗消息**（#177 实测二次崩）—— `msg.encode("utf-8","replace").decode("ascii","replace")` 看着像「转成安全 ASCII」，实际把**整条消息（连本来完全正常的中文）**洗成 U+FFFD；而 U+FFFD 在 GBK 下**同样编不出**，第二次 `print` 再抛且无人兜底 → `cp936` 控制台下整条命令以 traceback 结束（`merge_gate` 连「合并已成功」都报成退出码 1）。判据不是「消息里有没有 U+FFFD」，而是「消息里有没有**任何** GBK 编不出的字符」（`✓` `✅` `⚠` U+FFFD 编不出；中文、`—`、`┄` 可以）。正确目标是**真实 stdout 编码** + `backslashreplace`：能编的原样、编不出的转成 `\u2713` 这类文本。

## 附：数字怎么核

本文每个计数都是现场跑出来的，不是记忆。要复核（数字会随文件增长过期，务必重跑）：

```bash
# 文件数
git ls-files 'scripts/test_*.py' | wc -l

# 各要素采用率（把要素换成要查的正则）
grep -l '^#!/usr/bin/env python3' scripts/test_*.py | wc -l
grep -l 'SECTIONS = \[\]' scripts/test_*.py | wc -l
grep -l '节全部通过' scripts/test_*.py | wc -l
grep -l '无框架，assert 直跑' scripts/test_*.py | wc -l
grep -l '覆盖：' scripts/test_*.py | wc -l

# subprocess 调用与带 encoding 的数量：用 AST 数，别用 grep——跨行调用会数错。
# 思路：ast.walk 找 Call，func 是 subprocess.* 记一次，keywords 里有 encoding 再记一次。

# 机械检查存量
grep -vc '^#' scripts/mechanical_baseline.txt
python scripts/check_mechanical.py
```

## 采用率（截至 #162，37 个文件）

| 要素 | 采用 | 说明 |
|---|---|---|
| `#!/usr/bin/env python3` 首行 | 34/37 | 缺 3 个 |
| `coding: utf-8` 声明 | 34/37 | 缺 3 个，与上一条不是同一批 |
| `HERE = dirname(abspath(__file__))` | 32/37 | |
| `sys.path.insert(0, HERE)` | 31/37 | 另 4 个用 `dirname(...)` 变体，合计 35/37 |
| `SECTIONS = []` | 6/37 | |
| `def section(` | 7/37 | |
| `def check(` | 10/37 | 4 种签名变体 |
| 三件套齐全 | **6/37** | 见 §2 名单 |
| docstring `覆盖：` | 22/37 | |
| 收尾「N 节全部通过」 | 6/37 | 与三件套同一批，`test_install_location` 格式略异 |
| 「无框架」 | 21/37 | 精确「无框架，assert 直跑」18/37 |
| docstring 首行带票号 | 29/37 | |
| pytest / unittest | **0/37** | 这是全文最硬的一条 |
| 读 `scripts/fixtures/` | 3/37 | |
| `errors="replace"` | 6/37 | |
| subprocess 调用（AST） | 22 处 / 11 文件 | 其中 13 处带 `encoding=` |
| 文本 `open()` 无 `encoding=` | 18 处 | 全部已在 baseline 里，只减不增 |

**这张表是「现状」不是「目标」**。它说明的只有一件事：这 8 条约定的采用率参差不齐，所以本文写的是**新文件怎么写**，不是「存量该怎么改」——回改 37 个文件不在 #162 范围。
