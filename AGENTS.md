# AGENTS.md — DataAnalysisAgent

## 项目定位

- **项目名称**: DataAnalysisAgent
- **核心目标**: 基于 ReAct(Reasoning + Acting)模式、借鉴 Claude Code「LLM + Harness」架构的数据分析 Agent。模型决定「做什么」,harness 决定「做多少」。
- **当前阶段**: 开发中(v0.1.0)

## 技术栈

- **语言**: Python ≥ 3.10
- **核心依赖**: Anthropic Messages API;可选 `data` 组(pandas / numpy / matplotlib / seaborn / plotly)。
- **工具链**: pytest(+asyncio / cov)、ruff、mypy、uv。
- **运行环境**: macOS + zsh;本目录**是 git repo**(2026-06 初始化)。

## 目录约定

```
src/data_analysis_agent/   核心源码(agent_loop / session / kernel / state_machine /
                           events / protocol / tools / artifacts)
tests/                     pytest(session / kernel / artifacts / compression / tools …)
openspec/                  OpenSpec 规格与变更(specs / changes)
docs/  examples/           文档与示例
.claude/                   项目级 commands 与 skills
```

## 已确认工作流与命令

```bash
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
pip install -e ".[data,dev,web]"   # 或 uv sync --all-extras --locked(web extra 供质量门 mypy 检查 web/)
pytest tests/ -v
ruff check src tests
mypy src
python -m data_analysis_agent      # CLI 入口;亦可用 console script `data-agent`
```

## 已知约束 / 关键决策

- 工具系统默认 **fail-closed**(见 `tools/base.py`);`python_exec` 默认走**持久内核**
  (`kernel/`,变量/DataFrame 跨调用存活),启动失败自动降级为受限一次性子进程,
  崩溃/超时则重启并向模型显式报告状态丢失。
- **多轮会话**由 `session.py` 的 `AgentSession` 承载(`AgentLoop.run()` 只跑单轮);
  `--persist` 下支持跨进程 resume,恢复时自动做账本闭合(防孤儿 tool_use 触发 API 400)。
- 消息持久化为 append-only JSONL,支持 session fork(`persistence.py`)。
  注意 `MessageStore.__len__` 使空 store 为 falsy,判空必须用 `is not None`。
- **产物交付**:工具 metadata 中的图像经 `ArtifactStore`(`artifacts.py`)落盘,
  以真实路径交付用户;visualization 工具默认生成绝对路径的保存代码;
  工具自写文件经 `metadata["artifact_paths"]` 上报。
- **HTML 报告**(`tools/html_report.py`):结构化输入 → 自包含 H5 页面(ECharts);
  输出强制限定产物目录(fail-closed);文本全转义,chart option 做 `</` 逃逸防护;
  `echarts_src` 配 http(s) 走 CDN、配本地文件路径则内嵌(离线可用);PLAN 模式 deny。
- **自进化(领域化,阶段二)**:① `telemetry/` 把每轮 send 记成 TurnRecord 轨迹
  (EventConsumer 旁路,落 `~/.daa/trajectories/`);② `memory/` L1 领域记忆——数据集画像
  (列指纹分层失效)、口径定义(轻确认)、分析偏好,**记结构不记数值**(ADR 0004),经
  `memory_injector`/`memory_recorder` 回调接 agent_loop;③ `skills/loader.py` 把技能
  数据化为 JSON 记录(可装载,status 流转);④ `evolution/` 离线管线(独立 CLI
  `python -m data_analysis_agent.evolution`):synthesizer 从轨迹聚类合成 candidate 技能,
  evaluator 在冻结 fixture 上重跑 A/B + 最小样本门槛 promote/rollback(ADR 0005)。
  数据存 `~/.daa/`(可用 `DAA_HOME` 覆盖);进化与服务分离,绝不在交互主循环内运行。
- **接线模式**:所有自进化子系统经回调/旁路接入,agent_loop **不反向依赖** telemetry/memory/evolution
  (drift 规则强制)。技能文件格式选 **JSON 非 YAML**(项目零 YAML、避免新依赖)。
- **超大结果采样摘要**(`sampling/`):`python_exec` 沙箱对真实 DataFrame 出精确摘要;
  `agent_loop` 接缝对任意超大字符串用纯 stdlib 兜底(替换盲截断)。设计见
  `docs/superpowers/specs/2026-06-06-data-sampling-compaction-design.md`。沙箱子进程
  `PYTHONPATH=""` 故 `sandbox_summary.py` 以"读源码内联"注入,且 pandas 可选(缺失即退回原样)。
  内核侧 `kernel/kernel_main.py` 同约束(自包含、组合注入)。
- **跑测试前需可编辑安装**:`uv pip install -e ".[data,dev,web]"`(沙箱会拦 uv 缓存,需放行;web 供质量门 mypy 检查 web/);
  `sampling` 高保真测试依赖 pandas,缺失则 `importorskip` 跳过。
- **质量准出硬标尺**:每次迭代须过 `scripts/quality_gate.py`(ruff/format/mypy/pytest/drift),
  由阻断式 Stop hook 强制。规范见 `docs/QUALITY_BAR.md` 与 `docs/DEVELOPMENT.md`;架构与 manifest
  见 `docs/ARCHITECTURE.md`。新增/删模块必须同步 manifest。
- 完整架构说明见 `README.md`。
