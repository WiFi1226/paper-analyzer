#!/usr/bin/env python3
"""Assemble 入口：读取 agent 输出 → 组装报告 → 自检验证 → 保存。

本模块是流程协调者：决定「先做什么、后做什么、结果存哪」。
使用 paper_analyzer 独立包的 Config 对象管理所有路径。

用法:
    python scripts/paper_analyzer/assemble.py <orchestrator_output.json> [--outputs-dir <dir>]

输入:
    orchestrator_output.json — orchestrator 的 stdout JSON（含 routing + sections_path + paper_name）
    --outputs-dir            — 可选，agent 输出文件所在目录（默认 output/<论文名>/cache/_outputs/）

输出:
    - 最终报告保存到 output/<论文名>/<论文名>_analysis.md
    - stdout 输出保存路径和验证结果 JSON
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.config_loader import (
    get_agent_section_map, get_agent_canonical_order, unwrap_routing,
)
from paper_analyzer.core.report import (
    build_chapter_structure, assemble_report, validate_sections,
)
from paper_analyzer.errors import PaperAnalyzerError

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 1. Agent 输出加载（含分片合并）
# ══════════════════════════════════════════════════════════════════════════

def load_agent_outputs(
    paper_name: str,
    outputs_dir_override: str | None = None,
    config: Config | None = None,
) -> tuple[dict[str, str], list[Path]]:
    """读取所有 agent 输出，自动合并拆分的分片。"""
    if outputs_dir_override:
        outputs_dir = Path(outputs_dir_override)
    elif config:
        outputs_dir = config.outputs_dir(paper_name)
    else:
        outputs_dir = Path("output") / paper_name / "cache" / "_outputs"

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
            if paths:
                logger.warning("⚠️  告警：agent=%s 仅有单个分片文件（非拆分场景），忽略 —— %s",
                               agent_name, paths[0].name)
            continue

        paths.sort(key=lambda p: int(p.stem.split("_part")[-1]))

        for p in paths:
            sz = p.stat().st_size
            if sz == 0:
                logger.warning("⚠️  告警：分片文件为空 —— %s", p)
            elif sz < 100:
                logger.warning("⚠️  告警：分片文件异常小（%s 字节）—— %s", sz, p)

        parts = [p.read_text(encoding="utf-8").strip() for p in paths]
        merged = "\n\n---\n\n".join(parts)

        merged_len = len(merged)
        if merged_len < len(paths) * 500:
            logger.warning("⚠️  告警：合并后内容偏短 —— agent=%s（%s 字符，%s 个分片）",
                           agent_name, merged_len, len(paths))

        merged_path = outputs_dir / f"{agent_name}.md"
        merged_path.write_text(merged, encoding="utf-8")
        cleanup_parts.extend(paths)

    # 读取所有 agent 输出（不含分片文件）
    result: dict[str, str] = {}
    for fpath in sorted(outputs_dir.glob("*.md")):
        if "_part" in fpath.stem:
            continue
        agent_name = fpath.stem
        result[agent_name] = fpath.read_text(encoding="utf-8")
    return result, cleanup_parts


# ══════════════════════════════════════════════════════════════════════════
# 2. sections.json 路径推断
# ══════════════════════════════════════════════════════════════════════════

def _find_sections_path(
    orch_data: dict[str, Any],
    orchestrator_json_path: Path,
    paper_name: str,
) -> tuple[Path | None, str]:
    """逐层尝试定位 sections.json，返回 (path, paper_name_used)。

    尝试顺序：
      1. orchestrator JSON 中的 sections_path 字段
      2. paper_name 已知 → 在同目录下拼接
      3. 从目录结构反推 paper_name
    """
    cache_dir = orchestrator_json_path.parent

    # 1. orchestrator JSON 中的 sections_path
    if "sections_path" in orch_data:
        p = Path(orch_data["sections_path"])
        if p.exists():
            return p, paper_name

    # 2. paper_name 已知 → 直接拼
    if paper_name and paper_name != "unknown":
        p = cache_dir / f"{paper_name}_sections.json"
        if p.exists():
            return p, paper_name

    # 3. 从目录结构反推
    inferred = cache_dir.parent.name
    p = cache_dir / f"{inferred}_sections.json"
    if p.exists():
        return p, inferred

    return None, paper_name


# ══════════════════════════════════════════════════════════════════════════
# 3. 主入口
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="汇总报告生成器")
    parser.add_argument("orchestrator_json", help="编排器输出的 JSON 文件路径")
    parser.add_argument("--outputs-dir", default=None,
                        help="agent 输出文件所在目录（可选，默认 output/<论文名>/cache/_outputs/）")
    args = parser.parse_args()

    orchestrator_json_path = Path(args.orchestrator_json)
    if not orchestrator_json_path.exists():
        print(f"错误：编排器输出 JSON 不存在 —— {orchestrator_json_path}", file=sys.stderr)
        sys.exit(1)

    with open(orchestrator_json_path, encoding="utf-8") as f:
        orch_data: dict[str, Any] = json.load(f)

    # 兼容两种格式：纯 routing 结果 vs 完整 orchestrator 输出
    routing = unwrap_routing(orch_data)
    paper_name: str = orch_data.get("paper_name", "unknown")

    # 定位 sections.json
    sections_path, paper_name = _find_sections_path(
        orch_data, orchestrator_json_path, paper_name,
    )

    if sections_path is None:
        cache_dir = orchestrator_json_path.parent
        print(f"错误：无法定位 sections.json", file=sys.stderr)
        print(f"   已尝试:", file=sys.stderr)
        print(f"   1. orchestrator JSON 中的 sections_path 字段", file=sys.stderr)
        print(f"   2. {cache_dir / f'{paper_name}_sections.json'}", file=sys.stderr)
        print(f"   3. {cache_dir / f'{cache_dir.parent.name}_sections.json'}（目录结构推断）", file=sys.stderr)
        sys.exit(1)

    with open(sections_path, encoding="utf-8") as f:
        sections: list[dict[str, Any]] = json.load(f)

    # 读取 agent 输出（含分片合并）
    agent_outputs, cleanup_parts = load_agent_outputs(paper_name, args.outputs_dir)

    if not agent_outputs:
        print("警告：未找到任何 agent 输出文件", file=sys.stderr)
        print(f"   预期位置: output/{paper_name}/cache/_outputs/", file=sys.stderr)

    # 获取 agent 区段映射和排序（从 config 一次性获取）
    agent_section_map = get_agent_section_map()
    agent_canonical_order = get_agent_canonical_order()

    # 组装报告（调用通用 engine）
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

    base_info = "## 章节结构\n" + build_chapter_structure(sections, routing)
    report: str = assemble_report(
        title=f"# 论文分析报告: {paper_name}",
        section_blocks=section_blocks,
        base_info=base_info,
    )

    # 自检（调用通用 engine）
    issues: list[str] = validate_sections(
        report,
        expected_anchors=set(agent_outputs.keys()),
        expected_labels={agent_section_map.get(a, a) for a in agent_outputs},
    )
    if issues:
        print("⚠️  自检发现问题:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
    else:
        print("✅ 自检通过", file=sys.stderr)

    # 保存到 output/<论文名>/<论文名>_analysis.md
    paper_dir: Path = get_output_dir() / paper_name
    paper_dir.mkdir(parents=True, exist_ok=True)
    output_path: Path = paper_dir / f"{paper_name}_analysis.md"
    output_path.write_text(report, encoding="utf-8")

    # 报告保存成功后清理分片文件（改项 6：延迟清理）
    for p in cleanup_parts:
        try:
            p.unlink()
        except OSError:
            pass

    # 输出结果
    result: dict[str, Any] = {
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
