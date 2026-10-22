# 阶段9：封面（Krea2 数字迷幻流 · 手动输入参考模式）

> 用 Krea2 数字迷幻工作流生成海报底图，替代旧的抽帧+PIL叠字方案。
> 工作流路径：`/mnt/e/AI/Comfyui-WF-2026.8.8/Comfyui-WF-2026.8.8/ComfyUI/user/default/workflows/krea2数字迷幻流-CLI.json`

## 工作流结构（已解析，不可改结构，只改两个节点）

| 节点 | 类型 | 作用 | 改什么 |
|------|------|------|--------|
| 198 | ZML_TextInput | **手动输入参考入口** | 填参考文本（海报提示词） |
| 140 | ZML_PresetResolution | 分辨率预设 | 横版 `SDXL_1344x768` / 竖版 `SDXL_768x1344` |
| 18 | EmptyLatentImage | 空白 latent | 横版 1536×768 / 竖版 768×1344 |
| 197 | PromptExpand | 扩写（9router/low） | 不动，自动把 198 的文本扩写为英文数字迷幻 prompt |
| 217→218 | SetNode→GetNode | 同名配对 | 不动，自动传递扩写结果到 CLIPTextEncode |
| 2/86 | KSampler/KSamplerAdvanced | 采样 | 不动 |

**只改 198（参考文本）+ 140/18（分辨率）**。其余节点保持工作流原样。

## 参考文本设计规则

参考文本 = 填入 198 的中文描述，PromptExpand(197) 会用 9router/low 自动扩写为英文数字迷幻 prompt。

### 结构（150-280 字）
1. **主体**：核心意象（如"零碳工厂""薄膜光伏板""量子纠缠装置"）
2. **背景**：场景环境（如"三峡大坝""数据中心机柜阵列""朝阳地平线"）
3. **材质/特效**：数字迷幻元素（如"发光数据流""全息电路""彩虹油膜干涉"）
4. **色彩**：明确指定色调方向（见下）
5. **构图**：横/竖版构图意图

### 色调策略（按稿件情绪选）

| 情绪 | 色调关键词 | 示例稿件 |
|------|-----------|---------|
| 光明希望 | 金色朝阳、暖橙、晨光白、希望绿——高调光明无暗色 | 科技突破、成就记录 |
| 深色科技 | 翡翠绿、电光青、放射金，深渊黑对比自发光体 | 工业系统、能源基础设施 |
| 冷峻神秘 | 深蓝、银白、幽蓝辉光——冷调高对比 | 量子物理、航天 |

**色调必须在参考文本里明确写**——PromptExpand 和 Krea2 模型都依赖文本驱动色彩。

### 内容信息要求
- 概括稿件主要信息点（核心主体 + 1-2 个关键数据/场景）
- 吸引眼球（视觉冲击 > 信息密度）
- 审美高级（数字迷幻风格自带高级感，不要堆砌俗套元素）
- 不要写英文 prompt（198 是中文，197 自动扩写为英文）

## API 图转换（krea2_to_api.py）

工作流是 UI 格式（含 SetNode/GetNode + 静音节点），ComfyUI /prompt API 不认。转换脚本处理：

1. **过滤 mode=4 静音节点**（图片参考链/Danbooru/ImageCaption 等）
2. **SetNode/GetNode 同名配对展开**（217→218 传递扩写结果）
3. **静音节点 pass-through 透传**（如 142 ConditioningKrea2Rebalance 静音 → KSampler positive 透传到 7 CLIPTextEncode）
4. **KSampler/KSamplerAdvanced widget 映射**（跳过 control_after_generate 值）
5. **Anything Everywhere VAE 广播补连**（API 模式不认广播 → 手动给 VAEDecode/VAEEncode 补 `vae: ["4", 0]`）

```bash
# 用法
python3 krea2_to_api.py <workflow.json> <out_api.json> <参考文本> <分辨率预设> <宽> <高>
# 例
python3 krea2_to_api.py krea2数字迷幻流-CLI.json out.json "参考文本..." SDXL_1344x768 1536 768
```

输出是**裸 API 图**（不带 `{"prompt":}` 包装）——comfy-mcp 的 `run_workflow` 要求裸图。

## 提交（comfy-mcp）

```
COMFYUI_URL = http://192.168.50.231:18188（固定局域网 IP，不走 WSL 桥）
链路: WSL → 192.168.50.231:18188 → portproxy → 127.0.0.1:8188 → ComfyUI
```

1. `run_workflow(workflow_path=<bare_api.json>, wait=False)` → 返回 prompt_id
2. `job(action="wait", prompt_id=<id>, timeout_seconds=600)` → 等完成
3. 输出在 `ComfyUI/output/Krea2_*.png`，job 结果含 view URL

## 生成后处理

1. **vision 复检**：色调是否符合预期 / 主体清晰 / 无变形乱码 / 元素到位
2. **归入 `09_封面/`**：命名 `海报_krea2数字迷幻_<尺寸>.png`
3. **落盘参考文本**：`09_封面/海报_提示词_krea2数字迷幻.md`（便于复用/改版）
4. **发飞书审核**：`send_feishu_image.py`（chat_id = `oc_f036022e27553df636e533181a04b72b`）

## 平台规格

| 平台 | 尺寸 | 分辨率预设 | EmptyLatentImage |
|------|------|-----------|-----------------|
| B站 | 16:9 横版 → 2016×1152 | SDXL_1344x768 | 1536×768 |
| 抖音/视频号 | 9:16 竖版 → 1152×2016 | SDXL_768x1344 | 768×1344 |

## 连接链路配置（一次性，已持久化）

| 组件 | 配置 | 持久化 |
|------|------|--------|
| ComfyUI 启动参数 | `--listen 0.0.0.0`（绘世启动器自定义参数） | 启动器配置 |
| Windows portproxy | `0.0.0.0:18188 → 127.0.0.1:8188` | netsh 持久化 |
| Windows 防火墙 | 放行 18188 入站（TCP） | netsh 持久化 |
| config.yaml | `COMFYUI_URL: http://192.168.50.231:18188` | 手动改 |

**注意**：192.168.50.231 是 Windows 固定局域网 IP。若换网络需更新此 IP。portproxy 用 18188 避开 8188 占用坑（pitfalls 第 8 条）。

## 与旧方案的关系

旧方案（video-cover-generation skill / make_platform_covers.py）= 抽帧 + PIL 叠字，仍保留用于：
- 需要精确标题文字叠加的场景（金色标题+黑描边）
- 无需 AI 生图的快速封面

新方案（Krea2 数字迷幻）= AI 生成底图，用于：
- 海报级封面（数字迷幻风格，审美高级）
- 无需叠加文字（底图本身就是完整海报）

两套方案并存，按稿件需要选。
