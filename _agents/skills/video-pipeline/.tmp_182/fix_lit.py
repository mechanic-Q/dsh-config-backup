# -*- coding: utf-8 -*-
import io
P = "scripts/rotation_groundtruth.py"
s = io.open(P, encoding="utf-8").read()
old = '''                    help="生产证据桶扫描根（#182；缺省 DRIVER_DATE_ROOT / E:\\\\每日新中国）")'''
new = '''                    help="生产证据桶扫描根（#182；缺省读 DRIVER_DATE_ROOT，"
                         "未设则用与 gt 同源的帧根）")'''
assert s.count(old) == 1, "help"
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
import py_compile; py_compile.compile(P, doraise=True)
print("fixed help literal")
