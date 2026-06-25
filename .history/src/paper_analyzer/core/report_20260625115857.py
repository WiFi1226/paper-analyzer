#!/usr/bin/env python3
"""报告组装 —— 通用 Markdown 组装 + HTML 锚点自检 + 论文专属章节结构概览。

合并自原 assemble/report.py（章节结构概览）和 common/report_assembler.py（通用组装/自检）。

用法:
    from paper_analyzer.core.report import assemble_report, validate_sections, build_chapter_structure
"""

import re
from typing import Any


# ══════════════════════════════════════════════════════════════════════════
# 1. 章节结构概览（论文专属）
# ══════════════════════════════════════════════════════════════════════════

def build_chapter_structure(
    sections: list[dict[str, Any]],
    routing: dict[str, Any],
) -> str:
    """生成「章节结构」概览部分。

    从 routing["matches"]（扁平列表）直接构建章节 → agent 映射。
    """
    matches = routing.get("matches", [])
    unmatched = routing.get("unmatched", [])

    # 构建 title → agents 映射
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


# ══════════════════════════════════════════════════════════════════════════
# 2. 报告组装（通用）
# ══════════════════════════════════════════════════════════════════════════

def assemble_report(
    title: str,
    section_blocks: list[dict[str, Any]],
    base_info: str = "",
) -> str:
    """组装 Markdown 报告。

    Args:
        title:          报告标题（如 "论文分析报告: xxx"）
        section_blocks: 区段块列表 [{label, anchor, content}, ...]
        base_info:      报告前导信息（如章节结构概述）

    Returns:
        完整 Markdown 报告字符串
    """
    report_lines = [title, ""]

    if base_info:
        report_lines.extend([base_info, "", "---"])

    for block in section_blocks:
        label = block.get("label", "")
        anchor = block.get("anchor", "")
        content = block.get("content", "")

        report_lines.extend([
            "",
            f"## {label}",
            f"<!-- agent_section: {anchor} -->",
            "",
            content.strip(),
            "",
            "---",
        ])

    return "\n".join(report_lines)


# ══════════════════════════════════════════════════════════════════════════
# 3. 自检验证（通用）
# ══════════════════════════════════════════════════════════════════════════

def validate_sections(
    report: str,
    expected_anchors: set[str],
    expected_labels: set[str],
    min_chars: int = 500,
) -> list[str]:
    """HTML 锚点完整性 + 长度 + 标签检查。

    Args:
        report:          报告全文
        expected_anchors: 期望的 HTML 注释锚点集合
        expected_labels:  期望的区段标题集合
        min_chars:        最小字符数阈值

    Returns:
        问题列表（空列表 = 全部通过）
    """
    issues: list[str] = []

    # 1. 结构完整性：锚点集合对比
    present = set(re.findall(r'<!-- agent_section: (.+?) -->', report))
    missing = expected_anchors - present
    extra = present - expected_anchors
    for m in missing:
        issues.append(f"报告中缺少区段标记: {m}")
    for e in extra:
        issues.append(f"报告中出现未预期的区段标记: {e}")

    # 2. 报告长度检查
    if len(report) < min_chars:
        issues.append(f"报告内容过短（{len(report)} 字符），可能不完整")

    # 3. 区段标签检查
    for label in expected_labels:
        if label and label not in report:
            issues.append(f"缺少区段标题: {label}")

    return issues
