#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger.py — 台账 module（票7 #65）：driver 的 report.json 读写收口。

全库唯一读方/写方（s_gate_review 的磁盘直读已收编）。interface 五动词：

  record(path, value, add=False)  置值（点路径，如 "s04.move_dist" / "s08.vision"）；
                                  add=True 走累加语义——stages 计账专用（attempts/seconds
                                  相加、errors 拼接、tokens 逐字段相加、其余覆盖）
  record_event(path, item)        质量事件列表追加（gate_replies / enum_corrections）；
                                  去重发生在 flush 拼接时（与盘上旧列表按值相等去重）
  get(path, view="increment")     读。increment=本进程增量（run_stage 渲染、
                                  rotation_dataset 消费口径，与旧 self._report 直读一致）；
                                  merged=盘上旧值⊕本进程增量（跨重启恢复口径，仅
                                  s_gate_review 判读失败段点名使用）。缺值返回 None
  flush()                         合并落盘：幂等、原子（.tmp+os.replace）、自吞异常。
                                  返回即已落盘——#48 硬退出路径先 flush 再 os._exit 的
                                  时序承诺由同步语义保证
  summary()                       结构化摘要数据（stages=快照+增量合并口径，质量块=本进程
                                  增量口径）；文案渲染归调用方（Q8：车道措辞是门④政策）

落盘格式冻结（Q2）：与 run_pipeline.write_report 时代逐键一致。混合快照语义（Q12
原样保留）：stages=启动快照+本进程增量；顶层质量块=flush 时重读盘上旧文件浅合并。
不变量（0908 双计根治）：合并结果只进写盘通道，内存增量永不回写总量。

历史（docstring 搬自原 write_report，80c9a6d）：
  2026-09-08 根治两项：1) 深合并——此前内存全量覆盖写，driver 中途重启后早期阶段
  统计全部丢失（0907 期 s01-s08 统计丢失实锤），stages 外质量块（s07/s08/s09 对账、
  gate_replies）也不随 stages 合并而丢；2) 幂等——失败路径与 run().finally 各写一次，
  旧实现把合并结果写回内存再叠加，同阶段数字翻倍（0908 实证 s05 attempts=5/4 错、
  s03 seconds=-2.6）。现以启动快照为基：总量 = 快照 + 本进程增量，与当前文件内容无关。
  票7（#65）：自 run_pipeline 抽出为独立 module（架构评审候选1），写落盘改原子。
"""
import json
import os
import time

import atomicio


def _log(msg):
    print(f"[driver {time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Ledger:
    def __init__(self, path, meta=None, t0=None):
        """path=report.json 落盘路径；meta=顶层元数据（slug/date/style/started_at）；
        t0=进程起点（total_minutes 口径）。构造即读盘取 stages 启动快照（不写盘）。"""
        self._path = path
        self._t0 = t0 if t0 is not None else time.time()
        self._inc = dict(meta or {})
        self._inc.setdefault("stages", {})
        self._base_stages = (atomicio.load_json(path) or {}).get("stages", {})

    # ── 写 ────────────────────────────────────────────────────────────
    def _walk(self, path):
        """点路径逐段下行，缺段自动建 dict；返回 (宿主 dict, 末段键名)。"""
        keys = path.split(".")
        node = self._inc
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        return node, keys[-1]

    def record(self, path, value, add=False):
        """置值。path 点路径：末段为块名时整体置块（如 "s04"），嵌段置子键
        （如 "s08.vision"），裸键置顶层元数据（如 "slug"）。add=True 累加：
        int/float 相加、list 拼接、dict 递归累加、标量覆盖。"""
        node, leaf = self._walk(path)
        if not add:
            node[leaf] = value
            return
        node[leaf] = self._add_into(node.get(leaf), value)

    def record_event(self, path, item):
        """质量事件追加（列表块）。同进程重复追加不去重（与旧行为一致）；
        flush 与盘上旧列表拼接时按值相等去重。"""
        node, leaf = self._walk(path)
        node.setdefault(leaf, []).append(item)

    @staticmethod
    def _add_into(cur, delta):
        if isinstance(delta, dict):
            base = dict(cur) if isinstance(cur, dict) else {}
            for k, v in delta.items():
                base[k] = Ledger._add_into(base.get(k), v)
            return base
        if isinstance(delta, list):
            base = list(cur) if isinstance(cur, list) else []
            base.extend(delta)
            return base
        if isinstance(delta, (int, float)) and not isinstance(delta, bool) \
                and isinstance(cur, (int, float)) and not isinstance(cur, bool):
            return cur + delta
        return delta  # 首次出现或标量：直接置值

    # ── 读 ────────────────────────────────────────────────────────────
    def get(self, path, view="increment"):
        """读台账（缺值 None）。merged 视图 = 盘上旧值⊕本进程增量（与 flush 同一
        算法，只算不写）；票7 修复：恢复口径读顶层块——旧 s_gate_review 磁盘回退
        错读 stages.s08.vision（实际落盘顶层 s08 块），自 0908 起从未命中。"""
        root = self._merged_root() if view == "merged" else self._inc
        node = root
        for k in path.split("."):
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node

    def summary(self):
        """结构化质量摘要（渲染归调用方，Q8 定案）。质量块=本进程增量口径
        （与旧 report_summary 直读 self._report 数据源一致）；stages=快照+增量。"""
        out = {k: v for k, v in self._inc.items() if k != "stages"}
        out["stages"] = self._merged_stages(old_st={})
        return out

    # ── 合并 + 落盘 ───────────────────────────────────────────────────
    def _merged_stages(self, old_st=None):
        """stages 合并：以盘上现状为底、本进程出现过的阶段按「启动快照+增量」
        重算（与盘上现状无关——幂等关键），快照之外盘上阶段原样保留。"""
        st = dict(old_st or {})
        for k, v in self._inc.get("stages", {}).items():
            b = self._base_stages.get(k) if isinstance(self._base_stages.get(k), dict) else {}
            if not isinstance(v, dict):
                st[k] = v
                continue
            m = dict(b) if b else dict(v)
            if b:  # 总量 = 启动快照 + 本进程增量
                m["attempts"] = b.get("attempts", 0) + v.get("attempts", 0)
                m["seconds"] = round(b.get("seconds", 0) + v.get("seconds", 0), 1)
                m["errors"] = (list(b.get("errors", [])) + list(v.get("errors", [])))[-10:]
                if v.get("tokens"):
                    bt = b.get("tokens") or {}
                    m["tokens"] = {
                        "prompt": bt.get("prompt", 0) + v["tokens"].get("prompt", 0),
                        "completion": bt.get("completion", 0) + v["tokens"].get("completion", 0),
                        "calls": bt.get("calls", 0) + v["tokens"].get("calls", 0)}
            st[k] = m
        return st

    def _merged_root(self):
        """盘上旧文件 ⊕ 本进程增量（flush 与 get(view="merged") 共用算法）。"""
        # 读侧与写侧共用 atomicio 的短退避（#166）：换盘窗口内不再把旧台账当「无旧值」
        old = atomicio.load_json(self._path) or {}
        out = dict(self._inc)
        out["stages"] = self._merged_stages(old.get("stages", {}))
        # stages 外顶层质量块：dict 增量赢、list 拼接去重、started_at 取最早、其余保留
        for k, v in old.items():
            if k == "stages":
                continue
            if k == "started_at":  # 跨重启接力：保留最早启动时刻
                out["started_at"] = min(out.get("started_at") or v, v)
            elif isinstance(v, dict) and isinstance(out.get(k), dict):
                m = dict(v)
                m.update(out[k])
                out[k] = m
            elif isinstance(v, list) and isinstance(out.get(k), list):
                out[k] = v + [x for x in out[k] if x not in v]
            else:
                out.setdefault(k, v)
        return out

    def flush(self):
        """台账落盘（幂等合并 + 原子写 + 自吞异常）。内存增量保持本进程口径，
        合并结果只进写盘通道（双计复现的根源防线，0908 根治）。"""
        self._inc["total_minutes"] = round((time.time() - self._t0) / 60, 1)
        self._inc["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            out = None
            if os.path.exists(self._path):
                try:
                    out = self._merged_root()
                except Exception as e:  # noqa: BLE001 — 旧台账不可合并则用本进程数据
                    out = None
                    _log(f"[report] 旧台账合并失败（用本进程数据）: {e}")
            if out is None:
                out = self._inc
            self._write(out)
            _log(f"[report] 台账 → {self._path}")
        except Exception as e:  # noqa: BLE001 — 写失败不阻塞主流程（只留日志）
            _log(f"[report] 写入失败（不阻塞）: {e}")

    def _write(self, obj):
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        atomicio.replace_with_retry(tmp, self._path)
