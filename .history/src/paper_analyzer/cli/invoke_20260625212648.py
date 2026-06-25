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


# ── 超大文件占位符 ───────────────────────────────────────────────

def _truncate_if_oversized(prompt_text: str, file_path: str, max_chars: int) -> str:
    """超过 max_chars 时替换为 Read 工具占位符，否则原样返回。"""
    if len(prompt_text) > max_chars:
        return (
            f"[prompt 已保存至文件，共 {len(prompt_text)} 字符]\n"
            f"文件路径: {file_path}\n"
            "请用 Read 工具读取该文件获取完整分析任务。"
        )
    return prompt_text


# ── Prompt 缓存管理器 ─────────────────────────────────────────────

class _PromptCache:
    """管理 prompt 文件的缓存读写。

    封装所有磁盘 I/O：读取 meta.json、查找缓存条目、指纹校验、
    从文件恢复 prompt、写入 prompt/content 文件、写入元数据、清理孤儿文件。
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.prompts_root = cache_dir / "prompts"
        self.prompts_dir = self.prompts_root / "_prompts"
        self.contents_dir = self.prompts_root / "_contents"
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.contents_dir.mkdir(parents=True, exist_ok=True)

    # ── 读 ──────────────────────────────────────────────────────

    def load_meta(self) -> dict | None:
        """读取 meta.json，不存在时返回 None。"""
        return load_existing_meta(self.prompts_root / "meta.json")

    @staticmethod
    def find_cached(entries: list, agent: str, titles: list) -> dict | None:
        """在元数据 entries 中查找 agent 的缓存条目。"""
        return find_cached_agent_entry(entries, agent, titles)

    @staticmethod
    def is_fresh(entry: dict, rules_hash: str, sections_hash: str) -> bool:
        """检查缓存条目的指纹是否仍然有效。"""
        return is_agent_entry_fresh(entry, rules_hash, sections_hash, entry["agent"])

    def restore_parts(self, entry: dict, max_chars: int) -> list[dict[str, Any]] | None:
        """从磁盘文件恢复一个 agent 的所有 prompt part。

        任一文件缺失则整体返回 None，触发重新生成。
        """
        agent = entry["agent"]
        parts: list[dict[str, Any]] = []
        for part in entry.get("parts", []):
            fpath = Path(part["file_path"])
            if not fpath.exists():
                return None

            prompt_text = fpath.read_text(encoding="utf-8")
            file_path = str(fpath.resolve())

            # 推导 content 路径：prompt 文件 → 对应 content 文件
            content_path = str(self.contents_dir / fpath.name.replace("prompt_", "content_"))
            read_path = content_path if Path(content_path).exists() else file_path
            truncated = _truncate_if_oversized(prompt_text, read_path, max_chars)

            result: dict[str, Any] = {
                "agent": agent,
                "prompt_text": truncated,
                "total_chars": len(truncated),
                "chapter_titles": part.get("chapter_titles", []),
                "file_path": file_path,
            }
            if part.get("split_index") is not None:
                result["split_index"] = part["split_index"]
                result["split_total"] = part["split_total"]
            parts.append(result)
        return parts

    # ── 写 ──────────────────────────────────────────────────────

    def write_prompt(self, p: dict[str, Any]) -> str:
        """将一条 prompt 写入磁盘（含超大文件的 content 独立写入）。

        返回 prompt 文件的绝对路径。
        """
        agent: str = p["agent"]
        split_idx: int | None = p.get("split_index")
        if split_idx is not None:
            fname = f"prompt_{agent}_part{split_idx + 1}.txt"
        else:
            fname = f"prompt_{agent}.txt"
        fpath = self.prompts_dir / fname

        if p.get("content_text"):
            content_fpath = self.contents_dir / fname.replace("prompt_", "content_")
            content_fpath.write_text(p["content_text"], encoding="utf-8")
            p["__content_path__"] = str(content_fpath.resolve())

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

    def write_meta(
        self, agent_entries: list[dict[str, Any]], rules_chars: int,
        paper_name: str, warnings: list[str],
        rules_hash: str, sections_hash: str,
    ) -> None:
        """写入 meta.json 缓存元数据。"""
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
        (self.prompts_root / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    # ── 清理 ────────────────────────────────────────────────────

    @staticmethod
    def collect_kept_files(entry: dict) -> set[str]:
        """从缓存 entry 收集需要保留的文件路径集合。"""
        return collect_kept_files(entry)

    def cleanup(self, kept_files: set[str]) -> None:
        """删除 prompts 目录中不被引用的孤儿文件。"""
        for subdir in ("_prompts", "_contents"):
            d = self.prompts_root / subdir
            if not d.exists():
                continue
            for pattern in ["prompt_*.txt", "content_*.txt"]:
                for fpath in d.glob(pattern):
                    if str(fpath.resolve()) not in kept_files:
                        try:
                            fpath.unlink()
                        except OSError:
                            pass


def _run(args: argparse.Namespace, config: Config) -> None:
    # ══════════════════════════════════════════════════════════
    # 阶段 1：加载 —— 读取文件、建立缓存、计算指纹
    # ══════════════════════════════════════════════════════════
    sections_path = Path(args.sections_json)
    if not sections_path.exists():
        logger.error("错误：sections JSON 不存在 —— %s", sections_path)
        sys.exit(1)

    routing_path = Path(args.routing_json)
    if not routing_path.exists():
        logger.error("错误：routing JSON 不存在 —— %s", routing_path)
        sys.exit(1)

    max_chars = get_max_prompt_chars(config)

    with open(sections_path, encoding="utf-8") as f:
        sections = json.load(f)
    with open(routing_path, encoding="utf-8") as f:
        raw_data = json.load(f)
    logger.info("[章节] 读取:  %s 个章节 → %s", len(sections), sections_path)
    logger.info("[路径] 读取:  %s 条匹配 → %s", len(raw_data.get("matches", [])), routing_path)

    routing = unwrap_routing(raw_data)
    paper_name = raw_data.get("paper_name", "") or sections_path.parent.parent.stem
    matches = routing.get("matches", [])

    cache_dir = Path(args.cache_dir) if (args.cache_dir and args.cache_dir is not True) else None
    if cache_dir is None and paper_name:
        cache_dir = config.cache_dir(paper_name)
    cache = _PromptCache(cache_dir) if cache_dir else None

    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    rules, missing_rules = _load_rules(rules_dir, config)
    rules_hash = compute_rules_hash(rules)
    sections_hash = compute_sections_hash(sections)
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
    loaded_meta = cache.load_meta() if cache else None
    existing_agent_entries = loaded_meta.get("agent_entries", []) if loaded_meta else []

    # ══════════════════════════════════════════════════════════
    # 阶段 2：处理 —— 每个 agent 查缓存或生成新 prompt
    # ══════════════════════════════════════════════════════════
    new_prompts: list[dict[str, Any]] = []
    new_agent_entries: list[dict[str, Any]] = []
    kept_files: set[str] = set()
    cache_status: dict[str, str] = {}

    for agent, titles in agent_titles.items():
        cached_entry = cache.find_cached(existing_agent_entries, agent, titles) if cache else None

        if cached_entry is not None and cache is not None and cache.is_fresh(cached_entry, rules_hash, sections_hash):
            restored = cache.restore_parts(cached_entry, max_chars)
            if restored is not None:
                new_prompts.extend(restored)
                new_agent_entries.append(cached_entry)
                kept_files.update(cache.collect_kept_files(cached_entry))
                cache_status[agent] = "hit"
                logger.info("[缓存] 命中:  agent=%s, %s 个章节", agent, len(titles))
                continue
            warnings.append(f"缓存文件缺失（agent={agent}），将重新生成该 agent 的所有 prompt")

        cache_status[agent] = "miss"
        needs_cl = agent in chapter_list_agents
        content_block, footer = paper_content_formatter(
            titles, sections_lookup, rules, sections,
            needs_chapter_list=needs_cl, custom_footer=custom_footer,
        )
        agent_prompts = build_single_prompt(
            agent, titles, rules, content_block, footer, max_chars, warnings,
        )

        parts_meta: list[dict[str, Any]] = []
        for p in agent_prompts:
            file_path = cache.write_prompt(p) if cache else ""
            p["file_path"] = file_path
            read_path = p.get("__content_path__", file_path)
            content_path = p.pop("__content_path__", None)
            p["prompt_text"] = _truncate_if_oversized(p["prompt_text"], read_path, max_chars)
            p["total_chars"] = len(p["prompt_text"])
            new_prompts.append(p)

            part_meta: dict[str, Any] = {
                "chapter_titles": p["chapter_titles"],
                "file_path": file_path,
            }
            if p.get("split_index") is not None:
                part_meta["split_index"] = p["split_index"]
                part_meta["split_total"] = p["split_total"]
            parts_meta.append(part_meta)
            if file_path:
                kept_files.add(file_path)
            if content_path:
                kept_files.add(content_path)

        agent_entry = {
            "agent": agent,
            "titles": sorted(titles),
            "fingerprint": {"rules_sha256": rules_hash, "sections_sha256": sections_hash},
            "parts": parts_meta,
        }
        new_agent_entries.append(agent_entry)
        if cache is not None:
            kept_files.update(cache.collect_kept_files(agent_entry))

    if cache:
        cache.write_meta(new_agent_entries, len(rules), paper_name, warnings, rules_hash, sections_hash)
        kept_files.add(str((cache.prompts_root / "meta.json").resolve()))
        cache.cleanup(kept_files)

    # ══════════════════════════════════════════════════════════
    # 阶段 3：输出 —— 组装结果 JSON 并写入
    # ══════════════════════════════════════════════════════════
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

    for w in warnings:
        logger.warning("[警告] %s", w)

    if args.output:
        write_json(Path(args.output), result)
        logger.info("已保存: %s（%s 条 prompt）", args.output, len(new_prompts))
    elif paper_name:
        assert cache is not None
        output_path = cache.prompts_root / "prompts.json"
        write_json(output_path, result)
        logger.info("已保存: %s（%s 条 prompt）", output_path, len(new_prompts))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
