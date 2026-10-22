# 阶段5 定向适配：按输入自动切换 MiniMax 官方提示词模式

> 2026-09-01 · 讨论定稿 · 状态：**方案（待落地到 director_to_h3_llm.py）**
> 依据：MiniMax 官方 `h3-prompt-writing` skill 的 base-en.txt（T2VA/I2VA/FL2VA/L2VA）+ ref-en.txt（Ref2VA 六段式）

## 一、问题

阶段5（`director_to_h3_llm.py`）当前**硬编码 T2VA**：
- `rec["mode"] = "t2v"`（写死）
- `reference: None`（不支持参考图）
- SYSTEM prompt 只有 T2VA 写法

但用户可能提供参考图/视频/音频（如首帧、尾帧、风格参考、角色参考）。此时**官方写法必须切换**（base-en.txt §2.1）：

| 输入 | 官方模式 | 结构差异 |
|---|---|---|
| 无参考 | **T2VA** | 直接三字段，`[Shot 1]` 起 |
| 首帧参考图 | **I2VA** | 开头加 `For the target video, at 0.00 seconds..., <Picture 1> (from [Shot 1]) is fully referenced.` |
| 首尾帧 | **FL2VA** | 开头加 `Picture 1 aligns with 0.00s; Picture 2 aligns with S.SS秒` + 单镜路径 |
| 尾帧 | **L2VA** | 开头加 `<Picture 1> aligns with S.SS秒` + 收敛路径 |
| 多参考资产 | **Ref2VA** | 六段式：`subject_definitions / summary / retention_analysis / detailed_description / overall_soundscape / non_diegetic_music` |

## 二、目标

**阶段5 自动判断输入，切换对应官方写法**：
- 无 reference → T2VA（现状）
- 有 reference → 按参考类型（图/视频/音频、数量、位置）选 I2VA/FL2VA/L2VA/Ref2VA，注入对应的"对齐指令"段 + 参考标签规则

## 三、实现方案

### 3.1 输入接口（CLI 参数扩展）

```bash
# 现有
director_to_h3_llm.py <设计> <输出> --durations ... --overall ... --style ...

# 新增 --reference 参数（可重复）
director_to_h3_llm.py ... --reference image:first:/path/first_frame.png
                           --reference image:last:/path/last_frame.png
                           --reference video:/path/ref.mp4
                           --reference audio:/path/ref.wav
```

格式：`类型:位置:路径`
- 类型：`image` / `video` / `audio` / `subject`
- 位置：`first`（首帧→I2VA）/ `last`（尾帧→L2VA）/ `first+last`（FL2VA）/ `style`（风格参考）/ `character`（角色→Ref2VA）

### 3.2 自动判断逻辑（process_shot 内）

```python
def infer_mode(refs):
    if not refs: return "t2v", None
    images = [r for r in refs if r.type == "image"]
    if len(images) == 2 and {r.pos} == {"first", "last"}: return "fl2va", images
    if images and images[0].pos == "first": return "i2va", images[0]
    if images and images[0].pos == "last": return "l2va", images[0]
    return "ref2va", refs  # 多资产/视频/音频/角色
```

### 3.3 模式对应的 SYSTEM prompt 注入（前缀指令）

```python
MODE_PREFIX = {
  "i2va":  "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n",
  "fl2va": "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark; Picture 2 (from Shot N) aligns with the {dur}-second mark.\n\n",
  "l2va":  "How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the {dur}-second mark.\n\n",
  "ref2va": "使用六段式：subject_definitions / summary / retention_analysis / detailed_description / overall_soundscape / non_diegetic_music（参考标签 <Subject N>/<Picture N>/<Video N>/<Audio N> 跨段保持一致）",
}
```

### 3.4 落盘字段

```python
rec = {
  "id": sid, "prompt": prompt,
  "mode": mode,                    # "t2v"/"i2v"/"fl2v"/"l2v"/"ref2v"
  "reference": refs or None,       # 参考资产描述
  "scene_source": "director_llm",
}
```

## 四、质量保证

- **T2VA 默认不变**（无 reference 时零影响，现有 21 段流水线不动）
- 参考模式仅在 `--reference` 传入时激活（向后兼容）
- 参考标签跨段一致性：SYSTEM prompt 强调 `<Subject N>/<Picture N>` 复用同一标签
- 官方 ref-en.txt 的 retention_analysis（保留/转移/复用）规则只在 Ref2VA 模式注入

## 五、验证

1. 无 reference → 走 T2VA（现有产物对比，应一致）
2. `--reference image:first:xxx.png` → 输出含 "at 0.00 seconds... <Picture 1> (from [Shot 1])"
3. `--reference image:last:xxx.png` → 输出含 "<Picture 1> (from [Shot N]) aligns with S.SS秒"
4. `--reference image:first + image:last` → 输出含 "Picture 1 aligns with 0.00s; Picture 2 aligns with S.SS秒"
5. Ref2VA（多资产）→ 输出六段式

## 六、与现有流水线的关系

- 每日新中国：无参考图 → T2VA（现状，不受影响）
- 未来：如果某期要用"首帧定风格"（I2VA）、"角色一致"（Ref2VA）、"尾帧收束"（L2VA）→ 传 `--reference` 即可自动切换
- 这是**能力增强**，不是改写——官方五种模式全部可用，按需调用

## 七、参考资产来源（未来场景）

- I2VA 首帧：ComfyUI 生成的定妆图/风格帧
- FL2VA：首尾帧（如"从实景过渡到插画"）
- Ref2VA 角色：`references/video-craft/characters/` 角色卡
- Ref2VA 风格：风格块的参考图
