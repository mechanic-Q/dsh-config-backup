# 检索-deep-search 配置不生效（volatile 未解包）

**触发条件**：在 `~/.dsh/profiles/<p>/cordis.patch.yml` 里给 dsh-deep-search 写了 config（enabledEngines / enabledPlatforms / budgetChars / alwaysSearch / maxRounds / searxngInstances…），重启后**完全没变化**，尤其表现为：doctor 列出的引擎是默认全量表而不是你写的两三个；explicit target 之外永远只有默认链第一个引擎在跑；`alwaysSearch: false` 写了却照旧接管所有联网问题。

**首选姿势**：
1. 先跑 `node ~/.dsh/procedures/patches/deep-search-volatile-unwrap.mjs`（幂等，已打过会跳过），再重启 DSH。
2. 验证：`deep_search({doctor:true})` 的引擎列表应当**只剩你 YAML 写的那些**；再不放心看 `ctx.cfg` 生效值，或直接跑一条 query 看 `perTarget` 里出现的引擎名。
3. 根因一句话：`.volatile()` 字段经 Loader 交给插件 `apply()` 的是 **`{ get() }` 包装器**，插件必须 like `dsh-free-search` 的 `resolveConfig()` 那样解包；dsh-deep-search 0.1.9 没有解包，于是 `Array.isArray(wrapper)`、`typeof wrapper === "string"`、`Number.isFinite(wrapper)` 全判假 → 每个字段静默回落 schema 默认值。

**反面清单**：
- 别以为是 YAML 没生效：`dsh --profile <p> --dump-config` 能看到你的值确实是组合后的 config —— 配置**进来了**，是插件读的时候读歪了。
- 别以为没重启 / 装错 profile：查进程 StartTime 与命令行 `--profile` 即可排除。
- 别去改 `searchMaxResults` 之流：它在 preset 内层，被 bundle 置 `disabled`，改 profile 顶层无效（见 检索-搜索只回8条.md）。
- 别指望升级修：npm 上 0.1.9 已是最新（0.0.0-stage / 0.1.8 / 0.1.9）。
- 补丁打在 profile 的 node_modules 里，`pnpm install` / 升级会覆盖 → 重装后必须重跑补丁脚本。

**验证方式**：
- 隔离验证 unwrap 逻辑（无需重启）：从 index.mjs 里正则抠出 `resolveVolatileConfig` 用 `new Function` 跑一遍，喂 `{ get: () => ["tavily","bing"] }` 应得数组、普通值应原样透传、`null` 应得 `{}`。
- 真验证：重启后 `deep_search doctor` 的引擎清单 == YAML 清单，且 `perTarget` 首个引擎是你排在链首的那个。
- 备份文件在同目录 `index.mjs.bak-deepsearch-volatile`（原 md5 `c9bf7ede43888557ca2ac5b09cf02124`）。

## 验收记录（2026-10-21 20:47 重启后）
- doctor：engines = [tavily(ready), bing(ready)]，platforms = github/hackernews/npm/huggingface/modelscope/wikipedia，missingCredentials = []。打补丁前这里列 10 个引擎 + 整套默认平台表。
- 实跑 2 条 query（无 target）：`perTarget {"tavily":{"hits":6,"ms":4514}}` —— tavily 先答，bing 没跑（链式命中即止），证明 YAML 里 `[tavily, bing]` 顺序生效。12 条结果里约 10 条是 learn.foundry.com / release notes 真文档，仅 2 条噪音（nuke-build .NET 项目、Jim Cramer），耗时 2.7s；打补丁前 bing 单引擎是 8 条全噪音 / 3.3s。
- alwaysSearch:false 生效的旁证：注入给我的人设段从「For ANY question that needs a web search」变成了窄口径的「For time-sensitive or contested facts」。
- budgetChars 走的是和 enabledEngines 完全相同的 `Number.isFinite(rawConfig?.budgetChars)` 读法，既然数组字段已生效，8000 同样生效。
- 平台白名单可用：explicit target npm 正常返回 3 条（白名单外的平台会按未知 target 拒绝）。
- 注意：tavily /usage 在成功后仍显示 0/1000，可能是计数延迟；若长期为 0 说明实际走的不是计费通道，需要另查。

