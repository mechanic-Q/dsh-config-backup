#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""host_contract.py — 宿主契约的唯一读取入口（票 #144）。

docs/host-contract.md 是「谁在哪跑、用哪套语法、产物落在哪」的唯一事实源。
本模块只做两件事：解析文档里的机读声明块；给运行时守卫与测试提供访问器。
任何脚本都不准自己再读契约文档（scripts/ 里只有本文件出现该文件名）。

API:
    import host_contract as hc
    hc.current_host()      -> "windows"（driver 当前执行宿主；#151 两列合一后单列）
    hc.is_current_host(x)  -> 守卫用：detected 是否等于声明宿主
    hc.stage("s08")        -> 单阶段声明
    hc.shells() / hc.services() / hc.artifact_dirs() / hc.paths()

CLI:
    python3 scripts/host_contract.py current|stages|shells|dirs|services|json
    python3 scripts/host_contract.py stage s08
    契约缺失或损坏时打印原因并以 exit 2 退出（守卫 #145 依赖这个退出码）。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOC_REL = "docs/host-contract.md"
DOC_PATH = os.path.normpath(os.path.join(HERE, os.pardir, "docs", "host-contract.md"))
# 三个反引号的围栏；用 chr(96) 拼，避免本文件自身被 Markdown 围栏语法干扰。
FENCE = chr(96) * 3
MARKER = FENCE + "json host-contract"
_CACHE = {}

# 声明块必须带的顶层键；少一个就在加载时失败，不留给调用方 KeyError。
_TOP_KEYS = ("contract", "version", "hosts", "driver", "paths", "syntax_kinds",
             "stages", "shells", "services", "artifact_dirs")
_STAGE = ("id", "name", "host", "services", "syntax", "artifacts")
_SHELL = ("id", "host", "usage", "forbidden")
_SERVICE = ("id", "side", "endpoint", "usage", "access")
_ARTIFACT = ("id", "path", "writer", "readers", "cleanable", "clean_rule")


class HostContractError(ValueError):
    """契约文档缺失或损坏；消息里带文档路径，调用方原样上报即可。"""


def _fail(path, msg):
    raise HostContractError("%s（%s）" % (msg, path))


def _req(obj, keys, path, what):
    for key in keys:
        if key not in obj:
            _fail(path, "%s 缺字段 %r" % (what, key))
    return obj


def _list(value, path, what):
    if not isinstance(value, list) or not value:
        _fail(path, "%s 必须是非空列表" % what)
    return value


def _ids(value, path, what, allow_empty=False):
    """标识列表；allow_empty 用于「可以没有跨侧依赖、也可以不产出文件」的阶段字段。"""
    if not isinstance(value, list):
        _fail(path, "%s 必须是列表" % what)
    if not value and not allow_empty:
        _fail(path, "%s 必须是非空列表" % what)
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _fail(path, "%s 里有空的标识 %r" % (what, item))
    return value


def _extract_block(text, path):
    at = text.find(MARKER)
    if at < 0:
        _fail(path, "缺机读声明块（标记 %s）" % MARKER)
    start = text.find("\n", at)
    if start < 0:
        _fail(path, "机读声明块标志行后没有内容")
    end = text.find("\n" + FENCE, start + 1)
    if end < 0:
        _fail(path, "机读声明块没有闭合围栏（%s）" % FENCE)
    block = text[start + 1:end]
    if block.lstrip().startswith(FENCE):
        _fail(path, "机读声明块为空")
    return block


def _validate(data, path):
    if not isinstance(data, dict):
        _fail(path, "机读声明必须是 JSON 对象")
    _req(data, _TOP_KEYS, path, "契约")
    if data["contract"] != "host-contract":
        _fail(path, "契约名必须是 'host-contract'，实际 %r" % data["contract"])
    if not isinstance(data["version"], int):
        _fail(path, "version 必须是整数")

    hosts = data["hosts"]
    if not isinstance(hosts, dict) or not hosts:
        _fail(path, "hosts 必须是非空对象")
    for hid, h in hosts.items():
        _req(h, ("label", "evidence"), path, "宿主 %s" % hid)

    drv = _req(data["driver"], ("current", "current_why", "evidence"), path, "driver")
    if drv["current"] not in hosts:
        _fail(path, "driver.current=%r 未在 hosts 声明" % drv["current"])
    _list(drv["evidence"], path, "driver.evidence")
    for ev in drv["evidence"]:
        _req(ev, ("fact", "source"), path, "evidence 条目")

    _req(data["paths"], ("current_rule", "slug_rule", "converter"), path, "paths")

    kinds = data["syntax_kinds"]
    _ids(kinds, path, "syntax_kinds")

    stages = data["stages"]
    _list(stages, path, "stages")
    stage_ids = []
    for st in stages:
        _req(st, _STAGE, path, "阶段")
        sid = st["id"]
        if not isinstance(sid, str) or not re.match(r"^s\d{2}$", sid):
            _fail(path, "阶段 id 必须形如 s01：%r" % sid)
        if sid in stage_ids:
            _fail(path, "阶段 id 重复：%s" % sid)
        stage_ids.append(sid)
        if st["host"] not in hosts:
            _fail(path, "%s.host=%r 未在 hosts 声明" % (sid, st["host"]))
        _ids(st["syntax"], path, "%s.syntax" % sid)
        for kind in st["syntax"]:
            if kind not in kinds:
                _fail(path, "%s.syntax 用了未声明语法 %r" % (sid, kind))
        _ids(st["services"], path, "%s.services" % sid, allow_empty=True)
        _ids(st["artifacts"], path, "%s.artifacts" % sid, allow_empty=True)

    shells = data["shells"]
    _list(shells, path, "shells")
    shell_ids = []
    for sh in shells:
        _req(sh, _SHELL, path, "shell")
        if sh["id"] in shell_ids:
            _fail(path, "shell id 重复：%s" % sh["id"])
        shell_ids.append(sh["id"])
        if sh["host"] not in hosts:
            _fail(path, "shell %s 的 host=%r 未声明" % (sh["id"], sh["host"]))

    services = data["services"]
    _list(services, path, "services")
    svc_ids = []
    for sv in services:
        _req(sv, _SERVICE, path, "服务")
        if sv["id"] in svc_ids:
            _fail(path, "服务 id 重复：%s" % sv["id"])
        svc_ids.append(sv["id"])
        if sv["side"] not in hosts:
            _fail(path, "服务 %s 的 side=%r 未声明" % (sv["id"], sv["side"]))

    artifacts = data["artifact_dirs"]
    _list(artifacts, path, "artifact_dirs")
    art_ids = []
    for art in artifacts:
        _req(art, _ARTIFACT, path, "产物目录")
        if art["id"] in art_ids:
            _fail(path, "产物目录 id 重复：%s" % art["id"])
        art_ids.append(art["id"])
        if not isinstance(art["path"], str) or not art["path"].strip():
            _fail(path, "目录 %s 缺 path（#151 两列合一后为单串）" % art["id"])
        _ids(art["writer"], path, "%s.writer" % art["id"])
        _ids(art["readers"], path, "%s.readers" % art["id"])
        if not isinstance(art["cleanable"], bool):
            _fail(path, "目录 %s 的 cleanable 必须是布尔值" % art["id"])
        if not isinstance(art["clean_rule"], str) or not art["clean_rule"].strip():
            _fail(path, "目录 %s 缺 clean_rule" % art["id"])

    for st in stages:
        for sid in st["services"]:
            if sid not in svc_ids:
                _fail(path, "阶段 %s 引用了未声明服务 %r" % (st["id"], sid))
        for aid in st["artifacts"]:
            if aid not in art_ids:
                _fail(path, "阶段 %s 引用了未声明目录 %r" % (st["id"], aid))

    return data


def load(path=None):
    """解析并校验契约文档；结果按路径缓存。失败抛 HostContractError。"""
    path = os.path.normpath(path or DOC_PATH)
    if path in _CACHE:
        return _CACHE[path]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError as e:
        _fail(path, "宿主契约文档不是 UTF-8：%s" % e)
    except OSError as e:
        _fail(path, "宿主契约文档读不到：%s" % e)
    data = None
    try:
        data = json.loads(_extract_block(text, path))
    except json.JSONDecodeError as e:
        _fail(path, "机读声明不是合法 JSON：%s" % e)
    data = _validate(data, path)
    _CACHE[path] = data
    return data


def contract():
    return load()


def hosts():
    return load()["hosts"]


def host(host_id):
    try:
        return hosts()[host_id]
    except KeyError:
        _fail(DOC_PATH, "未声明宿主 %r（已声明：%s）" % (host_id, ", ".join(hosts())))


def driver():
    return load()["driver"]


def current_host():
    return driver()["current"]


def is_current_host(detected):
    """守卫 #145 用：detected 宿主是否等于文档声明的当前执行宿主。"""
    return detected == current_host()


def paths():
    return load()["paths"]


def syntax_kinds():
    return load()["syntax_kinds"]


def stages():
    return load()["stages"]


def stage_ids():
    return [s["id"] for s in stages()]


def _by_id(items, value, what):
    for item in items:
        if item["id"] == value:
            return item
    _fail(DOC_PATH, "未声明%s %r" % (what, value))


def stage(stage_id):
    return _by_id(stages(), stage_id, "阶段")


def shells():
    return load()["shells"]


def shell(shell_id):
    return _by_id(shells(), shell_id, " shell")


def services():
    return load()["services"]


def service(service_id):
    return _by_id(services(), service_id, "服务")


def artifact_dirs():
    return load()["artifact_dirs"]


def artifact(artifact_id):
    return _by_id(artifact_dirs(), artifact_id, "产物目录")


def _usage(msg):
    """命令行用法错误与契约损坏分开报：用法错误不甩锅给文档，也不抛 HostContractError。"""
    print("host_contract: %s" % msg, file=sys.stderr)
    return 2


def _dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if not argv or argv[0] in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        cmd = argv[0]
        if cmd == "current":
            print(current_host())
        elif cmd == "stages":
            print(_dump([{"id": s["id"], "name": s["name"], "host": s["host"],
                          "syntax": s["syntax"]} for s in stages()]))
        elif cmd == "shells":
            print(_dump(shells()))
        elif cmd == "dirs":
            print(_dump([{"id": a["id"], "cleanable": a["cleanable"]} for a in artifact_dirs()]))
        elif cmd == "services":
            print(_dump(services()))
        elif cmd == "json":
            print(_dump(contract()))
        elif cmd == "stage" and len(argv) == 2:
            print(_dump(stage(argv[1])))
        elif cmd == "stage":
            return _usage("用法: stage <id>（如 stage s08）")
        else:
            return _usage("未知子命令 %r；可用 current/stages/shells/dirs/services/json/stage <id>"
                          % cmd)
        return 0
    except HostContractError as e:
        print("host_contract: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
