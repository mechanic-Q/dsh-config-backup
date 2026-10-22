#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""merge_gate.py — 自动合并门禁：绿灯即合，主干红了打印 revert 命令（#153）。

合并这一步不是判断点，不该由人把关；真正的三处判断（票做得对不对、代码合不合
规矩、跑不跑得绿）都交给机器。本脚本把「绿灯即合」做成可执行的门禁：

  python scripts/merge_gate.py <PR 编号>          # 三关全过 → squash 合并 + 主干复查
  python scripts/merge_gate.py <PR 编号> --skip-wait
                                                  # 不等远端 CI（CI 未就绪视为拦截，
                                                  # --skip-wait 则只信本地跑器）

三关（缺一不可，全部本地判，私有免费仓没有 required checks 可依赖）：
  1. 远端 CI check 在本 PR 最新 commit 上是绿的（--skip-wait 时跳过等）；
  2. 本地全量自检 python scripts/run_tests.py 全绿（退出码 0 = 失败文件数 0）；
  3. PR 可合并（mergeable，无冲突 / 未被其他门拦）。

两处例外（都发生在 PR 之前或之外，与本门禁无关）：
  - #139 run_pipeline.py 语义冲突解（docs 直推类，不走本门禁）；
  - #147 删本地 worktree（仓库卫生，不进合并链）。

合并方式固定 squash（主干历史一线）；合并后在 main 上**复跑一遍 runner**（本地跑，
已是既有能力），红了不自动 revert——只打印 revert 命令，避免误伤（AC③）。

退出码：0 合并完成且主干复查绿；1 任一关拦截 / 主干复查红（含打印的 revert 命令）；
2 用法错误。所有 gh 调用显式 encoding='utf-8'（PR #124 铁律，cp936 控制台）。
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:            # 直跑时 sys.path[0] 已是本目录；被 import 时补上
    sys.path.insert(0, HERE)

import hostpath  # noqa: E402 — 子进程输出解码的单一事实源（#143），必须在 sys.path 之后

# CI check 名与 tests.yml 的 job name 一致（改 workflow 必须同步这里；
# 匹配方式是子串，job 改名时门禁会报「找不到 check」而不是误判绿）
CI_CHECK_KEY = "全部自检"
WAIT_SECONDS = 900        # 远端 CI 最长等 15 分钟（实测 1m57s，余量充足）
POLL_INTERVAL = 15


def _run(cmd, cwd=ROOT, timeout=None):
    """跑子进程；stdout/stderr 按「utf-8 → gbk → 替换」容错解码（#177）。

    解码口径两段，缺一不可：

    1. **写侧对齐**：给子进程注入 PYTHONIOENCODING=utf-8。Windows 下 Python 子进程
       的 stdout 是管道，默认按 locale（cp936）写，而本函数按 utf-8 解——#177 实测
       run_tests.py 的中文行就是这样整片糊成 U+FFFD，拦截文案里的「输出尾部」
       因此全不可读。与 run_tests.py 给它自己子进程注入同一姿势。
    2. **读侧容错**：注入只管得住 Python 子进程；gh / git 等外部工具照旧按控制台
       代码页吐字节。直接 utf-8/replace 会把它们的中文糊掉，所以按 utf-8 → gbk
       依次尝试，都失败才降级替换——**直接复用 hostpath.decode_output**（#143 定的
       唯一实现），不在这里写第二份解码口径。注意它就是「先 utf-8、再 gbk，都不
       行才 utf-8/replace」三步，不含任何编码回写。

    为什么写侧也要管（实测，别想当然）：`run_tests.py` 收尾打「全部通过 ✅」，
    它自己调了 `reconfigure(errors="replace")`，所以**不注入也不会崩**——只是把
    ✅ 变成 `?`（实测输出 `全部通过 ?`）。而 `? ` 一旦写进字节就再也救不回来：
    读侧容错只能对**已有字节**换编码解释，变不出被替换掉的字符。
    所以两侧不是「二选一」——注入保真（✅ 还是 ✅），`decode_output` 保底
    （cp936 字节能解出中文）。只有读侧的实现在 `run_tests.py` 上会得到
    `全部通过 ?`，正是票面禁止的「用 ? 丢信息」。
    （不带 reconfigure 的 Python 子进程则**会崩**：裸 `print('✅')` 在 cp936 管道
    下 rc=1。第 12 节两条都钉着。）

    返回 CompletedProcess，stdout/stderr 为 str。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=env, timeout=timeout)
    # 读侧容错解码：utf-8 → gbk → 替换（hostpath 是唯一实现，#143）。按字节进、按 str 出，
    # 不传 CompletedProcess —— 那个形态会把 stdout+stderr 拼在一起，这里两路要分开用。
    p.stdout = hostpath.decode_output(p.stdout)
    p.stderr = hostpath.decode_output(p.stderr)
    return p


def _say(msg):
    """唯一 stdout 出口：任何编码下都不抛，可编码字符原样、其余转义（#177）。

    #170 定稿姿势（run_pipeline.log() 同款）：try 正常打，UnicodeEncodeError 就按
    **真实 stdout 编码**做 backslashreplace 重打。

    反面（#177 的坑）：降级链别拿 ascii 当目标编码。旧实现
    `encode("utf-8","replace").decode("ascii","replace")` 把整条消息（连正常中文）
    洗成 U+FFFD，而 U+FFFD 在 GBK 下同样编不出 → 第二次 print 再抛，无人兜底，
    门禁以 traceback 结束（合并已成功也报成退出码 1）。替换符是「洗坏了的证据」，
    不是可以安全输出的字符。
    """
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        # 两侧都用 backslashreplace：能编的原样保住（中文），编不出的变成 \u2713
        # 这类纯 ASCII 文本；解码侧也用它，保证不会中途换成 U+FFFD。
        try:
            print(msg.encode(enc, "backslashreplace").decode(enc, "backslashreplace"),
                  flush=True)
        except LookupError:
            # 极端情况：stdout 报了个本机查不到的编码名（getattr 拿到垃圾值）。
            # 退到 ascii 仍是 **backslashreplace**——只转义、不糊化，不会产出 U+FFFD；
            # 这与票面禁止的「ascii + replace」不是一回事（那个才把整条洗成替换符）。
            print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


# ── 第一关：远端 CI ────────────────────────────────────────────────────
def ci_state(pr, wait):
    """本 PR 最新 commit 上的 CI check 状态。返回 (verdict, 说明)。

    verdict: "green" / "red" / "pending"（等超时）/ "missing"（找不到相关 check）。
    """
    p = _run(["gh", "pr", "view", str(pr), "--json",
              "statusCheckRollup,headRefOid"])
    if p.returncode != 0:
        return "missing", "gh pr view 失败: %s" % (p.stderr or "").strip()
    data = json.loads(p.stdout or "{}")
    head = data.get("headRefOid", "")[:12]
    checks = data.get("statusCheckRollup") or []
    if not checks:
        if wait:
            return "pending", "PR %s（head %s）没有任何 check（CI 可能还没开跑）" % (pr, head)
        return "missing", "PR %s（head %s）没有任何 check" % (pr, head)
    related = [c for c in checks if CI_CHECK_KEY in (c.get("name") or "")]
    if not related:
        return "missing", ("找到 %d 个 check 但没有一个名字含 %r（workflow job 改名了？）: %s"
                           % (len(checks), CI_CHECK_KEY,
                              [c.get("name") for c in checks]))
    c = related[0]
    status, conclusion = c.get("status"), c.get("conclusion")
    if status in ("PENDING", "IN_PROGRESS", "QUEUED", "REQUESTED") or conclusion is None:
        if wait:
            return "pending", "CI 还在跑（status=%s），head %s" % (status, head)
        return "pending", "CI 未就绪（status=%s）" % status
    if conclusion == "SUCCESS":
        return "green", "CI 绿（head %s）" % head
    return "red", "CI 红（conclusion=%s，head %s）" % (conclusion, head)


def wait_ci(pr):
    """等远端 CI 出结论；超时返回最后的 pending/red。"""
    deadline = time.time() + WAIT_SECONDS
    while True:
        verdict, why = ci_state(pr, wait=False)
        if verdict in ("green", "red", "missing"):
            return verdict, why
        if time.time() >= deadline:
            return verdict, "等了 %ds 仍未出结论: %s" % (WAIT_SECONDS, why)
        _say("  … CI 未就绪，%ds 后再问一次（%s）" % (POLL_INTERVAL, why))
        time.sleep(POLL_INTERVAL)


# ── 第二关：本地全量自检 ───────────────────────────────────────────────
def local_tests():
    """本地跑 scripts/run_tests.py 全量；返回 (ok, 退出码, 输出尾部)。"""
    p = _run([sys.executable, os.path.join(HERE, "run_tests.py")], timeout=1800)
    tail = (p.stdout or "").strip().splitlines()[-3:]
    return p.returncode == 0, p.returncode, "\n".join(tail)


# ── 第三关：PR 可合并 ─────────────────────────────────────────────────
def mergeable(pr):
    """GitHub 的 mergeable 判定（冲突 / base 权限等）。返回 (ok, 说明)。"""
    # mergeable 需要 GitHub 算一会儿；首次查询常是 null，轮询几次
    for _ in range(12):
        p = _run(["gh", "pr", "view", str(pr), "--json", "mergeable,mergeStateStatus"])
        if p.returncode != 0:
            return False, "gh pr view 失败: %s" % (p.stderr or "").strip()
        d = json.loads(p.stdout or "{}")
        m, state = d.get("mergeable"), d.get("mergeStateStatus")
        # gh 新版返回字符串 MERGEABLE/CONFLICTING/UNKNOWN，旧版返回布尔——两种都认
        if m is True or m == "MERGEABLE":
            return True, "可合并（%s）" % state
        if m is False or m == "CONFLICTING":
            return False, "GitHub 判定不可合并（mergeable=%s, state=%s）" % (m, state)
        time.sleep(10)
    return False, "mergeable 一直没算出来（UNKNOWN，等了120s），保守拦截"


# ── 合并与主干复查 ────────────────────────────────────────────────────
def merge(pr):
    """gh pr merge --squash --delete-branch；返回 (ok, gh 输出)。"""
    p = _run(["gh", "pr", "merge", str(pr), "--squash", "--delete-branch"],
             timeout=300)
    return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()


def main_recheck():
    """合并后在 main 上复跑全量；红则打印 revert 命令（不自动 revert）。"""
    _say("\n== 主干复查：在 main 上复跑 runner ==")
    ok, code, tail = local_tests()
    if ok:
        _say("  主干复查绿（退出码 0）。")
        return 0
    _say("  主干复查红（退出码 %s）——主干被这条 squash 弄红了。" % code)
    p = _run(["git", "log", "--oneline", "-1", "origin/main"])
    tip = (p.stdout or "").strip()
    _say("  main 最新提交: %s" % tip)
    _say("  ** 请人工执行（不自动 revert，避免误伤）:**")
    _say("    git revert --no-edit %s && git push" % tip.split()[0])
    _say("  复查输出尾部:\n%s" % tail)
    return 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="merge_gate.py",
        description="绿灯即合：CI + 本地全量 + mergeable 三关全过才 squash 合并（#153）")
    parser.add_argument("pr", type=int, help="要合并的 PR 编号")
    parser.add_argument("--skip-wait", action="store_true",
                        help="不等远端 CI（仍要求 check 已绿；CI 未就绪视为拦截）")
    args = parser.parse_args(argv)
    if args.pr <= 0:
        parser.error("PR 编号必须是正整数")
        return 2

    _say("== 门禁 1/3：远端 CI ==")
    if args.skip_wait:
        verdict, why = ci_state(args.pr, wait=False)
    else:
        verdict, why = wait_ci(args.pr)
    _say("  %s: %s" % (verdict, why))
    if verdict != "green":
        _say("拦截：CI 不是绿的（%s），不合。" % verdict)
        return 1

    _say("== 门禁 2/3：本地全量自检 ==")
    ok, code, tail = local_tests()
    _say("  退出码 %s（0=全绿）" % code)
    if not ok:
        _say("拦截：本地自检红（退出码 %d），不合。输出尾部:\n%s" % (code, tail))
        return 1

    _say("== 门禁 3/3：PR 可合并 ==")
    ok, why = mergeable(args.pr)
    _say("  %s" % why)
    if not ok:
        _say("拦截：%s，不合。" % why)
        return 1

    _say("== 三关全过，squash 合并 ==")
    ok, out = merge(args.pr)
    _say(out or ("（gh 无输出，退出码 0）"))
    if not ok:
        _say("合并失败：gh pr merge 非 0 退出，见上方输出。")
        return 1

    return main_recheck()


if __name__ == "__main__":
    sys.exit(main())
