"""paper-analyzer —— 论文分析工具包。

公共 API 导出。
"""

from paper_analyzer._config import Config, get_default_config, set_default_config
from paper_analyzer.errors import (
    PaperAnalyzerError,
    ConfigError,
    ConfigNotFoundError,
    ConfigFormatError,
    PdfExtractionError,
    CacheError,
    ToolNotFoundError,
    OutputSaveError,
)

__all__ = [
    "Config",
    "get_default_config",
    "set_default_config",
    "PaperAnalyzerError",
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigFormatError",
    "PdfExtractionError",
    "CacheError",
    "ToolNotFoundError",
    "OutputSaveError",
]
__version__ = "0.1.0"
