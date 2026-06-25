#!/usr/bin/env python3
"""论文文本切分引擎 —— 按一级标题将论文全文切分为若干章节。

本模块是纯文本处理引擎：所有函数接收输入、返回输出，不读盘、不写盘。
正则模式、阈值等由调用方显式传入。

支持两类论文格式（自动检测）：
  - 中文论文：引言、一、文献综述、...、六、结论与政策建议、参考文献
  - 英文论文：1. Introduction、2. Economic Environment、...、5. Conclusion

用法:
    from paper_analyzer.core.split import detect_style, find_cn_headings, find_en_headings, split_text

    style = detect_style(text, cn_pattern, en_line_pattern, en_mid_pattern)
    if style == "cn":
        headings = find_cn_headings(text, cn_pattern)
    else:
        headings = find_en_headings(text, en_line_pattern, en_mid_pattern, dedup_distance=30)
    sections = split_text(text, headings, min_pre_content_chars=50)
"""

import re
from typing import Any

# ══════════════════════════════════════════════════════════════════════════
# 算法常量（非配置，不依赖 YAML）
# ══════════════════════════════════════════════════════════════════════════

# 年份编号过滤：跳过纯年份数字开头的行（如 "2021. The stance..."）
_YEAR_PATTERN = re.compile(r"^\d{4,}\.")


# ══════════════════════════════════════════════════════════════════════════
# 1. 语言检测
# ══════════════════════════════════════════════════════════════════════════

def detect_style(
    text: str,
    cn_pattern: re.Pattern[str],
    en_line: re.Pattern[str],
    en_mid: re.Pattern[str],
) -> str:
    """自动检测论文语言风格：'cn' 或 'en'。

    通过比较中文和英文标题正则的匹配数来判断。
    当两者匹配数均为 0（非标准格式论文）时，用 ASCII 字母占比做启发式回退：
    英文论文通常 ASCII 字母（含空格、标点）占比 > 50%。

    Args:
        text:       论文全文
        cn_pattern: 中文标题正则
        en_line:    英文行首模式正则
        en_mid:     英文中继模式正则

    Returns:
        "cn" 或 "en"
    """
    cn_matches = len(cn_pattern.findall(text))
    en_matches = len(en_line.findall(text)) + len(en_mid.findall(text))

    if cn_matches > 0 or en_matches > 0:
        return "cn" if cn_matches >= en_matches else "en"

    # 双向 0 匹配 → 非标准格式，用字符占比启发式判断
    total_chars = len(text)
    if total_chars == 0:
        return "en"

    ascii_chars = sum(1 for c in text if ord(c) < 128)
    ascii_ratio = ascii_chars / total_chars
    return "en" if ascii_ratio > 0.5 else "cn"


# ══════════════════════════════════════════════════════════════════════════
# 2. 标题查找
# ══════════════════════════════════════════════════════════════════════════

def _clean_title(raw: str, style: str) -> str:
    """清洗标题文本。

    中文：去除所有空白字符。
    英文：保留单词间单个空格，trim 首尾空格。
    """
    if style == "cn":
        return re.sub(r"\s+", "", raw)
    else:
        return re.sub(r"\s+", " ", raw).strip()


def find_cn_headings(
    text: str,
    cn_pattern: re.Pattern[str],
) -> list[tuple[int, int, str]]:
    """在中文论文中查找所有一级标题。

    Args:
        text:       论文全文
        cn_pattern: 中文标题正则

    Returns:
        [(start_pos, end_pos, clean_title), ...]，按文本位置升序排列。
    """
    headings: list[tuple[int, int, str]] = []
    for m in cn_pattern.finditer(text):
        raw = m.group("title")
        clean = _clean_title(raw, "cn")
        headings.append((m.start(), m.end(), clean))
    return headings


def _collect_heading_matches(
    text: str,
    patterns: list[re.Pattern[str]],
    dedup_distance: int,
) -> list[tuple[int, int, str]]:
    """对一组正则模式遍历全文，收集并去重标题匹配。

    内部函数：合并行首模式和中继模式的结果。
    """
    seen: set[int] = set()
    candidates: list[tuple[int, int, str]] = []

    for pattern in patterns:
        for m in pattern.finditer(text):
            raw = m.group("title")
            # 跳过纯年份编号
            if _YEAR_PATTERN.match(raw):
                continue

            pos = m.start("title")
            end = m.end("title")

            # 去重
            too_close = False
            for existing_pos in seen:
                if abs(existing_pos - pos) < dedup_distance:
                    too_close = True
                    break
            if too_close:
                continue

            seen.add(pos)
            clean = _clean_title(raw, "en")
            candidates.append((pos, end, clean))

    candidates.sort(key=lambda x: x[0])
    return candidates


def find_en_headings(
    text: str,
    en_line: re.Pattern[str],
    en_mid: re.Pattern[str],
    dedup_distance: int = 30,
) -> list[tuple[int, int, str]]:
    """在英文论文中查找所有一级标题。

    合并行首模式和中继模式的匹配结果，按位置去重排序。

    Args:
        text:           论文全文
        en_line:        英文行首模式正则
        en_mid:         英文中继模式正则
        dedup_distance: 去重距离阈值（字符数）。

    Returns:
        [(start_pos, end_pos, clean_title), ...]，按文本位置升序排列。
    """
    return _collect_heading_matches(
        text,
        [en_line, en_mid],
        dedup_distance,
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. 文本切分
# ══════════════════════════════════════════════════════════════════════════

def split_text(
    text: str,
    headings: list[tuple[int, int, str]],
    min_pre_content_chars: int = 50,
) -> list[dict[str, Any]]:
    """按标题位置切分全文。

    Args:
        text:                  论文全文
        headings:              标题列表 [(start, end, title), ...]
        min_pre_content_chars: 第一个标题之前的文本至少 N 字符才保留为
                               「前置信息」独立章节。

    Returns:
        [{title: str, content: str}, ...]
    """
    sections: list[dict[str, Any]] = []

    # 第一个标题之前的内容 → 前置信息
    if headings and headings[0][0] > 0:
        before = text[:headings[0][0]].strip()
        if len(before) >= min_pre_content_chars:
            sections.append({"title": "前置信息", "content": before})

    # 按标题切分正文
    for i, (start, end, title) in enumerate(headings):
        content_start = end
        content_end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        content = text[content_start:content_end].strip()
        sections.append({"title": title, "content": content})

    return sections
