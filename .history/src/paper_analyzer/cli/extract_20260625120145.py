#!/usr/bin/env python3
"""paper-extract: PDF → TXT

用法:
    paper-extract paper.pdf -o text.txt
    paper-extract paper.pdf --pages 1-5
"""

import argparse
import logging
import sys
from pathlib import Path

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.pdf import extract_text, ensure_pdftotext
from paper_analyzer.errors import PaperAnalyzerError
from paper_analyzer.io import write_text

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-extract: 从 PDF 提取纯文本"
    )
    parser.add_argument("pdf", help="PDF 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出文件路径（默认 stdout）")
    parser.add_argument("--pages", default=None, help="页码范围，如 1-5")
    parser.add_argument("--project-root", default=None, help="项目根目录")
    parser.add_argument("--config-dir", default=None, help="自定义配置目录")
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")

    _setup_cli(parser.parse_args())


def _setup_cli(args: argparse.Namespace) -> None:
    """配置日志 + Config，然后执行主逻辑。"""
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s", stream=sys.stderr)
    elif args.quiet:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s", stream=sys.stderr)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    config = Config(
        project_root=args.project_root,
        config_dir=args.config_dir,
        output_dir=args.output_dir,
    )
    set_default_config(config)

    try:
        _run(args, config)
    except PaperAnalyzerError as e:
        logger.error("错误: %s", e)
        sys.exit(1)
    except FileNotFoundError as e:
        logger.error("错误: 文件不存在 —— %s", e)
        sys.exit(1)


def _run(args: argparse.Namespace, config: Config) -> None:
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        logger.error("错误：PDF 文件不存在 —— %s", pdf_path)
        sys.exit(1)

    ensure_pdftotext()
    logger.info("正在提取: %s", pdf_path.name)
    if args.pages:
        logger.info("  页码范围: %s", args.pages)

    text = extract_text(pdf_path, args.pages)

    if args.output:
        write_text(Path(args.output), text)
        logger.info("已保存: %s（%s 字符，%s 行）", args.output, len(text), text.count("\n") + 1)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
