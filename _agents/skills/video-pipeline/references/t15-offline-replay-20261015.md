# 离线复现 2026-10-15 期判读闭环（T15 · #127 · 2026-10-16）

> 任务票 #127（父 Spec #132）：用 T14 的 dsh_judge 判官对 10-15 期 19 段做全量复判，
> 落三类存证，把 judge_* 基准字段回写数据集。执行环境：离线复现（无 GPU、不真烧卡），
> 分支 feat/t15-judge-replay-baseline（rebase 于 T14 tip 1c358dd）。
> 工具：scripts/t15_replay.py；素材：references/t15-evidence-20261015/。

## 0. 结论一句话

**判官（deepseek-v4.1-flash，T14 通道）抓出 5 个翻转段 shot03/09/15/18/19，与人工确认的
5 段完全一致（5/5，零多报零漏报）**；本地 Qwen3-VL 当日 19/19 全判「正常」的 5 个漏报被
全部纠正，0 误报。数据集 19 行已回写 judge_* 基准字段，本地原判 vision_verdict 一字未动。

## 1. AC 对账

| AC | 要求 | 结果 |
|---|---|---|
| ① | 19 段全判官判定+判词，判官抓漏报须与人工确认 5 段一致否则逐条说明 | **一致 5/5**，见 §2 表；无需逐条说明 |
| ② | 三类素材各≥1 落盘带轮次可溯源帧 | ①2 条（合成）/②15 张（3 轮×5 段）/③5 张，均带 round 前缀+sha256+source_frame，见 §3 |
| ③ | 数据集新行含 judge 字段、本地原判不覆盖 | 19 行 +6 个 judge_* 键；vision_verdict 19/19 未动（§4 diff 实证） |
| ④ | 报告成文（哪几段/什么证据/数据集 diff） | 本文 |
| ⑤ | 流水线代码零改动 | git diff main --name-only 无 run_pipeline.py/vision 判读代码；本票新增仅 t15_replay.py + 测试 + 报告 + manifest（§5） |

## 2. 判官复判结果（round0 全量 19 段，判词为判官原话）

| 段 | 本地原判 | 判官 | conf | 判官判词（why） | 判翻轮次 | user_flagged |
|---|---|---|---|---|---|---|
| shot01 | 正常 | 正常 | 0.95 | 天空在上，飞檐重楼地平线水平 | 1 | null |
| shot02 | 正常 | 正常 | 0.95 | 塔身竖直，天空在上云海在下 | 1 | null |
| **shot03** | 正常 | **翻转90** | 0.97 | 地面在左缘竖条，天空在右，主体横躺 | 3 | true |
| shot04 | 正常 | 正常 | 0.95 | 穹顶在上台阶在下，人物站立 | 1 | null |
| shot05 | 正常 | 正常 | 0.95 | 天空山峦在上，人物立于平台 | 1 | null |
| shot06 | 正常 | 正常 | 0.95 | 天空在上麦田在下，地平线水平 | 1 | null |
| shot07 | 正常 | 正常 | 0.95 | 殿门朝上开，立柱竖直 | 1 | null |
| shot08 | 正常 | 正常 | 0.95 | 天空在上棉田在下，无人机正常悬飞 | 1 | null |
| shot09 | 正常 | **翻转90** | 0.98 | 人物横躺，天空日落位于右侧，地平线竖直 | 3 | true |
| shot10 | 正常 | 正常 | 0.95 | 楼阁在上场地在下，灯笼竖挂 | 1 | null |
| shot11 | 正常 | 正常 | 0.90 | 卷轴竖持，球场远景地平线水平 | 1 | null |
| shot12 | 正常 | 正常 | 0.95 | 天空在上管线延伸向远方 | 1 | null |
| shot13 | 正常 | 正常 | 0.95 | 铁塔竖直向上，天空在上 | 1 | null |
| shot14 | 正常 | 正常 | 0.95 | 海平线水平，天空在上建筑在下 | 1 | null |
| **shot15** | 正常 | **翻转90** | 0.98 | 人物横躺，海面竖直居中，天空在右缘 | 3 | true |
| shot16 | 正常 | 正常 | 0.95 | 海天在上，罗盘竖持船身正立 | 1 | null |
| shot17 | 正常 | 正常 | 0.95 | 雪山在上军阵立于地面 | 1 | null |
| **shot18** | 正常 | **翻转90** | 0.98 | 军阵横躺，地面在左，天空在右 | 3 | true |
| **shot19** | 正常 | **翻转90** | 0.97 | 旗帜横倒，地面在左，天空在右 | 3 | true |

- 判翻段全部为「翻转90」，conf 0.97–0.98；正常段 conf 0.90–0.95。
- 「判翻轮次」列=judge_flip_rounds：离线复现不烧卡，round1/2 复用的仍是原始现场帧，
  判官三轮复判均维持翻转结论——这本身是**判官判断稳定性的三次独立采样**。
- 与人工确认（Spec #132：shot03/09/15/18/19）**完全一致**，AC①的「逐条说明」分支未触发。

## 3. 三类存证（references/t15-evidence-20261015/，jpg 留本地不入 git，manifest.json 做账）

| 类 | 目录 | 数量 | 内容 |
|---|---|---|---|
| ① 判官确认本地翻转（真阳） | 1-confirmed/ | 2 | 本期本地 0 误翻、结构为空，按 Spec 测试决定用**构造样本补位**：shot02 帧旋转 90°/180°（PIL），标签由构造保证，同批帧两轮判官结论一致（shot02rot90→翻转90 conf 0.97、shot02rot180→翻转180 conf 0.95），并附原图负对照。synthetic=true 明标 |
| ② 判官抓本地漏报（本期主产物） | 2-judge-found/ | 15 | 5 段 × round0/1/2 三轮，round0 无前缀说明=真漏报现场；round1/2 帧名带轮次 |
| ③ 三轮未修好（离线形态） | 3-unfixed/ | 5 | final_shotNN.jpg；离线无 GPU 不真重烧，真机三轮素材归 T16 生产回合 |

每条 manifest 证据带：category / sid / round / frame（Windows 口径绝对路径）/
source_frame / sha256 / judge / conf / why / local_verdict / synthetic / note，可逐帧溯源。

**判官调用台账（5 次，全 ok）**：round0 71.8s in 34842 out 2450；round1 37.7s 22870/867；
round2 41.0s 14547/748；synthetic-a 25.8s 11353/362；synthetic-b 77.4s 9433/2111。
negative_control=[]（原图负对照通过：判官未把正立原图判翻）。

## 4. 数据集回写 diff（references/rotation-dataset.jsonl，470 行 → 470 行）

- **恰好 19 行变更**（全部 2026-10-15 期 shot01-19），字段级 diff：
  judge_verdict/judge_conf/judge_why/judge_flip_rounds/judge_rounds/judge_source
  各 19 行新增；user_flagged + user_flagged_source 仅 5 段（source=t15-offline-replay-20261016）。
- **vision_verdict 19/19 保持「正常」**——本地原判是留痕历史，回写只做增量（AC③）。
- 其余 451 行零触碰；schema 无键丢失；judge_source=dsh_judge/deepseek-v4.1-flash (T14 #126)。
- judge_rounds 是逐轮台账（round/local/judge/kind/conf/why），轮次分歧全部可回放。

## 5. 流水线代码零改动证明（AC⑤）

git diff main --name-only（12 文件，全部属 T14 已合内容或本票新增）：
本票新增 scripts/t15_replay.py、scripts/test_t15_replay.py、references/t15-evidence-20261015/manifest.json、
本文档；T14 既有 scripts/dsh_judge.py、scripts/test_dsh_judge.py、dsh-profile/judgeprobe/* 等。
**不含 run_pipeline.py、director_to_h3_llm.py 等任何流水线判读/生成代码**——判官完全旁路运行。

## 6. 测试

scripts/test_t15_replay.py（5 节全绿，裸脚本风格）：
四象限合成（merge 的 confirmed/judge_found/local_false/judge_normal + reburn 名单）、
①类 sid 防折叠（shot02rot90 归一 shot02 但不与 shot02/下划线变体互染）、
三类目录与期锚常量、save_evidence 条目结构（sha256/frame_win/round 前缀）、
数据集回写字段语义（manifest↔数据集只读互证：本地原判不动、flagged=5 段）。
test_dsh_judge / test_rotation_dataset / test_jev_calibrate / test_run_pipeline 同步复跑全 PASS。

## 7. 踩坑记录（给 T16 及后续复现者）

1. **①类合成 sid 折叠互染**：合成样本 sid 若用下划线变体（shot02_rot90），判官侧
   sid_norm 会把它折叠回 shot02，同批三条「同名」段互相污染输出——首跑实证：负对照
   原图被判「翻转90/180」（误报）。改为 shot02rot90/shot02rot180（无下划线）后干净。
   教训：**给判官喂构造样本时，sid 必须在归一化后仍互相可区分，且不与真实段撞名**。
2. **dryrun 与真跑共目录**：dryrun 也 rmtree 存证目录——曾把真跑的 22 张证据帧连带
   manifest 全删（帧可由源重拷，但判官判词/Token 台账不可再生，被迫整轮重跑）。
   已修：dryrun 隔离到 t15-evidence-20261015-dryrun/。
3. **GBK console**：git bash 下 python print 中文/⚠ emoji 走 GBK 会 UnicodeEncodeError
   （test_reconcile_clip 假阳性 FAIL 一次）。跑测试带 PYTHONIOENCODING=utf-8。
4. **帧路径双口径**：数据集/manifest 存 Windows 口径（E:\...），代码内部 POSIX 口径，
   经 dsh_judge.to_win_path 互转——判官 CLI 读盘必须 Windows 形式。
5. **重烧轮语义**：离线复现没有真重烧帧，round1/2 判的是原始帧。这不是缺陷而是设计：
   复现目标是「判官能否稳定抓出并维持结论」，真机重烧闭环归 T16。

## 8. 遗留与移交

- ③类 unfixed 的真机三轮素材（真重烧→真消失）由 T16 生产回合补齐。
- judge_conf 阈值未调（本轮 0.90-0.98 区间区分度足够），若 T16 遇边界样本再校准。
- 报告+manifest 进 git，jpg 证据帧按 .gitignore 约定只留本地（与 _frames 同策略）。
