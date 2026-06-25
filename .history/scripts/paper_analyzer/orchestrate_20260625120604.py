#!/usr/bin/env python3
"""编排入口：PDF 转换 → 切分 → 路由 → 统一输出。

本模块是流程协调者：决定「先做什么、后做什么、结果存哪」。
使用 paper_analyzer 独立包的 Config 对象管理所有路径。

用法:
    python scripts/paper_analyzer/orchestrate.py <文件路径> [--section <关键词>] [--agent <agent名>]

输入路径可以是：
  - .txt 文件（已有文本）
  - .pdf 文件（自动调用 pdftotext 转换）
  - 仅文件名（自动在 output/<文件名>/cache/ 下查找对应 .txt）

缓存策略：
  - pdftotext + split：mtime 缓存
  - route：展示旧结果 + 始终刷新匹配
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.config_loader import (
    load_settings, get_split_patterns, get_routing_rules,
)
from paper_analyzer.adapters.pdf import extract_text, ensure_pdftotext
from paper_analyzer.cache import mtime_fresh, config_changed
from paper_analyzer.core.split import (
    detect_style, find_cn_headings, find_en_headings, split_text,
)
from paper_analyzer.core.route import (
    filter_sections, match_chapters,
    build_filter_description, build_match_report, summarize_matches,
)
from paper_analyzer.errors import PaperAnalyzerError, PdfExtractionError, ToolNotFoundError

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────
# split.yaml 路径统一由 config.py 管理，不再在此硬编码

def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数（中英文混合场景，1 token ≈ 2 字符）。"""
    return len(text) // 2

# ══════════════════════════════════════════════════════════════════════════
# 0. 工具函数
# ══════════════════════════════════════════════════════════════════════════

def _normalize_paper_name(name: str) -> str:
    """将 paper_name 中的路径不安全字符统一替换为下划线。

    替换对象：空格、连字符、点号、逗号、分号、冒号、叹号、问号、
             &、括号（中英文）、方括号、花括号。
    连续多个替换符折叠为一个下划线，去掉首尾下划线。

    Examples:
        "My Paper v2"           → "My_Paper_v2"
        "does-hawkish-doveish"  → "does_hawkish_doveish"
        "Smith & Jones (2024)"  → "Smith_Jones_2024"
        "paper_final (3)"       → "paper_final_3"
    """
    sanitized = re.sub(r"[\s\-.,;:!?&()\[\]{}（）【】]+", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


# ══════════════════════════════════════════════════════════════════════════
# 1. 输入解析
# ══════════════════════════════════════════════════════════════════════════

def resolve_input(user_input: str, config: Config) -> tuple[Path, Path | None]:
    """解析用户输入，返回 (txt_path, pdf_path_or_none)。"""
    input_path = Path(user_input)

    if input_path.suffix.lower() == ".txt" and input_path.exists():
        return input_path, None

    if input_path.suffix.lower() == ".pdf":
        if not input_path.exists():
            logger.error("PDF 文件不存在 —— %s", input_path)
            sys.exit(1)
        txt_path, _ = _convert_pdf(input_path, config)
        return txt_path, input_path

    stem = _normalize_paper_name(input_path.stem)
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
            txt_path, _ = _convert_pdf(candidate, config)
            return txt_path, candidate

    logger.error("无法找到文件 '%s'", user_input)
    logger.error("   已尝试: %s", ', '.join(str(t) for t in tried))
    sys.exit(1)


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
# 2. PDF 转换（策略 A：内容寻址缓存）
# ══════════════════════════════════════════════════════════════════════════

def _convert_pdf(pdf_path: Path, config: Config) -> tuple[Path, str]:
    """PDF → TXT，基于 mtime 判断缓存。"""
    paper_name = _normalize_paper_name(pdf_path.stem)
    txt_path = config.txt_path(paper_name)

    if txt_path.exists() and txt_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        logger.info("[缓存] 文本缓存新鲜，跳过提取: %s", txt_path)
        return txt_path, "hit"

    try:
        ensure_pdftotext()
    except ToolNotFoundError as e:
        logger.error("错误: %s", e)
        sys.exit(1)

    logger.info("[PDF] 检测到 PDF，正在提取文本: %s", pdf_path.name)
    try:
        text = extract_text(pdf_path)
    except PdfExtractionError as e:
        logger.error("错误：PDF 提取失败 —— %s", e)
        sys.exit(1)

    if not text.strip():
        logger.warning("警告：提取到的文本为空（可能是扫描版 PDF）")

    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(text, encoding="utf-8")

    lines = text.count("\n") + 1
    logger.info("[完成] 文本提取完成 → %s（%s 行，%s 字符）", txt_path, f"{lines:,}", f"{len(text):,}")
    return txt_path, "miss"


# ══════════════════════════════════════════════════════════════════════════
# 3. 切分（mtime 缓存）
# ══════════════════════════════════════════════════════════════════════════

def run_split(
    txt_path: Path,
    pdf_path: Path | None = None,
) -> tuple[list[dict[str, Any]], Path, str]:
    """切分论文文本，返回 (sections, json_path)。

    缓存策略：mtime 比较（txt 未更新则复用 sections）。
    split.yaml 变更时强制重切。

    流程：
      1. mtime 判断：sections 存在且比 txt 新且 split.yaml 未变更 → 复用
      2. 缓存未命中 → detect_style → find_headings → split_text → 保存
    """
    paper_name = txt_path.stem
    json_path = get_sections_path(paper_name)

    # ── 缓存判断 ──
    if json_path.exists():
        if mtime_fresh(json_path, txt_path) and not config_changed(get_split_config_path(), json_path):
            sections = json.loads(json_path.read_text(encoding="utf-8"))
            print(f"[缓存] sections 缓存新鲜，跳过切分: {json_path}（{len(sections)} 个章节）", file=sys.stderr)
            return sections, json_path, "hit"

    # ── 执行切分 ──
    patterns = get_split_patterns()
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
        print("警告：未检测到一级标题，整篇作为「全文」处理", file=sys.stderr)
        sections = [{"title": "全文", "content": text.strip()}]
    else:
        sections = split_text(text, headings, patterns["min_pre_content_chars"])

    # 保存
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(sections, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return sections, json_path, "miss"


# ══════════════════════════════════════════════════════════════════════════
# 4. 路由匹配（策略 B：展示历史 + 始终刷新）
# ══════════════════════════════════════════════════════════════════════════

def _show_previous_routing(paper_name: str) -> None:
    """展示旧路由缓存摘要（仅供用户参照，不阻塞流程）。

    读取 _routing_auto.json（编排器自动匹配的历史记录）。
    旧缓存损坏或不存在时静默跳过。
    """
    routing_path = get_routing_auto_path(paper_name)
    if not routing_path.exists():
        return

    try:
        data = json.loads(routing_path.read_text(encoding="utf-8"))
        # 兼容外层包装格式（orchestrator 输出）
        routing = data.get("routing", data)
        prev_matches = routing.get("matches", [])
        prev_agents = list(dict.fromkeys(m["agent"] for m in prev_matches))
        prev_total = routing.get("total_chapters", 0)
        prev_unmatched = routing.get("unmatched", [])

        print(f"[历史] 上次匹配结果（仅供参照，将执行新匹配）:", file=sys.stderr)
        if prev_matches:
            summary = summarize_matches(prev_matches, prev_agents, prev_total)
            print(f"   {summary}", file=sys.stderr)
        if prev_unmatched:
            unmatched_titles = [u["title"] for u in prev_unmatched]
            print(f"   上次未匹配: {', '.join(unmatched_titles)}", file=sys.stderr)
    except (json.JSONDecodeError, OSError, KeyError):
        pass  # 旧缓存损坏，静默跳过


def _run_routing(
    sections: list[dict[str, Any]],
    section_filters: list[str] | None,
    agent_filters: list[str] | None,
    paper_name: str,
) -> dict[str, Any]:
    """执行路由匹配 + 保存缓存。

    流程（策略 B）：
      1. 展示旧路由缓存摘要
      2. 从 config 获取最新的路由规则
      3. 执行过滤 + 匹配
      4. 生成报告
      5. 写入新 _routing.json
    """
    # ── 展示历史（策略 B） ──
    _show_previous_routing(paper_name)

    # ── 获取最新路由规则 ──
    routes, aliases = get_routing_rules()
    has_user_filters = bool(section_filters or agent_filters)

    # ── 执行匹配 ──
    filtered = filter_sections(sections, section_filters)
    matches, unmatched = match_chapters(
        filtered, routes, aliases, agent_filters,
        has_user_filters=has_user_filters,
    )
    matched_agents = list(dict.fromkeys(m["agent"] for m in matches))

    # ── 生成报告 ──
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
        "report_markdown": report,
    }

    # ── 保存到 _routing_auto.json（纯历史记录，供下次展示参考） ──
    routing_auto_path = get_routing_auto_path(paper_name)
    routing_auto_path.parent.mkdir(parents=True, exist_ok=True)
    routing_auto_path.write_text(
        json.dumps(routing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return routing


# ══════════════════════════════════════════════════════════════════════════
# 5. 主入口
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="论文分析编排器：PDF 转换 → 切分 → 路由 → 统一输出"
    )
    parser.add_argument("file", help="输入文件路径（.txt / .pdf / 仅文件名）")
    parser.add_argument(
        "--section", "-s", action="append", default=None,
        help="只分析标题命中关键词的章节（可多次指定，OR 逻辑）",
    )
    parser.add_argument(
        "--agent", "-a", action="append", default=None,
        help="只调用指定的 agent（可多次指定，支持路由器别名展开）",
    )
    args = parser.parse_args()

    # ── Step 1: 输入解析 ──
    txt_path, pdf_path = resolve_input(args.file)
    paper_name = txt_path.stem

    # ── Step 2: 切分（策略 A：内容寻址缓存 / mtime 回退） ──
    sections, sections_json_path, sections_cache = run_split(txt_path, pdf_path)
    pdf_cache = "fresh" if pdf_path is None else sections_cache
    print(f"[切分] 完成: {len(sections)} 个章节 → {sections_json_path}", file=sys.stderr)

    # ── Step 3: 路由匹配（策略 B：展示历史 + 始终刷新） ──
    routing = _run_routing(
        sections, args.section, args.agent, paper_name,
    )
    print(f"[匹配] 完成: {len(routing['matches'])} 条匹配", file=sys.stderr)

    # ── Step 4: 汇总输出 ──
    txt_text = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
    result = {
        "paper_name": paper_name,
        "txt_path": str(txt_path),
        "sections_path": str(sections_json_path),
        "output_path": str(get_analysis_path(paper_name)),
        "total_chapters": len(sections),
        "cache": {
            "pdf_to_txt": pdf_cache,
            "sections": sections_cache,
        },
        "stats": {
            "total_chars": len(txt_text),
            "estimated_tokens": _estimate_tokens(txt_text),
        },
        "routing": routing,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
