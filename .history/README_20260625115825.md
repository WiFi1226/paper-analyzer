# paper-analyzer

论文分析工具包 —— 独立可用的 Python 工具集，支持 pip 安装。

## 功能

- **PDF 提取**：从 PDF 提取纯文本（基于 pdftotext）
- **章节切分**：按一级标题自动将论文切分为若干章节（中英文双支持）
- **Agent 路由**：基于关键词将章节匹配到对应的分析 Agent
- **Prompt 构造**：为每个 Agent 构造分析 Prompt（超长自动拆分）
- **Agent 输出保存**：自动保存 Agent 分析结果
- **报告组装**：将所有 Agent 输出组装为 Markdown 报告

## 安装

```bash
pip install paper-analyzer-core
# 可选：纯 Python PDF 降级方案
pip install paper-analyzer-core[pdf]
```

## CLI 命令

```bash
# PDF → TXT
paper-extract paper.pdf -o text.txt

# TXT → 章节 JSON
paper-split text.txt -o sections.json

# 章节 → Agent 路由
paper-route sections.json -o routing.json

# 路由 → Prompt
paper-invoke sections.json routing.json -o prompts.json

# Agent 输出 → 文件
paper-save-output <agent> <paper> <output.md> -o _outputs/

# Agent 输出 → 报告
paper-assemble orchestrator_result.json -o report.md
```

## API 调用

```python
from paper_analyzer.core.split import detect_style, split_text
from paper_analyzer.core.route import fuzzy_match, match_chapters
from paper_analyzer._config import Config

config = Config(project_root="/path/to/project")
```

## 配置

通过环境变量或 `--config-dir` 自定义：

```bash
export PAPER_ANALYZER_CONFIG_DIR=/path/to/custom
export PAPER_ANALYZER_ROOT=/path/to/project
export PAPER_ANALYZER_OUTPUT_DIR=/path/to/output
```

## License

MIT
