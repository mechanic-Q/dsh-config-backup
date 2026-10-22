import json
import subprocess
import sys

body = json.loads(subprocess.run(["gh", "issue", "view", "180", "--json", "body"],
                                 capture_output=True, text=True, encoding="utf-8").stdout)["body"]
pairs = [("`_archive_unfixed_evidence`把", "`_archive_unfixed_evidence` 把"),
         ("同一 manifest写口", "同一 manifest 写口")]
need = [p for p in pairs if p[0] in body]
if not need:
    print("无需修正")
    sys.exit(0)
for old, new in need:
    body = body.replace(old, new)
open(".scratch/180-fix.md", "w", encoding="utf-8", newline="").write(body)
p = subprocess.run(["gh", "issue", "edit", "180", "--body-file", ".scratch/180-fix.md"],
                   capture_output=True, text=True, encoding="utf-8")
print(p.returncode, p.stdout.strip()[:120], p.stderr.strip()[:200])
