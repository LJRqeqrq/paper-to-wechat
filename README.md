# paper-to-wechat

一键将学术论文 PDF 转换为微信公众号推文，自动完成解析、配图、封面生成、草稿发布全流程。

## 能做什么

- 上传一篇论文 PDF，自动生成带封面、配图、中文排版的公众号推文
- 论文图表自动提取（含英文标题），表格截取为图片，插入推文合适位置
- 中文自动翻译 & 公众号风格编排
- 一键发布到微信公众号草稿箱

## 环境要求

- **Python** >= 3.10
- **Node.js** >= 18
- **Claude Code**（CLI 或 IDE 插件）
- **微信公众号**（已认证的订阅号或服务号）

## 快速开始

### 1. 安装 Python 依赖

```bash
cd mcp/pdf-parser-mcp
pip install -r requirements.txt
```

### 2. 安装 Node.js 依赖

```bash
# screenshot MCP server
cd mcp/screenshot-mcp
npm install
npm run build

# wenyan MCP server
cd ../wenyan-mcp
npm install
npm run build
```

### 3. 配置微信密钥

在项目根目录创建 `.env` 文件：

```bash
WECHAT_APP_ID=你的公众号AppID
WECHAT_APP_SECRET=你的公众号AppSecret
```

### 4. 修改 MCP 路径

编辑 `.mcp.json`，将三个 MCP server 的绝对路径改为你本机的路径：

- `pdf-parser`：Python 可执行文件路径 + `server.py` 路径
- `screenshot`：`node` 命令 + `mcp/screenshot-mcp/dist/index.js` 路径
- `wenyan`：`node` 命令 + `mcp/wenyan-mcp/dist/index.js` 路径

示例（Windows）：
```json
{
  "mcpServers": {
    "pdf-parser": {
      "command": "C:\\Users\\你的用户名\\AppData\\Local\\Programs\\Python\\python.exe",
      "args": ["D:\\projects\\paper-to-wechat\\mcp\\pdf-parser-mcp\\server.py"]
    },
    "screenshot": {
      "command": "node",
      "args": ["D:\\projects\\paper-to-wechat\\mcp\\screenshot-mcp\\dist\\index.js"]
    },
    "wenyan": {
      "command": "node",
      "args": ["D:\\projects\\paper-to-wechat\\mcp\\wenyan-mcp\\dist\\index.js"]
    }
  }
}
```

### 5. 使用

打开 Claude Code，输入：

```
将这篇论文转成微信公众号推文：https://arxiv.org/pdf/1706.03762.pdf
```

或本地 PDF：

```
将 C:\papers\attention-is-all-you-need.pdf 转成微信公众号推文
```

系统会自动完成：
1. PDF 解析 → 提取全文 + 图表 + 表格
2. 封面生成 → 900×500 公众号大图封面
3. 文章编排 → 中文翻译 + 公众号风格排版
4. 草稿发布 → 推送到微信草稿箱

产物保存在 `blog/<论文名>/` 下，可在微信公众平台后台草稿箱查看和发布。

## 项目结构

```
paper-to-wechat/
├── .claude/
│   ├── agents/                          # Agent 定义（主编排 + 4 个子 Agent）
│   └── settings.local.json              # 本机权限配置（不上传 Git）
├── .env                                 # 微信密钥
├── .mcp.json                            # MCP 服务器注册
├── CLAUDE.md                            # Claude Code 项目指令
├── README.md
├── mcp/
│   ├── pdf-parser-mcp/                  # PDF 解析 MCP Server (Python)
│   │   ├── server.py
│   │   ├── requirements.txt
│   │   └── pyproject.toml
│   ├── screenshot-mcp/                  # HTML 截图 MCP Server (Node.js)
│   │   ├── src/index.ts
│   │   ├── dist/index.js
│   │   └── package.json
│   └── wenyan-mcp/                      # 微信发布 MCP Server (Node.js)
│       ├── src/index.ts
│       ├── dist/index.js
│       └── package.json
├── scripts/
│   ├── generate_cover_html.py           # 封面 HTML 生成
│   └── img_preprocess.py               # 图片预处理（WebP 转换 + 压缩）
├── templates/
│   └── cover_template.html             # 封面 HTML 模板
└── blog/                               # 推文产物（每篇论文一个子目录）
    └── <论文名>/
        ├── article.md                   # 最终推文
        ├── content.md                   # 全文 Markdown
        ├── metadata.json                # 论文元数据
        ├── cover.png                    # 封面截图
        ├── figures/                     # 图表（含英文标题渲染）
        └── tables/                      # 表格截图
```

## 常见问题

### 图片在草稿箱中不显示

检查论文目录名是否包含**空格**（如 `Explainable hybrid tabular ...`）。marked.js 不支持空格路径，系统已通过尖括号包裹自动修复。如仍有问题，避免在 `blog/` 下使用含空格的目录名。

### 发布失败：错误码 45166

推文中存在 Markdown 锚点链接 `[text](#anchor)`，发布前会自动检查并移除。

### 发布失败：错误码 40113

图片格式异常（常见 WebP 伪装成 PNG），`img_preprocess.py` 会自动检测并转换。

### 图片超过 1MB 限制

`img_preprocess.py` 会自动压缩到 1MB 以下。

### MCP Server 连接失败

确认 Python/Node.js 路径正确，依赖已安装，`.mcp.json` 中路径为绝对路径。

## License

MIT
