#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_mechanical.py — 机械检查器自身的护栏（#142，无框架，assert 直跑）。

分两层：
- 前面各节喂**样例代码**给检查器，断言「该抓的抓到、不误报」——规则的灵敏度与
  豁免范围都钉死，改判据先改这里。
- 最后一节拿**真实仓库**跑一遍，断言 baseline 与现状一致（新增违规 0 处）。
  这一节就是门禁本身：run_tests.py 发现 test_*.py 即执行（与 #147/#148 同一条
  挂载路径，不另造第二套执行逻辑），CI 调 runner 时自动带上。

覆盖：
1. 规则 1 syntax：语法错抓得到、带行号，且永远不进 baseline
2. 规则 2 subprocess-text：text/universal_newlines 无 encoding 抓到，有 encoding 不报
3. 规则 3 open-encoding：文本模式无 encoding 抓到，二进制/动态模式/Path.open 不报
4. 规则 4 path-literal：/mnt、盘符、用户目录抓到，URL 与 docstring 不报，noqa 豁免
5. 规则 5 except-pass：吞异常抓到，收窄类型与有后续动作的不报
6. 规则 6 host-mismatch：声明与路径/工具跨侧即抓，合法声明与 any 不报
6b. 规则 7 converter-dup（#171）：re 模式实参写死跨侧转换形态抓到（含常量名/
    chr/f-string 间接构造），普通正则与路径使用不报
7. baseline 语义：收录不报、新增报出文件行、缩减提示、--shrink 一键缩表、有新增时拒绝
8. 事故形态回归：#124 的 text=True、#137 的盘符 E: 与 /mnt/ 字面量都抓得到
9. 真实仓库：baseline 与现状一致（新增违规 0 处）——门禁
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import check_mechanical as cm

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def scan(src, rel="sample.py"):
    """样例源码 → 违规列表（走真实扫描路径，不落盘）。"""
    return cm.scan_source(rel, src.encode("utf-8"))


def rules(found, rule):
    return [v for v in found if v.rule == rule]


def one(found, rule):
    hits = rules(found, rule)
    assert len(hits) == 1, f"期望恰好 1 处 {rule}，实得 {hits}"
    return hits[0]


# ── 1. 语法 ────────────────────────────────────────────────────────────────
section("规则 1 syntax：语法错抓得到，且不进 baseline")
bad = "def f(:\n    return 1\n"
found = scan(bad)
hit = one(found, cm.RULE_SYNTAX)
check(hit.line >= 1, f"语法错带行号：{hit.where()}")
check("def f(" in hit.text, f"片段回显出错的那一行：{hit.text}")
check(not any(v.rule != cm.RULE_SYNTAX for v in found), "语法错的文件不再往下套规则（早退）")
# 语法规则不进 baseline：--shrink 生成的基线里不能出现 syntax 行
text = cm.dump_baseline([cm.Violation(cm.RULE_SYNTAX, "x.py", 1, "boom")])
check("\tsyntax\t" not in text and "syntax" not in text,
      "dump_baseline 丢弃 syntax 条目（语法错误零容忍）")

# ── 2. subprocess text=True ────────────────────────────────────────────────
section("规则 2 subprocess-text：text 无 encoding 抓到，有 encoding 不报")
found = scan(
    "import subprocess\n"
    "subprocess.run(['a'], text=True)\n"
    "subprocess.run(['b'], universal_newlines=True)\n"
    "subprocess.run(['c'], text=True, encoding='utf-8')\n"
    "subprocess.run(['d'], encoding='utf-8')\n"
    "subprocess.run(['e'], text=False)\n"
    "flag = True\n"
    "subprocess.run(['f'], text=flag)\n")
hits = rules(found, cm.RULE_TEXT)
check(len(hits) == 2, f"text/universal_newlines 无 encoding 共 2 处，实得 {len(hits)}")
check([h.line for h in hits] == [2, 3], "抓的是第 2、3 行")
found = scan("import subprocess as sp\nsp.run(['a'], text=True)\n")
check(len(rules(found, cm.RULE_TEXT)) == 1, "别名 sp.run 也认得出")
found = scan("from subprocess import run\nrun(['a'], text=True)\n")
check(len(rules(found, cm.RULE_TEXT)) == 1, "from subprocess import run 也认得出")

# ── 3. open() 编码 ─────────────────────────────────────────────────────────
section("规则 3 open-encoding：文本模式无 encoding 抓到，二进制/动态模式不报")
found = scan(
    "open('a.json')\n"
    "open('b.json', 'w')\n"
    "open('c.bin', 'rb')\n"
    "open('d.bin', mode='wb')\n"
    "open('e.json', encoding='utf-8')\n"
    "mode = 'r'\n"
    "open('f.json', mode)\n")
hits = rules(found, cm.RULE_OPEN)
check(len(hits) == 2, f"省略模式与 'w' 共 2 处，实得 {len(hits)}")
check([h.line for h in hits] == [1, 2], "二进制模式与已给 encoding 的不报")
found = scan("import io\nio.open('a.json')\n")
check(len(rules(found, cm.RULE_OPEN)) == 1, "io.open 也认得出")
found = scan("import pathlib\npathlib.Path('a.json').open()\n")
check(not rules(found, cm.RULE_OPEN), "Path.open 不报（编码由 Path 侧约定，不是本条范围）")

# ── 4. 路径字面量 ──────────────────────────────────────────────────────────
section("规则 4 path-literal：跨侧路径抓到，URL/docstring/断言期望值/noqa 不报")
found = scan(
    "A = '/mnt/e/x.mp4'\n"
    "B = r'E:\\out'\n"
    "C = r'C:\\Users\\lmr\\a.json'\n"
    "D = '/home/lmr/index-tts'\n"
    "E = r'\\\\wsl.localhost\\Ubuntu\\home'\n"
    "F = 'http://127.0.0.1:9222/json'\n"
    "G = 'https://example.com/a'\n")
hits = rules(found, cm.RULE_PATH)
check(len(hits) == 5, f"5 处跨侧路径，实得 {len(hits)}：{[h.text for h in hits]}")
check(not [h for h in hits if "http" in h.text], "URL 不误报（盘符正则不吃 http:）")
found = scan('"""docstring 里提到 /mnt/e/x 是文档不是代码。"""\nA = 1\n')
check(not rules(found, cm.RULE_PATH), "docstring 里的路径不报")
# 正则字面量里的 "d:" 曾被当成 D 盘（director_to_h3_llm.py:276 实锤误报）
found = scan('PAT = r"(?:At|By) (\\d\\d):(\\d\\d\\.\\d\\d\\d)"\n')
check(not rules(found, cm.RULE_PATH), "正则转义里的 \"d:\" 不误报")
# 反例断言必须写出被禁的字面量才能验它不在（#152 的 test_install_location.py:176
# 实锤）——断言里的期望值不算违规，但普通调用里的照抓。
found = scan(
    "check('/home/lmr/.hermes' not in src, '不再写死第三份副本')\n"
    "assert '/mnt/e/x' not in src\n"
    "log('/mnt/e/y')\n")
hits = rules(found, cm.RULE_PATH)
check(len(hits) == 1, f"断言期望值豁免、log 里的照抓，实得 {[h.text for h in hits]}")
found = scan("A = '/mnt/e/x'  # noqa: mechanical\n")
check(not rules(found, cm.RULE_PATH), "noqa: mechanical 单行豁免生效")
found = scan("A = '/mnt/e/x'  # noqa: BLE001\n")
check(len(rules(found, cm.RULE_PATH)) == 1, "别的 noqa 不算豁免（不能顺手放行）")

# ── 5. 吞异常 ──────────────────────────────────────────────────────────────
section("规则 5 except-pass：吞干净抓到，收窄类型/有动作的不报")
found = scan(
    "try:\n    a()\nexcept Exception:\n    pass\n"
    "try:\n    b()\nexcept:\n    pass\n"
    "try:\n    c()\nexcept ValueError:\n    pass\n"
    "try:\n    d()\nexcept Exception:\n    log('x')\n"
    "try:\n    e()\nexcept Exception as exc:\n    print(exc)\n"
    "try:\n    f()\nexcept Exception:\n    ...\n")
hits = rules(found, cm.RULE_EXCEPT)
check(len(hits) == 3,
      f"except Exception: pass / 裸 except: pass / except Exception: ... 共 3 处，"
      f"实得 {len(hits)}")

# ── 6. 宿主声明 ────────────────────────────────────────────────────────────
section("规则 6 host-mismatch：声明与用法跨侧即抓，合法声明不报")
found = scan("__host__ = 'windows'\nP = '/mnt/c/x'\n")
check(len(rules(found, cm.RULE_HOST)) == 1, "声明 windows 却用 /mnt/ 被抓")
found = scan("__host__ = 'wsl'\nP = r'C:\\out'\n")
check(len(rules(found, cm.RULE_HOST)) == 1, "声明 wsl 却用盘符被抓")
found = scan("__host__ = 'wsl'\nCMD = 'wsl.exe -e bash'\n")
check(len(rules(found, cm.RULE_HOST)) == 1, "声明 wsl 却调 wsl.exe（嵌套）被抓")
found = scan("__host__ = 'wsl'\nP = '/mnt/c/x'\n")
check(not rules(found, cm.RULE_HOST), "声明 wsl 用 /mnt/ 是正当的，不报")
found = scan("__host__ = 'windows'\nP = r'C:\\out'\n")
check(not rules(found, cm.RULE_HOST), "声明 windows 用盘符是正当的，不报")
found = scan("__host__ = 'any'\nP = '/mnt/c/x'\nQ = r'C:\\out'\n")
check(not rules(found, cm.RULE_HOST), "声明 any 两侧都允许")
found = scan("__host__ = 'macos'\n")
hit = one(found, cm.RULE_HOST)
check("host-contract" in hit.text, f"未知宿主 id 报错并指向词表来源：{hit.text}")
found = scan("P = '/mnt/c/x'\n")
check(not rules(found, cm.RULE_HOST), "未声明 __host__ 的脚本不受规则 6 约束")

# ── 6b. 规则 7 converter-dup（#171）───────────────────────────────────────
# 背景：规则 4 的子串判据被评审实测绕过（chr()/拼接构造第二份转换正则，三条
# 子串全 False 照样九节全绿）。规则 7 盯 re API 的模式实参，展开字面量段
# （str / 模块常量名 / 拼接 / chr / f-string）后命中跨侧转换形态即抓。
section("规则 7 converter-dup：re 模式写死跨侧转换抓到，普通正则不报")
found = scan("import re\nre.compile(r\"/mnt/([a-z])/\")\n")
hit = one(found, cm.RULE_CONV)
check("/mnt/" in hit.text or "re 模式实参" in hit.text, f"命中片段回显：{hit.text}")
found = scan("import re\nre.match(r\"^([A-Za-z]):[\\/](.*)$\", p)\n")
one(found, cm.RULE_CONV)
found = scan("import re\nre.sub(r\"/mnt/[a-z]/\", \"\", s)\n")
one(found, cm.RULE_CONV)
# 模块级常量名间接引用（写手最自然的「藏一下」姿势）
found = scan(
    "import re\n"
    "PFX = \"/mnt/\"\n"
    "re.compile(PFX + \"[a-z]/(.*)$\")\n")
one(found, cm.RULE_CONV)
# 评审实测绕过形态：chr 拼接（#143 评审反例，规则 4 子串全 False）
found = scan(
    "import re\n"
    "re.compile(chr(47) + \"mnt\" + chr(47) + \"[a-z]/\")\n")
one(found, cm.RULE_CONV)
found = scan(
    "import re\n"
    "re.compile(\"^([a-zA-Z])\" + chr(58) + chr(92))\n")
one(found, cm.RULE_CONV)
# f-string 内嵌 chr 段
found = scan(
    "import re\n"
    "re.compile(f\"{chr(47)}mnt/[a-z]/\")\n")
one(found, cm.RULE_CONV)
# 反面：普通正则 / 路径使用不归规则 7 管
found = scan(
    "import re\n"
    "re.compile(r\"\\d+\")\n"
    "p.startswith(\"/mnt/\")\n")
check(not rules(found, cm.RULE_CONV), "普通正则与路径使用不误报规则 7")
# 豁免面：检查器自身（判据正则即合法反例）不进规则 7 的账；
# 附加普通路径字面量，证明豁免只盖规则 7、规则 4 照抓（不外溢）。
found = scan(
    "import re\nRE_MNT = re.compile(r\"/mnt/([a-z])/\")\nP = '/mnt/c/x'\n",
    rel="scripts/check_mechanical.py")
check(not rules(found, cm.RULE_CONV), "CONV_EXEMPT_FILES 只豁免规则 7（检查器自身）")
check(rules(found, cm.RULE_PATH), "同一份源码规则 4 照抓（豁免不外溢）")

# ── 7. baseline 语义 ───────────────────────────────────────────────────────
section("baseline：收录不报 / 新增报出文件行 / 缩减提示 / --shrink / 拒绝")
tmp = tempfile.mkdtemp(prefix="mech_fixture_")
try:
    os.makedirs(os.path.join(tmp, "scripts"))
    sample = os.path.join(tmp, "scripts", "demo.py")
    baseline = os.path.join(tmp, "scripts", "mechanical_baseline.txt")

    def write(text):
        with open(sample, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def run(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cm.main(list(argv) + ["--root", tmp, "--baseline", baseline])
        return code, buf.getvalue()

    write("import subprocess\nsubprocess.run(['a'], text=True)\n")
    code, out = run(["--shrink"])
    check(code == 0, f"无 baseline 时 --shrink 生成，退出 0，实得 {code}")
    check(os.path.exists(baseline), "baseline 文件已落盘")
    code, out = run([])
    check(code == 0, f"存量入 baseline 后检查通过，退出 0，实得 {code}")

    write("import subprocess\nsubprocess.run(['a'], text=True)\n"
          "subprocess.run(['b'], text=True)\n")
    code, out = run([])
    check(code == 1, f"新增一处 → 退出 1，实得 {code}")
    check("scripts/demo.py:3" in out, f"点名文件与行：{out.strip().splitlines()[-1]}")
    code, out = run(["--shrink"])
    check(code == 1, f"有新增时 --shrink 拒绝执行，退出 1，实得 {code}")
    check("拒绝缩表" in out, "拒绝时说明原因")

    # 语法错永远算新增：早先 compare() 把 syntax 整个跳过，放个语法错文件仍退出 0
    broken = os.path.join(tmp, "scripts", "broken.py")
    with open(broken, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("def f(:\n    return 1\n")
    code, out = run([])
    check(code == 1, f"语法错 → 退出 1（零容忍，不比对 baseline），实得 {code}")
    check("scripts/broken.py" in out and "syntax" in out,
          "语法错点名文件并标注 syntax 规则")
    os.remove(broken)

    # 跨文件抵消：a.py 修掉一处、b.py 新增同形态一处，必须仍然判红并点名 b.py
    other = os.path.join(tmp, "scripts", "other.py")
    with open(other, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import subprocess\nsubprocess.run(['x'], text=True)\n")
    code, out = run([])
    check(code == 1,
          f"别的文件新增同形态违规 → 仍退出 1（不得被 a.py 的缩减余额抵消），实得 {code}")
    check("scripts/other.py:2" in out, f"点名真正新增的那个文件：{out.strip().splitlines()[-1]}")
    os.remove(other)

    write("import subprocess\nsubprocess.run(['a'], text=True, encoding='utf-8')\n")
    code, out = run([])
    check(code == 0, f"修掉违规后检查通过（baseline 允许多出），退出 0，实得 {code}")
    check("可缩减" in out, "修掉一处后提示可缩表")
    code, out = run(["--shrink"])
    check(code == 0, f"--shrink 缩表退出 0，实得 {code}")
    with open(baseline, encoding="utf-8") as fh:
        body = fh.read()
    check("subprocess-text" not in body, "缩表后该条已从 baseline 消失")
    check(body.startswith("# 机械检查 baseline"), "baseline 头部注释仍在")

    with open(baseline, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("subprocess-text\tscripts/demo.py\tX\tfrag\n")
    code, out = run([])
    check(code == 2, f"baseline 条数非整数 → 退出 2，实得 {code}")
    check("格式错误" in out, "报出格式错误")
    with open(baseline, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("bogus\tscripts/demo.py\t1\tfrag\n")
    code, out = run([])
    check(code == 2, f"未知规则名 → 退出 2，实得 {code}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── 8. 事故形态回归 ────────────────────────────────────────────────────────
section("事故形态：#124 的 text=True、#137 的 E:\\ 与 /mnt/ 都得抓到")
# PR #124：子进程 GBK 输出用 UTF-8 解 —— 规则 2 的原始事故
found = scan("import subprocess\n"
             "p = subprocess.run(cmd, capture_output=True, text=True)\n")
check(len(rules(found, cm.RULE_TEXT)) == 1, "#124 形态（text=True 无 encoding）被抓")
# #137：VISION_HOME 用 E:\\（在 WSL 恒假）—— 规则 4 的原始事故
found = scan('VISION_HOME = os.environ.get("H", r"E:\\AI\\LLM\\vision-judge")\n')
check(len(rules(found, cm.RULE_PATH)) == 1, "#137 形态（盘符字面量）被抓")
# #137：-File 传 /mnt/... 而 interop 不翻译 —— 规则 4 的另一种写法
found = scan("PS = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'\n")
check(len(rules(found, cm.RULE_PATH)) == 1, "#137 形态（/mnt/ 字面量）被抓")
# f-string 的常量段同样能抓（gen_krea2_cover 里的真实写法）
found = scan('p = f"/mnt/c/Users/LMR/AppData/Local/Temp/krea2_{tag}.json"\n')
check(len(rules(found, cm.RULE_PATH)) == 1, "f-string 里的 /mnt/ 常量段也能抓")

# ── 9. 真实仓库门禁 ────────────────────────────────────────────────────────
section("真实仓库：baseline 与现状一致（新增违规 0 处）")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code = cm.main([])
out = buf.getvalue()
check(code == 0, f"仓库当前无新增违规（退出 0），实得 {code}\n{out}")
check("新增违规 0 处" in out, "结论行明确写「新增违规 0 处」")
counts = out.split("（")[1].split("）")[0]
check("syntax=0" in counts,
      f"汇总行给出 syntax=0（全仓语法编译 0 错），实得 {counts!r}")

print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
