"""标准化文件读写工具。

本模块提供项目中通用的文本文件读写能力。

用法:
    from paper_analyzer.io import read_text, write_text, resolve_output_path
"""

import json
import logging
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config, get_default_config

logger = logging.getLogger(__name__)


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
