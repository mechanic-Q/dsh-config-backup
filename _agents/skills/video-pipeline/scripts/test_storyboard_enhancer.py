#!/usr/bin/env python3
"""storyboard_enhancer 自检闸门测试：断言七项检查在合规/违规数据上的行为。

用法：python3 test_storyboard_enhancer.py（无依赖，仅标准库）
"""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(__file__))
import storyboard_enhancer as E


def make_shot(shot_type="远景", hook="reversal", dur=3.9, n_secs=3, prev_out="收束"):
    """构造一段合规的 enhanced.json 条目。秒级指令覆盖到 dur×0.8 以上。"""
    n = int(n_secs)
    step = max(dur * 0.8 / n, 0.5)
    directives = []
    for t in range(n):
        s0 = round(t * step, 1)
        s1 = round((t + 1) * step, 1)
        directives.append({"time": f"{s0}-{s1}s", "action": "动作", "camera": "缓推",
                           "spatial": "中央", "audio": "口播中", "handoff": "锁定"})
    return {
        "id": "shot01", "text": "测试台词。", "chars": 14, "est_seconds": 3.1,
        "duration_est": dur, "节奏意图": "铺垫", "info_point": "测试信息点",
        "shot_type": shot_type,
        "continuity_handoff": {"in": "开场：画面从黑场进入", "out": prev_out},
        "reference_anchors": {"subject_position": "full-frame", "lighting_baseline": "key", "fixed_landmarks": []},
        "hook_type": hook,
        "per_second_directives": directives,
    }


def make_design(tmp, sid="shot01", rhythm="铺垫", strong=False, shot="全景"):
    """构造导演设计 shotXX.md（节奏词 + 镜头 + 信息点 + 可选钩子词），供 ②④⑤⑥ 校验。"""
    d = os.path.join(tmp, "design")
    os.makedirs(d, exist_ok=True)
    txt = f"# {sid}\n\n【情绪基调】{rhythm}，平稳推进\n【信息点】测试信息点\n【场景】测试场景\n【镜头】{shot}，平稳横移\n"
    if strong:
        txt += "【风格渗透】本段是钩子开场，悬念拉起\n"
    open(os.path.join(d, f"{sid}.md"), "w", encoding="utf-8").write(txt)
    return d


def run_checks(enhanced, design_dir=None):
    """直接调用脚本的 main 逻辑（通过临时文件 + 子进程），返回 (report, exit_code)。"""
    tmp = tempfile.mkdtemp(prefix="enhancer_test_")
    enh_path = os.path.join(tmp, "enhanced.json")
    rep_path = os.path.join(tmp, "report.json")
    json.dump(enhanced, open(enh_path, "w", encoding="utf-8"), ensure_ascii=False)
    import subprocess
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "storyboard_enhancer.py"),
           enh_path, "--out-report", rep_path]
    if design_dir:
        cmd += ["--design-dir", design_dir]
    r = subprocess.run(cmd, capture_output=True, text=True,
        cwd=os.path.dirname(__file__))
    rep = json.load(open(rep_path, encoding="utf-8")) if os.path.exists(rep_path) else {}
    return rep, r.returncode


def main():
    results = []

    # 1. 合规数据（景别交替 + 强hook首尾 + 秒级指令覆盖 + 导演设计含节奏词）→ 全 PASS exit 0
    ok = [make_shot("远景", "reversal", prev_out="远景缓入"),
          dict(make_shot("特写", "reveal", dur=5.2, n_secs=5), id="shot02",
               节奏意图="冲击", continuity_handoff={"in": "承接上段", "out": "特写定格"})]
    tmp1 = tempfile.mkdtemp(prefix="t1_")
    make_design(tmp1, "shot01", rhythm="铺垫", strong=True, shot="远景")  # 首段强钩子
    make_design(tmp1, "shot02", rhythm="冲击", shot="特写")
    rep, code = run_checks(ok, design_dir=os.path.join(tmp1, "design"))
    results.append(("合规数据全PASS", rep.get("all_pass") is True and code == 0, rep))

    # 2. 连续同景别（中景→中景）→ ⑦ FAIL exit 1
    dup = [make_shot("中景", "suspense", prev_out="收束"),
           dict(make_shot("中景", "suspense", prev_out="收束"), id="shot02")]
    tmp2 = tempfile.mkdtemp(prefix="t2_")
    make_design(tmp2, "shot01", rhythm="铺垫", shot="中景")
    make_design(tmp2, "shot02", rhythm="建立", shot="中景")
    rep2, code2 = run_checks(dup, design_dir=os.path.join(tmp2, "design"))
    has_dup_fail = any(f["check"] == "⑦景别交替" for f in rep2.get("failed_items", []))
    results.append(("连续同景别报⑦", has_dup_fail and code2 == 1, rep2))

    # 3. 同级微调（中景→中近景）→ ⑦ 放行
    near = [make_shot("中景", "suspense", prev_out="收束"),
            dict(make_shot("中近景", "suspense", prev_out="收束"), id="shot02")]
    tmp3 = tempfile.mkdtemp(prefix="t3_")
    make_design(tmp3, "shot01", rhythm="铺垫", shot="中景")
    make_design(tmp3, "shot02", rhythm="建立", shot="中近景")
    rep3, code3 = run_checks(near, design_dir=os.path.join(tmp3, "design"))
    no_dup_fail = not any(f["check"] == "⑦景别交替" for f in rep3.get("failed_items", []))
    results.append(("同级微调放行⑦", no_dup_fail, rep3))

    # 4. 首段弱 hook（suspense，导演设计也无钩子词）→ ④ FAIL
    weak = [make_shot("远景", "suspense", prev_out="收束"),
            dict(make_shot("特写", "reveal", dur=5.2, n_secs=5), id="shot02")]
    tmp4 = tempfile.mkdtemp(prefix="t4_")
    make_design(tmp4, "shot01", rhythm="铺垫", strong=False, shot="远景")  # 无钩子词
    make_design(tmp4, "shot02", rhythm="冲击", strong=True, shot="特写")
    rep4, _ = run_checks(weak, design_dir=os.path.join(tmp4, "design"))
    has_hook_fail = any(f["check"] == "④Hook分布" and f["id"] == "shot01"
                        for f in rep4.get("failed_items", []))
    results.append(("首段弱hook报④", has_hook_fail, rep4))

    # 汇总
    print("=== test_storyboard_enhancer ===")
    all_ok = True
    for name, passed, rep in results:
        print(f"  {'PASS' if passed else 'FAIL'} {name} (checks={rep.get('checks_total')}, fail={rep.get('fail')})")
        all_ok = all_ok and passed
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
