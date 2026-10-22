# -*- coding: utf-8 -*-
import io, re
P = "scripts/run_pipeline.py"
src = io.open(P, encoding="utf-8").read()

# ── C. 在 _check_frames 末尾增加「证据分类归档」与 manifest 写──
old3 = '''        self.ledger.record("s08.vision", {"checked": len(targets), "flipped": flipped,
                                          "tilted": tilted, "judge_failed": judge_fail,
                                          "clip_escalated": clip_escalated,
                                          "clip_layer": clip_layer_status,
                                          "form": form, "calibrated": cal_ok,
                                          "confidence": conf_map,
                                          "spot_check": spot_check,
                                          "low_conf": low_conf,
                                          "gate_auto": gate_auto,
                                          "probe_shadow": probe_rows,
                                          "evidence": evidence,
                                          "judge": judge_block})
        return flipped, tilted'''
new3 = '''        # 证据分类归档（#182 AC3/AC4：生产写入三类桶目录 + manifest.json）
        if isinstance(judge_block, dict) and judge_block.get("ok"):
            try:
                _classify_evidence_bucket(self.slug, evidence, frames)
            except Exception as e:  # noqa: BLE001 — 存证失败不阻塞主链
                log(f"[evidence] 分类归档与 manifest 写失败（不阻塞）: {e}")
        self.ledger.record("s08.vision", {"checked": len(targets), "flipped": flipped,
                                          "tilted": tilted, "judge_failed": judge_fail,
                                          "clip_escalated": clip_escalated,
                                          "clip_layer": clip_layer_status,
                                          "form": form, "calibrated": cal_ok,
                                          "confidence": conf_map,
                                          "spot_check": spot_check,
                                          "low_conf": low_conf,
                                          "gate_auto": gate_auto,
                                          "probe_shadow": probe_rows,
                                          "evidence": evidence,
                                          "judge": judge_block})
        return flipped, tilted'''
assert src.count(old3) == 1, "_check_frames record anchor"
src = src.replace(old3, new3)

io.open(P, "w", encoding="utf-8").write(src)
print("patch_run_pipeline_part2 ok")
