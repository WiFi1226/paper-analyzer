#!/usr/bin/env python3
"""paper-invoke: 路由结果 → Prompt 构造

用法:
    paper-invoke sections.json routing.json -o prompts.json
    paper-invoke sections.json routing.json --rules-dir /path/to/rules
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

# ── dispatch 清单构造 ──────────────────────────────────────────


def _build_dispatch_description(
    agent: str,
    paper_name: str,
    chapter_titles: list[str],
    part_num: int | None = None,
) -> str:
    """构造 Agent description 字符串。

    格式: "<agent>[ Part<N>]: paper=<paper_name> <chapter_titles>"
    示例:
        "chart-results-analyzer: paper=Freeman_2025... IV. Baseline Results"
        "chart-results-analyzer Part2: paper=Freeman_2025... V. Results"
    """
    titles_str = ", ".join(chapter_titles)
    agent_prefix = f"{agent} Part{part_num}" if part_num else agent
    return f"{agent_prefix}: paper={paper_name} {titles_str}"


def _build_dispatch_manifest(
    prompts: list[dict], paper_name: str,
) -> dict:
    """从已完成构造的 prompts 清单生成 dispatch 清单。"""
    calls = []
    for idx, entry in enumerate(prompts):
        split_total = entry.get("split_total", 0) or 0
        description = _build_dispatch_description(
            entry["agent"], paper_name, entry.get("chapter_titles", []),
            part_num=(entry.get("split_index", 0) + 1) if split_total > 1 else None,
        )
        calls.append({
            "sequence": idx + 1,
            "agent": entry["agent"],
            "description": description,
            "prompt_index": idx,
            "chapter_titles": entry.get("chapter_titles", []),
            "total_chars": entry.get("total_chars", 0),
        })
    return {"paper_name": paper_name, "calls": calls, "total_calls": len(calls)}

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
    parser.add_argument(
        "--config-file", action="append", default=None,
        help="指定单个配置文件路径（可多次指定，按文件名覆盖包内默认）",
    )
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
        config_files=args.config_file,
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
    paper_name = raw_data.get("paper_name", "")
    matches = routing.get("matches", [])

    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    rules, missing_rules = _load_rules(rules_dir, config)
    warnings: list[str] = list(missing_rules)
    if rules:
        logger.info("[规则] 读取:  %s 字符", len(rules))

    sections_lookup: dict[str, str] = {sec["title"]: sec.get("content", "") for sec in sections}

    agent_titles: dict[str, list[str]] = {}
    for m in matches:
        # 兼容新旧格式：agent（字符串）或 agents（数组）
        if "agents" in m:
            agent_list = m["agents"]
        elif "agent" in m:
            agent_list = [m["agent"]]
        else:
            continue
        for agent_name in agent_list:
            agent_titles.setdefault(agent_name, []).append(m["title"])
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
        # 构建 content_block 重建函数：递归拆分时只为子集章节构建内容
        def _rebuild_content(titles_subset: list[str]) -> str:
            cb, _ = paper_content_formatter(
                titles_subset, sections_lookup, rules, sections,
                needs_chapter_list=needs_cl, custom_footer=custom_footer,
            )
            return cb

        agent_prompts = build_single_prompt(
            agent, titles, rules, content_block, footer, get_max_prompt_chars(config), warnings,
            content_block_rebuilder=_rebuild_content,
        )
        logger.info("[构建] 完成:  agent=%s, %s 条 prompt", agent, len(agent_prompts))

        new_prompts.extend(agent_prompts)

    # ══════════════════════════════════════════════════════════
    # 阶段 3：输出
    # ══════════════════════════════════════════════════════════
    result = {
        "prompts": new_prompts,
        "total_agent_calls": len(new_prompts),
        "rules_chars": len(rules),
        "paper_name": paper_name,
        "warnings": warnings,
    }

    for w in warnings:
        logger.warning("[警告] %s", w)

    # ── 写 _prompts.json ────────────────────────────────────────
    if args.output:
        output_path = Path(args.output)
        write_json(output_path, result)
        logger.info("已保存: %s（%s 条 prompt）", output_path, len(new_prompts))
        dispatch_path = output_path.with_name("_dispatch.json")
    elif paper_name:
        prompts_dir = config.prompts_cache_dir(paper_name)
        output_path = prompts_dir / "_prompts.json"
        write_json(output_path, result)
        logger.info("已保存: %s（%s 条 prompt）", output_path, len(new_prompts))
        dispatch_path = prompts_dir / "_dispatch.json"
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        dispatch_path = None

    # ── 写 _dispatch.json（与 _prompts.json 同目录）────────────
    if dispatch_path and new_prompts and paper_name:
        manifest = _build_dispatch_manifest(new_prompts, paper_name)
        write_json(dispatch_path, manifest)
        logger.info("已保存: %s（%s 条 dispatch）", dispatch_path, len(new_prompts))


if __name__ == "__main__":
    main()
