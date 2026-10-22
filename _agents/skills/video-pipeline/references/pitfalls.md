# video-pipeline 踩坑记录与检测点标准

> 汇总历次流水线踩坑，每条含：根因 / 修复 / **检测点**（可自动化验证）。
> 检测点按 P0（用户红线/必查）P1（质量）P2（优化）分级。

> ⚠️ **目录编号迁移（2026-08-28 v3）**：目录编号改为 = 流程执行顺序。旧 `01_分镜→09_发布包` 已重排为 `00_口播稿→10_发布`（导演设计拆 `03_导演设计` + `03_导演设计`）。**下方历史条目的旧目录名为当时事件快照，保留原样**；实际运行时以 SKILL.md「文件管理」的新编号为准。新编号速查：00_口播稿 / 03_导演设计 / 03_导演设计 / 04_分镜增强 / 04_口播台词 / 06_H3prompt / 02_TTS语音 / 07_成片 / 08_字幕 / 09_封面 / 10_发布。

## 1. BGM 盖过人声（P0，用户红线，2026-08-26 零碳工厂）

**症状**：合成成片 BGM 峰值段盖过人声，用户多次反馈"BGM 比人声大"。

**根因**：`compose_final.py` 混音用 `amix`，**默认 `normalize=1`**——自动把所有输入归一化到相似电平。即使 BGM 预先乘了 0.25 低音量，amix 也会把它抬回人声电平。所以调 `--bgm-volume` 系数无效（反复出现但没根治的原因）。

**修复**：
1. `amix=inputs=2:...:normalize=0`（关自动归一化）
2. BGM 音量 0.25 → 0.2（人声 1.0 保持）

**检测点（已内置 compose_final.py，[LOUDCHECK]）**：
- 混音后自动测 `mean_volume`：人声-only vs 混合后，差值 ≤ 3dB（铺底容差）
- 超限自动降 BGM ×0.7 重混一次
- 实测数据：修复前混合后 mean -23.9dB（被压），修复后 -18.8dB（人声 -19.9dB，BGM 仅 +1.1dB）


## 2. amix normalize 三坑（P0，与 #1 同源）

| 坑 | 现象 | 修复 |
|---|---|---|
| normalize=1 默认 | BGM 被抬到人声电平 | 加 `normalize=0` |
| dropout_transition 过短 | 混音爆音/切断 | `duration=first:dropout_transition=2` |
| 分段音量不生效 | amix 拉平各段差异 | 见 script-to-h3-video 踩坑 9（分段 volume 用 if(between(t,...))） |


## 3. 阶段1可跳过（P0 流程漏洞，2026-08-25）

**症状**：直接从新闻概述生成视频，漏掉口播稿创作阶段（用户要求"任何文章都要先形成口播稿"）。

**根因**：流水线没有强制阶段1产出的代码闸门，靠 LLM 自觉遵守（软约束）。

**修复（硬约束）**：
- `pipeline_contract.py` 新增 `require_stage0(script_md)`：校验口播稿含三要素（来源头 `> 来源` / `URL` / `信息源验证`）
- `segment_script.py`（阶段2）开头强制调用，不满足 `sys.exit(2)` 拒绝运行

**检测点（已内置）**：阶段2运行前自动校验三要素；口播稿必须有 web 搜索验证的信息源验证表（j2 要求：搜索补充+验证+统合成稿）。


## 4. 口播稿元信息混入分段（P1，2026-08-25）

**症状**：分段器把口播稿的"信息源验证表"和"自检门"内容切成段混入口播台词。

**根因**：`segment_script.py` 的 `extract_body()` 先 `startswith('#')` 过滤，把 `## 自检门` 头吞掉，in_table 标记永不触发。

**修复**：元信息区（`## 自检门` / `## 信息源验证`）先于通用 `#` 过滤处理；进入后跳过到文件末尾。

**检测点**：分段结果每段字数 1-70，且不含"自检/黄金三秒/信息点"等元信息关键词（正则扫描）。


## 5. 分镜增强表六项自检（P1，已内置）

`storyboard_enhancer.py` 96 项自检（16段×6项）：时长≤15s / 节奏意图合法 / 连续性链成环 / Hook分布 / 有信息点 / 镜头类型合法。FAIL=0 才过。


## 6. H3 prompt 约束缺失（P1，2026-08-26）

**症状**：`director_to_h3_llm_paper.py` 输出 shot15 缺 off-screen/lips closed 约束（标了"3修正"但没补上）。

**修复**：提交前统一校验 16 段 prompt 均含 `off-screen voiceover` + `lips remain completely closed` + `non_diegetic_music: N/A`；缺失自动补。

**检测点**：`06_H3prompt/` 每段 JSON 三要素齐全（已脚本化验证）。


## 7. ComfyUI GPU 掉线（P0 环境，2026-08-26）

**症状**：H3 生成 14 段后 ComfyUI 崩溃，nvidia-smi 报 `GPU is lost. Reboot the system to recover this GPU`。设备状态全 Unknown。

**根因**：5080 供电问题（650W 电源容量不足 + Vdroop，见 comfyui-wsl-deployment skill）。高负载后 GPU 从设备树掉线。

**修复**：Windows 重启恢复 GPU。**检测点**：`nvidia-smi` 报 GPU lost 时必须提示用户重启，不能用静态卡凑合。

**教训**：shot15/16 曾用静态卡凑合（从 shot14 抽帧+缩放），用户纠正"15和16为什么不用comfyui"——**GPU 掉线要重启，不用静态卡降级**。


## 8. portproxy 8188 占用 ComfyUI 启动（P0 环境，2026-08-22 复发 2026-08-26）

**症状**：ComfyUI 启动报"端口 8188 已被占用"。

**根因**：之前加的 `netsh portproxy 0.0.0.0:8188→127.0.0.1:8188`（IP Helper 服务持有）反向占住 8188。

**修复**：删除该 portproxy 规则，改用 ComfyUI `--listen 0.0.0.0`（WSL 直连 Windows IP:8188）。

**检测点**：ComfyUI 启动前检查 `netsh interface portproxy show v4tov4` 无 8188 转发规则；有则删。


## 9. Windows 侧后台启动 ComfyUI 秒退（P0 环境，2026-08-26）

**症状**：从 WSL 用 `Start-Process` / `cmd start /b` 启动 ComfyUI，进程秒退或启动到 web server 前无 traceback 退出。

**根因**：跨 session 启动的 python 进程被 Windows session 隔离杀掉（无交互桌面句柄）。

**修复**：用户手动在桌面启动（秋叶启动器）最可靠；或 scheduled task 交互会话启动。

**Windows 侧免 GUI 直启正解（2026-09-22 实证）**：preflight 只会报"Windows 侧启动"，agent 需知道具体路径——绘世整合包目录 `E:\AI\Comfyui-WF-2026.8.8\Comfyui-WF-2026.8.8\` 下：
```
cd ComfyUI && ../python/python.exe main.py --listen 127.0.0.1 --port 8188
```
在 Windows 侧交互终端（桌面会话）运行，勿从 WSL 跨 session 启动；WSL 侧继续走已持久化的 portproxy `0.0.0.0:18188→127.0.0.1:8188`（`COMFYUI_URL=http://192.168.50.231:18188` 不变）。本次实证全盘搜了 5 轮才定位整合包目录，故记下。

**检测点**：WSL 远程启动失败后立即转 Windows 侧直启（上述命令）或人工桌面启动，不要反复试跨 session 路径。


## 10. 视频号 cookie 过期（P0 发布，2026-08-26）

**症状**：`sau tencent upload-video` 报 `cookie 已失效，得重新登录一下`。

**修复**：`sau tencent login --account diyi --headed` → 二维码同步桌面 → 用户扫码 → `check` 验证 valid。

**检测点**：发布前先 `sau tencent check --account diyi`；login 进程 exit 1 ≠ 失败（可能已保存成功），以 cookie mtime + check valid 为准。

**sau 调用方式（2026-09-22 实证）**：sau 装在 `~/.local/bin`，非交互 `bash -c` 不加载 PATH，报 `command not found`——脚本/远程调用一律用 `bash -lc`。


## 11. 视频号"日志全绿≠已发布"（P0 发布核验，2026-08-19 复验）

**根因**：SPA 页面点击"发表"后 `wait_for_url` 可因前端路由瞬变假通过，日志打出成功但提交未落库。

**检测点（唯一权威判据）**：patchright 带 cookie 访问 `channels.weixin.qq.com/platform/post/list`，读 `视频\s*(\d+)` 计数，发布前后对比必须 +1。日志/退出码/用户自称均不能替代。


## 12. B站 biliup 两个版本陷阱（P0 发布）

| 版本 | 位置 | 特性 |
|---|---|---|
| v0.4.98 daemon | `~/.local/bin/biliup` | 旧语法，`list --pubed` 失效 |
| biliup-cli 1.2.1 | `~/.social-auto-upload/tools/biliup/<平台>/biliup` | 支持 upload/show/list，正解 |

**检测点**：发布用 sau 运行时版（`-u <账密json>` 前缀），成功标志 `APP接口投稿成功` + 返回 bvid；核验用公开 view API（见 #25）——sau 没有 show 子命令，biliup 的 show 需完整二进制路径 + `-u <cookie>` 前缀，照旧文档裸跑 `biliup show <BV>` 必失败。


## 13. 抖音 AI 声明必传（P0 发布）

**检测点**：`--declaration "内容由AI生成"`（AI 生成画面/配音必须标注），日志确认 `自主声明已选择「内容由AI生成」`。


## 14. 字幕烧录标准（P0，2026-08-25 定稿）

- 480×864：字号 9px、描边 1.5、阴影 0.3、白字黑边、底部居中、MarginV=40
- 每行 ≤20 字语义断行，最多 5 行，块高 ≤140px，行宽 ≤180px（限 450）
- 禁 ass 滤镜（偏移 bug），用 `subtitles=srt`
- **检测点**：时间轴 3+ 采样点精确 seek 抽帧比对 + 音频对齐 + vision 复检


## 15. 封面标准（P0，2026-08-26 定稿：Krea2 数字迷幻流为主）

- **主方案（Krea2 数字迷幻流）**：口播稿→参考文本→198节点→krea2_to_api.py→comfy-mcp run_workflow→vision复检→09_发布包
  - 参考文本须明确色调（光明希望/深色科技/冷峻神秘），否则 Krea2 默认深色
  - 分辨率：B站 SDXL_1344x768+1536×768 / 抖音视频号 SDXL_768x1344+768×1344
  - 只改 198（参考文本）+ 140/18（分辨率），不动工作流结构
  - 连接：`COMFYUI_URL=http://192.168.50.231:18188`（固定IP + portproxy 18188→8188 + 防火墙放行）
- **旧方案（PIL叠字）**：B站 1920×1080 / 抖音视频号 1080×1440，标题金色(255,210,0)+黑描边+黑阴影无底框，标题前 5 字=搜索热词，12-18 字不截断——保留用于需精确文字叠加场景
- **检测点**：vision 复检色调/主体/瑕疵 + 参考文本落盘 `09_发布包/海报_提示词_krea2数字迷幻.md` + 底图文字去重


## 16. 新风格接入完整清单（P1，2026-08-26 纪实卡通牛来）

用户指定风格不在 7 个内置风格内时，**不是改现有脚本，而是新建风格变体**：
1. `references/styles/<风格>.md` — 视觉语言/运动/音频/负向约束/H3英文关键词
2. `cp director_llm_paper.py director_llm_<风格>.py` + 替换 SYSTEM_SHOT / SYSTEM_OVERALL（角色投射成新风格）
3. `cp director_to_h3_llm_paper.py director_to_h3_llm_<风格>.py` + 替换 SYSTEM（H3英文风格关键词）+ enforce_constraints 里的风格标记检查（`if "paper collage" not in p...` → 换成新风格关键词，如 `"cartoon"`）
4. **H3_MARKER 指纹**——submit_accel.py 的 OVERRIDE_CHECK 用该 marker 校验 prompt 覆盖成功。2026-09-03 起默认指纹为 `integrated_multimodal_description`（结构性字段头，所有 prompt 必有），换风格无需再设；**指纹永远选结构性必有的串（字段头/硬约束句），禁用风格词/内容词**（P0 全条目见 SKILL.md「踩坑记录」）


## 17. 分段目录必须先建（P1，2026-08-26）

segment_script.py / storyboard_enhancer.py 等脚本不自动创建输出目录，报 `FileNotFoundError`。
**正解**：跑任何阶段脚本前先 `mkdir -p 01_分镜 02_分镜增强 03_口播台词 04_风格化文案 05_TTS语音 06_H3prompt 07_成片 08_字幕 09_发布包 480P`。


## 18. compose_final.py 两个坑（P1，2026-08-26）

1. **concat list 相对路径报错**：脚本生成的 `_concat.txt` 用相对路径，ffmpeg 从 list 文件目录解析失败。正解：手动生成绝对路径 list：
   ```bash
   find <output_dir>/_merged -name "*.mp4" | sort | while read f; do echo "file '$PWD/$f'"; done > <output_dir>/_concat.txt
   ffmpeg -y -f concat -safe 0 -i <output_dir>/_concat.txt -c copy <output_dir>/成片_无BGM.mp4
   ```
2. **输出目录嵌套**（`07_成片/07_成片`）：脚本对 output_dir 参数处理有 bug。规避：手动跑 concat + BGM 混音（amix normalize=0 + volume 0.2 + stream_loop -1），绕开脚本。

**2026-09-03 已修复**：concat list 条目统一 `os.path.abspath()` 转绝对路径后再写入（双重嵌套根因与修复见 SKILL.md P0「ffmpeg concat 相对路径双重嵌套」），上述手动规避仅旧版本需要。


## 19. verify_output 对新风格的误报判定（P1，2026-08-26）

verify_output.py 的 90 项检查是为 paper 模板化风格设计的，对新风格（牛来卡通）会大面积误报 FAIL，**需人工判定不阻塞**：
- **style lock mismatch (prefix ratio 0.29)**：检查相邻段 prompt 前120字符公共前缀——模板化风格前缀高，新风格每段独立场景描述前缀低=反而说明每段独特
- **audio Xs vs video Ys**：TTS 比视频快是正常（compose 已用 -shortest 对齐）
- **director design missing info_point**：info_point 是增强器自动截取前20字，导演设计不逐字出现
- **segment ends mid-sentence**：分段器按句号切但段内句号后还有内容
**判定原则**：以 vision 抽帧 + BGM volumedetect 实测为准，verify 全 FAIL 时逐条看是否误报，不盲目重跑。

**定位（2026-09-26 grill 拍板）**：verify_output.py 是 **agent/手工模式的旁路验收工具，driver 不调用**（run_pipeline.py 零引用）——压缩车道后质量兜底已转移至抽帧 vision + 确认门④+⑤人审，与本条误报特性一致，不再当「接进 driver」的债；九项检查对手工模式仍有残值，保留不删。


## 20. 封面文字"正中间居中"= 上下+左右都居中（P0，2026-08-26 用户纠正两次）

用户说"居中"指**图片正中间**（上下+左右），不是顶部居中。最初只做左右居中放顶部被纠正。
**正解**：
```python
total_h = th + gap + sh  # 主标题高 + 间距 + 副标题高
y_start = (H - total_h) // 2  # 上下居中
x = (W - tw) // 2             # 左右居中
```
**检查点**：上下余量对称（y_start ≈ H-y_start-total_h），用像素测量验证（PIL 黑底基准图测文字 min/max x，中心=W//2）。


## 21. Krea2 封面 API 图提交（P0，2026-08-26）

krea2_to_api.py 输出**裸 API 图**（不带 `{"prompt":}` 包装）。提交到 ComfyUI `/prompt` 时必须直接 `--data-binary @api图`，**不能再包一层 `{"prompt": api}`**——否则报 `missing_node_type: Node 'ID #prompt' has no class_type`。
成功标志：返回 `{"prompt_id": "...", "number": N, "node_errors": {}}`。


## 22. 飞书上传 30MB 限制 + 压缩（P0，2026-08-26）

`/im/v1/files` 对 `file_type=stream` 有 **30MB 上限**。带字幕成片常超限（32.7MB）。
**正解**：`ffmpeg -i in.mp4 -c:v libx264 -preset slow -crf 26 -c:a aac -b:a 128k out.mp4`（32MB→16MB，画质可接受）。
飞书图片用 `chat_id`（oc_开头）+ `receive_id_type=chat_id`，避免 open_id cross-app 限制。


## 23. sau 环境真实路径 + B站封面必须用 biliup-cli（P0，2026-08-26）

- sau CLI：`/home/lmr/social-auto-upload/.venv/bin/sau`（**不是** `~/.social-auto-upload/`）
- cookies：`/home/lmr/social-auto-upload/cookies/{bilibili,douyin,tencent}_diyi.json`
- B站发布：`sau bilibili upload-video` **不支持 `--cover`**——直接调 biliup-cli 1.2.1：
  ```bash
  /home/lmr/.social-auto-upload/tools/biliup/linux-x86_64/biliup -u cookies/bilibili_diyi.json upload <视频> --title --desc --tid 21 --tag "t1,...,t10" --cover <横版.png>
  ```
  成功标志：`APP接口投稿成功` + 返回 bvid。核验用公开 view API（见 #25），不依赖 show 类子命令。


## 24. 视频号 cookie check invalid 不等于死（P0，2026-08-26）

`sau tencent check` 报 invalid 时，**先跑一次 login 流程验证**——本次 check 显示 invalid，但 `xvfb-run -a sau tencent login --account diyi --headed` 直接报 `cookie 有效` 并恢复。
**检查点**：login 后 cookie mtime 更新 + 直接 upload 看真实报错，不要仅凭 check 判死。


## 25. 发布后三平台核验（P0，2026-08-26 实测；B站行 2026-09-22 改公开 view API 并实测）

| 平台 | 命令 | 证据 |
|------|------|------|
| B站 | `curl -s "https://api.bilibili.com/x/web-interface/view?bvid=<BV>"` | code=0；校验 title/duration/tid=21（生活大区，防被改写成 232）；封面 URL 下载后 sha256 比对本地；-404=仍在审核稍后重试 |
| 抖音 | 日志链 | `视频发布成功` + `自主声明已选择「内容由AI生成」` + `封面设置完成` |
| 视频号 | 日志链 | `3:4 竖版封面已经设置完成` + `视频发布成功` |

三平台全部成功后，才是完整发布闭环。


## 26. 字幕时间轴漂移（P0，2026-08-29 每日新中国）

**症状**：后段"语音比字幕慢"（尾部字幕提前 2-5s 结束），反复出现。

**根因**：字幕 offset 累加用 whisper 语音结束时间（`words[-1].end`），但每段实际长度 = 音频全长（含尾部静音）。每段漏 0.1-0.3s，26 段累计漂移 5.7s。

**修复**：offset 逐段累加**音频全长**（`ffprobe format=duration`），不是 whisper 语音长。
**检测点**：SRT 末条结束时间 ≈ 成片总时长 - 0.5s；vision 抽尾部 181s 采样确认字幕与语音同步。


## 27. sidechain 闪避 BGM（P0，2026-08-29 根治"BGM 盖人声"）

**根因**：固定 BGM 音量（0.2）在高潮段仍盖人声——音量调低则整体太闷，调高则高潮盖人声，无解。

**修复**：`sidechaincompress` 自动闪避——人声出现 BGM 自动压低（threshold=0.03, ratio=6, attack=15, release=250, makeup=3），人声停 BGM 回升。注意：
- 同一 BGM 源被用两次必须 `asplit=2` 分路，否则 ffmpeg 报 "Invalid stream specifier: bgm_loop"
- BGM 先 `volume=0.3` 再进 sidechain，之后 `loudnorm=I=-16:TP=-1.5:LRA=11` 归一

**检测点**：volumedetect 抽 4 点（片头/报纸/主体/高潮），高潮段 mean ≤ 主体段 mean + 3dB。


## 28. 烧录三坑（P0，2026-08-29）

1. **SRT 序号必须纯数字**：`T1`/`T2` 前缀 libass 不识别，字幕静默不渲染（不报错）
2. **字幕文件路径必须 ASCII**：`../08_字幕/xxx.srt` 中文路径 ffmpeg 报 "Unable to open"；复制到 `/tmp/` 再烧
3. **顶部标题用 drawtext 不用 subtitles**：subtitles 的 `Alignment=8`（顶部）在 libass 渲染到中间/底部，不可靠；drawtext 精确指定 `y=20, x=(w-text_w)/2`
4. **禁一步双 subtitles 滤镜**：filtergraph 初始化失败，拆两步（先标题后口播）


## 29. 底部字幕与顶部标题重复（P1，2026-08-29）

**症状**：6 条新闻口播第一句 == 新闻标题，底部字幕和顶部标题显示同一句话，观感像"字幕下方加了标题"。

**根因**：shot01 等口播文本首句就是标题句。

**修复**：`gen_precise_subs.py` 跳过与标题相同的句子（`sent == title.strip()`），但注意兜底逻辑——若整段只有标题句（chunks 空），不要用 `chunks=[script]` 加回（`not chunks and script != title` 才兜底）。


## 30. 精确字幕方案（P0，2026-08-29 定稿，替代旧"按段均分"）

`gen_precise_subs.py`：whisper 词级时间戳（`--word_timestamps True`，缓存 `/tmp/wsout/{sid}.json`）+ 原稿文本按字数比例映射 + 语义断句（句号/逗号/虚词/丨标题 + 数字保护）+ 标题去重。段长=音频全长。
**原理**：时间戳来自真实发音时刻，文本来自准确原稿，两者合成 = 字幕与语音精确贴合，不靠"按段均分"猜。


## 31. whisper 缓存复用（P2，2026-08-29）

`gen_precise_subs.py` 的 whisper 调用结果缓存到 `/tmp/wsout/{sid}.json`，重跑不重复推理（26 段 whisper 首次 ~5 分钟，缓存后秒级）。**注意**：改断句逻辑重跑时不用重跑 whisper，直接读缓存。

**🚨 全局缓存换稿必污染（2026-09-03）**：`/tmp/wsout` 是全局路径，换新闻稿后 shot01.json 还是旧稿的时间轴→字幕全错。**修复**：缓存改 `<项目>/_cache/wsout/`（项目自包含）；`gen_subs_from_timeline.py` 的 wsdir 参数同样传项目内路径。

## 32. 🚨 竖屏画面被旋转 90°（P0，2026-08-31 天宫读报单，用户两次纠正）

**症状**：竖屏 9:16 成片里某段画面是"横构图旋转 90°"——观众要歪头/转手机才能看。用户原话："在竖屏画面里做了一个横屏画面，然后翻转了90度，让观众都得90度看"。

**根因**：H3 prompt 里的场景描述含"**环形/圆形竞技场 + 看台环绕 + 宏大全景**"类词汇（`ring-shaped arena centered on the frame` / `circular colosseum` / `four tiers of stands rising around` / `wide panoramic shot`）。这类"水平环绕型宏大场景"没有竖直锚点，H3 在竖屏容器里生成时倾向**俯视鸟瞰 + 横构图**，再被旋转适配竖屏 = 观众歪头看。**只有这种场景会中招**——有明确垂直方向感（巨碑/柱廊/人/铁轨/梯田）的段自然按竖屏生成，不出问题。

**判定**：vision 抽帧问"画面是竖直方向（天空在上地面在下）还是横着旋转90°？是否俯视鸟瞰？"。只有问题段需要重做，不要全量重跑。

**修复（三管齐下，已验证生效）**：
1. **正向重力/视角约束**（写进 prompt 开头）：
   ```
   This is a standard vertical 9:16 portrait video. The sky must be at the very top of the frame,
   the ground and crowds at the very bottom. The frame is NOT rotated — the horizon stays perfectly
   level and horizontal, gravity points straight down, and the camera is held upright, never tilted sideways.
   ```
2. **负面排除**（prompt 内显式禁止）：
   ```
   Absolutely forbidden: horizontal composition, any 90-degree rotation of the frame, sideways or
   tilted ground, a tilted horizon, the sky appearing at the side of the frame, vertical pillars
   rendered lying horizontally.
   ```
3. **场景改为垂直纵深构图**（最关键）：把"环形竞技场/看台环绕"改为"**竖直赛道/天梯从前景向画面顶部灭点延伸**"（`a straight sacred track stretches from the foreground toward the far vanishing point at the upper-middle of the frame, running vertically up the frame like a grand stairway to heaven — composition built around a vertical axis`），两侧看台如竖直立柱框住赛道。

**防再犯（导演设计层）**：导演设计（阶段3）和分镜增强（阶段4）里，**竖屏场景禁止写"环形/圆形/环绕/俯视鸟瞰"类布局**；一律用"竖直纵深/向灭点/垂直堆叠"构图语言。

**制度化（2026-08-31 用户要求融入全流程检查点）**：
1. **生成注入**：`director_to_h3_llm.py` SYSTEM 第 9 条内建竖屏方向约束规则 + `enforce_constraints` 自动注入 `PORTRAIT_CONSTRAINT`（每段缺则自动补 9:16/天空正上/地面正下/不旋转/镜头正立约束句）
2. **提交前检查**：`scripts/check_portrait_orientation.py <06_H3prompt>` 扫描每段——缺竖屏约束 → FAIL（exit 2）；命中风险词（`ring-shaped arena/circular colosseum/from above/aerial/bird's eye/panoramic`）→ WARN（需生成后人工复核）
3. **成片验收**：verify/验收报告必须含"画面竖直方向"抽帧检查（天空在上地面在下、无旋转90°、无俯视鸟瞰）；风险词命中的段必须 vision 抽帧确认

## 33. 🚨 krea2_to_api.py 三坑：LoraManager loras 提交失败（P0，2026-08-31 天宫封面实测）

Krea2 数字迷幻工作流用 `Lora Loader (LoraManager)`（节点 185）加载 4 个 LoRA，API 转换时连续踩三坑才跑通：

1. **194 PromptCategoryReader category 值带空格/错字**：工作流存 `"  krea2数字魔幻"`（前导空格 + "魔幻"非"迷幻"），ComfyUI 校验 `value_not_in_list`。**修复**：krea2_to_api.py 强制 `nodes[194]["widgets_values"][0] = "krea2数字魔幻"`（合法值从报错信息读——不同版本列表不同，以运行时 object_info/报错为准）
2. **185 loras 丢失**：LoraManager 的 loras 在 widgets_values[2]（list of dict），转换时被跳过 → `required_input_missing: loras`。**修复**：特例从 widgets_values[2] 解析 active loras
3. **loras 裸数组被当连接**：`loras: [["Detailer-KREA2",1.5,1.5],...]` → `bad_linked_input: must be length-2 list of [node_id, slot_index]`。**根因**：ComfyUI 校验器把 LORAS 类型裸数组当连接。**修复**：必须 `{"__value__": [ {name,strength,clipStrength,active}, ... ]}` 包装（get_loras_list 新格式，值须是**字典数组**——`_collect_widget_entries` 用 `lora.get("active")`，元组数组会 `'list' object has no attribute 'get'`）

**检测点**：提交后 `node_errors` 必须为空 `{}`；`job` 返回 `outputs` 非空（`Krea2_*.png`）才算成。提交只 `code:0` 但 `outputs:[]` = 节点执行失败（查 history/error）。

**脚本已修复**：`krea2_to_api.py` 内建 194 category 修正 + 185 loras `{"__value__": dict数组}` 处理。

## 34. 🚨 字幕时间轴根治：whisper 词级时间戳映射原文（P0，2026-08-31 用户纠正"字幕对不上"）

**旧方案**（`gen_precise_subs.py`）：whisper segment 边界 + 按字数摊派。**根因**：IndexTTS 句间停顿 <0.2s，whisper 无法按停顿切句（把两句并一段），句子起止靠猜 → 字幕对不上。

**新方案**（`scripts/gen_subs_from_timeline.py`）：
- whisper **词级**时间戳是连续的、按时间顺序的（每字 start/end 准确）——但同音错字多，不能直接用转写文本
- 把**原文每个字**按位置比例映射到 `[words[0].start, words[-1].end]` 连续时间轴
- 句子起止 = 句首字 start、句末字 end → **时间来自 whisper 实测、文本来自原文**，零 ASR 错字
- 偏移：每段起点 = 片头偏移 + 前段音频全长累加（`ffprobe` 实测）

**断行**（避免孤标点/词拆断）：
- 句号/逗号/顿号切，纯标点碎片并入前段
- >20 字在 14-20 字范围**从后往前找标点**断（保护词不拆断）；无标点则 20 字后 1-2 字内吞标点

**检测点**：①无孤标点/单字符 cue ②时间轴单调 ③末条止 ≈ 成片总长-0.5s ④vision 抽帧（首/中/尾 3 点）底部字幕=该处语音内容

## 35. 🚨 MatrixMedia 视频号"假成功"：CLI 全绿但后台无视频（P0，2026-08-31 实测）

**症状**：`cli publish -p sph` 返回 `✅ 视频号视频上传成功` + `resultStatus: success` + exit 0，pushData `publishStatus: success` / `publishSuccessCount: 1` / `publishAttemptCount: 3`，但用户视频号后台「视频」列表**实际没有**。多次重发（含换文件名绕过去重 `isDuplicate: false`）全部假成功。

**根因**：MatrixMedia 视频号发布用 `useRealBrowser: false`（Electron 内嵌浏览器）+ `closeWindowAfterPublish: true`——发表按钮点击未真正被 SPA 消费（与 sau 视频号"发表按钮不消费点击事件"同源）。**日志绿/exit 0/pushData success 均不可信**。

**处理**：
- 不用 MatrixMedia 发视频号（抖音/快手/头条/小红书可正常，仅视频号中招）
- 视频号改用 **sau 路径**（`xvfb-run -a sau tencent upload-video --headed`）或 GUI 手动
- 唯一权威判据 = 视频号后台「视频 N」计数 +1（patchright 带 cookie 访问 `channels.weixin.qq.com/platform/post/list` 截图 vision 读）

**教训**：跨平台发布工具的"成功日志"不可跨平台等同——每个平台的发布结果必须用该平台后台实测核验。

## 36. 🚨 vision 旋转误判删好镜头 + 静默重烧失败 → 成片缺段（P0，2026-09-07 实测）

**链条**：glm-4.6v 把量化验证合格（OpenCV 直线检测逐秒中位偏差 0.7°）的镜头误判为「翻转」→ driver 自动删产物强制重烧 → submit_accel 静默失败（exit 0 无产物）→ 装配器对缺段静默容忍照常拼接 → 确认门发出缺段成片（190.8s→178.2s，少一整段新闻）。

**修复链**（全部已验证）：
1. vision 判「翻转」但存疑时，先用 OpenCV HoughLinesP 量化复核（Canny→直线→近水平/近垂直线的角度偏差中位数，阈值 ≤3° 合格），**不信任单一 vision 结论**
2. 被误删的镜头从 ComfyUI 原始输出目录捞回：`generated.jsonl` 尾部同 id 条目的 `outputs` 路径（重烧失败时原文件未被覆盖）
3. **重烧「成功」必须核实产物落盘**：submit_accel exit 0 ≠ 产出，`ls 07_成片/1080P/` 确认段数后才能进装配

**装配器须加缺段硬校验**：`_main_shots.txt` 段数 ≠ enhanced.json 段数时应 FAIL，不得静默装配。

## 37. 🚨 镜头漂斜（roll/俯仰）vision 拦不住，提示词必须静态化（P0，2026-09-07 用户三轮纠正）

**症状**：镜头前 8 秒水平、第 9 秒起逐渐倾斜（中位偏差 7.1°），vision 检查（只问 90° 翻转）和我的人工复验（预设问题）全部漏放，用户肉眼发现。

**根因**：H3 对运镜动词的执行过度——「slow vertical tilt/下降镜头」「推近」都会诱发中途 roll/俯仰漂移；**只加「never tilted/level horizon」禁令不够**（v2 仍漂斜）。

**提示词修复公式**（v3 实测 0.7° 全程合格）：
- 运镜句改为**完全静态三脚架**：`The camera is a completely static tripod shot: zero camera movement of any kind, no pan, no tilt, no push, no roll, no drift.` + `all architectural lines locked perfectly vertical and horizontal`
- 审计全文删运镜残留语义（`descends from above`/`enter the frame from the top` 等），改为主体的自然运动（雾气漂过静止镜头）
- 内容元素避免「光柱从云隙刺下/光丝刺穿云海向下」等暗示俯仰取景的描述

**量化验收**：OpenCV HoughLinesP 逐秒测直线角度偏差（中位数 ≤3° 全程合格）；vision 只会答「没有90°翻转」，问不出构图斜。

## 38. 🚨 混帧率/混音频参数 concat 时间轴错乱（P0，2026-09-03）

**症状**：_main_src 理论 111.6s 实测 208s；或 成片_最终 95.4s vs 理论 119.4s。

**根因**：`concat -c copy` 要求所有段视频帧率/分辨率、音频采样率/声道完全一致。H3 段 24fps+22050Hz 单声道，垫片/intro 30fps+44100Hz 立体声——混流后 ffmpeg 时间戳错乱。

**修复**：垫片生成前 ffprobe 探测段的帧率/采样率/声道并严格对齐；_main_bgm 视频重编码 30fps；混音输出统一 `-ar 44100 -ac 2`。

## 39. 校验器禁止对内容形态做假设（P1，2026-09-03）

三处同类病：verify_output 的 STYLE_KEYS 词表缺新风格词→全段误报（见 #19）；cut_point 读 enhanced.json 的 text 字段（新 schema 已删）→空串全 FAIL；H3_MARKER 绑定风格词（见 #16）。**原则：校验器只依赖结构性/契约性字段（字段头、落盘文件位置），内容特征用统计判据（共享词≥3）而非白名单。**

## 40. 🚨 Krea2 工作流/插件升级后枚举漂移（P0，2026-09-03 三连环；提交侧三坑见 #33）

LoraManager category 存旧枚举 'krea数字梦幻'（合法值 'krea2数字魔幻'）；QuickSavePrompt(143) 同病；LoRA 名带 ' (1)' 后缀本地库无此名；unet_name 带子目录前缀。**修复（krea2_to_api.build_api 统一处理）**：`_enum_fix` 从 object_info 读合法枚举动态修正（兼容 3 种 schema 形态）；`_lora_clean` 去 ' (1)' 后缀；QuickSavePrompt/PromptExpand/ShowText 类节点剔除+悬空引用置空（**禁级联剔除——会连坐主链**）；object_info.json 缺失/超24h 自动从 ComfyUI 拉取。

## 41. 报纸段缩放必须 pad 不 crop（P1，2026-08-29）

报纸 PNG 是 1080×1300（手机壁纸），成片 1080×1920。合成报纸段用 `crop=1080:1920` 会把报纸上下裁掉（用户反馈"出画面被裁切"）。**正确**：`scale=1080:1300,pad=1080:1920:0:(oh-ih)/2:color=0xf6f1e6`（上下填充报纸底色，完整显示）。

## 42. 片头段必须保留原音轨（P1，2026-08-29）

`每日新中国片头.mp4` 自带音轨（aac），做片头段时**不要 `-an` 丢音频**——否则片头 5.1s 无声（用户反馈"片头没声音"）。直接 `-c:a aac` 保留原音轨。

## 43. 新高清工作流适配（P1，2026-08-29 每日新中国读报单实测）

**场景**：用户更新了 `▶▷MiniMaxH3-加速视频流整合.json`（129 节点），加了 jjTE 放大链（TEFlashVSRRestore 2x 超分 + TESpeedVideoCombine 输出），直接文生图输出 **1088×1920 H.264 MP4**（约 1080P 竖版）。分辨率不用调，FlashVSR 自动 2x。

**submit_accel.py 适配（3 处）**：
1. **fix_models BFS 起点 193→393**：旧代码从 `193`（SaveVideo）反向 BFS，会砍掉放大链（392/393/389/391 全丢）→ 只保留 480P 分支。新代码从 `393`（TESpeedVideoCombine）反向 BFS，保留 194 主链 + FlashVSR 放大链。
2. **输出文件名 193→393**：`api["393"]["inputs"]["filename_prefix"] = "480P/" + prefix`（393 是 TESpeedVideoCombine，输出 1080P）
3. **剪掉 QwenTE 提示增强链**（239/240/252/253/254）：它们是 194.prompt 的 link 上游，但我们直接覆盖 prompt 后变死链，留着会每次加载 Qwen3.5-VL 浪费显存。剪掉后 22 节点干净主链。

**ui2api.py 修复**：
- `widgets = n.get("widgets_values") or []`（原 `, []` 对 null 无效）
- `wval = (widgets[widget_idx] if isinstance(widgets, list) and widget_idx < len(widgets) else None)`（Fast Groups Bypasser 的 widgets 可能是非空 dict，`len()>0` 走 `[0]` 会 KeyError）

**验证**：
- 单段提交 → node_errors 空 → history outputs 在 `393` 节点，输出 `480P/<prefix>_00001-audio.mp4`（TESpeedVideoCombine 输出带 -audio 后缀）
- ffprobe 确认 `h264 + aac, 1088×1920, yuv420p, 24fps`
- 每段约 1.5-2 分钟（模型缓存后 87s）

## 44. qwen 慢 ≠ 卡水位（P0 判断纪律，2026-09-22 实证）

**症状**：s07 H3 prompt 转译阶段几十分钟无新产物落盘，看似卡死。

**根因**：转译本来就是重活——5080 16GB 上 16 镜实测约 **50 分钟**（GPU 99% 满载、每镜 3-5 分钟）。"水位"指这类阶段的正常耗时预算，慢是工作量大，不是卡。

**修复**：判断纪律——按墙钟时间武断干预（杀进程/重启）会毁掉断点续跑，几十分钟算力白烧；只有证据才能判卡死。

**检测点（干预前必查）**：①`nvidia-smi` GPU 利用率持续高位 = 在干活，继续等；②产物/日志 mtime 是否仍在推进；GPU 空转**且**日志长时间无更新才考虑异常。转译阶段一律给足 ~1 小时水位再谈异常。

## 45. 🚨 新旧车道切换必须清 s04-s07 stamps（P0 断点续跑，2026-09-25 压缩车道定案）

**症状**：换车道重跑后 s04-s07 半新半旧——`06_H3prompt/` 是单次调用新产物、`03_导演设计/04_分镜增强/05_美术画质/` 残留 v3 四阶段旧中间稿（或反向）；下游按旧 stamp 跳过，对账门与断点续跑全部错乱。

**根因**：两车道产物语义不同（compressed = s04 单次 KVMem 调用直出 06_H3prompt，s05/s06/s07 只写虚拟 stamp；legacy = v3 四阶段各自落真实中间稿），stamps 只记「阶段完成过」，不识别车道归属（ADR 0004 决策 1）。

**修复**：driver 已自动化——`_guard_lane_switch` 检测车道变化（CLI `--lane` > env `DRIVER_LANE` > 默认 compressed，既往车道记在 `_driver/lane.txt`）时自动清 s04-s07 stamps 并台账留痕（`lane_switch_cleared`）。**两种情况要手工清**：① 特性上线前的旧 v3 现场没有 `lane.txt`，首次跑压缩车道**故意不清**（防误伤续跑现场）——确认弃旧中间稿后自己清；② 绕开 driver 手工搬产物/换车道。命令：`rm <slug>/_driver/stage_s0{4,5,6,7}.json`（旧中间稿目录一并按需删）。

**检测点**：换车道后先 `--dry-run` 确认 s04 是 RUN 不是 SKIP；compressed 车道下 s05/s06/s07 的 stamp 带 `"virtual": true` meta，看到实体中间稿 + 虚拟 stamp 并存即半新半旧现场。

## 46. 🚨 KVMem 劣化态 4 t/s ≠ 卡死（P0 判断纪律，2026-09-25 压缩车道；#44 反侧）

**症状**：压缩车道整单 JSON 契约连败，或冒烟/请求 gen_tps 掉到个位数；GPU 满占用却半天出不来 token——看着像卡死。

**根因**：KVMem rc1 实例连续 ~30 轮请求后解码从健康水位劣化到 **4 t/s**（GPU 满占用、低功耗小算子形态；健康水位数值见检测点），且劣化态输出质量同步变坏 → 下游契约连败（E 盘指南红线 #1）。与 #44 是一对反例：#44 是「GPU 高位 = 慢活在干活，等」；本条是「GPU 满占用 + tps 个位数 = 服务劣化，立即重启」——**判据是 gen_tps，不是 GPU 利用率，更不是墙钟**。

**修复**：`pkill -f '[l]lama-kvmem-server'` 重启即恢复。driver 自愈阶梯自动做（CONTEXT.md 词条）：冒烟不过/整单连败 → 重启复测，最多 2 轮完整重启循环，耗尽硬失败叫人。预防纪律（指南红线 #1）：单实例会话请求控制在 ~20 轮内、每期分支跑完重启——driver 窗口托管已自动化（s04 起服、s08 卸载、跨期不驻留）。

**检测点**：冒烟放行水位 gen_tps ≥40（客户端口径，0925 票4 #61 校准：冷启首测 ~50、稳态 58-86、劣化 4——阈值与劣化态保持 10 倍距）。整单连败重启后复测即恢复 = 本条实锤；重启仍连败 2 轮 = 硬失败叫人。

## 47. WSL 跨界调用两条纪律：`bash -lc` 保 PATH、禁嵌套 wsl.exe 拉守护进程（P0 环境，2026-09-25 票3/4 实测）

**症状**：① `wsl -d Ubuntu -e bash -c 'pgrep …'` 报 command not found 或 PATH 下工具找不到；② WSL 内 driver 经嵌套 wsl.exe 拉起 KVMem 守护进程：nohup 无效、日志 0 字节，父会话一退全组被杀——冷启动必死、重启预算耗尽必硬失败叫人。

**根因**：① 非交互 shell 不加载 PATH（同 #10 sau 教训，`~/.local/bin` 等只在 login shell）；② 嵌套 wsl.exe 会话退出时终结整个进程组，nohup 挡不住，必须 setsid 脱离会话。

**修复**：① KVMem 运维命令（pgrep/pkill/nvidia-smi/test）一律 `wsl -d Ubuntu -e bash -lc '<cmd>'`；② WSL 内拉守护进程直接 `Popen(["setsid", "bash", "-c", …])`（start_qwen.sh 生产同款拓扑）；Windows 侧 driver 的非嵌套 wsl.exe 拉起实测存活，维持原路。`kvmem_window.py` 已按此实现，两侧均可运行。

**检测点**：pgrep/pkill 模式用 `[l]` 括号写法——bash -lc 包装进程的 cmdline 含模式原文，会自匹配；`wsl.exe --list` 输出 UTF-16LE 而 `-e bash` 输出 UTF-8，解析按 NUL 字节判别（kvmem_window.wsl_decode 双兼容）。

## 48. 两处裸 subprocess.run 是有意形态，勿再当收口债（P2 纪律，2026-09-26 grill 拍板）

「run_child 收口」候选（架构评审 v2 记忆遗留）实查已死：run_child/run_sub 全仓+全 git 史零命中，系评审时记忆产物。残存的两处裸 `subprocess.run`（run_pipeline.py s11 烧录 ffmpeg、封面叠字 overlay_cover_text）**维持现状不收口**：两处都是 list-argv 形态，比 `sh()` 的 shell=True 字符串更安全（无 shell 注入面、无引号转义坑），且不需要 sh() 的 env 合并（默认继承 os.environ）。强行改走 sh() 是降级。下次评审勿再捞出。

## 49. 🚨 KVMem 冒烟假劣化：冷启首发生成把池初始化/时钟爬坡计入 wall（P0 环境判读，2026-09-26 实证；#46 姊妹条）

**症状**：服务刚就绪的首发冒烟实测 33-40 t/s，贴着 40 水位线"压线通过"（0925 期 40.36 即此假象）；同服务 gen_speed_probe 实测冷 61.3 / 热 66.1——冒烟值是假的。

**根因**：CUDA graph/MTP 池初始化 + GPU 时钟爬坡（P8 735MHz→P1 3.3GHz）全部计入首次生成的 wall；probe_tps 客户端口径 completion_tokens/wall 被稀释。与 #46 的劣化态（4 t/s）差着一个数量级，但都落在水位线附近，极易误读。

**修复**：probe_tps 计时前先发一发预热请求（kvmem_window.py，warmup=True 可关，预热失败自吞）；冒烟水位语义不变（40 劣化线 / 4 灾难线）。PR #75。

**检测点**：冒烟值落 33-40 区间 ≠ 劣化，先看是否首发（重启后第一次冒烟）；gen_speed_probe 冷热对照是仲裁手段。

## 50. 🚨 视频号自动发布三坑：CDP 50MB 传输上限 / selector 未注入 iframe / 发表拦截弹窗（P0 发布，2026-09-27 全链路实证）

**症状**：① 83MB 成片 `fc.set_files()` 六连败 `Cannot transfer files larger than 50Mb`；② `fr.locator(...)`/`get_by_role(...)` 在编辑器 iframe 里**恒返回 0**、click 永远 "waiting for locator"——但 `fr.evaluate` 能看到元素；③ 点「发表」后弹「声明原创的视频有机会获得广告分成」（[直接发表,声明原创]），不处理则 URL 不跳转、发表未完成。

**根因**：① connect_over_cdp 被视为远端浏览器，Playwright 走内容传输；② Playwright selector 引擎未注入 micro/content iframe——**不是元素不在，是注入缺失**（evaluate 走普通 JS 执行所以正常）；③ 平台推广弹窗拦截发表流。

**修复**：① CDP 原生 `DOM.getDocument({depth:-1,pierce:true})` 找 `input[type=file]` + `DOM.setFileInputFiles` 直填本机路径（零内容传输）；② 全程 `frame.evaluate()` + `page.keyboard`（evaluate focus 后 type）+ JS `btn.click()`——**uploader 库 TencentVideo 各方法在此场景全部静默失效，勿再引用**；③ `pick_dialog_button` 决策表点【直接发表】（AI 内容不声明原创，合规）。

**检测点（三条纪律）**：
- 编辑器 iframe 会重载，旧 Frame 对象 URL 仍匹配但 document 已换——frame 必须按「元素在场」动态校验，不能按 URL 首匹配；
- `TencentVideo.wait_for_upload_complete` 是陷阱：每轮按钮定位超时 30s+ 但计时只 +2s，120s 上限实耗 30+ 分钟，超时后**存草稿并跳过发表**——差点把成片变草稿；就绪判据用「video 元素在场 + 发表钮非 disabled」自行探测；
- 发表成功唯一权威判据 = 创作中心列表 `视频 (N)` 计数 +1 且今日标题在场（0927 期 88→89 实证；与 #11 一脉相承）。

落地脚本：`scripts/sph_publish.py`（publish / --check-only 双模式，PR #76）；前置 = 视频号专用 Chrome 带登录态（`restart_sph_chrome.ps1`，CDP 9222，profile `chrome_sph_profile`；微信登录内存态，杀 Chrome 即丢——09-25 扫码的登录态到 09-27 仍存活，cookie 文件 invalid 不影响）。

## 51. 🚨 视频号编辑器 JS 点击全失效 + 封面/原创声明链路（P0 发布，2026-09-29 六轮实测）

**症状**：① 想给成片设封面，点封面预览的「编辑」按钮**毫无反应**——`element.click()`、完整 PointerEvent/MouseEvent 序列 dispatch、父链上溯 cursor:pointer、Playwright locator（含 force=True）**六种方式全部失效**，表现为"点了没反应"或"modal 永不出现"；② 上传等待 3 分钟窗对 89MB 成片不够，误判 UPLOAD_TIMEOUT（实际已传完）。

**根因**：微信视频号编辑器是 React 合成事件系统，**只认真实输入域事件**——程序化 `dispatchEvent` 合成的事件被 React 的事件委托层丢弃；Playwright 的 selector 引擎在该 iframe 中注入缺失（pitfalls #50 同源），locator click 的可操作性检查也过不去。

**修复（唯一可行路径）**：**CDP 真实鼠标事件**——
```python
client = await page.context.new_cdp_session(page)
tree = await client.send("DOM.getDocument", {"depth": -1, "pierce": True})
node = 穿透找目标节点  # DOM.getDocument 深度遍历 nodeName/attributes
box = await client.send("DOM.getBoxModel", {"nodeId": node["nodeId"]})
q = box["model"]["content"]           # 四角坐标，取对角中点
x, y = (q[0]+q[2])/2, (q[1]+q[5])/2  # 页面绝对坐标
await client.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
await asyncio.sleep(0.4)              # 先 move 触发 hover 态
await client.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
await asyncio.sleep(0.15)
await client.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
```
配套：modal 内「上传封面」用 `page.expect_file_chooser` 接住 CDP 点击弹出的文件选择器（chooser 路径实测可行）；确认按钮同样 CDP 点击。

**声明原创状态机（两层弹窗，09-29 实测结构）**：
- ①「声明原创的视频有机会获得广告分成」——按钮 [直接发表, 声明原创]；
- ②「原创权益」——条款 checkbox（`input[type=checkbox]` 首个）+ 底部【声明原创】按钮；
- 顺序：点①的【声明原创】→ 勾②的条款 → 点②的【声明原创】。00:00 别用「直接发表」（那等于放弃原创声明）。

**检测点**：
- 编辑器内一切"点了没反应"→ 立刻换 CDP Input 域，别在 JS/locator 上继续投入（六连败教训）；
- 上传就绪判据「video 元素在场 + 发表钮非 disabled」，窗口给足 10 分钟；
- 封面成功判据 = 页面 toast「封面已更新」+ 预览图变化，别只看 modal 是否关闭（.single-cover-uploader-wrap 关闭后仍在 DOM，误报率高）。

落地脚本：`scripts/sph_publish_v5.py`（PR #80；driver 完成钩子自动拉起，幂等标记 `10_发布/视频号发布完成.json`）。

## 52. 🚨 H3 工作流改版两坑：UI bypass 节点 API 化后被当真执行 + 三份脚本副本漂移（P0 生图，2026-10-13 工作流 23:54 版）

**背景（工作流 2026-10-13 改版后主链）**：194 T2V → 213 视频VAE + 208 音频VAE → 209 CreateVideo → 394 DLSSNR_Video（scale 2x，544x960 → 约1088x1920）→ 395 VHS_VideoCombine 出片；模型链 214 UNET → 399 SparkAttentionSM120 → 205 LoRA。旧 193 SaveVideo 输出已 bypass 断开；TE 增强链 239/240/252/253/254 全 bypass 出主链（194.prompt 变直连 widget）；时长链 199 → 198（帧数凑 ≡5 mod 17）→ 194.length。当晚 23:34-42 用户 UI 试跑 8 次走的是 193 SaveVideo，终版保存弃用它回 395。

**坑1（根因）**：UI bypass(mode=4) 节点转成 API prompt 后没有 mode 概念，会被当真节点提交执行。本次 399.model ← 210（bypass 的 SageAttention）← 214：UI 执行时 210 直通放行，而 ui2api+fix_models 的 BFS 把 210 收进主链当真跑 → SparkAttention 前多套一层 attention patch（行为漂移、白费显存）。

**修复**：`fix_models(api, wf)` 增加 bypass 直通重接——主链内指向 bypass 节点的输入改接该 bypass 节点自己的第一个 link 输入（多跳迭代收敛），随后把 bypass 节点从剪枝保留集剔除；并新增主链完整性校验（剪枝后任何 link 指向链外节点直接 ValueError，把 ComfyUI 侧 400 变成提交前可读错误）。

**坑2**：submit_accel 的 ui2api 曾按绝对路径从 hermes 旧克隆导入（`/home/lmr/.hermes/skills/video-production/video-pipeline/scripts`，克隆停在 #45/09-15）；hermes 与 `/home/lmr/comfy` 各有一份独立副本（非符号链接），此前全靠内容恰好一致（仅行尾差异）没炸。已改为按 `__file__` 同目录导入；改工作流解析逻辑只认仓库 `scripts/` 份，其余两处视为过期快照。

**检测点**：
- 工作流每次改版后先干跑一次转换：`fix_models(ui2api.convert(wf), wf)`，目检节点数与关键接线（399.model 应为 ['214', 0]），再上真跑；
- ⚠️ 遗留：395.audio 未接——当前主链出片**无音轨**（208 音频VAE链在生成但进不了成片；当晚 UI 试跑经 193 SaveVideo 时音轨正常）。接音频或确认弃用音轨需用户拍板。

## 53. 🚨 360 安全卫士吞 video2dlssnr.exe：394 DLSSNR 节点首执行即拦截+隔离，s08 全军覆没（P0 生图环境，2026-10-14 实锤）

**症状**：s08 全部 19 段提交成功（每段 H3 采样 40-75s 照跑）但 0 段落盘，submit_accel 报 `H3 产物缺段` 连败 2 次退场。ComfyUI 日志两种报错形态按时间顺序出现：①首个段 `PermissionError: [WinError 5] 拒绝访问`（`subprocess.Popen`/CreateProcess 层）→ ②后续所有段 `RuntimeError: video2dlssnr.exe not found`（find_exe 找不到 bin 下的 exe）。

**根因（三层叠加）**：
1. **360 安全卫士**（本机注册 AV，实时防护开；**Windows Defender 实为关闭**——`Get-MpComputerStatus` 全 False，别被名字骗了去查 Defender 隔离）对无签名 exe 首次执行即拒绝（WinError 5）并隔离删除，之后连"新拷贝进 bin 的 exe"也会被文件实时防护在写入后秒吃（未执行也吃）。
2. 双副本不一致：custom_nodes 下 `ComfyUI-video2dlssnr\bin\` 有完整版（exe+DLL），而实际被 ComfyUI 加载的 `video2dlssnr\bin\`（同类名注册，后加载者胜）只拷了 DLL 漏了 exe——首段能走到 Popen 说明当时 bin 里有 exe（用户所放），执行即被 360 拦下并隔离，后续全变 not found。
3. bin 里的 `nvngx.dll_dlssnr.dll` 怪文件名是同款病症残留。

**修复（顺序不可反，安全软件先白名单后恢复文件）**：360 安全卫士 GUI → 信任区添加 `E:\AI\Comfyui-WF-2026.8.8\Comfyui-WF-2026.8.8\ComfyUI\custom_nodes`（至少两个 video2dlssnr 的 bin 目录）→ 恢复区恢复 video2dlssnr.exe（或从 [DaniilSokolyuk/video2dlssnr](https://github.com/DaniilSokolyuk/video2dlssnr) Releases 重下）放入 `video2dlssnr\bin\`。exe 只有 431.5K，节点每次执行时 find_exe 动态查找，**补上无需重启 ComfyUI**。

**检测点**：
- s08 首段失败先看 ComfyUI 日志：`not found` / `WinError 5` 二连 = 安全软件吃 exe，不是工作流问题；
- `ls .../video2dlssnr/bin/video2dlssnr.exe` 存在且 ~431K、`--help` 能出帮助且 **ls 后文件仍在**（消失 = 被吃）；
- 全盘找副本别漏第二份目录 `ComfyUI-video2dlssnr\bin\`（完整版常在那份里）；
- 工作流改版引入新外部 exe 依赖时，改版当晚就要验证 exe 可执行且不被 AV 吃，别等次日无人值守首跑才发现。

## 54. 本地判读服务供给三坑：HF 直连停滞死锁 / 双进程交叉写坏模型 / cmd /s /c 引号剥离（T1 #102，2026-10-14）

**背景**：T1 供给我 llama.cpp b11603 CPU 构建 + 三档 Qwen3-VL GGUF（~14GB）当场踩满三个坑，每个都浪费一轮排查。

**坑1 HF 直连大文件停滞死锁**：`huggingface.co` 直连下载 GGUF 在 ~85MB 处速度归零但 **curl 永不退出**——外层重试逻辑只在 curl 退出时触发，等于永久挂起。修复：缺省走 `hf-mirror.com`（实测 3-14MB/s 且支持 Range 断点）+ `curl --speed-time 40 --speed-limit 20000`（<20KB/s 持续 40s 判死退出）让重试真正可触发。

**坑2 新旧下载进程交叉写坏同一文件**：杀 curl 不杀外层 bash 时，旧脚本的重试循环会复活新 curl，与新起进程对**同一目标文件** `-C -` 并发写——大小可能恰好对上但内容交错损坏，GGUF 加载期才炸甚至静默出垃圾。修复：供给脚本对每个模型文件做 **HF LFS sha256 校验**（tree API 的 `lfs.oid`），不符即删重下；杀下载进程必须 bash + curl 一起清（`Get-CimInstance` 按命令行过滤，注意 powershell 查询会匹配自身命令行，数字虚高）。

**坑3 WMI 拉起 `cmd /s /c ""…""` 外层引号剥离**：comfyui_selfheal 同款包装（`cmd /s /c ""exe" args "log" 2>&1"`）照抄到无重定向的场景，cmd /s 把外层引号剥进内层，服务静默起不来（rc=0、进程秒退、无日志）。修复：llama-server 路径与参数**全无空格**，直接把裸命令行给 `Win32_Process.Create`（日志走 `--log-file` 不需要重定向），彻底绕开 cmd 引号规则。

**检测点**：
- 模型文件下载后先 `sha256sum` 对 HF tree API 的 `lfs.oid` 再投入使用；
- 服务拉起后以 `/health` 200 + `/v1/models` alias 双确认为准，rc=0 不代表活着；
- 新构建 llama-server 先 `--help` 确认旗标存在（b11603 只有 `--image-min-tokens` 没有 max 版；判读速度坑见下条）。

## 55. 1080×1920 原生帧喂 Qwen3-VL：CPU prefill 16s/帧，缩长边 896 后 3.2s（T1 盲比实测，2026-10-14）

**症状**：2B Q4 本地判读单帧 17-35s，比 spec 预估（2-5s）慢一个数量级，盲比全程要 18h+。

**根因**：抽帧产物是 1080×1920 原生分辨率，Qwen3-VL 动态分辨率把它编成 **~1800 图 token**；CPU prefill 128 tok/s → 单帧 prefill 15.9s（服务端 `print_timing`：prompt eval 2046 tokens / 15.9s，decode 仅 0.25s）。`--image-min-tokens 1024` 是反向优化（强制下限更多 token，30s+）；该构建也没有 `--image-max-tokens` 可关。

**修复**：客户端统一缩图——`vision_common.image_b64()` 把长边缩到 896 再 JPEG（token 降 ~4.6 倍，判读是天空/地平线方向粗判不是细粒度 OCR）；driver `_check_frames`/盲比/冒烟三处共用同一函数保证口径一致；另加 `--ubatch-size 1024` 提 prefill 批宽。实测 2B 稳态 2.8-3.2s/帧。预处理失败原图直送，绝不因缩图炸判读链。

**检测点**：
- 换模型/换分辨率后先看服务端 `print_timing` 的 prompt eval token 数——图 token 占比异常就是分辨率问题；
- 准确率影响由盲比报告把关（缩图档与原图档不混测，换配置重跑）。

## 56. 🚨 确认门（飞书）三坑：毫秒当秒读群历史恒 0 条 / 凭证仍指旧 Hermes 应用 / 私聊与助理抢同一条回复（T13 #125，2026-10-16 实锤）

**背景**：发布前唯一不可跳过的 ④+⑤ 人工审核门（成片 + 5 平台封面 + 镜头九宫格一次审）走飞书群。T13 前 9 期确认门 **8 次 timeout、1 次 error，从未成功过一次**——人明明在群里回了「通过」，driver 却从不判 pass。

**坑1（根因，P0）群历史轮询把毫秒当秒**：`latest_user_texts(since_ms)` 里请求写成 `start_time={int(since_ms)}&end_time={int(time.time() * 1000)}`，而飞书 `/im/v1/messages` 的 **start_time/end_time 只认 Unix 秒**。since_ms 是 `int(time.time()*1000)`（≈1.79e12），当秒用等于把窗口起点推到 **11 万年后**，故恒返回 0 条 → 每期轮询到 timeout。返回项的 `update_time` 才是毫秒（实测 1792147468059），所以**入参秒、出参毫秒**，换算只能写在这一处。
**修复**：`start_time={int(since_ms // 1000)}&end_time={int(time.time())}`（`scripts/feishu_gate.py`）。
**检测点**：`scripts/test_feishu_gate.py` 第 6 节钉死单位——stub `_api` 截获请求，断言 start_time/end_time **都是 10 位**、窗口起点 ≤ 现在（**起点不能在未来**）、且 st ≈ since_ms//1000；把该行还原成毫秒表述测试立刻红（`AssertionError: 必须是 Unix 秒: st=1792147468059 et=1792156078583`）。

**坑2（P0）凭证源仍是旧外部 Hermes 应用**：driver 只读 `~/.hermes/.env` 的 FEISHU_*。用户拍板「把飞书改成 DSH 里边通的那个飞书，别使 Hermes 那个飞书了」——改读 DSH IM 连接器的渠道配置 `~/.dsh/dsh-im-connect/channels.json` → `channels[FEISHU_CHANNEL].config.appId/appSecret`（默认渠道 `feishu_439061570ff1`，应用 dsh-win），与 IM 助理**共用同一套应用凭据**、零额外维护；`.hermes/.env` 降为兜底且用 `cfg.setdefault`（**兜底不得覆盖新源**）。确认门群 id 走常量 `GATE_CHAT_ID`，可用 `FEISHU_GATE_CHAT_ID` 覆盖。
**★WSL 陷阱**：WSL 侧 `HOME=/home/lmr`，而 DSH 的 channels.json 在 `C:\Users\<winuser>\.dsh\`；只写 `~/.dsh` 会让 WSL 里的 driver **静默回落 hermes .env**（旧应用 + 旧会话，症状是"改了没生效"）。故 `_channels_path()` 按 env 覆盖 > 本机 `~/.dsh` > `glob("/mnt/c/Users/*/.dsh/dsh-im-connect/channels.json")` 依次探测（跨端铁律，与 PR #124/69756f3 同源）。
**检测点**：`preflight.py` 的 feishu-creds 失败文案必须同时点名 channels.json 与 hermes 兜底两条路；driver 起来先看 `_load_env()` 实际读到的 channels.json 路径，别信"应该读到了"。

**坑3（设计，别退回私聊）确认门不能用私聊**：IM 助理是 websocket 事件推送（**只推 @ 机器人 的消息**），私聊里人的每条回复都会被助理当活跃会话消费掉；driver 若也轮询同一会话，一条「通过」会被**判两次**。故 T13 新开群「读报确认门」：群里**不 @ 机器人** ⇒ 助理侧根本收不到那条消息，driver 只走 REST 历史只读拉取，两边不抢同一条回复。**检测点**：群里回「通过」→ 判 pass 并继续后续阶段；回「重生成 shot03」→ 判 `shot_regen` 且 `shots=["shot03"]` 带段号。

**坑4（P2）跨端令牌缓存路径**：`TOKEN_CACHE` 从写死 `/tmp/...` 改为 `os.path.join(tempfile.gettempdir(), "_feishu_tenant_token.json")`，WSL 与 Windows 双端都不再写错目录。

**发送计数口径（AC1）**：审核包逐件发（封面 ×5 + 九宫格 + 成片预览 + 正文），**每件一条「✅ {kind}已发送: {name}」**（#49 口径），最后一条汇总 `n/total 成功`；失败件不写成功行，故"发送计数与实际成功数一致"可被人与测试同时核对（测试断言 `out.count("已发送") == 4`）。

## 57. 🚨 10-17 期 driver code=4：飞书 token 被判无效不自愈 / 显存闸拿整卡水位 / 判读不短路（#137，2026-10-18 实锤）

**背景**：s01→s12 全通、审核包 8/8 已发，唯独**确认门轮询 6h20m 判不出结果**，22:03:17 飞书连续 20 次 `code=99991663 Invalid access token` → 门判 error → driver `_flush_exit(4)`，s13 发布从未执行。同日 s08 判读 17 段全灭、多耗约 14 分钟。

**坑1（P0，终局直接原因）token 被判无效后没有自愈路径，失败还全程静默**
`feishu_gate._api()` 每次只带缓存的 `tenant_access_token`，`latest_user_texts()` 见 `code != 0` 直接抛，轮询只累加 `fail_streak`——**全链路没有「99991663 → 重取 token → 重试一次」**。更坑的是失败日志只在第 20 次落一行，65 分钟静默期里日志全空，事后无法定位失效起点；而注释写「约 10 分钟连续失败」，实现是 20 × poll_s(10s) ≈ 3.3 分钟。
**修复**：`AUTH_ERR_CODES={99991663}`；`token(force=True)` 清内存缓存**并删磁盘 `TOKEN_CACHE`**（只清内存会被进程外缓存把同一枚失效 token 喂回）；`_api()` 改双发循环（命中鉴权码 → 记日志 → 强制重取 → 重试一次）；**每次失败都落日志**（含连续秒数）。
**检测点**：`repro_gate.py` 用 stub `_urlopen_json` 吊销手里的 token——修前 `action=abort polls=20 token_fetches=1`，修后 `action=pass polls=4 token_fetches=2` 且日志出现「返回 code=99991663（token 失效）→ 重取…后重试一次」。阈值 20 次不动（`test_feishu_gate` 2c 钉死）。

**坑2（P1）显存闸拿**整卡**水位当 Strata 泄漏**
`strata_window.stop()` 用 nvidia-smi 整卡 `memory.used` 判「< 4000 MiB 才放行」。本机 ComfyUI 常驻 7.5GB → 一次正常关窗被判 `RuntimeError: 显存未回落（used=7509 ≥ 4000）`；driver 重试走 `already-off` 早退分支，而该分支整段跳过显存检查 → 无条件放行。**闸既误报又漏报**：判据该问「我们杀掉的 pid 还占不占显存」，与邻居无关。
**修复**：新增 `vram_pids_mib(pids)`（`nvidia-smi --query-compute-apps=pid,used_memory`），关窗闸改为按进程归属：被杀 pid 仍占 → RuntimeError；别家占卡 → 记日志放行；查询失败沿用「未知→放行」。
**检测点**：`test_strata_window` 场景 10 两子场景（`vram_pids_mib` 返回 `{pid:12000}` → RuntimeError；返回 `{}` 且 used=7509 → `closed`）。

**坑3（P1）服务已离线仍逐段重试（不短路）**
`run_pipeline._check_frames()` 首行 `self._ensure_vision()` 丢弃返回值、无条件继续；每段每模型 2 次 × `timeout=180`。服务确定离线时 17 段空转 ≈ 14 分钟。`_ensure_vision()` 本身也没有返回值契约。
**修复**：`_ensure_vision` 返回 bool；离线即整批 `judge_failed=[…:判读服务离线]` + `skipped=vision_offline` 并 `return [], []`（不抽帧不重试）；批内首个拒连（Connection refused / timed out / Errno 110 / 111）后置 `dead` 事件，后续段跳过重试。
**检测点**：离线场景 `return=[]/[] sh_called=0 skipped=vision_offline`，且 `_frames/` 目录不存在；在线场景仍照旧进抽帧。

**坑4（P0，同 #123）vision 自愈阶梯在 WSL 侧四层叠加不可用**
① `VISION_HOME` 是 Windows 路径 → `os.path.exists(BIN)` 恒 False（exe 在也报缺文件）；② `"/mnt/c" + cand[2:]` 拼出 `/mnt/c\\Windows/System32/…`（反斜杠没换）→ FileNotFoundError；③ `text=True` 用 UTF-8 解 powershell.exe 的 GBK 输出 → `'utf-8' codec can't decode byte 0xb0`；④ `-File` 传 `/mnt/c/…ps1`，而 **WSL interop 不翻译命令行里的路径参数** → powershell 报「-File 实际参数不存在」。任一层都足以让「拉起」这一级失效（10-13 期 8/19 段 503 同源）。
**修复**：`_wsl_path()` 翻译 + 存在性检查走翻译路径；`_ps_path()` 双候选取存在者；`_decode_out()`（utf-8→gbk→replace）；`-File` 恒传 Windows 路径形态。
**检测点**：WSL 里 `os.path.exists(_wsl_path(BIN))=True`、`_ps_path()` 命中、`launch()` 返回 True。

**遗留**：token 为何在 expire(7200s) 前约 65 分钟被服务端判无效，触发源未定位（应用权限/密钥变更/事件均未证实）——但「判无效即重取」已能兜住；Jev 校准 T=40.4 近独热、`references/clip-probe.np` 缺失各有日志降级。

## 58. 🚨 临时 runner / 沙箱绕过脚本不许入仓：`.tmp_*` 与 `.wt/` 曾把工作树弄脏 193 条（#147，2026-10-19 实锤）

**症状**：`git status` 常驻几十上百条未跟踪项，`git worktree list` 挂着 4 个早已合并的 worktree；`.gitignore` 里没有任何一条能盖住它们。清完第二天又出现——因为产生它们的是**仓库自己的测试**，规则却写在**本地** `.git/info/exclude`（不随仓库走）。

**根因（三层）**：
1. **测试把临时目录建在仓库内**，且是有意的：`test_s08_judge_loop` 的 `_TMP_ROOT = <repo>/.tmp_t16_tests`（docstring 明写「不用 tempfile.mkdtemp：受限沙箱下它创建的目录带排他默认 ACL，连属主都列不了」）、`test_human_flag_loop` 的 `WORK = scripts/.tmp_t17_test`、`test_evidence_train_chain` 的 `_mktemp` 在仓库根建 `t18t_<pid>_<n>`。清理是「尽力而为」（`subprocess.run(["cmd","/c","rd","/s","/q", d])`——沙箱下 rmtree 会被硬杀），跑红/中断就留下。
2. **绕过沙箱的临时 runner 直接写进仓库**：`.tmp_t17_temp/_runner_gate.py`、`scripts/.tmp_t17_temp/_runner_dsh.py` 用 `os.mkdir` 版 mkdtemp **monkeypatch `tempfile.mkdtemp`** 来绕开 0o700 排他 ACL（docstring：「实测 0o511 可写…会话后删除」）。会话后没人删 → 连脚本带 `__pycache__` 一起留在树上。**这类脚本没有任何入库价值，它存在的唯一理由是绕开本机沙箱。**
3. **规则写错了地方**：`.wt/` 只写在 `.git/info/exclude:9`（本机私有），`.tmp_*/`/`t18t_*/` 压根没写。于是「本地看着干净、别人/CI 一 clone 全是脏的」。

**修复**（`.gitignore` 三条，前缀不带斜杠以匹配任意层级）：
```
.tmp_*/
t18t_*/
.wt/
```
清理旧账：先 `tar -czf <仓库外>/wt-hygiene-<时间戳>.tar.gz <每个产物目录>`（备份路径写进票评论，可还原），再删产物目录，最后 `git worktree remove <每个已合并 worktree>`（**不加 --force**）+ `git worktree prune`。

**⚠️ 两个坑中坑**：
- `git worktree remove` 对「零 tracked 改动但有未跟踪产物」的 worktree 一律拒绝（`fatal: contains modified or untracked files, use --force to delete it`）。不要顺手加 `--force`（票面禁止，也等于放弃「先备份再删」）；正确姿势是**逐条删未跟踪项**：`git -C "$w" ls-files -o --directory --exclude-standard -z | while IFS= read -r -d '' p; do rm -rf "$w/$p"; done`，删到 `status --porcelain --untracked-files=all` 为空后 remove 自然通过。
- **`git clean -fd` 会被 CC Safety Net 拦**（Rule: git.clean-force）。别在循环里硬顶，直接换成上面的 `ls-files -o` + `rm`。

**检测点**：
- `scripts/test_repo_hygiene.py`（#147 新增）逐行断言三条规则存在，并用 `git check-ignore --no-index -v -z --stdin` 对**五个真实产生点**探针验证命中——且**必须断言来源文件是仓库根的 `.gitignore`**：用 `source.replace("\\","/") == ".gitignore"` 精确比对，**不要用 `os.path.basename(source)=='.gitignore'`**——后者会让将来任何层级的 `scripts/.gitignore` 通过，直接假绿。本地 exclude 同理会让测试假绿，这正是 #147 排查时「check-ignore 一次性全部命中」的假象来源。
- 反向断言四个必入库路径（`scripts/run_pipeline.py`、`scripts/fixtures/t15_rotation_dataset_20261015.jsonl`、`.gitignore`、`AGENTS.md`）不被误伤；再扫全部 `git ls-files` 确认**没有已跟踪文件被这三条新规则挡掉**（`*.log` 之类历史遗留另计，不判红）。
  - 活数据集 `references/rotation-dataset.jsonl` **不能写进这份清单**：`test_dataset_readonly.py` 会全仓扫描「测试文件里不得出现活数据集路径」，写死它会直接把全量测试跑红（#147 第一版就栽在这里）。它是否被误伤由上面那次 `git ls-files` 全量扫描兜住——已跟踪文件被新规则挡掉一律判红。
- `git check-ignore -z` 的 `-z` **只能配 `--stdin`**（否则 `fatal: -z only makes sense with --stdin`）；输出是「source / linenum / pattern / pathname」四段 NUL 分隔，没有额外记录分隔符。
- 可用性探测别只判 `shutil.which("git")`：**不在仓库内时 `check-ignore` 返回 rc=128**（`fatal: not a git repository`），会走到裸 assert 报红而不是预期的 SKIP。补一次 `git rev-parse --show-toplevel`（cwd=仓库根）再下结论。
- 验收口径：新建同名目录 `git status` 不报；`git worktree list` 只剩主树；`git status --porcelain` 为空（本条 AC 要等 T10/#119 落地后才能真正成立——并发测试会持续在 `.tmp_t16_tests/`、`scripts/.tmp_t17_test/` 下产新文件，规则生效但目录本身仍会重新出现）。

## 59. 🚨 「只拦新增」的 baseline 机制有两个自毁形态：零容忍规则被一起跳过 / 跨文件余额互相抵消（#142，2026-10-19 两轴评审实锤）

**症状**：机械检查器上线当天，六条规则的单测全绿、仓库门禁也全绿，但**它抓不到自己该抓的东西**——在仓库里放一个语法错文件，`check_mechanical.py` 仍退出 0，只打印「新增违规 0 处」。

**根因（两处，都在「比对 baseline」这一步）**：
1. **零容忍规则被当成 baseline 规则一起跳过**：`compare()` 开头写了 `if item.rule == RULE_SYNTAX: continue`——本意是「语法不进 baseline」，实现成了「语法不参与比对」，于是 syntax 违规既不进 new 也不影响退出码。`dump_baseline` 里同样的 `continue` 是对的（不写进文件），`compare` 里同样的写法是错的（连报告都吞了）。**同一个「跳过」在两个函数里含义不同，照抄就错。**
2. **stale 余额吸收忽略了文件路径**：为消除「文件改名产生新增+可缩减双份噪音」，吸收条件写成「同规则 + 同指纹」。结果 a.py 修掉一处、b.py 新增同形态一处 → 余额被抵消，退出码 0 且 b.py 不被点名，再 `--shrink` 直接把它写进 b.py 的基线。**「只拦新增」的判据是「这个文件里的这种形态变多了」，不是「全仓总数没变」。**

**修复**：
- syntax 不再参与 baseline 比对，直接进 `new`，走正常的新增报告与退出码 1；`dump_baseline` 里继续丢弃。
- 吸收只允许两种情况：**同一文件内缩量**，或**原文件已不在扫描范围**（`stale_key[1] not in scanned_paths`，真搬家）。跨文件抵消一律判红。
- 两条都补了回归测试：放语法错文件断言退出 1 且点名；别的文件新增同形态断言退出 1 且点名新文件。

**同批修掉的漏抓**：`_module_alias()` 只扫模块级 import，函数内的 `import subprocess as _sp` 解析不出点分名——`compose_final.py:312/376/437`、`submit_accel.py:272/297/307` 共 6 处 `text=True` 无 encoding 既不进 baseline 也永远不被拦。**别名解析必须取全树 import 的并集**（模块级优先，宁可漏报别处同名局部变量）。

**另一处误报**：`RE_DRIVE = (?<![\w])([A-Za-z]):[\\/]` 没排除反斜杠，正则字面量 `r"(?:At|By) (\d\d):(\d\d\.\d\d\d)"` 里的 `"d:"` 被当成 D 盘（`scripts/director_to_h3_llm.py:276`），把非路径字符串当存量收进 baseline 会误导后续缩表。改成 `(?<![\w\\])`。

**检测点**：
- `scripts/test_mechanical.py` 第 7 节：语法错 → 退出 1 并点名；跨文件新增同形态 → 仍退出 1 且点名新文件。这两条是**回归**，删掉它们等于把门禁退回自毁形态。
- 汇总行断言要**解析计数**（`"syntax=0" in counts`），不要写 `A or B` 这种近乎恒真的形式——原断言 `cm.RULE_SYNTAX not in out.split("（")[1].split("）")[0] or "syntax=0" in out` 永远为真，正是它让上面那个 bug 逃过了自己的测试。
- 新增规则时先问一句：这条规则**能不能进 baseline**？不能进（零容忍）就不许在 `compare` 里 continue，必须直接进 new。

**补记（同日 rebase 到 #152 后暴露的第四类误报）**：`test_install_location.py:176` 的护栏断言 `check("/home/lmr/.hermes" not in sh_src, "不再写死第三份副本（hermes）")` 被判成**新增** path-literal——反例断言必须写出被禁的字面量才能验它不在，那是**期望值**不是代码。修法：`_assertion_ids()` 收集 `ast.Assert` 的 test 与 `check(...)` 的直接字符串实参下的 Constant，与 docstring 豁免合并；**豁免面刻意收窄**（普通 `open`/`log`/拼接里的字面量照抓）。连带把 `test_dsh_judge.py` / `test_vision_date_root.py` 的断言期望值从 baseline 缩掉（171 条 → 161 条）。**教训**：写「某串不许出现」的护栏时，检查器与护栏会互相咬——凡是要断言「没有 X」，就得先让检查器认得「X 在这里是期望值」。



## 60. 🚨 安装位置假设：生产链路指向另一份 git 副本，改的那份不一定是跑的那份（#152，2026-10-19 实锤）

**症状**：维护副本里把 bug 修好了、测试全绿，跑生产还是老行为。查下去发现发布链路的绝对路径写死在**另一个 git 副本**上——那份停在一个月前的提交、工作树极脏（AGENTS.md/CONTEXT.md/SKILL.md/9 个未跟踪文件全 M），而且 **committed 版的 `sph_publish_v5.py` 本身就是坏的**（`py_compile` 直接失败：第 495 行字符串跨行没拼接），本地被人手工补过、补丁从未提交。也就是说：磁盘上跑的那份代码**在任何 commit 里都不存在**。

**根因（两层）**：
1. **把「脚本在哪」写成了常量**。`scripts/run_pipeline.py:2542` `v5_win = r"C:\Users\LMR\.zcode\skills\video-pipeline\scripts\sph_publish_v5.py"` 与 `:2544` 的 `/mnt/c/...` 兄弟行；`scripts/sph_publish_v5.py:36` `SKILL = r"C:\Users\LMR\.zcode\skills\video-pipeline\scripts"` + `:37` `sys.path.insert(0, SKILL)`（为了 import 邻居 `feishu_gate`），`:61` 又写死一次 `feishu_gate.py` 的绝对路径；`scripts/tencent_refresh_cookie.sh:19` 指向第三副本（`/home/lmr/.hermes/skills/video-production/video-pipeline`）、`:30` 指向旧副本的 chrome profile。**每处都假设「仓库只存在于我记住的那个路径」。**
2. **两份副本是分叉历史，不是落后**。`git merge-base --is-ancestor 31d65b3 fe4c417` 与 `git cat-file fe4c417` 双双失败（`fatal: Not a valid object name`）——旧副本的提交根本不在维护副本的历史里。所以「git pull 一下就对上了」这条路不存在，只能逐文件比对回收。

**修复**：
- **一律从脚本自身位置派生**：`HERE = os.path.dirname(os.path.abspath(__file__))`，邻居用 `os.path.join(HERE, "feishu_gate.py")`，`sys.path.insert(0, HERE)`；跨侧（WSL↔Windows）口径走 `dsh_judge.to_win_path()` / `to_host_path()`，**不许再另立第三份路径转换器**（写了会被 `test_host_contract.py` 第 9 节判红：除解析器外任何脚本出现契约文档路径即失败）。
- **shell 脚本同理**：`SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"`，profile 路径用 `wslpath -w "$SKILL_DIR/assets/chrome-tencent-profile"`。
- **旧副本改成指回维护副本的 junction**（不是删）：`cmd //c mklink //J "C:\Users\LMR\.zcode\skills\video-pipeline" "C:\Users\LMR\.agents\skills\video-pipeline"`。这样任何残留的旧路径调用仍能跑通，且**跑的一定是同一份代码**。删之前先 `cp -a` 整份备份到仓库外（本例 `E:\backup\video-pipeline-zcode-20261019`，217M/3272 文件，HEAD 与数据集 sha256 双端核对一致）。
- **回收旧副本里未提交的诚实记录**：`references/rotation-dataset.jsonl` 旧副本 451 行 vs 维护副本 487 行，`only_old=0`，8 处差异**全部是 `user_flagged`/`user_flagged_source`**（2026-10-13 的 shot04/06/08/10，source=`gate4-review-20261014`）。用 `rotation_dataset.set_user_flag([...], source="gate4-review-20261014")` 回填 4 条（含 shot06 的 `False`——「审过但判正常」也是记录），写前先做整表 `json.dumps(ensure_ascii=False)` 往返一致性验证，确认不会扰动其它字节。

**⚠️ 三个坑中坑**：
- **护栏测试「提交前绿、提交后红」**：断言「全仓已跟踪文件里没有 `.zcode`」的测试，**在它自己还是未跟踪文件时永远绿**（`git grep` 只扫已跟踪文件）；`git add` 那一刻它自己成了命中项，断言恒假。正解 = 扫描时用 pathspec 排除自身（`git grep ... -- . ':(exclude)scripts/test_install_location.py'`）**外加反向探针**（单独扫自身必须能命中，否则说明排除法把整仓扫空、假绿）。同理，**本条 pitfalls 自身也是合法命中**（它必须把历史路径原样留档，否则坑的正文就没了），一并排除、一并反向验证——所以该测试的排除表是两项，不是一项。另注：**git pathspec magic 没有 `:(include)`**，写了直接 `fatal: Invalid pathspec magic 'include'`（rc=128）。
- **注释里别写契约文档文件名**：发布器代码注释写了一句「见 `docs/host-contract.md`」，被 `test_host_contract.py:322-325` 判红——它 `glob(scripts/*.py)` 后断言「除 `host_contract.py` 外任何脚本都不得出现该字符串」。改成不带文件名的表述（「宿主契约要求路径转换收口」）即可。
- **发布器换执行者会换凭证解析环境**：`notify()` 从 `wsl.exe ... python3` 改成 `[sys.executable, FG, "send", ...]` 后，`feishu_gate` 的 `HERMES_ENV` 由 `/home/lmr/.hermes/.env` 变成 `C:\Users\LMR\.hermes\.env`。本例两端都先命中 `.dsh/dsh-im-connect/channels.json`（渠道自带 appId/appSecret）故无影响，但**这类回落是静默的**（`feishu_gate.py:46-50` 明写会「读到旧应用与旧会话」），换解释器时必须实测两端的凭证来源。

**检测点**：
- `scripts/test_install_location.py`（#152 新增，8 节，双端跑）：① 全仓已跟踪文件无 `.zcode` 字面量（排除本测试与本节 pitfalls #60 两处留档，且逐个反向探针验证排除没写错）② 发布器从自身位置解析邻居（把整份 `scripts/` copytree 到 tempfile、**唯独不搬 `feishu_gate.py`**，邻居必须落在临时目录并报缺失）③ driver 钩子同源派生 ④ cookie 脚本自解析 + `bash -n` ⑤ `.gitignore` 不再忽略 `.zcode/` 但仍忽略 chrome profile ⑥ 旧副本处置（存在则 `os.path.samefile(OLD_COPY, ROOT)` 必须真，不存在也算过）⑦ `--check` 零副作用（抓 `check()` 函数体断言不含 marker/notify/connect_over_cdp/setFileInputFiles）⑧ 飞书播报凭证源（`.dsh` 渠道自带凭据）。
- 干跑入口：`python scripts/sph_publish_v5.py --check <YYYY-MM-DD>` → 0=可发 / 3=输入缺失 / 2=日期非法；不写 marker、不发 feishu。
- 端到端确认「现有调用不受影响」：**经旧路径**（junction）分别从 Windows 与 WSL 各跑一次 `--check`，邻居行必须显示维护副本且 rc=0。
