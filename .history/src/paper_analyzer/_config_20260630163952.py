"""Config 对象 —— 统一路径管理和配置加载。

替代旧项目中所有硬编码的 __file__ 相对路径推导。

Config 对象职责：
  1. 持有 project_root、output_dir、config_dir 等路径
  2. 提供 cache_dir()、txt_path()、sections_path() 等路径计算方法
  3. 从 YAML 加载配置（包内默认 → 用户覆盖合并）

创建方式（优先级从高到低）：
  a. 用户显式传入 Config 对象
  b. 环境变量 PAPER_ANALYZER_ROOT / PAPER_ANALYZER_CONFIG_DIR / PAPER_ANALYZER_OUTPUT_DIR
  c. 当前工作目录 cwd
"""

import os
from pathlib import Path


class Config:
    """paper-analyzer 全局配置对象。

    所有需要路径的函数从 Config 对象获取路径，不再调用全局函数。
    """

    def __init__(
        self,
        project_root: str | Path | None = None,
        config_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        config_files: list[str | Path] | None = None,
    ):
        # ── project_root ──
        if project_root:
            self.project_root = Path(project_root).resolve()
        elif os.environ.get("PAPER_ANALYZER_ROOT"):
            self.project_root = Path(os.environ["PAPER_ANALYZER_ROOT"]).resolve()
        else:
            self.project_root = Path.cwd()

        # ── output_dir ──
        if output_dir:
            self.output_dir = Path(output_dir)
        elif os.environ.get("PAPER_ANALYZER_OUTPUT_DIR"):
            self.output_dir = Path(os.environ["PAPER_ANALYZER_OUTPUT_DIR"])
        else:
            self.output_dir = self.project_root / "output"

        # ── config_dir（用户自定义配置目录） ──
        if config_dir:
            self._user_config_dir = Path(config_dir)
        elif os.environ.get("PAPER_ANALYZER_CONFIG_DIR"):
            self._user_config_dir = Path(os.environ["PAPER_ANALYZER_CONFIG_DIR"])
        else:
            self._user_config_dir = None

        # ── 用户通过 --config-file 指定的配置文件 ──
        if config_files:
            self._config_files = [Path(f).resolve() for f in config_files]
        else:
            self._config_files = []

        # ── 包内默认配置目录 ──
        self._defaults_dir = Path(__file__).resolve().parent / "defaults"

    # ══════════════════════════════════════════════════════════════
    # 配置目录
    # ══════════════════════════════════════════════════════════════

    @property
    def user_config_dir(self) -> Path | None:
        """用户自定义配置目录（可能为 None）。"""
        return self._user_config_dir

    @property
    def defaults_dir(self) -> Path:
        """包内默认配置目录。"""
        return self._defaults_dir

    @property
    def config_files(self) -> list[Path]:
        """用户通过 --config-file 指定的配置文件列表。"""
        return self._config_files

    # ══════════════════════════════════════════════════════════════
    # 路径计算方法
    # ══════════════════════════════════════════════════════════════

    def cache_dir(self, paper_name: str) -> Path:
        """返回某论文的 cache 目录：output/<论文名>/cache/。自动创建。"""
        d = self.output_dir / paper_name / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def txt_path(self, paper_name: str) -> Path:
        """返回缓存 TXT 路径：output/<论文名>/cache/<论文名>.txt"""
        return self.cache_dir(paper_name) / f"{paper_name}.txt"

    def sections_path(self, paper_name: str) -> Path:
        """返回章节 JSON 路径：output/<论文名>/cache/_prompts/_sections.json"""
        return self.cache_dir(paper_name) / "_prompts" / "_sections.json"

    def routing_path(self, paper_name: str) -> Path:
        """返回路由方案路径：output/<论文名>/cache/_prompts/_routing.json"""
        return self.cache_dir(paper_name) / "_prompts" / "_routing.json"

    def routing_auto_path(self, paper_name: str) -> Path:
        """返回自动匹配路由历史路径：output/<论文名>/cache/_routing_auto.json"""
        return self.cache_dir(paper_name) / "_routing_auto.json"

    def analysis_path(self, paper_name: str) -> Path:
        """返回最终分析报告路径：output/<论文名>/<论文名>_analysis.md"""
        return self.output_dir / paper_name / f"{paper_name}_analysis.md"

    def prompts_cache_dir(self, paper_name: str) -> Path:
        """返回 prompt 缓存目录：output/<论文名>/cache/_prompts/。自动创建。"""
        d = self.cache_dir(paper_name) / "_prompts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def outputs_dir(self, paper_name: str) -> Path:
        """返回 agent 输出目录：output/<论文名>/cache/_contents/。自动创建。"""
        d = self.cache_dir(paper_name) / "_contents"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ══════════════════════════════════════════════════════════════
    # 便捷方法
    # ══════════════════════════════════════════════════════════════

    def resolve_output_path(self, paper_name: str, filename: str | None = None) -> Path:
        """解析 output 目录下的文件路径。

        Args:
            paper_name: 论文名（子目录名）
            filename: 可选，文件名

        Returns:
            output/<paper_name>/ 或 output/<paper_name>/<filename>
        """
        base = self.output_dir / paper_name
        if filename:
            return base / filename
        return base


# ── 模块级默认 Config 实例 ────────────────────────────────────
# 供未显式传入 Config 的场景使用。

_default_config: Config | None = None


def get_default_config() -> Config:
    """获取模块级默认 Config 实例（惰性初始化）。"""
    global _default_config
    if _default_config is None:
        _default_config = Config()
    return _default_config


def set_default_config(config: Config) -> None:
    """设置模块级默认 Config 实例。"""
    global _default_config
    _default_config = config
