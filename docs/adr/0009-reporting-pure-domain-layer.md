# 0009 — reporting 纯 stdlib 领域层(报告契约/文档/QA)

- 状态: Accepted (2026-07-06)

## 背景

报告交付审计(`docs/roadmap/2026-07-06-analysis-report-quality-audit.md`)给出 2.3/5:渲染器(`html_report`)强,报告智能层(契约/口径/图表语义/QA/eval)弱。设计基线(`docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md` §5.1)要求新建 `reporting` 包承载 UserNeed / DataContext / ProcessContext / ReportContract / MetricSpec / EvidenceRef / ChartSpec / ReportDocument / QA 等领域模型,且"may depend on stdlib and low-level shared utilities only ... must not depend on agent_loop, protocol, runtime, or evolution"。这是项目第一次引入一个横跨未来多个 Wave 的纯领域层,需要钉死其依赖边界,避免被运行时反向耦合(项目已有 telemetry/memory/evolution 经回调反向解耦的先例,见 `scripts/drift_rules.py`)。

## 决策

新建 `src/data_analysis_agent/reporting/` 包为**纯 stdlib 领域层**:Wave 1 上游理解层(UserNeed / DataContext / ProcessContext / TraceLink + 显式/隐式需求分离)+ Wave 2 契约/文档层(ReportContract / MetricSpec / EvidenceRef / ChartSpec / ReportDocument)与确定性 QA(readiness 三态 + blocker/high/medium/info,无 LLM)。依赖边界由 `scripts/drift_rules.py::IMPORT_RULES` 强制:`reporting` 禁止 import 任何 `data_analysis_agent.*` 顶层内部包(`agent_loop`/`protocol`/`runtime`/`evolution`/`telemetry`/`memory`/`tools`/`skills`/`session`/`kernel`/`context`/`security`/`sampling`/`persistence`/`state_machine`/`events`/`config`/`recovery`/`jsonl_store`/`artifacts`)。`tools` 未来可单向依赖 `reporting`(Wave 3+),反向禁止。

**为何不用 catch-all forbid**:`{forbid: ["data_analysis_agent"]}` 会误伤包内相对导入 `from .model import SourceKind`(解析为 `data_analysis_agent.reporting.model`,前缀命中 `data_analysis_agent`),使 reporting 内部模块互相不可引用。故必须**枚举各顶层包**而非用全量前缀。未来维护者勿"简化"为 catch-all。

**`contract.py` 物理拆分**:spec §5.1 的逻辑模块 "model" 实际约 700 LOC,为绕开 `scripts/checks.py::check_file_sizes` 的 `FILE_SIZE_LIMIT = 600` 告警(warning 非 error),拆为 `model.py`(Wave 1 上游)+ `contract.py`(Wave 2 契约/文档)。`contract.py` 是对 spec §5.1「Likely modules」名单的物理细化,非新职责。

## 理由

报告契约/文档/QA 是"业务正确性"载体,必须可被确定性测试与未来 eval gate 直接判定,不能混入运行时副作用(I/O、模型调用、状态)。纯 stdlib + 不可变 dataclass 让 `reporting` 可作为"报告就绪度"的不变量证明:给定 ReportDocument,QA 结果唯一且无外部依赖。这与 ADR 0004(记结构不记数值)、ADR 0005(eval 验证方法非数值)一脉相承——把可变性、外部依赖、数值锚定从核心判定逻辑中剥离。drift 强制则防止未来 Wave 接线时无意把运行时耦合进来(项目"接线经回调/旁路、核心不反向依赖"的既有模式在 `agent_loop` ✗→ evolution/telemetry/memory 上已验证)。

## 影响

新增 `src/data_analysis_agent/reporting/`(Wave 1-2:`__init__.py` / `model.py` / `requirement_parser.py` / `context_collector.py` / `traceability.py` / `contract.py` / `chart_rules.py` / `qa.py`)+ tests;`scripts/drift_rules.py` 加 reporting 条目;`docs/ARCHITECTURE.md` manifest + 依赖规则一节同步。Wave 1-2 零 runtime 接线(不注册工具、不改 agent_loop/runtime/skills/html_report)。Wave 3+ 的工具/技能/HTML v2 在此领域层之上构建。

## 实现偏离(相对 spec §5.1 / 计划,均经独立代码审查确认 sound)

1. **`ChartFamily` 定义于 `contract.py` 而非 `chart_rules.py`**(spec §5.1 / 计划 File Structure 把图族归属 chart_rules)。理由:`ChartSpec.family`(在 contract.py)需要该类型,把封闭词表与其数据类放一起避免循环依赖;`chart_rules.py` 反向 import。域模型拥有封闭词表,helper 消费之。
2. **`ChartSpec.fallback_family: ChartFamily | None`**(计划写 `str | None`)。更强的类型安全,避免任意字符串流入图族字段。
3. **`contract.py` 物理拆分**(spec §5.1 逻辑模块 "model" → `model.py` + `contract.py`),理由见「决策」末段。
4. **三条 spec §7 QA 规则显式 defer**(通用 inferred-as-explicit → Wave 3;表 vs 可视化 → Wave 5;离线 ECharts → Wave 4),理由见计划 Self-Review 与 `qa.py` 模块头注释。
