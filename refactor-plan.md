# 计划：CLI 交互式路由调整工具 `paper-routing`

## 一、背景

当前 `paper-analyzer` 工作流的 Step 1（交互式匹配审核）由 SKILL.md 中的 Agent 使用 AskUserQuestion 工具完成，需要 10~15 轮对话问答，效率低、体验割裂。改为一个独立的 CLI TUI 工具，用户在终端中一次性完成所有调整。

## 二、涉及文件

### 2.1 新建文件

```
src/paper_analyzer/cli/interactive_routing.py  ← TUI 核心逻辑
```

### 2.2 修改文件

```
pyproject.toml           ← 添加依赖 + entry point
src/paper_analyzer/cli/invoke.py   ← 支持 agents[] 新格式
```

---

## 三、`pyproject.toml` 变更

### 3.1 添加可选依赖

```toml
[project.optional-dependencies]
pdf = ["pdfminer.six>=20221105"]
tui = ["prompt-toolkit>=3.0"]       # ← 新增
dev = ["pytest>=7.0"]
```

### 3.2 添加 entry point

```toml
[project.scripts]
# ... 现有不变 ...
paper-routing = "paper_analyzer.cli.interactive_routing:main"  # ← 新增
```

---

## 四、`_routing.json` 数据模型变更

### 4.1 当前格式（输入）

```json
{
  "matches": [
    {"title": "I. Introduction", "agent": "introduction-analyzer", "char_count": 14133}
  ],
  "unmatched": [
    {"title": "II. Background", "char_count": 2713, "reason": "未匹配"}
  ],
  "matched_agents": ["introduction-analyzer"],
  "paper_name": "Freeman_2025_...",
  "filter_applied": null,
  "total_chapters": 9,
  "total_matches": 3,
  "report_markdown": "..."
}
```

### 4.2 新格式（输出，支持多选）

```json
{
  "matches": [
    {
      "title": "I. Introduction",
      "agents": ["introduction-analyzer", "literature-review-analyzer"],
      "char_count": 14133
    }
  ],
  "unmatched": [
    {"title": "II. Background", "char_count": 2713, "reason": "用户选择跳过"}
  ],
  "matched_agents": ["introduction-analyzer", "literature-review-analyzer"],
  "paper_name": "Freeman_2025_...",
  "filter_applied": null,
  "total_chapters": 9,
  "total_matches": 3,
  "report_markdown": "..."
}
```

关键变更点：
- **`matches[].agent`（字符串）→ `matches[].agents`（字符串数组）**——支持一个章节绑定多个 Agent
- **保留 `report_markdown` 等原始字段不变**
- **`unmatched[].reason` = `"用户选择跳过"` 表示跳过，`"未匹配"` 表示真的未匹配**

### 4.3 兼容性：从旧格式读取

`main()` 函数在读取 `_routing.json` 时，需要兼容旧格式：

```python
# 兼容新旧格式
if "agent" in match:
    agents = [match["agent"]]      # 旧格式：单字符串
elif "agents" in match:
    agents = match["agents"]       # 新格式：数组
else:
    agents = []                    # 无分配
```

---

## 五、`interactive_routing.py` 完整规范

### 5.1 入口与参数

```
用法: paper-routing <routing_json_path>

参数:
  routing_json_path    _routing.json 的绝对路径（由 paper-orchestrate 输出）
```

`main()` 函数：

```python
def main():
    # 1. 解析命令行参数
    # 2. 读取 _routing.json（兼容新旧格式）
    # 3. 扫描 .claude/agents/（从 routing_json_path 往上找项目根）
    # 4. 启动 TUI Application
    # 5. 退出后根据结果决定：写入 / 放弃
```

### 5.2 查找项目根与 Agent 目录

```python
def find_project_root(routing_path: Path) -> Path:
    """从 _routing.json 路径倒推项目根。

    路径模式: output/<paper>/cache/_prompts/_routing.json
      ↑ 项目根          ↑ 上溯 4 层
    """
    for parent in routing_path.parents:
        if (parent / ".claude").exists():
            return parent
    # 回退：通过当前工作目录找
    cwd = Path.cwd()
    for parent in cwd.parents:
        if (parent / ".claude").exists():
            return parent
    return cwd


def scan_agents(agents_dir: Path) -> dict[str, list[dict]]:
    """扫描 .claude/agents/ 目录。

    返回: {
        "paper-structure": [
            {"name": "introduction-analyzer", "description": "分析论文引言段落..."},
            ...
        ],
        "empirical-methodology": [...],
        "results-reporting": [...],
    }
    """
```

### 5.3 数据模型（内存中）

```python
@dataclass
class Section:
    title: str
    char_count: int
    agents: list[str]       # 章节绑定的 Agent 列表（空列表 = 未分配）
    skipped: bool           # True = 跳过此章

    @property
    def status_display(self) -> str:
        if self.skipped:
            return "⏭ 跳过"
        if not self.agents:
            return "— 未分配"
        # 显示格式见下文 5.5
        return self._agents_display()

    def _agents_display(self) -> str:
        names = [a[:12] for a in self.agents]  # 截断
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} + {names[1]}"
        if len(names) == 3:
            return f"{names[0]} + {names[1]} + {names[2]}"
        # >=4 个，折叠显示
        return f"{names[0]} + {names[1]} + ... +{len(names)-2}"
```

### 5.4 数据模型的读写转换

```python
@dataclass
class RoutingData:
    paper_name: str
    sections: list[Section]
    raw_extra: dict      # 保留所有原始字段，写入时合并

    @classmethod
    def from_json(cls, data: dict) -> "RoutingData":
        """从 JSON dict 读取（兼容新旧格式）"""
        all_sections = []
        seen = set()

        for m in data.get("matches", []):
            if "agent" in m:
                agents = [m["agent"]]
            elif "agents" in m:
                agents = m["agents"]
            else:
                agents = []
            all_sections.append(Section(
                title=m["title"],
                char_count=m["char_count"],
                agents=agents,
                skipped=False,
            ))
            seen.add(m["title"])

        for u in data.get("unmatched", []):
            if u["title"] in seen:
                continue
            all_sections.append(Section(
                title=u["title"],
                char_count=u["char_count"],
                agents=[],
                skipped=u.get("reason") == "用户选择跳过",
            ))

        raw = {k: v for k, v in data.items()
               if k not in ("matches", "unmatched")}

        return cls(paper_name=data.get("paper_name", ""),
                   sections=all_sections,
                   raw_extra=raw)

    def to_dict(self) -> dict:
        """转回 JSON dict"""
        matches = []
        unmatched = []
        matched_agents = set()

        for s in self.sections:
            if s.skipped or not s.agents:
                reason = "用户选择跳过" if s.skipped else "未匹配"
                unmatched.append({"title": s.title,
                                  "char_count": s.char_count,
                                  "reason": reason})
            else:
                matches.append({"title": s.title,
                                "agents": s.agents,
                                "char_count": s.char_count})
                matched_agents.update(s.agents)

        return {
            **self.raw_extra,
            "matches": matches,
            "unmatched": unmatched,
            "matched_agents": sorted(matched_agents),
        }
```

### 5.5 界面布局

TUI 界面分为三个区域（从上到下）：

```
┌──────────────────────────────────────────────────────────────────┐
│  ╔══════════════════════════════════════════════════════════════╗ │
│  ║       论文路由匹配调整 · Freeman_2025_Overlapping_...       ║ │
│  ╚══════════════════════════════════════════════════════════════╝ │
│                                                                  │
│  ┌─────┬───────────────────────────┬──────┬────────────────────┐ │
│  │  #  │ 章节                      │ 字数  │ 分配 Agent         │ │
│  ├─────┼───────────────────────────┼──────┼────────────────────┤ │
│  │  1  │ 前置信息                  │ 1782 │ ⏭ 跳过            │ │
│  │ ▶2◀ │ I. Introduction           │ 14133│ intro              │ │  ← 高亮行
│  │  3  │ II. Background            │ 2713 │ — 未分配           │ │
│  │  4  │ III. Data & Methodology   │ 17626│ causal + desc      │ │  ← 多选
│  │  5  │ IV. Baseline Results      │ 23474│ chart + causal +...│ │  ← 折叠
│  │  6  │ V. Addressing Endogeneity │ 23250│ — 未分配           │ │
│  │  7  │ VI. Effects on Firm Value │ 9572 │ — 未分配           │ │
│  │  8  │ VII. Additional Analyses  │ 22719│ chart              │ │
│  │  9  │ VIII. Conclusion          │ 17295│ — 未分配           │ │
│  └─────┴───────────────────────────┴──────┴────────────────────┘ │
│                                                                  │
│  📊 总 9 章 · 已分配 4 · 未分配 4 · 跳过 1                      │
│                                                                  │
│  Agent 快捷键（按一下 = 切换选中/取消）:                          │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ paper-structure:     [a]intro  [b]prelim  [c]lit-rev        ││
│  │ empirical-method:    [d]causal [e]desc    [f]struct [g]evid ││
│  │ results-reporting:   [h]chart                               ││
│  │ ─────────────────────────────────────────────────────────── ││
│  │ [s] ⏭ 跳过  [.] ⊘ 清除  [Enter] ✅ 确认写入  [q] ❌ 放弃  ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

### 5.6 快捷键映射

| 按键 | 功能 | 说明 |
|------|------|------|
| `j` / `↓` | 向下移动光标 | 循环滚动 |
| `k` / `↑` | 向上移动光标 | 循环滚动 |
| `g` | 跳到第一行 | |
| `G` | 跳到最后一行 | |
| `a` ~ `h` | toggle Agent | 每个字母对应一个 Agent，当前行按一下=选中，再按=取消 |
| `s` | toggle 跳过 | 当前行切换跳过/不跳过。切换跳过时会清除所有 Agent |
| `.` | 清除 | 清除当前行所有 Agent + 取消跳过 |
| `Enter` | 确认写入 | 写回 `_routing.json` 并退出 |
| `q` | 放弃退出 | 不保存退出 |

Agent 的快捷键映射规则（由 scan_agents 顺序决定）：

```
顺序扫描 .claude/agents/ 下的所有 agent 文件：
  第 1 个 → a
  第 2 个 → b
  第 3 个 → c
  第 4 个 → d
  第 5 个 → e
  第 6 个 → f
  第 7 个 → g
  第 8 个 → h
```

当前的 8 个 Agent 正好映射 `a`~`h`，未来超过 8 个时，用 `i`~`z` 扩展。

### 5.7 动态着色规则

| 元素 | 颜色 | 说明 |
|------|------|------|
| 高亮行（`▶` 标记） | `bold cyan` 背景 | 光标所在行 |
| 已分配的 Agent | `green` | 表格中显示 |
| 未分配 | `red` | `— 未分配` |
| 跳过 | `dim` | `⏭ 跳过` |
| 快捷键面板 - 已选中 | `green` 字母 + 全名 | 如 `[a]intro` 的 a 绿色 |
| 快捷键面板 - 未选中 | `white` 字母 + `dim` 全名 | 如 `[b]prelim` 的 b 白色 |
| 快捷键面板 - 当前章节已跳过 | `red` 删除线 | 整行快捷键变暗 |
| 统计数字 | `green`(已分配) `red`(未分配) `dim`(跳过) | |

### 5.8 刷新逻辑

每次按键后，**只刷新必要的部分**而非全屏重绘：

1. 光标所在行（更改 `▶` 位置）
2. 被修改的章节的"分配 Agent"列
3. 底部统计行
4. 快捷键面板的着色

使用 `prompt_toolkit` 的 `invalidate()` 机制实现增量刷新。

### 5.9 退出后输出

退出时在终端打印最终分配概览（非 TUI 模式，普通文本）：

```
✅ 已写入 /path/to/_routing.json

最终分配方案:
  1. 前置信息                    → ⏭ 跳过
  2. I. Introduction             → introduction-analyzer
  3. II. Background              → causal-inference-analyzer
  4. III. Data & Methodology     → causal-inference-analyzer + descriptive-measurement-analyzer
  5. IV. Baseline Results        → chart-results-analyzer
  ...
  📊 总 9 章 · 已分配 5 · 未分配 3 · 跳过 1
```

放弃退出时：

```
❌ 已放弃修改
```

---

## 六、`invoke.py` 的兼容性修改

`paper-invoke`（`cli/invoke.py`）生成的 `_dispatch.json` 目前为每个匹配章节生成**一个** dispatch call。多选后，一个章节可能对应多个 Agent，需要为每个 Agent 各生成一个 dispatch call。

### 6.1 当前逻辑（伪代码）

```python
for match in routing["matches"]:
    calls.append({
        "agent": match["agent"],       # 字符串
        "prompt_index": ...,
        "description": ...,
    })
```

### 6.2 修改后逻辑

```python
# 兼容新旧格式
for match in routing["matches"]:
    agents = match.get("agents", [])
    if not agents and "agent" in match:
        agents = [match["agent"]]
    for agent in agents:
        calls.append({
            "agent": agent,
            "prompt_index": ...,
            "description": ...,
        })
```

### 6.3 修改的文件

```
src/paper_analyzer/cli/invoke.py
```

涉及的具体函数位置由实现者定位。

---

## 七、与 SKILL.md 的集成（参考，供 testb 项目使用）

`paper-analyzer` 包的 agent 不需要关心这部分，但可以作为上下文参考。SKILL.md 中 Step 1 会改为：

```markdown
## Step 1：交互式匹配审核

1. **展示匹配报告** 从 Step 0 终端输出中提取 `routing_json_path`，
   读取该文件，在对话中渲染一张匹配概览表格。

2. **启动 TUI 调整工具**

   ```bash
   paper-routing "<routing_json_path>"
   ```

   提示用户切到终端操作。

3. **确认最终方案** 工具退出后，读取最新的 `_routing.json`，
   在对话中渲染最终分配表格，用户确认后进入 Step 2。
```

---

## 八、实现步骤（按顺序执行）

| # | 步骤 | 文件 | 说明 |
|---|------|------|------|
| 1 | 添加依赖 + entry point | `pyproject.toml` | 添加 `tui` optional-dependencies + `paper-routing` 入口 |
| 2 | 创建 TUI 核心脚本 | `cli/interactive_routing.py` | 实现完整的 `prompt_toolkit` TUI |
| 3 | 修改 invoke 支持多选 | `cli/invoke.py` | 兼容 `agents[]` 新格式 |
| 4 | 安装验证 | 终端 | `pip install -e ".[tui]"` + 运行 `paper-routing --help` |

---

## 九、验收标准

1. `paper-routing <routing_json_path>` 能正常启动 TUI 界面
2. `j`/`k` 上下移动光标，循环滚动
3. 字母键（`a`~`h`）toggle Agent，快捷键面板实时着色反馈
4. `s` toggle 跳过，`. ` 清除所有
5. `Enter` 写入并退出，`_routing.json` 格式正确
6. `q` 放弃退出，文件不变
7. 读取旧格式（`agent` 字符串）不出错
8. `paper-invoke` 能正确处理 `agents[]` 新格式，每个 Agent 各生成一个 dispatch call
