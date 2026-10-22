# 字体资产

## SmileySans-Oblique.ttf（得意黑·斜体）
- 用途：读报视频顶部新闻标题（drawtext）+ 底部口播字幕（libass FontName="Smiley Sans"）
- 选定：2026-09-05 用户从 6 款免费商用字体对比中选定（5号）
- 来源：https://github.com/atelier-anchor/smiley-sans （v2.0.1）
- 许可：SIL Open Font License 1.1——可自由商用、嵌入、再分发（需随附许可文本，
  完整文本见上游仓库 OFL.txt：https://github.com/atelier-anchor/smiley-sans/blob/main/OFL.txt）
- 本地安装：`cp assets/fonts/SmileySans-Oblique.ttf ~/.fonts/ && fc-cache -f`
  （libass 解析 FontName 依赖 fontconfig；未安装时 _burn 自动回退 Noto Sans CJK SC 并告警）
