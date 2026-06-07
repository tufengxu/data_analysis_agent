# DataAnalysisAgent

A data analysis agent built on the **ReAct (Reasoning + Acting)** pattern, inspired by Claude Code's **LLM + Harness** architecture.

> **Design Philosophy**: The model decides **what** to do; the harness decides **how much**.

## Architecture

```
data_analysis_agent/
├── agent_loop.py          # Core ReAct while-loop engine (9-step pipeline per turn)
├── state_machine.py       # Immutable state container, ContinueReason, Terminal
├── events.py              # Async event stream (streaming text, tool calls, state changes)
├── config.py              # Configuration management (env / file / CLI)
├── persistence.py         # Append-only JSONL message store with session fork
├── __main__.py            # CLI entry point (rich UI, interactive mode)
├── protocol/              # Anthropic Messages API adapter
│   ├── client.py          # Streaming/non-streaming API client with retry & lazy imports
│   └── messages.py        # ContentBlock type hierarchy
├── tools/                 # Tool system (registry, dispatcher, executor)
│   ├── base.py            # Tool abstract base class (fail-closed defaults)
│   ├── registry.py        # 3-stage registration: enumerate → filter → assemble
│   ├── file_read.py       # Read files with offset/limit
│   ├── python_exec.py     # Restricted Python subprocess execution
│   ├── nl_query.py        # Natural language to data query
│   └── visualization.py   # Generate matplotlib / seaborn / plotly charts
├── skills/                # Domain-specific analysis workflows
│   ├── base.py            # Skill abstract base class
│   ├── registry.py        # Skill registration and keyword matching
│   └── builtin.py         # Descriptive, Correlation, Trend analysis skills
├── context/               # Context management and compression
│   └── compression.py     # 5-level compression pipeline
├── sampling/              # Sampling-based compaction for large tool results
│   ├── config.py          # SamplingConfig + fidelity presets (low/mid/high)
│   ├── model.py           # ColumnSummary / TableSummary (L0+L1+L2 carriers)
│   ├── render.py          # L3 Markdown renderer (shared, sampling-caveat)
│   ├── text_summary.py    # Harness fallback (pure stdlib, any string result)
│   └── sandbox_summary.py # Exact DataFrame summary, inlined into python_exec
└── security/              # Permission engine (deny-first, 4-layer defense)
    └── permissions.py
```

## Key Features

- **ReAct AgentLoop**: Single `while` loop with 9-step pipeline per turn
- **Streaming First**: Real-time event stream for UI integration
- **Fail-Closed Security**: Tools default to serial, non-concurrent, destructive
- **Deny-First Permissions**: deny > ask > allow rule evaluation wired into tool execution
- **Immutable State**: Cross-iteration state updates via `state.with_x()` pattern
- **Error Recovery**: Max-token escalation, prompt-too-long recovery chain, ledger closure
- **Context Compression**: 5-level pipeline (Budget → Snip → Microcompact → Collapse → Auto-Compact)
- **Result Sampling**: Oversized tool results become L0–L3 sampled summaries (schema + exact/estimated stats + stratified-reservoir sample + outliers) instead of blind truncation — exact in-sandbox, stdlib fallback in-harness
- **Skill System**: Domain-specific workflows with deterministic priority routing and tool allowlists
- **Persistence**: Append-only JSONL store, session resume, fork

## Quick Start

### Installation

```bash
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
pip install -e ".[data,dev]"
```

### Configuration

Set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or create a config file:

```json
{
  "model": "claude-sonnet-4-6-20260401",
  "max_tokens": 8192,
  "max_turns": 15
}
```

### Usage

**Single query:**

```bash
python -m data_analysis_agent "Analyze the sales data in sales.csv"
```

**Interactive mode:**

```bash
python -m data_analysis_agent -i
```

**With config file:**

```bash
python -m data_analysis_agent -c config.json "Your query here"
```

## Programmatic Usage

```python
import asyncio
from data_analysis_agent import AgentLoop, AgentLoopConfig, ToolRegistry
from data_analysis_agent.tools import FileReadTool, PythonAnalysisTool

async def main():
    config = AgentLoopConfig(
        api_key="sk-ant-...",
        system_prompt="You are a senior data analyst.",
    )
    registry = ToolRegistry()
    registry.register(FileReadTool())
    registry.register(PythonAnalysisTool())

    agent = AgentLoop(config, registry)
    async for event in agent.run("Analyze data.csv"):
        print(event)

asyncio.run(main())
```

## Built-in Tools

| Tool              | Purpose                                            |
| ----------------- | -------------------------------------------------- |
| `file_read`       | Read local files with offset/limit                 |
| `python_analysis` | Execute restricted Python code for data processing |
| `nl_query`        | Natural language to structured query               |
| `visualization`   | Generate matplotlib / seaborn / plotly charts      |

## Built-in Skills

| Skill                  | Description                                         |
| ---------------------- | --------------------------------------------------- |
| `descriptive_analysis` | Mean, median, std, percentiles, distributions       |
| `correlation_analysis` | Pearson / Spearman matrices, heatmaps               |
| `trend_analysis`       | Time-series decomposition, seasonality, forecasting |

## Development

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check src tests
ruff format src tests

# Type check
mypy src
```

## Architecture Reference

See `docs/ARCHITECTURE.md` for the module map (machine-checked manifest), subsystem
invariants, and dependency rules. Design specs live under `docs/superpowers/specs/`.
