# CONTEXT.md — 术语表（读报视频流水线）

> 只收语言契约（词 → 精确含义），不写实现。实现细节见 references/ 与脚本 docstring。

## 阶段与门

- **对账门（reconciliation gate）**：进入贵资源阶段（H3 GPU 烧卡）前，核对「下游所需 ID 集合 == 上游产物 ID 集合」的硬校验。数量相等不算通过——ID 错位、缺尾段都必须 FAIL。
- **硬对账唯一化**：对账集中设在进 GPU 前一处（s07 产物对账 + s08 提交前自保），其余阶段保持轻量产物校验，不新增检查点。
- **确认门④+⑤**：成片+封面合并人工审核（审一次），发布前唯一不可跳过闸门。
- **九宫格**：确认门④随消息附发的全段中帧拼图（带段号标签），供人眼终审旋转/构图。
- **保守放行（已废除）**：旧逻辑里 vision 判读调用失败即放行产物的行为。现改为重试后记录报告并转人审。

## 设计权界（2026-09-22 定案）

- **枚举级纠偏（enum-level correction）**：违反自检硬校验时，driver 对设计层**闭合枚举字段**（景别等）的最小自动改写；语义字段（场景/情绪/镜头）任何下游不得改写。每次纠偏必须台账留痕（ADR 0003）。
- **环境预检（preflight）**：driver 进程启动即执行的环境护栏（脚本行尾/语法、ComfyUI、飞书凭证、必需资产），fail-fast 叫人。Strata 离线**不在**预检清单（#85：其在线性由 s04 入口自愈阶梯接管，无人值守场景杀掉服务须能自动恢复到底）；每日资产（报纸图/片头）不在其列，归 new_daily 与 s09。

## 必需资产（2026-09-23 定案）

- **必需资产（required asset）**：缺失即被环境预检拦截、永不静默回退的 skill 自带静态资产（当前仅翻页音效一项）。compose 内既有的合成回退代码语义降级为「不应到达的防御」，不得当作正常路径优化。

## 压缩车道（2026-09-24 定案；后端随 2026-10-12 全切 Strata 更新）

- **压缩车道（compressed lane）**：s04-s07 四个文字设计阶段压成对本地 **Strata** 实例（Windows 主机 8080；KVMem/18201 已退役）的**单次 LLM 调用**、直出 `06_H3prompt` 的车道。七项自检、枚举纠偏、横移配额、交接卡等前置防线随中间设计稿一并失效，质量兜底转移至生成期约束门、s08 消毒/重生成梯队、vision 判读与门④人审。云端调用已全退役（vision 判读 2026-10-14 起本地化，ADR 0007）。
- **Strata 窗口（原 KVMem 窗口）**：压缩车道本地服务的生命周期——s04 入口自动拉起（探活→跨界 powershell 拉起→失败叫人阶梯），s08 生图前自动停止（显存回落双确认），s11 入口再拉、s12 确认门前停（conflict 纪律：Strata 常驻占 94% VRAM 与生图互斥，单条视频常态两次加载）。窗口之外烧卡/后期独占显卡，服务不得在场。
- **自愈阶梯（self-heal ladder）**：Strata 拉起异常不叫人，按固定顺序自动处置（清残留→修复重启→复测），最多 2 轮修复性重启（窗口冷启动不占预算）；仍不在线即硬失败叫人。加载窗口 300s；就绪后首请求可达 ~70s 属专家缓存预热（RAM 降级随请求升温自愈），**不算故障、不触发重启**。

## 旋转防疫（2026-09-05 定案）

- **横向行进运镜（lateral travel）**：相机主动沿画面左右方向行进的运镜（trucks/tracks laterally、truck right、平行扫掠）。经验证为 H3 90° 旋转的高危特征（5/5 实锤旋转镜头命中；正常镜头约 1/3 含横向词但多为从属描述）。
- **运镜消毒（camera sanitization）**：只把横向行进运镜句替换为竖直安全句（静止+缓慢纵向漂移），场景/光线/声景描述原样保留。目的是救高危镜头的同时保住良好镜头的画面丰富性。
- **重生成梯队**：翻转段按「消毒版重生成（运动句整删）→ 按因竖直化重写」固定顺序处理，重烧 ≤2 轮（原样重采样已废除——0906/0912 两期实证重掷同概率不放大概率）；每轮重烧名单以判官复判为准（confirmed/judge_found 重烧、local_false 只记账），本地先判→判官复判→人工指认三轮判定仍不修即交人（T16 #128）。
- **判读兜底分层（judgment fallback tiers，2026-10-14 定案）**：抽帧判读三层信号——本地 VLM（Qwen3-VL，llama-server @8100，主判，判读窗口用完即关——2026-10-14 修订，ADR 0007）× CLIP 线性探针（微调，平行兜底，数据 ≥100 段后首训）经 `reconcile` 纯函数合成：一致采信 / CLIP 翻转 vs VLM 非翻转升级人审点名（宁误报不漏报）/ 单路缺失自动降级 / 双失败照旧留痕。旋转主防线仍是 s07 横移门 + 确认门④九宫格人审，机器判读只是辅助信号（ADR 0007）。
- **判读形态（judge form，2026-10-14 T8 定案）**：主判缺省走 **Jev 形态**（判别式投影：prompt 只答一个字 正/倾/翻，max_tokens=1 + logprobs 投影出三分类分布）——与 **JSON 形态**（生成式 `{"verdict": ...}`）共享判读正文只换输出指令；`DRIVER_VISION_MODE=json` 一条 env 显式回退，无静默自动切换。两形态与盲比/校准共用 vision_common 单一事实源。
- **置信度门控（confidence gating，2026-10-14 T8 定案）**：主判置信度经**温度缩放校准**（`jev_calibrate.py` 拟合参数落盘 references/jev-calibration.json）后过三级阈值：≥0.90 自动过 / 0.60-0.90 记录抽检 / <0.60 进九宫格点名。红线：校准参数未落盘时门控整体停用（未校准 softmax 虚高不得参与门控）。
- **影子评估（probe shadow，2026-10-14 T9 定案）**：CLIP 探针在主判完成后对同一批帧的离线复判，结论只落台账（s08.vision.probe_shadow）不进主链——影子不点名、不重烧；env `DRIVER_PROBE_SHADOW` 缺省开。
- **换岗门槛（handover gate，T10 定案）**：探针升主判的唯一依据是**真值门槛**（≥100 人工判定 且 ≥50 场景（distinct 期））+ 重训后验收（真实翻转召回 ≥90% 且假翻转 ≤2 次/期）；行数不算数，看场景多样性。确认门④九宫格人审永久保留（质量设计非过渡方案）。
- **判官（second-opinion judge，2026-10-16 T14 #126 定案）**：S8 判读闭环的第二意见层——WSL 侧 driver 经 interop 起 Windows 侧 DSH 桌面宿主 headless 子进程（`scripts/dsh_judge.py`，profile 进仓 `dsh-profile/judgeprobe/`），用桌面宿主视觉模型对本地 VLM 已判帧复判。判官词表是**四标签**（正常/翻转90/翻转180/倾斜，
定义在 dsh_judge.py LABEL_*），与本地三分类词表（正常/倾斜/翻转，vision_common.py
VERDICT_LEVELS）**不同源也非同粒度**——翻转90/翻转180 是判官的细分，本地只有「翻转」；
merge 时只按翻/非翻对齐比较。立场是**复核本地而非替代**：复判范围第 1 轮全量、第 2/3 轮只判本轮重烧段；`merge()` 四象限（本地翻+判官翻=正类重烧 / 本地正常+判官翻=抓漏报重烧 / 本地翻+判官正常=误报只记账）；fail-open——判官不可用/超时/输出不合法一律采信本地放行只记 error，不阻塞生产。判官判定不是真值，进独立字段 judge_*，vision_verdict 保持本地原判。开关/超时/换 profile 走 env `DRIVER_JUDGE` / `DRIVER_JUDGE_TIMEOUT` / `DRIVER_JUDGE_PROFILE`。跨侧口径：帧路径经 `to_win_path()` 转 Windows 绝对路径进 prompt。
- **轮次台账（judge rounds ledger，2026-10-17 T16 #128 定案）**：判官复判逐轮留痕的台账键——顶层平键 `judge_rounds`（每段每轮判定 list）与 `judge_disagreements`（判官与本地分歧段 list），Ledger 跨重启按 list 拼接去重累积读回；禁用嵌套 dict 键（`_merged_root` 对顶层 dict 块是增量整体赢，嵌套键重启即丢历史轮次）。
- **人工指认（human flag，2026-10-17 T17 #129 定案）**：确认门④群内自由文本指认翻转（指认词命中、带不带段号均认）或 CLI `--flag-shots` 指名段号与结论。执行语义：指认瞬间先存被定罪帧（重烧覆盖前，带 who/when/verdict 落台账 `human_flags` 顶层平键）→ 人工真值回填 rotation-dataset `user_flagged`（source=human-flag，独立字段不被模型结论覆盖，可统计）→ 指名段重烧+复判，仍翻转则再存证+换法重烧，直到不翻转或人喊停——不停机等 agent。
- **证据桶（evidence buckets，2026-10-17 T18 #130 定案；2026-10-22 ADR 0008 扩为四类）**：证据桶共四类——`1-confirmed`（本地翻+判官翻，正类）/ `2-judge-found`（本地正常+判官翻，强负类=抓漏报）/ `3-unfixed`（重烧后仍翻，最难正类）/ `4-human-flag`（人工指认翻转，正类，与判官四象限不同源）。manifest 为账（离线件 `references/t15-evidence-*/manifest.json` + 生产件 `<slug>/_frames/evidence/manifest.json`），`load_evidence_buckets` 单一解析口，训练链经 `row_material` 三源链接消费。**1/2 归类只认判官轮**：判官缺席时不新增这两类、不伪造标签，可见性由行内 `evidence_frames` 承担；`3-unfixed` 由 s08 收尾 `_archive_unfixed_evidence` 按 `still_flipped` 写（#194），判据是「重烧后仍翻」本身、不要求判官在场；`4-human-flag` 只由指认回调 `_flag_commit` 写。
- **旋转数据集（rotation dataset，2026-10-18 #148 收口）**：逐期累积的镜头特征 + 判定结论，一个文件（`references/rotation-dataset.jsonl`）只担**一个身份——真值单一事实源**；写入只走 `write_rows` 单一写口（`append_records` / `set_user_flag` / t15 回写都过它）。**入库纪律**：s08 跑完自动追加当期行 → **当班 agent 收尾前必须提交**（不得为干净工作树丢弃诚实记录，判读服务不可用就如实写「未判」）→ 校验口径 = `git status` 该文件无未提交改动 + 当期行数与镜头数一致。**测试不得读写活数据集**：夹具是 `scripts/fixtures/` 下的期次冻结快照，跑器给每个测试子进程注入 `ROTATION_DATASET_READONLY=1`，此时写活路径一律抛 `DatasetReadOnlyError`（显式传 tempfile 路径不受限）。
- **真值帧冻结（frozen truth frame，2026-10-22 ADR 0008 定案）**：人审指认翻转的瞬间，定罪帧在重烧覆盖之前被拷进不可变目录，`{frame, sha256, round, category}` 写进行内 `evidence_frames`；读取侧对 `user_flagged=True` 的行按「行内证据帧 → 证据桶帧 → 工作帧」取真值。**sha256 是硬条件**：账实不符即弃帧告警、不回落工作帧；整行无可用帧则不进评测池。
- **帧失效（frame_stale，2026-10-22 ADR 0008 定案）**：帧已被重烧覆盖、且没有冻结副本可回填的标注行——`frame_stale=true` + `frame_stale_reason`，消费侧不进评测池但**仍计入** `stats` 判定数（真值没错，错的是帧）。
- **证据链闭环（evidence chain closure，2026-10-22 ADR 0008 定案）**：判读闭环的落账段必须自洽——「识别到」之后要依次能答：账单有没有（manifest）、行里有没有（`evidence_frames`）、训练链取不取得到（`row_material`）、重烧之后取的还是不是那一帧（sha256）。四问缺一即断链，与判读准确率无关。

## 无人值守入口（2026-09-25 定案）

- **无人值守入口（unattended entry）**：每日定时任务驱动的 driver 启动通道，职责链固定为：前置检查（上游任务已结束、当日输入就绪）→ 环境拉起 → 脱离启动 driver。入口层不代用户做任何确认门决策。
- **环境拉起（environment bring-up）**：入口层把动态环境恢复到环境预检可通过状态的职责（当前仅 ComfyUI 在线一项），自带「启动→就绪等待→复查窗口」完整阶梯，彻底失败才放弃当日。环境预检语义不变：它假设环境已被拉到位，只对拉不起来的事 fail-fast 叫人。
- **放弃当日（give-up-for-the-day）**：入口层前置检查或环境拉起彻底失败的终态——飞书通知根因与人工修复路径后结束当日运行；现场原样保留（stamps 不动），当日可人工重跑接续。

## 身份与资源

- **driver**：run_pipeline.py 的全自动编排进程；agent 只在确认门与异常时回到现场。
- **安装位置假设（install-location assumption）**：把「脚本/资产在哪」写成仓库外的绝对路径常量，而不是从脚本自身位置派生。后果是「你改的那份不一定是跑的那份」——生产链路指向另一份 git 副本（分叉历史、可能带未提交补丁），维护副本里修好的 bug 跑生产照样复现（#152 实锤）。**判定口径**：任何生产脚本里出现指向本仓库某份副本的绝对路径即为违反；跨侧（WSL↔Windows）路径口径不算安装位置，走单一转换模块。
- **stamp**：`_driver/stage_sX.json` 完成标记，断点续跑的依据。
- **台账（report）**：`_driver/report.json`，各阶段尝试/耗时/错误与质量事件（消毒/重生成/钳制）的常开记录。资源对比口径：未缓存输入 + 缓存读 + 输出 + reasoning 总令牌与墙钟时间（agent 侧数据在 Hermes state.db）。
