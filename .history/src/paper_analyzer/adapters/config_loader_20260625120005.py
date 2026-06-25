"""YAML 配置加载器 —— 包内默认 + 用户覆盖合并。

配置加载优先级：
  用户显式指定 --config-dir /path/to/custom
         │
         ▼
  自定义目录存在吗？
    ├── routing.yaml 存在？ → 用自定义的
    ├── routing.yaml 不存在 → 用包内 defaults/routing.yaml
    ├── settings.yaml 存在？ → 用自定义的
    └── settings.yaml 不存在 → 用包内 defaults/settings.yaml

用户只需要提供自己改过的配置文件，不需要复制整个 defaults/ 目录。

分层策略：
  - split.yaml / agents.yaml → 不可用户覆盖（包内默认）
  - routing.yaml / settings.yaml → 可用户覆盖
"""

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from paper_analyzer._config import Config, get_default_config
from paper_analyzer.errors import ConfigError, ConfigNotFoundError, ConfigFormatError

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# YAML 读写
# ══════════════════════════════════════════════════════════════════════════

def load_yaml(path: Path) -> dict[str, Any]:
    """读取并解析 YAML 文件。

    Args:
        path: YAML 文件的绝对路径

    Returns:
        解析后的字典

    Raises:
        ConfigNotFoundError: 文件不存在
        ConfigFormatError: YAML 格式错误
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data is None:
                return {}
            return data
    except FileNotFoundError:
        raise ConfigNotFoundError(f"配置文件不存在 —— {path}")
    except yaml.YAMLError as e:
        raise ConfigFormatError(f"配置文件格式错误 —— {path}: {e}")


# ══════════════════════════════════════════════════════════════════════════
# 配置加载（包内默认 + 用户覆盖）
# ══════════════════════════════════════════════════════════════════════════

def _resolve_config_path(
    config: Config,
    filename: str,
    allow_user_override: bool,
) -> Path:
    """解析配置文件路径。

    Args:
        config:             Config 对象
        filename:           配置文件名（如 "settings.yaml"）
        allow_user_override: 是否允许用户覆盖

    Returns:
        解析后的配置文件路径
    """
    if allow_user_override and config.user_config_dir:
        user_path = config.user_config_dir / filename
        if user_path.exists():
            logger.debug("使用用户自定义配置: %s", user_path)
            return user_path
    return config.defaults_dir / filename


# ── 可覆盖配置映射 ──
_OVERRIDABLE = {"settings.yaml", "routing.yaml"}


def _load_config_file(
    config: Config,
    filename: str,
) -> dict[str, Any]:
    """加载单个配置文件（自动判断可否用户覆盖）。"""
    allow_override = filename in _OVERRIDABLE
    path = _resolve_config_path(config, filename, allow_override)
    logger.debug("加载配置: %s (用户覆盖=%s)", path, allow_override)
    return load_yaml(path)


# ══════════════════════════════════════════════════════════════════════════
# 公共 API：配置加载函数
# ══════════════════════════════════════════════════════════════════════════

def load_settings(config: Config | None = None) -> dict[str, Any]:
    """加载全局设置（阈值 + 输出路径）。

    优先用户覆盖，否则用包内默认。加载后自动验证。
    """
    if config is None:
        config = get_default_config()
    data = _load_config_file(config, "settings.yaml")
    issues = _validate_settings(data)
    if issues:
        for issue in issues:
            logger.warning("配置警告（settings.yaml）: %s", issue)
    return data


def load_routing(config: Config | None = None) -> dict[str, Any]:
    """加载路由配置（别名 + 路由表）。

    优先用户覆盖，否则用包内默认。加载后自动验证跨文件一致性。
    """
    if config is None:
        config = get_default_config()
    data = _load_config_file(config, "routing.yaml")
    agent_names = get_agent_names(config)
    issues = _validate_routing(data, agent_names)
    if issues:
        for issue in issues:
            logger.warning("配置警告（routing.yaml）: %s", issue)
    return data


def load_agents(config: Config | None = None) -> dict[str, Any]:
    """加载 agent 配置（区段标签 + 排序 + 规则列表）。

    不可用户覆盖。加载后自动验证。
    """
    if config is None:
        config = get_default_config()
    data = _load_config_file(config, "agents.yaml")
    issues = _validate_agents(data)
    if issues:
        for issue in issues:
            logger.warning("配置警告（agents.yaml）: %s", issue)
    return data


def load_split(config: Config | None = None) -> dict[str, Any]:
    """加载章节切分配置（中英文标题匹配规则）。

    不可用户覆盖。加载后自动验证。
    """
    if config is None:
        config = get_default_config()
    data = _load_config_file(config, "split.yaml")
    issues = _validate_split(data)
    if issues:
        for issue in issues:
            logger.warning("配置警告（split.yaml）: %s", issue)
    return data


# ══════════════════════════════════════════════════════════════════════════
# Agent 注册表查询
# ══════════════════════════════════════════════════════════════════════════

def get_agent_names(config: Config | None = None) -> set[str]:
    """返回所有叶子 agent 名称集合。"""
    data = load_agents(config)
    return set(data.get("agents", {}).keys())


def get_agent_section_map(config: Config | None = None) -> dict[str, str]:
    """返回 agent → section_label 映射。"""
    data = load_agents(config)
    agents = data.get("agents", {})
    return {name: info["section_label"] for name, info in agents.items()}


def get_agent_canonical_order(config: Config | None = None) -> dict[str, int]:
    """返回 agent → order 映射（排序用）。"""
    data = load_agents(config)
    agents = data.get("agents", {})
    return {name: info["order"] for name, info in agents.items()}


def get_routing_rules(config: Config | None = None) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """返回 (routes, aliases)。"""
    data = load_routing(config)
    return data.get("routes", []), data.get("aliases", {})


def get_max_prompt_chars(config: Config | None = None) -> int:
    """从 settings.yaml 加载 prompt 拆分阈值。"""
    return load_settings(config).get("max_prompt_chars", 50_000)


def unwrap_routing(data: dict[str, Any]) -> dict[str, Any]:
    """如果 data 是完整 orchestrator 输出（含 routing 子字段），提取内层 routing。"""
    return data.get("routing", data)


# ══════════════════════════════════════════════════════════════════════════
# 切分正则构建
# ══════════════════════════════════════════════════════════════════════════

def _build_cn_heading(cfg: dict[str, Any]) -> re.Pattern[str]:
    """从 YAML 配置构建中文标题正则。"""
    cn = cfg.get("cn_headings", {})
    max_ws = cn.get("max_leading_whitespace", 88)
    patterns = cn.get("patterns", [])

    alternatives = []
    for p in patterns:
        if "pattern" in p:
            alternatives.append(f"(?:{p['pattern']})")
        elif "prefix" in p:
            body_min = p.get("body_min", 2)
            body_max = p.get("body_max", 50)
            alternatives.append(
                f"(?:{p['prefix']}[^\\n]{{{body_min},{body_max}}})"
            )

    if not alternatives:
        alternatives = [
            r"(?:引\s{0,3}言)",
            r"(?:[一二三四五六七八九十]、[^\n]{2,50})",
            r"(?:结\s{0,3}论[^\n]{0,30})",
            r"(?:参考文献[^\n]{0,10})",
            r"(?:附\s{0,3}录[^\n]{0,30})",
        ]

    pattern = (
        r"^\s{0," + str(max_ws) + r"}"
        r"(?P<title>"
        + "|".join(alternatives) +
        r")$"
    )
    return re.compile(pattern, re.MULTILINE)


def _build_en_heading_blacklist(cfg: dict[str, Any]) -> str:
    """从 YAML 构建英文标题负面列表正则片段。"""
    en = cfg.get("en_headings", {})
    blacklist = en.get("blacklist", [])
    if not blacklist:
        return r"(?!)"
    return "|".join(f"{w}\\s" for w in blacklist)


def _build_en_title_body(cfg: dict[str, Any], blacklist_pattern: str) -> str:
    """从 YAML 构建英文标题正文正则片段。"""
    en = cfg.get("en_headings", {})
    number_formats = en.get("number_formats", [r"[1-9]\d{0,2}", r"[IVX]+"])
    body_min = en.get("body_min", 5)
    body_max = en.get("body_max", 80)
    body_start = en.get("body_start", r"[A-Z]")
    body_chars = en.get("body_chars", r"[A-Za-z0-9,\- &:]")

    numbers = "|".join(number_formats)
    return (
        r"(?:" + numbers + r")"
        r"\.\s+"
        r"(?!" + blacklist_pattern + r")"
        + body_start + body_chars + r"{" + str(body_min) + r"," + str(body_max) + r"}"
    )


def _build_en_headings(cfg: dict[str, Any]) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """从 YAML 构建英文标题的两种匹配正则（行首模式 + 中继模式）。"""
    en = cfg.get("en_headings", {})
    mid_spaces = en.get("mid_line_spaces", 4)

    blacklist = _build_en_heading_blacklist(cfg)
    title_body = _build_en_title_body(cfg, blacklist)

    en_heading_line = re.compile(
        r"^(?P<leading>\s*)(?P<title>" + title_body + r")"
        r"(?=\s|\n|\r|$)",
        re.MULTILINE,
    )

    en_heading_mid = re.compile(
        r"(?:^|\s{" + str(mid_spaces) + r",})(?P<title>" + title_body + r")"
        r"(?=\s|\n|\r|$)",
        re.MULTILINE,
    )

    return en_heading_line, en_heading_mid


@lru_cache(maxsize=1)
def get_split_patterns(config: Config | None = None) -> dict[str, Any]:
    """从 split.yaml 构建切分正则。

    Returns:
        {
            "cn_heading":          re.Pattern,
            "en_heading_line":     re.Pattern,
            "en_heading_mid":      re.Pattern,
            "min_pre_content_chars": int,
            "heading_dedup_distance": int,
        }
    """
    cfg = load_split(config)
    en_line, en_mid = _build_en_headings(cfg)
    return {
        "cn_heading": _build_cn_heading(cfg),
        "en_heading_line": en_line,
        "en_heading_mid": en_mid,
        "min_pre_content_chars": cfg.get("min_pre_content_chars", 50),
        "heading_dedup_distance": cfg.get("heading_dedup_distance", 30),
    }


# ══════════════════════════════════════════════════════════════════════════
# YAML Schema 验证
# ══════════════════════════════════════════════════════════════════════════

def _validate_settings(data: dict[str, Any]) -> list[str]:
    """验证 settings.yaml 字段类型和值。"""
    issues: list[str] = []
    max_chars = data.get("max_prompt_chars", 50000)
    if not isinstance(max_chars, int) or max_chars <= 0:
        issues.append(f"max_prompt_chars 必须是正整数，当前值: {max_chars}")
    output_dir = data.get("output_dir")
    if output_dir is not None and not isinstance(output_dir, str):
        issues.append(f"output_dir 必须是字符串，当前类型: {type(output_dir).__name__}")
    search_paths = data.get("pdf_search_paths")
    if search_paths is not None and not isinstance(search_paths, list):
        issues.append(f"pdf_search_paths 必须是列表，当前类型: {type(search_paths).__name__}")
    return issues


def _validate_agents(data: dict[str, Any]) -> list[str]:
    """验证 agents.yaml 字段完整性和类型。"""
    issues: list[str] = []
    agents = data.get("agents", {})
    if not isinstance(agents, dict) or not agents:
        issues.append("agents 字段为空或格式错误，至少需要一个 agent 定义")
        return issues
    for name, info in agents.items():
        for field in ["section_label", "order"]:
            if field not in info:
                issues.append(f"Agent '{name}' 缺少必填字段: {field}")
        order = info.get("order")
        if order is not None and not isinstance(order, int):
            issues.append(f"Agent '{name}' 的 order 必须是整数，当前值: {order}")
    return issues


def _validate_routing(data: dict[str, Any], agent_names: set[str]) -> list[str]:
    """验证 routing.yaml 引用的 agent 都在 agents.yaml 中注册。"""
    issues: list[str] = []
    if not agent_names:
        return issues
    for route in data.get("routes", []):
        agent = route.get("agent", "")
        if agent and agent not in agent_names:
            issues.append(f"routes 引用了未在 agents.yaml 中注册的 agent: '{agent}'")
    for alias, subs in data.get("aliases", {}).items():
        for sa in subs:
            if sa not in agent_names:
                issues.append(f"别名 '{alias}' 展开的 agent '{sa}' 未在 agents.yaml 中注册")
    return issues


def _validate_split(data: dict[str, Any]) -> list[str]:
    """验证 split.yaml 字段类型和必要结构。"""
    issues: list[str] = []

    val = data.get("min_pre_content_chars", 50)
    if not isinstance(val, int) or val < 0:
        issues.append(f"min_pre_content_chars 必须是非负整数，当前值: {val}")

    val = data.get("heading_dedup_distance", 30)
    if not isinstance(val, int) or val < 0:
        issues.append(f"heading_dedup_distance 必须是非负整数，当前值: {val}")

    cn = data.get("cn_headings", {})
    if not isinstance(cn, dict):
        issues.append("cn_headings 必须是字典")
    else:
        patterns = cn.get("patterns", [])
        if not isinstance(patterns, list) or not patterns:
            issues.append("cn_headings.patterns 必须是非空列表")
        for i, p in enumerate(patterns):
            if not isinstance(p, dict):
                issues.append(f"cn_headings.patterns[{i}] 必须是字典")
            elif "pattern" not in p and "prefix" not in p:
                issues.append(f"cn_headings.patterns[{i}] 缺少 pattern 或 prefix 字段")

    en = data.get("en_headings", {})
    if not isinstance(en, dict):
        issues.append("en_headings 必须是字典")
    else:
        for field in ["number_formats", "body_min", "body_max"]:
            if field not in en:
                issues.append(f"en_headings 缺少字段: {field}")

    return issues
