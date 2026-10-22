#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_run_tests.py — 测试跑器自身的护栏（#140）。

不碰真实 scripts/test_*.py：在临时目录造 fixture 测试文件，直接调
run_tests.main(argv, root=fixture)，断言 #140 的可离线部分：
  1) 退出码 == 失败文件数；用法错误 2
  2) 首行打印 宿主 / 解释器绝对路径 / Python 版本
  3) 子进程注入 PYTHONIOENCODING=utf-8（fixture 用 emoji + 中文断言）
  4) 失败明细点名文件与断言文本（故意改坏：断言文本）
  5) 只跑单个文件；未选中的文件必须打印跳过原因（无静默 skip）
  6) known-red 名单：失败降 SKIP 且打原因、不计数；转绿打 XPASS
  7) 超时记 FAIL 并带原因
  8) 子进程注入 ROTATION_DATASET_READONLY=1（#148 AC5：测试不许写生产数据集）
"""
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_tests

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def run(argv, root):
    """跑跑器并抓 stdout 全量输出（用于断言表格与明细）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run_tests.main(list(argv), root=root)
    return code, buf.getvalue()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# fixture 子进程源码：故意让「编码未注入」必然断言失败，且断言文本带中文/emoji
OK = (
    "# -*- coding: utf-8 -*-\n"
    "import os\n"
    "print('子进程中文输出 ✅')\n"
    "enc = os.environ.get('PYTHONIOENCODING')\n"
    "assert enc == 'utf-8', 'PYTHONIOENCODING 未注入: %r' % (enc,)\n"
    "print('fixture ok')\n"
)
BAD = (
    "# -*- coding: utf-8 -*-\n"
    "print('坏 fixture 先打印一行')\n"
    "assert 1 == 2, '故意改坏：断言文本'\n"
)
SLOW = (
    "# -*- coding: utf-8 -*-\n"
    "import time\n"
    "time.sleep(2.0)\n"
    "print('slow fixture 完成')\n"
)
# #148 AC5：跑器必须给每个测试子进程挂上「数据集只读」标记，否则测试能改写生产数据集
READONLY = (
    "# -*- coding: utf-8 -*-\n"
    "import os\n"
    "flag = os.environ.get('ROTATION_DATASET_READONLY')\n"
    "assert flag == '1', 'ROTATION_DATASET_READONLY 未注入: %r' % (flag,)\n"
    "print('readonly fixture ok')\n"
)


def main():
    tmp = tempfile.mkdtemp(prefix="run_tests_fixture_")
    try:
        all_dir = os.path.join(tmp, "all")
        slow_dir = os.path.join(tmp, "slow")
        ro_dir = os.path.join(tmp, "readonly")
        os.makedirs(all_dir)
        os.makedirs(slow_dir)
        os.makedirs(ro_dir)
        write(os.path.join(all_dir, "test_a_ok.py"), OK)
        write(os.path.join(all_dir, "test_b_bad.py"), BAD)
        write(os.path.join(slow_dir, "test_slow.py"), SLOW)
        write(os.path.join(ro_dir, "test_readonly.py"), READONLY)

        section("全量跑：退出码=失败数 + 首行宿主 + 失败明细")
        code, out = run([], all_dir)
        lines = out.splitlines()
        check(code == 1, f"一绿一红 → 退出码 1，实得 {code}")
        check(lines and "宿主=" in lines[0] and "解释器=" in lines[0]
              and "版本=" in lines[0], "首行打印 宿主/解释器/版本")
        exe = lines[0].split("解释器=")[1].split(" 版本=")[0].strip()
        check(os.path.isabs(exe), f"解释器是绝对路径：{exe}")
        check("PASS" in out and "test_a_ok.py" in out, "绿文件 PASS 入表")
        check("FAIL" in out and "test_b_bad.py" in out, "红文件 FAIL 入表")
        check(re.search(r"\d+\.\d+s", out) is not None, "表格带耗时")
        check("故意改坏：断言文本" in out, "失败明细点名断言文本")

        section("单文件过滤：其余文件打印跳过原因")
        code, out = run(["test_a_ok.py"], all_dir)
        check(code == 0, f"只跑绿文件 → 退出码 0，实得 {code}")
        check("PASS" in out and "test_a_ok.py" in out, "选中文件执行")
        check("SKIP" in out and "test_b_bad.py" in out and "未匹配" in out,
              "未选中文件打印跳过原因（无静默 skip）")

        section("--list 列出发现而不执行")
        code, out = run(["--list"], all_dir)
        check(code == 0, f"--list 退出 0，实得 {code}")
        check("test_a_ok.py" in out and "test_b_bad.py" in out, "--list 列出全部测试文件")

        section("未知选择器：用法错误退出 2")
        code, out = run(["no_such_test"], all_dir)
        check(code == 2, f"未知选择器 → 退出码 2，实得 {code}")
        check("no_such_test" in out and "未知" in out, "点名未知选择器")

        section("known-red：失败降 SKIP、打原因、不计数")
        old = run_tests.KNOWN_RED
        run_tests.KNOWN_RED = {"test_b_bad.py": "夹具已知红原因"}
        try:
            code, out = run([], all_dir)
            check(code == 0, f"唯一红在名单内 → 退出码 0，实得 {code}")
            check("SKIP(known-red)" in out and "test_b_bad.py" in out
                  and "夹具已知红原因" in out, "SKIP 行带名单原因")
        finally:
            run_tests.KNOWN_RED = old

        section("known-red 转绿：XPASS 提示移出")
        run_tests.KNOWN_RED = {"test_a_ok.py": "夹具",
                              "test_b_bad.py": "夹具已知红"}
        try:
            code, out = run([], all_dir)
            check(code == 0, f"名单内转绿不影响退出码，实得 {code}")
            check("XPASS" in out and "test_a_ok.py" in out, "转绿打 XPASS 提示")
        finally:
            run_tests.KNOWN_RED = old

        section("超时：记 FAIL 并带原因")
        code, out = run(["--timeout", "1"], slow_dir)
        check(code == 1, f"超时算失败 → 退出码 1，实得 {code}")
        check("test_slow.py" in out and "超时" in out, "超时明细点名文件与原因")

        section("数据集只读标记注入（#148）")
        # 父进程若已带标记，子进程即使不注入也会绿——先清掉再跑，确保断言的是注入行为
        saved = os.environ.pop("ROTATION_DATASET_READONLY", None)
        try:
            code, out = run([], ro_dir)
        finally:
            if saved is not None:
                os.environ["ROTATION_DATASET_READONLY"] = saved
        check(code == 0, f"子进程拿到 ROTATION_DATASET_READONLY=1 → 退出码 0，实得 {code}")
        # 敏感性：不注入该变量时同一 fixture 必须红（否则上面的绿是假的）
        env = {k: v for k, v in os.environ.items()
               if k != "ROTATION_DATASET_READONLY"}
        bare = subprocess.run([sys.executable, os.path.join(ro_dir, "test_readonly.py")],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(bare.returncode != 0, "未注入时 fixture 变红（证明断言真的在测注入）")

        # cp936 控制台下 '✅' 编不出来 → 跑器自身不能以退出码 1 崩掉（#140 AC3）
        section("跑器自身输出：gbk 严格流下全绿不崩")
        buf = io.BytesIO()
        gbk = io.TextIOWrapper(buf, encoding="gbk", errors="strict")
        with contextlib.redirect_stdout(gbk):
            code = run_tests.main(["test_a_ok.py"], root=all_dir)
        gbk.flush()
        check(code == 0, f"gbk 流下全绿退出码 0，实得 {code}")
        check("宿主=" in buf.getvalue().decode("gbk"), "gbk 流下仍打印首行")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")


if __name__ == "__main__":
    main()
