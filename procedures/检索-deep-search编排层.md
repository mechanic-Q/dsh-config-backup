# 检索-deep-search编排层

> **触发**：要做选型判断（A 还是 B）/ 判错要返工或花钱 / 关键证据只能来自二手转述 / 用户说「查证」「深挖」「最新进展」
> **验证**：`research_state(researchId, action=summary)` 里 `officialConfirmation=found` 且有 A 级证据，或明确报告「未检索到」

## 首选

先用轻的：`web_search` 或 `anysearch_search`。只有当三条判据满足任意两条时才上 deep_search：

```
deep_search(queries=[...3-5 条不同角度...], maxResultsPerQuery=5)
→ 拿 researchId
→ verify_claims(researchId, claims=[...])  # 关键断言对撞证据
→ research_state(researchId, action=summary) # 写答案前必读
```

改行为不用重启，改 `~/.dsh/profiles/desktop/cordis.patch.yml` 的 `- id: deep-search`，全部字段都是 `.volatile()`：
```yaml
- id: deep-search
  name: dsh-deep-search
  config:
    alwaysSearch: false      # 默认已 false：不接管所有联网问题
    promptInjection: true    # 保留提示注入，让我知道它存在
    maxRounds: 5
    budgetChars: 6000
```

## 别再来

| 弯路 | 为什么不通 |
| --- | --- |
| 穷举盘点题用它 | 它的价值是收敛到一个断言，不是展开清单；5 轮换来的是一句话 |
| 一手源能直读还用它 | 源码 / registry / 真装一遍都比搜索可靠，且这些证据天生 A 级 |
| 开了 `alwaysSearch: true` | 每个联网问题都被它接管，包括「查个 API 怎么用」 |
| 用 `@xiaowuwu/dsh-deep-search` 装但没给裸名别名 | 它的 patch 写 `name: dsh-deep-search`，scoped 包装不进去 |
| 装完发现被 skip | 它的 peer 要求 `<0.2.1-0`，桌面 runtime 是 0.2.1-rc.2 → 要 `dsh plugin allow-version` |

## 注意

- 装它必须用别名：`pnpm add "dsh-deep-search@npm:@xiaowuwu/dsh-deep-search@0.1.9"`。
- 兼容豁免写在 `<profile>/compatibility.json`，且**键要用 scoped 真名**：`@xiaowuwu/dsh-deep-search@0.1.9`。
- 常驻成本：3 个工具 ≈401 tok + 提示 ≈688 tok；`promptInjection: false` 省 688。

证据：本机 2026-10-21 实装验收（deep_search / research_state / verify_claims 三个工具全通过）