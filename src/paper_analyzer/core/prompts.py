#!/usr/bin/env python3
"""Prompt 构造引擎 —— 论文内容的格式化层 + 通用 Prompt 组装/拆分。

合并自原 invoke/prompts.py（论文专属格式化）和 common/prompt_builder.py（通用拼接/拆分）。

用法:
    from paper_analyzer.core.prompts import build_all_prompts
"""

from typing import Any


# ══════════════════════════════════════════════════════════════════════════
# 1. 章节块构建（论文专属格式化）
# ══════════════════════════════════════════════════════════════════════════

def build_section_block(titles: list[str], sections_lookup: dict[str, str]) -> str:
    """根据章节数量构造章节信息块。

    单章节 →「**章节标题**: ... **章节内容**: ...」
    多章节 →「**本分析包含以下 N 个章节（已合并）** ...」
    """
    if len(titles) == 1:
        title = titles[0]
        content = sections_lookup.get(title, "")
        return f"**章节标题**: {title}\n\n**章节内容**:\n{content}"

    lines = [f"**本分析包含以下 {len(titles)} 个章节（已合并）**："]
    for i, t in enumerate(titles, 1):
        lines.append(f"{i}. {t}")
    lines.append("")
    lines.append("---")
    for t in titles:
        content = sections_lookup.get(t, "")
        lines.append("")
        lines.append(f"## {t}")
        lines.append("")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


def build_chapter_title_list(sections: list[dict[str, Any]]) -> str:
    """生成完整章节标题列表（供 preliminary-info-analyzer 使用）。"""
    lines = [
        "**论文完整章节标题列表**（由 split 从论文中切分提取。"
        "⚠️ 此列表为论文一级章节的完整穷举，"
        "**禁止**在目录结构中添加此列表之外的任何条目——"
        "包括但不限于附录、补充材料、数据可用性声明、参考文献等）:"
    ]
    for i, sec in enumerate(sections, 1):
        lines.append(f"{i}. {sec['title']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 2. 论文内容格式化
# ══════════════════════════════════════════════════════════════════════════

def paper_content_formatter(
    titles: list[str],
    sections_lookup: dict[str, str],
    rules: str,
    all_sections: list[dict[str, Any]],
    needs_chapter_list: bool = False,
    custom_footer: str | None = None,
) -> tuple[str, str]:
    """将论文数据转换为通用 prompt 构造器需要的 content_block + footer。

    Args:
        custom_footer: 自定义 footer 文本（覆盖默认的"请按要求分析..."）

    Returns:
        (content_block, footer)
    """
    section_block = build_section_block(titles, sections_lookup)

    if custom_footer is not None:
        footer = custom_footer
    else:
        footer_parts = ["", "---", "", "请按照你定义的分析框架，对以上章节内容进行分析，输出结构化的分析报告。"]
        footer = "\n".join(footer_parts)

    if needs_chapter_list:
        title_list = build_chapter_title_list(all_sections)
        footer += "\n\n" + title_list

    content_block = section_block
    return content_block, footer


# ══════════════════════════════════════════════════════════════════════════
# 3. 通用 Prompt 组装（原 prompt_builder.py）
# ══════════════════════════════════════════════════════════════════════════

def assemble_prompt_parts(
    rules: str,
    content_block: str,
    footer: str,
) -> dict[str, str]:
    """将规则、内容、指令组装为 prompt 的三部分。

    Returns:
        {full, skeleton, content_block, footer}
    """
    skeleton_parts = [rules, "", "---", ""]
    skeleton = "\n".join(skeleton_parts)
    full = skeleton + "\n" + content_block + "\n" + footer

    return {
        "full": full,
        "skeleton": skeleton,
        "content_block": content_block,
        "footer": footer,
    }


def _make_prompt_dict(
    agent: str,
    prompt_text: str,
    titles: list[str],
    split_index: int | None = None,
    split_total: int | None = None,
) -> dict[str, Any]:
    """构建单个 prompt 条目的 dict。"""
    entry: dict[str, Any] = {
        "agent": agent,
        "prompt_text": prompt_text,
        "total_chars": len(prompt_text),
        "chapter_titles": titles,
    }
    if split_index is not None:
        entry["split_index"] = split_index
    if split_total is not None:
        entry["split_total"] = split_total
    return entry


# ══════════════════════════════════════════════════════════════════════════
# 4. 递归二分拆分
# ══════════════════════════════════════════════════════════════════════════

def build_single_prompt(
    agent: str,
    titles: list[str],
    rules: str,
    content_block: str,
    footer: str,
    max_chars: int,
    warnings: list[str] | None = None,
    content_block_rebuilder: Any | None = None,
) -> list[dict[str, Any]]:
    """为一个 agent 的一组内容构建 prompt，超长时递归二分拆分。

    Args:
        agent:                  标识名
        titles:                 内容项标题列表
        rules:                  规则全文
        content_block:          已构建好的内容块文本
        footer:                 指令文本
        max_chars:              拆分阈值
        warnings:               告警列表（可选，超限时追加）
        content_block_rebuilder: 可选，接受 titles 子集 → 返回新的 content_block 的可调用对象
                                 递归拆分时用于为每个子集重建内容块

    Returns:
        1 条或多条 prompt dict 的列表
    """
    if warnings is None:
        warnings = []

    parts = assemble_prompt_parts(rules, content_block, footer)
    prompt_text = parts["full"]

    # 不超限 → 直接返回
    if len(prompt_text) <= max_chars:
        return [_make_prompt_dict(agent, prompt_text, titles)]

    # 超限且单章 → 无法拆分，直接返回（内容已完整在 prompt_text 中）
    if len(titles) == 1:
        return [_make_prompt_dict(agent, prompt_text, titles)]

    # 超限且多章 → 递归二分拆分
    mid = len(titles) // 2

    left_titles = titles[:mid]
    right_titles = titles[mid:]

    # 为每个子集重建 content_block（避免所有分片共用同一份完整内容）
    left_block = content_block_rebuilder(left_titles) if content_block_rebuilder else content_block
    right_block = content_block_rebuilder(right_titles) if content_block_rebuilder else content_block

    left = build_single_prompt(
        agent, left_titles, rules, left_block, footer, max_chars, warnings,
        content_block_rebuilder,
    )
    right = build_single_prompt(
        agent, right_titles, rules, right_block, footer, max_chars, warnings,
        content_block_rebuilder,
    )

    total = len(left) + len(right)
    if total > 1:
        for i, p in enumerate(left + right):
            p["split_index"] = i
            p["split_total"] = total

    return left + right


# ══════════════════════════════════════════════════════════════════════════
# 5. 主入口
# ══════════════════════════════════════════════════════════════════════════

def build_all_prompts(
    sections: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    rules: str,
    max_chars: int,
    paper_name: str = "",
    chapter_list_agents: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """为每个 agent 构造完整 prompt，超限时自动拆分。

    Args:
        sections:   章节列表 [{title, content}, ...]
        matches:    匹配列表 [{title, agent, char_count}, ...]
        rules:      规则文件拼接后的全文
        max_chars:  prompt 拆分阈值
        paper_name: 论文名（透传到输出）
        chapter_list_agents: 需要注入完整章节标题列表的 agent 名称集合

    Returns:
        {prompts: [...], rules_chars, paper_name, warnings: [...]}
    """
    warnings: list[str] = []

    if not rules.strip():
        warnings.append("规则文件列表为空或全部缺失，prompt 中将缺少格式约束")

    # 建立 title → content 查找表
    sections_lookup: dict[str, str] = {}
    for sec in sections:
        sections_lookup[sec["title"]] = sec.get("content", "")

    # 按 agent 分组 matches
    agent_titles: dict[str, list[str]] = {}
    for m in matches:
        agent_titles.setdefault(m["agent"], []).append(m["title"])

    if not agent_titles:
        warnings.append("matches 为空，没有章节匹配到任何 agent")

    prompts: list[dict[str, Any]] = []

    for agent, titles in agent_titles.items():
        needs_cl = agent in chapter_list_agents
        content_block, footer = paper_content_formatter(
            titles, sections_lookup, rules, sections,
            needs_chapter_list=needs_cl,
        )
        agent_prompts = build_single_prompt(
            agent, titles, rules, content_block, footer, max_chars, warnings,
        )
        prompts.extend(agent_prompts)

    return {
        "prompts": prompts,
        "rules_chars": len(rules),
        "paper_name": paper_name,
        "warnings": warnings,
    }
