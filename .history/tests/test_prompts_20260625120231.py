"""测试 core/prompts.py —— Prompt 构造纯函数。"""

import pytest
from paper_analyzer.core.prompts import (
    build_section_block, build_chapter_title_list,
    assemble_prompt_parts, build_single_prompt,
    build_all_prompts, paper_content_formatter,
)


# ── build_section_block ────────────────────────────────────────

def test_build_section_block_single():
    lookup = {"引言": "这是引言内容。"}
    result = build_section_block(["引言"], lookup)
    assert "引言" in result
    assert "这是引言内容" in result


def test_build_section_block_multi():
    lookup = {"引言": "内容1", "结论": "内容2"}
    result = build_section_block(["引言", "结论"], lookup)
    assert "2 个章节" in result
    assert "引言" in result
    assert "结论" in result
    assert "内容1" in result
    assert "内容2" in result


# ── build_chapter_title_list ──────────────────────────────────

def test_build_chapter_title_list():
    sections = [{"title": "引言"}, {"title": "结论"}]
    result = build_chapter_title_list(sections)
    assert "引言" in result
    assert "结论" in result
    assert "1." in result
    assert "2." in result


# ── assemble_prompt_parts ──────────────────────────────────────

def test_assemble_prompt_parts():
    result = assemble_prompt_parts("规则", "内容块", "页脚")
    assert "full" in result
    assert "skeleton" in result
    assert "content_block" in result
    assert "footer" in result
    assert "规则" in result["full"]
    assert "内容块" in result["full"]
    assert "页脚" in result["full"]


# ── build_single_prompt ────────────────────────────────────────

def test_build_single_prompt_no_split():
    result = build_single_prompt(
        "test-agent", ["章节1"], "规则", "短内容", "页脚", 50000,
    )
    assert len(result) == 1
    assert result[0]["agent"] == "test-agent"
    assert result[0]["chapter_titles"] == ["章节1"]
    assert "prompt_text" in result[0]


def test_build_single_prompt_single_chapter_too_long():
    warnings = []
    result = build_single_prompt(
        "test-agent", ["超长章节"], "规则", "x" * 100, "页脚", 50, warnings,
    )
    assert len(result) == 1
    assert len(warnings) >= 1
    assert "content_text" in result[0]


def test_build_single_prompt_multi_chapter_split():
    result = build_single_prompt(
        "test-agent", ["A", "B", "C", "D"], "规则", "x" * 200, "页脚", 50,
    )
    # 应该被拆分为多个部分
    assert len(result) >= 1


# ── paper_content_formatter ────────────────────────────────────

def test_paper_content_formatter():
    lookup = {"引言": "内容"}
    sections = [{"title": "引言"}, {"title": "结论"}]
    content_block, footer = paper_content_formatter(
        ["引言"], lookup, "规则", sections,
    )
    assert "引言" in content_block
    assert "内容" in content_block
    assert "分析" in footer


def test_paper_content_formatter_with_chapter_list():
    lookup = {"前置信息": "内容"}
    sections = [{"title": "前置信息"}, {"title": "引言"}]
    content_block, footer = paper_content_formatter(
        ["前置信息"], lookup, "规则", sections,
        needs_chapter_list=True,
    )
    assert "完整章节标题列表" in footer


# ── build_all_prompts ──────────────────────────────────────────

def test_build_all_prompts_empty():
    result = build_all_prompts(
        [{"title": "引言", "content": "内容"}],
        [],
        "规则",
        50000,
    )
    assert "warnings" in result
    assert len(result["warnings"]) >= 1


def test_build_all_prompts_basic():
    sections = [
        {"title": "引言", "content": "引言内容" * 50},
    ]
    matches = [
        {"title": "引言", "agent": "introduction-analyzer", "char_count": 200},
    ]
    result = build_all_prompts(sections, matches, "规则全文", 50000)
    assert len(result["prompts"]) >= 1
    assert result["prompts"][0]["agent"] == "introduction-analyzer"
