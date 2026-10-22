#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ledger.py — 台账 module 单测（票7 #65，无框架 assert 直跑）。

覆盖：
1. 快照合并幂等（原 test_run_pipeline test2 断言原样移植）：同进程双写不翻倍、
   跨重启不丢旧阶段、顶层质量块保留、gate_replies 拼接去重、started_at 取最早
2. record 语义：点路径置值 / 整块替换 / add 累加（attempts、errors、tokens）
3. record_event：追加；flush 与盘上旧列表拼接去重
4. get 双视图：increment=本进程增量；merged=盘上旧值⊕增量
   （bug① 回归：恢复读顶层 s08.vision，不再错读 stages.s08.vision）
5. stages 合并细节：errors 末 10 截断、tokens 跨重启相加（原零覆盖）
6. flush 容错：旧文件损坏回退本进程数据、目标目录缺失自吞异常；.tmp 原子替换
7. summary：质量块=增量口径、stages=快照+增量口径
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ledger import Ledger

# ── 1. 快照合并幂等（原 test_run_pipeline test2 断言原样移植）────────────
tmp2 = tempfile.mkdtemp()
fp = os.path.join(tmp2, "report.json")
# 既往台账（上一进程留下：s01 完成、s03 有旧统计、顶层质量块）
json.dump({"slug": "t", "started_at": "2026-09-08 10:00:00",
           "stages": {"s01": {"attempts": 1, "seconds": 9.0, "errors": []},
                      "s03": {"attempts": 2, "seconds": 5.0, "errors": []}},
           "s07": {"total": 20, "ok": 20},
           "gate_replies": [{"gate": "4", "reply": "通过"}]},
          open(fp, "w"))

lg = Ledger(fp, meta={"slug": "t", "started_at": "2026-09-08 11:00:00"}, t0=0.0)
lg.record("stages.s01", {"attempts": 1, "seconds": 1.0, "errors": [],
                         "tokens": {"prompt": 10, "completion": 5, "calls": 1}}, add=True)
lg.flush()
r1 = json.load(open(fp))
# 总量 = 快照 + 增量：s01 attempts 1+1=2、seconds 9+1=10、tokens 只加增量
assert r1["stages"]["s01"]["attempts"] == 2, r1["stages"]["s01"]
assert abs(r1["stages"]["s01"]["seconds"] - 10.0) < 1e-6
assert r1["stages"]["s01"]["tokens"]["prompt"] == 10
# 上一进程 s03 保留；顶层质量块保留
assert r1["stages"]["s03"]["attempts"] == 2
assert r1["s07"] == {"total": 20, "ok": 20}
assert len(r1["gate_replies"]) == 1
assert r1["started_at"] == "2026-09-08 10:00:00", "started_at 保留最早"
# 同进程再写一次（含新阶段 s02）：不翻倍（幂等关键——合并与盘上现状无关）
lg.record("stages.s02", {"attempts": 1, "seconds": 3.0, "errors": []}, add=True)
lg.flush()
r2 = json.load(open(fp))
assert r2["stages"]["s01"]["attempts"] == 2, "同进程双写翻倍"
assert r2["stages"]["s02"]["attempts"] == 1
assert r2["s07"] == {"total": 20, "ok": 20}
# gate_replies 追加去重（与盘上旧值按值相等去重）
lg.record_event("gate_replies", {"gate": "4", "reply": "通过"})
lg.flush()
r3 = json.load(open(fp))
assert len(r3["gate_replies"]) == 1, r3["gate_replies"]
# 跨重启（新进程新快照）：旧值不丢、新进程从新基线累加
lg2 = Ledger(fp, t0=0.0)
lg2.record("stages.s01", {"attempts": 1, "seconds": 2.0, "errors": []}, add=True)
lg2.flush()
r4 = json.load(open(fp))
assert r4["stages"]["s01"]["attempts"] == 3, r4["stages"]["s01"]
assert r4["stages"]["s02"]["attempts"] == 1
assert r4["started_at"] == "2026-09-08 10:00:00"
print("test_ledger: 快照合并幂等 OK")

# ── 2. record 语义 ────────────────────────────────────────────────────
tmp3 = tempfile.mkdtemp()
lg3 = Ledger(os.path.join(tmp3, "report.json"), t0=0.0)
lg3.record("slug", "abc")                                   # 裸键=顶层元数据
lg3.record("s04", {"lane": "compressed", "ok": 2})          # 整块替换
lg3.record("s04.move_dist", {"横移": 3})                    # 嵌段子键（块自动建）
assert lg3.get("slug") == "abc" and lg3.get("s04.move_dist") == {"横移": 3}
lg3.record("s04", {"lane": "legacy"})                       # 整块替换覆盖旧值
assert lg3.get("s04") == {"lane": "legacy"}
assert lg3.get("s04.move_dist") is None
# add 累加：数值相加 / 列表拼接 / dict 递归 / 字符串覆盖 / 首次建块
lg3.record("stages.s01", {"title": "t", "attempts": 1, "errors": ["e1"],
                          "tokens": {"prompt": 5, "calls": 1}}, add=True)
lg3.record("stages.s01", {"title": "t", "attempts": 1, "seconds": 2.5,
                          "errors": ["e2"], "tokens": {"prompt": 7}}, add=True)
st = lg3.get("stages.s01")
assert st["attempts"] == 2 and st["errors"] == ["e1", "e2"], st
assert st["tokens"] == {"prompt": 12, "calls": 1}, st
assert st["seconds"] == 2.5 and st["title"] == "t"
print("test_ledger: record 语义 OK")

# ── 3. merged 视图 + bug① 回归（恢复读顶层 s08.vision）──────────────────
tmp4 = tempfile.mkdtemp()
fp4 = os.path.join(tmp4, "report.json")
# 上一进程（断点续跑前）：vision 落盘在顶层 s08 块——旧磁盘回退错读
# stages.s08.vision（stages.s08 只有计账），恢复恒空。merged 视图必须命中。
json.dump({"stages": {"s08": {"title": "H3", "attempts": 1, "seconds": 9.0, "errors": []}},
           "s08": {"segments": 16, "vision": {"checked": 16,
                                              "judge_failed": ["shot03:timeout"]}}},
          open(fp4, "w"))
lg4 = Ledger(fp4, t0=0.0)
assert lg4.get("s08.vision.judge_failed", view="merged") == ["shot03:timeout"], "bug① 恢复"
assert lg4.get("stages.s08.attempts", view="merged") == 1
assert lg4.get("s08.vision.judge_failed") is None, "increment 视图不回捞盘上旧值"
# 增量优先：本进程有 vision → 增量赢（同进程门④读到当前值）
lg4.record("s08.vision", {"checked": 16, "judge_failed": []})
assert lg4.get("s08.vision.judge_failed", view="merged") == []
# 增量块缺 vision 子键时，盘上旧 vision 存活（浅合并，增量赢在块级键上）
lg4b = Ledger(fp4, t0=0.0)
lg4b.record("s08.still_flipped", [])
mv = lg4b.get("s08", view="merged")
assert mv["vision"]["judge_failed"] == ["shot03:timeout"] and mv["still_flipped"] == []
print("test_ledger: merged 视图 + bug① 回归 OK")

# ── 4. stages 合并细节：errors 末 10 截断 / tokens 跨重启相加 ─────────────
tmp5 = tempfile.mkdtemp()
fp5 = os.path.join(tmp5, "report.json")
json.dump({"stages": {"s01": {"attempts": 3, "seconds": 30.0, "errors": [f"old{i}" for i in range(8)],
                              "tokens": {"prompt": 100, "completion": 50, "calls": 2}}}},
          open(fp5, "w"))
lg5 = Ledger(fp5, t0=0.0)
lg5.record("stages.s01", {"attempts": 1, "seconds": 5.0,
                          "errors": [f"new{i}" for i in range(7)],
                          "tokens": {"prompt": 10, "completion": 5, "calls": 1}}, add=True)
lg5.flush()
r5 = json.load(open(fp5))["stages"]["s01"]
assert r5["attempts"] == 4 and abs(r5["seconds"] - 35.0) < 1e-6
assert r5["errors"] == [f"old{i}" for i in range(5, 8)] + [f"new{i}" for i in range(7)], "末 10 截断"
assert r5["tokens"] == {"prompt": 110, "completion": 55, "calls": 3}, "tokens 跨重启相加"
print("test_ledger: errors 截断 + tokens 跨重启 OK")

# ── 5. flush 容错 + 原子写 ────────────────────────────────────────────
# 旧文件损坏：放弃合并、本进程数据照常落盘
tmp6 = tempfile.mkdtemp()
fp6 = os.path.join(tmp6, "report.json")
open(fp6, "w").write("{corrupt json")
lg6 = Ledger(fp6, meta={"slug": "t"}, t0=0.0)
lg6.record("s07", {"total": 1, "ok": 1})
lg6.flush()
r6 = json.load(open(fp6))
assert r6["s07"] == {"total": 1, "ok": 1} and "stages" in r6
# 目标目录缺失：自吞异常不炸
lg6b = Ledger(os.path.join(tmp6, "no_such_dir", "report.json"), t0=0.0)
lg6b.record("s07", {"total": 1, "ok": 1})
lg6b.flush()  # 不抛即通过
# 原子写：落盘后无 .tmp 残留
assert not os.path.exists(fp6 + ".tmp"), "原子写不得残留 .tmp"
print("test_ledger: flush 容错 + 原子写 OK")

# ── 6. summary：质量块增量口径 / stages 快照+增量口径 ────────────────────
tmp7 = tempfile.mkdtemp()
fp7 = os.path.join(tmp7, "report.json")
json.dump({"stages": {"s01": {"attempts": 2, "seconds": 20.0, "errors": []}},
           "gate_sheet": True}, open(fp7, "w"))
lg7 = Ledger(fp7, t0=0.0)
lg7.record("s04", {"lane": "compressed", "ok": 16, "total": 16})
lg7.record("stages.s01", {"attempts": 1, "seconds": 3.0, "errors": []}, add=True)
s = lg7.summary()
assert s["s04"] == {"lane": "compressed", "ok": 16, "total": 16}
assert s["stages"]["s01"]["attempts"] == 3, "stages 走快照+增量口径"
assert s.get("gate_sheet") is None, "未在本进程 record 过的块不进增量口径"
lg7.flush()
assert json.load(open(fp7))["gate_sheet"] is True, "flush 后盘上旧块仍保留"
print("test_ledger: summary OK")

print("test_ledger: 全部通过")
