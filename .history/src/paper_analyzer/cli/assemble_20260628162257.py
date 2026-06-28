#!/usr/bin/env python3
"""paper-assemble: Agent 输出 → 最终报告

用法:
    paper-assemble output/my_paper/cache/_contents/
    paper-assemble output/my_paper/cache/_contents/ -o report.md
"""

import argparse
import logging
import sys
from pathlib import Path

from paper_analyzer.adapters.config_loader import (
    get_agent_section_map, get_agent_canonical_order,
)
from paper_analyzer.errors import PaperAnalyzerError

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-assemble: 汇总 Agent 输出生成分析报告"
    )
    parser.add_argument("contents_dir", help="Agent 输出目录（如 output/my_paper/cache/_contents/）")
    parser.add_argument("-o", "--output", default=None,
                        help="输出路径（默认 <论文目录>/<论文名>_analysis.md）")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    _setup_cli(parser.parse_args())


def _setup_cli(args: argparse.Namespace) -> None:
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG,
                            format="%(levelname)s: %(message)s", stream=sys.stderr)
    elif args.quiet:
        logging.basicConfig(level=logging.WARNING,
                            format="%(levelname)s: %(message)s", stream=sys.stderr)
    else:
        logging.basicConfig(level=logging.INFO,
                            format="%(message)s", stream=sys.stderr)

    try:
        _run(args)
    except PaperAnalyzerError as e:
        logger.error("错误: %s", e)
        sys.exit(1)
    except FileNotFoundError as e:
        logger.error("错误: 文件不存在 —— %s", e)
        sys.exit(1)


def _run(args: argparse.Namespace) -> None:
    out_dir = Path(args.contents_dir).resolve()
    paper_name = out_dir.parent.parent.name

    if not out_dir.exists():
        logger.error("目录不存在: %s", out_dir)
        sys.exit(1)

    # 1. 加载 agent 注册信息（顺序 + 区段标签）
    order = get_agent_canonical_order()
    label = get_agent_section_map()

    if not order:
        logger.error("agents.yaml 未定义任何 agent")
        sys.exit(1)

    # 2. 读取 agent 输出
    outputs: dict[str, str] = {}
    for agent in order:
        fp = out_dir / f"{agent}.md"
        if fp.exists():
            outputs[agent] = fp.read_text(encoding="utf-8")

    if not outputs:
        logger.error("未找到任何 agent 输出（目录: %s）", out_dir)
        sys.exit(1)

    # 3. 按 order 排序 + 组装
    sorted_agents = sorted(outputs, key=lambda a: order[a])

    lines = [f"# 论文分析报告: {paper_name}", ""]

    for agent in sorted_agents:
        lines += [
            "",
            f"## {label.get(agent, agent)}",
            f"<!-- agent_section: {agent} -->",
            "",
            outputs[agent].strip(),
            "",
            "---",
        ]

    report = "\n".join(lines)

    # 4. 输出
    out_path = Path(args.output) if args.output else out_dir.parent.parent / f"{paper_name}_analysis.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

if __name__ == "__main__":
    main()
