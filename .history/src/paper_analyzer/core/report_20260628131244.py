"""报告组装 —— 通用 Markdown 组装。

用法:
    from paper_analyzer.core.report import assemble_report
"""

from typing import Any

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


