#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_repo_hygiene.py — 工作树卫生：忽略规则必须入库且不误伤（#147，无框架，assert 直跑）。

背景：#147 之前 `.wt/` 只写在**本地** `.git/info/exclude`，不随仓库走；`.tmp_*/`
与 `t18t_*` 则压根没规则。于是 test_s08_judge_loop / test_human_flag_loop /
test_evidence_train_chain 三个测试在仓库里留下的临时目录，一合并就重新把工作树
弄脏（#147 实测 193 条未跟踪）。规则写进 `.gitignore` 之后才叫「补齐」，写在
本地 exclude 里等于没补——本文件把这条钉死。

覆盖：
1. `.gitignore` 逐行存在三条规则：`.tmp_*/`、`.wt/`、`t18t_*/`
2. 行为验证：五个真实产生点都被**仓库内 `.gitignore`** 命中（不是本地 exclude）
3. 反向验证：源码 / 测试夹具 / 配置不被这三条规则误伤
4. 全量扫描：没有**已跟踪**文件被这三条新规则挡掉（防规则写宽把入库文件挡掉）
5. 产生点仍在：三个测试的临时目录常量没被改名（规则与真实目录名对得上）

无 git 可用时只有第 2/3/4 节跳过（大声打印原因），第 1/5 节纯文本照常断言。
"""
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GITIGNORE = os.path.join(ROOT, ".gitignore")

# 本票新增的三条规则（前缀不带斜杠，匹配任意层级）
RULES = (".tmp_*/", ".wt/", "t18t_*/")

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def git_available():
    """git 存在**且** cwd 在仓库内：否则 check-ignore 返回 rc=128 而非 0/1。"""
    if shutil.which("git") is None:
        return False
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode == 0


def check_ignore(paths):
    """返回 [(path, source, lineno, pattern)]；未命中的路径不出现。

    --no-index：即使路径不存在或已被跟踪也照判，探针路径不必真在盘上。
    """
    # -z 只配 --stdin 用（否则 fatal: -z only makes sense with --stdin）
    p = subprocess.run(
        ["git", "check-ignore", "--no-index", "-v", "-z", "--stdin"],
        input="\0".join(paths) + "\0", cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace")
    # rc=0 有命中 / rc=1 全不命中 / 其他为 git 报错
    assert p.returncode in (0, 1), f"git check-ignore 失败 rc={p.returncode}: {p.stderr}"
    # -z 下每条命中是 4 个 NUL 分隔字段：source / linenum / pattern / pathname
    # （没有额外的记录分隔符；未命中的路径不出现，故不能按下标配对）
    fields = p.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    assert len(fields) % 4 == 0, f"check-ignore -z 输出字段数不是 4 的倍数: {fields}"
    out = []
    for i in range(0, len(fields), 4):
        source, lineno, pattern, path = fields[i:i + 4]
        out.append((path, source.replace("\\", "/"), int(lineno), pattern))
    return out


# ── 1. .gitignore 里逐行存在三条规则 ────────────────────────────────────
section(".gitignore 逐行存在三条规则")
text = read(GITIGNORE)
lines = [ln.strip() for ln in text.splitlines()]
for pattern in RULES:
    check(pattern in lines, f".gitignore 有独立一行 {pattern}")
check(text.endswith("\n"), ".gitignore 以换行结尾（追加规则不会粘到上一行）")


# ── 2. 行为验证：真实产生点被仓库内 .gitignore 命中 ─────────────────────
# 这五条对应仓库里实际会写临时目录的地方，任何一条被别处（本地 exclude /
# 全局 excludesFile）命中都不算数——必须来源是仓库根的 .gitignore。
PROBES = [
    (".tmp_t16_tests/t16_loop_1792350987244_360936/01_分段/分镜清单_raw.json",
     "test_s08_judge_loop 的 _TMP_ROOT"),
    (".tmp_t17_temp/_runner_gate.py", "T17 手工调试残留"),
    ("scripts/.tmp_t17_test/fake-slug/_frames/shot01.jpg",
     "test_human_flag_loop 的 WORK"),
    (".wt/146/scripts/run_pipeline.py", "implementer worktree"),
    ("t18t_261414_1/rotation-dataset.jsonl", "test_evidence_train_chain 的 _mktemp"),
]
if not git_available():
    print("\n!! SKIP(no git)：第 2/3/4 节需要 git，本机 PATH 里没有")
else:
    section("真实产生点被仓库内 .gitignore 命中")
    hits = {path: (src, ln, pat) for path, src, ln, pat in check_ignore([p for p, _ in PROBES])}
    for path, why in PROBES:
        assert path in hits, f"未被忽略：{path}（{why}）——规则没生效，合并后会重新变脏"
        src, ln, pat = hits[path]
        # 必须是**仓库根**的 .gitignore：basename 比对会让任意层级的
        # scripts/.gitignore 通过，将来加一份就假绿。
        check(src.replace("\\", "/") == ".gitignore",
              f"{path} 命中来源是仓库根 .gitignore（{src}:{ln} {pat}）")

    # ── 3. 反向验证：不误伤源码 / 夹具 / 配置 ──────────────────────────
    section("不误伤源码 / 夹具 / 配置")
    # 只列「规则写宽了就会误伤」的代表性路径。活数据集（references/ 下那份
    # 期次 jsonl）不在此列：它已被跟踪，第 4 节的全量 git ls-files 扫描同样
    # 能兜住；而且 test_dataset_readonly.py 会全仓扫描「测试文件里不得出现
    # 活数据集路径」，在这里写死那个文件名会被判成违规（那条守卫是给写数据集
    # 用的，不是给忽略规则用的）。
    KEEP = [
        "scripts/run_pipeline.py",
        "scripts/fixtures/t15_rotation_dataset_20261015.jsonl",
        ".gitignore",
        "AGENTS.md",
    ]
    hurt = check_ignore(KEEP)
    check(not hurt, f"{len(KEEP)} 个必须入库的路径都没被忽略：" + ("" if not hurt else str(hurt)))

    # ── 4. 没有任何已跟踪文件落进忽略规则 ──────────────────────────────
    section("已跟踪文件不被忽略规则命中")
    ls = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        encoding="utf-8", errors="replace")
    check(ls.returncode == 0, "git ls-files 可读")
    tracked = [p for p in ls.stdout.split("\0") if p]
    check(len(tracked) > 100, f"仓库已跟踪 {len(tracked)} 个文件（>100，确认没在空目录里跑）")
    # 只追究本票新增的三条规则：仓库里早有 *_test/**.log 之类「已跟踪但命中忽略」
    # 的历史遗留（git 会继续跟踪它们），那与本票无关，打印出来但不判红。
    NEW_RULES = set(RULES)
    all_hits = check_ignore(tracked)
    swallowed = [h for h in all_hits if h[3] in NEW_RULES]
    check(not swallowed,
          "没有已跟踪文件被这三条新规则挡掉：" + ("" if not swallowed else str(swallowed[:5])))
    preexisting = [h for h in all_hits if h[3] not in NEW_RULES]
    print(f"  note: {len(preexisting)} 个已跟踪文件本来就命中别的忽略规则（历史遗留，不在本票范围）")


# ── 5. 产生点仍在：规则名与真实临时目录名对得上 ────────────────────────
section("三个产生点仍在，规则名与目录名对得上")
s08 = read(os.path.join(HERE, "test_s08_judge_loop.py"))
check('".tmp_t16_tests"' in s08, "test_s08_judge_loop 仍在仓库根写 .tmp_t16_tests")
t17 = read(os.path.join(HERE, "test_human_flag_loop.py"))
check('".tmp_t17_test"' in t17, "test_human_flag_loop 仍写 scripts/.tmp_t17_test")
t18 = read(os.path.join(HERE, "test_evidence_train_chain.py"))
check('"t18t_"' in t18, "test_evidence_train_chain 仍以 t18t_ 前缀建临时目录")

print(f"\n{'=' * 40}\n{len(SECTIONS)} 节全部通过：{'、'.join(SECTIONS)}")
