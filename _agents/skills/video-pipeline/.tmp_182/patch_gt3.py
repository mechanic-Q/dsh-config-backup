# -*- coding: utf-8 -*-
import io
P = "scripts/rotation_groundtruth.py"
s = io.open(P, encoding="utf-8").read()

# dry_run 与写清单两处调用都透传 frames_root
old = '''        for row in _export_rows(pos, neg, False, references_dir, date_root):'''
new = '''        for row in _export_rows(pos, neg, False, references_dir, date_root,
                                frames_root):'''
assert s.count(old) == 1, "dry"
s = s.replace(old, new)

old = '''        for row in _export_rows(pos, neg, synthetic, references_dir, date_root):'''
new = '''        for row in _export_rows(pos, neg, synthetic, references_dir, date_root,
                                frames_root):'''
assert s.count(old) == 1, "write"
s = s.replace(old, new)

# docstring 补 frames_root
old = '''    references_dir / date_root 覆盖证据桶根与工作帧根（缺省走仓内 references/
    与 DATE_ROOT）；测试与异地复算都靠这两个口，不在函数里读死常量（#141）。
    """'''
new = '''    references_dir / date_root 覆盖证据桶根与工作帧根（缺省走仓内 references/
    与 DATE_ROOT）；测试与异地复算都靠这两个口，不在函数里读死常量（#141）。
    frames_root 透传生产桶扫描根（#182）：生产件与离线件同口导出。
    """'''
assert s.count(old) == 1, "doc"
s = s.replace(old, new)

# CLI 加 --frames-root
old = '''    ap.add_argument("--dry-run", action="store_true",'''
new = '''    ap.add_argument("--frames-root", default=None,
                    help="生产证据桶扫描根（#182；缺省 DRIVER_DATE_ROOT / E:\\\\每日新中国）")
    ap.add_argument("--dry-run", action="store_true",'''
assert s.count(old) == 1, "cli"
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
print("patched 2")
