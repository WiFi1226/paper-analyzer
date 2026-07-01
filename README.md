# paper-analyzer

> ⚠️ **内测警告**
> 本项目目前处于 **Alpha 阶段**（v0.1.0），API 和行为可能在不通知的情况下发生变更，请谨慎用于生产环境。

论文分析工具包 —— 将学术论文（PDF/TXT）自动切分为章节，基于关键词将章节路由到对应的分析 Agent，构造 Prompt，最终组装为结构化分析报告。

## 功能

| 步骤 | 命令 | 说明 |
|---|---|---|
| PDF 提取 | `paper-extract` | 调用 `pdftotext` 从 PDF 提取纯文本（支持指定页码范围） |
| 章节切分 | `paper-split` | 按一级标题自动切分论文为章节（中英文双支持，自动检测） |
| Agent 路由 | `paper-route` | 基于关键词全量匹配，将章节分配到对应分析 Agent |
| 交互式路由调整 | `paper-routing` | TUI 工具，在终端中交互调整章节 → Agent 的分配 |
| Prompt 构造 | `paper-invoke` | 为每个 Agent 构造完整分析 Prompt（超长时递归二分自动拆分） |
| Agent 输出保存 | `paper-save-output` | 将 Agent 输出写入 `_contents/` 目录（含分片支持） |
| 报告组装 | `paper-assemble` | 汇总所有 Agent 输出，组装为最终 Markdown 分析报告 |
| 全流程编排 | `paper-orchestrate` | 一键执行：PDF 转换 → 切分 → 路由 → 统一输出 |

## 安装

### 系统依赖

PDF 提取依赖系统命令 `pdftotext`（来自 [poppler-utils](https://poppler.freedesktop.org/)）：

```bash
# macOS
brew install poppler

# Ubuntu / Debian
sudo apt-get install poppler-utils

# CentOS / RHEL
sudo yum install poppler-utils
```

### 安装包

```bash
pip install paper-analyzer-core

# 如需 TUI 交互式路由工具
pip install "paper-analyzer-core[tui]"

# 开发模式
pip install "paper-analyzer-core[dev]"
pip install -e ".[tui,dev]"   # 本地源码安装
```

## 快速开始

```bash
# 一键全流程（推荐）
paper-orchestrate paper.pdf

# 或分步执行：
paper-extract paper.pdf -o paper.txt
paper-split paper.txt -o sections.json
paper-route sections.json -o routing.json
paper-invoke sections.json routing.json -o prompts.json
paper-assemble output/my_paper/cache/_contents/ -o report.md
```

## CLI 命令详解

### `paper-orchestrate`

一键编排：PDF 转换 → 切分 → 路由，所有中间产物自动保存到 `output/<论文名>/cache/`。

```bash
paper-orchestrate paper.pdf
paper-orchestrate paper.txt
paper-orchestrate paper.pdf --section 引言 --agent causal-inference-analyzer
```

### `paper-extract`

```bash
paper-extract paper.pdf -o text.txt
paper-extract paper.pdf --pages 1-5
```

### `paper-split`

```bash
paper-split text.txt -o sections.json
paper-split text.txt --section 引言
```

### `paper-route`

```bash
paper-route sections.json -o routing.json
paper-route sections.json --section 引言 --agent causal-inference-analyzer
```

### `paper-routing`（TUI）

交互式路由调整工具，在终端中可视化调整章节 → Agent 的分配关系。

```bash
paper-routing output/my_paper/cache/_prompts/_routing.json
```

### `paper-invoke`

```bash
paper-invoke sections.json routing.json -o prompts.json
```

### `paper-save-output`

```bash
paper-save-output causal-inference-analyzer my_paper output.md
paper-save-output causal-inference-analyzer my_paper agent_output.md --part 1 --split-total 2
```

### `paper-assemble`

```bash
paper-assemble output/my_paper/cache/_contents/ -o report.md
```

## 配置文件

项目使用 YAML 配置文件，采用 **包内默认 + 用户覆盖** 机制：

| 文件 | 说明 |
|---|---|
| `defaults/settings.yaml` | 全局设置：`max_prompt_chars`、`pdf_search_paths` 等 |
| `defaults/split.yaml` | 章节切分规则：中英文标题正则、最少前置内容字符数等 |
| `defaults/agents.yaml` | Agent 注册表：名称、区段标签、排序、分类 |
| `defaults/routing.yaml` | 路由规则：别名 + 关键词 → Agent 映射表 |

用户只需提供自己修改过的文件，通过以下方式覆盖：

```bash
# 方式一：指定配置目录（覆盖同名文件）
paper-orchestrate paper.pdf --config-dir /path/to/my-config/

# 方式二：指定单个配置文件（可多次使用）
paper-orchestrate paper.pdf --config-file /path/to/my-settings.yaml

# 方式三：环境变量
export PAPER_ANALYZER_CONFIG_DIR=/path/to/my-config/
export PAPER_ANALYZER_ROOT=/path/to/project
export PAPER_ANALYZER_OUTPUT_DIR=/path/to/output
```

用户配置验证失败时会自动回退到包内默认并打印警告。

## 项目结构

```
src/paper_analyzer/
├── __init__.py
├── _config.py           # Config 对象 —— 统一路径管理
├── cache.py             # 缓存判断：SHA 指纹 + mtime 新鲜度
├── errors.py            # 自定义异常层次
├── io.py                # 标准化文件读写
├── adapters/
│   ├── config_loader.py # YAML 配置加载（默认 + 用户覆盖合并）
│   ├── output_saver.py  # Agent 输出保存
│   └── pdf.py           # PDF 提取（封装 pdftotext）
├── cli/
│   ├── extract.py           # paper-extract
│   ├── split.py             # paper-split
│   ├── route.py             # paper-route
│   ├── interactive_routing.py # paper-routing（TUI）
│   ├── invoke.py            # paper-invoke
│   ├── save_output.py       # paper-save-output
│   ├── assemble.py          # paper-assemble
│   └── orchestrate.py       # paper-orchestrate
├── core/
│   ├── split.py         # 章节切分引擎（纯函数，无 I/O）
│   ├── route.py         # 路由匹配引擎（关键词全量匹配）
│   └── prompts.py       # Prompt 构造引擎
└── defaults/
    ├── settings.yaml
    ├── split.yaml
    ├── agents.yaml
    └── routing.yaml
```

## API 调用

```python
from paper_analyzer._config import Config
from paper_analyzer.adapters.config_loader import get_split_patterns, get_routing_rules
from paper_analyzer.core.split import detect_style, find_cn_headings, find_en_headings, split_text
from paper_analyzer.core.route import filter_sections, match_chapters
from paper_analyzer.io import read_text, write_json, normalize_paper_name

# 初始化配置
config = Config(project_root="/path/to/project")

# 加载章节分割模式
patterns = get_split_patterns(config)

# 切分论文
text = read_text(config.txt_path("paper_name"))
style = detect_style(text, patterns["cn_heading"], patterns["en_heading_line"], patterns["en_heading_mid"])
if style == "cn":
    headings = find_cn_headings(text, patterns["cn_heading"])
else:
    headings = find_en_headings(text, patterns["en_heading_line"], patterns["en_heading_mid"], patterns["heading_number_regex"])
sections = split_text(text, headings, patterns["min_pre_content_chars"])
write_json(config.sections_path("paper_name"), sections)
```

## 输出目录结构

```
output/<paper_name>/
├── <paper_name>.txt                        # 提取的纯文本
├── cache/
│   ├── <paper_name>.txt                    # 缓存文本
│   ├── _prompts/
│   │   ├── _sections.json                  # 章节 JSON
│   │   ├── _routing.json                   # 路由结果
│   │   └── <agent>_prompts.json            # 构造的 Prompt
│   ├── _contents/
│   │   ├── <agent1>.md                     # Agent 分析输出
│   │   ├── <agent2>.md
│   │   └── _hook.log                       # hook 诊断日志
└── <paper_name>_analysis.md                # 最终报告
```

## License

MIT
