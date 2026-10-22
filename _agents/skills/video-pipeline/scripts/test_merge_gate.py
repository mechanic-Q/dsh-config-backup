#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_merge_gate.py — 自动合并门禁自身的护栏（#153，无框架，assert 直跑）。

背景：merge_gate.py 是「绿灯即合」的可执行门禁，它自己拦错（把红放行）或拦死
（把绿拦下）都比没有门禁更坏。本文件在**不碰真实 PR** 的前提下验证三种情形
（AC⑥）：绿（合并路径走到 gh pr merge）、红（本地自检红 → 拦截）、CI 未就绪
（拦截）。gh 与 run_tests.py 全部打桩注入，零出网、零真合并。

覆盖：
1. 三关全绿 → 调到 gh pr merge --squash --delete-branch，随后主干复查绿 → 退出码 0
2. 本地自检红（退出码非 0）→ 拦截，退出码 1，且不调 gh pr merge
3. CI 未就绪（PENDING）且 --skip-wait → 拦截，退出码 1
4. CI 红 conclusion=FAILURE → 拦截
5. 找不到名字含「全部自检」的 check → missing 拦截（job 改名不会误判绿）
6. 主干复查红 → 退出码 1 且打印 revert 命令（不自动 revert）
7. gh pr merge 失败 → 退出码 1
8. 用法错误（PR 编号 <= 0）→ 退出码 2
9. 真脚本静态断言：合并命令带 --squash --delete-branch
10. mergeable() 真解析：gh 新版字符串 MERGEABLE / 旧版布尔 / CONFLICTING
11. (#177) _say：gbk 严格流下「纯中文 / 中文+U+FFFD / 中文+✓」三类消息都不抛，
    且可编码中文原样保留（不得整条洗成替换符）
12. (#177) _run：子进程输出解码后可读、不夹 U+FFFD——Python 子进程（写侧对齐）
    与按 cp936 吐字节的子进程（读侧回退）两条路都钉
13. (#177) P3 回归：gbk 严格流下三关全绿 → 合并成功 → 退出码 0（不被 ✓ 改写）
14. (#177) P1 回归：gbk 严格流下本地自检红（尾部是**真的 _run 产物**）→
    拦截文案与尾部都打印 → 退出码 1
15. (#177) P2 回归：gbk 严格流下主干复查红（同样真尾部）→
    revert 命令与尾部都打印 → 退出码 1

兜底：本文件自己的 check()/section() 打印做了 per-call 降级（#170 同款）——第 11/14
节故意把 U+FFFD 放进消息里，裸跑在 cp936 控制台下若不做这层，测试自己先崩。
"""
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import merge_gate as mg

SECTIONS = []


def _emit(line):
    """cp936 裸跑下 U+FFFD / ✓ 等编不出的字符会掀翻整个文件（#170 同款，#177 重现）。

    本文件故意把 U+FFFD 放进断言消息（钉的就是它），所以打印必须自己能降级。
    **故意不复用 `mg._say`**：它是被测对象，若它坏了、或谁把降级改回 ascii 版，
    让测试的打印路径也一起坏掉，红起来就是「测试崩了」而不是「断言说了什么」。
    测试的观察工具必须独立于被测物。#170 的 `test_hostpath.check` 同款写法。
    """
    try:
        print(line)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(enc, "backslashreplace").decode(enc, "backslashreplace"))


def check(cond, msg):
    assert cond, msg
    _emit(f"  ok: {msg}")


def section(name):
    SECTIONS.append(name)
    _emit(f"\n== {name} ==")


def run_gate(monkey, argv):
    """在打桩环境下跑 mg.main(argv)，抓返回值与 stdout。"""
    saved = {k: getattr(mg, k) for k in monkey}
    for k, v in monkey.items():
        setattr(mg, k, v)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = mg.main(list(argv))
    finally:
        for k, v in saved.items():
            setattr(mg, k, v)
    return code, buf.getvalue()


def local_stub(ok=True, code=0):
    def _local():
        return ok, code, "合计: 绿=38 红=%d" % (0 if ok else 1)
    return _local


GREEN_CHECKS = [{"name": "全部自检（ubuntu-latest / Python 3.12）",
                 "status": "COMPLETED", "conclusion": "SUCCESS"}]


def pr_json(checks=None):
    """伪造 gh pr view 的 JSON 输出。"""
    return json.dumps({
        "headRefOid": "abc123def456",
        "statusCheckRollup": checks or [],
    })


# ── 1. 三关全绿：走到 gh pr merge，主干复查绿，退出码 0 ─────────────────
section("三关全绿 → 合并 + 主干复查绿 → 0")
merge_calls, recheck_calls = [], []
monkey = {
    "ci_state": lambda pr, wait: ("green", "CI 绿（head abc123def456）"),
    "local_tests": local_stub(ok=True),
    "mergeable": lambda pr: (True, "可合并（CLEAN）"),
    "merge": lambda pr: (merge_calls.append(pr), (True, "Squashed and merged"))[1],
    "main_recheck": lambda: (recheck_calls.append(1), 0)[1],
}
code, out = run_gate(monkey, ["42"])
check(code == 0, f"三关全绿退出码 0，实得 {code}")
check(merge_calls == [42], f"恰好调一次 merge 且传 PR 42，实得 {merge_calls}")
check(len(recheck_calls) == 1, "合并后跑了一次主干复查")

# ── 2. 本地自检红 → 拦截，不调 merge ──────────────────────────────────
section("本地自检红 → 拦截退出码 1")
merge_calls = []
code, out = run_gate({
    "ci_state": lambda pr, wait: ("green", "CI 绿"),
    "local_tests": local_stub(ok=False, code=2),
    "mergeable": lambda pr: (True, "可合并"),
    "merge": lambda pr: (merge_calls.append(pr), (True, "should not happen"))[1],
}, ["42"])
check(code == 1, f"本地红退出码 1，实得 {code}")
check(not merge_calls, "本地红时 gh pr merge 未被调用")
check("拦截" in out, "输出说明拦截原因")

# ── 3. CI 未就绪 + --skip-wait → 拦截 ─────────────────────────────────
section("CI 未就绪（PENDING）→ 拦截")
code, out = run_gate({
    "ci_state": lambda pr, wait: ("pending", "CI 未就绪（status=PENDING）"),
    "local_tests": local_stub(ok=True),
    "mergeable": lambda pr: (True, "可合并"),
    "merge": lambda pr: (True, "should not happen"),
}, ["42", "--skip-wait"])
check(code == 1, f"CI 未就绪退出码 1，实得 {code}")
check("拦截" in out, "拦截原因写明 CI 未就绪")

# ── 4. CI 红 → 拦截 ──────────────────────────────────────────────────
section("CI 红（FAILURE）→ 拦截")
code, out = run_gate({
    "ci_state": lambda pr, wait: ("red", "CI 红（conclusion=FAILURE，head abc123def456）"),
    "local_tests": local_stub(ok=True),
    "mergeable": lambda pr: (True, "可合并"),
    "merge": lambda pr: (True, "should not happen"),
}, ["42", "--skip-wait"])
check(code == 1, f"CI 红退出码 1，实得 {code}")

# ── 5. ci_state 真函数：check 改名 → missing，不误判绿 ────────────────
section("check 名对不上 → missing（job 改名防误判）")
saved_run = mg._run


def fake_run_json(payload, rc=0):
    def _run(cmd, cwd=None, timeout=None):
        class P:
            returncode = rc
            stdout = payload if cmd[:3] == ["gh", "pr", "view"] else ""
            stderr = ""
        return P()
    return _run


mg._run = fake_run_json(pr_json(checks=[{"name": "renamed job", "status": "COMPLETED",
                                         "conclusion": "SUCCESS"}]))
try:
    verdict, why = mg.ci_state(42, wait=False)
finally:
    mg._run = saved_run
check(verdict == "missing", f"陌生 check 名判 missing，实得 {verdict}")

mg._run = fake_run_json(pr_json(checks=GREEN_CHECKS))
try:
    verdict, why = mg.ci_state(42, wait=False)
finally:
    mg._run = saved_run
check(verdict == "green", f"真 check 名判 green，实得 {verdict}")
check("abc123def456" in why, "绿判定说明里带 head 短哈希")

mg._run = fake_run_json(pr_json(checks=[{"name": "全部自检（ubuntu-latest / Python 3.12）",
                                         "status": "IN_PROGRESS", "conclusion": None}]))
try:
    verdict, why = mg.ci_state(42, wait=False)
finally:
    mg._run = saved_run
check(verdict == "pending", f"跑动中的 check 判 pending，实得 {verdict}")

# ── 6. 主干复查红 → 打 revert 命令、不自动 revert、退出码 1 ───────────
section("主干复查红 → 打印 revert 命令")
saved_run = mg._run
saved_local = mg.local_tests


def fake_run_revert(cmd, cwd=None, timeout=None):
    class P:
        returncode = 0
        stdout = "f1e2d3c squash commit（主干上最新一条）" if cmd[:2] == ["git", "log"] else ""
        stderr = ""
    return P()


mg.local_tests = local_stub(ok=False, code=1)
mg._run = fake_run_revert
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        rc = mg.main_recheck()
finally:
    mg.local_tests = saved_local
    mg._run = saved_run
out = buf.getvalue()
check(rc == 1, f"主干复查红退出码 1，实得 {rc}")
check("git revert --no-edit f1e2d3c" in out, "打印了带短哈希的 revert 命令")
check("不自动 revert" in out, "明确说明不自动 revert")

# ── 7. gh pr merge 失败 → 退出码 1 ────────────────────────────────────
section("gh pr merge 失败 → 1")
code, out = run_gate({
    "ci_state": lambda pr, wait: ("green", "CI 绿"),
    "local_tests": local_stub(ok=True),
    "mergeable": lambda pr: (True, "可合并"),
    "merge": lambda pr: (False, "gh: Pull request is in clean status"),
}, ["42"])
check(code == 1, f"合并失败退出码 1，实得 {code}")

# ── 8. 用法错误 → 2（argparse error 以 SystemExit(2) 终止）─────────────
section("用法错误 → 2")


def run_gate_exit(monkey, argv):
    """同 run_gate，但抓 SystemExit 作为退出码（argparse error 路径）。"""
    saved = {k: getattr(mg, k) for k in monkey}
    for k, v in monkey.items():
        setattr(mg, k, v)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            mg.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        for k, v in saved.items():
            setattr(mg, k, v)
    return code, buf.getvalue()


code, out = run_gate_exit({}, ["0"])
check(code == 2, f"PR=0 argparse 退出码 2，实得 {code}")
code, out = run_gate_exit({}, ["-1"])
check(code == 2, f"PR=-1 argparse 退出码 2，实得 {code}")

# ── 9. 真脚本静态断言：合并命令带 --squash --delete-branch ─────────────
section("真脚本带 squash + delete-branch（防后改丢关键字）")
src = open(os.path.join(HERE, "merge_gate.py"), encoding="utf-8", errors="replace").read()
check('"--squash"' in src and '"--delete-branch"' in src,
      "merge() 命令含 --squash 与 --delete-branch")

# ── 10. mergeable() 真解析：gh 新版字符串 MERGEABLE / 旧版布尔 / CONFLICTING ──
# 踩坑：gh 2.100 的 mergeable 返回字符串 "MERGEABLE" 而非布尔，旧桩只测布尔
# 导致真跑时 UNKNOWN 误拦。此节直接打 _run（gh 层），堵住桩层级过高的盲区。
section("mergeable 真解析：字符串/布尔/冲突")
def _gh_json(payload):
    def _run(cmd, **kw):
        class P:
            returncode = 0
            stdout = json.dumps(payload)
            stderr = ""
        return P()
    return _run

saved_run = mg._run
try:
    mg._run = _gh_json({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})
    ok, why = mg.mergeable(42)
    check(ok and "CLEAN" in why, f"gh 字符串 MERGEABLE 放行（{why}）")
    mg._run = _gh_json({"mergeable": True, "mergeStateStatus": "CLEAN"})
    ok, why = mg.mergeable(42)
    check(ok, f"旧版布尔 true 放行（{why}）")
    mg._run = _gh_json({"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"})
    ok, why = mg.mergeable(42)
    check(not ok, f"CONFLICTING 拦截（{why}）")
finally:
    mg._run = saved_run

# ── 11. _say：gbk 严格流下三类消息都不崩、纯中文原样（#177） ──────────────
# 用 gbk+strict 的 TextIOWrapper 盖住 sys.stdout 让 print 走真 strict 编码路径，
# 不靠 PYTHONIOENCODING（跑器注入那是「跑器绿」，本票钉的是「cp936 控制台也不崩」）。
section("_say：gbk 严格流下三类消息都不崩、纯中文原样")


def gbk_run(fn):
    """在 gbk+strict 的 stdout 下执行 fn，返回 (gbk 解回的文本, 原始字节)。"""
    real = sys.stdout
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="gbk", errors="strict")
    try:
        sys.stdout = stream
        fn()
    finally:
        try:
            stream.flush()
        finally:
            sys.stdout = real
    raw = buf.getvalue()
    return raw.decode("gbk", errors="replace"), raw


def say_gbk(msg):
    """跑一次 _say(msg)，崩了就当断言失败报出来（别让它冒充测试崩溃）。"""
    try:
        return gbk_run(lambda: mg._say(msg))[0]
    except UnicodeEncodeError as e:
        raise AssertionError("_say 在 gbk 严格流下仍抛 UnicodeEncodeError: %s" % e)


_cn = "拦截：本地自检红（退出码 1），不合。"
out = say_gbk(_cn)
check(_cn in out, "纯中文消息原样输出，实得 %r" % out)
check("\ufffd" not in out and "\\ufffd" not in out, "纯中文消息不得被洗成替换符，实得 %r" % out)

out = say_gbk("拦截：输出尾部:\n合计: 绿=38 红=1 \ufffd\ufffd")
check("拦截：输出尾部" in out and "合计: 绿=38 红=1" in out,
      "含 U+FFFD 的消息里可编码中文原样保留，实得 %r" % out)
check("\\ufffd" in out, "不可编码的 U+FFFD 以 \\ufffd 转义输出，实得 %r" % out)

out = say_gbk("\u2713 Squashed and merged pull request #42 (main)")
check("\\u2713" in out, "勾号 U+2713 以 \\u2713 转义输出，实得 %r" % out)
check("Squashed and merged pull request #42 (main)" in out,
      "同行的 ASCII 段原样保留，实得 %r" % out)

# 幂等 / 不依赖调用前状态：连打两次都不崩（降级分支与正常分支各走一遍）
_two = gbk_run(lambda: (mg._say("第一次：正常中文"), mg._say("第二次：\u2713 混合")))[0]
check("第一次：正常中文" in _two and "\\u2713" in _two, "连续两次 _say 都输出，实得 %r" % _two)

# ── 12. _run：被调子进程的输出经门禁解码后可读（#177 Q1 拍板「一起修」） ──
# 钉的是契约「门禁拿到的文本可读」，不是某个机制：无论写侧被对齐成 utf-8，还是
# 子进程仍按 cp936 写（非 Python 子进程不看 PYTHONIOENCODING），都必须解出中文。
section("_run：子进程输出解码后可读、不夹替换符")
_p = mg._run([sys.executable, "-c", "print('合计: 绿=3 红=0')"])
check("合计: 绿=3 红=0" in (_p.stdout or ""),
      "Python 子进程的中文输出可读，实得 %r" % (_p.stdout,))
check("\ufffd" not in (_p.stdout or ""), "解码结果不得夹 U+FFFD，实得 %r" % (_p.stdout,))

# 写侧不归门禁管的子进程（gh/git 或未来别的工具）仍可能按 cp936 吐字节：
# 门禁必须按「utf-8 → 本地代码页 → 替换」容错解码，不能整片糊成替换符。
_cp936_bytes = "合计: 绿=3 红=0\n".encode("gbk")
# 用真子进程吐 gbk 字节（不靠桩）：gh/git 这类外部工具不看 PYTHONIOENCODING，
# 仍按控制台代码页写——读侧必须能回退解出来，否则拦截文案里的尾部还是糊的。
_p = mg._run([sys.executable, "-c",
              "import sys; sys.stdout.buffer.write(%r); sys.stdout.flush()"
              % _cp936_bytes])
check("合计: 绿=3 红=0" in (_p.stdout or ""),
      "子进程按 cp936 写的中文也能解出（utf-8 解不了要回退本地代码页），实得 %r" % (_p.stdout,))
check("\ufffd" not in (_p.stdout or ""), "cp936 回退解码不得夹 U+FFFD，实得 %r" % (_p.stdout,))

# 反向钉：门禁不得把「解不出的字节」整片冒充成可读中文（容错解码仍有底线）
_p = mg._run([sys.executable, "-c",
              "import sys; sys.stdout.buffer.write(b'\\xff\\xfeok'); sys.stdout.flush()"])
check("ok" in (_p.stdout or "") and "\ufffd" in (_p.stdout or ""),
      "解不出的字节降级为替换符（且 ASCII 段保留），实得 %r" % (_p.stdout,))

# ── 写侧注入：两条独立事实（#177 评审打回后修正过口径） ──────────────────
# 上面两例只证明「读侧能解 cp936 字节」——去掉注入它们照样绿，所以什么都证明不了。
# 写侧要钉的是**子进程自己怎么输出**，分两种：
#
# ① 不带 reconfigure 的 Python 子进程：打印 GBK 编不出的字符会**自己崩**
#    （cp936 管道下 print 抛 UnicodeEncodeError，rc=1）。注入后 rc=0。
_p = mg._run([sys.executable, "-c", "print('全部通过 \u2705')"])
check(_p.returncode == 0,
      "普通 Python 子进程打印 GBK 编不出的字符不崩（无注入时它自己抛 "
      "UnicodeEncodeError、rc=1；注入了才按 utf-8 写），实得 rc=%r stderr=%r"
      % (_p.returncode, _p.stderr))
check("全部通过 \u2705" in (_p.stdout or ""),
      "该字符按 utf-8 写回且解码正确，实得 %r" % (_p.stdout,))

# ② 像 run_tests.py 那样 reconfigure(errors="replace") 的子进程：**不崩**，
#    但会把 ✅ 变成 "?"。一旦变成 "?"，读侧无论怎么容错都变不回来——这正是
#    票面禁止的「用 ? 丢信息」。所以这里必须看到 ✅ 而不是 ?，只有注入能过。
_p = mg._run([sys.executable, "-c",
              "import sys\nsys.stdout.reconfigure(errors='replace')\n"
              "print('全部通过 \u2705')"])
check(_p.returncode == 0, "reconfigure(errors=replace) 的子进程 rc=0，实得 %r" % _p.returncode)
check("\u2705" in (_p.stdout or ""),
      "reconfigure 过的子进程也必须保住 ✅（无注入时它输出 '?'，读侧救不回来），"
      "实得 %r" % (_p.stdout,))
check("?" not in (_p.stdout or ""),
      "不得出现 '?'（那就是信息已经丢了），实得 %r" % (_p.stdout,))


def _gbk_call(fn, monkey=None):
    """在打桩 + gbk 严格流 stdout 下执行 fn()，返回 (返回值, gbk 解回的文本)。"""
    monkey = monkey or {}
    saved = {k: getattr(mg, k) for k in monkey}
    for k, v in monkey.items():
        setattr(mg, k, v)
    real = sys.stdout
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="gbk", errors="strict")
    try:
        sys.stdout = stream
        try:
            try:
                code = fn()
            except UnicodeEncodeError as e:
                raise AssertionError("gbk 严格流下门禁自身崩在编码上: %s" % e)
        finally:
            stream.flush()
    finally:
        sys.stdout = real
        for k, v in saved.items():
            setattr(mg, k, v)
    return code, buf.getvalue().decode("gbk", errors="replace")


# ── 13. P3 回归：合并成功行带勾号，gbk 严格流下退出码 0（#177） ─────────
section("P3：gbk 严格流下三关全绿 → 合并成功 → 退出码 0")
# 注意：这里**不能**复用 run_gate —— 它把 stdout 重定向到 StringIO，任何字符都编得
# 进去，崩溃路径根本不会走到。必须让 _say 打到真的 gbk 严格流上（诚实回归）。
REAL_GH_OK = "\u2713 Squashed and merged pull request #42 (main)"


def run_gate_gbk(monkey, argv):
    """在打桩 + gbk 严格流 stdout 下跑 gate，返回 (code, gbk 解回的文本)。"""
    return _gbk_call(lambda: mg.main(list(argv)), monkey)


_p3_code, _out13 = run_gate_gbk({
    "ci_state": lambda pr, wait: ("green", "CI 绿（head abc123def456）"),
    "local_tests": local_stub(ok=True),
    "mergeable": lambda pr: (True, "可合并（CLEAN）"),
    "merge": lambda pr: (True, REAL_GH_OK),
    "main_recheck": lambda: 0,
}, ["42"])
check(_p3_code == 0, "合并成功路径退出码应为 0（不得被 ✓ 编不出的编码异常改写成 1），实得 %s"
      % _p3_code)
check("Squashed and merged pull request #42" in _out13, "合并结果在输出里可见，实得 %r" % _out13)
check("\\u2713" in _out13, "勾号以转义形式出现而不是让门禁崩，实得 %r" % _out13)

# ── 14. P1 回归：本地自检红（尾部是真的 _run 产物），gbk 严格流下退出码 1 ──
section("P1：gbk 严格流下本地自检红 → 拦截文案与尾部都打印 → 退出码 1")
# AC 原话是「尾部按 _run 真实产物构造」。这里起一个真子进程、故意让它在
# reconfigure(errors="replace") 后吐出定级信息，再用**真的 local_tests 那三步**
# （_run → 取末 3 行 → _say）走完整链，而不是手写字面量：
#   _ERROR_TAIL 就是这条链真实产出的尾部，含它就该含的东西（中文、无替换符糊）。

def _real_red_tail():
    """用真 _run 跑一个"红"的子进程，返回 (ok, code, tail) —— 与 local_tests 同形状。"""
    p = mg._run([sys.executable, "-c",
                 "import sys\n"
                 "sys.stdout.reconfigure(errors='replace')\n"
                 "print('合计: 绿=38 红=1')\n"
                 "print('全部通过 \u2705')\n"
                 "raise SystemExit(1)\n"])
    tail = (p.stdout or "").strip().splitlines()[-3:]
    return p.returncode == 0, p.returncode, "\n".join(tail)


_ok14, _code14, _ERROR_TAIL = _real_red_tail()
check(_ok14 is False and _code14 == 1, "构造出真的红子进程（rc=1），实得 %r/%r" % (_ok14, _code14))
check("\u2705" in _ERROR_TAIL, "红子进程的尾部经由 _run 解码后是可读的（修复的实证），实得 %r" % _ERROR_TAIL)


def _local_p1():
    return False, 2, _ERROR_TAIL


_p1_code, _out14 = run_gate_gbk({
    "ci_state": lambda pr, wait: ("green", "CI 绿"),
    "local_tests": _local_p1,
    "mergeable": lambda pr: (True, "可合并"),
    "merge": lambda pr: (True, "should not happen"),
}, ["42"])
check(_p1_code == 1, "本地自检红退出码应为 1，实得 %s" % _p1_code)
check("拦截：本地自检红" in _out14, "拦截原因整行可读，实得 %r" % _out14)
check("输出尾部" in _out14, "输出尾部行可见，实得 %r" % _out14)
check("\\u2705" in _out14,
      "尾部里 GBK 编不出的 ✅ 以 \\u2705 转义打印（不是丢行、也不是糊成 ?/替换符），"
      "实得 %r" % _out14)

# ── 15. P2 回归：主干复查红（尾部是真的 _run 产物），gbk 严格流下退出码 1 ──
section("P2：gbk 严格流下主干复查红 → revert 命令与尾部都打印 → 退出码 1")


def _fake_run_p2(cmd, cwd=None, timeout=None):
    class P:
        returncode = 0
        stdout = "f1e2d3c squash commit（主干上最新一条）" if cmd[:2] == ["git", "log"] else ""
        stderr = ""
    return P()


_p2_code, _out15 = _gbk_call(lambda: mg.main_recheck(),
                              {"local_tests": _local_p1, "_run": _fake_run_p2})
check(_p2_code == 1, "主干复查红退出码应为 1，实得 %s" % _p2_code)
check("git revert --no-edit f1e2d3c" in _out15, "revert 命令可读，实得 %r" % _out15)
check("复查输出尾部" in _out15, "复查尾部行可见，实得 %r" % _out15)
check("\\u2705" in _out15, "复查尾部里 GBK 编不出的 ✅ 以转义形式出现，实得 %r" % _out15)

print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
