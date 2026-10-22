# Realistic Documentary（写实纪录片）

> 来源：video-pipeline 自己的风格变体（用户已有 director_llm_realistic.py）
> 用途：导演设计 / H3 prompt 的风格锁参考
> 定位：真实场景/纪实/科技现场类内容

## 视觉语言

- **真实场景质感**（real-world textures）：现场实拍感、自然光、生活化道具
- 自然色调、低饱和、电影感 LUT（35mm Film Grain）
- 不追求夸张 CG，追求"可信的现场"

## 色彩

- 自然色系：中性灰/大地色/环境本色
- 低饱和 + 轻微胶片颗粒
- 电影感色调分级（统一 LUT）

## 运动语言

- 手持跟拍（handheld）
- 稳定器平滑移动（gimbal）
- 自然节奏、不加戏

## 负向约束

- 禁卡通化
- 禁霓虹/赛博
- 禁塑料感渲染
- 禁过度调色

## 转译到 H3 prompt 的英文关键词

```
realistic documentary style, real-world textures, natural lighting,
handheld or gimbal camera, natural color grading, low saturation,
35mm film grain, cinematic LUT, believable real scene, no cartoon, no neon
```

## 对 video-pipeline 的适配

- 最适合：新闻纪实/科技现场/真实场景类内容
- 与 H3 契合：H3 擅长写实场景渲染（优于夸张风格）
- 与 paper-collage 对比：realistic 更"现场感"，paper-collage 更"编辑感"

## 升级路径（ponytail）

当前是纯视觉语言参考，与 director_llm_realistic.py 对应。
