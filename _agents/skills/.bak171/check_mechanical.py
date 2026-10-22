#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""check_mechanical.py — 机械检查：只拦新增的编码 / 路径 / 吞异常违规（#142）。

## 为什么只拦新增

存量太大、一次修不完，但**新增必须停**。本检查器把现有违规全部收进 baseline
白名单（同目录 mechanical_baseline.txt，只减不增），此后新增一处即失败并指出
文件与行；修掉一处可以 --shrink 一键缩表。两条真实事故都落在这些规则上：

- PR #124：子进程 GBK 输出用 UTF-8 解（text=True 不带 encoding），炸线程
- #137：跨侧四层叠加，其中两层是路径字面量——VISION_HOME 用 "E:\"（在 WSL
  恒假）、"-File /mnt/..."（Windows 侧 interop 不翻译命令行路径参数）

## 七条规则（判据 / 豁免范围）

1. syntax —— 全仓 *.py 用 compile(bytes) 编译一遍，任何 SyntaxError 即违规。
   **不进 baseline**：语法错误零容忍，防的是「改了但没跑」的回退。
   豁免：无。范围：git 跟踪的 *.py（无 git 时退化为目录遍历，跳过 .git /
   __pycache__ / .wt / .venv / node_modules / graphify-out / .tmp_*）。

2. subprocess-text —— subprocess.run/Popen/call/check_output/check_call 带
   text=True（或 universal_newlines=True）却没有 encoding=。判据是「按文本解码
   但没指定编码」——Windows 侧子进程输出多为 cp936，UTF-8 解必炸。
   豁免：显式给了 encoding 的；text 不是字面量 True 的（静态看不出）。

3. open-encoding —— 内置 open() / io.open() 按文本模式打开却没给 encoding=。
   判据：模式省略或为不含 "b" 的字面量，且没有 encoding 关键字。
   豁免：二进制模式（rb/wb/ab 等）；模式是变量或表达式（静态不可判）；
   pathlib.Path.open 与 codecs.open（后者 encoding 是位置参数，形态不同）。

4. path-literal —— 字符串字面量里写死了跨侧路径：/mnt/<盘>/、盘符 X: 后跟
   斜杠、用户目录 C:\Users\<名> 或 /home/<名> 或 /Users/<名>、UNC 前缀
   \\wsl.$ / \\wsl.localhost。判据是「这条路径只在某一侧成立」——换宿主即静默失效。
   豁免：docstring（散文里的路径是文档不是代码）；断言里的期望值（`assert` 与
   `check(...)` 的字符串实参——反例断言必须写出被禁的字面量才能验它不在）；
   整文件豁免见 EXEMPT_FILES；单行豁免写 "# noqa: mechanical"。f-string 只有
   **常量段**会命中（ast 把它拆成 Constant），插值出来的那半截静态不可判，故不抓。

5. except-pass —— except Exception:（或裸 except:）且函数体只有 pass 或 ...。
   判据是「把异常吞干净且不留任何痕迹」——静默失败的头号来源。两种写法等价，
   一并算（存量里 0 处 ... 写法，进 baseline 的只有 pass）。
   豁免：捕获具体异常类型的（那是收窄后的显式约定）；函数体还有日志/降级的。

6. host-mismatch —— 脚本模块级声明 __host__ = "<宿主>" 后，用了另一侧的路径
   语法或该侧专有工具：声明 windows 却写 /mnt/、/proc/、wslpath；声明 wsl 却写
   盘符路径、UNC、或再调 wsl.exe（嵌套）。宿主 id 经 host_contract 解析，本文件
   不复制一份词表；两侧路径正则也只写一遍——规则 6 直接复用规则 4 的那三条。
   豁免：未声明的脚本不受检（存量 0 处声明，见 baseline 头部说明）；声明 any。
   词表经 host_contract 读取；本文件不直接碰契约文档本体（那是解析器的专属职责，
   test_host_contract 第 9 节按子串钉着，连注释里出现路径都会被抓）。

7. converter-dup —— re 模块入口（compile/search/match/fullmatch/sub/subn/
   findall/finditer/split）的**模式实参**里写死跨侧路径转换形态：/mnt/ 后跟
   字符类（挂载点正则）、字符类后跟冒号（盘符正则）。规则 4 只认字符串常量的
   整形状，评审实测换大小写、拼接、chr(…) 记号即可绕过（#171）；规则 7 盯的是
   「转换意图落在 re 调用里」——比规则 4 宽，又比「任何字符串含 /mnt」窄
   （startswith("/mnt/") 这类路径**使用**不抓）。模式实参静态展开一层：字符串
   字面量、模块级常量名、拼接段与 f-string 常量段、chr(…) 记号都算。
   豁免：CONV_EXEMPT_FILES（检查器自身的判据正则就是合法反例）；EXEMPT_FILES
   两文件经 add() 自动豁免。残余风险（运行期才拼得出、静态展开不了的模式）
   见 test_hostpath.py 第 7 节的等价实现实证。

## 用法

  python scripts/check_mechanical.py              # 检查（有新增违规退出 1）
  python scripts/check_mechanical.py --shrink     # 按当前状态缩表（有新增则拒绝）
  python scripts/check_mechanical.py --verbose    # 连 baseline 内的违规一起列
  python scripts/check_mechanical.py --root DIR --baseline FILE   # 供测试指向夹具

退出码：0 干净 / 1 有新增违规（或 --shrink 被拒） / 2 用法或 baseline 格式错误。
"""
import argparse
import ast
import os
import re
import subprocess
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASELINE_NAME = "mechanical_baseline.txt"
DEFAULT_BASELINE = os.path.join(HERE, BASELINE_NAME)

RULE_SYNTAX = "syntax"
RULE_TEXT = "subprocess-text"
RULE_OPEN = "open-encoding"
RULE_PATH = "path-literal"
RULE_EXCEPT = "except-pass"
RULE_HOST = "host-mismatch"
RULE_CONV = "converter-dup"

# 顺序即报告顺序；baseline 也按此排序，便于人读 diff。
RULES = (RULE_SYNTAX, RULE_TEXT, RULE_OPEN, RULE_PATH, RULE_EXCEPT, RULE_HOST,
         RULE_CONV)

# 整文件豁免：值必须写清原因，评审时能自证。只豁免规则 2-7，语法规则不豁免。
# 「唯一豁免点」指**生产代码**里唯一一个为了表达转换而豁免的模块；上面
# test_mechanical.py 那条是检查器自己的夹具，性质不同，别把这句读成「整表只有一条」。
EXEMPT_FILES = {
    "scripts/test_mechanical.py": "检查器自身的样例——样例必须写出违规形态才能断言抓得到",
    # #143：跨侧转换器是该规则的唯一合法反例——它存在的意义就是同时写死两侧
    # 路径语法（/mnt/<盘>/… 与 <盘>:\…）；不豁免则无法表达转换本身。全仓只允许
    # 这一个生产模块这样写：test_hostpath.py 第 7 节对**全仓被 git 跟踪的 .py**
    # 反查转换正则/拼接，另有命中探针确保该判据没被写坏。
    "scripts/hostpath.py": "跨侧路径转换的唯一生产实现处，必须写死两侧路径语法；由 test_hostpath.py 第 7 节反查无第二份",
}

# converter-dup（规则 7）的文件级豁免：检查器自身的判据正则（RE_DRIVE/RE_MNT/
# RE_CONV_*）就是「盘符/挂载点正则」的合法反例——样例必须写出违规形态才能断言
# 抓得到，与 test_mechanical.py 进 EXEMPT_FILES 同理。EXEMPT_FILES 的两文件经
# add() 自动豁免全部规则，不在这里重复列。
CONV_EXEMPT_FILES = frozenset({"scripts/check_mechanical.py"})

# 遍历时的目录黑名单（无 git 的退化路径用）。
SKIP_DIRS = {".git", "__pycache__", ".wt", ".venv", "venv", "node_modules",
             "graphify-out", ".tmp_t16_tests"}

SUBPROCESS_TEXT_APIS = {
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "subprocess.check_output", "subprocess.check_call",
}

# 路径字面量。盘符前不限定边界会误伤 URL（"http://" 的 p 前是 t），故用 (?<![\w])；
# 反斜杠也要排除——正则字面量 r"(\d\d):(\d\d)" 里的 "d:" 曾被当成 D 盘
# （scripts/director_to_h3_llm.py:276 实锤），把非路径字符串收进 baseline 会误导缩表。
RE_DRIVE = re.compile(r"(?<![\w\\])([A-Za-z]):[\\/]")
RE_MNT = re.compile(r"/mnt/[A-Za-z]/")
RE_UNC = re.compile(r"\\\\wsl[.$]")
RE_HOME = re.compile(
    r"(?:[A-Za-z]:[\\/]{1,2}Users[\\/][A-Za-z0-9._-]+"
    r"|/home/[A-Za-z0-9._-]+"
    r"|/Users/[A-Za-z0-9._-]+"
    r"|" + RE_UNC.pattern + r")")

# 规则 7（#171）的转换形态：只认「/mnt/ 后跟字符类」与「字符类后跟冒号」两形，
# 不认任意 /mnt/ 出现——后者是路径使用不是转换器（避免误伤 startswith 等）。
# 字符类要求至少含一个字母：[0-9]:、[\d] 这类时间/数字正则不算盘符。
_CONV_CLS = r"\[[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*\]"
RE_CONV_APIS = frozenset({
    "re.compile", "re.search", "re.match", "re.fullmatch",
    "re.sub", "re.subn", "re.findall", "re.finditer", "re.split",
    "re.Pattern", "re.template",  # 预编译类型与模板入口也是「造正则」
})
RE_CONV_MNT = re.compile(r"/mnt/.{0,16}" + _CONV_CLS)
RE_CONV_DRV = re.compile(_CONV_CLS + r"\)?:")

# 规则 6 的两侧标记：路径语法复用上面三条正则（同一判据只写一遍），
# 外加各侧专有工具。改路径判据时不需要动这里。
WSL_MARKERS = (
    (RE_MNT, "WSL 侧 /mnt/<盘> 路径"),
    (re.compile(r"/proc/(?:version|net|mounts|cpuinfo)"), "WSL/Linux 侧 /proc 探测"),
    (re.compile(r"(?<![\w.])wslpath(?![\w])"), "wslpath 是 WSL 侧工具"),
)
WINDOWS_MARKERS = (
    (RE_DRIVE, "Windows 侧盘符路径"),
    (RE_UNC, "Windows 侧 UNC 路径"),
    (re.compile(r"(?<![\w.])wsl\.exe(?![\w])"), "wsl.exe 从 WSL 里调是嵌套"),
)

# 解析器不可用时的退化词表——不是第二份事实源：正常路径永远走 host_contract。
FALLBACK_HOSTS = frozenset({"wsl", "windows"})
ANY_HOST = "any"

HOST_DECL = "__host__"

# 仓库里护栏测试的断言辅助函数名（test_mechanical.py 与 test_install_location.py
# 都用 check(cond, msg) 这一款）。只用于识别「断言里的字面量是期望值」。
ASSERTION_HELPERS = frozenset({"check"})


class Violation(object):
    """一处违规：规则 / 仓库相对路径 / 行号 / 归一化源码片段。"""

    __slots__ = ("rule", "path", "line", "text")

    def __init__(self, rule, path, line, text):
        self.rule = rule
        self.path = path
        self.line = line
        self.text = text

    def key(self):
        return (self.rule, self.path, self.text)

    def where(self):
        return "%s:%d" % (self.path, self.line)

    def __repr__(self):
        return "<%s %s %s>" % (self.rule, self.where(), self.text)


class BaselineError(ValueError):
    """baseline 文件格式错误（行号与原文一并给出）。"""


def _utf8_safe_streams():
    """自身输出防崩：cp936 控制台下 '✅'/'▶' 会 UnicodeEncodeError（#140 AC3 同款）。

    检查器要打印源码片段，片段里什么都可能有；坏字符降级成 '?' 而不是让整条
    检查以退出码 1 崩掉——崩掉和「有新增违规」退出码撞车，读日志的人分不清。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def norm(text, limit=200):
    """归一化：折叠空白（指纹要抗缩进/换行变化），超长截断。"""
    flat = " ".join((text or "").split())
    return flat[:limit]


def _dotted(node, alias):
    """把表达式还原成点分全名（subprocess.run / sp.run / io.open）。"""
    if isinstance(node, ast.Name):
        return alias.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, alias)
        return None if base is None else base + "." + node.attr
    return None


def _module_alias(tree):
    """import 的局部名 → 点分全名（**含函数体内**的 import）。

    只看模块级会漏掉函数内的 `import subprocess as _sp`：compose_final.py 与
    submit_accel.py 各有 3 处这样写，于是 6 处 text=True 无 encoding 既不进
    baseline 也永远不被拦（#142 评审实测）。这里取全树 import 的并集；同名冲突
    时模块级优先（setdefault），宁可漏报也不误报别处的同名局部变量。
    """
    alias = {}
    local = {}
    for node in ast.walk(tree):
        bucket = local if node is not tree else alias
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.asname:
                    bucket[item.asname] = item.name
                else:
                    head = item.name.split(".")[0]
                    bucket[head] = head
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            for item in node.names:
                bucket[item.asname or item.name] = node.module + "." + item.name
    for name, dotted in local.items():
        alias.setdefault(name, dotted)
    return alias


def _docstring_ids(tree):
    """模块/类/函数的首个 Expr 字符串——散文里的路径不算违规。"""
    ids = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            ids.add(id(first.value))
    return ids


def _assertion_ids(tree):
    """assert / check(...) 的字符串实参——反例断言里的路径是**期望值**，不是代码。

    `check("/home/x/.hermes" not in src, "不再写死第三份副本")` 这类护栏断言必须
    写出被禁的字面量才能验它不在；把期望值当违规收录进 baseline 会误导缩表，
    也会让后来的人以为这行代码真的写了那个路径。只豁免「断言语句的直接实参」，
    普通调用（open/log/拼接）里的字面量照抓——豁免面越窄越好。
    """
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            args = [node.test]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ASSERTION_HELPERS:
            args = list(node.args)
        else:
            continue
        for arg in args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    ids.add(id(sub))
    return ids


def _kwarg(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw
    return None


def _is_true(node):
    return isinstance(node, ast.Constant) and node.value is True


def _open_mode(call):
    """open() 的模式：""=省略（文本）/ 字符串=字面量 / None=动态不可判。"""
    if len(call.args) > 1:
        mode = call.args[1]
    else:
        kw = _kwarg(call, "mode")
        mode = kw.value if kw is not None else None
    if mode is None:
        return ""
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return mode.value
    return None


def _noqa(lines, lineno):
    """该行（或上一行，链式调用常见）写了 noqa: mechanical。"""
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines) and "noqa" in lines[idx] and "mechanical" in lines[idx]:
            return True
    return False


def _segment(text, node):
    """节点源码片段（一行），拿不到就退回空串。"""
    try:
        seg = ast.get_source_segment(text, node)
    except (AttributeError, TypeError):
        seg = None
    return norm(seg or "")


def _fold_const_str(node):
    """纯常量表达式静态折叠：str 字面量 / chr(整数) / BinOp(Add) 链。

    折不出的（变量、运行期调用、其它运算）返回 None——宁可漏报不可误报。
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "chr" and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, int)
            and 0 <= node.args[0].value <= 0x10FFFF):
        return chr(node.args[0].value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_const_str(node.left)
        right = _fold_const_str(node.right)
        return None if left is None or right is None else left + right
    return None


def _module_const_strs(tree):
    """模块顶层字符串常量表：名字 → 值（供规则 7 展开模式实参）。

    值可以是纯常量表达式（str / chr / 拼接）——「哨兵常量拆成片段再拼接」
    是本仓规避字面量判据的既定写法（test_hostpath.py 的 MNT/MNT_CONV 实锤），
    评审还用 chr 拼接造常量，都折叠一层才看得到真实模式。List/Tuple/Set 里的
    字符串按 join 还原（元素全折出才收）。只取模块级：函数内局部常量跨文件
    引用不了，静态无从对账。
    """
    consts = {}
    for stmt in getattr(tree, "body", []):
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            folded = _fold_const_str(value)
            if folded is not None:
                consts[target.id] = folded
            elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                parts = [_fold_const_str(elt) for elt in value.elts]
                if parts and all(p is not None for p in parts):
                    consts[target.id] = "".join(parts)
    return consts


def _conv_pattern_text(node, consts):
    """规则 7：把 re API 的模式实参静态展开成文本（展开不出返回 None）。

    覆盖四形态：字符串字面量 / 模块级常量名 / 拼接与 f-string 的字面量段 /
    chr(…) 记号——后两种正是 #171 评审实测绕过规则 4 子串判据的写法。变量名
    查不到、展开后为空都返回 None（宁可漏报不可误报，残余风险记在
    test_hostpath.py 第 7 节）。

    遍历必须用先序递归而不是 ast.walk：walk 是 BFS，BinOp 拼接链
    a + b + c 会先吐最右操作数（顺序倒挂），chr(47) + "mnt" + … 拼出的
    "/mnt/…" 前缀就散了架——先序才按源码从左到右拼接。
    """
    pieces = []

    def visit(n):
        if isinstance(n, ast.Constant):
            if isinstance(n.value, str):
                pieces.append(n.value)
            return
        if isinstance(n, ast.Name):
            known = consts.get(n.id)
            if known:
                pieces.append(known)
            return
        if isinstance(n, ast.Call):
            fn = n.func
            if (isinstance(fn, ast.Name) and fn.id == "chr" and n.args
                    and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, int)
                    and 0 <= n.args[0].value <= 0x10FFFF):
                pieces.append(chr(n.args[0].value))
                return
            # "".join(常量序列)：评审实测的另一种拼接构造，按序展开实参
            if (isinstance(fn, ast.Attribute) and fn.attr == "join" and n.args
                    and isinstance(n.args[0], (ast.List, ast.Tuple, ast.Set))
                    and isinstance(fn.value, ast.Constant)
                    and isinstance(fn.value.value, str)):
                for elt in n.args[0].elts:
                    visit(elt)
                return
        for child in ast.iter_child_nodes(n):
            visit(child)

    visit(node)
    text = "".join(pieces)
    return text or None


def _host_ids():
    """宿主 id 词表来自 host_contract（唯一解析器），解析失败退回两值。

    这里**不直接读**契约文档——test_host_contract 第 9 节断言「除解析器外没有脚本
    直接读契约文档」，绕过 host_contract 会被它当场抓住（连注释里的路径字面量也算）。
    """
    try:
        sys.path.insert(0, HERE)
        import host_contract as hc
        return set(hc.hosts()) | {ANY_HOST}
    except (ImportError, OSError, ValueError) as exc:
        print("note: 读不到 host-contract 的宿主词表（%s），退回 wsl/windows" % exc,
              flush=True)
        return set(FALLBACK_HOSTS) | {ANY_HOST}


def _declared_host(tree):
    """模块级 __host__ = "<id>"；返回 (行号, 值, 是否字符串字面量)。"""
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == HOST_DECL:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return node.lineno, value.value, True
                return node.lineno, None, False
    return None, None, True


def scan_source(rel, data):
    """单文件违规列表（未过 baseline）。data 是原始字节，供 compile 认编码声明。"""
    # 全仓存量有 2 条 invalid escape sequence 的 SyntaxWarning（dy_login_win.py、
    # vision_bench_review.py 的 docstring）。它们不是错误，但 compile 与 ast.parse
    # 会各打一遍到 stderr，把真正的检查结论淹掉——这里静音，不吞任何 SyntaxError。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return _scan_source(rel, data)


def _scan_source(rel, data):
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    try:
        compile(data, rel, "exec")
    except SyntaxError as exc:
        lineno = exc.lineno or 0
        src = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        return [Violation(RULE_SYNTAX, rel, lineno, norm("%s | %s" % (exc.msg, src)))]
    except ValueError as exc:                       # 源码含空字节等
        return [Violation(RULE_SYNTAX, rel, 0, norm("无法编译: %s" % exc))]

    tree = ast.parse(text, filename=rel)
    alias = _module_alias(tree)
    docs = _docstring_ids(tree) | _assertion_ids(tree)
    exempt = rel in EXEMPT_FILES
    conv_exempt = exempt or rel in CONV_EXEMPT_FILES
    consts = _module_const_strs(tree)
    hosts = _host_ids()
    decl_line, decl_host, decl_ok = _declared_host(tree)
    found = []

    def add(rule, node, snippet=None):
        if exempt and rule != RULE_SYNTAX:
            return
        lineno = getattr(node, "lineno", 0)
        if _noqa(lines, lineno):
            return
        found.append(Violation(rule, rel, lineno, snippet or _segment(text, node)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func, alias)
            if name in SUBPROCESS_TEXT_APIS:
                text_kw = _kwarg(node, "text") or _kwarg(node, "universal_newlines")
                if text_kw is not None and _is_true(text_kw.value) \
                        and _kwarg(node, "encoding") is None:
                    add(RULE_TEXT, node)
            elif name in ("open", "io.open"):
                mode = _open_mode(node)
                if mode is not None and "b" not in mode \
                        and _kwarg(node, "encoding") is None:
                    add(RULE_OPEN, node)
            elif name in RE_CONV_APIS and node.args and not conv_exempt:
                pattern = _conv_pattern_text(node.args[0], consts)
                if pattern and (RE_CONV_MNT.search(pattern)
                                or RE_CONV_DRV.search(pattern)):
                    add(RULE_CONV, node,
                        norm("re 模式实参写死跨侧转换形态（/mnt/<盘>/ 或字符类+冒号）| %s"
                             % _segment(text, node)))
        elif isinstance(node, ast.ExceptHandler):
            body = node.body
            bare = node.type is None
            broad = isinstance(node.type, ast.Name) and node.type.id == "Exception"
            swallow = len(body) == 1 and (
                isinstance(body[0], ast.Pass)
                or (isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and body[0].value.value is Ellipsis))
            if (bare or broad) and swallow:
                add(RULE_EXCEPT, node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docs or not node.value:
                continue
            val = node.value
            if RE_DRIVE.search(val) or RE_MNT.search(val) or RE_HOME.search(val):
                add(RULE_PATH, node)

    if decl_line is not None and not exempt:
        if not decl_ok or decl_host not in hosts:
            found.append(Violation(
                RULE_HOST, rel, decl_line,
                norm("%s = %r（宿主 id 须取自 host-contract：%s）"
                     % (HOST_DECL, decl_host,
                        "、".join(sorted(hosts - {ANY_HOST}))))))
        elif decl_host != ANY_HOST:
            marks = WSL_MARKERS if decl_host == "windows" else WINDOWS_MARKERS
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docs:
                    continue
                for regex, why in marks:
                    if regex.search(node.value):
                        add(RULE_HOST, node,
                            norm("%s（脚本声明 __host__ = %r）| %s"
                                 % (why, decl_host, _segment(text, node))))
                        break
    found.sort(key=lambda v: (RULES.index(v.rule), v.line))
    return found


def _git_files(root):
    """git 跟踪的 *.py（相对路径，正斜杠）；不在仓库/没 git 时返回 None。"""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "*.py"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [item for item in (proc.stdout or "").split("\0") if item]


def _walk_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.append(rel.replace(os.sep, "/"))
    return out


def source_files(root):
    rels = _git_files(root)
    if rels is None:
        rels = _walk_files(root)
    return sorted(set(rels))


def scan(root):
    """全仓扫描；返回 (违规列表, 扫描文件数)。"""
    violations, files = [], source_files(root)
    for rel in files:
        path = os.path.join(root, rel.replace("/", os.sep))
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            print("note: 读不了 %s（%s），跳过" % (rel, exc), flush=True)
            continue
        violations.extend(scan_source(rel, data))
    return violations, len(files)


def load_baseline(path):
    """{(规则, 路径, 指纹): 允许条数}；文件不存在返回 None（与「空基线」区分）。"""
    if not os.path.exists(path):
        return None
    entries = {}
    with open(path, encoding="utf-8") as fh:
        rows = fh.read().splitlines()
    for lineno, line in enumerate(rows, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            raise BaselineError("%s:%d 需要 4 个 TAB 分隔字段，实得 %d"
                                % (path, lineno, len(parts)))
        rule, rel, count, fingerprint = parts
        if rule not in RULES:
            raise BaselineError("%s:%d 未知规则 %r" % (path, lineno, rule))
        try:
            entries[(rule, rel, fingerprint)] = int(count)
        except ValueError:
            raise BaselineError("%s:%d 条数不是整数: %r" % (path, lineno, count))
    return entries


def compare(violations, baseline, scanned_paths=None):
    """分组比对；返回 (新增[(violation, 允许数)], 可缩减[(key, 现有, 允许)])。

    语法违规**不参与 baseline 比对**：它们永远算新增（零容忍，见模块 docstring）。
    早先这里直接把 syntax 跳过，结果全仓放一个语法错文件检查器仍退出 0——门禁
    形同虚设（#142 评审实测打回）。现在 syntax 直接进 new，报告与退出码都走正常
    的新增路径。

    路径搬家（文件改名/换目录）会让同一指纹从一条 baseline 变成「新增 + 可缩减」
    两份噪音；这里把同规则同指纹的 stale 余额先吸收掉，只报真正多出来的。
    **但只吸收「那个文件已经不在扫描范围里」的余额**——否则 a.py 修掉一处、
    b.py 新增一处会被静默抵消，退出码 0 且 b.py 不被点名，等于绕过「新增必失败」。
    """
    scanned_paths = set(scanned_paths or ())
    groups = {}
    syntax_new = []
    for item in violations:
        if item.rule == RULE_SYNTAX:
            syntax_new.append((item, 0))
            continue
        groups.setdefault(item.key(), []).append(item)

    excess = []
    for key, items in groups.items():
        allowed = baseline.get(key, 0)
        if len(items) > allowed:
            excess.append((key, items[allowed:]))

    # stale 的键与 violations 的键同构；吸收时只认「同规则 + 同指纹 + 原文件已消失」。
    stale = {}
    for key, allowed in baseline.items():
        current = len(groups.get(key, []))
        if current < allowed:
            stale[key] = [current, allowed]          # [现有条数, 允许条数]

    def absorbable(stale_key, new_key):
        if stale_key[0] != new_key[0] or stale_key[2] != new_key[2]:
            return False
        if stale_key[1] == new_key[1]:
            return True                              # 同一文件内缩量，正常
        return stale_key[1] not in scanned_paths     # 原文件已不在仓库：才算搬家

    new = list(syntax_new)
    for key, items in excess:
        need = len(items)
        for skey in sorted(stale):
            if need <= 0:
                break
            if not absorbable(skey, key):
                continue
            current, allowed = stale[skey]
            take = min(allowed - current, need)
            if take <= 0:
                continue
            current += take
            need -= take
            if current >= allowed:
                stale.pop(skey)
            else:
                stale[skey] = [current, allowed]
        if need:
            for item in items[:need]:
                new.append((item, baseline.get(key, 0)))

    new.sort(key=lambda pair: (RULES.index(pair[0].rule), pair[0].path, pair[0].line))
    stale_rows = [(k, v[0], v[1]) for k, v in sorted(stale.items())]
    return new, stale_rows


def dump_baseline(violations):
    """按当前状态生成 baseline 文本（含头部注释）。"""
    groups = {}
    for item in violations:
        if item.rule == RULE_SYNTAX:
            continue
        groups[item.key()] = groups.get(item.key(), 0) + 1
    order = {rule: idx for idx, rule in enumerate(RULES)}
    lines = [
        "# 机械检查 baseline（#142）——只减不增。",
        "# 每条规则的判据与豁免范围写在 scripts/check_mechanical.py 的模块 docstring 里，",
        "# 这里只放存量清单；新增违规不进本文件（--shrink 遇到新增会直接拒绝）。",
        "#",
        "# 格式：规则<TAB>相对路径<TAB>允许条数<TAB>归一化源码片段",
        "# 指纹用源码片段而非行号：插行删行不会让基线失效，只有真改代码才会。",
        "# 语法规则不进本文件——语法错误零容忍，一次也不许进白名单。",
        "# 规则 6（host-mismatch）当前 0 条：全仓尚无脚本声明 __host__，声明即受检。",
        "# 规则 7（converter-dup，#171）盯 re 模式实参里的第二份转换形态；检查器",
        "# 自身判据正则走 CONV_EXEMPT_FILES，hostpath.py 走 EXEMPT_FILES，存量 0 条。",
        "",
    ]
    for key in sorted(groups, key=lambda k: (order[k[0]], k[1], k[2])):
        rule, rel, fingerprint = key
        lines.append("%s\t%s\t%d\t%s" % (rule, rel, groups[key], fingerprint))
    return "\n".join(lines) + "\n"


def report(violations, files, baseline, new, stale, verbose=False):
    """打印结论；返回退出码。"""
    counts = {}
    for item in violations:
        counts[item.rule] = counts.get(item.rule, 0) + 1
    print("机械检查：扫描 %d 个 .py，违规 %d 处（%s）"
          % (files, len(violations),
             "、".join("%s=%d" % (r, counts.get(r, 0)) for r in RULES)), flush=True)
    if not new:
        print("新增违规 0 处 —— baseline 覆盖 %d 条" % len(baseline), flush=True)
    if new:
        print()
        print("新增违规 %d 处（baseline 未收录，必须修掉或说明后进 baseline）："
              % len(new), flush=True)
        for item, allowed in new:
            print("  [%s] %s —— %s" % (item.rule, item.where(), item.text), flush=True)
            if allowed:
                print("    该文件此形态基线允许 %d 处，本次多出" % allowed, flush=True)
    if stale:
        print()
        print("可缩减 %d 条（已修掉但基线还留着，跑 --shrink 一键缩表）：" % len(stale),
              flush=True)
        for (rule, rel, fingerprint), current, allowed in stale:
            print("  [%s] %s 现有 %d / 允许 %d —— %s"
                  % (rule, rel, current, allowed, fingerprint), flush=True)
    if verbose:
        print()
        print("全部违规（含 baseline 内）：", flush=True)
        for item in violations:
            print("  [%s] %s —— %s" % (item.rule, item.where(), item.text), flush=True)
    return 1 if new else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="check_mechanical.py",
        description="机械检查：只拦新增的编码/路径/吞异常违规（#142）")
    parser.add_argument("--root", default=None, help="仓库根（默认本文件上一级）")
    parser.add_argument("--baseline", default=None,
                        help="baseline 文件（默认 <root>/scripts/%s）" % BASELINE_NAME)
    parser.add_argument("--shrink", action="store_true",
                        help="按当前状态重写 baseline（有新增违规时拒绝执行）")
    parser.add_argument("--verbose", action="store_true", help="连 baseline 内的也列出")
    args = parser.parse_args(argv)
    _utf8_safe_streams()

    root = os.path.abspath(args.root or ROOT)
    if not os.path.isdir(root):
        print("用法错误: --root 不是目录: %s" % root, flush=True)
        return 2
    baseline_path = os.path.abspath(
        args.baseline or os.path.join(root, "scripts", BASELINE_NAME))

    violations, files = scan(root)
    try:
        baseline = load_baseline(baseline_path)
    except BaselineError as exc:
        print("baseline 格式错误: %s" % exc, flush=True)
        return 2
    missing = baseline is None
    if missing:
        print("note: baseline 不存在（%s），按空基线处理" % baseline_path, flush=True)
        baseline = {}
    new, stale = compare(violations, baseline, source_files(root))

    if args.shrink:
        # 首次生成不算「新增」：没有 baseline 文件时全部存量就是待收录的起点，
        # 否则这个仓库永远生不出第一份 baseline（自举死锁）。
        if new and not missing:
            print()
            print("拒绝缩表：有 %d 处新增违规，先修掉（或说明后手工入基线）再缩表"
                  % len(new), flush=True)
            for item, _allowed in new:
                print("  [%s] %s —— %s" % (item.rule, item.where(), item.text),
                      flush=True)
            return 1
        text = dump_baseline(violations)
        with open(baseline_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        entries = len([ln for ln in text.splitlines()
                       if ln.strip() and not ln.startswith("#")])
        print("已重写 baseline: %s（%d 条，缩减 %d 条）"
              % (baseline_path, entries, len(stale)), flush=True)
        return 0

    code = report(violations, files, baseline, new, stale, verbose=args.verbose)
    if missing and not violations:
        print("（无违规且 baseline 缺失——首次用 --shrink 生成）", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
