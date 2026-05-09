# paper-to-wechat 项目说明

## 项目概述

本项目是一个 AI Agent 系统，能够自动将学术论文 PDF 转换为微信公众号推文，包括：
- 全文解析（文字、图表、公式）
- 图表提取（含英文标题渲染在图片中）+ 表格截取为图片
- 封面自动生成
- 配图自动上传微信图片库
- 自动发布到微信公众号草稿箱

## 工作流（每一步都必须执行，不可跳过）

### 步骤 1：PDF 解析（pdf-content-extractor agent）
- 调用 `pdf-parser-mcp` 解析 PDF
- 提取全文 Markdown → `content.md`
- 提取图表（含英文标题渲染） → `figures/` + `figures_manifest.json`
- 提取表格（截取为图片） → `tables/` + `tables_manifest.json`
- 提取元数据 → `metadata.json`
- **翻译所有 caption_cn 为中文**
- 运行 `img_preprocess.py` 对 `figures/` 和 `tables/` 图片预处理
- ✅ 验证：确认 figures/ 和 tables/ 都有产出物

### 步骤 2：封面生成（cover-image-generator agent）
- 根据 `metadata.json` 生成封面 HTML
- 调用 `screenshot-mcp` 截图 → `cover.png`（900×500）
- ✅ 验证：cover.png 存在且 < 1MB

### 步骤 3：文章编排（wechat-article-composer agent）
- **必须输出中文**（翻译英文论文内容）
- 根据 manifest 将图表和表格插入正文合适位置
- **图片/表格在上，caption 在下**：`![图X](path)` 后紧跟 `*▲ 图X：中文说明*`
- 所有图片路径使用**绝对路径**
- 生成带 frontmatter 的 `article.md`
- ✅ 验证：推文是中文、caption 格式为 `▲ 图X/▲ 表X`、caption 在图片下方、路径为绝对路径

### 步骤 4：草稿发布（wechat-draft-publisher agent）
- 发布前检查：图片格式/大小、无锚点链接、无 WebP
- 调用 `wenyan-mcp publish_article`，使用 **`file_path`** 参数（非 `content`）
- 选择主题
- ✅ 验证：返回 media_id，无错误码

## 微信公众号发布注意事项

### 必需的 Frontmatter

```markdown
---
title: 文章标题
cover: /绝对/路径/到/封面图.png
---
```

### 支持的格式

- **数学公式**：LaTeX 语法（`$...$` 和 `$$...$$`）
- **标准 Markdown**：标题、列表、粗体、斜体、引用等
- **图片**：相对或绝对路径（发布时自动上传到微信 CDN）
- **代码块**：` ```language `

### 不支持的格式

- **Markdown 锚点链接**：`[文本](#anchor)` 会导致发布失败（错误码 45166）
- **WebP 图片格式**：会导致发布失败（错误码 40113），已由 `img_preprocess.py` 自动处理
- **SVG 图片**：需要预先转换为 PNG

### 常见错误及解决方案

#### 图片过大（system error）
- 微信公众号 API 上传限制为 **1MB 以下**
- 已由 `img_preprocess.py` 自动压缩处理

#### 错误码 45166：内容验证失败
- 触发原因：Markdown 锚点链接 或 无效的微信公众号链接
- 解决方案：发布前自动检查并删除锚点链接

#### 错误码 40113：不支持的图片格式
- 触发原因：图片实际格式与扩展名不符（最常见 WebP 伪装成 PNG）
- 已由 `img_preprocess.py` 自动检测和转换

## 图片预处理要求

所有论文配图（figures/ + tables/）在发布前必须经过 `img_preprocess.py` 处理：
1. `file` 命令检测真实格式（WebP 伪装 → 转换为 PNG）
2. 压缩 > 1MB 的图片（resize 2000x2000 + quality 85%）
3. 确保扩展名与实际格式一致

## Caption 位置要求

**图片/表格在上，caption 在下。不可反过来！**

```markdown
<!-- 正确 -->
![图1](C:/Users/.../figures/figure_01.png)
*▲ 图1：中文说明*

<!-- 错误 -->
▲ 图1：中文说明
![图1](C:/Users/.../figures/figure_01.png)
```

## 图片路径要求

**article.md 中所有图片必须使用绝对路径**，且含空格的路径必须用尖括号包裹（wenyan-mcp 已自动处理）：

```markdown
<!-- 正确 -->
![图1](C:/Users/.../blog/<论文名>/figures/figure_01.png)

<!-- 错误 -->
![图1](./figures/figure_01.png)
```

原因：wenyan-mcp 在接收 `content` 参数时无法确定图片基础目录，导致相对路径上传失败。

## 表格处理

pdf-parser-mcp 自动检测论文中的表格：
- 表格标题（"Table X ..."）被识别为表格区域标记
- 表格内容和标题一起被渲染为 PNG 图片，保存到 `tables/` 目录
- `tables/tables_manifest.json` 包含表格的页码、caption 和上下文信息
- 公众号编排时，根据页面位置将表格图片插入到正文合适位置

## MCP 服务器

| 服务器 | 位置 | 用途 |
|--------|------|------|
| pdf-parser-mcp | `mcp/pdf-parser-mcp/server.py` | PDF 解析 + 图表（含标题）+ 表格提取 |
| screenshot-mcp | `mcp/screenshot-mcp/dist/index.js` | HTML 截图（封面生成） |
| wenyan-mcp | `mcp/wenyan-mcp/dist/index.js` | 微信草稿发布（密钥从项目根目录 .env 加载） |
