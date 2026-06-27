#!/usr/bin/env python3
"""paper-split: TXT → 章节 JSON

用法:
    paper-split text.txt -o sections.json
    paper-split text.txt --section 引言
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.config_loader import get_split_patterns
from paper_analyzer.core.split import (
    detect_style, find_cn_headings, find_en_headings, split_text,
)
from paper_analyzer.errors import PaperAnalyzerError
from paper_analyzer.io import write_json

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-split: 按一级标题将论文文本切分为章节"
    )
    parser.add_argument("txt", help="输入的 TXT 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 JSON 文件路径（默认 stdout）")
    parser.add_argument("--section", "-s", default=None, help="只输出指定标题的章节（模糊匹配）")
    parser.add_argument("--project-root", default=None, help="项目根目录")
    parser.add_argument("--config-dir", default=None, help="自定义配置目录")
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")

    _setup_cli(parser.parse_args())


def _setup_cli(args: argparse.Namespace) -> None:
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
    txt_path = Path(args.txt)
    if not txt_path.exists():
        logger.error("错误：文件不存在 —— %s", txt_path)
        sys.exit(1)

    patterns = get_split_patterns(config)
    text = txt_path.read_text(encoding="utf-8")
    style = detect_style(
        text,
        patterns["cn_heading"],
        patterns["en_heading_line"],
        patterns["en_heading_mid"],
    )

    if style == "cn":
        headings = find_cn_headings(text, patterns["cn_heading"])
    else:
        headings = find_en_headings(
            text,
            patterns["en_heading_line"],
            patterns["en_heading_mid"],
            patterns["heading_number_regex"],
            patterns["heading_dedup_distance"],
        )

    if not headings:
        logger.warning("未检测到任何一级标题，整篇作为「全文」处理")
        sections = [{"title": "全文", "content": text.strip()}]
    else:
        sections = split_text(text, headings, patterns["min_pre_content_chars"])

    # 可选过滤
    if args.section:
        sections = [s for s in sections if args.section in s["title"]]
        if not sections:
            logger.error("未找到匹配 '%s' 的章节", args.section)
            sys.exit(1)

    if args.output:
        write_json(Path(args.output), sections)
        logger.info("已保存: %s（%s 个章节）", args.output, len(sections))
    else:
        print(json.dumps(sections, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
