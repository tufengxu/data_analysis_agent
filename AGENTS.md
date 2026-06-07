# AGENTS.md — DataAnalysisAgent

## 项目定位

- **项目名称**: DataAnalysisAgent
- **核心目标**: 基于 ReAct(Reasoning + Acting)模式、借鉴 Claude Code「LLM + Harness」架构的数据分析 Agent。模型决定「做什么」,harness 决定「做多少」。
- **当前阶段**: 开发中(v0.1.0)

## 技术栈

- **语言**: Python ≥ 3.10
- **核心依赖**: Anthropic Messages API;可选 `data` 组(pandas / numpy / matplotlib / seaborn / plotly)。
- **工具链**: pytest(+asyncio / cov)、ruff、mypy、uv。
- **运行环境**: macOS + zsh;本目录**不是 git repo**。

## 目录约定

```
src/data_analysis_agent/   核心源码(agent_loop / state_machine / events / protocol / tools)
tests/                     pytest(integration / permissions / state_machine / tools)
openspec/                  OpenSpec 规格与变更(specs / changes)
docs/  examples/           文档与示例
.claude/                   项目级 commands 与 skills
```

## 已确认工作流与命令

```bash
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
pip install -e ".[data,dev]"      # 或 uv pip install -e ".[data,dev]"
pytest tests/ -v
ruff check src tests
mypy src
python -m data_analysis_agent      # CLI 入口;亦可用 console script `data-agent`
```

## 已知约束 / 关键决策

- 工具系统默认 **fail-closed**(见 `tools/base.py`);`python_exec` 走受限子进程执行。
- 消息持久化为 append-only JSONL,支持 session fork(`persistence.py`)。
- **超大结果采样摘要**(`sampling/`):`python_exec` 沙箱对真实 DataFrame 出精确摘要;
  `agent_loop` 接缝对任意超大字符串用纯 stdlib 兜底(替换盲截断)。设计见
  `docs/superpowers/specs/2026-06-06-data-sampling-compaction-design.md`。沙箱子进程
  `PYTHONPATH=""` 故 `sandbox_summary.py` 以"读源码内联"注入,且 pandas 可选(缺失即退回原样)。
- **跑测试前需可编辑安装**:`uv pip install -e ".[data,dev]"`(沙箱会拦 uv 缓存,需放行);
  `sampling` 高保真测试依赖 pandas,缺失则 `importorskip` 跳过。
- **质量准出硬标尺**:每次迭代须过 `scripts/quality_gate.py`(ruff/format/mypy/pytest/drift),
  由阻断式 Stop hook 强制。规范见 `docs/QUALITY_BAR.md` 与 `docs/DEVELOPMENT.md`;架构与 manifest
  见 `docs/ARCHITECTURE.md`。新增/删模块必须同步 manifest。
- 完整架构说明见 `README.md`。
