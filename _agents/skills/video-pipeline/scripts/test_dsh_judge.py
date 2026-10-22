#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dsh_judge.py — dsh_judge 单测（T14 #126 验收④）。

四条必测路径（全部密封，不碰真模型/真判官）：
  1) 成功解析   —— 假子进程吐 final 事件 → items 归一正确、usage 计得
  2) 超时       —— 假子进程挂起 → ok=False + 超时文案，不抛错
  3) 输出不合法 —— final 无 JSON 数组 / 无输出 → ok=False + 解析失败文案，不抛错
  4) 运行体缺失 —— EXE 指向不存在 / DRIVER_JUDGE=0 → ok=False + 缺失/关闭文案，不抛错
另测：跨侧路径口径（to_win_path / build_prompt）、parse_final 容错与漏判、
merge 四象限 + reburn 名单、垃圾超时 env 不炸导入、selftest 通道故障不抛错、
status CLI 退出码。

直跑：python scripts/test_dsh_judge.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dsh_judge as dj

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("PASS %s" % name)
    else:
        print("FAIL %s %s" % (name, detail))
        FAILS.append(name)


class _Patch(object):
    """临时改模块属性，用完还原。"""

    def __init__(self, target, **kw):
        self.t = target
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(self.t, k)
            setattr(self.t, k, v)
        return self.t

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(self.t, k, v)
        return False


def fake_child(final_text=None, usage=None, hang=0.0):
    """生成一段 python -c 子进程代码：按 headless 事件流格式吐 stdout。"""
    lines = ["import json,sys,time"]
    if hang:
        lines.append("time.sleep(%r)" % hang)
    if usage:
        lines.append("print(json.dumps({'type':'status','usage':%s}), flush=True)" % json.dumps(usage))
    if final_text is not None:
        lines.append("print(json.dumps({'type':'final','text':%s}), flush=True)" % json.dumps(final_text))
    return [sys.executable, "-X", "utf8", "-c", "; ".join(lines)]


ROWS = [{"sid": "shot01", "verdict": "翻转90", "conf": 0.8, "frame": "/mnt/e/x/shot01.jpg"}]


# 判官运行体在盘是 available() 的前提（judge() 第一行就查）。前三节测的是解析/超时/
# 输出合法性，不是「运行体在不在盘」——所以必须把 EXE 指向真实存在的解释器，
# 否则任何宿主（CI 的 ubuntu 尤其）都会在 available() 就短路返回。
# 第 4 节 t_missing_exe 反向利用这一点，测的就是缺失路径，故不套本上下文。
_REAL_EXE = _Patch(dj, EXE_WIN=sys.executable, EXE_WSL=sys.executable)


# ── 1) 成功解析 ──────────────────────────────────────────────────────────
def t_parse_ok():
    fake = fake_child('[{"shot":"shot1","judge":"翻转90","conf":0.9,"local_ok":false,"why":"横躺"}]',
                      usage={"inputTokens": 100, "outputTokens": 10, "reasoningTokens": 5})
    with _Patch(dj, judge_cmd=lambda prompt: fake), _REAL_EXE:
        r = dj.judge(ROWS, timeout=60)
    check("成功解析 ok=True", r["ok"] is True, r.get("error", ""))
    check("成功解析 sid 归一 shot1→shot01", r["items"] and r["items"][0]["sid"] == "shot01", str(r["items"]))
    check("成功解析 标签归一", r["items"] and r["items"][0]["judge"] == "翻转90")
    check("成功解析 usage 计得", (r["in_tok"], r["out_tok"], r["reasoning_tok"]) == (100, 10, 5),
          str((r["in_tok"], r["out_tok"], r["reasoning_tok"])))
    check("成功解析 不抛错且有 raw", "横躺" in (r["raw"] or ""))


# ── 2) 超时 ──────────────────────────────────────────────────────────────
def t_timeout():
    fake = fake_child(hang=8.0)
    with _Patch(dj, judge_cmd=lambda prompt: fake), _REAL_EXE:
        r = dj.judge(ROWS, timeout=0.5)
    check("超时 ok=False", r["ok"] is False)
    check("超时 文案带超时", "超时" in (r.get("error") or ""), r.get("error", ""))
    check("超时 items 空", r["items"] == [])
    check("超时 不抛错（函数返回即证）", True)


# ── 3) 输出不合法 ────────────────────────────────────────────────────────
def t_bad_output():
    # 3a final 是自由文本，无 JSON 数组
    fake = fake_child("抱歉，我看不到图片，无法判定。")
    with _Patch(dj, judge_cmd=lambda prompt: fake), _REAL_EXE:
        r = dj.judge(ROWS, timeout=60)
    check("输出不合法 ok=False", r["ok"] is False)
    check("输出不合法 文案带解析失败", "解析失败" in (r.get("error") or ""), r.get("error", ""))
    # 3b 无 final 事件
    fake2 = fake_child(None)
    with _Patch(dj, judge_cmd=lambda prompt: fake2), _REAL_EXE:
        r2 = dj.judge(ROWS, timeout=60)
    check("无输出 ok=False", r2["ok"] is False)
    check("无输出 有 error 文案", bool(r2.get("error")), "error 为空")


# ── 4) 运行体缺失 / 判官层关闭 ──────────────────────────────────────────
def t_missing_exe():
    with _Patch(dj, EXE_WIN="E:/no/such/DeepSeek Harness.exe",
                EXE_WSL="/no/such/DeepSeek Harness.exe"):
        r = dj.judge(ROWS, timeout=60)
        ok, reason = dj.available()
    check("运行体缺失 ok=False", r["ok"] is False)
    check("运行体缺失 文案", "运行体缺失" in (r.get("error") or ""), r.get("error", ""))
    check("运行体缺失 available()=False", ok is False and "运行体缺失" in reason, reason)


def t_disabled():
    old = os.environ.get(dj.ENABLE_ENV)
    os.environ[dj.ENABLE_ENV] = "0"
    try:
        r = dj.judge(ROWS, timeout=60)
        check("判官层关闭 ok=False", r["ok"] is False)
        check("判官层关闭 文案", "关闭" in (r.get("error") or ""), r.get("error", ""))
    finally:
        if old is None:
            os.environ.pop(dj.ENABLE_ENV, None)
        else:
            os.environ[dj.ENABLE_ENV] = old


# ── 跨侧路径口径（验收②）───────────────────────────────────────────────
def t_cross_side_paths():
    p = dj.to_win_path("/mnt/e/video/_out/shot05.jpg")
    check("to_win_path /mnt/e → E:", p == "E:" + chr(92) + "video" + chr(92) + "_out" + chr(92) + "shot05.jpg", p)
    p2 = dj.to_win_path("/mnt/c/Users/x.jpg")
    check("to_win_path /mnt/c → C:", p2.startswith("C:"), p2)
    p3 = dj.to_win_path("E:" + chr(92) + "already" + chr(92) + "win.jpg")
    check("to_win_path Windows 口径原样", p3.endswith("win.jpg") and chr(92) in p3, p3)
    # 反方向：Windows 口径 → 本机口径（WSL 是 /mnt/<盘>/…；Windows 原样；#140）
    w = "E:" + chr(92) + "video" + chr(92) + "shot05.jpg"
    h = dj.to_host_path(w)
    if dj._in_wsl():
        check("to_host_path E: → /mnt/e/", h == "/mnt/e/video/shot05.jpg", h)
    else:
        check("to_host_path Windows 口径原样", h == w, h)
    check("to_host_path 与 to_win_path 互逆", dj.to_win_path(h) == w, dj.to_win_path(h))
    rows = [{"sid": "shot05", "verdict": "正常", "conf": 0.9, "frame": "/mnt/e/a/b.jpg"}]
    prompt = dj.build_prompt(rows)
    check("build_prompt 帧路径转成 Windows 口径", ("E:" + chr(92) + "a" + chr(92) + "b.jpg") in prompt)
    check("build_prompt 不残留 /mnt/", "/mnt/" not in prompt)
    check("build_prompt 含段号与判定", "shot05=正常" in prompt)


# ── parse_final 细节 ────────────────────────────────────────────────────
def t_parse_final():
    items = dj.parse_final('前言 [ {"shot":"shot02","judge":"倒立","conf":0.7} ] 后记',
                           expect=("shot02",))
    check("parse_final 容忍围栏与杂字", items[0]["judge"] == "翻转180" and items[0]["conf"] == 0.7)
    for bad, name in [("完全没有数组", "无数组"), ("[1,2,3]", "非对象数组")]:
        try:
            dj.parse_final(bad)
            check("parse_final %s 应抛" % name, False)
        except ValueError:
            check("parse_final %s 抛 ValueError" % name, True)
    try:
        dj.parse_final('[{"shot":"shot01","judge":"正常"}]', expect=("shot01", "shot02"))
        check("parse_final 漏判应抛", False)
    except ValueError as e:
        check("parse_final 漏判 抛 ValueError", "漏判" in str(e))
    out = dj.parse_final('[{"shot":"shot3","judge":"tilt","conf":None}]'.replace("None", "null"))
    check("parse_final shot3→shot03 + tilt→倾斜", out[0]["sid"] == "shot03" and out[0]["judge"] == "倾斜")


# ── merge 四象限 + 重烧名单（#132 拍板：判官为准）───────────────────────
def t_merge():
    items = [
        {"sid": "shot01", "judge": "翻转90", "conf": 0.9, "why": "a"},
        {"sid": "shot02", "judge": "翻转90", "conf": 0.8, "why": "b"},
        {"sid": "shot03", "judge": "正常", "conf": 0.9, "why": "c"},
        {"sid": "shot04", "judge": "正常", "conf": 0.9, "why": "d"},
    ]
    local = {"shot01": "翻转90", "shot02": "正常", "shot03": "翻转90", "shot04": "正常"}
    m = dj.merge(local, items, round_tag="judge1")
    check("merge 确认翻转", m["confirmed"] == ["shot01"])
    check("merge 判官新发现", m["judge_found"] == ["shot02"])
    check("merge 本地误报", m["local_false"] == ["shot03"])
    check("merge 一致正常", m["judge_normal"] == ["shot04"])
    check("merge 行带轮次", all(r.get("round") == "judge1" for r in m["rows"]))
    check("reburn 名单 = 确认+新发现", dj.reburn_list(m) == ["shot01", "shot02"])
    s = dj.summarize(m)
    check("summarize 一行摘要", "确认翻转 1" in s and "判官新发现 1" in s
          and "本地误报 1" in s and "一致正常 1" in s)


# ── 垃圾超时 env 不炸导入（新增容错）────────────────────────────────────
def t_env_timeout_garbage():
    env = dict(os.environ)
    env["DRIVER_JUDGE_TIMEOUT"] = "abc"
    env["PYTHONUTF8"] = "1"
    r = subprocess.run([sys.executable, "-c", "import dsh_judge; print(dsh_judge.TIMEOUT)"],
                       capture_output=True, text=True, cwd=HERE, env=env,
                       encoding="utf-8", errors="replace", timeout=30)
    check("垃圾超时 env 导入不炸", r.returncode == 0, r.stderr[-160:])
    out_lines = [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]
    check("垃圾超时 env 回落 900.0", bool(out_lines) and out_lines[-1] == "900.0", r.stdout[-200:])


# ── selftest 通道故障不抛错（新增容错）──────────────────────────────────
def t_selftest_never_raises():
    fake = fake_child(hang=8.0)
    with _Patch(dj, judge_cmd=lambda prompt: fake):
        try:
            rc, txt, wall = dj._run_channel("回答两个字符：OK", timeout=0.5)
            check("selftest 超时返回失败不抛错", rc != 0 and txt == "", "rc=%r" % rc)
        except Exception as e:  # noqa: BLE001
            check("selftest 超时返回失败不抛错", False, "抛了 %s: %s" % (type(e).__name__, e))
    fake2 = fake_child(None)
    with _Patch(dj, judge_cmd=lambda prompt: fake2):
        try:
            rc2, txt2, _ = dj._run_channel("回答两个字符：OK", timeout=30)
            check("selftest 无输出返回失败不抛错", rc2 != 0 or "OK" not in txt2)
        except Exception as e:  # noqa: BLE001
            check("selftest 无输出返回失败不抛错", False, "抛了 %s" % type(e).__name__)


# ── 标签契约对齐（#126 评审定案：单一源 vision_common.JUDGE_LEVELS）─────
def t_docstring():
    import vision_common
    check("判官词表单一源", dj._JUDGE_LEVELS is vision_common.JUDGE_LEVELS)
    check("四标签契约", list(dj._JUDGE_LEVELS)
          == ["正常", "翻转90", "翻转180", "倾斜"])
    check("norm_judge 倾斜90度→倾斜",
          dj.norm_judge("画面倾斜90度") == dj.LABEL_TILT)


# ── status CLI（验收⑤：一条命令查询可用性）─────────────────────────────
def t_status_cli():
    env = dict(os.environ)
    env[dj.ENABLE_ENV] = "0"
    env["PYTHONUTF8"] = "1"
    r = subprocess.run([sys.executable, os.path.join(HERE, "dsh_judge.py"), "status"],
                       capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace", timeout=30)
    check("status 关闭时 rc=1", r.returncode == 1, "rc=%s out=%r" % (r.returncode, r.stdout[:120]))
    check("status 打印可用性行", ("判官可用" in r.stdout) and ("False" in r.stdout), r.stdout[:160])
    check("status 打印 EXE/CLI 行", ("EXE:" in r.stdout) and ("CLI:" in r.stdout))


def main():
    for fn in (t_parse_ok, t_timeout, t_bad_output, t_missing_exe, t_disabled,
               t_cross_side_paths, t_parse_final, t_merge, t_env_timeout_garbage,
               t_selftest_never_raises, t_docstring, t_status_cli):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — 单测自身故障也要暴露
            import traceback
            traceback.print_exc()
            FAILS.append(fn.__name__ + "(异常)")
    print()
    if FAILS:
        print("test_dsh_judge: %d 项失败 → %s" % (len(FAILS), FAILS))
        return 1
    print("test_dsh_judge: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
