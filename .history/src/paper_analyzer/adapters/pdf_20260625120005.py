#!/usr/bin/env python3
"""PDF 文本提取适配器 —— 封装 pdftotext 命令的调用细节。

本模块是纯适配器：只懂「pdftotext 这个外部命令怎么调」，
不知道 output 目录在哪、不知道论文名、不知道缓存。

运行时检测 pdftotext 是否安装，不存在时抛 ToolNotFoundError（而非 sys.exit）。

用法:
    from paper_analyzer.adapters.pdf import check_pdftotext, extract_text

    if not check_pdftotext():
        raise ToolNotFoundError("pdftotext 未安装...")
    text = extract_text(Path("paper.pdf"))

依赖:
    - poppler-utils（提供 pdftotext 命令）
    - macOS: brew install poppler
    - Linux:  apt-get install poppler-utils
"""

import logging
import shutil
import subprocess
from pathlib import Path

from paper_analyzer.errors import PdfExtractionError, ToolNotFoundError

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 公共 API
# ══════════════════════════════════════════════════════════════════════════

def check_pdftotext() -> bool:
    """检查 pdftotext 命令是否可用。

    Returns:
        True 表示命令存在且可执行。
    """
    return shutil.which("pdftotext") is not None


def ensure_pdftotext() -> None:
    """确保 pdftotext 可用，不可用时给出详细安装说明。

    Raises:
        ToolNotFoundError: pdftotext 未安装
    """
    if not check_pdftotext():
        raise ToolNotFoundError(
            "pdftotext 未安装。安装方式：\n"
            "  macOS:   brew install poppler\n"
            "  Ubuntu:  apt-get install poppler-utils\n"
            "  CentOS:  yum install poppler-utils\n"
            "安装后重试。"
        )


def extract_text(pdf_path: Path, pages: str | None = None) -> str:
    """调用 pdftotext -layout 提取 PDF 文本。

    自动检测 pdftotext 是否安装，不存在时抛 ToolNotFoundError。

    Args:
        pdf_path: PDF 文件路径（必须存在）
        pages:    页码范围（可选），如 "1-5"。None 表示提取全文。

    Returns:
        提取到的纯文本字符串。

    Raises:
        ToolNotFoundError:  pdftotext 未安装
        PdfExtractionError: pdftotext 执行失败（非零退出码）
    """
    ensure_pdftotext()

    cmd = ["pdftotext", "-layout"]

    if pages:
        start, end = pages.split("-")
        cmd.extend(["-f", start, "-l", end])

    cmd.extend([str(pdf_path), "-"])

    logger.debug("执行 pdftotext: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or "未知错误"
        raise PdfExtractionError(f"pdftotext 执行失败: {error_msg}")

    return result.stdout
