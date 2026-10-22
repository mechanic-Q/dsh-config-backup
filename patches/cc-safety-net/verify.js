// usage: node verify.js [chunkPath]
const { pathToFileURL } = require('url');
const fs = require('fs');
const target = process.argv[2] || 'C:/Users/LMR/.dsh/profiles/desktop/node_modules/cc-safety-net/dist/chunks/index-1x1d9s1m.js';
(async () => {
  const mod = await import(pathToFileURL(target).href);
  let fn = null;
  mod.yt({ tools: { guard: (f) => { fn = f; } } });
  if (!fn) { console.log('NO GUARD CAPTURED'); return; }
  const cwd = 'E:/AI/deepseek-harness/temp';
  const call = async (name, args) => {
    try {
      const res = await fn({ name, arguments: args, agent: { session: { header: { id: 's1', cwd } } } });
      return String(res || '').startsWith('BLOCKED') ? 'BLOCKED' : 'allow';
    } catch (e) { return 'THREW: ' + (e && e.message); }
  };
  const cmds = ['git reset --hard HEAD~3', 'rm -rf /', 'cat ~/.ssh/id_rsa', 'rm -rf ./build', 'git status'];
  console.log('--- command arg (5 tools) ---');
  for (const name of ['bash', 'pwsh', 'ops_bash', 'wsl', 'ssh_exec']) {
    for (const c of cmds) console.log(name.padEnd(9) + ' | ' + c.padEnd(26) + ' => ' + await call(name, { command: c }));
  }
  console.log('--- ops_bash script_path (no command field) ---');
  const dangerScript = 'E:/AI/deepseek-harness/temp/verify-danger.sh';
  const safeScript = 'E:/AI/deepseek-harness/temp/verify-safe.sh';
  fs.writeFileSync(dangerScript, '#!/bin/bash\ngit reset --hard HEAD~3\n', 'utf8');
  fs.writeFileSync(safeScript, '#!/bin/bash\ngit status\n.\x2fgradlew build\n', 'utf8');
  console.log('script_path danger => ' + await call('ops_bash', { script_path: dangerScript }));
  console.log('script_path safe   => ' + await call('ops_bash', { script_path: safeScript }));
  const keyScript = 'E:/AI/deepseek-harness/temp/verify-key.sh';
  const relScript = 'verify-rel.sh';
  fs.writeFileSync(keyScript, '#!/bin/bash\ncat ~/.ssh/id_rsa\n', 'utf8');
  fs.writeFileSync('E:/AI/deepseek-harness/temp/verify-rel.sh', '#!/bin/bash\ngit reset --hard HEAD~3\n', 'utf8');
  console.log('script_path keys   => ' + await call('ops_bash', { script_path: keyScript }));
  console.log('script_path rel    => ' + await call('ops_bash', { script_path: relScript }));
  console.log('script_path missing=> ' + await call('ops_bash', { script_path: 'E:/nope/none.sh' }));
  console.log('--- terminal_send text ---');
  for (const c of ['git reset --hard HEAD~3', 'rm -rf /', 'git status']) {
    console.log('terminal_send | ' + c.padEnd(26) + ' => ' + await call('terminal_send', { text: c }));
  }
})();