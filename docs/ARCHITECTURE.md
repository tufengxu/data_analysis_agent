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
- **session**:`AgentLoop.run()` 只执行单轮;跨轮历史/恢复由 `AgentSession` 持有。
  resume 时必须先 `ensure_tool_ledger_closed`(防孤儿 tool_use 触发 API 400)。
- **kernel**:持久内核为主路径,无状态沙箱为永久降级路径(启动失败 → 永久回落;
  崩溃/超时 → 重启并向模型显式报告状态丢失)。`kernel_main.py` 自包含,
  不得 import 本包(与 `sandbox_summary.py` 同约束,组合后注入沙箱)。
- **artifacts**:叶子模块(纯 stdlib);工具 metadata 中的图像必须经 `ArtifactStore`
  落盘后以真实路径交付用户,不得静默丢弃。
- **context/compression**:任何压缩/折叠/截断不得切断 tool_use/tool_result 配对。

## 依赖规则(与 `scripts/drift_rules.py` 强制项同源)

- `sampling/*` ✗→ `tools`/`agent_loop`/`protocol`/`skills`/`security`/`context`
- `sampling/sandbox_summary.py` ✗→ 任何 `data_analysis_agent.*`
- `tools/*` ✗→ `agent_loop`
- `protocol/*` ✗→ `agent_loop`/`tools`/`skills`
- `kernel/*` ✗→ `tools`/`agent_loop`/`protocol`/`skills`/`security`/`context`
- `kernel/kernel_main.py` ✗→ 任何 `data_analysis_agent.*`
- `artifacts.py` ✗→ 任何 `data_analysis_agent.*`
- `jsonl_store.py` ✗→ 任何 `data_analysis_agent.*`(纯 stdlib 叶子,被各 store 组合)
- `telemetry/*` ✗→ `agent_loop`/`tools`/`skills`/`protocol`/`security`(经 EventConsumer 反向解耦)
- `memory/*` ✗→ `agent_loop`/`tools`/`skills`/`protocol`/`security`(经回调注入反向解耦;可依赖 `context`)
- `security/tool_gate.py` ✗→ `agent_loop`/`runtime`/`session`(被 agent_loop 依赖的授权接缝,不得反向耦合)
- `recovery.py` ✗→ `agent_loop`/`runtime`/`session`(被 agent_loop 依赖的恢复策略接缝,不得反向耦合)
- 任何核心模块 ✗→ `evolution`(evolution 是顶层离线 sink,依赖向下,不被 core 依赖)
- `evolution/synthesizer.py` ✗→ `protocol`/`agent_loop`(反思经 reflect_fn 注入;仅离线 CLI 入口可依赖 `protocol`)
- `reporting/*` ✗→ 任何 `data_analysis_agent.*`(纯 stdlib 领域层,被 tools 单向依赖;见 ADR 0009)

## 模块 manifest

<!-- manifest:start -->

```
src/data_analysis_agent/__main__.py = "CLI 入口:rich UI、交互模式(单事件循环)、审批交互(装配委托 runtime)"
src/data_analysis_agent/runtime.py = "Composition root:AgentRuntime.from_config 统一装配,CLI 与 eval 同源(顶层 sink)"
src/data_analysis_agent/agent_loop.py = "ReAct while-loop 引擎 + 9 步流水线 + 错误恢复 + 账本闭合"
src/data_analysis_agent/recovery.py = "RecoveryPolicy:模型错误/截断的恢复阶梯决策(collapse-drain → reactive-compact → token-escalate),agent_loop 的可测接缝"
src/data_analysis_agent/session.py = "AgentSession:跨轮历史容器、store 恢复、send() 入口;旁路接入 trajectory_logger / memory_adjudicator(rephrase 门控轻确认)"
src/data_analysis_agent/artifacts.py = "ArtifactStore:base64 图像落盘,产物交付(叶子模块)"
src/data_analysis_agent/kernel/manager.py = "KernelManager:持久内核生命周期 + 行协议 JSON I/O"
src/data_analysis_agent/kernel/kernel_main.py = "内核沙箱侧 REPL(自包含,组合注入,不得 import 本包)"
src/data_analysis_agent/state_machine.py = "不可变状态容器、ContinueReason、TerminalReason"
src/data_analysis_agent/events.py = "异步事件流类型(流式文本/工具/状态变更)"
src/data_analysis_agent/config.py = "AgentConfig 加载合并 + sampling_config() 构造"
src/data_analysis_agent/persistence.py = "append-only JSONL 消息存储 + session fork(组合 JsonlStore)"
src/data_analysis_agent/jsonl_store.py = "JsonlStore primitive:原子重写 + 读容错 + 只读降级(纯 stdlib 叶子)"
src/data_analysis_agent/context/compression.py = "5 级消息压缩流水线"
src/data_analysis_agent/protocol/client.py = "Anthropic 流式/非流式客户端 + 重试 + 懒导入"
src/data_analysis_agent/protocol/messages.py = "ContentBlock 类型层级"
src/data_analysis_agent/tools/base.py = "Tool 抽象基类 + ToolResult/Validation/Permission"
src/data_analysis_agent/tools/registry.py = "工具注册/过滤/装配(3 阶段)"
src/data_analysis_agent/tools/file_read.py = "按 offset/limit 读文件"
src/data_analysis_agent/tools/data_profile.py = "只读数据画像:文件/目录结构发现(CSV/TSV/Parquet/Excel 多 sheet),供发现 sheet 与跨文件连接键(路径白名单)"
src/data_analysis_agent/tools/python_exec.py = "受限子进程执行 + 采样摘要注入"
src/data_analysis_agent/tools/nl_query.py = "自然语言 → pandas/SQL 代码生成"
src/data_analysis_agent/tools/visualization.py = "matplotlib/seaborn/plotly 图表生成"
src/data_analysis_agent/tools/html_report.py = "结构化输入 → 自包含 H5 HTML 报告(ECharts),输出限定产物目录"
src/data_analysis_agent/tools/retrieve_result.py = "retrieve_result 工具:按行分页回取被摘要前的原始工具结果"
src/data_analysis_agent/tools/report_need.py = "report_need 只读工具:raw_request → UserNeed(显式/隐式分离 + uncertainty,封装 reporting.requirement_parser)"
src/data_analysis_agent/tools/report_context.py = "report_context 只读工具:data_profile+事件 → DataContext+ProcessContext(封装 reporting.context_collector)"
src/data_analysis_agent/tools/report_contract.py = "report_contract 只读工具:UserNeed+上下文 → ReportContract(field_sources+四类 ref+missing_context,封装 reporting.traceability)"
src/data_analysis_agent/skills/base.py = "Skill 抽象基类"
src/data_analysis_agent/skills/registry.py = "技能注册 + 关键词匹配 + 优先级路由"
src/data_analysis_agent/skills/builtin.py = "描述性/相关性/趋势/报告生成/联合分析 五个内置分析技能"
src/data_analysis_agent/skills/loader.py = "DeclarativeSkill + 从 JSON 记录装载/保存(L2 进化载体,status 流转)"
src/data_analysis_agent/evolution/synthesizer.py = "轨迹筛选/聚类 → reflect_fn 反思 → candidate 技能(离线,过拟合防护)"
src/data_analysis_agent/evolution/memory_miner.py = "轨迹 → L1 领域记忆抽取(注入式 extract_fn;metric 写未确认,(kind,key) 去重;离线 sink)"
src/data_analysis_agent/evolution/evaluator.py = "fixture 重跑 + A/B + 最小样本门槛 + promote/rollback(断言验证方法非数值)"
src/data_analysis_agent/evolution/eval_harvester.py = "轨迹 → EvalTask JSON + fixture 冻结(解决 eval 冷启动;断言验证方法非数值;离线 sink)"
src/data_analysis_agent/evolution/__main__.py = "进化离线 CLI:synthesize/mine-memory/list/evaluate;llm_reflect/llm_extract 默认实现"
src/data_analysis_agent/security/permissions.py = "deny-first 权限引擎(4 层防御)"
src/data_analysis_agent/security/tool_gate.py = "ToolGate:单次工具授权决策(decide 引擎策略 / validate 自检校验),agent_loop 的测试接缝"
src/data_analysis_agent/sampling/config.py = "SamplingConfig + fidelity 档位预设"
src/data_analysis_agent/sampling/model.py = "ColumnSummary / TableSummary 数据类"
src/data_analysis_agent/sampling/render.py = "L3 Markdown 渲染器(共享,带采样警告)"
src/data_analysis_agent/sampling/text_summary.py = "harness 纯 stdlib 兜底摘要器"
src/data_analysis_agent/sampling/sandbox_summary.py = "精确 DataFrame 摘要,内联进 python_exec 沙箱"
src/data_analysis_agent/sampling/result_store.py = "持久化结果存储(CCR-lite):原文落盘 + 按行回取 + TTL/容量回收"
src/data_analysis_agent/telemetry/trajectory.py = "TurnRecord/TrajectoryLogger:实现 EventConsumer,按会话落 JSONL 轨迹(自进化原料)"
src/data_analysis_agent/telemetry/feedback.py = "显式(/good /bad)与隐式(rephrase)反馈信号"
src/data_analysis_agent/memory/model.py = "MemoryEntry(三类文本记忆)+ DatasetProfile(结构层/统计层/列指纹)"
src/data_analysis_agent/memory/store.py = "MemoryStore:JSONL 文本记忆,关键词+子串检索;touch 仅记最近用,note_accepted_use 驱动口径轻确认"
src/data_analysis_agent/memory/profiler.py = "数据集画像确定性生成 + 列指纹分层失效(fresh/stale/invalid)"
src/data_analysis_agent/memory/injector.py = "MemoryInjector:render 注入 + record_tool 在线画像 + remember_metric/pref 显式写入 + adjudicate(rephrase-gated 轻确认)"
src/data_analysis_agent/reporting/model.py = "报告领域层(Wave1):UserNeed/DataContext/ProcessContext/TraceLink + 显式/隐式需求分离 + 通用 to_dict/from_dict(纯 stdlib,ADR 0009)"
src/data_analysis_agent/reporting/requirement_parser.py = "报告领域层(Wave1):确定性需求解析(raw_request → UserNeed,显式/隐式分离,CJK 启发式,无 LLM)"
src/data_analysis_agent/reporting/context_collector.py = "报告领域层(Wave1):data_profile→DataContext、工具事件→ProcessContext(纯 dict 输入,sensitive_mode 隐私降级)"
src/data_analysis_agent/reporting/traceability.py = "报告领域层(Wave1):契约字段溯源映射(需求/数据/过程 → TraceLink,中读解释,无依据不产 link)"
src/data_analysis_agent/reporting/contract.py = "报告领域层(Wave2):ReportContract/MetricSpec/EvidenceRef/ChartSpec/ReportDocument 契约与文档模型 + 封闭词表枚举(纯 stdlib,ADR 0009)"
src/data_analysis_agent/reporting/chart_rules.py = "报告领域层(Wave2):图族选择 + 数据充分性 + fallback(MIN_TREND/MIN_SCATTER,确定性,无 LLM)"
src/data_analysis_agent/reporting/qa.py = "报告领域层(Wave2):确定性 QA(readiness 三态 + blocker/high/medium/info 规则,无 LLM,ADR 0009)"
```

<!-- manifest:end -->
