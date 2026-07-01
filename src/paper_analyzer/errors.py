"""自定义异常层次 —— 所有 paper-analyzer 异常的基类。

库代码（core/、adapters/）统一抛这些异常，不调用 sys.exit()。
只有 cli/ 层在 main() 入口处 catch 异常后 sys.exit(1)。
"""


class PaperAnalyzerError(Exception):
    """所有 paper-analyzer 异常的基类。"""
    pass


# ── 配置错误 ──────────────────────────────────────────────────

class ConfigError(PaperAnalyzerError):
    """配置加载或解析错误。"""
    pass


class ConfigNotFoundError(ConfigError):
    """配置文件不存在。"""
    pass


class ConfigFormatError(ConfigError):
    """配置文件格式错误（YAML 语法 / JSON 格式）。"""
    pass


# ── PDF 错误 ──────────────────────────────────────────────────

class PdfExtractionError(PaperAnalyzerError):
    """PDF 文本提取失败（pdftotext 执行错误等）。"""
    pass


# ── 缓存错误 ──────────────────────────────────────────────────

class CacheError(PaperAnalyzerError):
    """缓存读写错误。"""
    pass


# ── 外部工具错误 ──────────────────────────────────────────────

class ToolNotFoundError(PaperAnalyzerError):
    """外部命令未安装（如 pdftotext）。"""
    pass


