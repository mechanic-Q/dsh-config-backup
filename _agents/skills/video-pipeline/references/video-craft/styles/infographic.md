# Infographic（深蓝信息图）

> 来源：video-pipeline 自己的风格变体（用户已有 director_llm_infographic.py）
> 用途：导演设计 / H3 prompt 的风格锁参考
> 定位：数据/科技/解释类内容的信息图可视化

## 视觉语言

- **深蓝面板**（deep blue panel）为底
- **动态图表**（dynamic charts）：折线/柱状/饼图/数据流
- **扁平图标**（flat icons）：几何简化图标体系
- 数据可视化：数字醒目、单位清晰、趋势可视

## 色彩

- 深蓝底（#0a1628 系）+ 青色（#00d4ff）/ 橙色（#ff9f1c）高亮
- 信息层级：背景深蓝 → 图表青色 → 关键数字橙色

## 运动语言

- 数据流流动（data streams）
- 图表增长动画（chart growth）
- 图标弹出（icon pop-in）
- 数字滚动/高亮（number count-up/highlight）

## 负向约束

- 禁照片实景（信息图是图形化的）
- 禁杂乱多色（控制在 3 色体系）
- 禁无层级的大段文字

## 转译到 H3 prompt 的英文关键词

```
deep blue data-panel background, dynamic animated charts, flat icons,
data visualization, cyan and orange accent highlights, information hierarchy,
data streams, chart growth animations, icon pop-in, number count-up,
clean geometric layout, science explainer style
```

## 对 video-pipeline 的适配

- 最适合：数据/科技/航天/突破类新闻（数字多、逻辑强）
- 与 H3 的契合：H3 擅长文字/图表/UI 清晰渲染（官方 3d skill 也强调 H3 的 packaging/UI 强项）
- 与 paper-collage 对比：infographic 更"理性科技"，paper-collage 更"人文科普"

## 升级路径（ponytail）

当前是纯视觉语言参考，与 director_llm_infographic.py 对应。若以后要自动化校验风格一致性，升级 check 脚本。
