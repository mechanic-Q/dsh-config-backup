#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_preflight.py — 环境护栏预检自检（#46，assert 直跑风格同 test_run_pipeline）。

覆盖：
1. CRLF 扫描：字节级检测，只有 b"\\r\\n" 命中（工作区被本地工具改脏 = 叫人，#46 拍板不自动改）
2. bash -n 语法检查（注入 runner；另跑一次真 bash 冒烟）
3. check_http 探活（注入 urlopen，不真联网）
4. 飞书凭证：黑盒复用 FeishuGate 同一校验（补丁 feishu_gate._load_env）
5. check_static 汇总：失败项顺序确定（crlf → bash-syntax → comfyui → feishu-creds → sfx-asset）；
   sfx-asset（#52）：音效缺失 detail 含路径+静默回退警示、fix 含 cdn 直链+处理规格，在位不新增 Failure
6. human_message：附具体失败项与修法，明示 stamps 未动
7. Strata 在线门（#86，两车道唯一 LLM 探活）：_gate_strata 离线抛 RuntimeError 附手动步骤 /
   在线放行并按 ledger_key 留痕；run_stage 的 env 旗标触发 gate（8888 探活/重启阶梯已退役）
8. main() 接线 e2e：Strata 离线 → 预检拦截叫人（不建 slug）；dry-run 只告警不退出
"""
import os
import sys
import tempfile
import shutil
import time as _time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import preflight as pf
import feishu_gate
import run_pipeline as rp

FULL_ENV = {"FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b", "FEISHU_DAILY_NEWS_CHAT_ID": "c"}


def mk_scripts(dirpath):
    """good.sh = LF 合法；bad.sh = CRLF；badsyntax.sh = LF 但语法错（真跑 bash -n 会炸）。
    返回三者绝对路径。"""
    good = os.path.join(dirpath, "good.sh")
    bad = os.path.join(dirpath, "bad.sh")
    badsyntax = os.path.join(dirpath, "badsyntax.sh")
    open(good, "wb").write(b"#!/bin/bash\nset -euo pipefail\n")
    open(bad, "wb").write(b"#!/bin/bash\r\nset -euo pipefail\r\n")
    open(badsyntax, "wb").write(b"#!/bin/bash\nfi\n")
    return good, bad, badsyntax


def write_sfx(repo_root):
    """在 repo 根落一份翻页音效资产（路径与 preflight.SFX_FLIP_REL 同形）。"""
    p = os.path.join(repo_root, "assets", "sfx", "page_flip_bigbook.wav")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(b"RIFF")


def mk_repo(parent, with_sfx):
    """repo 形状（#52）：scripts/ 下 good/bad/badsyntax 三脚本 + assets/sfx/ 翻页音效
    视 with_sfx 在位。返回 scripts_dir。"""
    scripts_dir = os.path.join(parent, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    mk_scripts(scripts_dir)
    if with_sfx:
        write_sfx(parent)
    return scripts_dir


# ── 1. CRLF 扫描 ─────────────────────────────────────────────────────
tmp = tempfile.mkdtemp()
good, bad, badsyntax = mk_scripts(tmp)
scripts = pf.find_shell_scripts(tmp)
assert scripts == sorted([good, bad, badsyntax]), scripts
assert pf.scan_crlf(scripts) == [bad], "只有 CRLF 文件命中"

# ── 2. bash -n 语法检查 ──────────────────────────────────────────────
calls = []
def fake_run(argv, timeout=None, stdin_payload=None):
    calls.append((tuple(argv), bool(stdin_payload), timeout))
    # nt 走 stdin：按内容判坏脚本（CRLF 的 bad.sh 内容含 \r）；POSIX 按文件名
    if os.name == "nt":
        hit_bad = b"\r" in (stdin_payload or b"")
    else:
        hit_bad = os.path.basename(argv[-1]) == "bad.sh"
    return (2, "line 2: syntax error near unexpected token") if hit_bad else (0, "")

errs = pf.check_bash_syntax([good, bad], run=fake_run)
assert list(errs) == [bad], errs
assert "syntax error" in errs[bad]
# nt 下 stdin 喂内容（盘符路径对 MSYS bash 无解，2026-10-12 实测）；POSIX 原样文件参数
if os.name == "nt":
    assert calls == [(("bash", "-n"), True, 15), (("bash", "-n"), True, 15)], calls
else:
    assert calls == [(("bash", "-n", good), False, 15), (("bash", "-n", bad), False, 15)], calls
if shutil.which("bash"):  # 真 bash 冒烟：LF 脚本必须过（nt 走 stdin，不受路径形态影响）
    assert pf.check_bash_syntax([good]) == {}, f"真 bash -n 不应报错（{os.name}）"
    assert pf.check_bash_syntax([badsyntax]) != {}, "真 bash -n 应抓到语法错误"

# _run 是护栏自己的执行端：超时/缺可执行文件必须转成「一条 Failure」，不能裸抛
# TimeoutExpired（2026-10-20 实体：bash -n 挂死 15s 把整期在预检就冲掉，traceback 里
# 看不出坏的是哪个脚本）。用 sys.executable 起一个必超时的子进程钉住这条。
rc, out = pf._run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
assert rc != 0 and "超时" in out, (rc, out)
rc, out = pf._run(["definitely-no-such-binary-xyz"], timeout=5)
assert rc == 127 and "不存在" in out, (rc, out)
errs = pf.check_bash_syntax([good], run=lambda a, timeout=None, stdin_payload=None: (124, "命令超时（15s 未返回）: bash -n"))
assert list(errs) == [good] and "超时" in errs[good], errs

# ── 3. check_http 探活 ───────────────────────────────────────────────
class FakeResp:
    def __init__(self, status):
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

ok, detail = pf.check_http("http://x/queue", urlopen=lambda u, timeout: FakeResp(200))
assert ok, detail
ok, detail = pf.check_http("http://x/queue", urlopen=lambda u, timeout: FakeResp(503))
assert not ok and "503" in detail
def refused(u, timeout):
    raise OSError("connection refused")
ok, detail = pf.check_http("http://x/queue", urlopen=refused)
assert not ok and "refused" in detail

# ── 4. 飞书凭证（黑盒：与 Driver 后续构造同一校验）─────────────────────
_orig_load_env = feishu_gate._load_env
try:
    feishu_gate._load_env = lambda: dict(FULL_ENV)
    assert pf.check_feishu_creds() is None
    feishu_gate._load_env = lambda: {"FEISHU_APP_ID": "a"}
    f = pf.check_feishu_creds()
    assert f is not None and f.kind == "feishu-creds", f
    assert "缺失" in f.detail and "FEISHU" in f.detail, f
    assert ".hermes" in f.fix, f
finally:
    feishu_gate._load_env = _orig_load_env

# ── 5. check_static 汇总：顺序确定、全绿为空、CRLF 不重复跑 bash -n、sfx-asset（#52）──
repo1 = tempfile.mkdtemp()  # 无音效资产 → 全故障场景含 sfx-asset
s1 = mk_repo(repo1, with_sfx=False)
repo2 = tempfile.mkdtemp()  # 资产在位 → 不新增 Failure
s2 = mk_repo(repo2, with_sfx=True)

ok200 = lambda u, timeout: FakeResp(200)
calls5 = []
def run5(argv, timeout=None, stdin_payload=None):
    calls5.append(argv)
    if os.name == "nt":
        hit = b"fi" in (stdin_payload or b"")   # badsyntax.sh 的语法错误行内容
    else:
        hit = os.path.basename(argv[-1]) == "badsyntax.sh"
    return (2, "syntax error near unexpected token `fi'") if hit else (0, "")

try:
    feishu_gate._load_env = lambda: dict(FULL_ENV)
    fails = pf.check_static(s1, "http://x/queue", run=run5, urlopen=ok200)
    assert [f.kind for f in fails] == ["crlf", "bash-syntax", "sfx-asset"], fails
    # CRLF 文件不重复跑 bash -n（\r 必然报语法错，双报是噪音）：good+badsyntax 两发，
    # 且命中语法错误的只有 badsyntax 一份内容（CRLF 的 bad.sh 内容不进 bash）
    assert len(calls5) == 2, calls5
    assert [f.kind for f in fails].count("bash-syntax") == 1, fails
    sfx = fails[-1]
    assert "page_flip_bigbook.wav" in sfx.detail and "静默回退" in sfx.detail, sfx
    assert "cdn.freesound.org" in sfx.fix and "-3dBFS" in sfx.fix, sfx
    assert pf.check_sfx_asset(s2) is None, "资产在位不产生 Failure"

    feishu_gate._load_env = lambda: {}
    fails = pf.check_static(s1, "http://x/queue", run=run5, urlopen=refused)
    assert [f.kind for f in fails] == \
        ["crlf", "bash-syntax", "comfyui", "feishu-creds", "sfx-asset"], fails
    assert "192.168" not in fails[2].fix, "comfyui 修法地址须取自入参而非硬编码"

    # AC2（#52）：资产在位时预检结果与现状完全一致——全故障桩下恰为既有 4 项
    fails = pf.check_static(s2, "http://x/queue", run=run5, urlopen=refused)
    assert [f.kind for f in fails] == ["crlf", "bash-syntax", "comfyui", "feishu-creds"], fails

    repo3 = tempfile.mkdtemp()
    s3 = os.path.join(repo3, "scripts")
    os.makedirs(s3)
    open(os.path.join(s3, "ok.sh"), "wb").write(b"#!/bin/bash\ntrue\n")
    write_sfx(repo3)
    feishu_gate._load_env = lambda: dict(FULL_ENV)
    t0 = _time.time()
    fails = pf.check_static(s3, "http://x/queue",
                            run=lambda a, timeout=None, stdin_payload=None: (0, ""),
                            urlopen=ok200)
    assert fails == [], fails
    assert _time.time() - t0 < 5, "静态检查应秒级（注入桩下近乎瞬时）"
finally:
    feishu_gate._load_env = _orig_load_env

# ── 6. 叫人消息：失败项 + 修法 + stamps 未动 ──────────────────────────
F = pf.Failure
msg = pf.human_message([F("crlf", "行尾脏了", "跑 sed 修"), F("comfyui", "不可达", "开 ComfyUI")])
assert "🚨" in msg and "stamps" in msg
assert msg.count("❌") == 2 and msg.count("修法:") == 2
assert "行尾脏了" in msg and "开 ComfyUI" in msg

# ── 7. Strata 生命周期钩子（#85：_gate_strata = ensure_online 自愈阶梯 + 台账）──
from ledger import Ledger

_ensure_saved = rp.strata_window.ensure_online
_log_saved = rp.log
try:
    d = rp.Driver.__new__(rp.Driver)
    d.notify = lambda t: None
    d.ledger = Ledger(os.path.join(tempfile.mkdtemp(), "report.json"), t0=0.0)

    # 7a. 阶梯放行 + 台账留痕（压缩 s04 缺省键 / legacy 阶段键透传）——离线自动拉起
    # 是阶梯内部事（桩掉即不真拉），gate 只认结果
    def _fake_ensure(notify=None):
        return {"outcome": "started", "restarts": 1, "warmed": True,
                "at": "2026-10-12 00:00:00"}
    rp.strata_window.ensure_online = _fake_ensure
    d._gate_strata()
    _rec = d.ledger.get("strata.s04_entry")
    assert _rec["outcome"] == "started" and _rec["restarts"] == 1, _rec
    d._gate_strata(ledger_key="strata.s05_entry")
    assert d.ledger.get("strata.s05_entry")["url"] == rp.LANE_STRATA_URL

    # 7b. 阶梯耗尽 → StrataHardError（RuntimeError 子类）上抛——run_stage 重试语义：
    # 重试 1 次后连败叫人，手动步骤在叫人消息（不在 gate 内拼接）
    def _exhausted(notify=None):
        raise rp.strata_window.StrataHardError("Strata 重启预算耗尽")
    rp.strata_window.ensure_online = _exhausted
    try:
        d._gate_strata()
        raise AssertionError("阶梯耗尽必须上抛 StrataHardError")
    except rp.strata_window.StrataHardError as e:
        assert "预算耗尽" in str(e), str(e)

    # 7c. run_stage 的 env 旗标触发 gate（8888 时代 _gate_gpu 的接缝替身）
    import types as _types
    d2 = rp.Driver.__new__(rp.Driver)
    d2.stamps_dir = tempfile.mkdtemp()
    d2.feishu = _types.SimpleNamespace(send_text=lambda t: None)
    d2.ledger = Ledger(os.path.join(d2.stamps_dir, "report.json"), t0=0.0)
    d2.stamp = lambda *a, **k: None
    d2.done = lambda s: False
    _gate_calls = []
    d2._gate_strata = lambda ledger_key="strata.s04_entry": _gate_calls.append(ledger_key)
    rp.log = lambda m: None
    d2.run_stage("s05", "摄影师（Strata）", lambda: None, env={"LLM_API": "x"})
    assert _gate_calls == ["strata.s05_entry"], _gate_calls
    d2.run_stage("s01", "分段", lambda: None, env=None)
    assert _gate_calls == ["strata.s05_entry"], "env=None 阶段不得触发 LLM gate"
finally:
    rp.strata_window.ensure_online = _ensure_saved
    rp.log = _log_saved
print("test_preflight: Strata 生命周期钩子（阶梯留痕/耗尽上抛/旗标触发）OK")

# ── 8. main() 接线 e2e：预检拦截叫人 + Strata 离线不拦 + dry-run 只告警 ──
# AC1/AC2 的无副作用替身：exit 3、飞书恰好一条、slug/stamps 不被创建；
# dry-run 只告警不拦截。（#85：Strata 离线不再预检拦截——归 s04 入口自愈阶梯，
# 无人值守场景杀 Strata 后 driver 必须能跑到底）
_saved8 = [(attr, m, getattr(m, attr)) for attr, m in
           (("HERE", rp), ("COMFY_QUEUE_URL", pf), ("check_http", pf),
            ("FeishuGate", feishu_gate), ("argv", sys))]
_sent = []


class FakeGate:
    def __init__(self, *a, **k):
        pass
    def send_text(self, text):
        _sent.append(text)


os.environ["DRIVER_HOST_OVERRIDE"] = "1"  # #145 守卫：非声明宿主（如 WSL 上跑本测试）越权放行，本测只验预检语义

try:
    dirty_dir = tempfile.mkdtemp()
    dirty = os.path.join(dirty_dir, "dirty.sh")
    open(dirty, "wb").write(b"#!/bin/bash\r\ntrue\r\n")
    ghost_slug = os.path.join(dirty_dir, "slug")
    fake_md = os.path.join(dirty_dir, "x.md")
    open(fake_md, "w").write("# 口播稿\n")

    rp.HERE = dirty_dir
    pf.COMFY_QUEUE_URL = "http://stub/queue"
    pf.check_http = lambda url, timeout=5.0, urlopen=None: (False, "stub-down")
    feishu_gate.FeishuGate = FakeGate

    # 8a. 真跑：CRLF + ComfyUI 挂 → 拦截叫人，不建 slug；Strata 不在拦截清单
    #（#85 起预检不查 Strata，其在线性归 s04 入口自愈阶梯接管）
    sys.argv = ["run_pipeline.py", "--script", fake_md, "--workdir", ghost_slug, "--bgm", ""]
    try:
        rp.main()
        raise AssertionError("预检失败必须 sys.exit(3)")
    except SystemExit as e:
        assert e.code == 3, e.code
    assert len(_sent) == 1, _sent
    assert "dirty.sh" in _sent[0] and "stamps" in _sent[0], _sent[0]
    assert "ComfyUI" in _sent[0], "ComfyUI 失败项应列入同一条消息"
    assert "Strata" not in _sent[0], "Strata 离线不得进预检拦截清单（#85 自愈阶梯接管）"
    assert not os.path.exists(ghost_slug), "拦截时不得创建 slug/_driver 目录"

    # 8b. dry-run：同样环境只告警，不叫人不退出，照常打印执行计划
    os.makedirs(ghost_slug)
    sys.argv = ["run_pipeline.py", "--script", fake_md, "--workdir", ghost_slug,
                "--bgm", "", "--dry-run"]
    rp.main()  # 不应退出
    assert len(_sent) == 1, "dry-run 不得发飞书"
finally:
    for attr, m, v in _saved8:
        setattr(m, attr, v)

print("test_preflight: 全部通过")
