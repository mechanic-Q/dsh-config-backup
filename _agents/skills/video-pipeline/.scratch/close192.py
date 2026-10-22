# -*- coding: utf-8 -*-
"""#192 收尾：正文回填实施记录 + 地图 #180 追加 gist（先读全文再拼装，防覆盖）。"""
import io
import json
import subprocess


def gh_json(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("gh failed: %s\n%s" % (args, r.stderr))
    return json.loads(r.stdout)


app192 = io.open(".scratch/app192.md", encoding="utf-8").read().strip("\n")
gist = io.open(".scratch/gist180.md", encoding="utf-8").read().strip("\n")

b192 = gh_json(["issue", "view", "192", "--json", "body"])["body"]
if "## 实施记录（2026-10-22）" in b192:
    raise SystemExit("192 已回填过，别重复追加")
new192 = b192.rstrip("\n") + "\n\n" + app192 + "\n"
io.open(".scratch/b192.md", "w", encoding="utf-8", newline="\n").write(new192)

b180 = gh_json(["issue", "view", "180", "--json", "body"])["body"]
if "归档门语义收紧" in b180:
    raise SystemExit("180 已追加过 gist")
anchor = "## Not yet specified"
if anchor not in b180 or "## Decisions so far" not in b180:
    raise SystemExit("180 锚点缺失")
i = b180.find(anchor)
new180 = b180[:i].rstrip("\n") + "\n\n" + gist + "\n\n" + b180[i:]
io.open(".scratch/b180.md", "w", encoding="utf-8", newline="\n").write(new180)

print("192: %d -> %d；180: %d -> %d" % (len(b192), len(new192), len(b180), len(new180)))
print("192 段落齐: Parent=%s What=%s AC=%s" % ("## Parent" in new192,
      "## What to build" in new192, "## Acceptance criteria" in new192))
print("180 段落齐: Decisions=%s NotYet=%s OutOfScope=%s" % ("## Decisions so far" in new180,
      "## Not yet specified" in new180, "## Out of scope" in new180))
