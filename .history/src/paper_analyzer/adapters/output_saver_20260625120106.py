"""Agent 输出保存 —— 将 Agent 分析结果写入文件。

核心函数从原 invoke/hook.py 提取，不含 VS Code 对接逻辑（stdin JSON 解析）。
VS Code 用户需在自己的项目中维护对接脚本。
"""

import logging
import re
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config, get_default_config

logger = logging.getLogger(__name__)


def parse_agent_description(description: str) -> dict[str, Any]:
    """从 Agent description 中提取结构化字段。

    description 格式:
        "<agent名>[ Part<N>]: paper=<paper_name> [chapter_titles...]"

    Returns:
        {
            paper_name: str | None,
            part_num: int,
            agent: str | None,
        }
    """
    result: dict[str, Any] = {
        "paper_name": None,
        "part_num": 1,
        "agent": None,
    }

    colon_idx = description.find(":")
    if colon_idx == -1:
        return result

    prefix = description[:colon_idx].strip()

    part_match = re.search(r"Part(\d+)", prefix)
    if part_match:
        result["part_num"] = int(part_match.group(1))
        result["agent"] = prefix[:part_match.start()].strip()
    else:
        result["agent"] = prefix

    paper_match = re.search(r"paper=(\S+)", description[colon_idx:])
    if paper_match:
        result["paper_name"] = paper_match.group(1)

    return result


def extract_agent_text(tool_response: Any) -> str:
    """从 Agent tool_response 中提取输出正文。

    tool_response 可能是：
      - dict（含 content[].text）
      - str（JSON 字符串 或 纯文本）
    """
    import json

    if isinstance(tool_response, dict):
        if "content" in tool_response:
            parts = [
                item["text"] for item in tool_response["content"]
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if parts:
                return "\n".join(parts)
        try:
            return json.dumps(tool_response, ensure_ascii=False, indent=2)
        except Exception:
            return str(tool_response)

    if not isinstance(tool_response, str):
        tool_response = str(tool_response)

    try:
        resp = json.loads(tool_response)
    except (json.JSONDecodeError, TypeError):
        return tool_response

    if isinstance(resp, dict) and "content" in resp:
        parts = [
            item["text"] for item in resp["content"]
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if parts:
            return "\n".join(parts)

    return tool_response


def save_agent_output(
    agent: str,
    paper_name: str,
    output_text: str,
    part_num: int = 1,
    split_total: int = 0,
    config: Config | None = None,
) -> Path:
    """将 Agent 输出保存到 _outputs/ 目录。

    Args:
        agent:        Agent 名称
        paper_name:   论文名
        output_text:  Agent 输出正文
        part_num:     分片编号（默认 1，非拆分场景）
        split_total:  总分片数（0 表示未拆分）
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
    output_path.write_text(output_text, encoding="utf-8")
    logger.info("已保存: %s（%s 字符，约 %s tokens）", filename, output_chars, output_chars // 2)

    return output_path
