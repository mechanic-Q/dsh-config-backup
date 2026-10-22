#!/usr/bin/env node
// 用途：给 profile 里的 dsh-deep-search 打「volatile 解包」补丁，并可选调整引擎链顺序。
// 为什么需要：@deepseek-ai/schemastery 的 .volatile() 字段，Loader 交给插件 apply() 的
// 是 { get() } 包装器而不是值本身；dsh-deep-search 0.1.9 的 apply 直接用
// Array.isArray / typeof / Number.isFinite 读，全部判断失败 → 整个 YAML config 静默回落默认值
// 而 dsh-free-search 有 resolveConfig() 解包，所以 free-search 的配置生效、deep-search 不生效。
// 何时跑：pnpm install / 升级 dsh-deep-search 之后（node_modules 被覆盖）。
// 用法：node deep-search-volatile-unwrap.mjs [--profile <name>]   （默认 desktop）
import { readFileSync, writeFileSync, existsSync, copyFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const args = process.argv.slice(2);
const pi = args.indexOf("--profile");
const profile = pi >= 0 ? args[pi + 1] : "desktop";
const target = join(homedir(), ".dsh", "profiles", profile, "node_modules", "dsh-deep-search", "lib", "index.mjs");

if (!existsSync(target)) {
  console.error("找不到 " + target + "（该 profile 没装 dsh-deep-search？）");
  process.exit(2);
}
const src = readFileSync(target, "utf8");
if (src.includes("function resolveVolatileConfig")) {
  console.log("已打过补丁，跳过：" + target);
  process.exit(0);
}
const anchor = "function apply(ctx, rawConfig) {";
const idx = src.indexOf(anchor);
if (idx < 0) {
  console.error("找不到 anchor 'function apply(ctx, rawConfig) {'，插件结构变了，需人工看。");
  process.exit(3);
}
const backup = target + ".bak-" + new Date().toISOString().replace(/[:.]/g, "-");
copyFileSync(target, backup);
const helper = [
  "// [local patch] Loader 把 .volatile() 字段以 { get() } 活引用交给 apply()，",
  "// 不解包时下面所有 Array.isArray / typeof / Number.isFinite 都判假，",
  "// 整套 YAML config 会静默回落 schema 默认值。",
  "function resolveVolatileConfig(config) {",
  "\tif (config === null || typeof config !== \"object\") return {};",
  "\tconst out = {};",
  "\tfor (const [key, value] of Object.entries(config)) {",
  "\t\tout[key] = value !== null && typeof value === \"object\" && typeof value.get === \"function\" ? value.get() : value;",
  "\t}",
  "\treturn out;",
  "}",
  "",
  "function apply(ctx, rawConfig) {",
  "\trawConfig = resolveVolatileConfig(rawConfig);",
].join("\n");
writeFileSync(target, src.slice(0, idx) + helper + src.slice(idx + anchor.length));
console.log("已打补丁：" + target);
console.log("备份：" + backup);
console.log("重启 DSH 生效。");
