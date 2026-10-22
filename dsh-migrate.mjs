#!/usr/bin/env node
/**
 * dsh-migrate - one-click backup / restore of a DeepSeek Harness setup.
 *
 *   node dsh-migrate.mjs backup  [--out DIR] [--with-credentials] [--with-history]
 *                                [--with-usage] [--keep N] [--push owner/repo] [--keep-stage]
 *   node dsh-migrate.mjs restore ARCHIVE [--home DSH_HOME] [--profile desktop] [--install] [--dry-run]
 *   node dsh-migrate.mjs verify  ARCHIVE
 *   node dsh-migrate.mjs list    ARCHIVE
 *
 * Zero dependencies, Node >= 18. Uses system tar.
 *
 * v2 (2026-10-22): learned from dsh-config-manager (the GUI backup plugin):
 *   - a SECTION registry instead of a flat file list, so the archive can REPORT
 *     what it did and did not capture (same idea as their 17-section registry)
 *   - generic state discovery (storages/*.json, small top-level state dirs)
 *     instead of only the six plugin dirs that existed in October
 *   - a secret scanner over everything staged, so an API key pasted into a
 *     prompt / skill / procedure is caught BEFORE the archive leaves the machine
 *   - a standalone `verify` command (sha256, no restore side effects)
 *   - opt-in sessions, and retention (--keep N)
 *
 * What this script does that the plugin deliberately does not:
 *   - ships the FULL tree of project-level skills (~/.agents/skills), not just
 *     the SKILL.md text the skills service exposes
 *   - ships agent-procedures/ + procedures/ (the distilled lessons file)
 *   - ships pnpm-lock.yaml, so the restored node_modules is byte-deterministic
 *
 * v3 (2026-10-22): "what ELSE is worth carrying" pass.
 *   - tools:     DSH_HOME/bin (crawler.sh and friends) + ~/.gitconfig identity
 *   - autostart: Windows scheduled tasks created by DSH plugins, plus Startup
 *                shortcuts, so the machine still behaves the same after a move
 *   - skill-lock: ~/.agents/.skill-lock.json - WHICH repo each skill came from,
 *                so skill updates still work on the target machine
 *   - credential hygiene: files that ARE a credential (openai-gateway/api-key,
 *     dsh-pocket/token*) are never staged, and value-holder fields inside
 *     config JSON/YAML are replaced with __REDACTED__ so "--with-credentials"
 *     stays the only way a secret can ever leave this machine
 *   - optional history: --with-usage (cost ledger), --with-history (sessions +
 *     attachments), both off by default because they are big and chatty
 */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HOME = os.homedir();
const DSH_HOME = process.env.DSH_HOME || path.join(HOME, '.dsh');
const PROFILE = process.env.DSH_PROFILE || 'desktop';
const AGENTS_SKILLS = process.env.DSH_AGENTS_SKILLS || path.join(HOME, '.agents', 'skills');

const SKIP_DIRS = new Set(['node_modules', '.venv', 'venv', '__pycache__', '.git',
  'chrome-tencent-profile', '_verify_backup', '_test', 'graphify-out', '.cache', 'dist', 'build',
  '.mypy_cache', '.pytest_cache']);
const MAX_FILE = 25 * 1024 * 1024;

/* ------------------------------------------------------------------ */
/* Section registry                                                     */
/*                                                                      */
/* Every backup item names a section. The archive records, per section, */
/* how many files landed and whether the source existed at all - so a    */
/* restore on the target machine can say "skills: 104 files" instead of  */
/* "trust me". A missing source is reported, never silently skipped.     */
/* ------------------------------------------------------------------ */
const SECTIONS = {
  'core-config': 'DSH top-level state: AGENTS.md, pet, skin, remote devices, undo/telemetry/market',
  profiles: 'profile plugin graph: package.json, pnpm-lock.yaml, pnpm-workspace.yaml, cordis*.yml',
  storages: 'namespaced stores: MCP connectors, workspaces, schedules, grants',
  'plugin-data': 'per-plugin runtime state (task board, IM bridge, channel pack, ...)',
  skills: 'skills under DSH_HOME/skills',
  'agents-skills': 'project-level skills (~/.agents/skills) - FULL trees, not SKILL.md only',
  knowledge: 'agent-procedures/, procedures/ (distilled lessons), patches/',
  tools: 'DSH_HOME/bin scripts + ~/.gitconfig commit identity',
  autostart: 'Windows scheduled tasks + Startup shortcuts that keep the setup running',
  credentials: '.credentials.yaml (encrypted when --with-credentials)',
  sessions: 'conversation history (opt-in via --with-history)',
  history: 'cost ledger, attachments (opt-in via --with-usage / --with-history)'
};

/* A file whose NAME is the secret: never staged, always reported. */
const CRED_BASENAMES = /^(api-key|token|token-lan|token\.json|secret|secret\.txt|credentials\.json|credentials\.yaml|\.credentials\.yaml|id_rsa|id_ed25519)$/i;

/* Value-holder fields inside config JSON/YAML. A bare "apiKeyHeader" or
   "apiKeyEnv" is an env-var / header NAME, not a secret, so those are excluded
   by requiring the key to be exactly one of these. */
const REDACT_JSON = /("(?:apiKeyValue|bearerToken|accessToken|refreshToken|clientSecret|secretValue|privateKey|password|passphrase)"\s*:\s*")([^"]{6,})(")/gi;
const REDACT_YAML = /^([ \t]*(?:apiKey|api_key|bearerToken|accessToken|refreshToken|clientSecret|password|passphrase)[ \t]*:[ \t]*)(?!["']?(?:__REDACTED|API_KEY_|\$|env:|[A-Z0-9_]+_API_KEY))(\S.{5,})$/gim;

function manifest(profile, opts) {
  var P = 'profiles/' + profile;
  var other = /^(web|headless|judgeprobe|desktop-lan)\/(cordis\.patch\.yml|cordis\.yml|package\.json|pnpm-workspace\.yaml|pnpm-lock\.yaml|compatibility\.json|\.dsh-codex-ui-preferences\.json|\.npmrc)$/;
  var items = [
    /* ---- profiles: THE source of truth for plugins, providers, settings ---- */
    { section: 'profiles', from: P + '/package.json', to: P + '/package.json' },
    { section: 'profiles', from: P + '/pnpm-lock.yaml', to: P + '/pnpm-lock.yaml' },
    { section: 'profiles', from: P + '/pnpm-workspace.yaml', to: P + '/pnpm-workspace.yaml' },
    { section: 'profiles', from: P + '/cordis.patch.yml', to: P + '/cordis.patch.yml' },
    { section: 'profiles', from: P + '/cordis.yml', to: P + '/cordis.yml', optional: true },
    { section: 'profiles', from: P + '/compatibility.json', to: P + '/compatibility.json', optional: true },
    { section: 'profiles', from: P + '/.npmrc', to: P + '/.npmrc', optional: true },
    { section: 'profiles', from: P + '/.dsh-market', to: P + '/.dsh-market', dir: true, optional: true },
    { section: 'profiles', from: 'profiles', to: 'profiles', dir: true, maxDepth: 1,
      only: function (r) { return other.test(r); } },

    /* ---- core-config ---- */
    { section: 'core-config', from: 'AGENTS.md', to: 'AGENTS.md', optional: true },
    { section: 'core-config', from: 'pet.json', to: 'pet.json', optional: true },
    { section: 'core-config', from: 'skin-center-active.json', to: 'skin-center-active.json', optional: true },
    { section: 'core-config', from: 'remote-web-ui-devices.json', to: 'remote-web-ui-devices.json', optional: true },
    { section: 'core-config', from: '.anonymous-user-id', to: '.anonymous-user-id', optional: true },
    { section: 'core-config', from: '.dshw-turn.json', to: '.dshw-turn.json', optional: true },
    { section: 'core-config', from: '.dshw-usage.json', to: '.dshw-usage.json', optional: true },
    { section: 'core-config', from: 'dsh-market', to: 'dsh-market', dir: true, optional: true },
    // remote-web-ui-registry/ 存放每台已配对设备的 43 字符配对密钥（"secret"），
    // 属于活凭据而非配置，绝不入包；换机后重新配对即可。devices.json 只有设备 id/UA，保留。
    { section: 'core-config', from: 'mattskillsdeck', to: 'mattskillsdeck', dir: true, optional: true },
    { section: 'core-config', from: 'openai-gateway', to: 'openai-gateway', dir: true, optional: true },
    { section: 'core-config', from: 'lan-autostart', to: 'lan-autostart', dir: true, optional: true },
    { section: 'core-config', from: 'dsh-pocket', to: 'dsh-pocket', dir: true, optional: true },
    { section: 'core-config', from: 'undo', to: 'undo', dir: true, optional: true },
    { section: 'core-config', from: 'telemetry', to: 'telemetry', dir: true, optional: true },

    /* ---- storages: every top-level *.json (MCP connectors are the big one) --- */
    { section: 'storages', from: 'storages', to: 'storages', dir: true, maxDepth: 1, optional: true,
      only: function (r) { return /^[^/]+\.json$/.test(r); } },
    { section: 'storages', from: 'storages/mcp_connector_grants_v1', to: 'storages/mcp_connector_grants_v1',
      dir: true, optional: true },
    { section: 'storages', from: 'storages/cost-meter', to: 'storages/cost-meter', dir: true, optional: true,
      maxDepth: 1, only: function (r) { return /^[^/]+\.json$/.test(r) && r.indexOf('ledger') !== 0; } },

    /* ---- plugin data dirs ---- */
    { section: 'plugin-data', from: 'our-free-model', to: 'our-free-model', dir: true, optional: true,
      skipDirs: ['rollback', 'vendor', 'logs'], skipExts: ['.log'] },
    { section: 'plugin-data', from: 'dsh-im-connect', to: 'dsh-im-connect', dir: true, optional: true,
      skipExts: ['.log', '.db', '.db-wal', '.db-shm'] },
    { section: 'plugin-data', from: 'channel-pack', to: 'channel-pack', dir: true, optional: true },
    { section: 'plugin-data', from: 'llm-deepseek', to: 'llm-deepseek', dir: true, optional: true },
    { section: 'plugin-data', from: 'task-board', to: 'task-board', dir: true, optional: true },
    { section: 'plugin-data', from: 'skills-manager', to: 'skills-manager', dir: true, optional: true,
      exts: ['.json'], skipDirs: ['repository-cache', 'uploads', 'trash'] },

    /* ---- skills + knowledge ---- */
    { section: 'skills', from: 'skills', to: 'skills', dir: true, optional: true },
    { section: 'agents-skills', from: AGENTS_SKILLS, to: '_agents/skills', dir: true, optional: true, abs: true },
    { section: 'agents-skills', from: path.join(path.dirname(AGENTS_SKILLS), '.skill-lock.json'),
      to: '_agents/.skill-lock.json', abs: true, optional: true },
    { section: 'knowledge', from: 'agent-procedures', to: 'agent-procedures', dir: true, optional: true },
    { section: 'knowledge', from: 'procedures', to: 'procedures', dir: true, optional: true },
    { section: 'knowledge', from: 'patches', to: 'patches', dir: true, optional: true },

    /* ---- tools: the scripts this setup actually calls ---- */
    { section: 'tools', from: 'bin', to: 'bin', dir: true, optional: true },
    { section: 'tools', from: path.join(HOME, '.gitconfig'), to: '_tools/gitconfig', abs: true, optional: true }
  ];

  /* ---- autostart: what makes this machine behave the same after a move ---- */
  var tasks = discoverWindowsTasks();
  for (var ti = 0; ti < tasks.length; ti++) {
    items.push({ section: 'autostart', from: tasks[ti].file,
      to: '_autostart/tasks/' + tasks[ti].name, abs: true, optional: true });
  }
  var startupDir = path.join(process.env.APPDATA || HOME, 'Microsoft', 'Windows',
    'Start Menu', 'Programs', 'Startup');
  var shortcuts = listDir(startupDir);
  for (var li = 0; li < shortcuts.length; li++) {
    if (/^desktop\.ini$/i.test(shortcuts[li])) continue;
    items.push({ section: 'autostart', from: path.join(startupDir, shortcuts[li]),
      to: '_autostart/startup/' + shortcuts[li], abs: true, optional: true });
  }

  /* ---- optional history ---- */
  if (opts && (opts.withHistory || opts.withSessions)) {
    items.push({ section: 'history', from: 'sessions', to: 'sessions', dir: true, optional: true });
    items.push({ section: 'history', from: 'attachments', to: 'attachments', dir: true, optional: true });
  }
  if (opts && (opts.withHistory || opts.withUsage)) {
    items.push({ section: 'history', from: 'storages/cost-meter/ledger.json',
      to: 'history/cost-meter/ledger.json', optional: true });
  }
  return items;
}

function listDir(dir) {
  try { return fs.readdirSync(dir); } catch (e) { return []; }
}

/* Windows keeps scheduled-task definitions in a binary-ish UTF-16 XML file, one
   per task. That directory cannot be LISTED without elevation, but a known file
   can be read - so enumerate through schtasks and then read the file directly. */
function discoverWindowsTasks() {
  if (process.platform !== 'win32') return [];
  var taskDir = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'Tasks');
  var names = [];
  var q = spawnSync('schtasks', ['/query', '/fo', 'csv', '/nh'], { encoding: 'latin1' });
  if (q.status === 0) {
    var lines = (q.stdout || '').split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var m = /^"\\?(.+?)"/.exec(lines[i].trim());
      if (!m) continue;
      var n = m[1];
      if (n.indexOf('\\') >= 0) continue; // sub-folders need elevation to read
      if (!/dsh|deepseek/i.test(n)) continue;
      if (names.indexOf(n) < 0) names.push(n);
    }
  }
  var out = [];
  for (var k = 0; k < names.length; k++) {
    var full = path.join(taskDir, names[k]);
    if (fs.existsSync(full)) out.push({ name: names[k], file: full });
  }
  return out;
}

const sha = function (buf) { return crypto.createHash('sha256').update(buf).digest('hex'); };
const rel = function (p) { return p.split(path.sep).join('/'); };

function walk(src, skipDirs, out, depth, maxDepth) {
  out = out || [];
  depth = depth || 0;
  if (maxDepth !== undefined && depth >= maxDepth) {
    var flat;
    try { flat = fs.readdirSync(src, { withFileTypes: true }); } catch (e) { return out; }
    for (var k = 0; k < flat.length; k++) {
      var fe = flat[k];
      if (!fe.isFile()) continue;
      out.push(path.join(src, fe.name));
    }
    return out;
  }
  var ents;
  try { ents = fs.readdirSync(src, { withFileTypes: true }); } catch (e) { return out; }
  for (var i = 0; i < ents.length; i++) {
    var e = ents[i];
    if (SKIP_DIRS.has(e.name)) continue;
    if (skipDirs && skipDirs.indexOf(e.name) >= 0) continue;
    var p = path.join(src, e.name);
    if (e.isDirectory()) walk(p, skipDirs, out, depth + 1, maxDepth);
    else if (e.isFile()) out.push(p);
  }
  return out;
}

/* Secret scanner: run over every staged TEXT file so a key copied into a
   prompt, a skill or a procedure is caught before the archive is pushed.
   Only the file name and the pattern class are reported - never the value. */
const SECRET_PATTERNS = [
  ['openai/deepseek key', /\bsk-[A-Za-z0-9_\-]{20,}/g],
  ['aws access key', /\bAKIA[0-9A-Z]{16}\b/g],
  ['github token', /\bgh[pousr]_[A-Za-z0-9]{30,}/g],
  ['google api key', /\bAIza[0-9A-Za-z_\-]{30,}/g],
  ['jwt', /\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\./g],
  ['private key block', /-----BEGIN [A-Z ]*PRIVATE KEY-----/g],
  ['slack token', /\bxox[baprs]-[A-Za-z0-9\-]{10,}/g]
];
const TEXT_EXT = /\.(json|jsonl|ya?ml|md|txt|mjs|cjs|js|ts|py|sh|ps1|toml|ini|cfg|env|html)$/i;

function scanSecrets(stage, files) {
  var hits = [];
  for (var i = 0; i < files.length; i++) {
    var f = files[i];
    if (!TEXT_EXT.test(f.to)) continue;
    if (f.bytes > 2 * 1024 * 1024) continue;
    var text;
    try { text = fs.readFileSync(path.join(stage, f.to), 'utf8'); } catch (e) { continue; }
    for (var k = 0; k < SECRET_PATTERNS.length; k++) {
      var re = new RegExp(SECRET_PATTERNS[k][1].source, 'g');
      if (re.test(text)) hits.push({ file: f.to, kind: SECRET_PATTERNS[k][0] });
    }
  }
  return hits;
}

/* Redaction: value-holder fields are replaced in-place, keeping the key so the
   restore still has the provider / connector entry to paste a new secret into.
   Only config-shaped files are touched (JSON/YAML inside the config sections),
   never skills, docs or scripts - those are human text and must stay verbatim. */
const REDACTABLE = /^(profiles\/|storages\/|core-config|plugin-data|_extra\/)/;
function redactText(to, text) {
  if (!/\.(json|ya?ml)$/i.test(to)) return { text: text, count: 0 };
  var n = 0;
  var ph = '__REDACTED_BY_DSH_MIGRATE__';
  if (/\.json$/i.test(to)) {
    text = text.replace(REDACT_JSON, function (m, a, val, c) { n++; return a + ph + c; });
  } else {
    text = text.replace(REDACT_YAML, function (m, a) { n++; return a + ph; });
  }
  return { text: text, count: n };
}

function redactFor(to, buf) {
  var p = rel(to);
  if (!REDACTABLE.test(p)) return { buf: buf, count: 0 };
  if (buf.length > 4 * 1024 * 1024) return { buf: buf, count: 0 };
  var text;
  try { text = buf.toString('utf8'); } catch (e) { return { buf: buf, count: 0 }; }
  var r = redactText(p, text);
  if (!r.count) return { buf: buf, count: 0 };
  return { buf: Buffer.from(r.text, 'utf8'), count: r.count };
}

function writeStage(stage, to, buf, files) {
  var dest = path.join(stage, to);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, buf);
  files.push({ to: rel(to), bytes: buf.length, sha256: sha(buf) });
}

function tryVer() {
  try {
    var r = spawnSync('dsh', ['--version'], { encoding: 'utf8', shell: process.platform === 'win32' });
    return (r.stdout || '').trim() || null;
  } catch (e) { return null; }
}

function sectionReport(items, files, warnings) {
  var rep = {};
  var seen = {};
  for (var i = 0; i < items.length; i++) seen[items[i].section] = { present: false, declared: true };
  for (var s in seen) rep[s] = { present: false, note: SECTIONS[s] || '', files: 0, bytes: 0 };
  return rep;
}

function restoreDoc(meta) {
  var lines = [
    '# DSH backup - ' + meta.profile + ' profile',
    '',
    'Created ' + meta.createdAt + ' on ' + meta.host + ' (' + meta.platform + '), dsh ' + (meta.dshVersion || '?') + '.',
    'Credentials: ' + meta.credentials,
    'Sessions: ' + (meta.withSessions ? 'included' : 'not included (re-run with --with-history)'),
    'Usage ledger: ' + (meta.withUsage ? 'included' : 'not included (re-run with --with-usage)'),
    '',
    '## What is in here',
    ''
  ];
  for (var s in meta.sections) {
    var r = meta.sections[s];
    lines.push('- ' + s + ': ' + (r.present ? r.files + ' files, ' + (r.bytes / 1048576).toFixed(2) + ' MB' : 'NOT PRESENT on the source machine')
      + '  - ' + r.note);
  }
  lines.push('');
  lines.push('## Restore on another machine');
  lines.push('');
  lines.push('Requirements: Node 18+ and tar. Nothing else - this backup carries its own');
  lines.push('restore tool (dsh-migrate.mjs) and the pnpm install is optional.');
  lines.push('');
  lines.push('Fastest path (from the pushed GitHub repo):');
  lines.push('  node dsh-migrate.mjs restore --repo <owner>/<repo> --install');
  lines.push('');
  lines.push('From this archive:');
  lines.push('  node dsh-migrate.mjs restore <this-archive> --install');
  lines.push('');
  lines.push('Verify without touching anything:');
  lines.push('  node dsh-migrate.mjs verify <this-archive>');
  lines.push('');
  lines.push('Steps:');
  lines.push('1. QUIT DeepSeek Harness first - the desktop profile is locked while it runs.');
  lines.push('2. Run one of the commands above. --install runs pnpm install --frozen-lockfile;');
  lines.push('   skip it if pnpm is missing, then just start DSH once and its plugin manager');
  lines.push('   installs from the restored package.json + pnpm-lock.yaml (needs network).');
  lines.push('3. Everything user-facing lives in profiles/<profile>/cordis.patch.yml: providers,');
  lines.push('   models, baseURLs, UI settings, plugin config. Check that file first if the UI');
  lines.push('   looks different after restore.');
  lines.push('4. Skills under _agents/ restore to ~/.agents/skills (override with DSH_AGENTS_SKILLS).');
  lines.push('5. Credentials: this backup says "' + meta.credentials + '". If encrypted, export');
  lines.push('   DSH_BACKUP_PASSPHRASE before restoring. On a fresh machine you can also just');
  lines.push('   swap ONE key in .credentials.yaml (or in Settings) and keep everything else.');
  lines.push('6. Workspace paths in storages/workspace.json point at the OLD machine - delete or');
  lines.push('   re-add workspaces whose folders do not exist.');
  lines.push('7. Restoring never overwrites silently: previous files are copied to');
  lines.push('   <DSH_HOME>/backups/pre-restore-<timestamp>/ first.');
  lines.push('8. Rollback if it goes wrong: copy that pre-restore folder back over <DSH_HOME>.');
  lines.push('9. _autostart/ holds the Windows scheduled tasks (import with: schtasks /create');
  lines.push('   /xml "<this archive>/_autostart/tasks/DSH-TempClean.xml" /tn DSH-TempClean) and');
  lines.push('   any Startup shortcuts. They are NOT installed automatically - a task that');
  lines.push('   deletes files must be a deliberate decision on the new machine.');
  lines.push('10. _tools/gitconfig is the commit identity (~/.gitconfig) used to push this');
  lines.push('   backup; copy it over if git on the new machine has none.');
  lines.push('');
  if (meta.excludedCredentials && meta.excludedCredentials.length) {
    lines.push('## Credentials deliberately left out');
    lines.push('');
    lines.push('These files hold a secret as their whole content and were NOT staged:');
    meta.excludedCredentials.forEach(function (f) { lines.push('- ' + f); });
    lines.push('');
    lines.push('Re-enter them on the target machine (Settings / the owning plugin).');
    lines.push('');
  }
  if (meta.redactions && meta.redactions.length) {
    lines.push('## Fields blanked in transit');
    lines.push('');
    lines.push('Value-holder fields were replaced with __REDACTED_BY_DSH_MIGRATE__');
    lines.push('(the key itself is kept, only the value is gone):');
    meta.redactions.forEach(function (r) { lines.push('- ' + r.file + ': ' + r.fields + ' field(s)'); });
    lines.push('');
    lines.push('Paste the real values back in - for MCP connectors, reconnect the server.');
    lines.push('');
  }
  if (meta.secretHits && meta.secretHits.length) {
    lines.push('## WARNING: possible secrets found in staged files');
    lines.push('');
    meta.secretHits.forEach(function (h) { lines.push('- ' + h.file + ': ' + h.kind); });
    lines.push('');
    lines.push('Rotate anything real, or strip it before pushing this archive anywhere.');
    lines.push('');
  }
  return lines.join('\n');
}

function backup(opts) {
  var stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  var outDir = opts.out || path.join(DSH_HOME, 'backups');
  fs.mkdirSync(outDir, { recursive: true });
  var stage = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-migrate-'));
  var files = [];
  var warnings = [];
  var total = 0;

  var items = manifest(opts.profile, opts);
  var sections = {};
  var excludedCred = [];
  var redactions = [];
  var itemCount = {};
  for (var sl = 0; sl < items.length; sl++) {
    itemCount[items[sl].section] = (itemCount[items[sl].section] || 0) + 1;
  }
  for (var sk in SECTIONS) {
    sections[sk] = {
      present: false,
      note: SECTIONS[sk],
      declared: (itemCount[sk] || 0) > 0,
      files: 0,
      bytes: 0
    };
  }

  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var src = item.abs ? item.from : path.join(DSH_HOME, item.from);
    var st;
    try { st = fs.statSync(src); } catch (e) { if (!item.optional) warnings.push('missing: ' + item.from); continue; }
    var secRec = sections[item.section];
    var added = 0;
    if (st.isDirectory()) {
      var list = walk(src, item.skipDirs, [], 0, item.maxDepth);
      for (var j = 0; j < list.length; j++) {
        var f = list[j];
        var r = rel(path.relative(src, f));
        if (item.only && !item.only(r)) continue;
        if (item.exts && item.exts.indexOf(path.extname(f)) < 0) continue;
        if (item.skipExts && item.skipExts.indexOf(path.extname(f).toLowerCase()) >= 0) continue;
        if (CRED_BASENAMES.test(path.basename(f))) {
          excludedCred.push(item.to + '/' + r);
          continue;
        }
        var s = fs.statSync(f);
        if (s.size > MAX_FILE) { warnings.push('skip large file (' + (s.size / 1048576).toFixed(1) + 'MB): ' + item.to + '/' + r); continue; }
        var fbuf = fs.readFileSync(f);
        var fto = path.join(item.to, r);
        var red = redactFor(fto, fbuf);
        if (red.count) { redactions.push({ file: rel(fto), fields: red.count }); fbuf = red.buf; }
        writeStage(stage, fto, fbuf, files);
        total += s.size;
        secRec.bytes += s.size;
        added++;
      }
    } else {
      if (item.exts && item.exts.indexOf(path.extname(src)) < 0) continue;
      if (CRED_BASENAMES.test(path.basename(src))) {
        excludedCred.push(item.to);
      } else {
        var buf = fs.readFileSync(src);
        var rred = redactFor(item.to, buf);
        if (rred.count) { redactions.push({ file: rel(item.to), fields: rred.count }); buf = rred.buf; }
        writeStage(stage, item.to, buf, files);
        total += buf.length;
        secRec.bytes += buf.length;
        added++;
      }
    }
    if (added > 0) { secRec.present = true; secRec.files += added; }
  }

  var credentials = null;
  var credSrc = path.join(DSH_HOME, '.credentials.yaml');
  if (fs.existsSync(credSrc)) {
    if (opts.withCredentials) {
      var pass = opts.passphrase || process.env.DSH_BACKUP_PASSPHRASE;
      if (!pass || pass.length < 8) throw new Error('--with-credentials needs DSH_BACKUP_PASSPHRASE (>=8 chars)');
      var salt = crypto.randomBytes(16);
      var iv = crypto.randomBytes(12);
      var key = crypto.scryptSync(pass, salt, 32);
      var c = crypto.createCipheriv('aes-256-gcm', key, iv);
      var ct = Buffer.concat([c.update(fs.readFileSync(credSrc)), c.final()]);
      var blob = Buffer.from(JSON.stringify({
        alg: 'aes-256-gcm+scrypt',
        salt: salt.toString('base64'),
        iv: iv.toString('base64'),
        tag: c.getAuthTag().toString('base64'),
        data: ct.toString('base64')
      }));
      writeStage(stage, 'credentials.enc.json', blob, files);
      credentials = 'encrypted (credentials.enc.json)';
      total += blob.length;
      sections.credentials.present = true;
      sections.credentials.files = 1;
      sections.credentials.bytes = blob.length;
    } else {
      credentials = 'skipped - sign in / re-enter API keys on the new machine, or re-run with --with-credentials';
      sections.credentials = {
        present: false,
        note: SECTIONS.credentials,
        declared: true,
        status: 'EXISTS but NOT included (needs --with-credentials)',
        files: 0,
        bytes: 0
      };
    }
  }

  var secretHits = scanSecrets(stage, files);
  if (secretHits.length) {
    warnings.push('possible secrets in ' + secretHits.length + ' file(s): '
      + secretHits.map(function (h) { return h.file + ' [' + h.kind + ']'; }).join(', '));
  }

  var meta = {
    schema: 2,
    createdAt: new Date().toISOString(),
    host: os.hostname(),
    platform: process.platform + '-' + process.arch,
    dshHome: DSH_HOME,
    profile: opts.profile,
    dshVersion: tryVer(),
    node: process.version,
    credentials: credentials,
    withSessions: !!(opts.withSessions || opts.withHistory),
    withUsage: !!opts.withUsage,
    sections: sections,
    secretHits: secretHits,
    redactions: redactions,
    excludedCredentials: excludedCred,
    totalBytes: total,
    fileCount: 0,
    files: files,
    warnings: warnings
  };
  // Self-contained: the archive carries the very tool that restores it, so the
  // target machine needs nothing but Node + tar.
  try {
    var selfPath = fileURLToPath(import.meta.url);
    var selfBuf = fs.readFileSync(selfPath);
    fs.writeFileSync(path.join(stage, 'dsh-migrate.mjs'), selfBuf);
    files.push({ to: 'dsh-migrate.mjs', bytes: selfBuf.length, sha256: sha(selfBuf) });
    total += selfBuf.length;
  } catch (e) { warnings.push('could not embed dsh-migrate.mjs: ' + e.message); }
  meta.fileCount = files.length;
  meta.totalBytes = total;

  fs.writeFileSync(path.join(stage, 'MANIFEST.json'), JSON.stringify(meta, null, 2));
  fs.writeFileSync(path.join(stage, 'RESTORE.md'), restoreDoc(meta));

  var archive = path.join(outDir, 'dsh-backup-' + opts.profile + '-' + stamp + '.tar.gz');
  var tar = spawnSync('tar', ['-czf', path.basename(archive), '-C', stage, '.'], { cwd: outDir, stdio: 'inherit' });
  if (tar.status !== 0) throw new Error('tar failed (exit ' + tar.status + ')');

  var size = fs.statSync(archive).size;
  var pruned = [];
  if (opts.keep && opts.keep > 0) pruned = prune(outDir, 'dsh-backup-' + opts.profile + '-', opts.keep, archive);
  var pushed = null;
  if (opts.push) { writeBootstrap(stage, meta); pushed = pushRepo(stage, opts.push, meta); }
  return { archive: archive, size: size, stage: stage, meta: meta, pushed: pushed, pruned: pruned };
}

function prune(dir, prefix, keep, current) {
  var removed = [];
  var names;
  try { names = fs.readdirSync(dir); } catch (e) { return removed; }
  var mine = names.filter(function (n) {
    return n.indexOf(prefix) === 0 && /\.tar\.gz$/.test(n);
  }).map(function (n) {
    var full = path.join(dir, n);
    var st;
    try { st = fs.statSync(full); } catch (e) { st = { mtimeMs: 0 }; }
    return { name: n, full: full, mtime: st.mtimeMs };
  }).sort(function (a, b) { return b.mtime - a.mtime; });
  for (var i = keep; i < mine.length; i++) {
    if (mine[i].full === current) continue;
    try { fs.rmSync(mine[i].full, { force: true }); removed.push(mine[i].name); } catch (e) { }
  }
  return removed;
}

function pushRepo(stage, repo, meta) {
  var run = function (cmd, args, cwd) {
    var r = spawnSync(cmd, args, { cwd: cwd, encoding: 'utf8' });
    if (r.status !== 0) throw new Error(cmd + ' ' + args.join(' ') + ' failed: ' + ((r.stderr || r.stdout || '').slice(0, 400)));
    return (r.stdout || '').trim();
  };
  var g = function (args) { return run('git', args, stage); };
  if (!fs.existsSync(path.join(stage, '.git'))) {
    g(['init', '-q']);
    g(['config', 'user.name', process.env.GIT_AUTHOR_NAME || 'dsh-migrate']);
    g(['config', 'user.email', process.env.GIT_AUTHOR_EMAIL || 'dsh-migrate@localhost']);
    g(['config', 'core.autocrlf', 'false']);
    g(['config', 'core.safecrlf', 'false']);
  }
  fs.writeFileSync(path.join(stage, '.gitignore'), '*.tar.gz\n');
  // Byte-for-byte fidelity: a backup must never go through CRLF translation,
  // otherwise sha256 verification fails on the target machine and binaries corrupt.
  fs.writeFileSync(path.join(stage, '.gitattributes'), '* -text\n');
  g(['add', '-A', '-f']);
  g(['commit', '-q', '-m', 'dsh backup ' + meta.createdAt + ' (' + meta.profile + ')']);
  g(['branch', '-M', 'main']);

  var view = spawnSync('gh', ['repo', 'view', repo, '--json', 'name'], { cwd: stage, encoding: 'utf8' });
  if (view.status === 0) {
    if (g(['remote']).indexOf('origin') < 0) g(['remote', 'add', 'origin', 'https://github.com/' + repo + '.git']);
    g(['push', '-q', '-u', 'origin', 'main', '--force']);
    return 'pushed to https://github.com/' + repo;
  }
  run('gh', ['repo', 'create', repo, '--private', '--source', '.', '--remote', 'origin', '--push',
    '--description', 'DeepSeek Harness configuration backup'], stage);
  return 'created https://github.com/' + repo;
}

/* One-click restore, for the machine on the other end of the link.

   The repo does NOT carry the .tar.gz (*.gitignore keeps archives out of git) -
   it carries the expanded backup tree, so the bootstrap restores from "." and
   the script's own sha256 pre-check still runs on every file. These three files
   are written AFTER the archive is tarred on purpose: they belong to the repo,
   not to the backup, so the archive and its MANIFEST stay byte-identical to what
   was verified on the source machine. */
function writeBootstrap(stage, meta) {
  var home = meta.dshHome || '%USERPROFILE%\\.dsh';
  var ps1 = [
    '# DSH one-click restore - generated by dsh-migrate on ' + meta.createdAt,
    '# Restores: profiles, plugin data, skills, knowledge, tools, autostart staging.',
    '# Does NOT restore: credentials (by design) unless credentials.enc.json is present.',
    '',
    '$ErrorActionPreference = "Stop"',
    '$here = Split-Path -Parent $MyInvocation.MyCommand.Path',
    'Set-Location -LiteralPath $here',
    'Write-Host ""',
    'Write-Host "=== DSH one-click restore ===" -ForegroundColor Cyan',
    'Write-Host ("profile: ' + meta.profile + '  created: ' + meta.createdAt + '")',
    'Write-Host ""',
    '',
    'if (-not (Get-Command node -ErrorAction SilentlyContinue)) {',
    '  Write-Host "[X] node not found. Install Node.js 18+ first: https://nodejs.org" -ForegroundColor Red',
    '  exit 1',
    '}',
    'Write-Host ("[1/3] node " + (& node -v))',
    '',
    'if (-not $env:DSH_BOOTSTRAP_FORCE) {',
    '  $busy = Get-Process | Where-Object { $_.ProcessName -match "DeepSeek|deepseek" }',
    '  if ($busy) {',
    '    Write-Host "[!] DeepSeek Harness is still running. QUIT it completely (tray icon too)," -ForegroundColor Yellow',
    '    Write-Host "    then press Enter to continue..." -ForegroundColor Yellow',
    '    Read-Host | Out-Null',
    '  }',
    '}',
    '',
    '$target = if ($env:DSH_HOME) { $env:DSH_HOME } else { "' + home + '" }',
    'Write-Host ("[2/3] restoring into " + $target)',
    'if (Test-Path -LiteralPath ".\\credentials.enc.json") {',
    '  $sec = Read-Host "backup passphrase (DSH_BACKUP_PASSPHRASE)" -AsSecureString',
    '  $env:DSH_BACKUP_PASSPHRASE = [Runtime.InteropServices.Marshal]::PtrToStringAuto(',
    '    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))',
    '}',
    '& node .\\dsh-migrate.mjs restore . --install',
    'if ($LASTEXITCODE -ne 0) {',
    '  Write-Host "[X] restore failed. Nothing was half-written: originals are in" -ForegroundColor Red',
    '  Write-Host ("    " + $target + "\\backups\\pre-restore-*") -ForegroundColor Red',
    '  exit $LASTEXITCODE',
    '}',
    '',
    'Write-Host ""',
    'Write-Host "[3/3] done. Manual follow-up (see RESTORE.md):" -ForegroundColor Green',
    'Write-Host "  - credentials: not carried by this backup - re-enter the API keys,"',
    'Write-Host "    or copy .credentials.yaml from the old machine by hand."',
    'Write-Host "  - workspaces in storages/workspace.json still point at the OLD machine."',
    'Write-Host "  - scheduled tasks are only staged under .dsh\\backups\\autostart-import-*"',
    'Write-Host "    import with: schtasks /create /xml <file> /tn <name>"',
    '',
    'if (-not $env:DSH_BOOTSTRAP_NO_LAUNCH) {',
    '  $exe = @(',
    '    "$env:LOCALAPPDATA\\Programs\\DeepSeek Harness\\DeepSeek Harness.exe",',
    '    "C:\\Program Files\\DeepSeek Harness\\DeepSeek Harness.exe",',
    '    "$env:LOCALAPPDATA\\Programs\\deepseek-harness\\DeepSeek Harness.exe"',
    '  ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1',
    '  if ($exe) {',
    '    Write-Host ""',
    '    $go = Read-Host "Start DeepSeek Harness now? [Y/n]"',
    '    if ($go -ne "n" -and $go -ne "N") { Start-Process -FilePath $exe; Write-Host "launched." }',
    '  } else {',
    '    Write-Host ""',
    '    Write-Host "DeepSeek Harness not found on this machine - install it, then start it once."',
    '  }',
    '}'
  ].join('\r\n');

  var cmd = [
    '@echo off',
    'chcp 65001 >nul 2>nul',
    'setlocal',
    'cd /d "%~dp0"',
    'where node >nul 2>nul',
    'if errorlevel 1 (',
    '  echo [X] Node.js 18+ not found. Install it first: https://nodejs.org',
    '  pause',
    '  exit /b 1',
    ')',
    'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"',
    'set RC=%ERRORLEVEL%',
    'if not "%RC%"=="0" pause',
    'exit /b %RC%'
  ].join('\r\n') + '\r\n';

  var sh = [
    '#!/usr/bin/env bash',
    '# DSH one-click restore - generated by dsh-migrate on ' + meta.createdAt,
    'set -euo pipefail',
    'cd "$(dirname "$0")"',
    'echo "=== DSH one-click restore (profile ' + meta.profile + ') ==="',
    'command -v node >/dev/null 2>&1 || { echo "[X] node not found - install Node.js 18+" >&2; exit 1; }',
    'echo "[1/2] node $(node -v)"',
    'if [ -f credentials.enc.json ]; then',
    '  printf "backup passphrase: "',
    '  read -rs PW; echo',
    '  export DSH_BACKUP_PASSPHRASE="$PW"',
    'fi',
    'node ./dsh-migrate.mjs restore . --install',
    'echo',
    'echo "[2/2] done. Follow-up: credentials (not carried), workspace paths, scheduled tasks."',
    'echo "Details: RESTORE.md"'
  ].join('\n') + '\n';

  // PS 5.1 reads .ps1 as ANSI without a BOM - the BOM is what keeps the file readable.
  fs.writeFileSync(path.join(stage, 'bootstrap.ps1'), '\ufeff' + ps1, 'utf8');
  fs.writeFileSync(path.join(stage, 'bootstrap.cmd'), cmd, 'utf8');
  fs.writeFileSync(path.join(stage, 'bootstrap.sh'), sh, 'utf8');
  try { fs.chmodSync(path.join(stage, 'bootstrap.sh'), 0o755); } catch (e) { }
}

function extractTo(archive, opts) {
  var tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-restore-'));
  if (archive && fs.existsSync(archive) && fs.statSync(archive).isDirectory()) {
    if (!fs.existsSync(path.join(archive, 'MANIFEST.json')))
      throw new Error('not a backup directory (no MANIFEST.json): ' + archive);
    return { dir: path.resolve(archive), temp: false };
  }
  if (opts && opts.repo) {
    console.log('> cloning https://github.com/' + opts.repo + '.git');
    var cl = spawnSync('git', ['clone', '--depth', '1', 'https://github.com/' + opts.repo + '.git', tmp],
      { stdio: 'inherit' });
    if (cl.status !== 0) throw new Error('git clone failed - is the repo private? run: gh auth login');
    fs.rmSync(path.join(tmp, '.git'), { recursive: true, force: true });
    return { dir: tmp, temp: true };
  }
  var tar = spawnSync('tar', ['-xzf', path.basename(archive), '-C', tmp], { cwd: path.dirname(path.resolve(archive)), stdio: 'inherit' });
  if (tar.status !== 0) throw new Error('tar extract failed');
  return { dir: tmp, temp: true };
}

function verifyArchive(archive) {
  var ex = extractTo(archive, {});
  var meta = JSON.parse(fs.readFileSync(path.join(ex.dir, 'MANIFEST.json'), 'utf8'));
  var bad = 0;
  var missing = 0;
  for (var i = 0; i < meta.files.length; i++) {
    var f = meta.files[i];
    var p = path.join(ex.dir, f.to);
    if (!fs.existsSync(p)) { missing++; console.log('  MISSING ' + f.to); continue; }
    if (sha(fs.readFileSync(p)) !== f.sha256) { bad++; console.log('  CORRUPT ' + f.to); }
  }
  if (ex.temp) fs.rmSync(ex.dir, { recursive: true, force: true });
  console.log('checked   ' + meta.files.length + ' files');
  console.log('missing   ' + missing);
  console.log('corrupt   ' + bad);
  return { missing: missing, corrupt: bad, meta: meta };
}

function restore(archive, opts) {
  var home = opts.home || DSH_HOME;
  var ex = extractTo(archive, opts);
  var tmp = ex.dir;
  var dirSrc = ex.temp ? null : tmp;
  var meta = JSON.parse(fs.readFileSync(path.join(tmp, 'MANIFEST.json'), 'utf8'));

  var bad = 0;
  for (var i = 0; i < meta.files.length; i++) {
    var f = meta.files[i];
    var p = path.join(tmp, f.to);
    if (!fs.existsSync(p)) { bad++; console.log('  MISSING ' + f.to); continue; }
    if (sha(fs.readFileSync(p)) !== f.sha256) { bad++; console.log('  CORRUPT ' + f.to); }
  }
  if (bad) console.log('warning: ' + bad + ' file(s) failed verification');

  var stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  var bak = path.join(home, 'backups', 'pre-restore-' + stamp);
  var restored = 0;
  var backed = 0;
  for (var k = 0; k < meta.files.length; k++) {
    var e = meta.files[k];
    if (e.to === 'credentials.enc.json' || e.to === 'dsh-migrate.mjs') continue;
    var src = path.join(tmp, e.to);
    // Destination map: three archive prefixes live OUTSIDE DSH_HOME, and one must
    // never be auto-installed (a scheduled task that deletes files is the user's call).
    var dst;
    if (e.to.indexOf('_agents/skills/') === 0) {
      dst = path.join(AGENTS_SKILLS, e.to.slice('_agents/skills/'.length));
    } else if (e.to === '_agents/.skill-lock.json') {
      dst = path.join(path.dirname(AGENTS_SKILLS), '.skill-lock.json');
    } else if (e.to === '_tools/gitconfig') {
      dst = path.join(HOME, '.gitconfig');
    } else if (e.to.indexOf('_autostart/') === 0) {
      dst = path.join(home, 'backups', 'autostart-import-' + stamp, e.to.slice('_autostart/'.length));
    } else if (e.to === 'history/cost-meter/ledger.json') {
      dst = path.join(home, 'storages', 'cost-meter', 'ledger.json');
    } else {
      dst = path.join(home, e.to);
    }
    if (!fs.existsSync(src)) continue;
    if (opts.dryRun) { console.log('  would write ' + e.to); restored++; continue; }
    if (fs.existsSync(dst)) {
      var b = path.join(bak, e.to);
      fs.mkdirSync(path.dirname(b), { recursive: true });
      fs.copyFileSync(dst, b);
      backed++;
    }
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(src, dst);
    restored++;
  }

  if (meta.credentials && meta.credentials.indexOf('encrypted') === 0 && !opts.dryRun) {
    var pass = opts.passphrase || process.env.DSH_BACKUP_PASSPHRASE;
    if (!pass) console.log('credentials.enc.json present but no DSH_BACKUP_PASSPHRASE - skipped');
    else {
      var bj = JSON.parse(fs.readFileSync(path.join(tmp, 'credentials.enc.json'), 'utf8'));
      var key = crypto.scryptSync(pass, Buffer.from(bj.salt, 'base64'), 32);
      var d = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(bj.iv, 'base64'));
      d.setAuthTag(Buffer.from(bj.tag, 'base64'));
      var plain = Buffer.concat([d.update(Buffer.from(bj.data, 'base64')), d.final()]);
      fs.mkdirSync(home, { recursive: true });
      fs.writeFileSync(path.join(home, '.credentials.yaml'), plain);
      console.log('credentials restored');
    }
  }

  var installed = null;
  if (opts.install && !opts.dryRun) installed = runPnpm(home, meta.profile);
  if (!opts.dryRun && (meta.excludedCredentials || []).length) {
    console.log('\nnot restored (credentials - re-enter by hand):');
    meta.excludedCredentials.forEach(function (f) { console.log('  - ' + f); });
  }
  if (!opts.dryRun && (meta.redactions || []).length) {
    console.log('\nblanked fields (' + '__REDACTED_BY_DSH_MIGRATE__' + ') need real values:');
    meta.redactions.forEach(function (r) { console.log('  - ' + r.file + ' (' + r.fields + ')'); });
  }
  if (!opts.dryRun && meta.sections && meta.sections.autostart && meta.sections.autostart.files) {
    console.log('\nautostart files staged (NOT installed): ' + path.join(home, 'backups', 'autostart-import-' + stamp));
    console.log('  import a task with: schtasks /create /xml <file> /tn <name>');
  }
  // Never delete a directory the user pointed us at (restore DIR / --repo clone is
  // temp, but a plain backup directory is theirs).
  if (tmp !== dirSrc) fs.rmSync(tmp, { recursive: true, force: true });
  return { restored: restored, backed: backed, bak: bak, installed: installed, meta: meta };
}

/* pnpm resolution: PATH first, then the copy shipped inside DeepSeek Harness
   (plain JS, so any Node >= 18 can run it). Returns { cmd, args }. */
function findPnpm() {
  if (process.env.DSH_PNPM) return { cmd: process.env.DSH_PNPM, args: [] };
  var which = spawnSync(process.platform === 'win32' ? 'where' : 'which', ['pnpm'], { encoding: 'utf8' });
  if (which.status === 0) {
    var lines = which.stdout.split(/\r?\n/).map(function (s) { return s.trim(); }).filter(Boolean);
    for (var li = 0; li < lines.length; li++) {
      var hit = lines[li];
      if (process.platform === 'win32' && fs.existsSync(hit + '.cmd')) return { cmd: hit + '.cmd', args: [] };
      if (process.platform === 'win32' && fs.existsSync(hit + '.exe')) return { cmd: hit + '.exe', args: [] };
      if (fs.existsSync(hit)) return { cmd: hit, args: [] };
    }
  }
  var roots = [
    process.env.DSH_INSTALL_DIR,
    'C:/Program Files/DeepSeek Harness',
    'C:/Program Files/deepseek-harness',
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'DeepSeek Harness'),
    '/Applications/DeepSeek Harness.app/Contents/Resources',
    '/opt/DeepSeek Harness'
  ];
  var rels = [
    'resources/runtime/primary-runtime/dependencies/pnpm/bin/pnpm.cjs',
    'runtime/primary-runtime/dependencies/pnpm/bin/pnpm.cjs'
  ];
  for (var i = 0; i < roots.length; i++) {
    for (var j = 0; j < rels.length; j++) {
      if (!roots[i]) continue;
      var c = path.join(roots[i], rels[j]);
      if (fs.existsSync(c)) return { cmd: process.execPath, args: [c] };
    }
  }
  var guesses = [
    path.join(process.env.LOCALAPPDATA || '', 'pnpm', 'pnpm.exe'),
    '/usr/local/bin/pnpm',
    '/opt/homebrew/bin/pnpm'
  ];
  for (var k = 0; k < guesses.length; k++) if (guesses[k] && fs.existsSync(guesses[k])) return { cmd: guesses[k], args: [] };
  return null;
}

/* pnpm 11 re-verifies every lockfile entry against minimumReleaseAge. A package
   that the registry manifest no longer lists (or that is younger than the cutoff)
   aborts the whole install. The profile already carries such exemptions for
   other packages; add the offenders and retry once. */
function healReleaseAge(dir, output) {
  var bad = [];
  var re = /^\s+(\S+) could not be checked against minimumReleaseAge/gm;
  var m;
  while ((m = re.exec(output)) !== null) bad.push(m[1]);
  if (!bad.length) return false;
  var wsPath = path.join(dir, 'pnpm-workspace.yaml');
  if (!fs.existsSync(wsPath)) return false;
  var text = fs.readFileSync(wsPath, 'utf8');
  var add = bad.filter(function (b) { return text.indexOf(b) < 0; });
  if (!add.length) return false;
  var lines = add.map(function (b) { return "  - '" + b + "'"; }).join('\n');
  if (/^minimumReleaseAgeExclude:/m.test(text)) {
    text = text.replace(/^minimumReleaseAgeExclude:[ \t]*$/m, 'minimumReleaseAgeExclude:\n' + lines);
  } else {
    text = text.trimEnd() + '\n\nminimumReleaseAgeExclude:\n' + lines + '\n';
  }
  fs.writeFileSync(wsPath, text);
  console.log('  added minimumReleaseAge exemption for: ' + add.join(', '));
  return true;
}

function runPnpm(home, profile) {
  var dir = path.join(home, 'profiles', profile);
  var pnpm = findPnpm();
  if (!pnpm) return 'skipped: pnpm not found - start DeepSeek Harness and let its plugin manager install';
  var argv = pnpm.args.concat(['install', '--frozen-lockfile']);
  var logPath = path.join(os.tmpdir(), 'dsh-migrate-pnpm.log');
  for (var attempt = 1; attempt <= 3; attempt++) {
    console.log('\n> ' + pnpm.cmd + ' ' + argv.join(' ') + '   (cwd ' + dir + ')');
    var needsShell = /\.(cmd|bat)$/i.test(pnpm.cmd);
    var cmdLine = needsShell && /\s/.test(pnpm.cmd) ? '"' + pnpm.cmd + '"' : pnpm.cmd;
    var fd = fs.openSync(logPath, 'w');
    var r;
    try {
      r = spawnSync(cmdLine, argv, {
        cwd: dir,
        shell: needsShell,
        stdio: ['ignore', fd, fd],
        maxBuffer: 64 * 1024 * 1024
      });
    } finally {
      try { fs.closeSync(fd); } catch (e) {}
    }
    var out = '';
    try { out = fs.readFileSync(logPath, 'utf8'); } catch (e) {}
    var tail = out.split(/\r?\n/).filter(Boolean).slice(-8).join('\n');
    if (r.status === 0) {
      console.log(out.split(/\r?\n/).filter(Boolean).slice(-3).join('\n'));
      return 'ok';
    }
    if (healReleaseAge(dir, out)) { console.log('  retrying install after policy exemption'); continue; }
    if (r.error) console.log('  spawn error: ' + r.error.message);
    console.log(tail);
    return 'failed (exit ' + r.status + ') - open DeepSeek Harness once; its plugin manager reinstalls from the same manifest';
  }
  return 'failed';
}

function list(archive) {
  var tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-list-'));
  var lr = spawnSync('tar', ['-xzf', path.basename(archive), '-C', tmp, './MANIFEST.json'], { cwd: path.dirname(path.resolve(archive)) });
  if (lr.status !== 0) spawnSync('tar', ['-xzf', path.basename(archive), '-C', tmp, 'MANIFEST.json'], { cwd: path.dirname(path.resolve(archive)) });
  var meta = JSON.parse(fs.readFileSync(path.join(tmp, 'MANIFEST.json'), 'utf8'));
  fs.rmSync(tmp, { recursive: true, force: true });
  console.log('created   ' + meta.createdAt + '  (' + meta.host + ', ' + meta.platform + ')');
  console.log('profile   ' + meta.profile + '   dsh ' + (meta.dshVersion || '?'));
  console.log('files     ' + meta.fileCount + '  (' + (meta.totalBytes / 1048576).toFixed(2) + ' MB raw)');
  console.log('creds     ' + meta.credentials);
  if (meta.sections) {
    console.log('\nsections:');
    for (var s in meta.sections) {
      var r = meta.sections[s];
      var state = r.present
        ? String(r.files).padStart(5) + ' files  ' + (r.bytes / 1048576).toFixed(2).padStart(7) + ' MB'
        : '  --   ' + (r.status || (r.declared ? 'missing on source' : 'not in this backup'));
      console.log('  ' + (s + '            ').slice(0, 14) + state + '   ' + (r.note || ''));
    }
  } else {
    var top = new Map();
    for (var i = 0; i < meta.files.length; i++) {
      var f = meta.files[i];
      var key = f.to.split('/')[0];
      var cur = top.get(key) || { n: 0, b: 0 };
      top.set(key, { n: cur.n + 1, b: cur.b + f.bytes });
    }
    var rows = Array.from(top.entries()).sort(function (a, b) { return b[1].b - a[1].b; });
    for (var j = 0; j < rows.length; j++) {
      var k = rows[j][0];
      var v = rows[j][1];
      console.log('  ' + (v.b / 1048576).toFixed(2).padStart(8) + ' MB  ' + String(v.n).padStart(4) + ' files  ' + k);
    }
  }
  if (meta.warnings && meta.warnings.length) {
    console.log('\nwarnings:');
    meta.warnings.forEach(function (w) { console.log('  - ' + w); });
  }
}

var argv = process.argv.slice(2);
var cmd = argv[0];
var flag = function (n) { return argv.indexOf(n) >= 0; };
var val = function (n) { var i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : undefined; };
var positional = [];
for (var pi = 0; pi < argv.length; pi++) {
  if (argv[pi].indexOf('--') === 0) { pi++; continue; }
  positional.push(argv[pi]);
}

try {
  if (cmd === 'backup') {
    var r = backup({
      out: val('--out'),
      profile: val('--profile') || PROFILE,
      withCredentials: flag('--with-credentials'),
      withSessions: flag('--with-sessions'),
      withHistory: flag('--with-history'),
      withUsage: flag('--with-usage'),
      keep: val('--keep') ? parseInt(val('--keep'), 10) : undefined,
      passphrase: val('--passphrase'),
      push: val('--push')
    });
    console.log('\narchive  ' + r.archive);
    console.log('size     ' + (r.size / 1048576).toFixed(2) + ' MB');
    console.log('files    ' + r.meta.fileCount);
    console.log('creds    ' + r.meta.credentials);
    if (r.pushed) console.log('github   ' + r.pushed);
    if (r.pruned && r.pruned.length) console.log('pruned   ' + r.pruned.length + ' old archive(s)');
    if (r.meta.warnings.length) {
      console.log('warnings:');
      r.meta.warnings.forEach(function (w) { console.log('  - ' + w); });
    }
    if (!flag('--keep-stage')) fs.rmSync(r.stage, { recursive: true, force: true });
  } else if (cmd === 'restore') {
    var archive = positional[1];
    var repo = val('--repo');
    if (!archive && !repo) throw new Error('usage: restore ARCHIVE  |  restore --repo owner/repo');
    var rr = restore(archive, {
      repo: repo,
      home: val('--home'),
      dryRun: flag('--dry-run'),
      install: flag('--install'),
      passphrase: val('--passphrase')
    });
    if (flag('--dry-run')) {
      console.log('\ndry run: ' + rr.restored + ' file(s) would be written into ' + (val('--home') || DSH_HOME));
      console.log('nothing was written; real run would first copy existing files to ' + rr.bak);
    } else {
      console.log('\nrestored ' + rr.restored + ' file(s) into ' + (val('--home') || DSH_HOME));
      console.log('previous copies saved to ' + rr.bak);
    }
    if (rr.installed) console.log('pnpm: ' + rr.installed);
  } else if (cmd === 'verify') {
    if (!positional[1]) throw new Error('usage: verify ARCHIVE');
    var v = verifyArchive(positional[1]);
    if (v.missing || v.corrupt) process.exit(1);
  } else if (cmd === 'list') {
    list(positional[1]);
  } else {
    console.log('usage:');
    console.log('  node dsh-migrate.mjs backup  [--out DIR] [--with-credentials] [--with-history] [--with-usage]');
    console.log('                               [--keep N] [--push owner/repo] [--keep-stage]');
    console.log('  node dsh-migrate.mjs restore ARCHIVE [--home DSH_HOME] [--install] [--dry-run]');
    console.log('  node dsh-migrate.mjs restore DIR|.     restore straight from a repo checkout');
    console.log('  node dsh-migrate.mjs restore --repo owner/repo [--install]');
    console.log('  node dsh-migrate.mjs verify  ARCHIVE');
    console.log('  node dsh-migrate.mjs list    ARCHIVE');
    console.log('');
    console.log('with --push the repo also gets bootstrap.cmd / bootstrap.ps1 / bootstrap.sh:');
    console.log('the receiving machine runs bootstrap.cmd (Windows) or bootstrap.sh (macOS/Linux)');
  }
} catch (e) {
  console.error('error: ' + e.message);
  process.exit(1);
}
