#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_host_guard.py — #145 启动守卫的护栏（host_banner / ensure_host / main 接线）。

无框架，assert 直跑（跑器 run_tests.py，注入 PYTHONIOENCODING=utf-8）。
本机宿主≠声明宿主（windows）时真跑会 exit 2，因此进程级先设
DRIVER_HOST_OVERRIDE，全部经 stub / 子进程验证，不裸跑 main()。

覆盖清单：
  1) host_banner 首行：宿主 / 解释器绝对路径 / 版本 同 run_tests.header_line 口径
  2) ensure_host 三态：匹配→通过行；不匹配→stderr 原因+文档位置、exit 码精确 ==2；
     不匹配+DRIVER_HOST_OVERRIDE→警告文案、不退出
  3) 子进程端到端：--status 先分流不受守卫约束；非 status 且宿主不匹配→exit 2、
     stderr 含文档相对路径（经 hc.DOC_REL 引用，不写字面量）；豁免 env→exit 0
  4) main() 源码接线：守卫块换成 ensure_host() 调用（内联实现已撤除）
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import host_contract as hc
import run_pipeline as rp
import run_tests

SECTIONS = []
OVERRIDE_KEY = "DRIVER_HOST_OVERRIDE"


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def main():
    saved_env = os.environ.get(OVERRIDE_KEY)
    os.environ[OVERRIDE_KEY] = "1"  # 本测试进程多数宿主≠声明宿主，先全局放行，节内再手术
    try:
        run_all()
    finally:
        if saved_env is None:
            os.environ.pop(OVERRIDE_KEY, None)
        else:
            os.environ[OVERRIDE_KEY] = saved_env
    print(f"\n{len(SECTIONS)} 节全部通过")


def run_all():
    section("1 host_banner 首行口径（#145 AC1）")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rp.host_banner()
    line = buf.getvalue().strip()
    m = re.match(r"宿主=(\S+) 解释器=(\S+) 版本=(\S+)$", line)
    check(bool(m), f"横幅格式 = {line!r}")
    check(bool(m) and os.path.isabs(m.group(2)), "解释器为绝对路径")
    check(bool(m) and m.group(3) == sys.version.split()[0], "版本与当前解释器一致")
    import run_tests as _rt
    hdr = _rt.header_line()
    check(hdr.split(" 解释器=")[1] == line.split(" 解释器=")[1],
          "解释器口径与 run_tests.header_line 一致")

    section("2 ensure_host 三态（stub 判定）")
    saved = (rp.hostpath.host_matches_declaration, rp.log)
    try:
        # 2a 匹配 → 打通过行、不退出
        rp.hostpath.host_matches_declaration = lambda detected=None: (True, "stub 一致")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rp.ensure_host()
        out = buf.getvalue()
        check("宿主守卫通过" in out and "stub 一致" in out, "匹配态打通过行")
        # 2b 不匹配 → stderr 原因 + 文档位置，exit 2（无豁免：节内摘掉进程级放行 env）
        detail = "宿主不匹配：当前 stub，声明 windows"
        rp.hostpath.host_matches_declaration = lambda detected=None: (False, detail)
        os.environ.pop(OVERRIDE_KEY, None)
        buf_err = io.StringIO()
        code = None
        try:
            with contextlib.redirect_stderr(buf_err):
                rp.ensure_host()
        except SystemExit as e:
            code = e.code
        err = buf_err.getvalue()
        check(code == 2, f"exit 码精确 ==2（got {code!r}）")
        check(detail in err, "stderr 含 stub 原因")
        check(hc.DOC_REL in err, "stderr 含文档相对路径（hc.DOC_REL）")
        check("DRIVER_HOST_OVERRIDE" in err, "stderr 指明豁免 env")
        # 2c 不匹配 + 豁免 → 警告文案、不退出（env 再设回去）
        os.environ[OVERRIDE_KEY] = "1"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rp.ensure_host()
        out = buf.getvalue()
        check("已豁免宿主守卫" in out and "非权威宿主" in out, "豁免警告文案")
        check(detail in out, "豁免行含原因")
    finally:
        rp.hostpath.host_matches_declaration, rp.log = saved

    section("3 子进程端到端（真 CLI）")
    py = sys.executable
    script = os.path.join(HERE, "run_pipeline.py")
    base = dict(os.environ)
    base["PYTHONIOENCODING"] = "utf-8"
    # 3a --status 先分流：不匹配宿主也不被守卫拦（守卫在分流之后）
    env = dict(base)
    env.pop(OVERRIDE_KEY, None)
    r = subprocess.run([py, script, "--status", "--workdir", tempfile.mkdtemp()],
                       capture_output=True, env=env, encoding="utf-8", errors="replace")
    check(r.returncode == 0, f"--status 不受守卫约束（exit {r.returncode}）")
    check("宿主守卫" not in (r.stdout + r.stderr), "--status 输出无守卫字样")
    # 3b/3c 真不匹配场景：本进程宿主≠契约声明时才可测（多数开发机即如此；
    # 宿主==声明时三态逻辑已由第 2 节 stub 覆盖，真 CLI 态打 SKIP，不静默）。
    mismatched = rp.hostpath.detect_host() != rp.hostpath.declared_host()
    if mismatched:
        # 3b 非 status、无豁免 → exit 2 + 文档位置。--script 必须给：
        # 守卫在「缺 --script」fail-fast 之后，不给会先死在缺参检查（exit 1）。
        env = dict(base)
        env.pop(OVERRIDE_KEY, None)
        r = subprocess.run([py, script, "--script", os.devnull],
                           capture_output=True, env=env,
                           encoding="utf-8", errors="replace", cwd=HERE)
        check(r.returncode == 2, f"守卫拦下：exit 2（got {r.returncode}）")
        check(hc.DOC_REL in (r.stderr or ""), "stderr 含文档相对路径")
        check("Traceback" not in (r.stderr or ""), "失败不抛堆栈")
        # 3c 豁免 env 生效：守卫放行（横幅+豁免警告），再用非法 --stop-after 在
        # preflight 之前 fail-fast——preflight 会真探活/发飞书，测试绝不能碰。
        env2 = dict(base)
        env2[OVERRIDE_KEY] = "1"
        r = subprocess.run([py, script, "--script", os.devnull,
                            "--stop-after", "s99"],
                           capture_output=True, env=env2,
                           encoding="utf-8", errors="replace", cwd=HERE)
        combined = (r.stdout or "") + (r.stderr or "")
        check("已豁免宿主守卫" in combined, "豁免警告在真 CLI 输出中出现")
        check("--stop-after" in combined, "豁免后走到 stop-after 校验（守卫确实放行）")
    else:
        print("  !! SKIP(3b/3c)：当前宿主=契约声明宿主，真 CLI 不匹配态无法在本机构造；"
              "三态逻辑已由第 2 节 stub 覆盖")

    section("4 main() 源码接线")
    src = open(os.path.join(HERE, "run_pipeline.py"), encoding="utf-8",
               errors="replace").read()
    main_body = src[src.index("def main():"):src.index('__name__ == "__main__"')]
    check("ensure_host()" in main_body, "main() 调用 ensure_host()")
    check("host_matches_declaration" not in main_body, "内联判定已撤出 main()")
    check("def host_banner" in src and "def ensure_host" in src, "守卫函数模块级存在")


if __name__ == "__main__":
    main()
