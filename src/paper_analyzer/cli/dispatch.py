#!/usr/bin/env python3
"""paper-dispatch: _prompts.json → _dispatch.json

为每个 agent 调用生成精确的 description 字符串，消除 Claude 手动拼接的格式风险。

用法:
    paper-dispatch <prompts_json>
    paper-dispatch <prompts_json> -o <dispatch_json>

输出 _dispatch.json 结构:
    {
      "paper_name": "...",
      "calls": [
        {
          "sequence": 1,
          "agent": "introduction-analyzer",
          "description": "introduction-analyzer: paper=... 章节A, 章节B",
          "prompt_index": 0,
          "chapter_titles": ["章节A", "章节B"],
          "total_chars": 22132
        }
      ]
    }
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.errors import PaperAnalyzerError
from paper_analyzer.io import write_json

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-dispatch: 为每个 agent 调用生成 dispatch 清单"
    )
    parser.add_argument("prompts_json", help="_prompts.json 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 _dispatch.json 路径（默认与输入同目录）")
    parser.add_argument("--project-root", default=None, help="项目根目录")
    parser.add_argument("--config-dir", default=None, help="自定义配置目录")
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")

    args = parser.parse_args()
    _setup_cli(args)


def _setup_cli(args: argparse.Namespace) -> None:
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s", stream=sys.stderr)
    elif args.quiet:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s", stream=sys.stderr)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    config = Config(
        project_root=args.project_root,
        config_dir=args.config_dir,
        output_dir=args.output_dir,
    )
    set_default_config(config)

    try:
        _run(args, config)
    except PaperAnalyzerError as e:
        logger.error("错误: %s", e)
        sys.exit(1)
    except FileNotFoundError as e:
        logger.error("错误: 文件不存在 —— %s", e)
        sys.exit(1)
    except (ValueError, KeyError) as e:
        logger.error("错误: 数据格式异常 —— %s", e)
        sys.exit(1)


def build_description(agent: str, paper_name: str, chapter_titles: list[str]) -> str:
    """构造 Agent description 字符串。

    格式: "<agent>: paper=<paper_name> <chapter_titles>"
    示例: "chart-results-analyzer: paper=Freeman_2025... IV. Baseline Results"
    """
    titles_str = ", ".join(chapter_titles)
    return f"{agent}: paper={paper_name} {titles_str}"


def _run(args: argparse.Namespace, config: Config) -> None:
    prompts_path = Path(args.prompts_json)
    if not prompts_path.exists():
        logger.error("错误: _prompts.json 不存在 —— %s", prompts_path)
        sys.exit(1)

    with open(prompts_path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    paper_name = data.get("paper_name", "")
    prompts = data.get("prompts", [])

    if not paper_name:
        raise ValueError("_prompts.json 缺少 paper_name 字段")
    if not prompts:
        logger.warning("_prompts.json 中 prompts 数组为空")

    logger.info("[读取] %s 条 prompt, paper=%s", len(prompts), paper_name)

    calls: list[dict[str, Any]] = []
    for idx, prompt_entry in enumerate(prompts):
        agent = prompt_entry.get("agent", "")
        chapter_titles = prompt_entry.get("chapter_titles", [])
        total_chars = prompt_entry.get("total_chars", 0)

        if not agent:
            logger.warning("[跳过] prompts[%s] 缺少 agent 字段", idx)
            continue

        description = build_description(agent, paper_name, chapter_titles)

        calls.append({
            "sequence": idx + 1,
            "agent": agent,
            "description": description,
            "prompt_index": idx,
            "chapter_titles": chapter_titles,
            "total_chars": total_chars,
        })

    logger.info("[构建] %s 条 dispatch 记录", len(calls))

    result = {
        "paper_name": paper_name,
        "calls": calls,
        "total_calls": len(calls),
    }

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = prompts_path.parent / "_dispatch.json"

    write_json(output_path, result)
    logger.info("已保存: %s", output_path)


if __name__ == "__main__":
    main()
