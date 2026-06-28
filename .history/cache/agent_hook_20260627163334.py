#!/usr/bin/env python3
"""VS Code PostToolUse hook —— 捕获串行 Agent 输出并直接保存。

工作原理：由 PostToolUse 触发，通过 stdin 接收 tool 调用信息（JSON）。
串行 subagent 完成后，tool_response 即为分析报告文本，直接保存到 _contents/。

诊断方式：output/<paper>/cache/_contents/hook.log
"""

import json
import logging
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config
from paper_analyzer.adapters.output_saver import save_agent_output

# ── 日志 ──────────────────────────────────────────────────────────
_log_file: Path | None = None

logger = logging.getLogger("agent_hook")


def _log(msg: str) -> None:
    """写诊断日志（stderr + 文件双写，确保不丢失）。"""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr)
    if _log_file is not None:
        try:
            with open(_log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


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


# ── 主逻辑 ────────────────────────────────────────────────────────

def main() -> None:
    try:
        _handle()
    except Exception:
        tb = traceback.format_exc()
        # stderr 输出（VS Code 可能不展示，但保留用于调试）
        print(tb, file=sys.stderr)
        # 写入日志文件（确保异常可被诊断）
        if _log_file is not None:
            try:
                with open(_log_file, "a", encoding="utf-8") as f:
                    f.write(tb + "\n")
            except OSError:
                pass
        print('{"decision":"allow"}')


def _handle() -> None:
    data = json.load(sys.stdin)

    # 只处理 Agent 工具调用
    if data.get("tool_name") not in ("Agent", "runSubagent"):
        print('{"decision":"allow"}')
        return

    desc = data.get("tool_input", {}).get("description", "")
    parsed = parse_agent_description(desc)
    if parsed["paper_name"] is None:
        _log(f"description 格式异常，无法解析 paper_name: {desc[:100]}")
        print('{"decision":"allow"}')
        return

    config = Config()
    paper_name = parsed["paper_name"]

    # 设置日志文件
    output_dir = config.outputs_dir(paper_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    global _log_file
    _log_file = output_dir / "hook.log"

    agent = parsed["agent"] or "unknown"
    _log(f"hook 触发: agent={agent} paper={paper_name}")

    raw_response = data.get("tool_response", "")

    # ── 分支：异步启动 vs 同步返回 ──────────────────────────────
    if isinstance(raw_response, dict) and raw_response.get("isAsync") is True:
        output_file = raw_response.get("outputFile", "")
        agent_id = raw_response.get("agentId", "")
        _log(f"异步启动: agent={agent} outputFile={output_file}")

        if output_file:
            record_pending(agent, paper_name, output_file, parsed["part_num"], agent_id, output_dir)
            _log(f"已记录待提取: .pending.json + {output_file}")
        else:
            _log("异步启动但无 outputFile，跳过")

        # 异步不写入 .md，也不覆写 tool_response（让 Claude 看到原始元数据）
        print('{"decision":"allow"}')
        return

    # ── 同步分支：正常提取并保存 ──────────────────────────────
    output_text = extract_agent_text(raw_response)
    _log(f"提取输出: {len(raw_response) if isinstance(raw_response, str) else type(raw_response).__name__} → {len(output_text)} 字符")

    output_path = save_agent_output(
        agent=agent,
        paper_name=paper_name,
        output_text=output_text,
        part_num=parsed["part_num"],
        config=config,
    )
    _log(f"已保存: {output_path.name}")

    # 覆写 tool_response 为简短摘要
    output_chars = len(output_text)
    summary = (
        f" Agent 输出已保存: {output_path.name}"
        f"（{output_chars:,} 字符）→ {output_path.parent}"
    )
    print(json.dumps({"decision": "allow", "tool_response": summary}))


if __name__ == "__main__":
    main()
