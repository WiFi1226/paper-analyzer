#!/usr/bin/env python3
"""paper-invoke: 路由结果 → Prompt 构造

用法:
    paper-invoke sections.json routing.json -o prompts.json
    paper-invoke sections.json routing.json --rules-dir /path/to/rules --cache-dir /path/to/cache
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.config_loader import (
    load_settings, load_agents, unwrap_routing, get_max_prompt_chars,
)
from paper_analyzer.core.prompts import (
    paper_content_formatter, build_single_prompt,
)
from paper_analyzer.errors import PaperAnalyzerError
from paper_analyzer.io import write_json

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-invoke: 为每个 agent 构造完整 Prompt"
    )
    parser.add_argument("sections_json", help="split 输出的 sections JSON 文件路径")
    parser.add_argument("routing_json", help="route 输出的路由 JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 JSON 文件路径（默认 stdout）")
    parser.add_argument("--rules-dir", default=None, help="规则文件目录（默认当前目录下 .claude/rules/）")
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


def _load_rules_from_disk(rules_dir: Path) -> list[tuple[str, Path]]:
    """扫描规则目录下所有 .md 文件，按文件名排序。"""
    if not rules_dir.exists():
        return []
    files = sorted(
        (f.name, f) for f in rules_dir.iterdir()
        if f.is_file() and f.suffix == ".md"
    )
    return files


def _load_rules(rules_dir: Path | None = None, config: Config | None = None) -> tuple[str, list[str]]:
    """按顺序读取规则文件并拼接为通用规则块。"""
    if rules_dir is None:
        if config:
            rules_dir = config.project_root / ".claude" / "rules"
        else:
            rules_dir = Path.cwd() / ".claude" / "rules"

    parts = []
    missing = []
    for fname, fpath in _load_rules_from_disk(rules_dir):
        if fpath.exists():
            parts.append(fpath.read_text(encoding="utf-8").strip())
        else:
            msg = f"规则文件缺失: {fname}"
            missing.append(msg)
            logger.warning("⚠️  %s（预期路径: %s）", msg, fpath)
    return "\n\n".join(parts), missing




def _run(args: argparse.Namespace, config: Config) -> None:
    # ══════════════════════════════════════════════════════════
    # 阶段 1：加载
    # ══════════════════════════════════════════════════════════
    sections_path = Path(args.sections_json)
    if not sections_path.exists():
        logger.error("错误：sections JSON 不存在 —— %s", sections_path)
        sys.exit(1)

    routing_path = Path(args.routing_json)
    if not routing_path.exists():
        logger.error("错误：routing JSON 不存在 —— %s", routing_path)
        sys.exit(1)

    with open(sections_path, encoding="utf-8") as f:
        sections = json.load(f)
    with open(routing_path, encoding="utf-8") as f:
        raw_data = json.load(f)
    logger.info("[章节] 读取:  %s 个章节 → %s", len(sections), sections_path)
    logger.info("[路径] 读取:  %s 条匹配 → %s", len(raw_data.get("matches", [])), routing_path)

    routing = unwrap_routing(raw_data)
    paper_name = raw_data.get("paper_name", "") or sections_path.parent.parent.stem
    matches = routing.get("matches", [])

    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    rules, missing_rules = _load_rules(rules_dir, config)
    warnings: list[str] = list(missing_rules)
    if rules:
        logger.info("[规则] 读取:  %s 字符", len(rules))

    sections_lookup: dict[str, str] = {sec["title"]: sec.get("content", "") for sec in sections}

    agent_titles: dict[str, list[str]] = {}
    for m in matches:
        agent_titles.setdefault(m["agent"], []).append(m["title"])
    logger.info("[分组] 完成:  %s 个 agent → %s",
        len(agent_titles),
        ", ".join(f"{k}({len(v)})" for k, v in agent_titles.items()))

    agents_cfg = load_agents(config).get("agents", {})
    chapter_list_agents = frozenset(
        name for name, info in agents_cfg.items() if info.get("needs_chapter_list")
    )

    custom_footer = load_settings(config).get("prompt_footer")

    # ══════════════════════════════════════════════════════════
    # 阶段 2：处理
    # ══════════════════════════════════════════════════════════
    new_prompts: list[dict[str, Any]] = []

    for agent, titles in agent_titles.items():
        needs_cl = agent in chapter_list_agents
        content_block, footer = paper_content_formatter(
            titles, sections_lookup, rules, sections,
            needs_chapter_list=needs_cl, custom_footer=custom_footer,
        )
        agent_prompts = build_single_prompt(
            agent, titles, rules, content_block, footer, get_max_prompt_chars(config), warnings,
        )
        logger.info("[构建] 完成:  agent=%s, %s 条 prompt", agent, len(agent_prompts))

        for p in agent_prompts:
            p["file_path"] = ""
            p["total_chars"] = len(p["prompt_text"])
            new_prompts.append(p)

    # ══════════════════════════════════════════════════════════
    # 阶段 3：输出
    # ══════════════════════════════════════════════════════════
    total_input_chars = sum(len(p["prompt_text"]) for p in new_prompts)
    result = {
        "prompts": new_prompts,
        "total_agent_calls": len(new_prompts),
        "rules_chars": len(rules),
        "paper_name": paper_name,
        "warnings": warnings,
        "stats": {
            "total_input_chars": total_input_chars,
            "total_estimated_tokens": total_input_chars // 2,
        },
    }

    for w in warnings:
        logger.warning("[警告] %s", w)

    if args.output:
        write_json(Path(args.output), result)
        logger.info("已保存: %s（%s 条 prompt）", args.output, len(new_prompts))
    elif paper_name:
        output_path = config.prompts_cache_dir(paper_name) / "prompts.json"
        write_json(output_path, result)
        logger.info("已保存: %s（%s 条 prompt）", output_path, len(new_prompts))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
