import json
import subprocess
import sys

REPO = "mechanic-Q/video-pipeline"
KEY = "## 实施记录 / 验收核验"

body = subprocess.run(
    ["gh", "issue", "view", "189", "-R", REPO, "--json", "body", "-q", ".body"],
    capture_output=True, check=True).stdout.decode("utf-8", "replace")
section = open("C:/Users/LMR/.agents/skills/video-pipeline/.scratch/189-section.md",
               encoding="utf-8").read().strip()

if KEY in body:
    print("already appended, skip")
    sys.exit(0)

new = body.rstrip() + "\n\n" + section + "\n"
with open("C:/Users/LMR/.agents/skills/video-pipeline/.scratch/189-body-new.md",
          "w", encoding="utf-8", newline="\n") as f:
    f.write(new)
print("new body bytes:", len(new.encode("utf-8")))
