"""测试 core/report.py —— 报告组装纯函数。"""

import pytest
from paper_analyzer.core.report import (
    assemble_report, validate_sections, build_chapter_structure,
)


# ── build_chapter_structure ────────────────────────────────────

def test_build_chapter_structure():
    sections = [{"title": "引言"}, {"title": "结论"}]
    routing = {
        "matches": [
            {"title": "引言", "agent": "introduction-analyzer", "char_count": 100},
        ],
        "unmatched": [
            {"title": "结论", "char_count": 50, "reason": "未命中"},
        ],
    }
    result = build_chapter_structure(sections, routing)
    assert "引言" in result
    assert "introduction-analyzer" in result
    assert "结论" in result
    assert "跳过" in result


# ── assemble_report ────────────────────────────────────────────

def test_assemble_report_basic():
    blocks = [
        {"label": "引言分析", "anchor": "introduction", "content": "分析内容"},
    ]
    report = assemble_report("# 报告标题", blocks)
    assert "# 报告标题" in report
    assert "## 引言分析" in report
    assert "agent_section: introduction" in report
    assert "分析内容" in report


def test_assemble_report_with_base_info():
    blocks = [{"label": "分析", "anchor": "analysis", "content": "内容"}]
    report = assemble_report("# 标题", blocks, base_info="## 章节结构\n概述")
    assert "## 章节结构" in report
    assert "概述" in report


def test_assemble_report_multiple_blocks():
    blocks = [
        {"label": "A", "anchor": "a", "content": "内容A"},
        {"label": "B", "anchor": "b", "content": "内容B"},
    ]
    report = assemble_report("# 报告", blocks)
    assert "内容A" in report
    assert "内容B" in report


# ── validate_sections ──────────────────────────────────────────

def test_validate_sections_pass():
    report = "<!-- agent_section: a -->\n内容" * 100
    issues = validate_sections(
        report,
        expected_anchors={"a"},
        expected_labels={"分析"},
        min_chars=10,
    )
    assert len(issues) == 0


def test_validate_sections_missing_anchor():
    report = "短内容"
    issues = validate_sections(
        report,
        expected_anchors={"missing_anchor"},
        expected_labels=set(),
        min_chars=1,
    )
    assert len(issues) >= 1
    assert any("missing_anchor" in i for i in issues)


def test_validate_sections_too_short():
    report = "短"
    issues = validate_sections(
        report,
        expected_anchors=set(),
        expected_labels=set(),
        min_chars=500,
    )
    assert len(issues) >= 1
    assert any("过短" in i for i in issues)


def test_validate_sections_extra_anchor():
    report = "<!-- agent_section: a -->\n<!-- agent_section: b -->\n" + "x" * 100
    issues = validate_sections(
        report,
        expected_anchors={"a"},
        expected_labels=set(),
        min_chars=10,
    )
    assert len(issues) >= 1
    assert any("未预期" in i for i in issues)
