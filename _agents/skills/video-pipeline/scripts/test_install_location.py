#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_install_location.py — 安装位置假设消除（#152，无框架，assert 直跑）。

背景：#152 之前发布链路写死另一份 git 副本（用户目录下的 .zcode/skills/
video-pipeline），那份停在旧提交、committed 版还语法错。结果是「你改的那份不
一定是跑的那份」。本文件把「脚本从自身位置解析同仓邻居」钉死。

覆盖：
1. 全仓（已跟踪文件）不再出现 .zcode 字面量（AC①）
2. 发布器 sph_publish_v5.py 从自身位置解析 feishu_gate 邻居：把脚本单独复制到
   临时目录跑 --check，邻居必须落在临时目录里；在仓库内跑，邻居必须落在本仓
3. driver 钩子 run_pipeline.py 用 HERE 派生发布器路径，不再有写死副本
4. tencent_refresh_cookie.sh 的 ps1 与 chrome profile 也从自身位置派生
5. .gitignore 不再忽略 .zcode/，但**仍然**忽略 chrome profile（登录态严禁入库）
6. 旧副本处置：.zcode 那份若还在，必须是指回维护副本的 junction/软链
   （samefile 为真）；已删除同样算通过
7. --check 干跑零副作用：不写发布完成标记、不发飞书
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PUBLISHER = os.path.join(HERE, "sph_publish_v5.py")
DRIVER = os.path.join(HERE, "run_pipeline.py")
COOKIE_SH = os.path.join(HERE, "tencent_refresh_cookie.sh")
GITIGNORE = os.path.join(ROOT, ".gitignore")

# 旧副本位置（本票要处置的那一份）；跑测试的机器上没有它也算通过
OLD_COPY = os.path.join(os.path.expanduser("~"), ".zcode", "skills", "video-pipeline")


def _main_root():
    """「维护副本」根：git 主工作树（本仓现在是多 worktree 开发，#141 起从
    .agents/worktrees/<branch> 里跑测试很常见）。在链接工作树里 ROOT 是工作树
    自己的目录，拿它去 samefile 旧 junction 必然不等——比的应该是主检出。
    取不到（无 git / 非仓）就退回 ROOT，行为与历史一致；
    探不到不算红，但**不静默**：打印一行说明走的是回落口径。"""
    if not git_available():
        print("  info: 无 git，维护副本根回落 ROOT")
        return ROOT
    p = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    common = (p.stdout or "").strip()
    if p.returncode != 0 or not common:
        print(f"  info: git-common-dir 取不到（rc={p.returncode}），回落 ROOT："
              f"{(p.stderr or '').strip()[:120]}")
        return ROOT
    return os.path.dirname(os.path.abspath(common))
# 本文件相对仓库根的路径：全仓扫描要排除它（见第 1 节）
SELF_REL = os.path.relpath(os.path.abspath(__file__), ROOT).replace(os.sep, "/")

SECTIONS = []


def section(name):
    SECTIONS.append(name)
    print(f"\n== {name} ==")


def check(cond, msg, detail=""):
    assert cond, f"{msg}{(' :: ' + str(detail)) if detail else ''}"
    print(f"  ok: {msg}")


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def git_available():
    if shutil.which("git") is None:
        return False
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode == 0


def run_publisher(script, args=()):
    """跑发布器 --check，返回 (rc, stdout)。--check 不连 CDP、不写标记、不发飞书。"""
    p = subprocess.run([sys.executable, script, "--check", *args],
                       capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def neighbor_line(out):
    """从 --check 输出里取「飞书邻居」那一行（路径 + 在/缺失）。"""
    for line in out.splitlines():
        if "飞书邻居" in line:
            return line
    return ""


# ── 1) 全仓不再出现 .zcode 字面量（AC①）────────────────────────────────
section("1) 全仓无 .zcode 字面量")
# 有两处「合法命中」必须排除，且排除本身要有反向探针证明排除得对：
#   · 本文件——要断言「别处没有」，它自己就得写出这个词（docstring、OLD_COPY、断言文案）；
#   · references/pitfalls.md——第 60 条要把历史路径原样留档，那些路径正是那条坑的正文。
# 排除法若失效（pathspec 拼错），git grep 会整仓无命中 → 假绿，故逐个反向验证。
EXCLUDED = {
    SELF_REL: "本测试自身：断言文案必须写出该词",
    "references/pitfalls.md": "pitfalls #60 留档历史路径",
}
if git_available():
    p = subprocess.run(["git", "grep", "-I", "-n", "-i", "zcode", "--", "."]
                       + [f":(exclude){rel}" for rel in EXCLUDED],
                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    check(p.returncode in (0, 1), "git grep 可用（rc=0 有命中 / rc=1 无命中）",
          f"rc={p.returncode} err={p.stderr[:200]}")
    hits = [ln for ln in (p.stdout or "").splitlines() if ln.strip()]
    check(p.returncode == 1 and not hits, "已跟踪文件里没有 .zcode / zcode 字样",
          "\n".join(hits[:10]))
    # 反向探针：每个被排除的路径单独扫都必须真能命中，否则说明排除项写错了
    # （路径不存在 / 拼错时 git grep 也返回「无命中」，与「真的干净」无法区分）。
    for rel, why in EXCLUDED.items():
        q = subprocess.run(["git", "grep", "-I", "-n", "-i", "zcode", "--", rel],
                           cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           encoding="utf-8", errors="replace")
        check(q.returncode == 0 and (q.stdout or "").strip(),
              f"反向探针：{rel} 单独扫确实命中（排除理由是「{why}」，不是路径写错）",
              f"rc={q.returncode}")
else:
    print("  skip: 无 git，跳过全仓扫描（人工 grep 代替）")

# 源码三处改动点也要单独钉（git grep 缺失时仍能守住）
pub_src = read(PUBLISHER)
drv_src = read(DRIVER)
sh_src = read(COOKIE_SH)
check(".zcode" not in pub_src, "sph_publish_v5.py 源码无 .zcode")
check(".zcode" not in drv_src, "run_pipeline.py 源码无 .zcode")
check(".zcode" not in sh_src, "tencent_refresh_cookie.sh 源码无 .zcode")

# ── 2) 发布器从自身位置解析 feishu_gate 邻居 ────────────────────────────
section("2) 发布器从自身位置解析邻居")
check("HERE = os.path.dirname(os.path.abspath(__file__))" in pub_src,
      "用 __file__ 求 HERE")
check('FG = os.path.join(HERE, "feishu_gate.py")' in pub_src,
      "邻居 feishu_gate.py 由 HERE 派生")
check("sys.path.insert(0, HERE)" in pub_src, "import 路径也指向 HERE")
# 跨侧路径口径不在发布器里另立一份（host-contract.md 记「收口到 #143 单一模块」）
check("def _host_path" not in pub_src, "发布器不自带第三份路径转换器")
check("_dj.to_host_path(" in pub_src, "跨侧口径走 dsh_judge.to_host_path")

rc, out = run_publisher(PUBLISHER, ["2026-10-15"])
line = neighbor_line(out)
check("飞书邻居" in line, "--check 打印飞书邻居行", out[-300:])
check(os.path.join(HERE, "feishu_gate.py").replace("\\", "/") in line.replace("\\", "/"),
      "在仓库内跑时邻居落在本仓 scripts/", line)

# 把发布器搬到临时目录：邻居必须跟着脚本走，而不是跟着仓库走。
# 整份 scripts/ 一起搬（发布器要 import 同目录的 dsh_judge 等模块），唯独**不搬**
# feishu_gate.py —— 它正是本节要验的邻居，留在原仓才算真验到「跟着脚本走」。
tmpdir = tempfile.mkdtemp(prefix="sph_selfloc_")
try:
    shutil.copytree(HERE, tmpdir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("feishu_gate.py", "__pycache__"))
    lone = os.path.join(tmpdir, "sph_publish_v5.py")
    rc2, out2 = run_publisher(lone, ["2026-10-15"])
    line2 = neighbor_line(out2)
    check(rc2 in (0, 3), "孤立副本 --check 能跑完（rc 0 可发 / 3 输入不全）",
          f"rc={rc2} {out2[-300:]}")
    check(tmpdir.replace("\\", "/") in line2.replace("\\", "/"),
          "孤立副本的邻居落在它自己的目录里（不指向原仓库）", line2)
    check("缺失" in line2, "孤立目录里没有 feishu_gate.py，如实报缺失", line2)
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

# ── 3) driver 钩子用 HERE 派生发布器 ────────────────────────────────────
section("3) driver 钩子同源派生")
check('v5_local = os.path.join(HERE, "sph_publish_v5.py")' in drv_src,
      "driver 用 HERE 派生 v5 路径")
check("v5_win = dj.to_win_path(v5_local)" in drv_src,
      "跨侧口径走 to_win_path（不手写两份）")
check("sph_publish_v5.py" in drv_src, "driver 仍引用发布器（没被误删）")
check("import dsh_judge as dj" in drv_src,
      "to_win_path 的来源模块在 import（别名与同文件既有写法一致）")

# ── 4) cookie 脚本自解析 ────────────────────────────────────────────────
section("4) cookie 脚本自解析")
check('SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in sh_src,
      "SKILL_DIR 由脚本自身位置派生")
check('PS1_SCRIPT="$SKILL_DIR/scripts/export_tencent_cookie.ps1"' in sh_src,
      "ps1 邻居由 SKILL_DIR 派生")
check('CHROME_PROFILE="$SKILL_DIR/assets/chrome-tencent-profile"' in sh_src,
      "chrome profile 由 SKILL_DIR 派生")
check('wslpath -w "$CHROME_PROFILE"' in sh_src,
      "profile 用 wslpath 换 Windows 口径")
check("/home/lmr/.hermes" not in sh_src, "不再写死第三份副本（hermes）")
check("export_tencent_cookie.ps1" in sh_src, "ps1 引用仍在")
if shutil.which("bash"):
    # 走 stdin 而不是传路径：Windows 的 bash 是 MSYS（不认 C:/…），WSL 的 bash
    # 不认 C:\…，同一份源码喂进去两边都能验语法。
    # input 必须是 bytes：传 str 时 Python 用宿主 locale（cp936）编码，中文注释
    # 被编坏后引号错位，bash 会报 "unexpected end of file"（本机实测）。
    # text=True 与 bytes stdin 互斥，故收 bytes 自己解码
    p = subprocess.run(["bash", "-n"], input=sh_src.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err = (p.stderr or b"").decode("utf-8", "replace")
    check(p.returncode == 0, "bash -n 语法通过", err[-200:])
else:
    print("  skip: 无 bash，跳过语法检查")

# ── 5) .gitignore：不再忽略 .zcode，仍忽略 profile ──────────────────────
section("5) .gitignore 规则")
gi = read(GITIGNORE)
rules = [ln.strip() for ln in gi.splitlines() if ln.strip() and not ln.strip().startswith("#")]
check(".zcode/" not in rules, ".zcode/ 忽略规则已删（仓库内无此路径）")
check("assets/chrome-tencent-profile/" in rules,
      "chrome profile 仍被忽略（登录态严禁入库）")
if git_available():
    p = subprocess.run(["git", "check-ignore", "--no-index", "-v", "--stdin"],
                       input="assets/chrome-tencent-profile/Cookies\n.zcode/probe\n",
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    check("chrome-tencent-profile" in out, "profile 探针被忽略（行为验证）", out[:200])
    check(".zcode/probe" not in out, ".zcode 探针不再被忽略（行为验证）", out[:200])
else:
    print("  skip: 无 git，跳过 check-ignore 行为验证")

# ── 6) 旧副本处置（AC③）───────────────────────────────────────────────
section("6) 旧副本处置")
if os.path.exists(OLD_COPY):
    check(os.path.isdir(OLD_COPY), "旧路径存在且是目录（junction/软链也算）", OLD_COPY)
    same = False
    main_root = _main_root()
    try:
        same = os.path.samefile(OLD_COPY, main_root) or os.path.samefile(OLD_COPY, ROOT)
    except OSError as e:
        print(f"  info: samefile 失败 {e}")
    check(same, "旧路径与维护副本是同一份（junction / 软链）", OLD_COPY)
    check(os.path.isfile(os.path.join(OLD_COPY, "scripts", "sph_publish_v5.py")),
          "经旧路径能看到发布器（现有调用不受影响）")
else:
    print(f"  ok: 旧副本已不存在（按票允许的另一种处置）{OLD_COPY}")

# ── 7) --check 干跑零副作用 ────────────────────────────────────────────
section("7) --check 干跑零副作用")
check('CHECK_ONLY = "--check" in sys.argv[1:]' in pub_src, "CHECK_ONLY 旗标就位")
check("if CHECK_ONLY:\n        sys.exit(check())" in pub_src.replace("\r\n", "\n"),
      "--check 走 check() 而不是 main()")
check("asyncio.run(main())" in pub_src, "真发布路径仍在（--check 之外）")
# check() 里不许出现真发布动作：连 CDP / 写标记 / 发飞书
m = re.search(r"\ndef check\(\):(.*?)(?=\n\ndef |\n\nif __name__)", pub_src, re.S)
check(m is not None and m.group(1).strip(), "能定位 check() 函数体")
body = m.group(1) if m else ""
for forbidden, why in (("marker", "不写发布完成标记"),
                       ("notify(", "不发飞书"),
                       ("connect_over_cdp", "不连 CDP"),
                       ("setFileInputFiles", "不上传成片")):
    check(forbidden not in body, f"check() 内 {why}")

# 日期参数写错必须报错，不能静默回落「今天」（否则打错日期会发错单）
rc_bad, out_bad = run_publisher(PUBLISHER, ["2026-13-01"])
check(rc_bad == 2, "非法日期报错退出（rc=2），不静默回落今天", f"rc={rc_bad} {out_bad[-200:]}")
check("非法日期" in out_bad, "非法日期有明确提示", out_bad[-200:])

# ── 8) 飞书播报不经 wsl.exe，凭证源仍是 DSH ─────────────────────────────
section("8) 飞书播报凭证源")
check("[sys.executable, FG, \"send\"" in pub_src,
      "notify 用当前解释器直跑同目录 feishu_gate.py")
check("'wsl'" not in pub_src and '"wsl"' not in pub_src,
      "不再经 wsl.exe 转手（执行者与路径一起改掉了才是真自解析）")
# 行为验证：改执行者后凭证源必须仍是 channels.json，不能静默回落 hermes .env
# （feishu_gate.py:46-50 记的坑：回落会读到旧应用与旧会话，改了没生效）
#
# 2026-10-19（#141）：本节原来断言「本机 ~/.dsh/dsh-im-connect/channels.json 在盘」，
# 那是把开发机的桌面 DSH 状态当成了被测行为——CI（ubuntu）上没有这个文件，测试
# 会因为「这台机器没装 DSH」而红，而不是因为代码错。改为在本进程内造一份同形
# channels.json（临时目录），再把模块级 CHANNELS_JSON 指过去：验的是解析口径
# 与「不回落 hermes」，与本机装没装 DSH 无关。
sys.path.insert(0, HERE)
import feishu_gate as _fg  # noqa: E402 — 需在 sys.path.insert 之后
import json as _json  # noqa: E402
_chan = os.environ.get("FEISHU_CHANNEL") or _fg.DEFAULT_CHANNEL
_chan_dir = tempfile.mkdtemp(prefix="t152_chan_")
_chan_file = os.path.join(_chan_dir, "channels.json")
with open(_chan_file, "w", encoding="utf-8") as _f:
    _json.dump({"channels": {_chan: {"config": {"appId": "cli_fixture_app",
                                                "appSecret": "fixture_secret"}}}},
               _f, ensure_ascii=False)
_old_chan_json = _fg.CHANNELS_JSON
_fg.CHANNELS_JSON = _chan_file
try:
    check(os.path.exists(_fg.CHANNELS_JSON),
          "channels.json 在该宿主可见（本进程自造的夹具路径）", _fg.CHANNELS_JSON)
    _cfg = (((_json.load(open(_fg.CHANNELS_JSON, encoding="utf-8")).get("channels") or {})
             .get(_chan) or {}).get("config") or {})
    check(bool((_cfg.get("appId") or "").strip()) and bool((_cfg.get("appSecret") or "").strip()),
          f"渠道 {_chan} 自带 appId/appSecret（无需回落）")
    # 行为断言（替代原来数".dsh 在不在路径里"的字面量检查）：解析结果必须来自
    # channels.json，而不是 hermes .env 回落——两者取值不相同，一眼可辨。
    _env = _fg._load_env()
    check(_env.get("FEISHU_APP_ID") == "cli_fixture_app",
          "凭证源是 channels.json（不是 hermes .env 回落）", _env.get("FEISHU_APP_ID"))
finally:
    _fg.CHANNELS_JSON = _old_chan_json
    shutil.rmtree(_chan_dir, ignore_errors=True)

print(f"\ntest_install_location: {len(SECTIONS)} 节全部通过")
