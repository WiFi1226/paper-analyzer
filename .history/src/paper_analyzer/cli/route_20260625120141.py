#!/usr/bin/env python3
"""paper-route: 章节 JSON → Agent 路由

用法:
    paper-route sections.json -o routing.json
    paper-route sections.json --section "引言" --agent "causal-inference"
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.config_loader import get_routing_rules
from paper_analyzer.core.route import (
    filter_sections, match_chapters,
    build_filter_description, build_match_report, summarize_matches,
)
from paper_analyzer.errors import PaperAnalyzerError
from paper_analyzer.io import write_json

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-route: 章节 → Agent 路由器（关键词全量匹配）"
    )
    parser.add_argument("sections_json", help="split 输出的 sections JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出路由 JSON 文件路径（默认 stdout）")
    parser.add_argument("--section", "-s", action="append", default=None,
                        help="只分析标题命中关键词的章节（可多次指定，OR 逻辑）")
    parser.add_argument("--agent", "-a", action="append", default=None,
                        help="只调用指定的 agent（可多次指定，支持别名展开）")
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
    sections_path = Path(args.sections_json)
    if not sections_path.exists():
        logger.error("错误：sections JSON 不存在 —— %s", sections_path)
        sys.exit(1)

    with open(sections_path, encoding="utf-8") as f:
        sections = json.load(f)

    routes, aliases = get_routing_rules(config)
    has_user_filters = bool(args.section or args.agent)

    filtered = filter_sections(sections, args.section)
    matches, unmatched = match_chapters(
        filtered, routes, aliases, args.agent,
        has_user_filters=has_user_filters,
    )
    matched_agents = list(dict.fromkeys(m["agent"] for m in matches))

    report = build_match_report(
        sections, matches, args.section, args.agent, aliases,
    )
    filter_desc = build_filter_description(args.section, args.agent, aliases)

    routing = {
        "matches": matches,
        "unmatched": unmatched,
        "matched_agents": matched_agents,
        "filter_applied": filter_desc,
        "total_chapters": len(sections),
        "report_markdown": report,
    }

    if args.output:
        write_json(Path(args.output), routing)
        summary = summarize_matches(matches, matched_agents, len(sections))
        logger.info("已保存: %s（%s）", args.output, summary)
    else:
        print(json.dumps(routing, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
