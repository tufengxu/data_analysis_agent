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
- `disk_cap.py` ✗→ 任何 `data_analysis_agent.*`(纯 stdlib 叶子,被 telemetry/skills 组合,目录磁盘上限 helper)
- `pii.py` ✗→ 任何 `data_analysis_agent.*`(纯 stdlib 叶子,被 telemetry/**main** 组合,PII scrubber)
- `telemetry/*` ✗→ `agent_loop`/`tools`/`skills`/`protocol`/`security`(经 EventConsumer 反向解耦)
- `memory/*` ✗→ `agent_loop`/`tools`/`skills`/`protocol`/`security`(经回调注入反向解耦;可依赖 `context`)
- `security/tool_gate.py` ✗→ `agent_loop`/`runtime`/`session`(被 agent_loop 依赖的授权接缝,不得反向耦合)
- `recovery.py` ✗→ `agent_loop`/`runtime`/`session`(被 agent_loop 依赖的恢复策略接缝,不得反向耦合)
- 任何核心模块 ✗→ `evolution`(evolution 是顶层离线 sink,依赖向下,不被 core 依赖)
- `evolution/synthesizer.py` ✗→ `protocol`/`agent_loop`(反思经 reflect_fn 注入;仅离线 CLI 入口可依赖 `protocol`)
- `reporting/*` ✗→ 任何 `data_analysis_agent.*`(纯 stdlib 领域层,被 tools 单向依赖;见 ADR 0009)
- `web/*` ✗→ 一切内部包(仅允许 `reporting`+ fastapi/starlette/pydantic;见 Wave 8 plan)
- `causal/*` ✗→ 除 `reporting` 外的一切内部包(纯 stdlib 因果决策领域层,单向依赖 reporting 复用 Serializable;见 ADR 0010)

## v2:能力核心层与双基座(2026-08)

四层结构(依赖只准向下;`capabilities/*` 禁入下列所有 v1 harness/装配/表现层,
由 drift 规则强制;第三基座接入只需新增 `harnesses/<name>/` 适配目录,能力层零改动,
见 `docs/THIRD_HARNESS_GUIDE.md`):

```
能力核心层 capabilities/(contracts + tabular/reporting/causal/evolution/sampling)
        ↓
统一暴露层 capabilities/serving/(MCP stdio server,官方 mcp SDK + CLI data-agent-capabilities)
        ↓
基座适配层 harnesses/pi、harnesses/deepseek/(仅胶水:工具代理/结果压缩接缝/轨迹翻译;
        spawn 白名单与规模上限由 checks.check_harness_adapters 强制)
        ↓
Agent 组装(preset:系统提示 + 工具注册 + 技能注入 + 事件接线)
```

- **迁移方式**:sampling 物理迁移(v1 `sampling/` 为纯 re-export shim,公共 API 不变);
  causal/reporting 委托式迁移(契约在能力层,实现单向复用纯领域层);tabular 委托 v1
  tools/kernel(KernelHolder 持久内核跨调用存活)。
- **ToolResultCompactor**(`capabilities/sampling/compactor.py`):harness 无关压缩接缝,
  v1 `agent_loop` 与两基座适配层共用同一实现;ResultStore 支持多进程共享
  (mtime 检测重载,best-effort)。
- **TrajectoryEvent**(`capabilities/evolution/`):`daa.trajectory.v1` 契约,记结构不记数值
  (digest 字段强制 `^[0-9a-f]{12}:\d+$`);Pi/dsh 翻译器在适配层,经
  `evolution_record_event` 写入,`load_v2_turns` 供离线 evolution 管线消费。
- **传输一致性**:进程内直调 / MCP stdio / CLI 三传输等价输出由
  `tests/test_capability_serving.py` 守护;真实 stdio 分帧由
  `examples/v2/smoke_stdio_mcp.py` 验证。
- 运行/验证手册:`docs/V2_RUNBOOK.md`;v1 全部行为与测试不回归(R2)。

## 模块 manifest

<!-- manifest:start -->

```
src/data_analysis_agent/capabilities/contracts.py = "v2 能力契约:CapabilitySpec/Registry/fail-closed 执行包络(Harness 无关,三传输同源)"
src/data_analysis_agent/capabilities/causal/registry.py = "因果能力域注册:causal_analyze(分析)/causal_estimate(推断)/causal_report,委托纯领域层 causal/*(委托式迁移)"
src/data_analysis_agent/capabilities/reporting/registry.py = "报告能力域注册:need/context/contract 纯函数 + chart/html 渲染(委托 tools.chart_render/html_report,产物目录限定)"
src/data_analysis_agent/capabilities/tabular/registry.py = "表格能力域注册:7 能力委托 v1 tools;KernelHolder 持久内核跨调用存活"
src/data_analysis_agent/capabilities/evolution/trajectory.py = "TrajectoryEvent 契约(daa.trajectory.v1):记结构不记数值,digest 字段拒绝原文"
src/data_analysis_agent/capabilities/evolution/store.py = "TrajectoryWriter 轨迹 JSONL 写入(DAA_HOME/trajectories/v2)+ verify_file 校验"
src/data_analysis_agent/capabilities/evolution/convert.py = "v2 轨迹 → v1 TurnRecord 转换器(离线 evolution 管线消费)"
src/data_analysis_agent/capabilities/evolution/registry.py = "自进化能力域注册:evolution_record_event / evolution_verify_trajectory"
src/data_analysis_agent/capabilities/serving/registry.py = "serving 全量装配:五域 register_all + sampling_compact_result/retrieve_result(DAA_CAPABILITIES_* 环境变量)"
src/data_analysis_agent/capabilities/serving/mcp_server.py = "MCP stdio server(mcp SDK 2.x 低层 API):动态 schema 来自 CapabilitySpec,envelope JSON 文本返回"
src/data_analysis_agent/capabilities/serving/cli.py = "data-agent-capabilities CLI(mcp/list/call/compact/retrieve;_run_coro 支持嵌入异步宿主)"
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
src/data_analysis_agent/doctor.py = "doctor 健康检查:API key / data extras / DAA_HOME 可写 / ~/.daa 各子目录磁盘用量 / ECharts 模式 / 权限预设 / kernel python / Web 端口的 pass/warn/fail 只读报告(P1-1.7)"
src/data_analysis_agent/persistence.py = "append-only JSONL 消息存储 + session fork(组合 JsonlStore)"
src/data_analysis_agent/jsonl_store.py = "JsonlStore primitive:原子重写 + 读容错 + 只读降级(纯 stdlib 叶子)"
src/data_analysis_agent/disk_cap.py = "目录磁盘上限 helper:best-effort 按 byte cap 淘汰最旧/最低 rank 文件,protected 永不删(纯 stdlib 叶子;trajectory/skills 共用)"
src/data_analysis_agent/pii.py = "PII scrubber:best-effort 正则 redact 邮箱/中国手机/身份证/IPv4(纯 stdlib 叶子;trajectory 落盘前 scrub user_input/final_text_digest/tool input_digest,__main__ scrub manifest request)"
src/data_analysis_agent/workspace.py = "Project/ProjectManifest/RunManifest:本地项目工作区,把一次 run 的 session 态产物(artifact/kernel/results/messages)统一到同一根 + project/run 清单(原子写,opt-in;trajectories/memory/skills 仍走全局 ~/.daa,P1-2)"
src/data_analysis_agent/context/compression.py = "5 级消息压缩流水线"
src/data_analysis_agent/protocol/client.py = "Anthropic 流式/非流式客户端 + 重试 + 懒导入"
src/data_analysis_agent/protocol/messages.py = "ContentBlock 类型层级"
src/data_analysis_agent/tools/base.py = "Tool 抽象基类 + ToolResult/Validation/Permission"
src/data_analysis_agent/tools/registry.py = "工具注册/过滤/装配(3 阶段)"
src/data_analysis_agent/tools/file_read.py = "按 offset/limit 读文件"
src/data_analysis_agent/tools/data_profile.py = "只读数据画像:文件/目录结构发现(CSV/TSV/Parquet/Excel 多 sheet),供发现 sheet 与跨文件连接键(路径白名单)"
src/data_analysis_agent/tools/data_quality.py = "只读数据质量检查:单文件缺失/重复行/唯一性/常量列/数值离群(IQR)/类型异常(数字·日期存文本),与 data_profile 结构发现互补(路径白名单)"
src/data_analysis_agent/tools/join_planner.py = "只读跨表 join 顾问:多文件/多 sheet → 候选键(同名列)/唯一性→关系(1:1/1:N/N:1/N:N)/值覆盖/估算连接行数/行乘积风险/null-key 风险/推荐连接顺序(大表为锚,优先入端 unique),与 data_profile+data_quality 互补(路径白名单)"
src/data_analysis_agent/tools/metric_contract.py = "只读口径规整工具:name/numerator/denominator/aggregation/filters/exclusions/time_window/grain/timezone/unit → MetricSpec + 完整性校验(至少一个 num/den/agg 等)+ memory_definition 交叉核对(confirmed/unconfirmed/absent + 名字一致性)+ signature;无状态无路径(镜像 report_contract)"
src/data_analysis_agent/tools/python_exec.py = "受限子进程执行 + 采样摘要注入"
src/data_analysis_agent/tools/nl_query.py = "自然语言 → pandas/SQL 代码生成"
src/data_analysis_agent/tools/visualization.py = "matplotlib/seaborn/plotly 图表生成"
src/data_analysis_agent/tools/html_report.py = "结构化输入 → 自包含 H5 HTML 报告(ECharts),输出限定产物目录"
src/data_analysis_agent/tools/retrieve_result.py = "retrieve_result 工具:按行分页回取被摘要前的原始工具结果"
src/data_analysis_agent/tools/report_need.py = "report_need 只读工具:raw_request → UserNeed(显式/隐式分离 + uncertainty,封装 reporting.requirement_parser)"
src/data_analysis_agent/tools/report_context.py = "report_context 只读工具:data_profile+事件 → DataContext+ProcessContext(封装 reporting.context_collector)"
src/data_analysis_agent/tools/report_contract.py = "report_contract 只读工具:UserNeed+上下文 → ReportContract(field_sources+四类 ref+missing_context,封装 reporting.traceability)"
src/data_analysis_agent/tools/causal_contract.py = "causal_contract 只读工具:问题+上下文 → CausalContract(intent/claim_level/assignment/missing_context,封装 causal.intent;Stage1,ADR 0010)"
src/data_analysis_agent/tools/causal_qa.py = "causal_qa 只读工具:CausalContract → CausalQAReport(6 态就绪 + 闭词汇 finding,封装 causal.qa;观察性永不 EXPERIMENT_READY)"
src/data_analysis_agent/tools/experiment_readout.py = "experiment_readout 只读工具:records/columns → ExperimentReadout(效应/SRM/护栏/有界决策,封装 causal.experiment;正态近似 z 检验)"
src/data_analysis_agent/tools/causal_action_plan.py = "causal_action_plan 只读工具:ExperimentReadout → ActionPlan(机制/假设/监控/回滚/反驳;SRM/退化不升 ship,封装 causal.experiment.build_action_plan)"
src/data_analysis_agent/tools/causal_report.py = "causal_report 只读工具:contract+qa+readout(+action_plan) → ReportDocument(FINDING 紧跟 CAVEAT + 中性措辞,封装 causal.report_adapter.to_report_document;让 causal 结果经 QA 闸进 html_report v2)"
src/data_analysis_agent/tools/chart_render.py = "chart_render 工具:结构化 ChartSpec+数据 → ECharts option+JSON artifact(按图族生成 + 数据充分性 + fallback,非只读)"
src/data_analysis_agent/skills/base.py = "Skill 抽象基类"
src/data_analysis_agent/skills/registry.py = "技能注册 + 关键词匹配 + 优先级路由"
src/data_analysis_agent/skills/builtin.py = "描述性/相关性/趋势/报告生成/联合分析 五个内置分析技能"
src/data_analysis_agent/skills/causal_skill.py = "因果决策分析内置技能:路由因果/实验/行动请求(causal_contract→causal_qa→experiment_readout→causal_action_plan,强制工作流 + 禁止相关当因果,Stage1)"
src/data_analysis_agent/skills/loader.py = "DeclarativeSkill + 从 JSON 记录装载/保存(L2 进化载体,status 流转)"
src/data_analysis_agent/evolution/synthesizer.py = "轨迹筛选/聚类 → reflect_fn 反思 → candidate 技能(离线,过拟合防护)"
src/data_analysis_agent/evolution/memory_miner.py = "轨迹 → L1 领域记忆抽取(注入式 extract_fn;metric 写未确认,(kind,key) 去重;离线 sink)"
src/data_analysis_agent/evolution/evaluator.py = "fixture 重跑 + A/B + 最小样本门槛 + promote/rollback(断言验证方法/结构;冻结 fixture 上允许 numeric_anchor 数值锚——ADR 0005 例外)"
src/data_analysis_agent/evolution/eval_harvester.py = "轨迹 → EvalTask JSON + fixture 冻结(解决 eval 冷启动;断言验证方法非数值;离线 sink)"
src/data_analysis_agent/evolution/eval_taxonomy.py = "eval 失败分类学:区分 code/tool vs 报告质量 vs 数值正确性失败(spec §8 Wave 7+8)"
src/data_analysis_agent/evolution/__main__.py = "进化离线 CLI:synthesize/mine-memory/list/evaluate;llm_reflect/llm_extract 默认实现"
src/data_analysis_agent/security/permissions.py = "deny-first 权限引擎(4 层防御)"
src/data_analysis_agent/security/tool_gate.py = "ToolGate:单次工具授权决策(decide 引擎策略 / validate 自检校验),agent_loop 的测试接缝"
src/data_analysis_agent/security/sanitizer.py = "确定性 prompt-injection 净化叶(结构性载体剥离 + 注入标记检出 + 数值泄露检出 + 数据框包装;纯 stdlib,agent_loop/skills/runtime 注入 memory)"
src/data_analysis_agent/capabilities/sampling/compactor.py = "ToolResultCompactor 接缝契约 + DefaultToolResultCompactor 参考实现(v1 compact_result 语义 + ResultStore 召回句柄)"
src/data_analysis_agent/capabilities/sampling/config.py = "SamplingConfig + fidelity 档位预设(v2 物理迁移自 sampling/)"
src/data_analysis_agent/capabilities/sampling/model.py = "ColumnSummary / TableSummary 数据类(v2 物理迁移)"
src/data_analysis_agent/capabilities/sampling/render.py = "L3 Markdown 渲染器(共享,带采样警告;v2 物理迁移)"
src/data_analysis_agent/capabilities/sampling/text_summary.py = "harness 纯 stdlib 兜底摘要器(v2 物理迁移)"
src/data_analysis_agent/capabilities/sampling/sandbox_summary.py = "精确 DataFrame 摘要,内联进 python_exec 沙箱(自包含约束不变;v2 物理迁移)"
src/data_analysis_agent/capabilities/sampling/result_store.py = "持久化结果存储(CCR-lite):原文落盘 + 按行回取 + TTL/容量回收(v2 物理迁移)"
src/data_analysis_agent/sampling/config.py = "v1 shim → capabilities/sampling/config(物理迁移)"
src/data_analysis_agent/sampling/model.py = "v1 shim → capabilities/sampling/model(物理迁移)"
src/data_analysis_agent/sampling/render.py = "v1 shim → capabilities/sampling/render(物理迁移)"
src/data_analysis_agent/sampling/text_summary.py = "v1 shim → capabilities/sampling/text_summary(物理迁移)"
src/data_analysis_agent/sampling/sandbox_summary.py = "v1 shim → capabilities/sampling/sandbox_summary(物理迁移)"
src/data_analysis_agent/sampling/result_store.py = "v1 shim → capabilities/sampling/result_store(物理迁移)"
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
src/data_analysis_agent/reporting/templates.py = "报告领域层(Wave6):8 报告类型 curated 模板(section-role spine + 默认图族 + 必备 caveat)+ 确定性 select/match_template"
src/data_analysis_agent/reporting/overlays.py = "报告领域层:域 overlay(retail/saas/finance/... 微调模板 required_caveats,确定性)"
src/data_analysis_agent/causal/model.py = "因果决策领域层(Stage1):CausalContract/CausalReadiness/EffectEstimate/SRMResult/ContrastResult/ExperimentReadout/ActionPlan + 封闭词表枚举(复用 reporting.Serializable,纯 stdlib,ADR 0010)"
src/data_analysis_agent/causal/intent.py = "因果决策领域层(Stage1):确定性因果/实验/行动意图解析 + claim_level 推断(无 LLM,全小写子串匹配)"
src/data_analysis_agent/causal/qa.py = "因果决策领域层(Stage1):确定性因果就绪 QA(6 态 CausalReadiness + 闭词汇 finding,无 LLM,ADR 0010)"
src/data_analysis_agent/causal/experiment.py = "因果决策领域层(Stage1):A/B 实验统计(正态近似 z 检验 CI)+ SRM 卡方 + 护栏 + 决策分类(纯 stdlib math,退化数据不算 p,ADR 0010)"
src/data_analysis_agent/causal/report_adapter.py = "因果决策领域层(Stage1):causal 结果 → reporting.ReportDocument 适配(FINDING 紧跟 CAVEAT + readiness 映射;唯一导入 reporting 的模块,ADR 0010)"
src/data_analysis_agent/web/app.py = "Web Workbench(Wave8 MVP):FastAPI app + API 端点(need/context/contract/qa/template)+ artifact 安全预览(消费 reporting 纯函数)"
src/data_analysis_agent/web/schemas.py = "Web Workbench(Wave8):Pydantic 请求模型"
src/data_analysis_agent/web/__main__.py = "Web Workbench(Wave8):uvicorn 启动入口(data-agent-web)"
src/data_analysis_agent/server/event_codec.py = "AgentEvent → 稳定 SSE JSON dict 的纯函数映射(roadmap P1-3.5 事件 codec 契约;字段名冻结)"
src/data_analysis_agent/server/app.py = "FastAPI workbench:/api/run/stream(SSE 跑 runtime 推事件)+ /api/upload + /api/approval + 静态首页;localhost-only,复用 AgentRuntime.from_config"
src/data_analysis_agent/server/approval.py = "Web 审批通道(P1-3.7):AWAITING_CONFIRMATION 时挂起 SSE、等浏览器 /api/approval 裁决,超时=deny(fail-closed);threading.Event 跨线程/循环安全"
src/data_analysis_agent/server/__main__.py = "uvicorn 启动入口(python -m data_analysis_agent.server,强制绑 127.0.0.1)"
src/data_analysis_agent/server/bind.py = "localhost-only 绑定策略(P1-3.2):is_loopback/resolve_bind_host/unsafe_warning;server 与 web 两入口共享,非 loopback 无 --unsafe 即 fail-closed 拒启"
```

<!-- manifest:end -->
