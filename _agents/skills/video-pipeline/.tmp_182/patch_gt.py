# -*- coding: utf-8 -*-
"""#182：gt.row_material / evidence_by_row 透传 frames_root（生产桶扫描口）。"""
import io
P = "scripts/rotation_groundtruth.py"
s = io.open(P, encoding="utf-8").read()

old = '''def evidence_by_row(rows=None, references_dir=None):
    """数据集行 → 证据桶条目（T18 #130）：按 (date, slug, shot) 链接全部
    t15-evidence-* 桶条目。返回 {(date,slug,shot): [条目,...]}；条目即
    rotation_dataset.load_evidence_buckets 的形状（category/sid/round/frame/…）。
    帧缺盘的条目也保留（manifest 为账），调用方按 linked 自取。"""
    out = {}
    for e in rd.load_evidence_buckets(references_dir):'''
new = '''def evidence_by_row(rows=None, references_dir=None, frames_root=None):
    """数据集行 → 证据桶条目（T18 #130 + #182）：按 (date, slug, shot) 链接全部
    证据桶条目——离线件（references/t15-evidence-*）+ 生产件
    （<slug>/_frames/evidence/manifest.json）。返回 {(date,slug,shot): [条目,...]}；
    条目即 rotation_dataset.load_evidence_buckets 的形状（category/sid/round/frame/…）。
    帧缺盘的条目也保留（manifest 为账），调用方按 linked 自取。
    frames_root 透传生产桶扫描根（#182）：测试注入 tempdir，生产缺省走
    DRIVER_DATE_ROOT / E: 盘。"""
    out = {}
    for e in rd.load_evidence_buckets(references_dir, frames_root):'''
assert s.count(old) == 1, "evidence_by_row"
s = s.replace(old, new)

old = '''def row_material(r, references_dir=None, date_root=None):'''
new = '''def row_material(r, references_dir=None, date_root=None, frames_root=None):'''
assert s.count(old) == 1, "row_material sig"
s = s.replace(old, new)

old = '''    linked = evidence_by_row([r], references_dir).get(
        (r["date"], r["slug"], r["shot"]), [])'''
new = '''    linked = evidence_by_row([r], references_dir, frames_root).get(
        (r["date"], r["slug"], r["shot"]), [])'''
assert s.count(old) == 1, "row_material link"
s = s.replace(old, new)

# row_material docstring 补一句 frames_root
old = '''    [{path, kind, category, round, label_hint}]——kind=work(工作帧) /
    evidence-1-confirmed / evidence-2-judge-found / evidence-3-unfixed /
    rowlink(行内 evidence_frames)。桶帧按 (date,slug,shot) 链接本行，
    category+round 逐条保留；缺盘帧不进候选。"""'''
new = '''    [{path, kind, category, round, label_hint}]——kind=work(工作帧) /
    evidence-1-confirmed / evidence-2-judge-found / evidence-3-unfixed /
    rowlink(行内 evidence_frames)。桶帧按 (date,slug,shot) 链接本行，
    category+round 逐条保留；缺盘帧不进候选。
    frames_root 透传生产桶扫描根（#182）：生产件与离线件同口消费。"""'''
assert s.count(old) == 1, "row_material doc"
s = s.replace(old, new)

io.open(P, "w", encoding="utf-8").write(s)
import py_compile; py_compile.compile(P, doraise=True)
print("gt frames_root threaded")
