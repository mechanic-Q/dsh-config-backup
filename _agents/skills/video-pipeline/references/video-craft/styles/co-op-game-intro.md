# Co-op Game Intro（双人合作游戏开场）

> 来源：MiniMax H3 官方 skill `co-op-game-intro-generator`（53 行）提炼
> 用途：导演设计 / H3 prompt 的风格锁参考
> 定位：双人合作游戏菜单/开场动画，两名角色 + 玩家卡 + 菜单交互

## 视觉语言

- **游戏主菜单 UI**（game main menu UI）：高品质游戏宣传海报感，UI 与角色深度融合
- **固定框架**（framework-preserving）：中央角色、左上玩家卡、右侧竖排菜单、底部警戒胶带、少量角落涂鸦点缀、Z 字形阅读路径、留白充足、Continue 为视觉焦点
- **现代商业游戏 UI**：统一按钮尺寸、圆角矩形、轻微倾斜、极简喷溅/滴落元素、现代干净贴纸设计
- 干净、强视觉冲击、克制装饰（avoid over-decoration）

## 色彩

- **五色以内调色板**（palette within five colors）：主色 + UI 底色 + 文本色 + 功能强调色 + 红色危险强调
- 高对比色块（high-contrast color blocking）、清新现代感
- 所有 UI/按钮/图标/文字颜色必须匹配调色板

## 运动语言

- 菜单交互运动：按钮 hover/点击状态、Continue 大按钮中心高亮
- 角色 + UI 联动，UI 环绕角色而非遮挡
- 保持游戏菜单的动效节奏（轻快、有反馈感）

## 角色处理（identity mapping）

- 玩家参考图只用于身份映射：可辨识脸型轮廓、发型、眼镜、相对面部比例、独特特征
- **不继承**照片写实、皮肤纹理、真实光照、镜头质感、原图风格
- 把脸重绘进选定风格，同时保留身份锚点

## 负向约束

- 禁复杂多页 UI、禁精确复刻品牌 logo
- 禁无角色的通用标题序列（generic character-free title sequences）
- 禁过度装饰、脏污/密集划痕/过多墨渍/大片油漆飞溅
- 禁文字换行/两行标题（单行标题铁律）

## 转译到 H3 prompt 的英文关键词

```
two-player co-op game intro, game main menu UI, high-quality game promo poster,
deep UI-character integration, modern commercial game UI design,
two centered characters with player cards and vertical side menu,
palette within five colors, high-contrast color blocking, fresh modern look,
rounded rectangle buttons, Continue as central highlighted button,
bold sans-serif all-caps typography, tight tracking, heavy strokes,
clean premium UI-first background, slight texture detail only,
top main light with soft shadows and rim light, contact shadows, high-quality GI
```

## 对 video-pipeline 的适配

- 最适合：游戏/双人/角色类选题、游戏介绍、角色开场
- 与 H3 契合：H3 擅长 UI/文字/界面清晰渲染 + 角色一致
- 与 3d-animation 对比：co-op 是游戏菜单框架（角色 + UI 卡片），3d-animation 是通用拟人化故事剧场
- ⚠️ 确认图前置：官方流程是先出 1 张确认图（框架保留 + 风格主导）等用户批准再生成视频——你的流水线阶段2 方向卡已覆盖此确认门

## 升级路径（ponytail）

纯视觉语言参考。若未来要自动化校验（读导演设计检查是否用了本风格元素），升级为 `styles/check_style.py`。
