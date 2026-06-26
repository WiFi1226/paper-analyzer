#!/usr/bin/env python3
"""VS Code PostToolUse hook —— 捕获 Agent 输出并自动保存。

这是 VS Code 特有的对接脚本，不属于 paper_analyzer 通用包。
工作原理：由 PostToolUse 触发，通过 stdin 接收 tool 调用信息（JSON），
提取 agent 输出正文，写入对应 paper 的 _outputs/ 目录。

诊断方式：查看 output/<paper>/cache/_outputs/_hook.log
"""

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from paper_analyzer._config import Config
from paper_analyzer.adapters.output_saver import save_agent_output, parse_agent_description

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


# ── 主逻辑 ────────────────────────────────────────────────────────

def main() -> None:
    try:
        _handle()
    except Exception:
        traceback.print_exc(file=sys.stderr)
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
    _log_file = output_dir / "_hook.log"

    agent = parsed["agent"] or "unknown"
    _log(f"hook 触发: agent={agent} paper={paper_name}")

    save_agent_output(
        agent=agent,
        paper_name=paper_name,
        output_text=data.get("tool_response", ""),
        part_num=parsed["part_num"],
        config=config,
    )

    print('{"decision":"allow"}')
# TODO 修改这个

if __name__ == "__main__":
    main()
