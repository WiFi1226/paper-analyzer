"""通用缓存判断 —— SHA 指纹 + mtime 新鲜度 + 缓存元数据读写。

纯函数模块，不依赖 paper_analyzer 的任何配置。
"""
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# SHA 指纹
# ══════════════════════════════════════════════════════════════════════════

def sha256(text: str) -> str:
    """返回文本的 SHA-256 十六进制字符串。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_rules_hash(rules: str) -> str:
    """计算规则全文的内容指纹。"""
    return sha256(rules)


def compute_sections_hash(sections: list[dict[str, Any]]) -> str:
    """计算完整章节标题列表的内容指纹。"""
    titles = [s["title"] for s in sections]
    return sha256(json.dumps(titles, ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════════
# mtime 缓存
# ══════════════════════════════════════════════════════════════════════════

def mtime_fresh(cache_path: Path, *source_paths: Path) -> bool:
    """基于 mtime 的缓存新鲜度判断。

    条件：
      1. cache_path 存在
      2. 所有 source_path 的 mtime 不晚于 cache_path
    """
    if not cache_path.exists():
        return False
    cache_mtime = cache_path.stat().st_mtime
    for src in source_paths:
        if src.exists() and src.stat().st_mtime > cache_mtime:
            return False
    return True


def config_changed(config_path: Path, cache_path: Path) -> bool:
    """检查配置文件是否比缓存更新。"""
    return (
        config_path.exists()
        and config_path.stat().st_mtime > cache_path.stat().st_mtime
    )


# ══════════════════════════════════════════════════════════════════════════
# 缓存元数据读写
# ══════════════════════════════════════════════════════════════════════════

def load_existing_meta(meta_path: Path) -> dict[str, Any] | None:
    """读取已有缓存元数据。返回 None 表示无缓存或格式不兼容。"""
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if "fingerprint" not in meta:
        return None
    return meta


def find_cached_agent_entry(
    agent_entries: list[dict[str, Any]],
    agent: str,
    titles: list[str],
) -> dict[str, Any] | None:
    """在缓存的 agent_entries 中查找匹配的条目。

    匹配条件：agent 相同 且 titles 集合完全相同（顺序无关）。
    """
    target = set(titles)
    for entry in agent_entries:
        if entry.get("agent") == agent and set(entry.get("titles", [])) == target:
            return entry
    return None


def is_agent_entry_fresh(
    entry: dict[str, Any],
    rules_hash: str,
    sections_hash: str,
    agent: str,
) -> bool:
    """检查 agent 缓存条目指纹是否与当前输入匹配。"""
    fingerprint = entry.get("fingerprint", {})
    if fingerprint.get("rules_sha256") != rules_hash:
        return False
    if agent == "preliminary-info-analyzer":
        if fingerprint.get("sections_sha256") != sections_hash:
            return False
    return True


def collect_kept_files(agent_entry: dict[str, Any]) -> set[str]:
    """收集一个 agent 缓存条目引用的所有文件路径。"""
    paths: set[str] = set()
    for part in agent_entry.get("parts", []):
        if part.get("file_path"):
            paths.add(part["file_path"])
    return paths
