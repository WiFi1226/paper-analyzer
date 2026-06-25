#!/usr/bin/env python3
"""编排入口：PDF 转换 → 切分 → 路由 → 统一输出。

本模块是流程协调者：决定「先做什么、后做什么、结果存哪」。
使用 paper_analyzer 独立包的 Config 对象管理所有路径。

用法:
    paper-orchestrate <文件路径> [--section <关键词>] [--agent <agent名>]

输入路径可以是：
  - .txt 文件（已有文本）
  - .pdf 文件（自动调用 pdftotext 转换）
  - 仅文件名（自动在 output/<文件名>/cache/ 下查找对应 .txt）
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.config_loader import (
    load_settings, get_split_patterns, get_routing_rules,
)
from paper_analyzer.adapters.pdf import extract_text, ensure_pdftotext

from paper_analyzer.core.split import (
    detect_style, find_cn_headings, find_en_headings, split_text,
)
from paper_analyzer.core.route import (
    filter_sections, match_chapters,
    build_filter_description, build_match_report,
)
from paper_analyzer.errors import PaperAnalyzerError

from paper_analyzer.io import normalize_paper_name, write_json

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 1. 输入解析
# ══════════════════════════════════════════════════════════════════════════

def resolve_input(user_input: str, config: Config) -> tuple[Path, Path | None]:
    """解析用户输入，返回 (txt_path, pdf_path_or_none)。

    Raises:
        PaperAnalyzerError: 文件不存在或无法找到
    """
    input_path = Path(user_input)

    if input_path.suffix.lower() == ".txt" and input_path.exists():
        return input_path, None

    if input_path.suffix.lower() == ".pdf":
        if not input_path.exists():
            raise PaperAnalyzerError(f"PDF 文件不存在 —— {input_path}")
        txt_path = _convert_pdf(input_path, config)
        return txt_path, input_path

    stem = normalize_paper_name(input_path.stem)
    cached_txt = config.txt_path(stem)
    if cached_txt.exists():
        pdf_path = _find_original_pdf(cached_txt, config)
        return cached_txt, pdf_path

    settings = load_settings(config)
    search_paths = settings.get("pdf_search_paths") or []
    tried = [input_path, cached_txt]
    for sp in search_paths:
        candidate = config.project_root / sp / f"{stem}.pdf"
        tried.append(candidate)
        if candidate.exists():
            txt_path = _convert_pdf(candidate, config)
            return txt_path, candidate

    raise PaperAnalyzerError(
        f"无法找到文件 '{user_input}'（已尝试: {', '.join(str(t) for t in tried)}）"
    )


def _find_original_pdf(txt_path: Path, config: Config) -> Path | None:
    """尝试在 pdf_search_paths 中查找 txt 对应的原始 PDF。"""
    settings = load_settings(config)
    search_paths = settings.get("pdf_search_paths") or []
    paper_name = txt_path.stem
    for sp in search_paths:
        candidate = config.project_root / sp / f"{paper_name}.pdf"
        if candidate.exists():
            return candidate
    return None


# ══════════════════════════════════════════════════════════════════════════
# 2. PDF 转换
# ══════════════════════════════════════════════════════════════════════════

def _convert_pdf(pdf_path: Path, config: Config) -> Path:
    """PDF → TXT。

    Raises:
        ToolNotFoundError: pdftotext 未安装
        PdfExtractionError: PDF 提取失败
    """
    paper_name = normalize_paper_name(pdf_path.stem)
    txt_path = config.txt_path(paper_name)

    ensure_pdftotext()

    logger.info("[PDF] 检测: %s", pdf_path.name)
    text = extract_text(pdf_path)

    if not text.strip():
        logger.warning("警告：提取到的文本为空（可能是扫描版 PDF）")

    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(text, encoding="utf-8")

    lines = text.count("\n") + 1
    logger.info("[PDF] 提取: → %s（%s 行，%s 字符）", txt_path, f"{lines:,}", f"{len(text):,}")
    return txt_path


# ══════════════════════════════════════════════════════════════════════════
# 3. 切分
# ══════════════════════════════════════════════════════════════════════════

def run_split(
    txt_path: Path,
    config: Config,
) -> tuple[list[dict[str, Any]], Path]:
    """切分论文文本，返回 (sections, json_path)。"""
    paper_name = txt_path.stem
    json_path = config.sections_path(paper_name)

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
            patterns["heading_dedup_distance"],
        )

    if not headings:
        logger.warning("未检测到一级标题，整篇作为「全文」处理")
        sections = [{"title": "全文", "content": text.strip()}]
    else:
        sections = split_text(text, headings, patterns["min_pre_content_chars"])

    write_json(json_path, sections)

    return sections, json_path


# ══════════════════════════════════════════════════════════════════════════
# 4. 路由匹配
# ══════════════════════════════════════════════════════════════════════════

def _run_routing(
    sections: list[dict[str, Any]],
    section_filters: list[str] | None,
    agent_filters: list[str] | None,
    config: Config,
    paper_name: str,
) -> tuple[dict[str, Any], Path]:
    """执行路由匹配，写入路由结果，返回 (routing, routing_path)。"""

    routes, aliases = get_routing_rules(config)
    has_user_filters = bool(section_filters or agent_filters)

    filtered = filter_sections(sections, section_filters)
    matches, unmatched = match_chapters(
        filtered, routes, aliases, agent_filters,
        has_user_filters=has_user_filters,
    )
    matched_agents = list(dict.fromkeys(m["agent"] for m in matches))

    report = build_match_report(
        sections, matches, section_filters, agent_filters, aliases,
    )
    filter_desc = build_filter_description(section_filters, agent_filters, aliases)

    routing = {
        "matches": matches,
        "unmatched": unmatched,
        "matched_agents": matched_agents,
        "filter_applied": filter_desc,
        "total_chapters": len(sections),
        "total_matches": len(matches),
        "report_markdown": report,
    }

    routing_path = config.routing_path(paper_name)
    write_json(routing_path, routing)
    return routing, routing_path


# ══════════════════════════════════════════════════════════════════════════
# 5. 主入口
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="论文分析编排器：PDF 转换 → 切分 → 路由 → 统一输出"
    )
    parser.add_argument("file", help="输入文件路径（.txt / .pdf / 仅文件名）")
    parser.add_argument("--section", "-s", action="append", default=None,
                        help="只分析标题命中关键词的章节（可多次指定，OR 逻辑）")
    parser.add_argument("--agent", "-a", action="append", default=None,
                        help="只调用指定的 agent（可多次指定，支持路由器别名展开）")
    parser.add_argument("--project-root", default=None, help="项目根目录")
    parser.add_argument("--config-dir", default=None, help="自定义配置目录")
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")
    args = parser.parse_args()

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

    txt_path, pdf_path = resolve_input(args.file, config)
    paper_name = txt_path.stem

    sections, sections_json_path = run_split(txt_path, config)
    logger.info("[切分] 完成: %s 个章节 → %s", len(sections), sections_json_path)

    routing, routing_json_path = _run_routing(sections, args.section, args.agent, config, paper_name)
    logger.info("[匹配] 完成: %s 条匹配 → %s", routing['total_matches'], routing_json_path)

if __name__ == "__main__":
    main()
