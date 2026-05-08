#!/usr/bin/env python3
"""
封面 HTML 生成器

根据论文元数据，使用 Jinja2 模板渲染微信公众号风格的封面 HTML。
输出的 HTML 可用于 screenshot-mcp 截图生成封面 PNG。

用法:
    python generate_cover_html.py metadata.json -o cover.html
    python generate_cover_html.py --title "论文标题" --authors "作者" -o cover.html
"""

import argparse
import json
import re
import sys
from pathlib import Path


TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "cover_template.html"


def render_template(**kwargs) -> str:
    """简易模板渲染：支持 {{variable}} 和 {{#variable}}...{{/variable}} 条件块"""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    # 处理条件块 {{#var}}...{{/var}}
    def replace_conditional(match):
        var_name = match.group(1)
        content = match.group(2)
        if kwargs.get(var_name):
            return content
        return ""

    template = re.sub(
        r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}",
        replace_conditional,
        template,
        flags=re.DOTALL,
    )

    # 简单变量替换
    for key, value in kwargs.items():
        if value:
            template = template.replace(f"{{{{{key}}}}}", str(value))
        else:
            template = template.replace(f"{{{{{key}}}}}", "")

    return template


def load_metadata(metadata_path: str) -> dict:
    """从 metadata.json 加载论文信息"""
    with open(metadata_path, encoding="utf-8") as f:
        meta = json.load(f)

    # 提取简短标题（中文或英文）
    title_full = meta.get("title", "论文标题")
    # 截断过长的标题
    if len(title_full) > 60:
        title_full = title_full[:57] + "..."

    authors_full = meta.get("authors", "")
    # 截断过长的作者列表
    if len(authors_full) > 120:
        authors_full = authors_full[:117] + "..."

    venue = ""
    if meta.get("year"):
        venue = meta["year"]
    # 尝试从标题或文本中提取会议/期刊名
    if meta.get("keywords"):
        keywords = meta["keywords"]
        # 常见会议名称
        for conf in ["CVPR", "ICCV", "ECCV", "NeurIPS", "ICML", "ICLR", "ACL", "EMNLP", "NAACL", "AAAI", "IJCAI", "SIGGRAPH", "CHI"]:
            if conf.lower() in keywords.lower():
                venue = f"{conf} {meta.get('year', '')}"
                break

    return {
        "title": title_full,
        "title_en": "",  # 保留英文原标题用于副标题
        "authors": authors_full,
        "venue": venue.strip(),
        "tag": "论文解读",
    }


def main():
    parser = argparse.ArgumentParser(description="生成微信公众号推文封面 HTML")
    parser.add_argument("metadata", nargs="?", help="metadata.json 文件路径")
    parser.add_argument("--title", help="论文标题（中文）")
    parser.add_argument("--title_en", default="", help="论文原标题（英文）")
    parser.add_argument("--authors", default="", help="作者")
    parser.add_argument("--venue", default="", help="发表会议/期刊及年份")
    parser.add_argument("--tag", default="论文解读", help="封面标签文字")
    parser.add_argument("-o", "--output", default="cover.html", help="输出 HTML 文件路径")
    args = parser.parse_args()

    if args.metadata:
        params = load_metadata(args.metadata)
        # 命令行参数可覆盖
        if args.title:
            params["title"] = args.title
        if args.authors:
            params["authors"] = args.authors
        if args.venue:
            params["venue"] = args.venue
        if args.tag:
            params["tag"] = args.tag
        params["title_en"] = args.title_en or params.get("title_en", "")
    else:
        params = {
            "title": args.title or "论文标题",
            "title_en": args.title_en,
            "authors": args.authors,
            "venue": args.venue,
            "tag": args.tag,
        }

    html = render_template(**params)
    output_path = Path(args.output)
    output_path.write_text(html, encoding="utf-8")
    print(f"封面 HTML 已生成: {output_path}")


if __name__ == "__main__":
    main()
