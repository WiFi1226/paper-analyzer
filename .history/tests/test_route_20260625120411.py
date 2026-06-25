"""测试 core/route.py —— 路由匹配纯函数。"""

import pytest
from paper_analyzer.core.route import (
    fuzzy_match, filter_sections, match_chapters,
    build_filter_description, build_match_report, summarize_matches,
)


# ── fuzzy_match ────────────────────────────────────────────────

def test_fuzzy_match_hit():
    assert fuzzy_match("引言", ["引言", "Introduction"]) is True


def test_fuzzy_match_hit_en():
    assert fuzzy_match("Introduction", ["引言", "Introduction"]) is True


def test_fuzzy_match_case_insensitive():
    assert fuzzy_match("INTRODUCTION", ["introduction"]) is True


def test_fuzzy_match_miss():
    assert fuzzy_match("结论", ["引言", "Introduction"]) is False


def test_fuzzy_match_partial():
    assert fuzzy_match("研究背景与意义", ["背景"]) is True


# ── filter_sections ────────────────────────────────────────────

def test_filter_sections_no_filter():
    sections = [{"title": "引言"}, {"title": "结论"}]
    assert filter_sections(sections, None) == sections
    assert filter_sections(sections, []) == sections


def test_filter_sections_with_filter():
    sections = [{"title": "引言"}, {"title": "结论"}, {"title": "文献综述"}]
    result = filter_sections(sections, ["引言"])
    assert len(result) == 1
    assert result[0]["title"] == "引言"


def test_filter_sections_multi_filter():
    sections = [{"title": "引言"}, {"title": "结论"}, {"title": "文献综述"}]
    result = filter_sections(sections, ["引言", "结论"])
    assert len(result) == 2


# ── match_chapters ─────────────────────────────────────────────

SAMPLE_SECTIONS = [
    {"title": "引言", "content": "引言内容" * 10},
    {"title": "文献综述", "content": "综述内容" * 20},
    {"title": "实证分析", "content": "分析内容" * 30},
    {"title": "结论", "content": "结论内容" * 10},
]

SAMPLE_ROUTES = [
    {"agent": "introduction-analyzer", "keywords": ["引言", "Introduction"]},
    {"agent": "literature-analyzer", "keywords": ["文献综述", "Literature"]},
    {"agent": "results-analyzer", "keywords": ["实证", "结果", "Results"]},
]

SAMPLE_ALIASES = {
    "structure": ["introduction-analyzer", "literature-analyzer"],
}


def test_match_chapters_basic():
    matches, unmatched = match_chapters(SAMPLE_SECTIONS, SAMPLE_ROUTES, SAMPLE_ALIASES)
    assert len(matches) >= 2  # 引言 + 文献综述
    assert any(m["agent"] == "introduction-analyzer" for m in matches)
    assert any(m["agent"] == "literature-analyzer" for m in matches)


def test_match_chapters_unmatched():
    matches, unmatched = match_chapters(SAMPLE_SECTIONS, SAMPLE_ROUTES, SAMPLE_ALIASES)
    # 结论不应匹配（不在路由表中）
    unmatched_titles = [u["title"] for u in unmatched]
    assert "结论" in unmatched_titles


def test_match_chapters_agent_filter():
    matches, unmatched = match_chapters(
        SAMPLE_SECTIONS, SAMPLE_ROUTES, SAMPLE_ALIASES,
        agent_filters=["introduction-analyzer"],
    )
    agents = {m["agent"] for m in matches}
    assert "introduction-analyzer" in agents
    assert "literature-analyzer" not in agents


def test_match_chapters_alias_expansion():
    matches, unmatched = match_chapters(
        SAMPLE_SECTIONS, SAMPLE_ROUTES, SAMPLE_ALIASES,
        agent_filters=["structure"],
    )
    agents = {m["agent"] for m in matches}
    assert "introduction-analyzer" in agents
    assert "literature-analyzer" in agents


def test_match_chapters_char_count():
    matches, _ = match_chapters(SAMPLE_SECTIONS, SAMPLE_ROUTES, SAMPLE_ALIASES)
    for m in matches:
        assert "char_count" in m
        assert m["char_count"] > 0


# ── build_filter_description ───────────────────────────────────

def test_build_filter_description_none():
    assert build_filter_description(None, None) is None


def test_build_filter_description_section_only():
    desc = build_filter_description(["引言"], None)
    assert "引言" in desc


def test_build_filter_description_with_alias():
    aliases = {"structure": ["a", "b"]}
    desc = build_filter_description(None, ["structure"], aliases)
    assert "structure" in desc
    assert "a" in desc


# ── build_match_report ─────────────────────────────────────────

def test_build_match_report():
    matches = [
        {"title": "引言", "agent": "introduction-analyzer", "char_count": 100},
    ]
    report = build_match_report(
        [{"title": "引言"}, {"title": "结论"}],
        matches,
    )
    assert "引言" in report
    assert "introduction-analyzer" in report


# ── summarize_matches ──────────────────────────────────────────

def test_summarize_matches():
    matches = [
        {"agent": "a", "title": "x"},
        {"agent": "b", "title": "y"},
    ]
    result = summarize_matches(matches, total_chapters=5)
    assert "5" in result
    assert "2" in result
