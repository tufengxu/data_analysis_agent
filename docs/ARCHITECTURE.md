# Architecture

DataAnalysisAgent 是 ReAct(Reasoning+Acting)模式的数据分析 agent:模型决定「做什么」,
harness 决定「做多少」。本文件是架构的单一事实源;下方 manifest 段被 `scripts/checks.py`
机器校验,**新增/删除模块必须同步更新这里,否则质量闸 fail**。

## 子系统不变量

- **tools**:fail-closed(`is_destructive` 默认 True);`python_exec` 走受限子进程,
  `PYTHONPATH=""`。工具不得反向依赖 `agent_loop`。
- **sampling**:叶子工具,只在包内自依赖;`sandbox_summary.py` 不得 import 本包(被内联进沙箱)。
- **protocol**:底层 LLM 适配,不得依赖 `agent_loop`/`tools`/`skills`。
- **state**:不可变,经 `with_*()` 更新。

## 依赖规则(与 `scripts/drift_rules.py` 强制项同源)

- `sampling/*` ✗→ `tools`/`agent_loop`/`protocol`/`skills`/`security`/`context`
- `sampling/sandbox_summary.py` ✗→ 任何 `data_analysis_agent.*`
- `tools/*` ✗→ `agent_loop`
- `protocol/*` ✗→ `agent_loop`/`tools`/`skills`

## 模块 manifest

<!-- manifest:start -->

```
src/data_analysis_agent/__main__.py = "CLI 入口:rich UI、交互模式、registry/agent 装配"
src/data_analysis_agent/agent_loop.py = "ReAct while-loop 引擎 + 9 步流水线 + 错误恢复"
src/data_analysis_agent/state_machine.py = "不可变状态容器、ContinueReason、TerminalReason"
src/data_analysis_agent/events.py = "异步事件流类型(流式文本/工具/状态变更)"
src/data_analysis_agent/config.py = "AgentConfig 加载合并 + sampling_config() 构造"
src/data_analysis_agent/persistence.py = "append-only JSONL 消息存储 + session fork"
src/data_analysis_agent/context/compression.py = "5 级消息压缩流水线"
src/data_analysis_agent/protocol/client.py = "Anthropic 流式/非流式客户端 + 重试 + 懒导入"
src/data_analysis_agent/protocol/messages.py = "ContentBlock 类型层级"
src/data_analysis_agent/tools/base.py = "Tool 抽象基类 + ToolResult/Validation/Permission"
src/data_analysis_agent/tools/registry.py = "工具注册/过滤/装配(3 阶段)"
src/data_analysis_agent/tools/file_read.py = "按 offset/limit 读文件"
src/data_analysis_agent/tools/python_exec.py = "受限子进程执行 + 采样摘要注入"
src/data_analysis_agent/tools/nl_query.py = "自然语言 → pandas/SQL 代码生成"
src/data_analysis_agent/tools/visualization.py = "matplotlib/seaborn/plotly 图表生成"
src/data_analysis_agent/skills/base.py = "Skill 抽象基类"
src/data_analysis_agent/skills/registry.py = "技能注册 + 关键词匹配 + 优先级路由"
src/data_analysis_agent/skills/builtin.py = "描述性/相关性/趋势 三个内置分析技能"
src/data_analysis_agent/security/permissions.py = "deny-first 权限引擎(4 层防御)"
src/data_analysis_agent/sampling/config.py = "SamplingConfig + fidelity 档位预设"
src/data_analysis_agent/sampling/model.py = "ColumnSummary / TableSummary 数据类"
src/data_analysis_agent/sampling/render.py = "L3 Markdown 渲染器(共享,带采样警告)"
src/data_analysis_agent/sampling/text_summary.py = "harness 纯 stdlib 兜底摘要器"
src/data_analysis_agent/sampling/sandbox_summary.py = "精确 DataFrame 摘要,内联进 python_exec 沙箱"
```

<!-- manifest:end -->
