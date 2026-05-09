#!/usr/bin/env python3
"""
pdf-parser-mcp: 学术论文 PDF 解析 MCP Server

功能：
- 全文 Markdown 转换（pymupdf4llm）
- 图表提取（内嵌图片 + 英文标题，渲染为含标题的完整图片）
- 表格提取（检测表格标题，截取表格区域为图片，输出到 tables/）
- 元数据提取（标题、作者、摘要、关键词）

依赖: PyMuPDF>=1.23.0, pymupdf4llm, httpx, Pillow, mcp
"""

import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from PIL import Image


# ── 常量 ──────────────────────────────────────────────

FIGURE_MIN_WIDTH = 200   # 图表最小宽度（像素），小于此值视为装饰元素
FIGURE_MIN_HEIGHT = 150  # 图表最小高度（像素）
MAX_CAPTION_DISTANCE = 250  # 图片与标题的最远距离（pt），超出视为不相关
RENDER_DPI = 2.0  # 渲染分辨率倍率（2x = 144 DPI 效果）


# ── MCP Server ─────────────────────────────────────────

server = Server("pdf-parser-mcp")


# ── 辅助函数 ────────────────────────────────────────────

async def download_pdf(url: str, save_path: str) -> str:
    """下载远程 PDF 到本地"""
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(resp.content)
    return save_path


def extract_metadata_with_fitz(doc: fitz.Document, file_path: str) -> dict:
    """从 PDF 中提取元数据"""
    meta = doc.metadata
    title = meta.get("title", "")
    author = meta.get("author", "")

    # 如果没有标准元数据，尝试从第一页文本提取
    if not title or not author:
        first_page_text = doc[0].get_text("text") if len(doc) > 0 else ""
        lines = [l.strip() for l in first_page_text.split("\n") if l.strip()]
        if not title and lines:
            title = lines[0][:200]
        if not author and len(lines) > 1:
            for line in lines[1:8]:
                if len(line) < 150 and re.search(r"[A-Z][a-z]+", line):
                    author = line.strip()
                    break

    # 提取摘要：在正文前几页搜索 "Abstract" 关键词
    abstract = ""
    for page_num in range(min(3, len(doc))):
        text = doc[page_num].get_text("text")
        match = re.search(
            r"(?:Abstract|ABSTRACT|abstract)[\s\n]+(.+?)(?:\n\s*(?:\d+\.?\s+)?(?:Introduction|INTRODUCTION|1\.))",
            text, re.DOTALL,
        )
        if match:
            abstract = re.sub(r"\s+", " ", match.group(1)).strip()[:1000]
            break

    return {
        "title": title.strip(),
        "authors": author.strip(),
        "abstract": abstract,
        "keywords": meta.get("keywords", ""),
        "year": meta.get("creationDate", "")[:4] if meta.get("creationDate") else "",
        "page_count": len(doc),
    }


# ── 图表与表格提取（核心逻辑）─────────────────────────────

def _caption_distance(img_bbox: fitz.Rect, cap_bbox: fitz.Rect) -> float:
    """
    计算图片与标题的"距离"。
    标题通常在图片下方（figure）或上方（table）。
    返回越小表示越近。
    """
    # 标题在图片下方
    if cap_bbox.y0 >= img_bbox.y1:
        vertical_gap = cap_bbox.y0 - img_bbox.y1
    # 标题在图片上方
    elif img_bbox.y0 >= cap_bbox.y1:
        vertical_gap = img_bbox.y0 - cap_bbox.y1
    # 重叠
    else:
        vertical_gap = 0

    # 水平对齐度（中心点差异）
    img_center_x = (img_bbox.x0 + img_bbox.x1) / 2
    cap_center_x = (cap_bbox.x0 + cap_bbox.x1) / 2
    horizontal_mismatch = abs(img_center_x - cap_center_x)

    # 综合距离：垂直距离 + 水平偏移惩罚
    return vertical_gap + horizontal_mismatch * 0.5


def _find_nearest_caption(
    img_bbox: fitz.Rect,
    captions: list[tuple],
    used_ids: set,
) -> tuple | None:
    """在候选标题列表中找到离图片最近的标题"""
    best = None
    best_dist = float("inf")
    for cap_block, cap_num, cap_text in captions:
        if id(cap_block) in used_ids:
            continue
        cap_bbox = fitz.Rect(cap_block[:4])
        dist = _caption_distance(img_bbox, cap_bbox)
        if dist < best_dist and dist < MAX_CAPTION_DISTANCE:
            best_dist = dist
            best = (cap_block, cap_num, cap_text)
    return best


def _is_valid_table_caption(block: tuple, text: str) -> bool:
    """
    判断文本块是否为真正的表格标题（而非正文中的交叉引用）。

    真正的表格标题特征：以"Table X"开头且首行为简短标题。
    正文引用特征：以"Table X presents/shows..."开头的叙事性内容。

    注意：有些 PDF 中表格标题和内容被合并为一个文本块，
    此时以首行判断为准，不因整体高度大而拒绝。
    """
    bbox = fitz.Rect(block[:4])
    height = bbox.y1 - bbox.y0

    # 检测正文引用特征词（作为动词使用，后接宾语）
    body_ref_patterns = [
        r'\bpresents?\s',
        r'\bshows?\s',
        r'\bsummarizes?\s',
        r'\bcompares?\s',
        r'\billustrates?\s',
        r'\bdemonstrates?\s',
        r'\bprovides?\s',
        r'\bhighlights?\s',
        r'\bdetails?\s',
        r'\bevaluates?\s',
        r'\bconducts?\s',
    ]

    # 提取首行（表格标题行）
    first_line = text.split("\n")[0].strip()

    # 首行必须是简短的表格标题（"Table X ..."）
    if len(first_line) > 150:
        return False

    # 高文本块（表格内容合并在内）：检查首行不含叙事动词
    if height > 30:
        for pat in body_ref_patterns:
            if re.search(pat, first_line, re.IGNORECASE):
                return False
        return True

    # 矮文本块（仅标题行）：用整体文本检查
    if len(text) > 150:
        return False

    for pat in body_ref_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return False

    return True


def _is_table_content_block(block: tuple) -> bool:
    """
    判断文本块是否看起来像表格内容（而非正文段落）。

    表格行特征：短文本、含数字或缩写、结构化布局、高缩写密度。
    正文特征：长叙事句、流畅段落、完整句子结构。
    """
    text = (block[4] or "").strip()
    if not text:
        return False

    bbox = fitz.Rect(block[:4])
    height = bbox.y1 - bbox.y0

    # 页面页眉/页脚（高度极小）
    if height < 8:
        return False

    # 短行 → 很可能是表格行
    if len(text) < 100:
        return True

    # 含多个数字 → 很可能是表格数据行
    if len(re.findall(r'\d+', text)) >= 3:
        return True

    # 高缩写密度 → 可能是缩写列表、术语表等表格内容
    # 检测大量全大写词（缩写如 PHQ、AUC、TVAE）
    uppercase_abbrs = re.findall(r'\b[A-Z]{2,}\b', text)
    if len(uppercase_abbrs) >= 3:
        return True

    # 检测列对齐模式（多个短片段被连续空格分隔 → 表格行）
    # 例如: "SVM, Decision Trees, Neural Network     Limited to a single dataset"
    if re.search(r'\S\s{4,}\S', text):
        return True

    # 长段落 + 以小写字母开头 → 可能是跨页续文（不是独立表格内容）
    if len(text) > 150 and re.match(r'^[a-z]', text):
        return False

    # 长段落 → 检查是否叙事性文本
    if len(text) > 200:
        # 检测完整句子结构 (主语+谓语)
        if re.search(r'\b(?:is|are|was|were|has|have|can|may|will|should|would)\b', text):
            return False  # 含助动词 → 叙事句
        if re.search(r'[.!?]\s+[A-Z]', text):
            return False  # 多句 → 正文段落

    words = text.split()
    if len(words) > 25:
        return False  # 非常长 → 很可能是正文

    return True


def _find_table_content_blocks(
    cap_bbox: fitz.Rect,
    text_blocks: list,
    max_distance: float = 150,
    max_blocks: int = 20,
) -> list:
    """
    智能查找表格标题下方的表格内容块，遇到正文段落即停止。

    步骤：
    1. 收集所有候选块（水平重叠 + 垂直在 max_distance 内）
    2. 按 y 坐标排序（确保双栏布局中表格行在正文之前被处理）
    3. 逐个检查，遇到正文块即停止
    """
    # 收集候选块
    candidates = []
    for block in text_blocks:
        b_bbox = fitz.Rect(block[:4])

        # 必须在标题下方且在最大距离内
        if b_bbox.y0 < cap_bbox.y1:
            continue
        if b_bbox.y0 - cap_bbox.y1 > max_distance:
            continue

        # 必须有水平重叠
        overlap_x = min(b_bbox.x1, cap_bbox.x1) - max(b_bbox.x0, cap_bbox.x0)
        if overlap_x <= 0:
            continue

        candidates.append(block)

    # 按 y 坐标排序，确保离标题最近的块先被处理
    candidates.sort(key=lambda b: b[1])  # b[1] = y0

    # 逐个检查，遇到正文即停止
    below = []
    for block in candidates:
        if _is_table_content_block(block):
            below.append(block)
        else:
            break  # 正文段落，停止收集

        if len(below) >= max_blocks:
            break

    return below


def _render_page_area(
    page: fitz.Page,
    clip_rect: fitz.Rect,
    output_path: Path,
    padding: float = 6.0,
) -> None:
    """渲染页面的指定区域为 PNG 图片"""
    # 添加 padding
    clip = fitz.Rect(
        clip_rect.x0 - padding,
        clip_rect.y0 - padding,
        clip_rect.x1 + padding,
        clip_rect.y1 + padding,
    )
    # 确保不超出页面
    clip.x0 = max(0, clip.x0)
    clip.y0 = max(0, clip.y0)
    clip.x1 = min(page.rect.x1, clip.x1)
    clip.y1 = min(page.rect.y1, clip.y1)

    mat = fitz.Matrix(RENDER_DPI, RENDER_DPI)
    pix = page.get_pixmap(matrix=mat, clip=clip)
    pix.save(str(output_path))


def extract_figures_and_tables_from_pdf(
    doc: fitz.Document,
    output_dir: str,
) -> tuple[list[dict], list[dict]]:
    """
    从 PDF 中提取图表和表格。

    图表（figures/）：内嵌图片 + 英文标题，渲染为含标题的完整图片。
    表格（tables/）：检测表格标题，截取表格区域为图片。

    返回 (figures_manifest, tables_manifest)
    """
    out = Path(output_dir)
    figures_dir = out / "figures"
    tables_dir = out / "tables"
    figures_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    figures_manifest = []
    tables_manifest = []
    figure_idx = 0
    table_idx = 0

    for page_num in range(len(doc)):
        page = doc[page_num]

        # ── 获取页面元素 ──
        page_images = page.get_images(full=True)  # [(xref, smask, w, h, ...), ...]
        text_blocks = page.get_text("blocks")

        # ── 识别标题文本块 ──
        figure_captions = []  # [(block, fig_number, text)]
        table_captions = []   # [(block, table_number, text)]

        for block in text_blocks:
            text = (block[4] or "").strip()
            if not text:
                continue
            # 匹配 "Fig. X." / "Figure X."
            m = re.match(
                r"(?:Fig\.?|Figure)\s*(\d+)[\.:]\s*(.+)",
                text, re.IGNORECASE,
            )
            if m and len(text) < 500:
                figure_captions.append((block, int(m.group(1)), text))
                continue
            # 匹配 "Table X" / "Table X."
            m = re.match(
                r"Table\s+(\d+)[.\s]*(.*)",
                text, re.IGNORECASE,
            )
            if m and len(text) < 500 and _is_valid_table_caption(block, text):
                table_captions.append((block, int(m.group(1)), text))
                continue

        # ── 处理内嵌图片：匹配到最近的标题 ──
        used_caption_ids = set()

        for img_tuple in page_images:
            xref = img_tuple[0]
            width = img_tuple[2]
            height = img_tuple[3]

            # 过滤太小的图片
            if width < FIGURE_MIN_WIDTH and height < FIGURE_MIN_HEIGHT:
                continue

            base_image = doc.extract_image(xref)
            if base_image is None:
                continue

            # 获取图片在页面上的位置
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            img_bbox = rects[0]  # 使用第一个出现位置

            # 分别查找最近的 figure caption 和 table caption
            nearest_fig = _find_nearest_caption(
                img_bbox, figure_captions, used_caption_ids,
            )
            nearest_tbl = _find_nearest_caption(
                img_bbox, table_captions, used_caption_ids,
            )

            # 决定归属：距离更近者胜出
            nearest = None
            nearest_type = "figure"
            if nearest_fig and nearest_tbl:
                dist_fig = _caption_distance(img_bbox, fitz.Rect(nearest_fig[0][:4]))
                dist_tbl = _caption_distance(img_bbox, fitz.Rect(nearest_tbl[0][:4]))
                if dist_fig <= dist_tbl:
                    nearest, nearest_type = nearest_fig, "figure"
                else:
                    nearest, nearest_type = nearest_tbl, "table"
            elif nearest_fig:
                nearest, nearest_type = nearest_fig, "figure"
            elif nearest_tbl:
                nearest, nearest_type = nearest_tbl, "table"
            else:
                # 无标题 → 默认为 figure
                nearest, nearest_type = None, "figure"

            # ── 计算渲染区域 ──
            if nearest is not None:
                cap_block, cap_num, cap_text = nearest
                cap_bbox = fitz.Rect(cap_block[:4])
                combined_bbox = img_bbox | cap_bbox
                used_caption_ids.add(id(cap_block))
            else:
                cap_block, cap_num, cap_text = None, 0, ""
                combined_bbox = img_bbox

            # ── 渲染并保存 ──
            if nearest_type == "figure":
                figure_idx += 1
                fig_id = f"figure_{figure_idx:02d}"
                filepath = figures_dir / f"{fig_id}.png"
                _render_page_area(page, combined_bbox, filepath)

                figures_manifest.append({
                    "id": fig_id,
                    "filename": f"{fig_id}.png",
                    "page": page_num + 1,
                    "caption": cap_text if nearest else "",
                    "caption_cn": "",
                    "context_section": _detect_section(page.get_text("text")),
                    "width": width,
                    "height": height,
                    "size_bytes": filepath.stat().st_size,
                    "has_caption_rendered": nearest is not None,
                })
            else:
                table_idx += 1
                tbl_id = f"table_{table_idx:02d}"
                filepath = tables_dir / f"{tbl_id}.png"
                _render_page_area(page, combined_bbox, filepath)

                tables_manifest.append({
                    "id": tbl_id,
                    "filename": f"{tbl_id}.png",
                    "page": page_num + 1,
                    "caption": cap_text if nearest else "",
                    "caption_cn": "",
                    "context_section": _detect_section(page.get_text("text")),
                    "width": width,
                    "height": height,
                    "size_bytes": filepath.stat().st_size,
                    "has_caption_rendered": nearest is not None,
                    "source_type": "embedded_image",
                })

        # ── 处理孤儿标题（无对应内嵌图片的表格标题 → 截取文本区域）──
        for cap_list, cap_type, target_dir, target_manifest, counter_key in [
            (table_captions, "table", tables_dir, tables_manifest, "table"),
        ]:
            for cap_block, cap_num, cap_text in cap_list:
                if id(cap_block) in used_caption_ids:
                    continue

                cap_bbox = fitz.Rect(cap_block[:4])
                content_blocks = _find_table_content_blocks(cap_bbox, text_blocks)
                if not content_blocks:
                    continue

                # 合并标题 + 内容区域
                combined = cap_bbox
                for cb in content_blocks:
                    combined = combined | fitz.Rect(cb[:4])

                if combined.height < 30:
                    continue  # 区域太小，跳过

                if cap_type == "figure":
                    figure_idx += 1
                    item_id = f"figure_{figure_idx:02d}"
                    filepath = figures_dir / f"{item_id}.png"
                else:
                    table_idx += 1
                    item_id = f"table_{table_idx:02d}"
                    filepath = tables_dir / f"{item_id}.png"

                _render_page_area(page, combined, filepath)

                entry = {
                    "id": item_id,
                    "filename": f"{item_id}.png",
                    "page": page_num + 1,
                    "caption": cap_text,
                    "caption_cn": "",
                    "context_section": _detect_section(page.get_text("text")),
                    "width": int(combined.width * RENDER_DPI),
                    "height": int(combined.height * RENDER_DPI),
                    "size_bytes": filepath.stat().st_size,
                    "has_caption_rendered": True,
                }
                if cap_type == "table":
                    entry["source_type"] = "text_region"

                target_manifest.append(entry)
                used_caption_ids.add(id(cap_block))

    return figures_manifest, tables_manifest


def _detect_section(page_text: str) -> str:
    """检测页面所属的章节"""
    section_patterns = [
        r"^(?:(\d+\.?\s+)?(?:Introduction|Related Work|Background|Method|Experiment|Result|Discussion|Conclusion|Abstract))",
        r"^(\d+\.?\s+.+)$",
    ]
    for pattern in section_patterns:
        match = re.search(pattern, page_text, re.MULTILINE | re.IGNORECASE)
        if match:
            return (match.group(1) or "").strip()
    return ""


def convert_pdf_to_markdown(doc: fitz.Document) -> str:
    """将 PDF 转换为 Markdown"""
    try:
        import pymupdf4llm
        md_content = pymupdf4llm.to_markdown(doc)
    except ImportError:
        # 回退：逐页提取文本
        pages_md = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            lines = text.split("\n")
            md_lines = []
            for line in lines:
                line = line.strip()
                if not line:
                    md_lines.append("")
                elif re.match(
                    r"^(?:Abstract|Introduction|Related Work|Method|Experiment|"
                    r"Conclusion|Reference|Acknowledgment)",
                    line, re.IGNORECASE,
                ):
                    md_lines.append(f"## {line}")
                elif re.match(r"^\d+\.?\s+[A-Z]", line):
                    md_lines.append(f"### {line}")
                else:
                    md_lines.append(line)
            pages_md.append("\n".join(md_lines))
        md_content = "\n\n".join(pages_md)

    return md_content


# ── MCP Tools ──────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="parse_pdf",
            description="下载远程 PDF 并全量解析：提取 Markdown 全文、图表（含英文标题渲染）、表格（截取为图片）、元数据。输出到指定目录。",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "PDF 文件的远程 URL（如 arXiv 链接）",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "输出目录的绝对路径，所有产物将保存到此目录",
                    },
                },
                "required": ["url", "output_dir"],
            },
        ),
        Tool(
            name="parse_pdf_local",
            description="解析本地 PDF 文件：提取 Markdown 全文、图表（含英文标题渲染）、表格（截取为图片）、元数据。输出到指定目录。",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "本地 PDF 文件的绝对路径",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "输出目录的绝对路径，所有产物将保存到此目录",
                    },
                },
                "required": ["file_path", "output_dir"],
            },
        ),
        Tool(
            name="extract_figures",
            description="提取 PDF 中所有图表（含英文标题渲染）和表格（截取为图片），不解析全文。图表保存到 figures/，表格保存到 tables/。",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "本地 PDF 文件的绝对路径",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "输出目录的绝对路径",
                    },
                },
                "required": ["file_path", "output_dir"],
            },
        ),
        Tool(
            name="get_paper_metadata",
            description="提取论文元数据：标题、作者、摘要、关键词、年份、页数。",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "本地 PDF 文件的绝对路径",
                    },
                },
                "required": ["file_path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    output_dir = Path(arguments.get("output_dir", ""))

    if name == "parse_pdf":
        url = arguments["url"]
        output_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = output_dir / "paper.pdf"
        await download_pdf(url, str(pdf_path))

        return await _process_pdf(str(pdf_path), str(output_dir))

    elif name == "parse_pdf_local":
        file_path = arguments["file_path"]
        output_dir.mkdir(parents=True, exist_ok=True)
        return await _process_pdf(file_path, str(output_dir))

    elif name == "extract_figures":
        file_path = arguments["file_path"]
        output_dir.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(file_path)
        figures_manifest, tables_manifest = extract_figures_and_tables_from_pdf(
            doc, str(output_dir),
        )
        doc.close()

        # 写入 manifest
        _write_manifests(output_dir, figures_manifest, tables_manifest)

        return [TextContent(
            type="text",
            text=(
                f"提取完成。\n"
                f"图表: {len(figures_manifest)} 张 → {output_dir / 'figures'}\n"
                f"表格: {len(tables_manifest)} 张 → {output_dir / 'tables'}\n"
            ),
        )]

    elif name == "get_paper_metadata":
        file_path = arguments["file_path"]
        doc = fitz.open(file_path)
        metadata = extract_metadata_with_fitz(doc, file_path)
        doc.close()

        return [TextContent(
            type="text",
            text=f"论文元数据:\n{json.dumps(metadata, ensure_ascii=False, indent=2)}",
        )]

    else:
        raise ValueError(f"未知工具: {name}")


def _write_manifests(
    output_dir: Path,
    figures_manifest: list[dict],
    tables_manifest: list[dict],
) -> None:
    """写入 figures_manifest.json 和 tables_manifest.json"""
    figures_dir = output_dir / "figures"
    tables_dir = output_dir / "tables"
    figures_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    with open(figures_dir / "figures_manifest.json", "w", encoding="utf-8") as f:
        json.dump(figures_manifest, f, ensure_ascii=False, indent=2)

    with open(tables_dir / "tables_manifest.json", "w", encoding="utf-8") as f:
        json.dump(tables_manifest, f, ensure_ascii=False, indent=2)


async def _process_pdf(file_path: str, output_dir: str) -> list[TextContent]:
    """核心处理逻辑：解析 PDF 并输出所有产物"""
    doc = fitz.open(file_path)

    # 1. 元数据
    metadata = extract_metadata_with_fitz(doc, file_path)

    # 2. 图表与表格提取（新逻辑：渲染含标题的完整图片）
    figures_manifest, tables_manifest = extract_figures_and_tables_from_pdf(
        doc, output_dir,
    )

    # 3. Markdown 转换
    md_content = convert_pdf_to_markdown(doc)

    doc.close()

    # 4. 保存产物
    out = Path(output_dir)

    # metadata.json
    with open(out / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    # content.md
    with open(out / "content.md", "w", encoding="utf-8") as f:
        f.write(md_content)

    # manifests
    _write_manifests(out, figures_manifest, tables_manifest)

    report = (
        f"PDF 解析完成。\n"
        f"标题: {metadata['title']}\n"
        f"作者: {metadata['authors']}\n"
        f"页数: {metadata['page_count']}\n"
        f"提取图表: {len(figures_manifest)} 张（含英文标题渲染）→ {output_dir}/figures/\n"
        f"提取表格: {len(tables_manifest)} 张 → {output_dir}/tables/\n"
        f"输出目录: {output_dir}\n"
        f"产物:\n"
        f"  - {output_dir}/content.md (全文 Markdown)\n"
        f"  - {output_dir}/metadata.json (论文元数据)\n"
        f"  - {output_dir}/figures/ (图表 {len(figures_manifest)} 张，含英文标题)\n"
        f"  - {output_dir}/figures/figures_manifest.json (图表清单)\n"
        f"  - {output_dir}/tables/ (表格 {len(tables_manifest)} 张)\n"
        f"  - {output_dir}/tables/tables_manifest.json (表格清单)\n"
    )

    return [TextContent(type="text", text=report)]


# ── 启动入口 ───────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
