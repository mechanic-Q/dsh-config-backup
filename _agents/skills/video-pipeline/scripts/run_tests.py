#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_tests.py — 一条命令跑完 scripts/test_*.py（#140）。

仓库测试是裸脚本（直接 assert，无 pytest/unittest），此前只能手工逐个
`python scripts/test_*.py`，Windows 与 WSL 双端结果靠人肉对表。本跑器把
发现 / 执行 / 计时 / 汇总 / 退出码收成一条命令：

  python scripts/run_tests.py                  # 全跑（发现 scripts/test_*.py，含 #142 的
                                               # 机械检查门禁 test_mechanical.py）
  python scripts/run_tests.py t15_replay       # 只跑名字里含 t15_replay 的
  python scripts/run_tests.py -k vision        # 关键字再过滤
  python scripts/run_tests.py --list           # 只列会被执行的文件
  python scripts/run_tests.py --timeout 600    # 单文件超时（秒，默认 300）

退出码 = 失败文件数（0 全绿；用法错误 2）。
首行打印 宿主 / 解释器绝对路径 / Python 版本——双端对表先看这一行。
子进程一律注入 PYTHONIOENCODING=utf-8 并显式 encoding='utf-8' 解码：否则
Windows cp936 控制台下带 emoji 的断言直接 UnicodeEncodeError 崩（PR #124 铁律）。

机械检查（#142）不是另起一套执行逻辑：它就是 scripts/test_mechanical.py，被本跑器
按普通测试发现并执行；它的最后一节拿真实仓库跑 check_mechanical.py，断言 baseline
与现状一致（新增违规 0 处）。CI 调本跑器时自动带上，无需第二条命令。

无静默 skip：known-red 名单内失败打 SKIP(known-red) 并给出原因、不计失败；
名单内转绿打 XPASS 提醒移出；给定选择器/-k 时未选中的文件逐个打印跳过原因
（一个都没选中则报错退 2，不静默）。
"""
import argparse
import os
import platform
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 仓库根
sys.path.insert(0, HERE)              # 让 hostpath 从本目录导入（#169 宿主判定收口）

import hostpath

DEFAULT_TIMEOUT = 300.0
STATUS_W = 16

# known-red：已确认暂红、不阻塞本条的测试文件（文件名 → 原因），本轮为空。
# 名单内失败降级 SKIP 且不计数；转绿打 XPASS 提醒移出。除本名单外，跑器没有
# 任何跳过路径。
KNOWN_RED = {}


def host_label():
    """宿主 id：一行委托 hostpath.detect_host（#169 收口）。

    id 落在 host_contract.hosts() 词表内（wsl / windows），跑器首行的双端
    对表从此只有一套词汇；不再自造 platform.system() 第三态。
    """
    return hostpath.detect_host()


def header_line():
    """宿主、解释器绝对路径、Python 版本一行打全（#140 AC2，双端对表先看它）。"""
    exe = sys.executable or ""
    if exe:
        exe = os.path.abspath(exe)
    return "宿主=%s 解释器=%s 版本=%s" % (host_label(), exe, platform.python_version())


def _utf8_safe_streams():
    """跑器自身输出防崩：cp936 控制台下 '✅' 等字符会 UnicodeEncodeError。

    子进程已注入 PYTHONIOENCODING=utf-8，但跑器自己的 stdout 仍按宿主控制台
    编码（Windows 常为 gbk/cp936）输出；给当前流补 errors='replace'，坏字符
    降级成 '?' 而不是让整条命令以退出码 1 崩掉（#140 AC3）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def discover(root):
    """root 下的 test_*.py（按名排序）。"""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return sorted(n for n in names
                  if n.startswith("test_") and n.endswith(".py")
                  and os.path.isfile(os.path.join(root, n)))


def _match(name, selector):
    sel = os.path.basename(selector or "")
    if sel.endswith(".py"):
        sel = sel[:-3]
    return sel in name


def select(names, selectors, keyword):
    """按选择器/-k 过滤；返回 (选中, 跳过[(文件, 原因)], 未匹配的选择器)。"""
    unknown = [s for s in selectors if s and not any(_match(n, s) for n in names)]
    chosen, skipped = [], []
    for n in names:
        if selectors and not any(_match(n, s) for s in selectors):
            skipped.append((n, "未匹配选择器 %s" % " ".join(selectors)))
            continue
        if keyword and keyword not in n:
            skipped.append((n, "未匹配 -k %s" % keyword))
            continue
        chosen.append(n)
    return chosen, skipped, unknown


def run_one(path, cwd, timeout):
    """跑单个测试文件；成功/失败/超时都返回结果字典（不抛）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"      # #140 AC3：不注入则 cp936 下 emoji 断言崩
    env["ROTATION_DATASET_READONLY"] = "1"  # #148 AC5：测试子进程一律不许写生产数据集
    started = time.time()
    try:
        p = subprocess.run(
            [sys.executable, path], cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", timeout=timeout)
        return {"ok": p.returncode == 0, "code": p.returncode,
                "out": p.stdout or "", "secs": time.time() - started,
                "timeout": False}
    except subprocess.TimeoutExpired as e:
        out = e.output or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return {"ok": False, "code": None, "out": out,
                "secs": time.time() - started, "timeout": True}


def _clip(text, limit=400):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


_FILE_RE = re.compile(r'File "([^"]+)", line (\d+)')
_ASSERT_RE = re.compile(r"^AssertionError(?:\s*:\s*(.*))?$")


def location(out):
    """最后一个 File/line 帧（断言失败点）；没有返回 None。"""
    hits = _FILE_RE.findall(out or "")
    if not hits:
        return None
    fp, line = hits[-1]
    return fp, int(line)


def source_line(fp, line):
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()
    except OSError:
        pass
    return None


def failure_reason(out, loc=None):
    """断言文本优先；裸 assert 回读失败行的源码；其他异常取异常行。"""
    lines = (out or "").splitlines()
    msg = None
    for ln in lines:
        m = _ASSERT_RE.match(ln.strip())
        if m:
            msg = (m.group(1) or "").strip()
    if msg:
        return "断言", _clip(msg)
    loc = loc or location(out)
    if loc:
        src = source_line(*loc)
        if src:
            return "断言", _clip(src)
    tail = [ln.strip() for ln in lines if ln.strip()]
    return "异常", _clip(tail[-1] if tail else "(无输出)")


def print_detail(name, res, timeout):
    print("  %s（%.1fs）" % (name, res["secs"]), flush=True)
    if res["timeout"]:
        print("    超时: 超过 %.1fs 未结束" % timeout, flush=True)
        return
    print("    退出码: %s" % res["code"], flush=True)
    loc = location(res["out"])
    kind, text = failure_reason(res["out"], loc)
    print("    %s: %s" % (kind, text), flush=True)
    if loc:
        print("    位置: %s:%d" % loc, flush=True)
    tail = [ln for ln in (res["out"] or "").rstrip().splitlines()[-12:]]
    if tail:
        print("    输出尾部:", flush=True)
        for ln in tail:
            print("      " + ln, flush=True)


def _row(status, name, width, secs, note=""):
    line = "  %-*s %-*s %6.1fs" % (STATUS_W, status, width, name, secs)
    if note:
        line += " —— " + note
    print(line, flush=True)


def main(argv=None, root=None):
    """跑测试；返回退出码（= 失败文件数）。root 供自测指向 fixture 目录。"""
    _utf8_safe_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.path.abspath(root or HERE)
    parser = argparse.ArgumentParser(
        prog="run_tests.py",
        description="一条命令跑完 scripts/test_*.py（退出码=失败文件数）")
    parser.add_argument("selectors", nargs="*",
                        help="文件名/主干/路径子串；给定时只跑匹配项（其余打印跳过原因）")
    parser.add_argument("-k", "--keyword", default="", help="文件名关键字再过滤")
    parser.add_argument("--list", action="store_true", help="只列出将被执行的文件")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="单文件超时秒数（默认 %d）" % DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    print(header_line(), flush=True)
    if args.timeout <= 0:
        print("用法错误: --timeout 必须 > 0", flush=True)
        return 2
    names = discover(root)
    if not names:
        print("没有发现测试文件: %s" % os.path.join(root, "test_*.py"), flush=True)
        return 2

    chosen, skipped, unknown = select(names, args.selectors, args.keyword)
    if unknown:
        for sel in unknown:
            print("未知选择器: %s（%s 下没有匹配的 test_*.py，--list 可看清单）"
                  % (sel, root), flush=True)
        return 2
    print("测试目录: %s（%d 个文件，选中 %d）" % (root, len(names), len(chosen)),
          flush=True)
    if args.list:
        for n in chosen:
            print("  " + n, flush=True)
        return 0
    if not chosen:
        print("没有选中任何测试文件（%d 个候选，--list 可看清单）" % len(names),
              flush=True)
        return 2

    width = max(len(n) for n in names) + 2
    cwd = os.path.dirname(root)
    green = red = kred = xpass = 0
    failures = []
    started = time.time()
    for name in chosen:
        res = run_one(os.path.join(root, name), cwd, args.timeout)
        if name in KNOWN_RED:
            if res["ok"]:
                xpass += 1
                green += 1
                _row("XPASS", name, width, res["secs"],
                     "已转绿，请从 KNOWN_RED 移除")
            else:
                kred += 1
                _row("SKIP(known-red)", name, width, res["secs"], KNOWN_RED[name])
        elif res["ok"]:
            green += 1
            _row("PASS", name, width, res["secs"])
        else:
            red += 1
            _row("FAIL", name, width, res["secs"],
                 "超时" if res["timeout"] else "退出码 %s" % res["code"])
            failures.append((name, res))
    total = time.time() - started

    if skipped:
        print("\n跳过（未选中，%d 个）:" % len(skipped), flush=True)
        for n, reason in skipped:
            print("  %-*s %-*s —— %s" % (STATUS_W, "SKIP", width, n, reason),
                  flush=True)
    if failures:
        print("\n失败明细", flush=True)
    for name, res in failures:
        print_detail(name, res, args.timeout)

    print("\n合计: 绿=%d 红=%d known-red=%d 总耗时=%.1fs"
          % (green, red, kred, total), flush=True)
    if xpass:
        print("注意: %d 个 known-red 文件已转绿（XPASS），请从 KNOWN_RED 移除"
              % xpass, flush=True)
    if red:
        print("失败 %d 个文件（退出码 %d）" % (red, red), flush=True)
    else:
        print("全部通过 ✅", flush=True)
    return red


if __name__ == "__main__":
    sys.exit(main())
