# Spike 报告：判官运行体实跑验收（T14 #126，2026-10-16）

> 票 #126 的验收③实跑记录：三帧金标准 + 双跑一致性。代码在 scripts/dsh_judge.py，
> profile 装机说明在 dsh-profile/judgeprobe/README.md。
> 环境：Windows 10 22H2 + WSL2 Ubuntu；判官 = DSH 桌面宿主（profile judgeprobe，
> provider=buddy，model=glm-5.3-flash）；driver = WSL 侧 run_pipeline。

## 结论速览

| 验收项 | 结果 |
|---|---|
| ① 环境探针 | `python scripts/dsh_judge.py status` → 判官可用（profile 实配模型名） |
| ② 跨端通道 | WSL driver 起 Windows 宿主 headless 会话，read_image 逐帧判读 ✓ |
| ③ 三帧金标准 | shot05→正常 / rot90→翻转90 / rot180→翻转180，全对 |
| ③' 双跑一致性 | 同帧集连跑两次，四象限归并结果一致（确认翻转/新发现/误报/一致正常 全同） |

## 三帧金标准（人工标注 → 判官输出）

| 帧 | 人工 | 判官 | 本地 VLM |
|---|---|---|---|
| shot05（正立） | 正常 | 正常 | 正常 |
| rot90（横躺） | 翻转90 | 翻转90 | 翻转 |
| rot180（倒立） | 翻转180 | 翻转180 | 翻转 |

双跑间隔数分钟、会话独立；usage（in/out/reasoning tok）逐次记录在台账，
两次 final JSON 逐段一致（judge/conf/why 语义一致，why 措辞可有微小差异）。

## 复跑方式

```bash
# Windows 侧探针（先确认运行体在盘、profile 已装）
python scripts/dsh_judge.py status
# WSL 侧自检（跨端通道 + 2 字符应答）
python scripts/dsh_judge.py selftest
# 真判：run_pipeline 判读闭环第 8 步自动触发（DRIVER_JUDGE=0 可关层）
```

金标准帧：本轮实跑用的 3 帧取自 10-16 的重烧回归集（shot05 原帧 + 旋转 90/180 派生帧），
未入库（体积原因）；复跑时取任意已判片段的帧按上表人工标注即可对拍。
