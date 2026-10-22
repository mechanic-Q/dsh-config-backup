#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atomicio.py — 换盘重试 / 容错读 JSON（#166）。

收口的是**会被并发读的那几个文件**：driver 的心跳（`Heartbeat._write`）、台账
（`Ledger._write` 与读旧值），以及查询侧的 `driver_status._load_json`。其余
`os.replace` 调用点（`run_pipeline` 的 stamp 迁移、`strata_h3_lane` 的单写
车道）没有并发读窗口，不在本票范围，故意不动。

为什么需要它：Windows 上 `os.replace` 要求目标文件此刻**没有别的句柄打开**
（ERROR_SHARING_VIOLATION → `PermissionError [WinError 5]`）。并发读者
（`run_pipeline --status`、`driver_status.collect`、人工 cat、杀毒/索引进程）
恰好落在窗口里就能让换盘失败。

注意失败的性质：**原子性没问题**（读者永远看不到半截 JSON），丢的是**可用性**——
  - 心跳写侧一次就放弃 → 心跳从此停更，--status 把活进程判成「卡住」（#166 假红）；
  - 读侧裸 open 直接抛 → 测试/--status 偶发红；
  - collect() 读到 None → 静默退回「日志 mtime」口径，判定依据说错但不报错。
所以修法落在「短暂互斥就退避重试」，而**不是**退回 in-place 写（那会重新引入
读到半截 JSON，是更坏的一类 bug）。

重试边界（读写两侧同款）：
  - 只重试 `PermissionError`——文件不存在、路径非法等是确定性错误，重试没有意义；
  - 总时长硬上限 `sum(RETRY_DELAYS)`，绝不无限循环：心跳线程不能被一次长期占用卡死；
  - 写侧最终放弃时先收拾 .tmp 现场再抛，调用方照旧自吞异常（心跳失败不拖垮 driver）；
  - 读侧最终放弃时**留一行 stderr**：重试耗尽与「文件本来就不存在」必须能分开，
    否则 #166 那种静默降级只是把窗口从毫秒放宽到秒级。
"""
import json
import os
import sys
import time

# 最多重试 4 次的退避间隔（秒）：合计 0.02+0.05+0.1+0.2 = 0.37s（连同首次尝试共 5 次）。
# 取这个量级是因为冲突窗口只有一次 os.replace 的耗时（毫秒级），读者持句柄的
# 时长也远小于一秒；再长就是在替「文件被长期占用」白白烧 cpu。
RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2)


def _retry(fn, on_exhausted):
    """跑 fn()；撞上 PermissionError 就按 RETRY_DELAYS 退避重试。

    预算耗尽后最后一次失败交给 on_exhausted(exc)：它的返回值即本函数的返回值，
    它也可以重新抛出。读写两侧共用这一副骨架，差别只在「放弃时做什么」。
    """
    for delay in RETRY_DELAYS:
        try:
            return fn()
        except PermissionError:
            time.sleep(delay)
    try:
        return fn()
    except PermissionError as exc:
        return on_exhausted(exc)


def _remove(path, why):
    """收拾临时文件现场；清理失败只留痕（别把清理异常盖过原始错误）。"""
    try:
        os.remove(path)
    except OSError as exc:
        print(f"[atomicio] {why}失败（{path}）: {exc}", file=sys.stderr)


def replace_with_retry(src, dst):
    """os.replace 的短退避版本：目标被短暂占用时重试，耗尽则清理 .tmp 并抛原始异常。"""
    def _try():
        os.replace(src, dst)
        return True

    def _give_up(exc):
        _remove(src, "重试耗尽，临时文件清理")
        raise exc

    _retry(_try, _give_up)


def load_json(path):
    """容错读 JSON：文件暂被占用时重试；不存在/坏掉返回 None（不抛）。

    重试耗尽时留一行 stderr——这是与「文件本来就没有」唯一可区分的地方，
    必须看得见，否则 collect() 退回日志 mtime 口径依然是静默的。
    """
    def _try():
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None

    def _give_up(exc):
        print(f"[atomicio] 读 {path} 重试 {len(RETRY_DELAYS)} 次仍被占用（{exc}），"
              f"按读不到处理", file=sys.stderr)
        return None

    try:
        return _retry(_try, _give_up)
    except Exception:  # noqa: BLE001 — 不存在/坏 JSON 都是「读不到」
        return None
