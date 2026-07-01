#!/usr/bin/env python3
"""章节 → Agent 路由器：对章节标题做关键词全量匹配。

本模块是纯匹配引擎：所有函数接收输入、返回输出，不读盘、不写盘。
路由表和别名由调用方显式传入。

用法:
    from paper_analyzer.core.route import (
        fuzzy_match, filter_sections, match_chapters,
        build_filter_description, build_match_report,
    )

    filtered = filter_sections(sections, section_filters)
    matches, unmatched = match_chapters(filtered, routes, aliases, agent_filters)
    report = build_match_report(sections, matches, section_filters, agent_filters, aliases)
"""

from typing import Any


# ══════════════════════════════════════════════════════════════════════════
# 1. 基础匹配
# ══════════════════════════════════════════════════════════════════════════

def fuzzy_match(title: str, keywords: list[str]) -> bool:
    """章节标题是否包含任一关键词（大小写不敏感）。"""
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


# ══════════════════════════════════════════════════════════════════════════
# 2. 过滤
# ══════════════════════════════════════════════════════════════════════════

def filter_sections(
    sections: list[dict[str, Any]],
    section_filters: list[str] | None,
) -> list[dict[str, Any]]:
    """按 --section 参数过滤章节（任一关键词命中即保留）。

    Args:
        sections:       章节列表 [{title, content}, ...]
        section_filters: --section 参数值列表，None 或空 list 表示不过滤

    Returns:
        过滤后的章节列表。
    """
    if not section_filters:
        return sections
    return [s for s in sections if any(f in s["title"] for f in section_filters)]


# ══════════════════════════════════════════════════════════════════════════
# 3. 章节匹配
# ══════════════════════════════════════════════════════════════════════════

def match_chapters(
    sections: list[dict[str, Any]],
    routing_table: list[dict[str, Any]],
    agent_aliases: dict[str, list[str]],
    agent_filters: list[str] | None = None,
    has_user_filters: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """逐章节匹配路由表。

    对每章遍历路由表，标题命中关键词即记录一次匹配。
    同一章节可命中多个 agent（1:N 关系）。

    Args:
        sections:         章节列表 [{title, content}, ...]
        routing_table:    路由规则 [{agent, keywords}, ...]
        agent_aliases:    Agent 别名展开表 {alias: [agent, ...]}
        agent_filters:    --agent 参数值列表，None 表示不过滤
        has_user_filters: 是否使用了 --section 或 --agent 过滤

    Returns:
        (matches, unmatched)
        matches:   [{title, agent, char_count}, ...]
        unmatched: [{title, char_count, reason}, ...]
    """
    matches: list[dict[str, Any]] = []
    matched_titles: set[str] = set()

    # 展开 agent 过滤
    allowed_agents: set[str] | None = None
    if agent_filters:
        allowed_agents = set()
        for af in agent_filters:
            allowed_agents.update(agent_aliases.get(af, [af]))

    for sec in sections:
        title: str = sec["title"]
        content: str = sec.get("content", "")
        char_count: int = len(content)

        for entry in routing_table:
            if allowed_agents is not None and entry["agent"] not in allowed_agents:
                continue
            if fuzzy_match(title, entry["keywords"]):
                matches.append({
                    "title": title,
                    "agent": entry["agent"],
                    "char_count": char_count,
                })
                matched_titles.add(title)

    # 收集未命中章节
    unmatched: list[dict[str, Any]] = []
    for sec in sections:
        if sec["title"] not in matched_titles:
            content = sec.get("content", "")
            if has_user_filters:
                reason = "被过滤条件排除（--section 或 --agent 过滤后不匹配路由关键词，或不在过滤范围）"
            else:
                reason = "未命中路由表关键词"
            unmatched.append({
                "title": sec["title"],
                "char_count": len(content),
                "reason": reason,
            })

    return matches, unmatched


# ══════════════════════════════════════════════════════════════════════════
# 4. 报告生成
# ══════════════════════════════════════════════════════════════════════════

def build_filter_description(
    section_filters: list[str] | None,
    agent_filters: list[str] | None,
    agent_aliases: dict[str, list[str]] | None = None,
) -> str | None:
    """将过滤参数构建为人类可读的描述字符串。

    Args:
        section_filters: --section 参数值列表
        agent_filters:   --agent 参数值列表
        agent_aliases:   Agent 别名展开表

    Returns:
        如 '--section "引言" --agent paper-structure-analyzer → ...'
        None 表示无过滤。
    """
    if agent_aliases is None:
        agent_aliases = {}

    parts: list[str] = []

    if section_filters:
        parts.append("--section " + " ".join(f'"{f}"' for f in section_filters))

    if agent_filters:
        expanded_parts: list[str] = []
        for af in agent_filters:
            if af in agent_aliases:
                expanded = "、".join(agent_aliases[af])
                expanded_parts.append(f"{af} → {expanded}")
            else:
                expanded_parts.append(af)
        parts.append("--agent " + "；".join(expanded_parts))

    return " ".join(parts) if parts else None


def build_match_report(
    sections: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    section_filters: list[str] | None = None,
    agent_filters: list[str] | None = None,
    agent_aliases: dict[str, list[str]] | None = None,
) -> str:
    """生成 Markdown 格式的章节匹配报告。

    Args:
        sections:       完整章节列表
        matches:        匹配结果 [{title, agent, char_count}, ...]
        section_filters: --section 参数
        agent_filters:   --agent 参数
        agent_aliases:   Agent 别名表

    Returns:
        Markdown 格式的匹配报告字符串。
    """
    if agent_aliases is None:
        agent_aliases = {}

    # 构建标题 → 匹配的 agent 列表
    title_to_agents: dict[str, list[str]] = {}
    for m in matches:
        title_to_agents.setdefault(m["title"], []).append(m["agent"])

    # ── 过滤说明 ──
    filter_desc = build_filter_description(
        section_filters, agent_filters, agent_aliases,
    )
    filter_note = (
        f"\n> 参数过滤已应用：{filter_desc} → "
        f"上述匹配表已按参数缩小范围。\n"
        if filter_desc
        else ""
    )

    # ── 匹配表 ──
    rows: list[str] = []
    for row_idx, sec in enumerate(sections):
        title = sec["title"]
        matched = title_to_agents.get(title, [])

        if matched:
            agents_str = ", ".join(matched)
            rows.append(f"| {row_idx + 1} | {title} | {agents_str} |")
        else:
            status = (
                "[跳过] 过滤条件排除"
                if (section_filters or agent_filters)
                else "[未命中] 跳过"
            )
            rows.append(f"| {row_idx + 1} | {title} | {status} |")

    return f"""## 章节匹配结果

共识别 **{len(sections)}** 个章节，匹配情况如下：

| # | 章节标题 | 匹配 Agent |
|---|---------|-----------|
{"\n".join(rows)}

> **匹配规则**：章节标题与路由表关键词做模糊匹配。
> **未命中**的章节将被跳过，不送入任何 agent 分析。
{filter_note}"""


def summarize_matches(
    matches: list[dict[str, Any]],
    matched_agents: list[str] | None = None,
    total_chapters: int = 0,
) -> str:
    """生成匹配摘要。

    Args:
        matches:         匹配结果列表
        matched_agents:  去重后的 agent 列表（可选）
        total_chapters:  总章节数

    Returns:
        如 "6 个章节 → 3 个 agent（causal-inference, chart-results, text-results）"
    """
    if matched_agents is None:
        matched_agents = list(dict.fromkeys(m["agent"] for m in matches))
    return (
        f"{total_chapters} 个章节 → {len(matched_agents)} 个 agent"
        f"（{', '.join(matched_agents)}）"
    )
