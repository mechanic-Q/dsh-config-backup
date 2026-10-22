#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hostpath.py — 宿主判定 / 跨侧路径转换 / 子进程输出解码的单一事实源（#143）。

为什么存在：同一件事在本仓曾有 7 份实现——宿主判定 4 份（/proc/version、
/mnt/c/Windows、platform.release、/proc/net/route）、Windows↔WSL 路径转换 2 份
（互为逆向却各写各的正则）、子进程输出解码 1 份。#137 的四层叠加事故全部出自
这类重复实现：一处改了、另一处没改，症状是「看起来应该对」的静默失败。

边界：本模块只回答三件事——现在是哪个宿主、路径在两套口径间怎么换、子进程吐的
字节怎么解。宿主 id 与「当前/目标宿主」的声明由契约解析器 scripts/host_contract.py
提供（本文件自己不读文档）；解析器不可用时退化到 _FALLBACK_HOSTS 那两个 id——
这是**降级副本**，不是第二份事实源，正常路径永远先问解析器。

调用方约定：保留原函数名、函数体一行委托过来，调用方零改动（vision_common 有
17 个 importer，改签名会炸一片）。

对机械检查器的说明：本文件必然写死两侧路径语法（/mnt/<盘>/… 与 <盘>:\…），
所以整文件进 check_mechanical.EXEMPT_FILES 的「路径字面量」豁免——这是全仓
唯一给转换器开的口子，理由写在 check_mechanical 的豁免表里。
"""
import os
import platform
import re

WIN_SEP = chr(92)

# 两侧口径的语法（本文件是唯一允许同时写出的地方）。
_MNT_RE = re.compile(r"^/mnt/([a-zA-Z])/(.*)$", re.S)
_DRIVE_RE = re.compile(r"^([a-zA-Z]):[\\/]?(.*)$", re.S)

# 宿主证据来源（顺序即可信度：内核自述 > 发行版字符串 > Windows 挂载痕迹）。
# 注意这里没有 /proc/net/route：任何 Linux（含容器）都有它，只证明「不是 Windows」，
# 不证明「是 WSL」。旧 dsh_judge._in_wsl() 单靠它判，裸 Linux 会被误判成 WSL；
# 收口时按更严的那一份（旧 vision_common / vision_server 口径）统一，理由记在此处。
_PROC_VERSION = "/proc/version"
_WINDOWS_MOUNT = "/mnt/c/Windows"

# 契约解析失败时的退化词表（与契约声明同名；正常路径走 host_contract.hosts()）。
_FALLBACK_HOSTS = ("wsl", "windows")

# 最近一次读契约声明失败的原因（给 #145 启动守卫报根因用，别让异常静默消失）。
_DECLARED_ERROR = [None]


def _remember_declared_error(exc):
    _DECLARED_ERROR[0] = "%s: %s" % (type(exc).__name__, exc)


def _proc_version_says_wsl():
    """内核自述里带 Microsoft（WSL1/WSL2 都有），最可信的一条。"""
    try:
        with open(_PROC_VERSION, encoding="utf-8", errors="replace") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _release_says_wsl():
    """发行版字符串里带 Microsoft（旧 vision_server 的判据，保留作第二条）。"""
    try:
        rel = platform.release()
    except Exception:  # noqa: BLE001 — 判宿主不许把调用方带崩，退化成空串
        rel = ""
    return "microsoft" in rel.lower()


def _windows_mount_present():
    """C 盘挂在 /mnt/c/Windows 下（旧 vision_server 的另一条判据）。"""
    return os.path.exists(_WINDOWS_MOUNT)


def _wsl_evidence():
    """返回「这是 WSL」的第一条成立证据（人可读 id），都不成立返回空串。

    三条证据合并成一处判定：本仓历史上四个调用点各挑一种（/proc/version、
    platform.release、/mnt/c/Windows、/proc/net/route）。前三条是对 WSL 本身的
    证据，第四条（route）只是「Linux」的证据，已按上面常量的注释降级掉——
    宁可把裸 Linux 判成「不在 WSL」（这台机器上那种组合不存在），也不要把容器
    误当 WSL 去做 /mnt 转换。
    """
    if _proc_version_says_wsl():
        return "proc-version"
    if _release_says_wsl():
        return "platform-release"
    if _windows_mount_present():
        return "windows-mount"
    return ""


def is_wsl():
    """当前进程是否跑在 WSL 内（三条 WSL 专属证据任一成立）。"""
    return bool(_wsl_evidence())


def host_ids():
    """契约声明里的宿主词表（读不到声明时退化为本模块的缺省词表）。"""
    try:
        import host_contract
        ids = tuple(host_contract.hosts())
        if ids:
            return ids
        _remember_declared_error("hosts() 返回空词表")
    except Exception as exc:  # noqa: BLE001 — 判定不能因为文档缺失就炸
        _remember_declared_error(exc)
    return _FALLBACK_HOSTS


def detect_host():
    """本机宿主 id：只区分 wsl 与非 wsl（非 WSL 一律记 windows）。

    #171 口径修正：取值不承诺落在 host_ids() 内——契约将来加第三个宿主
    id 时这里仍只会返回这两个词；声明/比较走 host_contract 或
    host_matches_declaration。
    """
    return "wsl" if is_wsl() else "windows"


def declared_host():
    """契约声明的「当前宿主」id；声明不可读时返回 None，由调用方决定怎么处理。"""
    try:
        import host_contract
        declared = host_contract.current_host()
        if declared:
            _DECLARED_ERROR[0] = None  # 这次读成功了，别把上次的失败当现行原因
            return declared
        _remember_declared_error("current_host() 返回空值")
    except Exception as exc:  # noqa: BLE001
        _remember_declared_error(exc)
    return None


def declared_error():
    """最近一次读契约声明失败的原因；成功过或从未失败则返回 None。"""
    return _DECLARED_ERROR[0]


def host_matches_declaration(detected=None):
    """按契约声明校验宿主，返回 (ok, detail)。

    detail 永远是人可读的一句话，供启动守卫直接贴到 stderr（#145）：不匹配时
    说清「当前是谁、声明是谁」，声明不可读时说清读不到，而不是抛堆栈。
    """
    d = detect_host() if detected is None else detected
    decl = declared_host()
    if not decl:
        why = declared_error() or "未知原因"
        return False, "宿主声明不可读，无法校验当前宿主 %s（%s）" % (d, why)
    # #171：比较语义复用契约的 is_current_host（detected == current_host()），
    # 不再自己比一次。走到这里 decl 必非 None ⇒ host_contract 可导入，
    # 无需 try 兜底（host_ids/declared_host 的 try 是为「契约不可读」场景，
    # 该场景在上一行已提前返回 False）。
    import host_contract

    if host_contract.is_current_host(d):
        return True, "宿主 %s 与声明一致（证据 %s）" % (d, _wsl_evidence() or "non-wsl")
    return False, "宿主不匹配：当前 %s，声明 %s" % (d, decl)


def to_win_path(p):
    """WSL 口径 → Windows 口径；不是 /mnt/<盘>/… 的原样返回（Windows 口径即恒等）。"""
    m = _MNT_RE.match(p or "")
    if not m:
        return p
    return m.group(1).upper() + ":" + WIN_SEP + m.group(2).replace("/", WIN_SEP)


def to_host_path(p):
    """Windows 口径 → 本机口径：WSL 侧换成 /mnt/<盘>/…，非 WSL 侧恒等。"""
    if not p or not is_wsl():
        return p
    m = _DRIVE_RE.match(p)
    if not m:
        return p
    return "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace(WIN_SEP, "/")


def decode_output(p):
    """子进程输出容错解码：UTF-8 → GBK → 替换。

    入参可以是 CompletedProcess（取 stdout+stderr 拼接）或 bytes/str。存在理由
    见 #137 第三层：WSL 侧 interop 子进程吐的是 Windows 控制台代码页（GBK/936），
    text=True 让解释器按 UTF-8 解，UnicodeDecodeError 直接把调用炸掉；而调用方
    真正只关心 ASCII 标记（rc=/pid=/done），容错解码即可。
    """
    if isinstance(p, str):
        return p
    if isinstance(p, (bytes, bytearray)):
        return _decode_bytes(bytes(p))
    out = getattr(p, "stdout", b"") or b""
    err = getattr(p, "stderr", b"") or b""
    # text=True 与二进制混用时 stdout/stderr 可能一侧 str 一侧 bytes：各自解完再拼，
    # 不丢任何一侧（旧 _decode_out 在这里直接 TypeError）。
    return _as_text(out) + _as_text(err)


def _as_text(v):
    if isinstance(v, str):
        return v
    if isinstance(v, (bytes, bytearray)):
        return _decode_bytes(bytes(v))
    return ""


def _decode_bytes(raw):
    """UTF-8 → GBK → 替换：WSL 侧 interop 吐 Windows 控制台代码页（GBK/936）。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
