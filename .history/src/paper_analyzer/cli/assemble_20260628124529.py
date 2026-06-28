#!/usr/bin/env python3
"""paper-assemble: Agent 输出 → 最终报告

用法:
    paper-assemble orchestrator_result.json -o report.md
    paper-assemble orchestrator_result.json --outputs-dir _contents/
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
    get_agent_section_map, get_agent_canonical_order, unwrap_routing,
)
from paper_analyzer.core.report import assemble_report
from paper_analyzer.errors import PaperAnalyzerError

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-assemble: 汇总 Agent 输出生成分析报告"
    )
    parser.add_argument("orchestrator_json", help="编排器输出的 JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出报告路径（默认 output/<论文名>/<论文名>_analysis.md）")
    parser.add_argument("--outputs-dir", default=None, help="agent 输出文件所在目录")
    parser.add_argument("--project-root", default=None, help="项目根目录")
    parser.add_argument("--config-dir", default=None, help="自定义配置目录")
    parser.add_argument("--output-dir", default=None, help="输出根目录")
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


def _load_agent_outputs(outputs_dir: Path) -> tuple[dict[str, str], list[Path]]:
    """读取所有 agent 输出，自动合并拆分分片。

    Returns:
        (agent名 → 输出正文, 待清理的分片路径列表)
    """
    if not outputs_dir.exists():
        return {}, []

    # 检测并合并分片
    part_files: dict[str, list[Path]] = {}
    for fpath in outputs_dir.glob("*_part*.md"):
        stem = fpath.stem
        if "_part" in stem:
            agent_name = stem[:stem.rindex("_part")]
            part_files.setdefault(agent_name, []).append(fpath)

    cleanup_parts: list[Path] = []
    for agent_name, paths in part_files.items():
        if len(paths) <= 1:
            continue
        paths.sort(key=lambda p: int(p.stem.split("_part")[-1]))
        parts = [p.read_text(encoding="utf-8").strip() for p in paths]
        merged = "\n\n---\n\n".join(parts)
        merged_path = outputs_dir / f"{agent_name}.md"
        merged_path.write_text(merged, encoding="utf-8")
        cleanup_parts.extend(paths)

    # 读取所有 agent 输出
    result: dict[str, str] = {}
    for fpath in sorted(outputs_dir.glob("*.md")):
        if "_part" in fpath.stem:
            continue
        agent_name = fpath.stem
        result[agent_name] = fpath.read_text(encoding="utf-8")
    return result, cleanup_parts


def _find_sections_path(
    orch_data: dict[str, Any],
    orchestrator_json_path: Path,
    paper_name: str,
) -> tuple[Path | None, str]:
    """逐层尝试定位 sections.json。"""
    cache_dir = orchestrator_json_path.parent

    if "sections_path" in orch_data:
        p = Path(orch_data["sections_path"])
        if p.exists():
            return p, paper_name

    if paper_name and paper_name != "unknown":
        p = cache_dir / f"{paper_name}_sections.json"
        if p.exists():
            return p, paper_name

    inferred = cache_dir.parent.name
    p = cache_dir / f"{inferred}_sections.json"
    if p.exists():
        return p, inferred

    return None, paper_name


# ══════════════════════════════════════════════════════════════════════════
# 辅助：章节结构概览 & 报告验证
# ══════════════════════════════════════════════════════════════════════════

def _build_chapter_structure(
    sections: list[dict[str, Any]],
    routing: dict[str, Any],
) -> str:
    """生成「章节结构」概览部分。"""
    matches = routing.get("matches", [])
    unmatched = routing.get("unmatched", [])

    title_to_agents: dict[str, list[str]] = {}
    for m in matches:
        title_to_agents.setdefault(m["title"], []).append(m["agent"])

    unmatched_titles = {u["title"] for u in unmatched}

    lines = []
    for sec in sections:
        title = sec["title"]
        agents = title_to_agents.get(title, [])

        if agents:
            agent_names = ", ".join(a for a in agents)
            lines.append(f"- {title} → {agent_names}")
        elif title in unmatched_titles:
            lines.append(f"- {title} → （跳过）")

    return "\n".join(lines)


def _validate_report(
    report: str,
    expected_anchors: set[str],
    expected_labels: set[str],
    min_chars: int = 500,
) -> list[str]:
    """HTML 锚点完整性 + 长度 + 标签检查。"""
    issues: list[str] = []

    present = set(re.findall(r'<!-- agent_section: (.+?) -->', report))
    missing = expected_anchors - present
    extra = present - expected_anchors
    for m in missing:
        issues.append(f"报告中缺少区段标记: {m}")
    for e in extra:
        issues.append(f"报告中出现未预期的区段标记: {e}")

    if len(report) < min_chars:
        issues.append(f"报告内容过短（{len(report)} 字符），可能不完整")

    for label in expected_labels:
        if label and label not in report:
            issues.append(f"缺少区段标题: {label}")

    return issues


def _run(args: argparse.Namespace, config: Config) -> None:
    orchestrator_json_path = Path(args.orchestrator_json)
    if not orchestrator_json_path.exists():
        logger.error("错误：编排器输出 JSON 不存在 —— %s", orchestrator_json_path)
        sys.exit(1)

    with open(orchestrator_json_path, encoding="utf-8") as f:
        orch_data = json.load(f)

    routing = unwrap_routing(orch_data)
    paper_name = orch_data.get("paper_name", "unknown")

    sections_path, paper_name = _find_sections_path(
        orch_data, orchestrator_json_path, paper_name,
    )

    if sections_path is None:
        logger.error("错误：无法定位 sections.json")
        sys.exit(1)

    with open(sections_path, encoding="utf-8") as f:
        sections = json.load(f)

    if args.outputs_dir:
        outputs_dir = Path(args.outputs_dir)
    else:
        outputs_dir = config.outputs_dir(paper_name)

    agent_outputs, cleanup_parts = _load_agent_outputs(outputs_dir)

    if not agent_outputs:
        logger.warning("警告：未找到任何 agent 输出文件")
        logger.warning("   预期位置: %s", outputs_dir)

    agent_section_map = get_agent_section_map(config)
    agent_canonical_order = get_agent_canonical_order(config)

    sorted_agents = sorted(
        agent_outputs.keys(),
        key=lambda a: agent_canonical_order.get(a, 99999),
    )

    section_blocks: list[dict[str, Any]] = []
    for agent in sorted_agents:
        section_blocks.append({
            "label": agent_section_map.get(agent, agent),
            "anchor": agent,
            "content": agent_outputs[agent],
        })

    base_info = "## 章节结构\n" + _build_chapter_structure(sections, routing)
    report = assemble_report(
        title=f"# 论文分析报告: {paper_name}",
        section_blocks=section_blocks,
        base_info=base_info,
    )

    issues = _validate_report(
        report,
        expected_anchors=set(agent_outputs.keys()),
        expected_labels={agent_section_map.get(a, a) for a in agent_outputs},
    )
    if issues:
        logger.warning("⚠️  自检发现问题:")
        for issue in issues:
            logger.warning("  - %s", issue)
    else:
        logger.info("✅ 自检通过")

    # 保存报告
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = config.analysis_path(paper_name)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    # 清理分片文件
    for p in cleanup_parts:
        try:
            p.unlink()
        except OSError:
            pass

    result = {
        "output_path": str(output_path),
        "paper_name": paper_name,
        "lines": report.count("\n") + 1,
        "chars": len(report),
        "agents_included": sorted_agents,
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
