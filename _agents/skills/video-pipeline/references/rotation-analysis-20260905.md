# H3 镜头 90° 旋转问题 · 跨跑单分析（2026-09-05）

> 数据来源：daily-china-20260902 / 20260903(-v3) / 20260904 三次跑单的 06_H3prompt + 07_成片/1080P 全量产物，
> 及 Hermes 会话库（state.db）中的原始 driver 日志。注：文中 s07/s08 为 2026-09-07 阶段号重整后的新名（当时称 s6/s9）。本文是 `schema.py` 横移契约与 s07/s08 防疫逻辑的依据。

## 1. 问题镜头清单（视频抽帧实锤）

| 跑单 | 旋转镜头 | 证据 |
|---|---|---|
| 0903（旧版 prompt） | shot06（蟠桃台地）、shot07（桃枝月宫）、shot16（雪岭炮阵） | 当场验收发现，prompt 级重生成修复，修复后产物（0903-v3）全 20 段正立 |
| 0904（driver 首跑） | **shot07、shot08、shot15、shot19**（4/23） | 2026-09-05 抽帧目检 + 中帧对照表；vision 只报了 1 段，且「判读失败保守放行」1 段 |

## 2. 共性结论（Q2 的答案）

**5/5 实锤旋转镜头的 prompt 全部以「横向行进类运镜」为主导相机指令**，措辞高度一致：

- 0903旧-06: `The camera trucks laterally with large amplitude ... gliding smoothly past`
- 0903旧-07: `The camera tracks laterally with medium amplitude at a slightly fast speed, sweeping across ...`
- 0903旧-16: `trucks laterally with large amplitude ... continues trucking laterally`
- 0904-08: `slow, steady tracking shot from left to right, moving parallel to the street`
- 0904-19: `slow, steady Truck Right movement ... gliding parallel to the rows of tablets`

而修复版（0903-v3）把运镜改成了 **static（静止）或 tilt upward（纵向摇）后全部正立**。
对照组：约 1/3 的正常镜头也含横向词，但多为小幅度/从属描述（如 0904-04 `truck to the right with small amplitude` 正常）——
横向行进是**强危险因子（必要不充分）**，不是决定性成因。机制假说：H3 类模型在 9:16 画幅里被要求
沿建筑成排结构「平行行进」时，有一定概率通过旋转世界来满足横向行进先验；云海+人物极小的天宫风格放大了该倾向。

## 3. 检测手段负结果记录（同样重要，避免重复踩坑）

| 方法 | 结果 |
|---|---|
| 边缘方向直方图（Sobel 强梯度纵横比） | 完全无区分度（旋转段 0.46–0.52 vs 正常段 0.45–0.57），云海中性纹理淹没结构信号 |
| 天空亮度位置（左/中/右三分亮度不对称） | 单帧巧合命中，3 帧中位数全量验证漏 3/4（07/08/15），且 0901 出现疑似误报——弃用 |
| glm-4.6v 绝对判断（天空位置描述） | shot07 明显横躺仍判「正常」（本次成片事故直接原因之一） |
| glm-4.6v 人物姿态提示词 | 4 帧全判「正常」 |
| glm-4.6v 双图二选一（2-AFC） | 仅抓到 shot19，仍漏 07/15/08 |
| 更换视觉模型（ds-vision-exp / agnes-2.5-pro / nemotron-omni） | 路由层 402/格式/401，均不可用 |

**结论**：本风格（写意云海 + 1/15 人物）下没有任何廉价检测器或当前可用 VLM 能可靠判定旋转。
因此防线设计为：**s07 生成端预防（横移门+消毒，唯一在 GPU 烧钱前的确定性拦截）+ 确认门④九宫格人审（人眼终审）**；
vision 判读降级为辅助信号+留痕（失败重试 2 次后记录报告，不再「保守放行」）。

## 4. 已实施的防线（2026-09-05）

1. `director_to_h3_llm.py` s07：横移运镜门（`schema.lateral_camera_sentences`）→ 禁横移重试 → 仍命中句级消毒
   （`schema.sanitize_camera_text`，只动运镜句，场景/光线/声景保留）并记录 `camera_sanitized`。
2. 同脚本：工作清单从设计目录推导（消灭 shot9999 探测噪音）；失败段自动补跑一轮；`RESULT` 行 + exit 1。
3. `run_pipeline.py` s07/s08：prompt ID 集合 == raw 段 ID 集合 的硬对账（s08 烧卡前再验一次，缺尾段不再静默）。
4. s08 重生成梯队：原样重生成 → 运镜消毒后重生成 → 硬失败叫人。
5. `_check_frames`：重力提示词 + 判读重试；失败记录报告，确认门④附「镜头九宫格」由人终审。
6. `_clamp_srt`：字幕 cue 尾部钳制到成片时长（0904 末条 cue 越界 2.2s 被裁教训）。
7. `_driver/report.json` 常开台账：各阶段尝试/耗时/错误 + 消毒/重生成/判读/钳制记录。
