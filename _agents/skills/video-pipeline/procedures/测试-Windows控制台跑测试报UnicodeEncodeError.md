# 测试 - Windows 控制台裸跑 python scripts/test_*.py 报 UnicodeEncodeError

## 触发条件
Windows 控制台（cp936/GBK 代码页）直接 `python scripts/test_*.py`，测试里出现 U+FFFD 或其它非 GBK 字符的 print/check 输出时炸：
`UnicodeEncodeError: 'gbk' codec can't encode character '\ufffd'`。

**2026-10-19 起状态更新（#170 / PR #176 合入 3109fd6）**：三个真崩文件已修复，裸跑约定现在真的成立——
- `scripts/run_pipeline.py:179` log() 与 `scripts/comfyui_selfheal.py` log()：per-call 降级（try print → except UnicodeEncodeError 用 `msg.encode(enc,"backslashreplace").decode(enc,"replace")` 按真实 stdout 编码重打，enc=getattr(sys.stdout,"encoding",None) or "utf-8"）。
- `scripts/test_hostpath.py` check() 同款降级打印（断言不动）；`scripts/test_schema_strip_motion.py` ✅→ok ASCII 记号。
- 判据见 `docs/agents/test-conventions.md` §9：「真的会 print 到 stdout 的字符必须可被 locale 编码」；跑器注入 PYTHONIOENCODING=utf-8 ≠ 约定成立。

**2026-10-20 补充（#177 / PR #179 合入 a54c66b）：上面这行抄写姿势的 `decode(enc,"replace")` 有坑，新代码别照抄。**
`replace` 在**解码**侧仍然可能生成 U+FFFD（它就是「替换」的产物），而 U+FFFD 在 GBK 下同样编不出——
真出事时（消息里已经夹了替换符）第二次 print 会再抛，等于降级链自己又崩一次。#177 的门禁就是这么死的：
它把 `ascii` 当目标编码（`encode("utf-8","replace").decode("ascii","replace")`），整条消息连正常中文都被洗成替换符。
**新代码请用两侧都 `backslashreplace`**（`scripts/merge_gate.py` 的 `_say()` 是当前定稿）：

```python
except UnicodeEncodeError:
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(msg.encode(enc, "backslashreplace").decode(enc, "backslashreplace"), flush=True)
```

存量三处（`run_pipeline.log` / `comfyui_selfheal.log` / `test_hostpath.check`）仍是 `decode(enc,"replace")`——
它们的**消息可控**（不含 U+FFFD），实测不崩，故未回改（#177 明确不动存量）。`merge_gate` 是唯一消息内容不可控的出口（gh 输出、子进程输出尾部），所以只有它必须用 `backslashreplace`。

## 首选姿势（可直接抄）
- 全量：`python scripts/run_tests.py`（跑器注入 utf-8，40 个 test_* 一次跑完）。
- 裸跑单文件诊断：现在多数文件可直接 `python scripts/test_xxx.py`；带非常规字符的新代码请照 §9 用 ASCII 记号或 per-call 降级，别再靠环境变量兜。

## 反面清单（试过不通的）
- 报错行（print）不是断言根因行，容易误诊——先看断言逻辑再看打印。
- 管道 `| tail` 只改显示：EXIT 码显示 0 是 tail 的退出码，不是 python 的——别信管道后的退出码。
- 别用 `sys.stdout.reconfigure()` 入口改全局：测试替换 stdout 的场景会互相干扰（#170 定稿理由）。
- 别按字符表批量改「文件里出现的非常规字符」：只改真的会打印到 stdout 的（4 个不打印分支的文件本来就不崩）。
- 断言不许弱化：test_hostpath.py:227 精确比较 `"\ufffd\ufffd\x00bad"` 只改打印方式；改完 mutation 验证（改返回值必须红）。
- 测试 docstring 写裸 `\U` 字面量是 SyntaxError（truncated \UXXXXXXXX escape）——措辞用「U+ 转义」。

## 验证方式
- 裸跑三修复文件（env -u PYTHONIOENCODING）rc=0；钉测试 = test_run_pipeline.py 第 10 节（gbk strict TextIOWrapper 盖 sys.stdout，🚨 降级为 U0001f6a8 转义）。
- run_tests.py 汇总行全绿且退出码 0；check_mechanical.py 新增违规 0。

---
来源：#171 执行过程（2026-10-19）初版；#170 执行（2026-10-19，PR #176）更新为修复后状态；
#177 执行（2026-10-20，PR #179）补「decode 侧用 replace 仍会二次崩」——新代码用两侧 backslashreplace。
