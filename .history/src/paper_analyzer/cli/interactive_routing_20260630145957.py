#!/usr/bin/env python3
"""paper-routing: 交互式路由调整 TUI 工具

用法:
    paper-routing <routing_json_path>
    paper-routing <routing_json_path> --project-root /path/to/project
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style

from paper_analyzer._config import Config, set_default_config
from paper_analyzer.adapters.config_loader import load_agents
from paper_analyzer.errors import PaperAnalyzerError
from paper_analyzer.io import read_json, write_json

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════

@dataclass
class Section:
    """单个章节的路由状态。"""
    title: str
    char_count: int
    agents: list[str] = field(default_factory=list)
    skipped: bool = False
    _index: int = 0  # 原始序号，同 order 组内保持相对顺序

    def status_display(self, label_map: dict[str, str] | None = None) -> str:
        """生成状态显示文本。

        Args:
            label_map: agent 内部名 → 中文显示名（None 时用截断内部名）
        """
        if self.skipped:
            return "⏭ 跳过"
        if not self.agents:
            return "— 未分配"
        return self._agents_display(label_map)

    def _agents_display(self, label_map: dict[str, str] | None = None) -> str:
        if label_map:
            names = [label_map.get(a, a) for a in self.agents]
        else:
            names = [a[:12] for a in self.agents]
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} + {names[1]}"
        if len(names) == 3:
            return f"{names[0]} + {names[1]} + {names[2]}"
        return f"{names[0]} + {names[1]} + ... +{len(names) - 2}"


@dataclass
class RoutingData:
    """路由方案的完整内存表示。"""
    paper_name: str
    sections: list[Section]
    raw_extra: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RoutingData":
        """从 JSON dict 读取（兼容新旧格式）。"""
        all_sections: list[Section] = []
        seen: set[str] = set()

        for m in data.get("matches", []):
            # 兼容新旧格式
            if "agents" in m:
                agents = list(m["agents"])
            elif "agent" in m:
                agents = [m["agent"]]
            else:
                agents = []
            all_sections.append(Section(
                title=m["title"],
                char_count=m.get("char_count", 0),
                agents=agents,
                skipped=False,
                _index=len(all_sections),
            ))
            seen.add(m["title"])

        for u in data.get("unmatched", []):
            if u["title"] in seen:
                continue
            all_sections.append(Section(
                title=u["title"],
                char_count=u.get("char_count", 0),
                agents=[],
                skipped=u.get("reason") == "用户选择跳过",
                _index=len(all_sections),
            ))

        raw = {k: v for k, v in data.items()
               if k not in ("matches", "unmatched")}

        return cls(
            paper_name=data.get("paper_name", ""),
            sections=all_sections,
            raw_extra=raw,
        )

    @staticmethod
    def _parse_report_order(report_markdown: str) -> list[str]:
        """从 report_markdown 表格解析章节标题的原始顺序。"""
        titles: list[str] = []
        for line in report_markdown.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|")]
            # 格式: | N | title | agent |
            # parts[0] 为空, parts[1]=序号, parts[2]=标题, parts[3]=agent
            if len(parts) >= 3:
                try:
                    int(parts[1])  # 跳过表头和分隔行
                    titles.append(parts[2])
                except (ValueError, TypeError):
                    continue
        return titles

    def sort_by_document_order(self) -> None:
        """按论文原始章节顺序排序（从 report_markdown 重建）。"""
        report_md = self.raw_extra.get("report_markdown", "")
        if report_md:
            ordered = self._parse_report_order(report_md)
            if ordered:
                order_map = {t: i for i, t in enumerate(ordered)}
                self.sections.sort(key=lambda s: order_map.get(s.title, 9999))
                return
        # 回退：按 _index
        self.sections.sort(key=lambda s: s._index)

    def to_dict(self) -> dict[str, Any]:
        """转回 JSON dict（新格式：agents[] 数组）。"""
        matches: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        matched_agents: set[str] = set()

        for s in self.sections:
            if s.skipped or not s.agents:
                reason = "用户选择跳过" if s.skipped else "未匹配"
                unmatched.append({
                    "title": s.title,
                    "char_count": s.char_count,
                    "reason": reason,
                })
            else:
                matches.append({
                    "title": s.title,
                    "agents": s.agents,
                    "char_count": s.char_count,
                })
                matched_agents.update(s.agents)

        return {
            **self.raw_extra,
            "matches": matches,
            "unmatched": unmatched,
            "matched_agents": sorted(matched_agents),
        }

    @property
    def total(self) -> int:
        return len(self.sections)

    @property
    def assigned_count(self) -> int:
        return sum(1 for s in self.sections if s.agents and not s.skipped)

    @property
    def unassigned_count(self) -> int:
        return sum(1 for s in self.sections if not s.agents and not s.skipped)

    @property
    def skipped_count(self) -> int:
        return sum(1 for s in self.sections if s.skipped)


# ══════════════════════════════════════════════════════════════
# Agent 信息加载（从 YAML 注册表）
# ══════════════════════════════════════════════════════════════

def _build_agent_label_map(agents: list[dict]) -> dict[str, str]:
    """构建 agent 内部名 → 中文显示名映射，用于表格展示。"""
    return {a["name"]: a["section_label"] for a in agents}


def _load_agent_list(config: Config) -> list[dict[str, Any]]:
    """从 agents.yaml 注册表加载 Agent 列表。

    返回按 category + order 排序的列表：
    [{name, section_label, category, description, order}, ...]
    """
    agents_cfg = load_agents(config).get("agents", {})
    result: list[dict[str, Any]] = []
    for name, info in agents_cfg.items():
        result.append({
            "name": name,
            "section_label": info.get("section_label", name),
            "category": info.get("category", "other"),
            "description": info.get("description", ""),
            "order": info.get("order", 999),
        })
    result.sort(key=lambda a: (a["category"], a["order"]))
    return result


def _build_shortcut_map(agents: list[dict]) -> dict[str, dict]:
    """为 Agent 分配快捷键字母 a-z。

    Returns:
        {shortcut_char: agent_dict}
    """
    shortcuts: dict[str, dict] = {}
    for i, agent in enumerate(agents):
        if i < 26:
            ch = chr(ord('a') + i)
            shortcuts[ch] = agent
    return shortcuts


# ══════════════════════════════════════════════════════════════
# TUI 应用
# ══════════════════════════════════════════════════════════════

def _build_tui_app(
    routing_data: RoutingData,
    agents: list[dict],
    shortcut_map: dict[str, dict],
    agent_label_map: dict[str, str],
    output_path: Path,
) -> Application:
    """构建 prompt_toolkit Application。"""

    cursor_index = 0

    # ── 样式 ──
    style = Style.from_dict({
        "header": "bold white",
        "cursor-row": "bg:#005f87 bold",
        "agent-assigned": "#00af00",
        "agent-unassigned": "#af0000",
        "agent-skipped": "italic #888888",
        "shortcut-selected": "#00af00 bold",
        "shortcut-unselected": "#aaaaaa",
        "shortcut-skipped": "#af0000 italic",
        "stats-assigned": "#00af00",
        "stats-unassigned": "#af0000",
        "stats-skipped": "#888888",
    })

    # ── 按键绑定 ──
    kb = KeyBindings()

    @kb.add("j")
    @kb.add("down")
    def _move_down(event: Any) -> None:
        nonlocal cursor_index
        if routing_data.sections:
            cursor_index = (cursor_index + 1) % len(routing_data.sections)

    @kb.add("k")
    @kb.add("up")
    def _move_up(event: Any) -> None:
        nonlocal cursor_index
        if routing_data.sections:
            cursor_index = (cursor_index - 1) % len(routing_data.sections)

    @kb.add("g")
    def _goto_top(event: Any) -> None:
        nonlocal cursor_index
        cursor_index = 0

    @kb.add("G")
    def _goto_bottom(event: Any) -> None:
        nonlocal cursor_index
        if routing_data.sections:
            cursor_index = len(routing_data.sections) - 1

    @kb.add("s")
    def _toggle_skip(event: Any) -> None:
        if routing_data.sections:
            sec = routing_data.sections[cursor_index]
            sec.skipped = not sec.skipped
            if sec.skipped:
                sec.agents.clear()

    @kb.add(".")
    def _clear_row(event: Any) -> None:
        if routing_data.sections:
            sec = routing_data.sections[cursor_index]
            sec.agents.clear()
            sec.skipped = False

    # 为每个快捷键字母绑定 toggle
    for ch, agent_info in shortcut_map.items():
        agent_name: str = agent_info["name"]

        def _make_toggle(ch: str = ch, name: str = agent_name) -> None:
            @kb.add(ch)
            def _toggle(event: Any) -> None:
                nonlocal cursor_index
                if routing_data.sections:
                    sec = routing_data.sections[cursor_index]
                    sec.skipped = False
                    if name in sec.agents:
                        sec.agents.remove(name)
                    else:
                        sec.agents.append(name)

        _make_toggle()

    @kb.add("enter")
    def _confirm(event: Any) -> None:
        write_json(output_path, routing_data.to_dict())
        event.app.exit(result="saved")

    @kb.add("q")
    def _quit(event: Any) -> None:
        event.app.exit(result="quit")

    # ── 渲染函数 ──

    def _render_table() -> list[tuple[str, str]]:
        """渲染章节表格（使用中文 agent 名）。"""
        lines: list[tuple[str, str]] = []
        header = "  {:<3}  {:<32}  {:>6}  {}".format(
            "#", "章节", "字数", "分配 Agent"
        )
        lines.append(("class:header", header + "\n"))
        lines.append(("", "─" * 78 + "\n"))

        for i, sec in enumerate(routing_data.sections):
            prefix = "▶" if i == cursor_index else " "
            display = sec.status_display(agent_label_map)

            row_style = "class:cursor-row" if i == cursor_index else ""

            if sec.skipped:
                agent_style = "class:agent-skipped"
            elif sec.agents:
                agent_style = "class:agent-assigned"
            else:
                agent_style = "class:agent-unassigned"

            title_display = (
                sec.title[:30] + "…" if len(sec.title) > 31 else sec.title
            )

            row = (
                f" {prefix}{i + 1:<2}  "
                f"{title_display:<32}  "
                f"{sec.char_count:>6,}  "
            )
            lines.append((row_style, row))
            lines.append((f"{row_style} {agent_style}", display + "\n"))

        return lines

    def _render_stats() -> list[tuple[str, str]]:
        """渲染底部统计行。"""
        d = routing_data
        return [
            ("", "📊 总 "),
            ("", str(d.total)),
            ("", " 章 · 已分配 "),
            ("class:stats-assigned", str(d.assigned_count)),
            ("", " · 未分配 "),
            ("class:stats-unassigned", str(d.unassigned_count)),
            ("", " · 跳过 "),
            ("class:stats-skipped", str(d.skipped_count)),
        ]

    def _render_shortcuts() -> list[tuple[str, str]]:
        """渲染快捷键面板。"""
        lines: list[tuple[str, str]] = []
        lines.append((
            "class:header",
            "Agent 快捷键（按一下 = 切换选中/取消）:\n",
        ))

        # 按 category 分组
        by_category: dict[str, list[dict]] = {}
        for agent in agents:
            by_category.setdefault(agent["category"], []).append(agent)

        for cat, cat_agents in by_category.items():
            cat_parts: list[tuple[str, str]] = []
            for a in cat_agents:
                # 找到这个 agent 对应的快捷键
                ch: str | None = None
                for c, info in shortcut_map.items():
                    if info["name"] == a["name"]:
                        ch = c
                        break
                if ch is None:
                    continue

                # 判断当前行是否跳过
                cur = (
                    routing_data.sections[cursor_index]
                    if routing_data.sections
                    else None
                )
                is_current_skipped = cur.skipped if cur else False

                # 判断该 agent 是否被当前章节选中
                is_selected = (
                    cur and a["name"] in cur.agents if cur else False
                )

                if is_current_skipped:
                    style_key = "class:shortcut-skipped"
                elif is_selected:
                    style_key = "class:shortcut-selected"
                else:
                    style_key = "class:shortcut-unselected"

                cat_parts.append((
                    style_key,
                    f"[{ch}]{a['section_label']}  ",
                ))

            label = cat.replace("_", " ").title() + ": "
            lines.append(("", f"  {label:<22}"))
            lines.extend(cat_parts)
            lines.append(("", "\n"))

        lines.append(("", "─" * 78 + "\n"))
        lines.append(("class:shortcut-selected", "[s] ⏭ 跳过  "))
        lines.append(("", "[.] ⊘ 清除  "))
        lines.append(("class:header", "[Enter] ✅ 确认写入  "))
        lines.append(("class:shortcut-skipped", "[q] ❌ 放弃\n"))

        return lines

    def _get_formatted_text() -> FormattedText:
        """构建完整的 FormattedText（无框线，避免中文双宽字符乱码）。"""
        parts: list[tuple[str, str]] = []

        # 标题 —— 使用简单分隔线代替 ╔══╗ 框线
        title = f"论文路由匹配调整 · {routing_data.paper_name}"
        parts.append(("class:header", "━" * 78 + "\n"))
        parts.append(("class:header", f"  {title[:74]}\n"))
        parts.append(("class:header", "━" * 78 + "\n\n"))

        # 表格
        parts.extend(_render_table())
        parts.append(("", "\n"))

        # 统计
        parts.extend(_render_stats())
        parts.append(("", "\n\n"))

        # 快捷键
        parts.extend(_render_shortcuts())

        return FormattedText(parts)

    # ── 布局 ──
    main_window = Window(
        content=FormattedTextControl(_get_formatted_text),
        always_hide_cursor=True,
    )

    root_container = HSplit([main_window])
    layout = Layout(root_container)

    return Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=True,
    )


# ══════════════════════════════════════════════════════════════
# CLI 入口（遵循现有模式：main → _setup_cli → _run）
# ══════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="paper-routing: 交互式路由调整 TUI 工具"
    )
    parser.add_argument(
        "routing_json",
        help="_routing.json 的路径",
    )
    parser.add_argument(
        "--project-root", default=None, help="项目根目录"
    )
    parser.add_argument(
        "--config-dir", default=None, help="自定义配置目录"
    )
    parser.add_argument(
        "--output-dir", default=None, help="输出目录"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="详细输出"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="静默模式"
    )

    _setup_cli(parser.parse_args())


def _setup_cli(args: argparse.Namespace) -> None:
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)s: %(message)s",
            stream=sys.stderr,
        )
    elif args.quiet:
        logging.basicConfig(
            level=logging.WARNING,
            format="%(levelname)s: %(message)s",
            stream=sys.stderr,
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            stream=sys.stderr,
        )

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


def _run(args: argparse.Namespace, config: Config) -> None:
    routing_path = Path(args.routing_json)
    if not routing_path.exists():
        logger.error("错误：routing JSON 不存在 —— %s", routing_path)
        sys.exit(1)

    # 1. 加载数据（兼容新旧格式）
    raw_data = read_json(routing_path)
    routing_data = RoutingData.from_json(raw_data)
    logger.info(
        "已加载: %s 个章节（已分配 %s）",
        routing_data.total,
        routing_data.assigned_count,
    )

    # 2. 加载 Agent 列表（从 YAML 注册表）
    agents = _load_agent_list(config)
    shortcut_map = _build_shortcut_map(agents)
    agent_label_map = _build_agent_label_map(agents)

    if len(agents) > 26:
        logger.warning("Agent 数量超过 26 个，仅前 26 个分配快捷键")

    # 3. 按 report_markdown 重建文档原始顺序
    routing_data.sort_by_document_order()

    # 4. 启动 TUI
    app = _build_tui_app(
        routing_data, agents, shortcut_map, agent_label_map, routing_path,
    )
    result = app.run()

    # 5. 退出后输出
    if result == "saved":
        print(f"\n✅ 已写入 {routing_path}\n")
        print("最终分配方案:")
        for i, sec in enumerate(routing_data.sections):
            print(f"  {i + 1:>2}. {sec.title[:40]:<40} → {sec.status_display(agent_label_map)}")
        print(
            f"\n  📊 总 {routing_data.total} 章 · "
            f"已分配 {routing_data.assigned_count} · "
            f"未分配 {routing_data.unassigned_count} · "
            f"跳过 {routing_data.skipped_count}"
        )
    else:
        print("\n❌ 已放弃修改")


if __name__ == "__main__":
    main()
