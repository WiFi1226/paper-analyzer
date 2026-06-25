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
    load_agents, unwrap_routing, get_max_prompt_chars,
)
from paper_analyzer.cache import (
    sha256, compute_rules_hash, compute_sections_hash,
    load_existing_meta, find_cached_agent_entry, is_agent_entry_fresh,
    collect_kept_files, cleanup_orphaned_files,
)
from paper_analyzer.core.prompts import (
    build_all_prompts, paper_content_formatter, build_single_prompt,
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
    parser.add_argument("--cache-dir", "-c", default=None, help="prompt 缓存目录")
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


def _write_prompt_file(p: dict[str, Any], cache_dir: Path) -> str:
    """将一条 prompt 写入文件，返回文件的绝对路径。"""
    prompts_dir = cache_dir / "_prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    agent: str = p["agent"]
    split_idx: int | None = p.get("split_index")
    if split_idx is not None:
        fname = f"prompt_{agent}_part{split_idx + 1}.txt"
    else:
        fname = f"prompt_{agent}.txt"
    fpath = prompts_dir / fname

    if p.get("content_text"):
        content_fname = fname.replace("prompt_", "content_")
        content_fpath = prompts_dir / content_fname
        content_fpath.write_text(p["content_text"], encoding="utf-8")
        p["_content_path"] = str(content_fpath.resolve())

        title = p["chapter_titles"][0]
        content_ref = (
            f"**章节标题**: {title}\n\n"
            f"---\n"
            f"⚠️ **本章节过长（{len(p['content_text']):,} 字符），完整内容已写入独立文件。**\n"
            f"**你必须使用 Read 工具读取以下文件获取完整章节内容**，否则分析将不完整：\n\n"
            f"```\n{content_fpath.resolve()}\n```\n\n"
            f"读取后请继续按上述规则对该章节进行分析。"
        )
        inline_prompt = p["skeleton_text"] + "\n" + content_ref + "\n" + p["footer_text"]
        fpath.write_text(inline_prompt, encoding="utf-8")
    else:
        fpath.write_text(p["prompt_text"], encoding="utf-8")

    return str(fpath.resolve())


def _finalize_prompt_entry(p: dict[str, Any], file_path: str, max_chars: int) -> dict[str, Any]:
    """完成 prompt 条目的最终处理。"""
    p["file_path"] = file_path
    if len(p["prompt_text"]) > max_chars:
        p["prompt_text"] = (
            f"[prompt 已保存至文件，共 {len(p['prompt_text'])} 字符]\n"
            f"文件路径: {p['file_path']}\n"
            "请用 Read 工具读取该文件获取完整分析任务。"
        )
        p["total_chars"] = len(p["prompt_text"])
    return p


def _restore_parts_from_cache(agent_entry: dict[str, Any], max_chars: int) -> list[dict[str, Any]] | None:
    """从缓存文件恢复一个 agent 的所有 prompt part。"""
    agent = agent_entry["agent"]
    parts: list[dict[str, Any]] = []
    for part in agent_entry.get("parts", []):
        fpath = Path(part["file_path"])
        if not fpath.exists():
            return None

        prompt_text = fpath.read_text(encoding="utf-8")
        result: dict[str, Any] = {
            "agent": agent,
            "prompt_text": prompt_text,
            "total_chars": len(prompt_text),
            "chapter_titles": part.get("chapter_titles", []),
            "file_path": str(fpath.resolve()),
        }
        if part.get("split_index") is not None:
            result["split_index"] = part["split_index"]
            result["split_total"] = part["split_total"]

        if len(prompt_text) > max_chars:
            result["prompt_text"] = (
                f"[prompt 已保存至文件，共 {len(prompt_text)} 字符]\n"
                f"文件路径: {result['file_path']}\n"
                "请用 Read 工具读取该文件获取完整分析任务。"
            )
            result["total_chars"] = len(result["prompt_text"])

        parts.append(result)
    return parts


def _write_cache_meta(
    cache_dir: Path,
    agent_entries: list[dict[str, Any]],
    rules_chars: int,
    paper_name: str,
    warnings: list[str],
    rules_hash: str,
    sections_hash: str,
) -> None:
    """写入 _prompts/_meta.json。"""
    prompts_dir = cache_dir / "_prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "rules_chars": rules_chars,
        "paper_name": paper_name,
        "warnings": warnings,
        "fingerprint": {
            "rules_sha256": rules_hash,
            "sections_sha256": sections_hash,
        },
        "agent_entries": agent_entries,
    }
    (prompts_dir / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _run(args: argparse.Namespace, config: Config) -> None:
    sections_path = Path(args.sections_json)
    if not sections_path.exists():
        logger.error("错误：sections JSON 不存在 —— %s", sections_path)
        sys.exit(1)

    routing_path = Path(args.routing_json)
    if not routing_path.exists():
        logger.error("错误：routing JSON 不存在 —— %s", routing_path)
        sys.exit(1)

    max_chars = get_max_prompt_chars(config)
    cache_dir = Path(args.cache_dir) if args.cache_dir else None

    with open(sections_path, encoding="utf-8") as f:
        sections = json.load(f)

    with open(routing_path, encoding="utf-8") as f:
        raw_data = json.load(f)

    routing = unwrap_routing(raw_data)
    paper_name = raw_data.get("paper_name", "") or sections_path.stem
    matches = routing.get("matches", [])

    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    rules, missing_rules = _load_rules(rules_dir, config)
    rules_hash = compute_rules_hash(rules)
    sections_hash = compute_sections_hash(sections)
    warnings: list[str] = list(missing_rules)

    # 建立 title → content 查找表
    sections_lookup: dict[str, str] = {}
    for sec in sections:
        sections_lookup[sec["title"]] = sec.get("content", "")

    # 按 agent 分组
    agent_titles: dict[str, list[str]] = {}
    for m in matches:
        agent_titles.setdefault(m["agent"], []).append(m["title"])

    # 从 agents.yaml 获取需要注入章节列表的 agent
    agents_cfg = load_agents(config).get("agents", {})
    chapter_list_agents = frozenset(
        name for name, info in agents_cfg.items()
        if info.get("needs_chapter_list")
    )

    # 加载已有缓存
    existing_meta = load_existing_meta(cache_dir) if cache_dir else None
    existing_agent_entries = existing_meta.get("agent_entries", []) if existing_meta else []

    new_prompts: list[dict[str, Any]] = []
    new_agent_entries: list[dict[str, Any]] = []
    kept_files: set[str] = set()
    cache_status: dict[str, str] = {}

    for agent, titles in agent_titles.items():
        cached_agent = find_cached_agent_entry(existing_agent_entries, agent, titles)

        if cached_agent and is_agent_entry_fresh(cached_agent, rules_hash, sections_hash, agent):
            restored_parts = _restore_parts_from_cache(cached_agent, max_chars)
            if restored_parts is not None:
                new_prompts.extend(restored_parts)
                new_agent_entries.append(cached_agent)
                kept_files.update(collect_kept_files(cached_agent))
                cache_status[agent] = "hit"
                logger.info("[缓存] prompt 缓存命中（agent=%s, %s 个章节）", agent, len(titles))
                continue
            warnings.append(f"缓存文件缺失（agent={agent}），将重新生成该 agent 的所有 prompt")
            cache_status[agent] = "miss"

        cache_status.setdefault(agent, "miss")
        needs_cl = agent in chapter_list_agents
        content_block, footer = paper_content_formatter(
            titles, sections_lookup, rules, sections,
            needs_chapter_list=needs_cl,
        )
        agent_prompts = build_single_prompt(
            agent, titles, rules, content_block, footer, max_chars, warnings,
        )

        parts_meta: list[dict[str, Any]] = []
        for p in agent_prompts:
            file_path = _write_prompt_file(p, cache_dir) if cache_dir else ""
            p = _finalize_prompt_entry(p, file_path, max_chars)
            new_prompts.append(p)
            p["estimated_tokens"] = len(p["prompt_text"]) // 2

            part_meta: dict[str, Any] = {
                "chapter_titles": p["chapter_titles"],
                "file_path": file_path,
            }
            if p.get("split_index") is not None:
                part_meta["split_index"] = p["split_index"]
                part_meta["split_total"] = p["split_total"]
            if p.get("_content_path"):
                part_meta["_content_path"] = p["_content_path"]
            parts_meta.append(part_meta)
            kept_files.add(file_path)

        agent_entry = {
            "agent": agent,
            "titles": sorted(titles),
            "fingerprint": {
                "rules_sha256": rules_hash,
                "sections_sha256": sections_hash,
            },
            "parts": parts_meta,
        }
        new_agent_entries.append(agent_entry)
        kept_files.update(collect_kept_files(agent_entry))

    if cache_dir:
        _write_cache_meta(
            cache_dir, new_agent_entries, len(rules), paper_name, warnings,
            rules_hash, sections_hash,
        )
        kept_files.add(str((cache_dir / "_prompts" / "_meta.json").resolve()))
        cleanup_orphaned_files(cache_dir, kept_files)

    total_input_chars = sum(len(p["prompt_text"]) for p in new_prompts)
    result = {
        "prompts": new_prompts,
        "total_agent_calls": len(new_prompts),
        "rules_chars": len(rules),
        "paper_name": paper_name,
        "warnings": warnings,
        "cache": cache_status,
        "stats": {
            "total_input_chars": total_input_chars,
            "total_estimated_tokens": total_input_chars // 2,
        },
    }

    if args.output:
        write_json(Path(args.output), result)
        logger.info("已保存: %s（%s 条 prompt）", args.output, len(new_prompts))
    elif paper_name:
        output_path = config.cache_dir(paper_name) / "prompts.json"
        write_json(output_path, result)
        logger.info("已保存: %s（%s 条 prompt）", output_path, len(new_prompts))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
