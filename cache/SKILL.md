---
name: paper-analyzer
description: >
  论文结构化分析流水线。用法：
  /paper-analyzer <PDF文件路径> [--section <关键词> ...] [--agent <agent名> ...] [--project-root <项目根目录>]
  自动切分章节 → 路由匹配 agent → 构造 prompt → 调用 agent → 汇总报告。
---

# Paper Analyzer（论文结构化分析技能）

## Step 0：编排

根据用户提供的路径直接执行，**不要验证文件是否存在、不要搜索**。如果用户未提供文件路径，直接询问。

```bash
paper-orchestrate "<文件路径>" [--section "<关键词>"] [--agent "<agent名>"] [--project-root <当前项目根目录>]
```

将 `/paper-analyzer` 后的 `--section` 和 `--agent` 原样传入，进入 Step 1。


## Step 1：交互式匹配审核

**先不要调用任何 agent。**

1. **展示匹配报告** 从 Step 0 终端输出中提取 `routing_json_path`（格式：`[匹配] 完成: N 条匹配 → <绝对路径>`），读取该文件，展示一张表格（列：章节 | 匹配结果 | 字数），未匹配的章节在匹配结果列显示「未命中」。如果终端输出中未找到，则回退读取 `output/<论文名>/cache/_prompts/_routing.json`。

2. **（按需）手动调整匹配** 如果用户对自动匹配结果不满意，让用户在终端手动执行以下命令启动 TUI 调整：

```bash
    paper-routing "<routing_json_path>"
```

- 用户在 TUI 中按 `Enter` 确认写入，或按 `q` 放弃修改。调整完成后回到聊天告知结果。

3. **展示最终分配表** 读取最新的 `_routing.json`，在对话中渲染一张 Markdown 表格（列：章节 | 匹配结果 | 字数）

## Step 2：构造 prompt

根据 Step 0 终端输出中的 sections_path 和 Step 1 输出中的 routing_path 执行：

```bash
paper-invoke "<sections_path>" "<routing_path>" --rules-dir .claude/rules [--project-root <当前项目根目录>]
```

## Step 3: 调用 agent

`paper-invoke`（Step 2）已同时生成 `_prompts.json` 和 `_dispatch.json`。

读取 `output/<论文名>/cache/_prompts/_dispatch.json`，按 `calls` 数组的 `sequence` 顺序，逐条调用 Agent 工具。**description 字符串已预置为正确格式，直接使用，不要手动修改。**

| 参数 | 取值 |
|------|------|
| `subagent_type` | `call.agent` |
| `description` | `call.description`（hook 依此捕获输出） |
| `prompt` | `_prompts.json` 的 `prompts[call.prompt_index].prompt_text` |

每次调用完成一个后再启动下一个（串行）。

## Step 4: 汇总报告

所有 agent 调用完成后，执行：

```bash
paper-assemble output/<论文名>/cache/_contents/
```

展示终端输出日志。

## 全局约束

- **严格按 Step 顺序执行**：不要自行增加步骤、读取额外文件、或发起未被要求的询问。
- **文件读取白名单**：只允许读取当前 Step 指令中直接出现的文件路径（`output/<论文名>/...` 视为路径模板，允许按实际论文名解析）。Step 指令未写明的文件一律禁止读取。
- **终端输出即事实**：Step 的输入只能是上一个 Step 的终端输出，或当前 Step 白名单中的文件。禁止因为「终端输出不够详细」而自行补充读取任何文件。
- **禁止反复读取文件**：除非报错需要排查，禁止为了确认内容而重复读写同一文件。








