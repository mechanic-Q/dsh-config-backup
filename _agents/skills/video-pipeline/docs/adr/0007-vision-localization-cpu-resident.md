# ADR 0007：抽帧 vision 判读本地化——CPU 常驻 llama-server + CLIP 兜底分层

- 状态：已接受（2026-10-14，spec #101 拍板；票 #102/#103-#106 落地 / #107 收口）
- 取代：[ADR 0006](0006-strata-backend-and-window.md) 决策①中「**vision 判读保留
  9router 云端**——多模态物理上不可本地化」的结论。0006 的连通口径（WSL 网关发现、
  `--host 0.0.0.0`）、生命周期阶梯语义（探活→拉起→就绪等待→失败叫人/降级）整体沿用。
- 上下文：每日无人值守读报链里，抽帧竖屏判读是全链最后一个云端依赖（9router
  glm-4.6v）。2026-10-14 期实锤 19 段中 5 段 503/空响应，判读整批空窗只能人审九宫格
  兜底；且 vision 能力（判读、封面复检、报纸日期三合一校验）散在云端，行为不可控、
  结论不可复现、数据集结论无法归因。

## 决策

1. **判读服务 = 本机 CPU 常驻 llama-server**：llama.cpp 官方构建（pin b11603，
   win-cpu-x64）+ Qwen3-VL Instruct Q4_K_M GGUF + mmproj F16，端口 8100
   （0.0.0.0，WSL 经网关访问，同 0006 口径）。**否决 GPU 判读窗口**：CPU 推理与
   Strata/ComfyUI 烧卡窗口零显存交互，不新增窗口纪律、不改 s08 边烧边判时序。
   不开机自启；driver s08 判读入口 `_ensure_vision` 自愈（探活→失联 WMI 自动拉起
   一次→就绪等待→彻底失败降级留痕），复用 0006 阶梯语义。
2. **端点同构切换**：判读底层本就是 OpenAI 兼容调用（base64 image_url），切换 =
   env 缺省翻转（`DRIVER_VISION_API` 缺省 9router → 本地 8100；`DRIVER_VISION_MODEL`
   缺省 → 盲比定档 alias）。判读面（三级判定/单帧单问/横构图多帧/重试×2/留痕点名）
   **零改动**，fake 端点五形态测试证明语义不变；9router 保留为显式 env 回退。
3. **模型定档 = 存量数据集盲比**：2B/4B/8B 三档对 rotation-dataset 存量帧（帧在盘行，
   10-14 时点 370 行，随每日增长）盲比 glm-4.6v 结论，判据「翻转 0 漏检 + 总体一致率
   ≥98%」取最小达标档；盲比工具 `vision_blindbench.py` 独立成件，报告落 docs/spikes/
   即定档与换档依据。翻转真值以人标行（user_flagged=true）为准——glm 基线在这些行
   本就未判/误判，一致率只对 glm 有效基线行计。
4. **CLIP 平行兜底层**：open_clip ViT-B/32 + 线性探针（数据集 ≥100 段后 T7 首训），
   输出映射三级 verdict + 置信度；两路经 `reconcile` 纯函数合成——一致采信 / CLIP
   翻转 vs VLM 非翻转升级人审点名（宁误报不漏报）/ 单路缺失自动降级 / 双失败照旧
   留痕。层缺席时行为与现状完全一致。CLIP 零样本空间朝向不可靠（VSR 类基准近随机），
   只做微调探针，不做零样本。
5. **同一端点接管其余 vision 消费者**（均在烧卡窗口外）：报纸日期三合一校验
   （`newspaper_date_check.py`，三合一语义不变）与 Krea2 封面复检
   （`cover_recheck.py`，结论落盘供确认门⑤人审参考）。SKILL.md「vision 是唯一
   云端例外」条目退役，读报链彻底无云端依赖（除口播稿来源网页）。
6. **数据归因**：旋转数据集每条结论带 `vision_backend/vision_model` 字段（#103），
   存量行缺字段按 glm-4.6v 解读；盲比与 CLIP 重训靠它分源、可复现。

## 请求与部署口径

- 服务参数：`--ctx-size 16384 --parallel 2 --threads 16 --threads-batch 16
  --image-min-tokens 1024`（b11603 对 Qwen-VL 的 grounding 精度下限警告，
  issue #16842）。推理模式 = **CPU**（llama.cpp win-cpu-x64 构建，零 CUDA 依赖，
  7950X 物理核 16 线程，SMT 超订反伤吞吐）。
- 首帧延迟含 mmproj/图编码预热，明显低于稳态；稳态分位以盲比报告 p50/p95 为准。
  供给（二进制+模型）由 `vision-server-setup.sh` 幂等下载：断点续传 + 停滞自愈
  （`--speed-time`）+ HF LFS sha256 校验（缺省 hf-mirror——HF 直连实测大文件停滞死锁）。
- 排障：`vision_smoke.py` 一键三项（探活/单帧判读/延迟），退出码区分服务问题/
  模型问题/判读逻辑问题；`vision_server.py probe|ensure|stop|url` 为生命周期 CLI。

## 后果

- 云端抖动不再造成判读空窗；数据集结论可归因可复现。
- 新增维护面：vision-judge 供给目录（E:\AI\LLM\vision-judge，bin+models ~14GB）、
  换档须重跑盲比（一条命令）；CPU 判读吞吐低于云端（首帧预热 15-20s、稳态见盲比
  报告），每天 20-50 帧的判读预算内可接受。
- 已知风险（继承 0006 时代定位）：glm-4.6v 对本风格（人物极小+云海）旋转判定本就
  定位「辅助信号」——本地化解决的是**可用性与可复现性，不承诺提升判读准确率**；
  准确率提升由 CLIP 兜底层的数据飞轮（T7 起手动重训）承担。
- CLIP 层首轮训练前缺席，reconcile 自动退化 VLM 单路——不阻塞本 ADR 生效。

## 修订（2026-10-14 晚，用户拍板）：「CPU 常驻」例外取消，改判读窗口化

- 决策④（CPU 常驻）与全本地模型「用完即关」的总纪律冲突：Strata 窗口四锚点、
  ComfyUI 收尾清卡（09-28 约定）均已窗口化，唯判读服务从不关闭、~4GB RAM 常驻。
- 改为：**判读窗口**——s08 入口 `_ensure_vision` 自愈阶梯拉起（原语义不变），
  s08 收尾 / gate④ 镜头级重做收尾 / 连败退场三处关窗（`vision_server.stop()`）；
  gate④ 再判经自愈阶梯自动重拉，不依赖常驻。
- `stop()` 精确化：按命令行 `--port 8100` 匹配杀进程（CIM），不按映像名全杀
  （Strata 引擎同为 llama-server，全杀误伤 8080）。
- 代价：每期一次 ~40s 冷载入（窗口冷启动不占重试预算，同 Strata 口径；首帧单判
  后判读面本身数秒）。收益：纪律统一、RAM 回收。
- 落地：PR #122（feat/vision-window-close，真机 launch→online→stop→offline
  全链验证）。
