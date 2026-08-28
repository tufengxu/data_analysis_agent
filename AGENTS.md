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
src/data_analysis_agent/   核心源码。v1 harness(agent_loop / session / state_machine /
                           events / protocol / runtime)+ 能力侧(tools / kernel / causal /
                           reporting / telemetry / memory / skills / evolution / artifacts);
                           capabilities/ 为 v2 能力核心层(contracts + 五域 + serving,
                           sampling 实现已物理迁入,v1 sampling/ 为 re-export shim)
harnesses/                 v2 双基座适配层(shared MCP 客户端 / pi / deepseek,仅胶水)
tests/                     pytest(session / kernel / artifacts / compression / tools /
                           capability_* …)
openspec/                  OpenSpec 规格与变更(specs / changes)
docs/  examples/           文档(ARCHITECTURE manifest / ADR / V2_RUNBOOK / QUALITY_BAR /
                           DEVELOPMENT …)与示例(eval_tasks / v2 端到端演示)
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

### v2(能力核心层 + 双基座,2026-08)

```bash
uv pip install -e ".[data,dev,web,serving]"      # serving extra 提供 mcp SDK
.venv/bin/data-agent-capabilities list            # v2 能力层 CLI(19 能力;子命令 mcp/call/compact/retrieve)
.venv/bin/python examples/v2/demo_e2e.py         # 无 LLM 端到端演示(读表→图表→HTML→因果→压缩/召回→轨迹)
(cd harnesses/pi && npm run smoke)               # Pi 适配器无 key 冒烟(deepseek 同理)
bash harnesses/check-ts.sh                       # TS 适配器类型检查(质量门 ts 步同款)
```

- 布局:`src/data_analysis_agent/capabilities/`(契约 + tabular/reporting/causal/evolution/sampling
  + serving)、`harnesses/{shared,pi,deepseek}/`。分层与依赖方向见 `docs/ARCHITECTURE.md` v2 章节。
- v1 `sampling/` 是纯 re-export shim(实现物理迁移在 `capabilities/sampling/`);改采样逻辑
  去能力层改,v1 公共 API 不变。
- `capabilities/*` 禁 import v1 harness(agent_loop/session/state_machine/protocol/events/runtime/
  config/...)与 `data_analysis_agent.sampling` shim(drift 强制);适配器只准经
  `data-agent-capabilities` 入口调 Python(`checks.check_harness_adapters` 强制)。
- 基座版本 pin:Pi `@earendil-works/pi-coding-agent@0.84.3`、dsh `@deepseek-ai/dsh@0.1.1-rc.2`
  (核实日期 2026-08-26,记录于 openspec/changes/v2-capability-core/design.md)。

## CodeGraph 辅助检索

本项目已在 `.codegraph/` 建立本机索引。Claude Code、Codex、Pi、Kimi 等具备
Shell 执行能力的 Agent 使用相同的 CLI 步骤；从 IDE 或受限环境启动时，为避免
`PATH` 差异，统一使用受控入口 `/Users/fengxutu/.local/bin/codegraph`。

```bash
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
/Users/fengxutu/.local/bin/codegraph status .
# 仅在索引陈旧、任务确需最新图谱且允许刷新派生索引时执行
/Users/fengxutu/.local/bin/codegraph sync .
/Users/fengxutu/.local/bin/codegraph explore "<架构、调用链或影响范围问题>"
/Users/fengxutu/.local/bin/codegraph node "<符号名>"
/Users/fengxutu/.local/bin/codegraph callers "<符号名>"
/Users/fengxutu/.local/bin/codegraph callees "<符号名>"
/Users/fengxutu/.local/bin/codegraph impact "<符号名>"
```

- 在架构梳理、跨文件调用链、符号定位和改动影响分析中，可先用 CodeGraph 快速缩小范围；
  简单的已知路径读取或精确文本搜索可直接使用原生文件读取与 `rg`。
- 开始结构性调查前先执行 `codegraph status .`。若索引陈旧，或其他 Agent/工作树刚修改过代码，
  且当前任务允许刷新本机派生索引，再执行 `codegraph sync .`。Git 的 commit、merge/pull、
  checkout 后另有项目本地 Hook 自动同步，但它不覆盖尚未触发这些 Git 事件的工作区修改。
- CodeGraph 是派生索引，结果可能因索引时点、静态解析、动态调用、反射或启发式解析而不完整；
  所有输出只作为辅助证据，不视为当前源码的权威替代。
- 涉及修改、缺陷根因、安全、权限、持久化、并发、数据损坏风险或其他高影响判断时，必须回到
  当前源码、配置、测试、类型/静态检查和必要的运行结果进行验证；CodeGraph 与源码冲突时以
  当前可复现证据为准。
- 不因 CodeGraph 返回了源码片段就禁止或省略必要的 `Read`、`rg`、测试和运行验证；也不为简单
  问题机械增加图谱调用。
- 未经用户明确要求，不运行 `codegraph init`、`index`、`uninit`、`install` 或真实升级；版本检查
  仅使用 `codegraph upgrade --check`。

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
  `agent_loop` 接缝对任意超大字符串用纯 stdlib 兜底(替换盲截断)。沙箱子进程
  `PYTHONPATH=""` 故 `sandbox_summary.py` 以"读源码内联"注入,且 pandas 可选(缺失即退回原样)。
  内核侧 `kernel/kernel_main.py` 同约束(自包含、组合注入)。
- **上下文压缩契约**(2026-08 context-compression-upgrade,ADR 0012):触发阈值随上下文压力
  自适应下探(floor 2000),`[result_id=` 回取页豁免压缩;触发判定**只在能力层**(D0 单一
  事实源,Pi/dsh 适配器只做下界预筛 `DAA_COMPACT_FLOOR` 并服从 `was_compacted` 裁决);
  压力 ≥0.75 自动降档 low(`adaptive_fidelity=False` 锁档);kernel 按 frame 快照摘要新增/
  变化 DataFrame 并带 variable 溯源;reactive compaction 用六节 handoff 模板 + 数据态重注入;
  `retrieve_result` 支持单谓词/投影/采样下取(capabilities/sampling/slicing.py);JSON/JSONL 走结构骨架摘要;
  `evolution compare-sampling` 出三臂(control/default/low)压缩-保真对比。
- **跑测试前需可编辑安装**:`uv pip install -e ".[data,dev,web]"`(沙箱会拦 uv 缓存,需放行;web 供质量门 mypy 检查 web/);
  `sampling` 高保真测试依赖 pandas,缺失则 `importorskip` 跳过。
- **质量准出硬标尺**:每次迭代须过 `scripts/quality_gate.py`(ruff/format/mypy/pytest/drift),
  由阻断式 Stop hook 强制。规范见 `docs/QUALITY_BAR.md` 与 `docs/DEVELOPMENT.md`;架构与 manifest
  见 `docs/ARCHITECTURE.md`。新增/删模块必须同步 manifest。
- 完整架构说明见 `README.md`。
