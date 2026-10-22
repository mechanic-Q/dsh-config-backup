# -*- coding: utf-8 -*-
import io
P = "scripts/test_evidence_production.py"
s = io.open(P, encoding="utf-8").read()
old = '''    except Exception:  # noqa: BLE001 — 清理失败不影响结论
        pass'''
new = '''    except Exception as e:  # noqa: BLE001 — 清理失败不影响结论，但要留痕
        print("清理未完成（需手工删）: %s -> %s" % (_d, e))'''
assert s.count(old) == 1
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
print("fixed except-pass")
