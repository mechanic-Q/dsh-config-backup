(function () {
  // cc-safety-net: DSH adapter patch (v2)
  // v1: dialect map + ops_bash / wsl / ssh_exec -> posix
  // v2: + ops_bash.script_path is read and checked as the command text
  //     + terminal_send.text is checked (dialect auto)
  const fs = require('fs');
  const P = 'C:/Users/LMR/.dsh/profiles/desktop/node_modules/cc-safety-net/dist/chunks/index-1x1d9s1m.js';
  if (!fs.existsSync(P)) { console.error('not found: ' + P); process.exit(2); }
  let s = fs.readFileSync(P, 'utf8');
  const before = s;

  // --- 1. import readFileSync
  const A1 = 'import{Z,x,it,l}from"./index-z14g58a6.js";';
  const A1N = 'import{readFileSync as rf}from"node:fs";' + A1;
  if (!s.includes(A1N)) {
    if (!s.includes(A1)) { console.error('anchor 1 not found'); process.exit(3); }
    s = s.replace(A1, A1N);
  }

  // --- 2. dialect map (rewritten canonically, covers original and v1)
  const mapRe = /var et=new Map\(\[.*?\]\)/;
  const MAP = 'var et=new Map([["bash","posix"],["pwsh","powershell"],["ops_bash","posix"],["wsl","posix"],["ssh_exec","posix"],["terminal_send","auto"]])';
  if (!s.includes(MAP)) {
    if (!mapRe.test(s)) { console.error('anchor 2 not found'); process.exit(3); }
    s = s.replace(mapRe, MAP);
  }

  // --- 3. extractor: accept script_path (ops_bash) and text (terminal_send)
  const A3 = 'let p=t,a=p.command;';
  const A3N = A3
    + 'if((typeof a!=="string"||a.trim()==="")&&n==="ops_bash"&&typeof p.script_path==="string"&&p.script_path.trim()!=="")'
    + 'try{a=rf(f(o,p.script_path),"utf8")}catch{return void 0}'
    + 'if((typeof a!=="string"||a.trim()==="")&&n==="terminal_send"&&typeof p.text==="string")a=p.text;';
  if (!s.includes(A3N)) {
    if (!s.includes(A3)) { console.error('anchor 3 not found'); process.exit(3); }
    s = s.replace(A3, A3N);
  }

  if (s === before) { console.log('already patched (v2)'); return; }
  const tmp = P + '.tmp';
  fs.writeFileSync(tmp, s, 'utf8');   // new inode: breaks pnpm hardlink
  fs.unlinkSync(P);
  fs.renameSync(tmp, P);
  console.log('patched v2, bytes=' + Buffer.byteLength(s) + ', nlink=' + fs.statSync(P).nlink);
})();