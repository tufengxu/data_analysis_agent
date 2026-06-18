# DataAnalysisAgent

A data analysis agent built on the **ReAct (Reasoning + Acting)** pattern, inspired by Claude Code's **LLM + Harness** architecture.

> **Design Philosophy**: The model decides **what** to do; the harness decides **how much**.

## Architecture

```
data_analysis_agent/
├── agent_loop.py          # Core ReAct while-loop engine (9-step pipeline per turn)
├── session.py             # AgentSession: multi-turn history carrier + resume
├── state_machine.py       # Immutable state container, ContinueReason, Terminal
├── events.py              # Async event stream (streaming text, tool calls, state changes)
├── config.py              # Configuration management (env / file / CLI)
├── persistence.py         # Append-only JSONL message store with session fork
├── artifacts.py           # ArtifactStore: persists charts for user delivery
├── __main__.py            # CLI entry point (rich UI, interactive mode, approval prompts)
├── kernel/                # Persistent analysis kernel (state survives across calls)
│   ├── manager.py         # Lifecycle + line-protocol JSON I/O, crash/timeout recovery
│   └── kernel_main.py     # Sandbox-side REPL (self-contained, composed + injected)
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
│   ├── builtin.py         # Descriptive, Correlation, Trend, Report skills
│   └── loader.py          # DeclarativeSkill: load/save skills as JSON records (L2 carrier)
├── telemetry/             # Self-evolution corpus: trajectory recording + feedback
│   ├── trajectory.py      # TurnRecord + TrajectoryLogger (EventConsumer side channel)
│   └── feedback.py        # Explicit (/good /bad) + implicit (rephrase) signals
├── memory/                # L1 domain memory (cross-session learning)
│   ├── model.py           # MemoryEntry (3 kinds) + DatasetProfile (struct/stats + fingerprint)
│   ├── store.py           # Keyword/substring retrieval; metric light-confirm
│   ├── profiler.py        # Deterministic dataset profiling + layered staleness
│   └── injector.py        # Inject recalls into prompt; capture profiles on reads
├── evolution/             # Offline pipeline (never in the live loop)
│   ├── synthesizer.py     # Trajectory clusters → candidate skills (overfit guards)
│   ├── evaluator.py       # Fixture rerun + A/B + sample gate → promote/rollback
│   └── __main__.py        # `python -m data_analysis_agent.evolution`
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
- **Multi-Turn Sessions**: `AgentSession` carries history across turns; interactive mode
  shares one session/kernel/event-loop, `--persist` resumes across processes
- **Persistent Kernel**: `python_analysis` state (variables, DataFrames) survives across
  calls; crash/timeout auto-restart with explicit state-loss notice, stateless fallback
- **Streaming First**: Real-time event stream for UI integration
- **Fail-Closed Security**: Tools default to serial, non-concurrent, destructive
- **Deny-First Permissions**: deny > ask > allow wired into tool execution; ASK escalates
  to an interactive approval prompt (fail-closed without one)
- **Artifact Delivery**: chart images survive sandbox teardown via ArtifactStore and are
  reported to the user as real file paths
- **HTML Reports**: `html_report` renders structured findings into a self-contained,
  mobile-friendly H5 page with ECharts charts (CDN by default; configure a local
  `echarts_src` file to embed for fully-offline reports)
- **Self-Evolution (domain-aware)**: every session is recorded as a trajectory
  (`telemetry/`); domain memory (`memory/`) learns dataset profiles, metric
  definitions, and analysis preferences across sessions — structure is remembered,
  numeric findings deliberately are not (ADR 0004); the offline pipeline
  (`evolution/`) distills recurring uncovered tasks into candidate skills and gates
  promotion by rerunning them on frozen fixtures, with a minimum-sample fallback to
  human review (ADR 0005)
- **Immutable State**: Cross-iteration state updates via `state.with_x()` pattern
- **Error Recovery**: Max-token escalation, prompt-too-long recovery chain, ledger closure
- **Context Compression**: 5-level pipeline (Budget → Snip → Microcompact → Collapse →
  Auto-Compact with LLM summary), pairing-safe and CJK-aware
- **Result Sampling**: Oversized tool results become L0–L3 sampled summaries (schema + exact/estimated stats + stratified-reservoir sample + outliers) instead of blind truncation — exact in-sandbox, stdlib fallback in-harness
- **Skill System**: Domain-specific workflows routed on the latest user message, with tool allowlists
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

| Tool              | Purpose                                                      |
| ----------------- | ------------------------------------------------------------ |
| `file_read`       | Read local files with offset/limit                           |
| `python_analysis` | Execute Python in the persistent kernel (stateless fallback) |
| `nl_query`        | Natural language to structured query                         |
| `visualization`   | Generate matplotlib / seaborn / plotly charts                |
| `retrieve_result` | Page through the original of a summarized tool result        |
| `html_report`     | Render a self-contained H5 HTML report with ECharts charts   |

## Built-in Skills

| Skill                  | Description                                            |
| ---------------------- | ------------------------------------------------------ |
| `descriptive_analysis` | Mean, median, std, percentiles, distributions          |
| `correlation_analysis` | Pearson / Spearman matrices, heatmaps                  |
| `trend_analysis`       | Time-series decomposition, seasonality, forecasting    |
| `report_generation`    | H5 HTML analysis report with ECharts charts and tables |

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

## Quality Gate (Definition of Done)

每次迭代须通过 `python scripts/quality_gate.py`(ruff / format / mypy / pytest / 架构漂移检测),
由阻断式 Stop hook 强制。详见 `docs/QUALITY_BAR.md`、`docs/DEVELOPMENT.md`、`docs/ARCHITECTURE.md`。

## Architecture Reference

See `docs/ARCHITECTURE.md` for the module map (machine-checked manifest), subsystem
invariants, and dependency rules. Design specs live under `docs/superpowers/specs/`.
