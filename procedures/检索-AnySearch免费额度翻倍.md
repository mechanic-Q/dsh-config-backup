# 检索-AnySearch 免费额度翻倍（1000 → 2000 次/天）

**触发条件**：AnySearch 免费档 1,000 次/天不够用；或已经吃过 `402 daily_free_quota_exhausted`；或想提前把额度抬高一倍。

**首选姿势**（两条路，任选其一，都是 2,000 req/day + 20 QPS）：

- **Student Plan**：定价页/控制台点「Student? Unlock more quota free」→ 填教育邮箱（支持 `.edu` / `.edu.cn` / `.ac.uk` 等）→ 收 6 位验证码 → 验证的同时把邮箱绑定到账号。
- **Developer Plan**：点「Verify with GitHub」走一键 OAuth（原文：`Only requires public profile info, no private repo access`，不读私有仓库）。必须**同时**满足：账号 ≥ 6 个月、≥ 3 个公开仓库、≥ 2 个非 fork 原创仓库、近 180 天 ≥ 3 次活动。

两档 modal 的 Features 原文都是 `2,000 requests/day (2x Free)` + `20 QPS rate limit`。解锁后**沿用同一把 key**，不用换凭据。

**取证方法**（英文定价页只写 1,000，翻倍信息只在内联 JSON 里）：

```bash
curl -sL https://www.anysearch.com/zh/pricing | grep -o 'freeQuotaUnlock.\{0,700\}'
```

- 注意：这个 URL 现在返回 **HTTP 404，但 body 仍有 ~79KB 内联 JSON**，404 不代表没数据，照样 grep 得到 `freeQuotaUnlock`。
- 原文关键段：`"freeQuotaUnlock":{"student":{"subtitle":"Verify with .edu email",...},"developer":{"subtitle":"One-click GitHub verify","requirements":["Account ≥ 6 months old","≥ 3 public repos","≥ 2 non-fork original repos","Active within the last 180 days (≥ 3 events)"],"githubFooter":"Only requires public profile info, no private repo access"}}`

**反面清单**：

- **别用 `X-Ratelimit-Limit` 判断额度**：免费档与 2000 档**都是 20 QPS**（free plan features 原文：`1,000 requests/day` + `20 QPS rate limit per key`；解锁 modal 也写 20 QPS）。实测带 key 请求返回 `X-Ratelimit-Limit: 20`，两档无法区分 —— 一度据此误判「已解锁」，查了定价 JSON 才纠正。
- **没有额度查询 API**：`/v1/usage`、`/v1/account`、`/v1/me`、`/v1/quota`、`/v1/keys` 全部 404。日额度只能看控制台，或等 402 报错。
- **匿名额度按出口 IP 计**：走 Clash 这类共享出口时，等于和整条节点的人共用一个池，极易被别人烧干 —— 有 key 就一定要带 key。
- 402 后别原地重试：网关会自动下发 `as_auto_` 临时账号 + key。
- 别把「200 QPS」「按分钟计」之类的说法当真：实测只有 20 QPS 这一档。

**实测状态（2026-10-21，账户 mechanic-q）**：控制台 **Overview** 显示 `Developer` 徽章 + `$0 / MO` + `Quota Usage 1,989 remaining` + `Used 11 / 2,000` + `Rate Limit: 20 QPS` → **该账号早已是 2,000 档，无需再做验证**。顺带两个细节：① 那 11 次里含我做的几次探针调用；② 「Total Calls 12」与「Used 11 / 2,000」并不同步，看额度只认 `Used N / 2,000`。

**验证方式**：

1. 看控制台 **Overview**（不是 pricing 页）：有 `Developer`/`Student` 徽章且 `Used N / 2,000` 即已解锁（已实测确认过一次）。
2. 带 key 打 `POST https://api.anysearch.com/v1/search`（`Authorization: Bearer <key>`，body `{"query":"hello","max_results":1}`）返回 200 只说明 key 有效，与额度无关 —— 别把它当额度证据。
