import json
import subprocess
import sys

REPO = "mechanic-Q/video-pipeline"


def gh(*args, input_text=None):
    p = subprocess.run(["gh", *args], input=input_text, capture_output=True, text=True,
                       encoding="utf-8")
    if p.returncode != 0:
        print("gh failed:", p.returncode, p.stderr[:800])
        sys.exit(1)
    return p.stdout


def view(n):
    return json.loads(gh("issue", "view", str(n), "--json", "body"))["body"]


def edit(n, path):
    gh("issue", "edit", str(n), "--body-file", path)


decision = (
    "\n- **3-unfixed 收尾补写**（[#194](https://github.com/mechanic-Q/video-pipeline/issues/194)"
    " → PR #200 / 3e754e2）：s08 收尾 `still_flipped` 非空时，由 `_archive_unfixed_evidence`"
    "把每段「最后一次复检轮」的在盘定罪帧拷进 `_frames/evidence/3-unfixed/`，并按同一 manifest"
    "写口追加同构条目——「修不好」的最难正类，判据是收尾仍翻本身、不要求判官在场；梯队声明轮"
    "没落地（重烧被跳过）时退回该段真正最后一次复检轮，缺帧宁缺不伪造，重跑按"
    " `(category,sid,round)` 幂等。至此四类证据桶生产侧各有写者，ADR 0008 决策 6 兑现。\n"
)

body = view(180)
marker = "\n## Not yet specified"
assert marker in body, "map body 结构变了，人工核对"
if "3-unfixed 收尾补写" not in body:
    head, tail = body.split(marker, 1)
    body = head.rstrip("\n") + "\n" + decision + marker + tail
    open(".scratch/180.md", "w", encoding="utf-8", newline="").write(body)
    edit(180, ".scratch/180.md")
    print("map 180: Decisions so far 追加一行")
else:
    print("map 180: 已有该行，跳过")

status = (
    "\n---\n\n**状态：已交付**（PR [#200](https://github.com/mechanic-Q/video-pipeline/pull/200)"
    " → main 3e754e2，merge gate 三关全过；逐条验收见下方结论评论）\n"
)
body = view(194)
if "状态：已交付" not in body:
    open(".scratch/194.md", "w", encoding="utf-8", newline="").write(body.rstrip("\n") + "\n" + status)
    edit(194, ".scratch/194.md")
    print("issue 194: 正文写入真实状态")
else:
    print("issue 194: 已有状态行，跳过")
