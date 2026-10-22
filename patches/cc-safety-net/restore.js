(function () {
  const fs = require('fs');
  const path = require('path');
  const P = 'C:/Users/LMR/.dsh/profiles/desktop/node_modules/cc-safety-net/dist/chunks/index-1x1d9s1m.js';
  const src = path.join(process.env.DSH_CCSN_BACKUP_DIR || 'C:/Users/LMR/.dsh/patches/cc-safety-net', 'index-1x1d9s1m.orig.js');
  if (!fs.existsSync(src)) { console.error('backup missing: ' + src); process.exit(2); }
  if (!fs.existsSync(P)) { console.error('target missing: ' + P); process.exit(2); }
  const tmp = P + '.restore';
  fs.writeFileSync(tmp, fs.readFileSync(src));   // new inode
  fs.unlinkSync(P);
  fs.renameSync(tmp, P);
  console.log('restored original, nlink=' + fs.statSync(P).nlink);
})();