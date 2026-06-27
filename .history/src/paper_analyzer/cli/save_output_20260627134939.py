#!/usr/bin/env python3
"""paper-save-output: Agent 输出 → 文件

保存 Agent 分析结果到 _contents/ 目录。

用法:
    paper-save-output <agent_name> <paper_name> <output.md> -o _contents/
    paper-save-output causal-inference-analyzer my_paper agent_output.md
"""

import argparse
import logging
import sys
from pathlib import Path

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.output_saver import save_agent_output
from paper_analyzer.errors import PaperAnalyzerError

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-save-output: 保存 Agent 分析输出到文件"
    )
    parser.add_argument("agent", help="Agent 名称")
    parser.add_argument("paper", help="论文名")
    parser.add_argument("output_file", help="Agent 输出文件路径（.md）")
    parser.add_argument("-o", "--output-dir", default=None, help="输出根目录（默认 output/<论文名>/cache/_contents/）")
    parser.add_argument("--part", type=int, default=1, help="分片编号（默认 1）")
    parser.add_argument("--split-total", type=int, default=0, help="总分片数（0 表示未拆分）")
    parser.add_argument("--project-root", default=None, help="项目根目录")
    parser.add_argument("--config-dir", default=None, help="自定义配置目录")
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
    output_file = Path(args.output_file)
    if not output_file.exists():
        logger.error("错误：输出文件不存在 —— %s", output_file)
        sys.exit(1)

    output_text = output_file.read_text(encoding="utf-8")

    saved_path = save_agent_output(
        agent=args.agent,
        paper_name=args.paper,
        output_text=output_text,
        part_num=args.part,
        split_total=args.split_total,
        config=config,
    )

    logger.info("输出已保存: %s", saved_path)


if __name__ == "__main__":
    main()
