#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_compose_final.py — 装配脚本子进程输出收敛的自检（#183，无框架，assert 直跑）。

背景：2026-10-20 跑 10-19 期 s09 装配时，compose_final.ff() 的末行
`r.stderr[-300:]` 抛 TypeError: 'NoneType' object is not subscriptable，
整期装配被冲掉，而且真正的 ffmpeg 失败行永远看不到（None 上取尾巴先炸）。
同命令手工直跑不复现，触发条件未定位——所以修法不是找回触发条件，而是让
「输出取尾巴」这件事不可能崩：None 转空串、bytes 容错解码，失败只由 rc 表达。

覆盖：
1. _txt 三态收敛（None / bytes / str）
2. _tail 取尾与 None 兜底
3. ff() 在 stdout/stderr 均为 None 时仍返回三元组（复现事故形态）
4. ff() 在 bytes 输出时容错解码
5. 源码静态守卫：除 _txt/_tail 内部外不存在裸 .stdout/.stderr 取用
6. 真子进程冒烟：ff(ffmpeg -version) 拿到 rc 与输出（无 ffmpeg 则 SKIP）
"""
import ast
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import compose_final as cf   # noqa: E402 — 必须在 sys.path.insert 之后

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class _FakeRun:
    """subprocess 替身：run 固定返回给定的 (returncode, stdout, stderr)。"""

    def __init__(self, rc, out, err):
        self._rc, self._out, self._err = rc, out, err

    def run(self, *a, **kw):
        return type("R", (), {"returncode": self._rc, "stdout": self._out,
                              "stderr": self._err})()


class _StubCtx:
    """临时替换 compose_final.subprocess（桩必须打在真实边界上），退出还原。"""

    def __enter__(self):
        self.orig = cf.subprocess
        return self

    def __call__(self, stub):
        cf.subprocess = stub
        return self

    def __exit__(self, *exc):
        cf.subprocess = self.orig
        return False


# ── 1. _txt 三态 ──────────────────────────────────────────────────────
section("_txt 三态")
check(cf._txt(None) == "", "None 收敛成空串")
check(cf._txt("abc") == "abc", "str 原样返回")
check(cf._txt("中文".encode("utf-8")) == "中文", "utf-8 字节解码为文本")
check(cf._txt(b"\xff\xfe\xfd").startswith("\ufffd"), "非法字节降级为替换符而不是抛异常")

# ── 2. _tail 取尾 ─────────────────────────────────────────────────────
section("_tail 取尾")
check(cf._tail(None) == "", "None 收敛成空串（事故点）")
check(cf._tail("abc", 300) == "abc", "短于 n 时原样返回")
check(cf._tail("x" * 500, 300) == "x" * 300, "长于 n 时只取末尾 n 个字符")
check(cf._tail("尾巴" * 200, 4) == "尾巴" * 2, "多字节中文按字符切不炸")

# ── 3. ff() 在 None 输出下不崩（事故形态）───────────────────────────
section("ff() None 输出")
with _StubCtx()(_FakeRun(0, None, None)):
    rc, so, se = cf.ff(["ffmpeg", "-version"], timeout=5)
check(rc == 0, "rc 原样透出")
check(so == "" and se == "", "None 输出收敛成空串（旧代码在此 TypeError）")
with _StubCtx()(_FakeRun(1, "out-tail", None)):
    rc, so, se = cf.ff(["ffmpeg"], timeout=5)
check(rc == 1 and so == "out-tail" and se == "",
      "只有 stderr 为 None 时也不崩，stdout 尾巴保留")

# ── 4. ff() bytes 输出容错解码 ───────────────────────────────────────
section("ff() bytes 输出")
with _StubCtx()(_FakeRun(0, "中文".encode("utf-8"), b"")):
    rc, so, se = cf.ff(["ffmpeg"], timeout=5)
check(rc == 0 and so == "中文" and se == "", "bytes 输出解码为文本")

# ── 5. 源码静态守卫：无裸 .stdout/.stderr ────────────────────────────
section("源码静态守卫")
tree = ast.parse(_read(os.path.join(HERE, "compose_final.py")))
WRAPPERS = {"_txt", "_tail"}
bare = []


class _Visitor(ast.NodeVisitor):
    """记录所有 .stdout/.stderr 取用点，标出是否被 _txt/_tail 包裹。"""

    def __init__(self):
        self.stack = []

    def generic_visit(self, node):
        self.stack.append(node)
        super().generic_visit(node)
        self.stack.pop()

    def visit_Attribute(self, node):
        if node.attr in ("stdout", "stderr"):
            parent = self.stack[-1] if self.stack else None
            wrapped = (isinstance(parent, ast.Call)
                       and isinstance(parent.func, ast.Name)
                       and parent.func.id in WRAPPERS)
            if not wrapped:
                bare.append((node.lineno, node.attr))
        self.generic_visit(node)


_Visitor().visit(tree)
check(not bare, f"compose_final.py 无裸 stdout/stderr 取用（发现 {bare}）")

# ── 6. 真子进程冒烟 ──────────────────────────────────────────────────
if shutil.which("ffmpeg") is None:
    print("\n!! SKIP(no ffmpeg)：第 6 节需要 PATH 里有 ffmpeg，本机没有")
else:
    section("真子进程冒烟")
    rc, so, se = cf.ff(["ffmpeg", "-version"], timeout=30)
    check(rc == 0, "ffmpeg -version 返回 rc=0")
    check(isinstance(so, str) and len(so) > 0, "stdout 是非空文本（不是 None）")
    check(isinstance(se, str), "stderr 是文本（不是 None）")

print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
