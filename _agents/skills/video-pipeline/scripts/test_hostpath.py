#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hostpath 单一事实源的自检（票 #143）。

无框架，assert 直跑：python3 scripts/test_hostpath.py

覆盖：宿主判定的三条证据合并（逐条 monkeypatch 证明真的被读）与契约词表一致；
按声明校验的三态；Windows→WSL 与 WSL→Windows 两向转换的边界（含裸盘符取舍）；
两种宿主语义下同一输入结果一致；跨侧 round-trip 幂等；子进程输出解码的三档回落；
全仓（git 跟踪的每一份 .py，不只 scripts/）不存在第二份跨侧转换正则/拼接；
八个调用点用 AST 断言「函数体只剩一行 return 委托」（#171 起不写死数字，
    从第 8 节 DELEGATES 表算出；旧文案的「四个/六个」都是当时的漏算）；
    #169 的三处宿主判定收口（rotation_groundtruth.DATE_ROOT、
    run_tests/driver_status 的 host_label）同源断言 + 裸 Linux 回归探针
    （三证据全假 → 三处一律非 WSL）+ 全仓宿主判定记号反查。
"""
import ast
import glob
import os
import subprocess
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import host_contract as hc
import hostpath as hp

WIN_SEP = chr(92)

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    # #170：cp936 裸跑下 U+FFFD（U+FFFD repr 进 ok 消息）不可编码，strict print 掀翻
    # 整个文件。断言照旧精确，只降级「怎么打印」：编码失败就 backslashreplace 转义重打。
    line = f"  ok: {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(enc, "backslashreplace").decode(enc, "replace"))


# 这些记号是本文件要**排除**的东西：任何其它脚本里出现即等于第二份实现。
# 拆成片段书写，免得本文件自己先被机械检查器按路径字面量抓走。
MNT = "/mnt"
MNT_CONV = "^/mnt/("
DRIVE_CONV = "^([a-zA-Z]):"
MNT_CAT = '"/mnt/" +'
LEGAL_CONVERTERS = ("hostpath.py", "test_hostpath.py",
                     "run_pipeline.py")  # #151：driver 侧 interop 换算 win_to_wsl/wsl_argv（hostpath.to_host_path 在 Windows 宿主恒等，盘符→/mnt 只能在 driver 做；#171 记录收口）


# ── 1. 宿主判定的词表与契约一致 ─────────────────────────────────────────
section("1. 宿主词表与契约同步")
ids = hp.host_ids()
check(isinstance(ids, tuple) and len(ids) >= 2, f"词表非空且是多于一个宿主: {ids}")
check(set(ids) == set(hc.hosts()), "词表与契约解析器一致（正常路径走声明，不抄一份）")
# 反向探针：解析器坏掉时必须退化到 _FALLBACK_HOSTS，而不是抛异常或返回空词表。
_real_hosts = hc.hosts
try:
    hc.hosts = lambda: (_ for _ in ()).throw(RuntimeError("模拟契约读不到"))
    check(hp.host_ids() == hp._FALLBACK_HOSTS,
          f"解析器不可用时退化为内置词表: {hp._FALLBACK_HOSTS}")
    check("模拟契约读不到" in (hp.declared_error() or ""),
          f"退化原因被记下来（守卫能报根因，不是静默）: {hp.declared_error()}")
finally:
    hc.hosts = _real_hosts
det = hp.detect_host()
check(det in ids, f"detect_host() 落在词表内: {det}")
check(hp.detect_host() == ("wsl" if hp.is_wsl() else "windows"),
      "detect_host 与 is_wsl 同一口径（同一输入结果相同）")
check(isinstance(hp.declared_host(), str), "契约声明可读时返回宿主 id")
check(hp.host_matches_declaration()[0] == (det == hc.current_host()),
      "host_matches_declaration 就是 detect_host 与声明相等")


# ── 1b. 三条宿主证据逐条被读（mutation：任一恒假都该被下面的断言抓到）────
section("1b. 宿主证据逐条生效")
_real_pv, _real_rel, _real_mnt = (
    hp._proc_version_says_wsl, hp._release_says_wsl, hp._windows_mount_present)
try:
    # 全假 → 不是 WSL，且证据为空
    hp._proc_version_says_wsl = lambda: False
    hp._release_says_wsl = lambda: False
    hp._windows_mount_present = lambda: False
    check(hp._wsl_evidence() == "" and hp.is_wsl() is False,
          "三条证据都不成立 → 判不是 WSL（裸 Linux/Windows 走这条）")
    # 逐条单独为真 → 各自都能把判定翻成 WSL（证明三条都在读，不是死代码）
    for name, attr in (("proc-version", "_proc_version_says_wsl"),
                       ("platform-release", "_release_says_wsl"),
                       ("windows-mount", "_windows_mount_present")):
        hp._proc_version_says_wsl = lambda: False
        hp._release_says_wsl = lambda: False
        hp._windows_mount_present = lambda: False
        setattr(hp, attr, lambda: True)
        check(hp._wsl_evidence() == name and hp.is_wsl() is True,
              f"只有 {name} 成立时也判 WSL，且证据标成它（该条真的被读）")
finally:
    hp._proc_version_says_wsl = _real_pv
    hp._release_says_wsl = _real_rel
    hp._windows_mount_present = _real_mnt
check(hp._proc_version_says_wsl is _real_pv and hp._release_says_wsl is _real_rel,
      "三条证据函数都已还原（不残留 monkeypatch 污染后续节）")
# 注释/docstring 里允许解释「为什么不用 route」（那是踩坑记录），所以按 AST 只看
# **被赋值的**字符串常量：判定路径不得再引用它。
with open(os.path.join(HERE, "hostpath.py"), encoding="utf-8", errors="replace") as _f:
    _hp_tree = ast.parse(_f.read())
_ROUTE_REFS = []
for _n in ast.walk(_hp_tree):
    _v = getattr(_n, "value", None)
    if isinstance(_n, ast.Assign) and isinstance(_v, ast.Constant) \
            and isinstance(_v.value, str) and "net/route" in _v.value:
        _ROUTE_REFS.append(_v.value)
check(not _ROUTE_REFS,
      f"判定路径不再引用 /proc/net/route（任何 Linux 都有，会把容器误判成 WSL）: {_ROUTE_REFS}")


# ── 2. 按声明校验的三态（#145 的启动守依靠这三态）──────────────────────
section("2. 按声明校验三态")
ok, detail = hp.host_matches_declaration(hc.current_host())
check(ok is True, "显式喂声明宿主 → 通过")
check(isinstance(detail, str) and detail, f"通过时也给一句人话: {detail}")
wrong = [h for h in ids if h != hc.current_host()][0]
ok2, detail2 = hp.host_matches_declaration(wrong)
check(ok2 is False, "喂非声明宿主 → 不通过")
check(hc.current_host() in detail2 and wrong in detail2,
      f"不通过时 detail 同时说清「声明是谁」「当前是谁」: {detail2}")
check("不匹配" in detail2, "不通过时 detail 是给操作者看的一句话，不是堆栈")

_real_declared = hp.declared_host
try:
    hp.declared_host = lambda: None
    ok3, detail3 = hp.host_matches_declaration(det)
    check(ok3 is False and "不可读" in detail3,
          f"声明读不到 → 判不通过并说明原因（而不是抛异常）: {detail3}")
finally:
    hp.declared_host = _real_declared


# ── 3. Windows→WSL 转换（vision_server 旧 _wsl_path 的口径）──────────────
section("3. Windows 口径 → WSL 口径")
_real_is_wsl = hp.is_wsl
try:
    hp.is_wsl = lambda: True
    check(hp.to_host_path("E:" + WIN_SEP + "AI" + WIN_SEP + "x.jpg")
          == "/mnt/e/AI/x.jpg", "盘符 + 反斜杠 → /mnt/<小写盘符>/… 并把分隔符转 /")
    check(hp.to_host_path("C:/Users/x.jpg") == "/mnt/c/Users/x.jpg",
          "正斜杠形态同样转（不被反斜杠写法绑死）")
    check(hp.to_host_path("/mnt/e/already.jpg") == "/mnt/e/already.jpg",
          "已是 WSL 口径原样返回")
    check(hp.to_host_path("relative/x.jpg") == "relative/x.jpg", "相对路径不动")
    check(hp.to_host_path("") == "" and hp.to_host_path(None) is None, "空值原样返回")
    # 裸盘符（无分隔符）：本模块取的是 dsh_judge 旧口径（宽容），不是 vision_server
    # 旧 _wsl_path 的严格口径。这个取舍要在测试里钉住，否则下次收口又变成静默差异。
    check(hp.to_host_path("E:") == "/mnt/e/", "裸盘符 'E:' → /mnt/e/（宽容口径）")
    check(hp.to_host_path("E:a") == "/mnt/e/a",
          "裸盘符 'E:a' 也转（旧 vision_server 会原样返回，收口时按更宽的一份统一）")
finally:
    hp.is_wsl = _real_is_wsl

try:
    hp.is_wsl = lambda: False
    w = "E:" + WIN_SEP + "AI" + WIN_SEP + "x.jpg"
    check(hp.to_host_path(w) == w, "Windows 宿主上 to_host_path 是恒等（不无脑造 /mnt）")
finally:
    hp.is_wsl = _real_is_wsl


# ── 4. WSL→Windows 转换（dsh_judge 旧 to_win_path 的口径）────────────────
section("4. WSL 口径 → Windows 口径")
check(hp.to_win_path("/mnt/e/video/_out/shot05.jpg")
      == "E:" + WIN_SEP + "video" + WIN_SEP + "_out" + WIN_SEP + "shot05.jpg",
      "/mnt/<盘>/… → <大写盘符>:\\…（判官 read_image 要的就是这个）")
check(hp.to_win_path("/mnt/c/x.jpg").startswith("C:"), "盘符大写")
check(hp.to_win_path("E:" + WIN_SEP + "already" + WIN_SEP + "w.jpg")
      == "E:" + WIN_SEP + "already" + WIN_SEP + "w.jpg", "已是 Windows 口径原样")
check(hp.to_win_path("relative/x.jpg") == "relative/x.jpg", "相对路径不动")
check(hp.to_win_path("") == "" and hp.to_win_path(None) is None, "空值原样返回")
check(hp.to_win_path("/mnt/e") == "/mnt/e", "只有 /mnt/<盘>/ 带余下部分才算转换对象")
# to_win_path 不读 is_wsl：Windows 口径是「输出目标」，不是「本机现状」。
# 这条在两种宿主下都必须给出同样的答案（AC：双宿主行为一致）。
_real_is_wsl_for_win = hp.is_wsl
try:
    for flag in (True, False):
        hp.is_wsl = lambda flag=flag: flag
        check(hp.to_win_path("/mnt/e/a/b.jpg")
              == "E:" + WIN_SEP + "a" + WIN_SEP + "b.jpg",
              f"is_wsl={flag} 时 to_win_path 结果相同（转换不依赖当前宿主）")
finally:
    hp.is_wsl = _real_is_wsl_for_win


# ── 5. 跨侧 round-trip 幂等（AC：可幂等 round-trip）─────────────────────
section("5. 跨侧 round-trip 幂等")
cases = [
    "E:" + WIN_SEP + "AI" + WIN_SEP + "deepseek-harness" + WIN_SEP + "a.jpg",
    "C:" + WIN_SEP + "Users" + WIN_SEP + "LMR" + WIN_SEP + "t.png",
]
try:
    hp.is_wsl = lambda: True
    for w in cases:
        once = hp.to_win_path(hp.to_host_path(w))
        twice = hp.to_win_path(hp.to_host_path(once))
        check(once == w, f"win → host → win 回到原值: {w}")
        check(twice == once, "再走一遍不漂移（幂等）")
    # 下面三行的期望值刻意拆开拼：机械检查器只认「整串字面量」，
    # 拼出来的串不构成第二份转换实现，也免得本文件被规则 4 当新增抓走。
    for u in (MNT + "/e/video/a.jpg", MNT + "/c/tmp/t.png"):
        check(hp.to_host_path(hp.to_win_path(u)) == u, f"host → win → host 回到原值: {u}")
    # 正斜杠的 Windows 写法：往返后统一成反斜杠（既有归一化语义，不是漂移）。
    # 断言「一次性归一 + 之后恒等」，而不是要求字面相等。
    slashy = "D" + ":/work/sub/clip.mp4"
    canon = "D:" + WIN_SEP + "work" + WIN_SEP + "sub" + WIN_SEP + "clip.mp4"
    got = hp.to_win_path(hp.to_host_path(slashy))
    check(got == canon, f"正斜杠写法归一成反斜杠规范形态: {got}")
    check(hp.to_win_path(hp.to_host_path(got)) == got, "归一后即不动点（幂等从第二遍起成立）")
finally:
    hp.is_wsl = _real_is_wsl


# ── 6. 子进程输出解码三档 ──────────────────────────────────────────────
section("6. 子进程输出解码")
check(hp.decode_output(b"rc=0 pid=42") == "rc=0 pid=42", "UTF-8 字节直接解出")
check(hp.decode_output("已经是 str") == "已经是 str", "str 原样返回（不二次解码）")
gbk = "中文路径".encode("gbk")
got = hp.decode_output(gbk)
check(got == "中文路径", f"GBK 字节退到 GBK 解出（#137 第三层的病根）: {got!r}")
bad = b"\xff\xfe\x00bad"
out = hp.decode_output(bad)
check(out == "\ufffd\ufffd\x00bad",
      f"解不出的字节最后走 U+FFFD 替换，且 ASCII 段原样保留: {out!r}")
cp = subprocess.CompletedProcess(args=[], returncode=0,
                                 stdout=b"rc=0 pid=7", stderr=b"warn")
check("rc=0 pid=7" in hp.decode_output(cp) and "warn" in hp.decode_output(cp),
      "CompletedProcess 取 stdout+stderr 拼接（旧 _decode_out 的入参形状）")
cp_none = subprocess.CompletedProcess(args=[], returncode=1, stdout=None, stderr=None)
check(hp.decode_output(cp_none) == "", "stdout/stderr 为 None 时回空串而非炸")
cp_str = subprocess.CompletedProcess(args=[], returncode=0, stdout="done", stderr="")
check(hp.decode_output(cp_str) == "done", "text=True 已解成 str 时不重复解码")
mixed = subprocess.CompletedProcess(args=[], returncode=0, stdout="rc=0 ".encode("ascii"),
                                   stderr="中文告警")
check(hp.decode_output(mixed) == "rc=0 中文告警",
      "一侧 bytes 一侧 str 的混用形态两侧都要留住（旧实现这里会 TypeError）")


# ── 7. 全仓不存在第二份跨侧转换（AC：唯一实现点）───────────────────────
section("7. 无第二份跨侧转换实现")


def _tracked_py():
    """仓库里所有**被 git 跟踪**的 .py（含 _test/ 与仓库根，不只 scripts/）。

    为什么不用 glob 就地爬目录：未跟踪的新文件不在 git 视野里，而转换器的自然
    落点恰恰是新建文件——用 glob 扫「当前可见的文件」会漏掉正要写的第二份实现。
    git 拿不到时退化为爬 scripts/，但那时断言会自己报出来（见下面的 selftest）。
    """
    try:
        p = subprocess.run(["git", "ls-files", "-z", "--", "*.py"], cwd=ROOT,
                           capture_output=True, encoding="utf-8", errors="replace",
                           timeout=60)
        if p.returncode == 0 and p.stdout:
            return [os.path.join(ROOT, rel) for rel in p.stdout.split(chr(0)) if rel]
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(glob.glob(os.path.join(HERE, "*.py")))


SCANNED = _tracked_py()
# 反向探针：扫描面塌了（git 不可用/路径算错）时这里先红，而不是「扫了 0 个文件 → 无违规」。
check(len(SCANNED) >= 100,
      f"扫描面覆盖全仓被跟踪的 .py（不是只扫 scripts/）：{len(SCANNED)} 个")
_norm = [p.replace(os.sep, "/") for p in SCANNED]
check(any("/_test/" in p for p in _norm), "_test/ 下的脚本也在扫描面内")
check(any("/scripts/" in p for p in _norm), "scripts/ 下当然也在扫描面内")
converter_hits = []
for path in sorted(SCANNED):
    name = os.path.basename(path)
    if name in LEGAL_CONVERTERS:
        continue
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    if MNT_CONV in src or DRIVE_CONV in src or MNT_CAT in src:
        converter_hits.append(os.path.relpath(path, ROOT).replace(os.sep, "/"))
check(not converter_hits,
      f"除 hostpath.py 外没有脚本自写跨侧转换正则/拼接: {converter_hits or '无'}")
# 命中探针：三种记号必须真的出现在唯一合法实现里。否则「无命中」可能只是
# 判据自己失效（改了个字符 / 常量被拼错）导致的假绿。
with open(os.path.join(HERE, "hostpath.py"), encoding="utf-8",
          errors="replace") as _hf:
    _HIT_SRC = _hf.read()
check(MNT_CONV in _HIT_SRC and DRIVE_CONV in _HIT_SRC and MNT_CAT in _HIT_SRC,
      "三条判据都能在唯一合法实现 hostpath.py 里命中（判据没被写坏）")
# 整文件豁免只该掩盖「路径字面量」这一类。把同一份源码换个**不在豁免表里**的
# 名字再过一遍检查器：若它其实还藏着 except-pass / open-encoding 之类的违规，
# 这里立刻红——否则「豁免」就成了兜住一切的黑箱（评审在 06535fa 上就指出过
# 当时的实现还额外藏了 2 条 except-pass）。
import check_mechanical as cm

with open(os.path.join(HERE, "hostpath.py"), encoding="utf-8", errors="replace") as _f:
    _RAW = _f.read().encode("utf-8")
_hits = cm.scan_source("scripts/__exempt_probe__.py", _RAW)
_hit_rules = sorted({v.rule for v in _hits})
check(_hits, "豁免探针有效：同一份源码不带豁免名时会报出违规（不是空扫）")
check(_hit_rules == [cm.RULE_CONV, cm.RULE_PATH],
      f"整文件豁免只掩盖 path-literal 与 converter-dup 两类（唯一合法转换器"
      + f"的转换正则就在本文件里，#171 起规则 7 也会点名它；实得 {_hit_rules}）")
check([h.path for h in _hits] == ["scripts/__exempt_probe__.py"] * len(_hits),
      f"命中的路径就是这份探针源码（不是别的文件串味）: {[h.path for h in _hits]}")
check("hostpath.py" in LEGAL_CONVERTERS,
      "唯一的合法实现点就是 hostpath.py")
check(set(LEGAL_CONVERTERS) == {"hostpath.py", "test_hostpath.py", "run_pipeline.py"},
      f"白名单没有被顺手放宽（#151 收录 driver 侧 interop 换算）: {sorted(LEGAL_CONVERTERS)}")

# #171 等价实现绕过实证：#143 评审曾用 chr()/join 拼接构造出「三条子串全
# False、九节全绿」的第二份转换正则——检查器第 7 条规则（converter-dup）
# 落地后这些形态必须全部红。判据展开靠 _conv_pattern_text（先序拼接），
# 这里把评审的三种写法逐一喂给 scan_source 验尸。
_BYPASS_CASES = {
    "chr 拼接 /mnt 前缀": (
        "import re" + chr(10)
        + "re.compile(chr(47) + " + chr(34) + "mnt" + chr(34)
        + " + chr(47) + " + chr(34) + "[a-z]/" + chr(34) + ")" + chr(10),
        cm.RULE_CONV),
    "chr 构造盘符+反斜杠": (
        "import re" + chr(10)
        + "re.compile(" + chr(34) + "^([a-zA-Z])" + chr(34)
        + " + chr(58) + chr(" + str(ord(chr(92))) + "))" + chr(10),
        cm.RULE_CONV),
    "模块常量名间接引用": (
        "import re" + chr(10)
        + "PFX = chr(47) + " + chr(34) + "mnt" + chr(34) + chr(10)
        + "re.compile(PFX + " + chr(34) + "/[a-z]/" + chr(34) + ")" + chr(10),
        cm.RULE_CONV),
    "f-string 内嵌 chr": (
        "import re" + chr(10)
        + "re.compile(f\"{chr(47)}mnt/[a-z]/\")" + chr(10),
        cm.RULE_CONV),
}

for label, (src, want_rule) in _BYPASS_CASES.items():
    _bhits = {v.rule for v in cm.scan_source("scripts/__conv_probe__.py", src.encode("utf-8"))}
    check(want_rule in _bhits,
          f"等价绕过形态必被规则 7 抓到（{label}；实得 {sorted(_bhits) or '无'})")
# 反向钉死：普通正则与「路径使用」不归规则 7 管（比规则 4 窄的边界）。
_neg = cm.scan_source("scripts/__conv_probe__.py",
                      ("import re" + chr(10) + "re.compile(" + chr(34) + chr(92) + "d+" + chr(34) + ")"
                       + chr(10) + "p.startswith(" + chr(34) + "/mnt/" + chr(34) + ")" + chr(10)).encode("utf-8"))
check(cm.RULE_CONV not in {v.rule for v in _neg},
      f"普通正则/路径使用不误报规则 7（实得 {sorted({v.rule for v in _neg}) or '无'})")


# ── 8. 调用点只剩一行委托（AC：八个调用方，#171 修正旧文案的 4/6 漏算）──
section("8. 调用点已收口为一行委托")
# 查表即断言：每个旧名对应它该委托到 hostpath 的哪个函数。
DELEGATES = {
    "dsh_judge.py": {"_in_wsl": "is_wsl", "to_win_path": "to_win_path",
                     "to_host_path": "to_host_path"},
    "vision_common.py": {"_in_wsl": "is_wsl"},
    "vision_server.py": {"_is_wsl": "is_wsl", "_wsl_path": "to_host_path",
                         "_decode_out": "decode_output"},
    "strata_h3_lane.py": {"_in_wsl": "is_wsl"},
}


def _delegation(src_text):
    """{旧函数名: 它委托到的 hostpath 函数名}，只认真·一行委托形态。

    判据（AST，不是子串）：函数体**有且只有**一条 return，return 的值是
    hostpath.<name>(...)。多挂一个 or、加一层 try、混进第二句逻辑，都会让
    这条断言红——子串检查抓不到这些。
    """
    found = {}
    for node in ast.walk(ast.parse(src_text)):
        if not isinstance(node, ast.FunctionDef):
            continue
        # docstring 不算逻辑（保留原文档），除此之外函数体必须只有那一条 return。
        body = [s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                        and isinstance(s.value.value, str))]
        if len(body) != 1:
            continue
        stmt = body[0]
        if not isinstance(stmt, ast.Return) or not isinstance(stmt.value, ast.Call):
            continue
        func = stmt.value.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "hostpath"):
            found[node.name] = func.attr
    return found


# #171：数字口径从表算出（DELEGATES 展开后的调用点总数），不写死——
# 旧文案「四个/六个」就是这样漂掉的。
TOTAL_CALLS = sum(len(v) for v in DELEGATES.values())
check(TOTAL_CALLS == 8,
      f"委托表覆盖 8 个调用点（#143 票面 6 是漏算解码与反向转换；实得 {TOTAL_CALLS}）")

for name, want in DELEGATES.items():
    with open(os.path.join(HERE, name), encoding="utf-8", errors="replace") as f:
        src = f.read()
    got = _delegation(src)
    for old, target in want.items():
        check(got.get(old) == target,
              f"{name}::{old} 是「一行 return hostpath.{target}(…)」（实得 {got.get(old)!r}）")
    check("hostpath" in src, f"{name} 确实 import 了 hostpath")
with open(os.path.join(HERE, "vision_server.py"),
          encoding="utf-8", errors="replace") as _vs:
    check(MNT_CAT not in _vs.read(), "vision_server 不再自己拼 /mnt 前缀")
# 反向探针：判据要能把「挂了 or 的假委托」判成不合格，否则上面全是假绿。
check(_delegation("def f(p):" + chr(10) + "    return hostpath.to_win_path(p) or p" + chr(10)) == {},
      "多挂 or 的形态不算一行委托（判据能抓到加壳）")
check(_delegation("def f(p):" + chr(10) + "    v = hostpath.to_win_path(p)" + chr(10)
                  + "    return v" + chr(10)) == {},
      "先赋值再 return 不算一行委托")
check(_delegation("def f(p):" + chr(10) + "    return hostpath.to_win_path(p)" + chr(10))
      == {"f": "to_win_path"}, "真·一行委托能判过（判据没写反）")


# ── 9. #169 收口：三处宿主判定同源 ────────────────────────────────────
section("9. #169 三处宿主判定收口")

# 9a. 两个 host_label 保留原名、函数体只剩一行委托（#143 同款姿势）。
DELEGATES_169 = {
    "run_tests.py": {"host_label": "detect_host"},
    "driver_status.py": {"host_label": "detect_host"},
}
for _name169, _want169 in DELEGATES_169.items():
    with open(os.path.join(HERE, _name169), encoding="utf-8", errors="replace") as _f:
        _src169 = _f.read()
    _got169 = _delegation(_src169)
    for _old, _target in _want169.items():
        check(_got169.get(_old) == _target,
              f"{_name169}::{_old} 是「一行 return hostpath.{_target}(…)」（实得 {_got169.get(_old)!r}）")
    check("hostpath" in _src169, f"{_name169} 确实 import 了 hostpath")

# 9b. rotation_groundtruth 的 DATE_ROOT 同源：Windows 口径常量只写一处，落地
#     口径交给 hostpath.to_host_path（AST 断言；注释里怎么提旧实现都不算数）。
with open(os.path.join(HERE, "rotation_groundtruth.py"),
          encoding="utf-8", errors="replace") as _f:
    _rg_src = _f.read()
_RG_WIN = _RG_HOST = False
for _n in ast.walk(ast.parse(_rg_src)):
    if not isinstance(_n, ast.Assign):
        continue
    _names = {t.id for t in _n.targets if isinstance(t, ast.Name)}
    if "DATE_ROOT_WIN" in _names and isinstance(_n.value, ast.Constant) \
            and isinstance(_n.value.value, str):
        _RG_WIN = True
    if "DATE_ROOT" in _names and isinstance(_n.value, ast.Call) \
            and isinstance(_n.value.func, ast.Attribute) \
            and isinstance(_n.value.func.value, ast.Name) \
            and _n.value.func.value.id == "hostpath" \
            and _n.value.func.attr == "to_host_path":
        _RG_HOST = True
check(_RG_WIN, "DATE_ROOT_WIN 是模块级字符串常量（Windows 口径唯一书写点）")
check(_RG_HOST, "DATE_ROOT = hostpath.to_host_path(…)（与 vision 两脚本同源，不再本地判宿主）")
check("hostpath" in _rg_src, "rotation_groundtruth 确实 import 了 hostpath")

# 9c. 裸 Linux 回归点：三条证据全假 → 三处判定一律「非 WSL」。
#     DATE_ROOT 在 import 时求值，必须在子进程里先补丁再导入才能同时盖住三处；
#     本探针在 #169 之前的代码上会真的红（WSL 上：run_tests 给旧词、
#     driver_status 判 wsl、DATE_ROOT 判到挂载点——三条各红各的）。
_PROBE_169 = "\n".join((
    "import hostpath as hp",
    "hp._proc_version_says_wsl = lambda: False",
    "hp._release_says_wsl = lambda: False",
    "hp._windows_mount_present = lambda: False",
    "assert hp.is_wsl() is False, '补丁未生效：三条证据全假仍判 WSL'",
    "import run_tests, driver_status",
    "assert run_tests.host_label() == 'windows', 'run_tests 宿主判定未收口'",
    "assert driver_status.host_label() == 'windows', 'driver_status 宿主判定未收口'",
    "import rotation_groundtruth as rg",
    "assert rg.DATE_ROOT == rg.DATE_ROOT_WIN, 'DATE_ROOT 未走 hostpath 同源换算'",
    "print('OK-169')",
))
_probe = subprocess.run([sys.executable, "-c", _PROBE_169], cwd=HERE,
                        capture_output=True, timeout=180)
check(_probe.returncode == 0 and "OK-169" in hp.decode_output(_probe.stdout),
      f"三证据全假 → 三处判定都给「非 WSL」（rc={_probe.returncode}）"
      f" {hp.decode_output(_probe.stderr.strip())[-300:]}")

# 9d. 契约读不到（AC2 的退化断言）：判定 id 仍落在退化词表内，不存在第三态。
_real_hosts9 = hc.hosts
try:
    hc.hosts = lambda: (_ for _ in ()).throw(RuntimeError("模拟契约读不到"))
    check(hp.detect_host() in hp._FALLBACK_HOSTS,
          f"契约读不到时 detect_host 仍在退化词表内: {hp.detect_host()}")
finally:
    hc.hosts = _real_hosts9
check(hc.hosts is _real_hosts9, "契约解析器已还原（不污染后续断言）")

# 9e. 全仓反查（AC5）：宿主判定三记号不再散落在 hostpath 之外。注释/docstring
#     不算（踩坑记录允许提，与第 1 节同款口径，按 AST 只看代码节点）；
#     vision_common.py 的白名单理由：wsl_gateway() 在读默认网关地址，不是判宿主。
ALLOW_169 = {"scripts/hostpath.py", "scripts/test_hostpath.py",
             "scripts/vision_common.py"}
_route_hits, _release_hits, _sw_hits = [], [], []
for _p in _tracked_py():
    _rel = os.path.relpath(_p, ROOT).replace(os.sep, "/")
    if _rel in ALLOW_169:
        continue
    with open(_p, "rb") as _f:
        _bytes = _f.read()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        _t = ast.parse(_bytes.decode("utf-8", "replace"), filename=_rel)
    for _n in ast.walk(_t):
        if isinstance(_n, ast.Constant) and isinstance(_n.value, str) \
                and "/proc/net/route" in _n.value:
            _route_hits.append(_rel)
        elif isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute):
            _fn = _n.func
            if _fn.attr == "release" and isinstance(_fn.value, ast.Name) \
                    and _fn.value.id == "platform":
                _release_hits.append(_rel)
            if _fn.attr == "startswith" and isinstance(_fn.value, ast.Attribute) \
                    and _fn.value.attr == "platform" \
                    and isinstance(_fn.value.value, ast.Name) \
                    and _fn.value.value.id == "sys":
                _sw_hits.append(_rel)
check(not _route_hits,
      f"/proc/net/route 只剩 hostpath（判定）与 vision_common（读网关）: {sorted(set(_route_hits))}")
check(not _release_hits,
      f"platform.release() 判宿主只剩 hostpath 内部: {sorted(set(_release_hits))}")
check(not _sw_hits,
      f"sys.platform.startswith(...) 判宿主已清零: {sorted(set(_sw_hits))}")
with open(os.path.join(HERE, "vision_common.py"), encoding="utf-8",
          errors="replace") as _f:
    check("/proc/net/route" in _f.read(),
          "vision_common 的网关读取还在（白名单理由：读网关不是判宿主，本票不收）")

# ── 10. vision_common 包式导入可用（AC③，#171 回归防线）────────────────
section("10. vision_common 包式导入")
# #143 曾让 vision_common 裸 import hostpath 且该文件没有 sys.path 注入，
# 仓库根下 python -c "import scripts.vision_common" 直接炸
# ModuleNotFoundError。本节把「包式导入可用」钉成断言，防止再退化。
_import = subprocess.run(
    [sys.executable, "-c", "import scripts.vision_common"],
    cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
check(_import.returncode == 0,
      f"仓库根 python -c 'import scripts.vision_common' 成功（rc={_import.returncode}）"
      + ("" if _import.returncode == 0 else f"，stderr 尾部: {_import.stderr.strip()[-300:]}"))



print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
