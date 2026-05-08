#!/usr/bin/env node

/**
 * screenshot-mcp: HTML 截图 MCP Server
 *
 * 基于 Puppeteer，将本地 HTML 文件或远程 URL 渲染为 PNG 截图。
 * 主要用于生成微信公众号推文封面图（默认 900×500）。
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import puppeteer, { Browser, Page } from "puppeteer";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

// ── 常量 ──────────────────────────────────────────────

const DEFAULT_WIDTH = 900;
const DEFAULT_HEIGHT = 500;
const DEFAULT_DEVICE_SCALE_FACTOR = 2; // Retina 质量

// ── MCP Server ─────────────────────────────────────────

const server = new Server(
  {
    name: "screenshot-mcp",
    version: "0.1.0",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

// ── 浏览器管理 ─────────────────────────────────────────

let browser: Browser | null = null;

async function getBrowser(): Promise<Browser> {
  if (!browser || !browser.connected) {
    browser = await puppeteer.launch({
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
      ],
    });
  }
  return browser;
}

async function takeScreenshot(
  urlOrHtml: string,
  outputPath: string,
  width: number,
  height: number,
  isLocalFile: boolean
): Promise<string> {
  const browser = await getBrowser();
  const page: Page = await browser.newPage();

  try {
    await page.setViewport({
      width,
      height,
      deviceScaleFactor: DEFAULT_DEVICE_SCALE_FACTOR,
    });

    if (isLocalFile) {
      const fileUrl = path.isAbsolute(urlOrHtml)
        ? `file:///${urlOrHtml.replace(/\\/g, "/")}`
        : `file:///${path.resolve(urlOrHtml).replace(/\\/g, "/")}`;
      await page.goto(fileUrl, { waitUntil: "networkidle0", timeout: 30000 });
    } else {
      await page.goto(urlOrHtml, { waitUntil: "networkidle0", timeout: 30000 });
    }

    // 等待字体和资源加载完成
    await page.evaluate(() => {
      return document.fonts ? document.fonts.ready : Promise.resolve();
    });

    // 获取实际内容高度，裁剪截图
    const bodyHandle = await page.$("body");
    if (bodyHandle) {
      const boundingBox = await bodyHandle.boundingBox();
      if (boundingBox && boundingBox.height > 0) {
        height = Math.min(Math.ceil(boundingBox.height), height);
        await page.setViewport({
          width,
          height,
          deviceScaleFactor: DEFAULT_DEVICE_SCALE_FACTOR,
        });
      }
    }

    // 确保输出目录存在
    const outputDir = path.dirname(outputPath);
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    await page.screenshot({
      path: outputPath,
      type: "png",
      fullPage: false,
      clip: { x: 0, y: 0, width, height },
    });

    const stats = fs.statSync(outputPath);
    const sizeKB = (stats.size / 1024).toFixed(1);

    return `截图完成: ${outputPath} (${width}x${height}, ${sizeKB}KB)`;
  } finally {
    await page.close();
  }
}

// ── 工具列表 ───────────────────────────────────────────

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "screenshot_html",
        description:
          "将本地 HTML 文件渲染为 PNG 截图。用于生成微信公众号推文封面图。默认尺寸 900×500（微信大图封面），2x Retina 质量。",
        inputSchema: {
          type: "object",
          properties: {
            html_path: {
              type: "string",
              description: "本地 HTML 文件的绝对路径",
            },
            output_path: {
              type: "string",
              description: "输出 PNG 截图的绝对路径",
            },
            width: {
              type: "number",
              description: `截图宽度（像素），默认 ${DEFAULT_WIDTH}`,
              default: DEFAULT_WIDTH,
            },
            height: {
              type: "number",
              description: `截图高度（像素），默认 ${DEFAULT_HEIGHT}`,
              default: DEFAULT_HEIGHT,
            },
          },
          required: ["html_path", "output_path"],
        },
      },
      {
        name: "screenshot_url",
        description:
          "对远程 URL 进行截图。用于截取在线网页内容。",
        inputSchema: {
          type: "object",
          properties: {
            url: {
              type: "string",
              description: "要截图的远程 URL",
            },
            output_path: {
              type: "string",
              description: "输出 PNG 截图的绝对路径",
            },
            width: {
              type: "number",
              description: `截图宽度（像素），默认 ${DEFAULT_WIDTH}`,
              default: DEFAULT_WIDTH,
            },
            height: {
              type: "number",
              description: `截图高度（像素），默认 ${DEFAULT_HEIGHT}`,
              default: DEFAULT_HEIGHT,
            },
          },
          required: ["url", "output_path"],
        },
      },
    ],
  };
});

// ── 工具调用处理 ───────────────────────────────────────

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  if (name === "screenshot_html") {
    const htmlPath = args?.html_path as string;
    const outputPath = args?.output_path as string;
    const width = (args?.width as number) || DEFAULT_WIDTH;
    const height = (args?.height as number) || DEFAULT_HEIGHT;

    if (!htmlPath || !outputPath) {
      return {
        content: [
          {
            type: "text",
            text: "错误: html_path 和 output_path 参数均为必填项。",
          },
        ],
        isError: true,
      };
    }

    if (!fs.existsSync(htmlPath)) {
      return {
        content: [
          {
            type: "text",
            text: `错误: HTML 文件不存在: ${htmlPath}`,
          },
        ],
        isError: true,
      };
    }

    try {
      const result = await takeScreenshot(htmlPath, outputPath, width, height, true);
      return { content: [{ type: "text", text: result }] };
    } catch (error) {
      return {
        content: [
          {
            type: "text",
            text: `截图失败: ${error instanceof Error ? error.message : String(error)}`,
          },
        ],
        isError: true,
      };
    }

  } else if (name === "screenshot_url") {
    const url = args?.url as string;
    const outputPath = args?.output_path as string;
    const width = (args?.width as number) || DEFAULT_WIDTH;
    const height = (args?.height as number) || DEFAULT_HEIGHT;

    if (!url || !outputPath) {
      return {
        content: [
          {
            type: "text",
            text: "错误: url 和 output_path 参数均为必填项。",
          },
        ],
        isError: true,
      };
    }

    try {
      const result = await takeScreenshot(url, outputPath, width, height, false);
      return { content: [{ type: "text", text: result }] };
    } catch (error) {
      return {
        content: [
          {
            type: "text",
            text: `截图失败: ${error instanceof Error ? error.message : String(error)}`,
          },
        ],
        isError: true,
      };
    }
  }

  throw new Error(`未知工具: ${name}`);
});

// ── 启动入口 ───────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((error) => {
  console.error("截图 MCP 服务器启动失败:", error);
  process.exit(1);
});

// 优雅退出
process.on("SIGINT", async () => {
  if (browser) {
    await browser.close();
  }
  process.exit(0);
});

process.on("SIGTERM", async () => {
  if (browser) {
    await browser.close();
  }
  process.exit(0);
});
