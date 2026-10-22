# Spike 报告：Windows 宿主的字体预检与字幕/标题字体实测（票 #150，AC3）

> 票 #150 的 AC3 实测记录：`fc-match` 在 Windows 上不存在，需给 s10 的字幕族名预检找替代实现。
> 本票不改流水线代码；`scripts/run_pipeline.py` 全程只读。临时素材全部在 `%TEMP%\\fontspike\\`，仓库只新增本文件。
> 环境：Windows 10 22H2（10.0.19045），Git Bash（MINGW64）执行命令；WSL2 Ubuntu（kernel 6.6.114.1）作对照。
> 关键被测对象：`C:\\ffmpeg-master-latest-win64-gpl\\bin\\ffmpeg.exe`（N-111911-gc4ab17a62d-20230904，配置含 `--enable-libass --enable-libfreetype --enable-fontconfig`）。
> 样本字体：`assets/fonts/SmileySans-Oblique.ttf`（2629764 字节）与用户字体目录 `SmileySans-Oblique-2.ttf`（2098640 字节），两者 name 表完全一致。

## 结论速览

| AC | 结论 |
|---|---|
| **B（前置阻塞）** | **`C:\\ffmpeg-master-latest-win64-gpl` 这个构建根本没有 drawtext 滤镜**——`-filters` 无、`-h filter=drawtext` 报 `Unknown filter 'drawtext'`、实跑 `No such filter: 'drawtext'`。s10 顶部标题**在 PATH 首选 ffmpeg 上跑不起来**，与转义无关。 |
| B（转义写法） | 在**有** drawtext 的 ffmpeg 4.3.1 上：`drawtext=fontfile='C\\:/Users/.../SmileySans-Oblique.ttf'`（**单引号 + 正斜杠 + 冒号前一层反斜杠**）✓ 生效；裸反斜杠路径**任何形式都不 work**；无引号时需**两层**冒号转义 `C\\\\:/`。 |
| A（族名） | Windows 的 libass 走 **DirectWrite+GDI**（不用 fontconfig）。`FontName=Smiley Sans` **必然静默回退**（回退到微软雅黑），因为该字符串只是 nameID 16 typographic family，不是 Windows 族名（nameID 1）。**可用族名只有 `Smiley Sans Oblique` 或 `得意黑`。** |
| A（失败行为） | **完全静默**：族名不存在时 rc=0、无 warning，只在 `-v verbose` 的 `fontselect:` 行露出马脚。md5 可硬证：`Smiley Sans` / `Noto Sans CJK SC` / `NoSuchFontXYZ123` 三者输出**字节完全一致**。 |
| A（fontsdir） | `fontsdir` 能让**未安装**的字体生效（Noto CJK 实测 ✓），但**不改变名字解析规则**——族名不对照样回退，尽管字体文件已加载进内存。 |
| C（推荐） | **主方案：文件存在性检查**（0.099 ms，仓库自带 `assets/fonts/SmileySans-Oblique.ttf`）+ **把族名常量改成 `Smiley Sans Oblique`**。**降级：ffmpeg 渲染探针**（0.052 s，读 fontselect 行）。**注册表不可用**（值名是中文「得意黑 斜体 (TrueType)」，按名 grep 匹配不到）。 |
| C（失败策略） | **fail-fast，不回退**。对齐 CONTEXT.md「必需资产」口径：字体是 skill 自带静态资产，缺失即环境预检拦截、永不静默回退。 |
| D（族名） | Windows 用 **`Smiley Sans Oblique`**（ASCII 最稳）；`得意黑` 同样命中但受控制台/编码影响。**回退族名必须从 `Noto Sans CJK SC` 改为 `Noto Sans SC`**——Windows 没装 CJK 变体，现常量会静默回退到雅黑。 |

## 0. 阻塞项：PATH 首选的 ffmpeg 没有 drawtext

这是本票最重要的发现，**优先级高于 B 的转义问题**——转义再对，滤镜不存在也跑不起来。

三重确认（全部实测）：

```bash
$FF -hide_banner -filters | grep -w drawtext        # → 无输出（NOT FOUND）
$FF -hide_banner -h filter=drawtext                 # → Unknown filter 'drawtext'.
$FF -y -v error -f lavfi -i color=c=black:s=320x240:d=1 -vf "drawtext=text=hi" -frames:v 1 out.png
# → [AVFilterGraph @ ...] No such filter: 'drawtext'
#   Error initializing a simple filtergraph
```

该构建共 **546 个滤镜**，`draw` 系列只有 `drawbox` / `drawgraph` / `drawgrid`。但它的 configuration **确实含**：

```
--enable-libfreetype --enable-libfribidi --enable-fontconfig --enable-libass
```

即 configure 开了依赖却**没把 drawtext 编进去**（BtbN 系 Windows GPL 构建的已知形态）。libass 的 `subtitles` / `ass` 滤镜**在**（它走 DirectWrite，不依赖 freetype）。

PATH 解析顺序（`which -a ffmpeg` 与 PowerShell `Get-Command ffmpeg -All` 一致）：

| # | 路径 | 版本 | drawtext | 备注 |
|---|---|---|---|---|
| 1 | `C:\\ffmpeg-master-latest-win64-gpl\\bin\\ffmpeg.exe` | N-111911-gc4ab17a62d-20230904 | **无** | **裸名 `ffmpeg` 会命中这个** |
| 2 | `C:\\Users\\LMR\\anaconda3\\Library\\bin\\ffmpeg.exe` | 4.3.1 | **有** | 含 fontconfig/freetype/libass |
| — | WSL `/usr/bin/ffmpeg` | 6.1.1-3ubuntu5 | 有 | 对照用 |

脚本里 ffmpeg 是按**裸名**调用的（`scripts/run_pipeline.py:1809` `cmd = ["ffmpeg", "-y", "-v", "error", ...]`；另见 `:1235`、`:1766` 的 `sh(f'ffmpeg ...')`），所以 Windows 上 s10 会命中第 1 个，**`drawtext` 直接失败**（`Filter not found`）。

**这不是本票能改的代码问题**（AC3 只负责字体预检），但必须让后续实现票知道：迁移 s10 到 Windows 前，**要么换带 drawtext 的 ffmpeg，要么把顶部标题也改成 libass 的 `ass`/`subtitles` 路线**。实测：同一 filtergraph 在 anaconda 4.3.1 上 rc=0，在 gpl 构建上 rc=3015096584 且 `Error opening output files: Filter not found`。

## 1. AC-B：drawtext `fontfile` 的 Windows 路径写法

在有 drawtext 的 anaconda ffmpeg 4.3.1 上做矩阵。素材：`smiley.ttf` 复制到 `%TEMP%\\fontspike\\`；文本「得意黑 ABC」；`fontsize=64`；`x=20:y=60`。
**判据用输出 PNG 的 md5**：`278ee561ce13` = 真正吃到得意黑；`e5d2e6fa013d` = 静默回退（日志出现 `Using "C:/Windows/fonts/CascadiaCode.ttf"`）。

| # | `fontfile` 写法（Python 字面量） | rc | 耗时 | md5 | 判定 |
|---|---|---|---|---|---|
| 1 | `C:/Users/.../SmileySans-Oblique.ttf`（正斜杠裸写） | 0 | 0.128s | e5d2e6fa013d | **静默回退** |
| 2 | `C\\:/Users/...`（单层转义冒号） | 0 | 0.120s | e5d2e6fa013d | **静默回退** |
| **3** | `C\\\\:/Users/...`（**两层**转义冒号） | 0 | 0.117s | **278ee561ce13** | **✓ 生效** |
| 4 | `C:\\Users\\...`（裸反斜杠） | 0 | 0.127s | e5d2e6fa013d | 静默回退 |
| 5 | `C\\:\\\\Users\\...` | 0 | 0.144s | e5d2e6fa013d | 静默回退 |
| 6 | `C\\\\:\\\\Users\\...` | 0 | 0.121s | e5d2e6fa013d | 静默回退 |
| **7** | `'C\\:/Users/...'`（**单引号 + 单层**冒号转义） | 0 | 0.115s | **278ee561ce13** | **✓ 生效** |
| 8 | `'C\\\\:/Users/...'`（单引号 + 两层） | 0 | 0.119s | e5d2e6fa013d | 静默回退 |
| 9 | `"C:/Users/..."`（双引号） | 0 | 0.124s | e5d2e6fa013d | 静默回退 |
| **10** | `assets/fonts/SmileySans-Oblique.ttf`（**相对路径**，cwd=仓库根） | 0 | 0.111s | **278ee561ce13** | **✓ 生效** |

**规则总结**：

- 盘符冒号在 filtergraph 里需要转义，但**引号会改变需要的层数**：**加了单引号 → 恰好一层 `\\:`**；**不加引号 → 要两层 `\\\\:`**（filtergraph 对未引用值再做一次 unescape）。
- **裸反斜杠路径任何形式都不 work**：反斜杠是 filtergraph 的转义字符，`\\U` `\\L` `\\a` 等被当转义序列吃掉，路径被截断。**必须换成正斜杠**。
- **双引号无效**（filtergraph 只认单引号做引用）。

**推荐写法（可直接抄）**：

```python
# 最稳：单引号 + 正斜杠 + 冒号前一层反斜杠
fontfile = "'" + title_font.replace("\\", "/").replace(":", "\\:") + "'"
vf = f"drawtext=fontfile={fontfile}:expansion=none:text='{text}':..."
```

**更稳的写法**：改用**相对路径**（`os.path.relpath`，cwd 设成仓库根），彻底绕开冒号与反斜杠——第 10 行实测生效，且不受引号/层数规则影响。

**静默性**：以上所有失败用例 **rc 均为 0**，只在 `-v verbose` 的 `[Parsed_drawtext_0 @ ...] Using "C:/Windows/fonts/CascadiaCode.ttf"` 一行露出马脚。drawtext 找不到 fontfile 时**不报错，静默回退到默认字体**（该版本 `font` 默认 `Sans`，fontconfig 缺配置时落到 CascadiaCode）。
另注：anaconda 4.3.1 每次都打印 `Fontconfig error: Cannot load default config file`，这是它没带 fontconfig 配置文件的常态告警，**不代表字体加载失败**，可忽略。

### 1.1 subtitles 的路径写法（顺带实测，s10 同样会遇到）

s10 的 `subtitles={srt}` 同样吃 Windows 盘符。实测（gpl 构建，`FontName=Smiley Sans Oblique`）：

| 写法 | rc | 耗时 | 结果 |
|---|---|---|---|
| `subtitles=C:/Users/.../test.srt` | 4294967274 | 0.041s | **失败**（`Error opening output files: Invalid argument`） |
| `subtitles=C\\:/Users/.../test.srt`（无引号一层） | 4294967274 | 0.038s | **失败** |
| `subtitles=filename='C\\:/Users/.../test.srt'` | **0** | 0.132s | **✓ 成功** |
| `subtitles=C:\\Users\\...\\test.srt`（裸反斜杠） | 4294967274 | 0.041s | **失败** |

→ **subtitles 必须用 `filename='...'` 命名参数 + 单引号 + 正斜杠 + 一层冒号转义**。与 drawtext 的结论一致：**单引号是最可靠的护城河**。

## 2. AC-A：族名能否直接用 "Smiley Sans"（真实对照实验）

### 2.1 实验装置

`%TEMP%\\fontspike\\base.mp4`：720x1280、3 秒、深色底、libx264 yuv420p（生成耗时 **0.149s**）。
`test.srt`：单 cue 0–3s，文本「得意黑字体渲染测试 ABC 2026」。
命令形态：`[ffmpeg.exe, -y, -v, verbose, -i, base.mp4, -vf, <vf>, -frames:v, 1, <out.png>]`，单帧渲染 **0.104–0.144s**。

### 2.2 矩阵（FontSize=64，Alignment=5 便于看整行）

| tag | `force_style FontName` | fontsdir | rc | 耗时 | fontselect 结果 |
|---|---|---|---|---|---|
| m01_arial | `Arial` | 无 | 0 | 0.124s | `(Arial,400,0) -> ArialMT, 0`；随后 `Glyph 0x5F97 not found, selecting one more font` → `MicrosoftYaHeiUI, 1` |
| m02_yahei | `Microsoft YaHei` | 无 | 0 | 0.121s | `-> MicrosoftYaHei, 0`（唯一命中，**无 fallback 行**） |
| **m03_smiley_typo** | **`Smiley Sans`** | 无 | **0** | 0.116s | `-> ArialMT, 0`；`Glyph 0x5F97 not found` → `MicrosoftYaHeiUI, 1`（**静默回退**） |
| **m04_smiley_fam** | **`Smiley Sans Oblique`** | 无 | 0 | 0.115s | `-> SmileySans-Oblique, 0`（**✓ 命中**） |
| **m05_zh** | **`得意黑`** | 无 | 0 | 0.112s | `-> SmileySans-Oblique, 0`（**✓ 命中**） |
| m06_ps | `SmileySans-Oblique` | 无 | 0 | 0.121s | `-> ArialMT` → `MicrosoftYaHeiUI`（PostScript 名**不作**族名，回退） |
| m07 | `Smiley Sans Oblique` | `assets/fonts` | 0 | 0.111s | `-> SmileySans-Oblique, 0` |
| m08 | `得意黑` | `assets/fonts` | 0 | 0.116s | `-> SmileySans-Oblique, 0` |
| **m09_bogus** | **`NoSuchFontXYZ123`** | 无 | **0** | 0.121s | `-> ArialMT` → `MicrosoftYaHeiUI`（**不存在的族名也不报错**） |

每个成功用例的 stderr 都有一行决定性证据：

```
[Parsed_subtitles_0 @ ...] Using font provider directwrite (with GDI)
```

→ **Windows 上的 libass 不用 fontconfig，用 DirectWrite+GDI** 的系统字体枚举。`--enable-fontconfig` 只对 drawtext 的 freetype 路径生效。**这解释了为什么 WSL 上 fc-match 判「有」的族名，Windows 上会静默失效。**

### 2.3 PNG 目视结论（`read_image` 亲眼看，720x1280）

- **v1_SmileySans.png**（`FontName=Smiley Sans`）：字形是**微软雅黑**——横细竖粗、笔画末端有小三角喇叭口、「得」的双人旁是标准雅黑形态、「黑」的四点是独立小点。**不是得意黑。**
- **v2_SmileySansOblique.png**（`FontName=Smiley Sans Oblique`）：**得意黑**——整体明显**右倾（oblique 斜体）**、笔画几何化（等粗直线）、字形窄长、「得」的双人旁是两条平行等粗直线、「黑」的四点被拉成横线。
- **v3_dedhei.png**（`得意黑`）：与 v2 **像素级同图**。
- **v4_NotoSansCJKSC.png** / **v6_bogus.png**：与 v1 **像素级同图**。
- **v5_MicrosoftYaHei.png**：雅黑，与 v1 同族但字重/微调有差异（md5 不同）。
- **v7_fontsdir_oblique.png**：得意黑（与 v2 极近，md5 因字体索引不同而略异）。

**md5 硬证据（「静默回退」的决定性证明）**：

```
143dd4d9f78bbbb4480db93b657b6652  v1_SmileySans.png        ← FontName="Smiley Sans"
4e5bcc64adc7e47f83064b4b0ff48b8d  v2_SmileySansOblique.png ← 得意黑
4e5bcc64adc7e47f83064b4b0ff48b8d  v3_dedhei.png            ← 与 v2 同
143dd4d9f78bbbb4480db93b657b6652  v4_NotoSansCJKSC.png     ← 与 v1 同
7ef09a3920b4c77131214ef3fa63dfb0  v5_MicrosoftYaHei.png
143dd4d9f78bbbb4480db93b657b6652  v6_bogus.png             ← 与 v1 同
2e1f7d2cc389544e2ab0856381c508c5  v7_fontsdir_oblique.png
```

即 **`Smiley Sans` / `Noto Sans CJK SC` / `NoSuchFontXYZ123` 三者输出字节完全一致** = 全部落到同一个回退字体。**「静默回退」不是推测，是 md5 相等。**

### 2.4 为什么必然回退：字体 name 表

手工解析 TTF 的 name 表（`assets/fonts/SmileySans-Oblique.ttf` 与用户目录 `SmileySans-Oblique-2.ttf` **完全相同**）：

| nameID | 含义 | 值 |
|---|---|---|
| 1 | **family（Windows 族名）** | `Smiley Sans Oblique`, `得意黑` |
| 2 | subfamily | `Regular` |
| 4 | full name | `Smiley Sans Oblique`, `得意黑 斜体` |
| 6 | PostScript name | `SmileySans-Oblique` |
| **16** | **typographic family** | **`Smiley Sans`**, `得意黑` |
| 17 | typographic subfamily | `Oblique`, `斜体` |

**`Smiley Sans` 只是 nameID 16 typographic family，不是 nameID 1 族名。** DirectWrite 只认 nameID 1，所以 `FontName=Smiley Sans` **必然回退**。可用族名只有 **`Smiley Sans Oblique`** 或 **`得意黑`**。

### 2.5 fontsdir 的真实作用（隔离 fixtures）

建了两个隔离目录：`fd_only_smiley/`（只放 `SmileySans-Oblique.ttf`）与 `fd_noto/`（从 WSL 拷出 `NotoSansCJK-Regular.ttc`，19484784 字节）。

| tag | 请求族名 | fontsdir | fontselect 结果 | 结论 |
|---|---|---|---|---|
| x1 | `Noto Sans CJK SC` | 无 | `ArialMT` → `MicrosoftYaHeiUI` | 系统没装 Noto CJK，回退 |
| **x2** | `Noto Sans CJK SC` | `fd_noto` | `Loading font file 'fd_noto\\NotoSansCJK-Regular.ttc'` → `NotoSansCJKsc-Regular, 2` | **fontsdir 能让未安装字体生效** |
| **x3** | `Smiley Sans` | `fd_only_smiley` | 文件已加载，但 fontselect → `ArialMT` → `MicrosoftYaHeiUI` | **fontsdir 不改变名字解析规则**：族名不对照样回退，尽管字体已在内存 |
| x4 | `Smiley Sans Oblique` | `fd_noto` | `-> SmileySans-Oblique, 0` | 系统已装的优先，fontsdir 只是补充 |

`fontselect` 的第三个数字是字体索引：**0 = 系统字体 provider，2 = 内存/fontsdir 字体**。
注：`fontsdir` 会把目录里**所有文件**当字体加载，`assets/fonts/README.md` 会触发 `Error opening memory font 'README.md'`（非致命，rc 仍 0）。

### 2.6 生产参数下的复验（Alignment=2, MarginV=60, FontSize=20, Outline=3, Shadow=1）

| tag | FontName | fontsdir | rc | 耗时 | md5 | fontselect 选中 |
|---|---|---|---|---|---|---|
| prod_smiley | `Smiley Sans` | 无 | 0 | 0.127s | c4e658636ebe | `MicrosoftYaHeiUI, 1`（**回退**） |
| **prod_smiley_obl** | `Smiley Sans Oblique` | 无 | 0 | 0.116s | d563dd0b6336 | `SmileySans-Oblique, 0`（**✓**） |
| prod_smiley_fd | `Smiley Sans Oblique` | `assets/fonts` | 0 | 0.120s | 305c145772fb | `SmileySans-Oblique, 0`（**✓**） |

PNG 目视（720x1280，两行字幕居中偏下）：`prod_smiley` 是**微软雅黑**（字面方正、笔画粗细对比明显、「测」的竖钩末端有小三角）；`prod_smiley_obl` / `_fd` 是**得意黑**（整行右倾、笔画等粗、「试」的言字旁是几何直线）。**`_obl` 与 `_fd` 肉眼几乎无差**，说明系统已装时 fontsdir 纯属冗余。

### 2.7 整段烧录耗时（3 秒 720x1280，`-c:v libx264 -preset fast -crf 20`，含字幕滤镜）

| tag | FontName | rc | 耗时 | 输出大小 |
|---|---|---|---|---|
| full_obl | `Smiley Sans Oblique` | 0 | **0.300s** | 22989 字节 |
| full_typo | `Smiley Sans` | 0 | **0.303s** | 22284 字节 |

→ **回退与命中在耗时上无差别**（0.300 vs 0.303s），**性能不能作为判据**，只能靠画面或日志。

## 3. AC-A 补充：Windows 上真正的回退族名

| FontName | fontselect 选中 | rc | 耗时 | md5 | 说明 |
|---|---|---|---|---|---|
| **`Noto Sans SC`** | `Noto-Sans-SC, 0` | 0 | 0.111s | d30d7a796cd7 | **✓ 系统已装** |
| `Noto Sans CJK SC` | `MicrosoftYaHeiUI, 1` | 0 | 0.112s | c4e658636ebe | **回退**——Windows 没装 CJK 变体 |
| `Microsoft YaHei` | `MicrosoftYaHei, 0` | 0 | 0.106s | 386aaa7e581c | ✓ |
| `SimHei` | `SimHei, 0` | 0 | 0.121s | 2521f83a284e | ✓ |

**当前脚本的 `SUBTITLE_FONT_FALLBACK = "Noto Sans CJK SC"` 在 Windows 上不存在**，会静默回退到雅黑 UI。Windows 侧应改用 **`Noto Sans SC`**（与 WSL 的 Noto CJK 视觉最接近）或 **`Microsoft YaHei`**。

Windows 已装族名全枚举（PowerShell `System.Drawing.Text.InstalledFontCollection`）：**TOTAL=370 个族**。相关：`Microsoft YaHei UI` / `Microsoft YaHei UI Light` / `Noto Sans` / `Noto Sans SC`（+Black/DemiLight/Light/Medium/Thin）/ `Noto Sans JP`（+6 变体）/ `Noto Sans KR`。**没有 `Noto Sans CJK SC`，没有 `Smiley Sans` 任何形式。**

## 4. AC-C：替代 `fc-match` 的三条预检路线

### 候选 1：字体文件存在性（推荐主方案）

```python
os.path.isfile(os.path.join(HERE, "..", "assets", "fonts", "SmileySans-Oblique.ttf"))
```

实测：`True`，**0.0989 ms**，size=2629764。仓库 `assets/fonts/` 内容 = `['README.md', 'SmileySans-Oblique.ttf']`。

- **优点**：零依赖、零外部进程、0.1 ms、跨宿主（Windows/WSL 同一份代码）、资产是 skill 自带的。
- **缺点**：只证明「文件在」，不证明「系统能按族名解析到它」——但见下：**这个缺点可以用「同时走 fontsdir」消掉**（§2.5 x2 证明 fontsdir 能让未安装字体生效）。

### 候选 2a：注册表枚举（不推荐）

`winreg` 全枚举 `HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts` + HKLM 同名键，按**值（路径）**匹配 `smiley`：

- 命中 **1 条**，耗时 **2.70 ms**：
  `('HKCU', '得意黑 斜体 (TrueType)', 'C:\\Users\\LMR\\AppData\\Local\\Microsoft\\Windows\\Fonts\\SmileySans-Oblique-2.ttf')`
- **致命坑**：值名是中文「得意黑 斜体 (TrueType)」。按值名 grep `smiley` **匹配不到**（PowerShell `-match 'Smiley|得意'` 也失败）；必须按**值路径**匹配。
- 结论：**能work但脆**——依赖注册表结构、依赖用户是否装了字体（而 skill 自带字体不需要装）、值名编码不可靠。**降级备选，不作主判据。**

### 候选 2b：读字体 name 表（辅助校验）

`fontTools.ttLib.TTFont` 读 nameID 1：`['Smiley Sans Oblique', '得意黑']`，耗时 **47.28 ms**（anaconda 的 fontTools 4.55.3）。

- 用途：**校验族名常量与字体文件是否匹配**（防「改了文件没改常量」）。**不建议放进每次预检**（47 ms + 引入 fontTools 依赖）。

### 候选 3：ffmpeg 渲染探针（推荐降级方案）

渲 1 帧 64x64 黑底 + 1 秒 srt，读 `-v verbose` 的 `fontselect:` 行：

```bash
ffmpeg -y -v verbose -f lavfi -i color=c=black:s=64x64:d=0.2 \
  -vf "subtitles=filename='C\\:/.../probe.srt':force_style='Alignment=5,FontSize=24,FontName=<FAM>'" \
  -frames:v 1 out.png
# 判据：fontselect 只出现 1 次且选中名含 SmileySans → 真命中
#       出现 2 次（第一次 ArialMT，glyph 缺失后再补选）→ 回退
```

实测（gpl 构建）：

| 请求族名 | rc | 耗时 | fontselect 次数 | 选中 | 判定 |
|---|---|---|---|---|---|
| `Smiley Sans` | 0 | 0.069s | 2 | `MicrosoftYaHeiUI, 1` | **REJECT** |
| `Smiley Sans Oblique` | 0 | **0.052s** | 1 | `SmileySans-Oblique, 0` | **PASS** |
| `得意黑` | 0 | 0.054s | 1 | `SmileySans-Oblique, 0` | **PASS** |
| `Noto Sans CJK SC` | 0 | 0.057s | 2 | `MicrosoftYaHeiUI, 1` | **REJECT** |
| `NoSuchFontXYZ123` | 0 | 0.055s | 2 | `MicrosoftYaHeiUI, 1` | **REJECT** |

anaconda 4.3.1 对照：rc=0、0.130s、`('SmileySans-Oblique','0')`——**判据跨版本通用**。

- **优点**：**直接测「系统能不能解析这个族名」**，与生产路径完全同构（同一个 libass、同一个 DirectWrite）。
- **缺点**：0.05 s + 一个子进程 + 需要临时 srt + **依赖 verbose 日志格式**（跨 ffmpeg 版本可能变）；且 gpl 构建上 `subtitles` 滤镜在，探针可用（若某构建连 subtitles 都没有，探针也会假阴性）。

### 候选 4（意外发现）：anaconda 自带的 `fc-match.exe`（**不可直接照搬**）

`C:\\Users\\LMR\\anaconda3\\Library\\bin\\fc-match.exe` / `fc-list.exe` **存在且可用**，而且**会扫用户字体目录**：

```
fc-list | grep -i smiley
→ C:/Users/LMR/AppData/Local/Microsoft/Windows/Fonts\SmileySans-Oblique-2.ttf: Smiley Sans,得意黑,Smiley Sans Oblique:style=Oblique,斜体,Regular

fc-match 'Smiley Sans'
→ SmileySans-Oblique-2.ttf: "Smiley Sans" "Oblique"      rc=0, 115 ms
```

`fc-list` 共 **893** 行，其中 **59** 条来自 `Local/Microsoft/Windows/Fonts`（用户字体目录被扫到）。

**但**：`fc-match -f '%{family}'` 格式串在这版上**输出空串**——`Smiley Sans` / `Smiley Sans Oblique` / `得意黑` / `Noto Sans CJK SC` / `Microsoft YaHei` **全部返回空串、rc=0**，耗时 91–115 ms。`fc-match --version` 也不正常（输出 `NotoSans-Regular.ttf: "Noto Sans" "Regular"`）——这是 MSYS2 移植的裁剪版。

→ **脚本现有的 `fc-match -f '%{family}'` 写法在 Windows 上会恒返回空串，照搬会「恒判失败」。** 且它只在装了 anaconda 的机器上存在，**不能作为 skill 的依赖**。

### 推荐方案与降级方案

**主方案（推荐）**：

1. **把 `SUBTITLE_FONT_NAME` 的默认值从 `"Smiley Sans"` 改为 `"Smiley Sans Oblique"`**（§2.2 m04、§2.6 prod_smiley_obl 实测命中；ASCII 无编码风险）。
2. **预检 = 文件存在性**：`os.path.isfile(assets/fonts/SmileySans-Oblique.ttf)`，0.099 ms。缺失 → **fail-fast**（见下）。
3. **s10 的 `subtitles` 同时传 `fontsdir=<仓库 assets/fonts 的绝对路径>`**：这样即使系统没装该字体，libass 也能从内存加载（§2.5 x2 证明可行）。注意 `fontsdir` 会尝试加载目录下所有文件，**建议把 `README.md` 移出 `assets/fonts/` 或单独建 `assets/fonts/ttf/`**，避免每次 `Error opening memory font`。
4. **预检用族名做二次确认**：用候选 2b（读 nameID 1）在**开发期**校验常量与文件匹配，不进每次运行。

**降级方案（当「文件在」但「系统解析不到」需要区分时）**：

- **ffmpeg 渲染探针**（候选 3，0.052 s）：仅当出现「字幕字体明显不对」的现场告警时才跑，不放进常规预检。
- 判定用 `fontselect` 行数 + 选中名，**不要用耗时**（§2.7 证明命中与回退耗时无差别）。

**明确不推荐**：

- **注册表枚举**（值名中文编码不可靠，且依赖「用户装了字体」而 skill 自带字体不需要装）。
- **照搬 `fc-match`**（Windows 上格式串失效；且只在装 anaconda 的机器有）。

### 失败时 fail-fast 还是回退？

**fail-fast，不回退。** 依据 CONTEXT.md：

- 「**必需资产**」：缺失即被环境预检拦截、**永不静默回退**的 skill 自带静态资产。字体与翻页音效同类——`assets/fonts/SmileySans-Oblique.ttf` 是仓库自带的，不是环境依赖。
- 「**保守放行（已废除）**」：旧逻辑「判读失败即放行」已废除，改为重试后记录报告并转人审。**「判不到字体就静默用 Noto」正是同一类保守放行**，应一并废除。
- 「**环境预检（preflight）**」：driver 启动即执行的环境护栏，**fail-fast 叫人**。字体预检应挂在 preflight 里，而不是 s10 里「回退 + log 告警」——s10 已经在烧录，此时回退等于**用错字体出片**。

**具体口径建议**：

| 情形 | 处置 |
|---|---|
| `assets/fonts/SmileySans-Oblique.ttf` 不存在 | **preflight fail-fast**（必需资产缺失） |
| 文件在，但系统族名解析不到 | **不 fail-fast**——改走 `fontsdir` 从内存加载（§2.5 x2 已证明可行），**这是修复不是回退** |
| `fontsdir` 也失败（极端） | **fail-fast 叫人**，不静默换 Noto |

即：**「文件缺失」和「系统没装」是两个不同问题**。前者 fail-fast，后者用 fontsdir 消解——**都不该退化成「悄悄用 Noto」**。

## 5. AC-D：Windows 上最稳的族名

WSL 侧 `fc-match -f '%{family}|%{file}'` 的实测（对照）：

```
[Smiley Sans]          → 得意黑,Smiley Sans,Smiley Sans Oblique|/home/lmr/.fonts/SmileySans-Oblique.ttf
[Smiley Sans Oblique]  → 得意黑,Smiley Sans,Smiley Sans Oblique|/home/lmr/.fonts/SmileySans-Oblique.ttf
[得意黑]                → 得意黑,Smiley Sans,Smiley Sans Oblique|/home/lmr/.fonts/SmileySans-Oblique.ttf
[Noto Sans CJK SC]     → Noto Sans CJK SC|/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
```

**WSL 上三个别名全都能解析到同一文件**（fontconfig 认 nameID 16 typographic family）。**Windows 的 DirectWrite 只认 nameID 1**，所以只有 `Smiley Sans Oblique` 与 `得意黑` 可用。**这就是迁移后族名必须换的根本原因。**

**推荐值：`Smiley Sans Oblique`**

| 候选 | Windows 命中 | 理由 |
|---|---|---|
| **`Smiley Sans Oblique`** | ✓（m04 / prod_smiley_obl） | **推荐**：纯 ASCII，不受控制台/编码影响；是 nameID 1 |
| `得意黑` | ✓（m05） | 同样命中，但中文族名在 GBK 控制台日志里显示为「寰楁剰榛」乱码（实测），排障时容易误判 |
| `Smiley Sans` | ✗ 静默回退 | 只是 nameID 16 |
| `SmileySans-Oblique` | ✗ 静默回退 | PostScript 名，不作族名 |

**注意**：该字体是 **Oblique（斜体）**设计——「Smiley Sans Oblique」这个名字里带 Oblique 是**字体本身的设计**，不是让 libass 再倾斜一次。实测画面（v2/v3）确认没有双重倾斜。

**回退族名也要改**：`SUBTITLE_FONT_FALLBACK` 从 `"Noto Sans CJK SC"` → `"Noto Sans SC"`（§3 实测，Windows 只装了后者）。

## 6. 未知项（诚实清单）

- **drawtext 缺失的根因未查证**：只确认了「configuration 开了 freetype/fontconfig 但滤镜没编进去」。没有去查 BtbN 的构建脚本，也没测其他 Windows ffmpeg 发行版（本机只有这一个 gpl 构建 + anaconda 4.3.1）。**换哪个 ffmpeg 发行版能同时有 drawtext + libass + 支持 5080 的编码器，需要后续票实测。**
- **anaconda 4.3.1 的 drawtext 用于生产是否可接受未评估**：它太旧（2020 年），libx264/libx265 版本、`-c:a copy` 兼容性、4K 性能都没测。**本票只用它验证转义规则，不建议直接切生产。**
- **fontsdir 与 `README.md` 的冲突未定方案**：只确认会打 `Error opening memory font`（非致命）。是移走 README 还是改目录结构，留给实现票。
- **`得意黑` 在非中文 locale 的 Windows 上是否还能解析未测**：本机是中文 locale。理论上 DirectWrite 按 nameID 1 的 Unicode 串匹配，与 locale 无关，但没实测。
- **多字体变体（Bold/Oblique）的族名+subfamily 组合未测**：本票只有 Oblique 一个变体。若后续要加字重，`force_style` 里的 `Bold=1` 与族名如何组合需要另测。
- **用户字体目录与 `assets/fonts` 两份文件版本不一致未评估**：用户目录的是 2098640 字节、仓库的是 2629764 字节（name 表相同但体积差 530KB）。渲染出来是否完全一致未逐像素比对（v2 用系统字体、v7 用 fontsdir 内存字体，md5 不同但肉眼无差）。**建议统一用仓库那份 + fontsdir，不要依赖用户装字体。**

## 7. 对后续票的参数直引

| 票 | 直接可用的参数 |
|---|---|
| **s10 烧录迁移** | `subtitles=filename='C\\:/.../x.srt':fontsdir='C\\:/.../assets/fonts':force_style='...FontName=Smiley Sans Oblique...'`（**必须 `filename=` 命名参数 + 单引号 + 正斜杠 + 冒号一层转义**） |
| **s10 标题（drawtext）** | ⚠️ **先解决 ffmpeg 构建问题**：`C:\\ffmpeg-master-latest-win64-gpl` 无 drawtext。有 drawtext 时写法：`drawtext=fontfile='C\\:/.../SmileySans-Oblique.ttf':...`（单引号 + 正斜杠 + 一层冒号转义）；或改相对路径 |
| **字体常量** | `SUBTITLE_FONT_NAME = "Smiley Sans Oblique"`（原 `"Smiley Sans"` **Windows 上必然静默回退**） |
| **回退常量** | `SUBTITLE_FONT_FALLBACK = "Noto Sans SC"`（原 `"Noto Sans CJK SC"` **Windows 上不存在**） |
| **预检（AC3 交付）** | `os.path.isfile(<repo>/assets/fonts/SmileySans-Oblique.ttf)`，0.099 ms；缺失 → **preflight fail-fast** |
| **预检降级** | ffmpeg 探针：`-v verbose` 读 `fontselect:`，**1 次且含 SmileySans = 命中；2 次 = 回退**；耗时 0.052 s；**勿用耗时作判据** |
| **不要用** | `fc-match`（Windows 上 `-f '%{family}'` 恒返回空串）；注册表值名（中文编码不可靠） |
| **测试素材** | `%TEMP%\\fontspike\\`（base.mp4 / test.srt / fd_only_smiley / fd_noto / 各 PNG）——**仓库不留** |
