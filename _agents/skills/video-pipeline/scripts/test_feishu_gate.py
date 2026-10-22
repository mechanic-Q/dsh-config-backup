#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feishu_gate.py — #49 门逐步成功日志自检（assert 直跑，风格同 test_preflight）。

全程 dry + 桩，不出网、不发飞书。覆盖：
1. dry 跑一次门（2图+1文件+正文）：每个包发送成功各打一行（含序号/文件名/大小），
   最后一行轮询含窗口起点与截止时刻（HH:MM:SS）
2. 轮询期间：收到回复打一行判定并返回；到截止无回复打 timeout 行；轮询连续失败到上限打 error 行
3. 失败行为不变：发送 code!=0 不打成功行（原有失败打印保持）且汇总如实计数；strict 上传失败照旧抛
4. 群历史轮询的时间单位（#125 AC3）：start_time/end_time 必须是 Unix 秒且起点不在未来
5. 环境变量凭证也回落确认门群常量（#125 review）：走 FEISHU_APP_ID/SECRET 而未设
   FEISHU_GATE_CHAT_ID 时，群号须与 channels.json 分支同口径回落 GATE_CHAT_ID
"""
import contextlib
import io
import os
import re
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import feishu_gate

# 凭证桩（同 test_preflight 惯用法）：不依赖 ~/.hermes/.env
_orig_load_env = feishu_gate._load_env
try:
    feishu_gate._load_env = lambda: {
        "FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b", "FEISHU_DAILY_NEWS_CHAT_ID": "c"}

    def mk_gate(dry=True):
        return feishu_gate.FeishuGate(dry=dry)

    tmp = tempfile.mkdtemp()
    img1 = os.path.join(tmp, "封面_bilibili.png")
    img2 = os.path.join(tmp, "封面_douyin.png")
    big = os.path.join(tmp, "最终成片_预览版.mp4")
    open(img1, "wb").write(b"x" * 300 * 1024)          # KB 档
    open(img2, "wb").write(b"x" * 1024)
    open(big, "wb").write(b"x" * (2 * 1024 * 1024))    # 2MB → MB 档

    # ── 1. dry 门：逐条成功行 + 轮询起止行 ─────────────────────────────
    since = int(time.time() * 1000) - 5000
    g = mk_gate()
    g.latest_user_texts = lambda since_ms: []  # 轮询桩：永不回复
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        t0 = time.time()
        r = g.gate_confirm(caption="正文内容一二三", image_path=[img1, img2],
                           file_path=big, since_ms=since, timeout_s=0.3, poll_s=0.05)
    out = buf.getvalue()
    assert r["status"] == "timeout", r
    assert r["action"] == "abort", r  # 票8 #67：timeout → abort
    assert time.time() - t0 < 30, "timeout 0.3s 的门不应久等"
    for frag in ("1/4", "2/4", "3/4", "4/4", "已发送",
                 "封面_bilibili.png", "最终成片_预览版.mp4", "2.0MB", "7 字"):
        assert frag in out, (frag, out)
    assert out.count("已发送") == 4, f"应逐条 4 行成功日志，不是汇总: {out}"
    # 失败行不应出现
    assert "发送失败" not in out, out
    # 轮询行：起/截止时刻 HH:MM:SS，截止-起 ≈ timeout_s（不再混入上传耗时）；
    # since 生效线在轮询开始之前（driver 在发送前就取 since）
    poll_lines = [ln for ln in out.splitlines() if "开始轮询" in ln and "截止" in ln]
    assert len(poll_lines) == 1, out
    assert "4/4 成功" in poll_lines[0], poll_lines[0]
    m = re.search(r"起 (\d{2}:\d{2}:\d{2})、(\d{2}:\d{2}:\d{2}) 之后的新回复计入、"
                  r"截止 (\d{2}:\d{2}:\d{2})", poll_lines[0])
    assert m, poll_lines[0]
    secs = [int(h[:2]) * 3600 + int(h[3:5]) * 60 + int(h[6:]) for h in m.groups()]
    gap = (secs[2] - secs[0]) % 86400
    assert abs(gap - 0.3) < 2, f"截止-起应≈timeout_s: {poll_lines[0]}"
    lead = (secs[0] - secs[1]) % 86400
    assert abs(lead - 5.0) < 2, f"轮询起-since 应≈5s: {poll_lines[0]}"

    # ── 2. 轮询收到回复：判定行 + 正常返回 ─────────────────────────────
    g2 = mk_gate()
    g2.latest_user_texts = lambda since_ms: [(int(time.time() * 1000), "通过")]
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        r2 = g2.gate_confirm(caption="cap", timeout_s=5, poll_s=0.01)
    assert r2["status"] == "pass" and r2["reply"] == "通过", r2
    assert r2["action"] == "pass", r2  # 票8 #67：pass → pass
    out2 = buf2.getvalue()
    assert "收到回复" in out2 and "pass" in out2, out2

    # 2c. 轮询连续失败到上限：打 error 行并返回 error（#49 前这段最长静默期无日志）
    g5 = mk_gate()

    def always_fail(since_ms):
        raise RuntimeError("历史消息拉取失败 999 mock")

    g5.latest_user_texts = always_fail
    buf5 = io.StringIO()
    with contextlib.redirect_stdout(buf5):
        r5 = g5.gate_confirm(timeout_s=60, poll_s=0.001)
    assert r5["status"] == "error" and "mock" in r5["reply"], r5
    assert r5["action"] == "abort", r5  # 票8 #67：error → abort
    assert "连续失败 20 次" in buf5.getvalue() and "error" in buf5.getvalue(), buf5.getvalue()

    # ── 3. 失败行为不变 ────────────────────────────────────────────────
    # 3a. 发送 code!=0：不打成功行，原有失败打印保持（非 dry 走真 _send 失败分支）
    g3 = mk_gate(dry=False)
    g3.latest_user_texts = lambda since_ms: []
    g3._api = lambda *a, **k: {"code": 9, "msg": "mock 失败"}
    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        r3 = g3.gate_confirm(caption="hi", timeout_s=0.05, poll_s=0.01)
    out3 = buf3.getvalue()
    assert r3["status"] == "timeout", r3
    assert "已发送" not in out3, out3
    assert "发送失败 code=9" in out3, out3
    assert "0/1 成功" in out3, f"汇总须如实计数: {out3}"

    # 3b. strict 上传失败：照旧抛 RuntimeError，无成功行（非 dry 才走 _upload）
    g4 = mk_gate(dry=False)

    def boom_upload(path, api, fields, file_field):
        return {"code": 9, "msg": "boom"}

    g4._upload = boom_upload
    buf4 = io.StringIO()
    with contextlib.redirect_stdout(buf4):
        try:
            g4.gate_confirm(image_path=img1, timeout_s=0.05)
            raise AssertionError("strict 上传失败必须抛 RuntimeError")
        except RuntimeError as e:
            assert "上传失败" in str(e), str(e)
    assert "已发送" not in buf4.getvalue(), buf4.getvalue()

    # ── 4. decision 政策表（票8 #67 Q1）：gate_confirm 返回三动作 Decision ──
    # action 是门内消化政策表后的 driver 执行指令；status 仍记原始 6 值
    # （gate_replies 台账键形冻结）。传输异常路径（strict 上传失败抛 RuntimeError）
    # 不在本表——它不走 decision，由 run_stage 重试接住。
    def _reply_gate(text):
        gd = mk_gate()
        gd.latest_user_texts = lambda since_ms: [(int(time.time() * 1000), text)]
        return gd

    r = _reply_gate("通过").gate_confirm(timeout_s=5, poll_s=0.01)
    assert r["action"] == "pass" and r["status"] == "pass", r
    # shot_regen → retry 且带 shots（shotNN 格式化不变）
    r = _reply_gate("重生成 shot02").gate_confirm(timeout_s=5, poll_s=0.01)
    assert r["action"] == "retry" and r["shots"] == ["shot02"] and r["status"] == "shot_regen", r
    # regen（无段号）→ retry 无 shots 键（Q3：shots 空表 = 封面重做）
    r = _reply_gate("重生成").gate_confirm(timeout_s=5, poll_s=0.01)
    assert r["action"] == "retry" and r["status"] == "regen" and not r.get("shots"), r
    # feedback → abort
    r = _reply_gate("字幕太快了").gate_confirm(timeout_s=5, poll_s=0.01)
    assert r["action"] == "abort" and r["status"] == "feedback", r

    # ── 5. classify / parse_shot_ids 表驱动（票8 #67 Q4/Q6 + #69 收紧落定）──
    # 本节钉的是 0913 实证修复 + #69 两候选收紧后的行为；改匹配规则须连表一起改。
    g7 = mk_gate()
    for text, want in [
        ("", "feedback"), ("   ", "feedback"),
        ("通过", "pass"), ("OK", "pass"), ("👍", "pass"), ("可以", "pass"),
        ("通过。", "pass"), ("ok！", "pass"),
        ("通过 批准", "pass"), ("同意", "pass"),           # 0913 白名单
        ("横版通过 竖版通过", "pass"), ("都通过", "pass"),
        ("同意发布", "feedback"),                           # 「发布」不在白名单（现状）
        ("标题有点歪", "feedback"),
    ]:
        got = g7.classify(text)
        assert got == want, f"classify({text!r}) = {got}, want {want}"
    # regen 族：REGEN 词命中 → 有段号 shot_regen / 无段号 regen
    assert g7.classify("重生成") == "regen"
    assert g7.classify("重生成 shot02") == "shot_regen"
    assert g7.classify("重来 1-4") == "shot_regen"
    assert g7.classify("重生成 3、5-8") == "shot_regen"   # 顿号区间整行走 classify
    assert g7.classify("重做 SHOT 2") == "shot_regen"    # shot 前缀大小写不敏感（带 REGEN 词）
    # Q6 已知边界（现状钉住，收紧另开票）：含 REGEN 词的拒绝语会触发 regen
    assert g7.classify("标题别重生成") == "regen"
    # 裸 shotNN（#69 候选1收紧落定）：shot 前缀本身就是镜头指称，直接 shot_regen
    #（与 parse 无条件 shotNN 拾取同构，parse("SHOT 2")==[2] 从不要求 REGEN 词）
    assert g7.classify("SHOT 2") == "shot_regen"
    assert g7.classify("shot02") == "shot_regen"
    assert g7.classify("shot02 不行") == "shot_regen"
    # 词边界/成段守卫（#70 review 补钉）：内嵌 shot 不算指称；不成段的段号
    # 宁 feedback 叫人不误做——classify 判定与 gate_confirm 取 shots 同走
    # parse_shot_ids，判 shot_regen 时 shots 必非空
    assert g7.classify("screenshot 3 不错") == "feedback"
    assert g7.classify("shot100") == "feedback"
    assert g7.classify("shot0") == "feedback"
    # 裸数字仍守卫：无 REGEN 词不触发 shot_regen（防误伤普通聊天，#69 拍板不放开）
    assert g7.classify("2") == "feedback"
    assert g7.classify("第 3 集不错") == "feedback"
    # parse_shot_ids：格式 / 守卫
    assert g7.parse_shot_ids("重生成 shot02") == [2]
    assert g7.parse_shot_ids("SHOT 2") == [2]
    assert g7.parse_shot_ids("screenshot 3") == [], "词边界：内嵌 shot 不算镜头指称"
    assert g7.parse_shot_ids("shot100") == [], "右界 (?!\\d)：三位段号不截断误拾"
    assert g7.parse_shot_ids("shot0") == [], "值域守卫：shot0 不成段"
    assert g7.parse_shot_ids("shot010") == [10], "前导零段号照常拾取"
    # 列举链补拾（2026-10-20 事故钉死）：「shot01/05/08/12/14/19 是问题镜头」旧实现
    # 只拾首段 shot01，其余五段静默丢弃且 classify 仍判 shot_regen（静默少做，最危险）。
    # 斜杠 / 顿号 / 逗号三种列举分隔符都必须并入同一批指认。
    assert g7.parse_shot_ids("shot01/05/08/12/14/19 是问题镜头") == [1, 5, 8, 12, 14, 19]
    assert g7.classify("shot01/05/08/12/14/19 是问题镜头") == "shot_regen"
    assert g7.parse_shot_ids("shot01/05/08") == [1, 5, 8]
    assert g7.parse_shot_ids("shot01、05、08") == [1, 5, 8]
    assert g7.parse_shot_ids("shot 1 / 2 / 3 都翻") == [1, 2, 3]
    # 链内区间不被链式穷举（「shot01/2-4」里的 2 后面跟 -4 → 不进链，交给区间逻辑）
    assert g7.parse_shot_ids("shot01/2-4") == [1]
    # 链路守卫：链只跟在 shotNN 之后，纯裸数字串仍是原行为（防误伤普通聊天）
    assert g7.parse_shot_ids("聊天数字 7 号到货") == []
    assert g7.parse_shot_ids("shot01/05/123") == [1, 5], "链内三位不截断误拾"
    assert g7.parse_shot_ids("重生成 3、5-8") == [3, 5, 6, 7, 8]
    assert g7.parse_shot_ids("重来 1-4") == [1, 2, 3, 4]
    assert g7.parse_shot_ids("重做 3.5") == [], "小数邻位守卫"
    # 区间尝试整段语义（#69 候选2收紧落定）：越界/倒序区间整段丢弃，端点数字
    # 不再被裸数字路径部分拾起（0913 现状是 0-5→[5]、1-199→[1] 部分拾起）；
    # 同句里段外的孤立合法数字照常拾起
    assert g7.parse_shot_ids("重跑 0-5") == []
    assert g7.parse_shot_ids("重跑 1-199") == []
    assert g7.parse_shot_ids("重跑 5-0") == []
    assert g7.parse_shot_ids("重跑 0-5 7") == [7]
    assert g7.parse_shot_ids("重跑 0-5、1-4") == [1, 2, 3, 4]
    # classify 连带：REGEN 词在但段号全被屏蔽 → 无段号 → 兼容旧封面重做
    assert g7.classify("重跑 0-5") == "regen"
    assert g7.parse_shot_ids("你好") == [], "无 REGEN 词不启用裸数字解析"
    assert g7.parse_shot_ids("聊天数字 7 号到货") == [], "同上（防误伤普通聊天）"

    # ── 6. 群历史轮询的时间单位（#125 AC3）：飞书 start_time/end_time 只认 Unix 秒 ──
    # 根因（2026-10-16）：旧实现把 driver 传的毫秒戳直接塞进 start_time，窗口起点被
    # 推到未来 5 万年 → 前 8 次确认门轮询恒读 0 条、次次 timeout。
    # 本节钉的是一条与实现无关的性质：请求窗口必须落在「过去～现在」且以秒表达；
    # 单位回退成毫秒会立刻让起点远大于 now，从而被下面两条断言抓住。
    g8 = mk_gate()
    cap = {}

    def cap_api(method, path, **kw):
        cap["method"], cap["path"] = method, path
        return {"code": 0, "data": {"items": []}}

    since_ms = 1792147468059          # 实测毫秒样例（见 latest_user_texts 注释）
    now = time.time()
    g8._api = cap_api
    assert g8.latest_user_texts(since_ms) == []
    q = urllib.parse.parse_qs(urllib.parse.urlparse(cap["path"]).query)
    assert cap["method"] == "GET", cap
    st, et = int(q["start_time"][0]), int(q["end_time"][0])
    # 秒口径：10 位；毫秒口径是 13 位（旧 bug 的形状）
    assert len(str(st)) == 10 and len(str(et)) == 10, f"必须是 Unix 秒: st={st} et={et}"
    # 起点必须不在未来（旧 bug 的判别式：毫秒戳恒 > now）
    assert st <= now, f"窗口起点不能在未来（单位错误）：{st} > {now}"
    # 起点 = since_ms 的秒截断，误差只来自 fixture 与本行的取时差
    assert abs(st - (since_ms // 1000)) <= 1, (st, since_ms // 1000)
    assert abs(et - now) <= 5, f"end_time 应为当前秒: {et} vs {now}"
    # 出参保持毫秒口径（driver 的 since_ms 与台账同为 ms）：update_time 原样透传
    g8._api = lambda *a, **k: {"code": 0, "data": {"items": [
        {"msg_type": "text", "sender": {"sender_type": "user"},
         "update_time": "1792147468060", "body": {"content": '{"text":"通过"}'}},
        {"msg_type": "text", "sender": {"sender_type": "app"},
         "update_time": "1792147468061", "body": {"content": '{"text":"bot"}'}},
        {"msg_type": "image", "sender": {"sender_type": "user"},
         "update_time": "1792147468062", "body": {"content": "{}"}},
    ]}}
    assert g8.latest_user_texts(since_ms) == [(1792147468060, "通过")], "ms 出参 + 只留 user 的 text"

    # ── 7. 环境变量凭证也回落确认门群常量（#125 review 补钉）──
    # 走 FEISHU_APP_ID/SECRET 环境变量时若未设 FEISHU_GATE_CHAT_ID，须与 channels.json
    # 分支一致回落 GATE_CHAT_ID；否则 preflight 修复文案教的「或设环境变量」那条路
    # 必抛「FEISHU_GATE_CHAT_ID 缺失（确认门群未配置）」。
    _env_backup = dict(os.environ)
    try:
        os.environ["FEISHU_APP_ID"] = "a"
        os.environ["FEISHU_APP_SECRET"] = "b"
        os.environ.pop("FEISHU_GATE_CHAT_ID", None)
        got = _orig_load_env()
        assert got["FEISHU_DAILY_NEWS_CHAT_ID"] == feishu_gate.GATE_CHAT_ID, \
            "env 凭证未回落确认门群常量: " + repr(got.get("FEISHU_DAILY_NEWS_CHAT_ID"))
    finally:
        os.environ.clear()
        os.environ.update(_env_backup)
finally:
    feishu_gate._load_env = _orig_load_env

print("test_feishu_gate: 全部通过（#49 逐步成功日志 + 票8 decision/classify 表）")
