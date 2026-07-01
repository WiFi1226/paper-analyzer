"""Agent 输出保存 —— 将 Agent 分析结果写入文件。

由 PostToolUse Hook 在串行 subagent 完成后直接调 save_agent_output()。
"""

import logging
from pathlib import Path

from paper_analyzer._config import Config, get_default_config

logger = logging.getLogger(__name__)


def save_agent_output(
    agent: str,
    paper_name: str,
    output_text: str,
    part_num: int = 1,
    split_total: int = 0,
    append: bool = False,
    config: Config | None = None,
) -> Path:
    """将 Agent 输出保存到 _contents/ 目录。

    Args:
        agent:        Agent 名称
        paper_name:   论文名
        output_text:  Agent 输出正文
        part_num:     分片编号（默认 1，非拆分场景）
        split_total:  总分片数（0 表示未拆分）
        append:       是否追加到已有文件（默认 False，同名时覆盖）
        config:       Config 对象

    Returns:
        输出文件路径
    """
    if config is None:
        config = get_default_config()

    output_dir = config.outputs_dir(paper_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    if split_total > 1:
        filename = f"{agent}_part{part_num}.md"
    else:
        filename = f"{agent}.md"

    output_path = output_dir / filename
    output_chars = len(output_text)

    if append and output_path.exists():
        existing = output_path.read_text(encoding="utf-8").rstrip()
        merged = existing + "\n\n---\n\n" + output_text
        output_path.write_text(merged, encoding="utf-8")
        logger.info(
            "已追加: %s（原 %s 字符 + 新 %s 字符）",
            filename, len(existing), output_chars,
        )
    else:
        output_path.write_text(output_text, encoding="utf-8")
        logger.info("已保存: %s（%s 字符）", filename, output_chars)

    return output_path
