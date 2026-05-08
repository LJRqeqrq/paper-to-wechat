#!/usr/bin/env python3
"""
图片预处理工具

在发布到微信公众号之前，对所有图片进行预处理：
1. 检测真实格式（WebP 伪装 → 转换为 PNG）
2. 压缩 > 1MB 的图片（resize + quality）
3. 确保扩展名与实际格式一致
4. 输出处理报告

用法:
    python img_preprocess.py <图片目录或文件>
    python img_preprocess.py ./figures/
    python img_preprocess.py cover.png figure_01.png
"""

import argparse
import json
import os
import struct
import sys
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError


# ── 常量 ──────────────────────────────────────────────

MAX_SIZE_BYTES = 1_000_000  # 微信 API 限制: 1MB
MAX_DIMENSION = 2000        # 最大边长
JPEG_QUALITY = 85
PNG_OPTIMIZE = True

# 文件魔数签名
MAGIC_SIGNATURES = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF8": "gif",
    b"BM": "bmp",
    b"RIFF": "webp",  # RIFF....WEBP
}


def detect_real_format(filepath: Path) -> str:
    """通过文件头魔数检测真实图片格式"""
    try:
        with open(filepath, "rb") as f:
            header = f.read(12)

        for magic, fmt in MAGIC_SIGNATURES.items():
            if header.startswith(magic):
                if magic == b"RIFF":
                    # 进一步检查是否是 WebP
                    if header[8:12] == b"WEBP":
                        return "webp"
                    return "unknown_riff"
                return fmt

        return "unknown"
    except Exception:
        return "error"


def get_image_info(filepath: Path) -> Optional[dict]:
    """获取图片详细信息"""
    try:
        img = Image.open(filepath)
        return {
            "format": img.format,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
            "size_bytes": filepath.stat().st_size,
        }
    except Exception:
        return None


def preprocess_image(filepath: Path, output_dir: Optional[Path] = None) -> dict:
    """
    预处理单张图片。
    返回处理结果 dict。
    """
    result = {
        "file": str(filepath),
        "original_size": filepath.stat().st_size,
        "actions": [],
        "success": True,
        "error": None,
    }

    try:
        # 步骤 1: 检测真实格式
        real_fmt = detect_real_format(filepath)
        current_ext = filepath.suffix.lower().lstrip(".")

        if real_fmt == "webp" and current_ext in ("png", "jpg", "jpeg"):
            # WebP 伪装 — 需要转换
            img = Image.open(filepath)
            new_path = filepath.with_suffix(".png")
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                img.save(new_path, "PNG", optimize=PNG_OPTIMIZE)
            else:
                img = img.convert("RGB")
                img.save(new_path, "PNG", optimize=PNG_OPTIMIZE)
            img.close()

            # 替换原文件
            backup_path = filepath.with_suffix(filepath.suffix + ".webp_backup")
            filepath.rename(backup_path)
            new_path.rename(filepath)
            result["actions"].append(f"WebP→PNG 转换: {filepath.name}")
            result["converted_from"] = "webp"

        elif real_fmt == "webp" and current_ext == "webp":
            # 显式 WebP — 转为 PNG
            img = Image.open(filepath)
            new_path = filepath.with_suffix(".png")
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                img.save(new_path, "PNG", optimize=PNG_OPTIMIZE)
            else:
                img = img.convert("RGB")
                img.save(new_path, "PNG", optimize=PNG_OPTIMIZE)
            img.close()
            filepath.unlink()
            # 更新 filepath 引用
            result["converted_from"] = "webp"
            result["actions"].append(f"WebP→PNG 转换: {new_path.name}")
            result["converted_path"] = str(new_path)

        # 步骤 2: 检查是否需要压缩（>1MB）
        current_path = Path(result.get("converted_path", str(filepath)))
        current_size = current_path.stat().st_size if current_path.exists() else result["original_size"]

        if current_size > MAX_SIZE_BYTES:
            img = Image.open(current_path)
            original_dims = (img.width, img.height)

            # 缩小尺寸
            if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
                img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

            # 保存为 JPEG（更小的体积）
            if img.mode in ("RGBA", "LA", "P"):
                # 有透明通道 — 仍需 PNG
                img.save(current_path, "PNG", optimize=PNG_OPTIMIZE)
            else:
                jpg_path = current_path.with_suffix(".jpg")
                img = img.convert("RGB")
                img.save(jpg_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
                if jpg_path != current_path:
                    if current_path.exists():
                        current_path.unlink()
                    result["converted_path"] = str(jpg_path)

            img.close()
            new_size = Path(result.get("converted_path", str(current_path))).stat().st_size
            result["actions"].append(
                f"压缩: {original_dims[0]}x{original_dims[1]} → "
                f"{result['original_size']//1024}KB → {new_size//1024}KB"
            )
            result["final_size"] = new_size
        else:
            result["final_size"] = current_size

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)

    return result


def preprocess_directory(directory: Path) -> list[dict]:
    """预处理目录下所有图片"""
    results = []
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

    for filepath in sorted(directory.rglob("*")):
        if filepath.suffix.lower() in image_exts and filepath.is_file():
            # 跳过备份文件
            if ".webp_backup" in filepath.suffix:
                continue
            result = preprocess_image(filepath)
            results.append(result)

    return results


def print_report(results: list[dict]):
    """打印处理报告"""
    total = len(results)
    success = sum(1 for r in results if r["success"])
    failed = total - success
    converted = sum(1 for r in results if r.get("converted_from"))
    compressed = sum(1 for r in results if "压缩" in " ".join(r.get("actions", [])))

    print(f"\n{'='*60}")
    print(f"图片预处理报告")
    print(f"{'='*60}")
    print(f"处理总数: {total}")
    print(f"成功: {success}")
    print(f"失败: {failed}")
    print(f"格式转换 (WebP→PNG): {converted}")
    print(f"压缩 (>1MB): {compressed}")
    print(f"{'='*60}")

    for r in results:
        status = "✓" if r["success"] else "✗"
        name = Path(r["file"]).name
        size_kb = r["original_size"] // 1024
        actions = " | ".join(r["actions"]) if r["actions"] else "无需处理"
        error = f" 错误: {r['error']}" if r["error"] else ""
        print(f"  {status} {name} ({size_kb}KB) → {actions}{error}")


def main():
    parser = argparse.ArgumentParser(
        description="图片预处理工具 — 检测格式、转换 WebP、压缩大图，确保满足微信公众号要求"
    )
    parser.add_argument(
        "paths", nargs="+", help="图片文件或目录路径（支持多个）"
    )
    parser.add_argument(
        "--report", "-r", action="store_true", help="输出 JSON 格式报告"
    )
    args = parser.parse_args()

    all_results = []

    for path_str in args.paths:
        path = Path(path_str)
        if not path.exists():
            print(f"警告: 路径不存在 — {path_str}", file=sys.stderr)
            continue

        if path.is_dir():
            results = preprocess_directory(path)
        else:
            results = [preprocess_image(path)]

        all_results.extend(results)

    if args.report:
        print(json.dumps(all_results, ensure_ascii=False, indent=2))
    else:
        print_report(all_results)

    # 返回码
    failed = sum(1 for r in all_results if not r["success"])
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
