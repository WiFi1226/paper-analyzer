"""标准化文件读写工具。

本模块提供项目中通用的文本文件读写能力，以及路径/字符串工具函数。

用法:
    from paper_analyzer.io import read_text, write_text, resolve_output_path, normalize_paper_name
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config, get_default_config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 字符串工具
# ══════════════════════════════════════════════════════════════════════════

def normalize_paper_name(name: str) -> str:
    """将 paper_name 中的路径不安全字符统一替换为下划线。

    替换对象：空格、连字符、点号、逗号、分号、冒号、叹号、问号、
             &、括号（中英文）、方括号、花括号。
    连续多个替换符折叠为一个下划线，去掉首尾下划线。

    Examples:
        "My Paper v2"           → "My_Paper_v2"
        "does-hawkish-doveish"  → "does_hawkish_doveish"
        "Smith & Jones (2024)"  → "Smith_Jones_2024"
        "paper_final (3)"       → "paper_final_3"
    """
    sanitized = re.sub(r"[\s\-.,;:!?&()\[\]{}（）【】]+", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


# ══════════════════════════════════════════════════════════════════════════
# 文件读写
# ══════════════════════════════════════════════════════════════════════════


def read_text(path: Path, encoding: str = "utf-8") -> str:
    """读取文本文件。

    Args:
        path: 文件路径
        encoding: 编码，默认 utf-8

    Returns:
        文件内容字符串
    """
    return path.read_text(encoding=encoding)


def write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """写入文本文件，自动创建父目录。

    Args:
        path: 输出路径
        text: 要写入的文本内容
        encoding: 编码，默认 utf-8
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)


def read_json(path: Path) -> Any:
    """读取 JSON 文件。

    Args:
        path: JSON 文件路径

    Returns:
        解析后的 Python 对象

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON 格式错误
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """写入 JSON 文件，自动创建父目录。

    Args:
        path:   输出路径
        data:   要写入的数据
        indent: JSON 缩进空格数，默认 2
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def resolve_output_path(
    paper_name: str,
    filename: str | None = None,
    config: Config | None = None,
) -> Path:
    """解析 output 目录下的文件路径。

    Args:
        paper_name: 论文名（子目录名）
        filename:   可选，文件名
        config:     Config 对象

    Returns:
        output/<paper_name>/ 或 output/<paper_name>/<filename>
    """
    if config is None:
        config = get_default_config()
    return config.resolve_output_path(paper_name, filename)
