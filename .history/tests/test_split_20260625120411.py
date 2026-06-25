"""测试 core/split.py —— 章节切分纯函数。"""

import re
import pytest
from paper_analyzer.core.split import (
    detect_style, find_cn_headings, find_en_headings, split_text,
)


# ── 测试用正则 ──────────────────────────────────────────────────

CN_PATTERN = re.compile(
    r"^\s{0,88}(?P<title>(?:引\s{0,3}言)|(?:[一二三四五六七八九十]、[^\n]{2,50})|"
    r"(?:结\s{0,3}论[^\n]{0,30})|(?:参考文献[^\n]{0,10})|(?:附\s{0,3}录[^\n]{0,30}))$",
    re.MULTILINE,
)

EN_LINE = re.compile(
    r"^(?P<leading>\s*)(?P<title>(?:[1-9]\d{0,2}|[IVX]+)\.\s+"
    r"(?!When\s|If\s|Because\s|Although\s)[A-Z][A-Za-z0-9,\- &:]{5,80})"
    r"(?=\s|\n|\r|$)",
    re.MULTILINE,
)

EN_MID = re.compile(
    r"(?:^|\s{4,})(?P<title>(?:[1-9]\d{0,2}|[IVX]+)\.\s+"
    r"(?!When\s|If\s|Because\s|Although\s)[A-Z][A-Za-z0-9,\- &:]{5,80})"
    r"(?=\s|\n|\r|$)",
    re.MULTILINE,
)


# ── detect_style ───────────────────────────────────────────────

def test_detect_style_cn():
    text = "引言\n这是中文论文的内容。\n一、研究背景\n一些内容。\n二、文献综述\n更多内容。"
    assert detect_style(text, CN_PATTERN, EN_LINE, EN_MID) == "cn"


def test_detect_style_en():
    text = "1. Introduction\nThis is an English paper.\n2. Literature Review\nMore content.\n3. Methodology"
    assert detect_style(text, CN_PATTERN, EN_LINE, EN_MID) == "en"


def test_detect_style_empty():
    assert detect_style("", CN_PATTERN, EN_LINE, EN_MID) == "en"


def test_detect_style_ascii_heuristic():
    text = "This is a paper without standard headings but mostly ASCII content."
    assert detect_style(text, CN_PATTERN, EN_LINE, EN_MID) == "en"


def test_detect_style_cn_heuristic():
    text = "这是一篇没有标准标题但主要是中文内容的论文。我们研究了这个问题。"
    assert detect_style(text, CN_PATTERN, EN_LINE, EN_MID) == "cn"


# ── find_cn_headings ───────────────────────────────────────────

def test_find_cn_headings():
    text = "引言\n这是引言内容。\n一、研究背景\n背景内容。\n二、文献综述\n综述内容。\n结论与政策建议\n最后的段落。"
    headings = find_cn_headings(text, CN_PATTERN)
    assert len(headings) == 4
    titles = [h[2] for h in headings]
    assert "引言" in titles
    assert "一、研究背景" in titles
    assert "二、文献综述" in titles
    assert any("结论" in t for t in titles)


def test_find_cn_headings_no_headings():
    text = "这是一段没有任何标题的普通文本。"
    headings = find_cn_headings(text, CN_PATTERN)
    assert len(headings) == 0


# ── find_en_headings ───────────────────────────────────────────

def test_find_en_headings():
    text = "1. Introduction\nIntro content.\n2. Literature Review\nLit content.\n3. Methodology\nMethod content."
    headings = find_en_headings(text, EN_LINE, EN_MID)
    assert len(headings) >= 2
    titles = [h[2] for h in headings]
    assert any("Introduction" in t for t in titles)


def test_find_en_headings_skip_year():
    text = "2021. The stance of monetary policy\nThis is not a heading.\n1. Introduction\nReal intro."
    headings = find_en_headings(text, EN_LINE, EN_MID)
    titles = [h[2] for h in headings]
    # 2021 应该被跳过
    assert not any(t.startswith("2021") for t in titles)


# ── split_text ─────────────────────────────────────────────────

def test_split_text_basic():
    text = "前置摘要信息足够长所以保留。\n" * 10 + "\n引言\n引言内容。\n一、研究背景\n背景内容。\n结论\n结论内容。"
    headings = find_cn_headings(text, CN_PATTERN)
    sections = split_text(text, headings, min_pre_content_chars=50)
    assert len(sections) >= 3


def test_split_text_short_preface():
    text = "短\n引言\n引言内容。"
    headings = find_cn_headings(text, CN_PATTERN)
    sections = split_text(text, headings, min_pre_content_chars=50)
    # 前置内容太短，不应保留
    titles = [s["title"] for s in sections]
    assert "前置信息" not in titles


def test_split_text_empty_headings():
    text = "没有标题的文本内容。"
    sections = split_text(text, [], min_pre_content_chars=50)
    assert len(sections) == 0


def test_split_text_content():
    text = "引言\n这是引言的内容部分。\n一、研究背景\n这是研究背景的内容。"
    headings = find_cn_headings(text, CN_PATTERN)
    sections = split_text(text, headings, min_pre_content_chars=50)
    # 引言的内容不应为空
    intro_section = next((s for s in sections if "引言" in s["title"]), None)
    assert intro_section is not None
    assert len(intro_section["content"]) > 0
