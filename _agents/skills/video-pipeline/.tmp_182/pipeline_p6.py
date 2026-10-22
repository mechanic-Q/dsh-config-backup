# -*- coding: utf-8 -*-
import io
P = "scripts/run_pipeline.py"
src = io.open(P, encoding="utf-8").read()

anchor = '''def log(msg):'''
assert src.count(anchor) == 1

helper = '''def _sha256_file(fp):
    """帧内容哈希（#182「清单是账」）：与 t15_replay.sha256 同算法，流式读取。

    证据行落哈希后，帧被覆盖/移动可由 rotation_dataset.verify_evidence_hashes
    逐条对出来。读失败返回空串（存证降级，不炸主链）。"""
    try:
        h = hashlib.sha256()
        with open(fp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


'''
src = src.replace(anchor, helper + anchor)
io.open(P, "w", encoding="utf-8").write(src)
print("added _sha256_file")
