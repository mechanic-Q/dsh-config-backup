# -*- coding: utf-8 -*-
import io
P = "scripts/rotation_dataset.py"
s = io.open(P, encoding="utf-8").read()

# 1) 词表自洽：布尔回落产出的粗类「翻转」也必须是合法值，否则写进去的值
#    不在自己的词表里（stats 透视会出现词表外取值）。
old = '''# 人工最终判定词表（spec #132：与判官四级词表同源对齐；布尔只表达
# "人说过话"，verdict 表达"人说了什么"——换岗评估要的是后者）
GROUND_TRUTH_VERDICTS = ("正常", "翻转90", "翻转180", "倾斜")'''
new = '''# 人工最终判定词表（spec #132：与判官四级词表同源对齐；布尔只表达
# "人说过话"，verdict 表达"人说了什么"——换岗评估要的是后者）。
# 「翻转」是粗类：旧调用方只给布尔（True/False）时无从知道 90/180，宁可
# 记粗类也不猜细分——它必须在本词表内，否则写进数据集的值不在自己的
# 词表里，stats 透视会冒出词表外取值。
GROUND_TRUTH_VERDICTS = ("正常", "翻转", "翻转90", "翻转180", "倾斜")'''
assert s.count(old) == 1, "vocab"
s = s.replace(old, new)

# 2) verify_evidence_cli 打印：拆开三元，避免优先级误读
old = '''    print("证据清单对账（verify_evidence_hashes）：共 %d 条 → " % len(results) +
          " / ".join(f"{k}:{v}" for k, v in sorted(by.items())) if results else
          "证据清单为空（离线件 + 生产件均无条目）")'''
new = '''    if not results:
        print("证据清单对账（verify_evidence_hashes）：证据清单为空"
              "（离线件 + 生产件均无条目）")
        return 0
    print("证据清单对账（verify_evidence_hashes）：共 %d 条 → %s"
          % (len(results), " / ".join(f"{k}:{v}" for k, v in sorted(by.items()))))'''
assert s.count(old) == 1, "print"
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
import py_compile; py_compile.compile(P, doraise=True)
print("fixed")
