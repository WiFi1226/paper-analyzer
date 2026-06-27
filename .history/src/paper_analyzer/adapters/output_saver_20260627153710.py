"""Agent 输出保存 —— 将 Agent 分析结果写入文件。

核心函数从原 invoke/hook.py 提取，不含 VS Code 对接逻辑（stdin JSON 解析）。
VS Code 用户需在自己的项目中维护对接脚本。
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from paper_analyzer._config import Config, get_default_config
from paper_analyzer.errors import OutputSaveError

logger = logging.getLogger(__name__)


def extract_result_from_jsonl(output_file: str, timeout: float = 300) -> str | None:
    """从异步 agent 的 JSONL output 文件中提取最终结果文本。

    文件结构：每行一个 JSON，最后一行 type=assistant 的 message.content
    中第一个 type=text 的 text 字段即为 agent 的分析报告。

    Args:
        output_file: outputFile 路径
        timeout:     等待文件就绪的超时秒数（默认 300）

    Returns:
        分析报告文本，提取失败返回 None
    """
    path = Path(output_file)
    deadline = time.time() + timeout

    # 等待文件出现并稳定（大小不再增长）
    last_size = -1
    stable_rounds = 0
    while time.time() < deadline:
        if not path.exists():
            time.sleep(2)
            continue
        cur = path.stat().st_size
        if cur == last_size:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_size = cur
        if stable_rounds >= 2 and cur > 0:
            break
        time.sleep(2)
    else:
        logger.warning("JSONL 文件超时 %s（%ds）", output_file, timeout)
        return None

    # 从最后一行向前找 type=assistant 的最终输出
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("无法读取 JSONL %s: %s", output_file, e)
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") not in ("assistant",):
            continue
        msg = entry.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue

        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        return text
        elif isinstance(content, str) and content.strip():
            return content.strip()

    logger.warning("JSONL %s 中未找到 assistant 输出文本", output_file)
    return None

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


# ── 异步待提取队列 ─────────────────────────────────────────

PENDING_FILE = ".pending.json"


def _pending_path(output_dir: Path) -> Path:
    return output_dir / PENDING_FILE


def record_pending(
    agent: str,
    paper_name: str,
    output_file: str,
    part_num: int,
    agent_id: str,
    output_dir: Path,
) -> None:
    """向 .pending.json 追加一条待提取记录（用于异步 agent）。

    Raises:
        OutputSaveError: .pending.json 格式损坏
        OSError: 文件读写失败
    """
    path = _pending_path(output_dir)
    entry = {
        "agent": agent,
        "paper_name": paper_name,
        "output_file": output_file,
        "part_num": part_num,
        "agent_id": agent_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                raise OutputSaveError(
                    f".pending.json 格式异常：期望 JSON 数组，实际为 {type(existing).__name__}"
                )
        except json.JSONDecodeError as e:
            raise OutputSaveError(f".pending.json 格式损坏，无法解析: {e}") from e

    existing.append(entry)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def drain_pending(output_dir: Path) -> list[str]:
    """处理 .pending.json，将已完成的异步 agent 输出写入 _contents/。

    供 assemble 阶段调用，作为兜底机制。

    Returns:
        成功提取的 agent 名列表（可重复，表示多次追加）。

    Raises:
        OutputSaveError: .pending.json 格式损坏
    """
    path = _pending_path(output_dir)
    if not path.exists():
        return []

    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise OutputSaveError(f".pending.json 格式损坏，无法解析: {e}") from e

    if not isinstance(entries, list):
        raise OutputSaveError(
            f".pending.json 格式异常：期望 JSON 数组，实际为 {type(entries).__name__}"
        )

    succeeded: list[str] = []
    remaining: list[dict] = []

    for entry in entries:
        agent = entry.get("agent", "unknown")
        paper_name = entry.get("paper_name", "unknown")
        output_file = entry.get("output_file", "")
        part_num = entry.get("part_num", 1)

        if not output_file:
            remaining.append(entry)
            continue

        text = extract_result_from_jsonl(output_file, timeout=0)
        if text is None:
            logger.warning("drain_pending 失败: agent=%s outputFile=%s", agent, output_file)
            remaining.append(entry)
            continue

        save_agent_output(
            agent=agent,
            paper_name=paper_name,
            output_text=text,
            part_num=part_num,
            append=True,
        )
        succeeded.append(agent)
        logger.info("drain_pending 完成: agent=%s (%s 字符)", agent, len(text))

    # 重写 .pending.json（仅保留失败的条目）
    if remaining:
        path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        try:
            path.unlink()
        except OSError:
            path.write_text("[]", encoding="utf-8")

    return succeeded
