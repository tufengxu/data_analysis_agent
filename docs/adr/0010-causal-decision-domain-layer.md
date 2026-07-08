# 0010 — causal 纯 stdlib 因果决策领域层

- 状态: Accepted (2026-07-08)

## 背景

路线图 P1-10(`docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md` §P1-10)要求把
DataAnalysisAgent 从"客观数据报告"推进到"决策支持":在严格区分 描述 / 相关 / 实验 /
因果假设 四类声称的前提下,把随机化 A/B 实验数据转成可审计、有界的业务决策。可执行方案
`docs/superpowers/plans/2026-07-08-causal-decision-stage1-executable.md` 把这一目标拆成
Stage 1 切片,其核心是一个新的因果领域包。

因果决策是"业务正确性"载体(甚至比报告更甚:错误地把相关当因果会直接导致错误的业务
动作),必须可被确定性测试与 eval gate 判定,不能混入运行时副作用。项目已有
`reporting` 纯领域层的成功先例(ADR 0009):纯 stdlib + 不可变 dataclass + drift 强制
依赖边界。causal 沿用同一范式。

## 决策

新建 `src/data_analysis_agent/causal/` 包为**纯 stdlib 因果决策领域层**:

- `model.py`:封闭词表枚举(`VariableRole`/`AssignmentMechanism`/`ClaimLevel`/
  `CausalReadiness`/`DecisionLevel`/`OutcomeKind`)+ 领域数据类(`CausalIntent`/
  `CausalQuestion`/`VariableBinding`/`CausalContract`/`CausalFinding`/`CausalQAReport`/
  `EffectEstimate`/`SRMResult`/`GuardrailResult`/`SegmentBreakdown`/`ContrastResult`/
  `ExperimentReadout`/`ActionRecommendation`/`ActionPlan`)。
- `intent.py`:确定性因果/实验/行动意图解析(无 LLM)。
- `qa.py`:因果就绪 QA(6 态 `CausalReadiness` + 闭词汇 finding)。
- `experiment.py`:A/B 统计(正态近似 z 检验)+ SRM(卡方)+ 护栏 + 决策分类。
- `report_adapter.py`:causal 结果 → `reporting.contract.ReportDocument`(唯一导入
  reporting 的模块)。

**复用而非重写**:causal 的所有数据类继承 `reporting.model.Serializable`(单一事实源,
往返契约 `Cls.from_dict(x.to_dict()) == x`),并复用 `SourceKind`。因此 causal **单向依赖**
`reporting`;反向(reporting → causal)由 drift 强制禁止,保持依赖 DAG 无环。

**依赖边界**由 `scripts/drift_rules.py::IMPORT_RULES` 强制:`causal` 禁止 import 除
`reporting` 外的任何 `data_analysis_agent.*` 顶层内部包。**为何不禁 reporting**:causal 复用
reporting 的 Serializable mixin 与 ReportDocument 渲染目标;reporting 本身是纯 stdlib 叶子,
causal 在其之上,属合理的同向依赖(与 `web → reporting` 同构)。**为何不用 catch-all forbid**:
与 ADR 0009 同理,`{forbid: ["data_analysis_agent"]}` 会误伤包内相对导入
`from .model import ...`(解析为 `data_analysis_agent.causal.model`,前缀命中),故必须枚举
各顶层包。同时把 `"data_analysis_agent.causal"` 加入 reporting 的 forbid 列表,防止反向耦合。

## 理由

因果结论的错误代价高于描述性结论:把观察性相关当因果,会直接产出"扩大投放"等错误业务
动作。因此因果领域层必须是"可审计的不变量":给定 CausalContract + 数据,QA 结果与实验
读出唯一且无外部依赖。纯 stdlib + 不可变 dataclass 让 Stage 1 的统计(z 检验 CI、SRM 卡方、
决策分类)完全确定性可测;`welch_z_approx`、`degenerate`、`notes` 等字段把方法学显式化,
供报告 QA 与人工审查。

统计口径刻意选**正态近似 z 检验**(纯 `math.erfc`,确定性,无 scipy):Stage 1 的目标是
建立"契约 + QA + 有界决策"的审计面,而非追求估计精度。DoWhy/EconML/CausalML、因果发现、
观察性估计量、多重比较校正、分群级检验、卡方 p 值均显式 defer(见 plan §16),待契约面
稳定后再以 adapter 形式接入(P2-12)。

与 ADR 0004(记结构不记数值)、ADR 0005(eval 验证方法非数值)一脉相承:实验读出携带
效应/CI 数值,但 eval fixture 只断结构(决策态、工具、报告 section),不断具体数值,避免
grader gaming 与数值锚定漂移。

## 影响

新增 `src/data_analysis_agent/causal/`(`__init__.py` / `model.py` / `intent.py` 本 ADR
同批;`qa.py` / `experiment.py` / `report_adapter.py` 随后续切片)+ tests;
`scripts/drift_rules.py` 加 causal 条目 + reporting forbid 增 causal;`docs/ARCHITECTURE.md`
manifest + 依赖规则一节同步。本批零 runtime 接线(不注册工具、不改 agent_loop/runtime/
skills/html_report);工具/skill 在后续切片接到此领域层之上。

## 实现偏离(相对 plan §4,均记录)

1. **`CausalIntent` 增 `has_observation_marker` 字段**(plan §4 的 CausalIntent 7 字段 → 8)。
   理由:plan §3 的"仅观察性表述 → ASSOCIATIONAL"规则需要载体;`infer_claim_level` 据此把
   "相关/关联/correlation"等表述确定性降为 ASSOCIATIONAL,不得升级为 causal。
2. **`CausalReadiness` 6 态 + 到 `reporting.Readiness` 的映射只在 `report_adapter` 做**。
   理由:causal 的就绪语义比 reporting 三态丰富;映射收敛在唯一导入 reporting 的适配模块,
   保持 `qa.py` 纯 stdlib。
3. **`ContrastResult` + `EffectEstimate`/`SRMResult`/`GuardrailResult`/`SegmentBreakdown` 拆分**
   (plan §4 已含)。理由:真实种子 `mobile_app_ab_test.csv` 是三臂(control/variant_a/
   variant_b),需 first-class 多对比;拆分使读出可审计、退化情形可标。
4. **`ActionPlan` 数据类归入 `model.py`(slice 1)**,只有其工具在 slice 6。理由:所有领域
   类型同源,避免跨切片类型归属混乱。
