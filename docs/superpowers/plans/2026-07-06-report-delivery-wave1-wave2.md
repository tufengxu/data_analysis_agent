# 报告交付优化 · Wave 1-2 实现计划 — reporting 纯领域层 + 确定性 QA 骨架

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development`(推荐)或 `superpowers:executing-plans` 按 Task 逐项实现。步骤用 `- [ ]` 复选框追踪。

> **Baseline:** `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`(设计基线,2026-07-06 冻结)
> **Audit:** `docs/roadmap/2026-07-06-analysis-report-quality-audit.md`(现状 2.3/5)
> **Scope:** 仅 Wave 1 + Wave 2。**零 runtime 改动**(不注册工具、不改 agent_loop/runtime/skills/html_report)。Wave 3+ 触及 runtime,作为后续独立变更。
> **Review:** 计划已经一轮独立只读子 Agent 评审(APPROVE-WITH-FIXES),本版已并入全部 11 条修订(见文末 Self-Review §「评审修订记录」)。

## Goal

把报告交付从「渲染器主导」升级的第一刀:建立 `reporting` 纯 stdlib 领域层,让「报告契约可溯源」与「报告就绪度可机器判定」成为不变量。Wave 1 建上游理解层(UserNeed / DataContext / ProcessContext / TraceLink + 显式/隐式需求分离),Wave 2 建契约层与确定性 QA(ReportContract / MetricSpec / EvidenceRef / ChartSpec / ReportDocument + readiness 三态)。

落地的设计 acceptance(对应 spec §8 Wave 1/2):

- 显式需求与推断需求**分开表示**,推断项标注来源(anti-hallucination 原语)。
- DataContext 能承载选定文件/sheet、schema、候选指标、日期范围、数据缺口。
- ProcessContext 能承载工具序列、假设、失败路径、派生表、artifact id(不依赖原始 chat 日志)。
- TraceLink 能解释契约字段为何存在。
- QA 能确定性分类 `draft / needs_review / ready`,阻断「契约与用户需求/上下文断链」的报告,阻断缺证据/缺图表 spec 的报告,**无需 LLM**。

## Architecture

新建 `src/data_analysis_agent/reporting/` 包,**仅依赖 stdlib**(`dataclasses`/`enum`/`json`/`typing`/`re`/`datetime`/`collections.abc`),不 import 任何 `data_analysis_agent.*` 内部模块。这是设计 §5.1 的硬依赖规则,由 `scripts/drift_rules.py` 新增条目强制。未来 `tools` 可单向依赖 `reporting`(Wave 3+),反向禁止。

为避免单文件超 600 LOC 告警(spec §5.1 把所有 dataclass 归到逻辑模块 "model",但实际约 700 LOC),按 Wave 自然拆为两个物理文件:`model.py`(Wave 1 上游)+ `contract.py`(Wave 2 契约/文档)。两者同属"领域模型"职责;`contract.py` 是对 spec §5.1「Likely modules」名单的物理细化(体积治理),非架构偏离——ADR 0009 明示。

确定性 helper(no LLM):

- `requirement_parser`:关键词/shingle 启发式提取显式 vs 隐式需求(CJK 2-gram 哲学,与 `skills/registry.py` ADR 0006 同源,无共享 tokenizer)。
- `context_collector`:消费 `data_profile` 工具输出 dict + 摘要化工具事件 dict(纯 dict 输入,不依赖 Event/dataclass)→ 构造 DataContext/ProcessContext。
- `chart_rules`:图族选择 + 数据充分性(趋势最小点数 / 散点最小观测数)。
- `qa`:ReportDocument(+Contract)→ `list[QAFinding]` + readiness。

## Tech Stack

Python 3.10+(stdlib `dataclasses`/`enum`/`re`/`json`/`typing`/`datetime`/`collections.abc`),pytest,ruff,mypy strict。**零新第三方依赖**。注意 pyproject `requires-python = ">=3.10"`、ruff `target-version = "py310"`,故**不用** `StrEnum`(3.11+),枚举一律 `class X(str, Enum)`。

## Global Constraints

- **质量闸**:每 Task 末 `.venv/bin/python scripts/quality_gate.py`(ruff check + ruff format --check + mypy strict + pytest + drift)须全绿。
- **venv 首次**:跑测试前需 `uv pip install -e ".[data,dev]"`(沙箱会拦 uv 缓存,需关沙箱执行)。命令在 `DataAnalysisAgent/` 目录下。
- **manifest 同步**:每个新增的非 `__init__.py` 源文件**必须**在同一 Task 内加进 `docs/ARCHITECTURE.md` 的 `<!-- manifest:start -->`/`<!-- manifest:end -->` 段(否则 drift fail:模块未登记)。`__init__.py` 不需登记。本计划共新增 **7** 个需登记模块。
- **drift 规则**:在 `scripts/drift_rules.py::IMPORT_RULES` 加 `reporting` who/forbid 条目(禁一切内部包,见 Task 1)。改依赖规则 = 大改,故必须配 ADR。
- **ADR**:新增 `docs/adr/0009-reporting-pure-domain-layer.md`(架构决策:新模块 + 新依赖规则边界)。
- **向后兼容**:Wave 1-2 **只新增**。不改 `html_report.py`/`visualization.py`/`builtin.py`/`agent_loop`/`runtime`/`config` 任何现有公开 API。不注册任何工具。
- **不可变**:`reporting` 数据类一律 `@dataclass(frozen=True)`,序列化字段用 `tuple`(非 `list`/`dict`),与项目「state 不可变」不变量(`state_machine.py`)同源。`generated_at` 等时间字段由调用方注入,不在数据类内调 `datetime.now()`,保证测试确定性。
- **mypy strict**:全字段注解;`from __future__ import annotations` 统一加。
- **enum 序列化约定**:`to_dict` 对 Enum 字段输出 `.value`(裸 str/基础类型);`from_dict` 经 `EnumCls(value)` 重建。嵌套 frozen dataclass 递归重建。
- **snake_case 内部 JSON**(spec §12 推荐)。
- **QA 为纯函数**(spec §12 推荐),无 I/O、无 LLM、无时间依赖、无随机。
- **提交节奏**:每 Task 末一次 conventional commit,**所有 commit 的 footer 引用 spec 路径**(QUALITY_BAR 大改规则)。
- **不触 runtime**:本计划完成后,模型/CLI 行为零变化(spec §8 Wave 1/2 的 acceptance 明确 "No runtime behavior changes yet")。

## File Structure

| 文件                                                      | 责任                                                                                                                                                                                                                          | 动作 |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| `src/data_analysis_agent/reporting/__init__.py`           | 包标记(docstring only,不做 re-export,避免 import 顺序抖动)                                                                                                                                                                    | 新建 |
| `src/data_analysis_agent/reporting/model.py`              | Wave1 上游数据类:`SourceKind`/`SourcedValue`/`ExplicitRequirements`/`ImplicitRequirements`/`Uncertainty`/`UserNeed`/`ColumnInfo`/`TableInfo`/`DataContext`/`ProcessStep`/`ProcessContext`/`TraceLink` + `to_dict`/`from_dict` | 新建 |
| `src/data_analysis_agent/reporting/requirement_parser.py` | 确定性:`parse_user_need(raw_request)` → UserNeed;启发式提取显式/隐式需求、uncertainty、clarification_needed                                                                                                                   | 新建 |
| `src/data_analysis_agent/reporting/context_collector.py`  | `build_data_context(profile_dict)` / `build_process_context(event_dicts, sensitive_mode)`;纯 dict 输入                                                                                                                        | 新建 |
| `src/data_analysis_agent/reporting/traceability.py`       | `link_to_contract_fields(...)` / `explain_link` / `index_by_target`;产出 TraceLink 解释契约字段来源                                                                                                                           | 新建 |
| `src/data_analysis_agent/reporting/contract.py`           | Wave2 数据类:`ReportType`/`Audience`/`BlockRole`/`TimeWindow`/`Comparison`/`MetricSpec`/`ReportContract`/`EvidenceRef`/`ProcessRef`/`ChartFields`/`ChartSpec`/`ReportBlock`/`ReportDocument` + `to_dict`/`from_dict`          | 新建 |
| `src/data_analysis_agent/reporting/chart_rules.py`        | `ChartFamily` enum + `select_family(...)` / `check_data_sufficiency(...)` / `suggest_fallback(...)` + 常量 `MIN_TREND_POINTS`/`MIN_SCATTER_POINTS`                                                                            | 新建 |
| `src/data_analysis_agent/reporting/qa.py`                 | `Severity`/`Readiness`/`QAFinding`/`QAReport` + `run_qa(document, *, artifact_exists, ...)`;确定性规则覆盖 spec §7 blocker/high/medium/info                                                                                   | 新建 |
| `scripts/drift_rules.py`                                  | `IMPORT_RULES` 加 `reporting` 条目(禁一切内部包)                                                                                                                                                                              | 改   |
| `docs/ARCHITECTURE.md`                                    | manifest 段加 **7** 行 + 依赖规则一节加 `reporting/*` 行                                                                                                                                                                      | 改   |
| `docs/adr/0009-reporting-pure-domain-layer.md`            | 架构决策记录                                                                                                                                                                                                                  | 新建 |
| `tests/test_reporting_model.py`                           | Wave1 数据类构造 + `to_dict`/`from_dict` 往返 + frozen 不可变                                                                                                                                                                 | 新建 |
| `tests/test_reporting_requirement_parser.py`              | 中文日报/周报/复盘/漏斗/异常 等请求 → 显式/隐式分离 + uncertainty                                                                                                                                                             | 新建 |
| `tests/test_reporting_context.py`                         | data_profile dict → DataContext;事件 dict → ProcessContext;sensitive_mode 归零                                                                                                                                                | 新建 |
| `tests/test_reporting_traceability.py`                    | 需求/上下文 → 契约字段溯源                                                                                                                                                                                                    | 新建 |
| `tests/test_reporting_contract.py`                        | Wave2 数据类 + JSON 往返(enum `.value`)                                                                                                                                                                                       | 新建 |
| `tests/test_reporting_chart_rules.py`                     | 图族选择 + 充分性 + fallback                                                                                                                                                                                                  | 新建 |
| `tests/test_reporting_qa.py`                              | readiness 三态 + blocker/high/medium/info 各规则(含中文因果标记、部分周期)+ 假阳性 fixture                                                                                                                                    | 新建 |
| `tests/test_reporting_acceptance.py`                      | 端到端:UserNeed→DataContext→Contract→Document→QA,验证 draft/needs_review/ready 分类                                                                                                                                           | 新建 |

**回滚策略**:全部改动为新增式(新包 + 新测试 + manifest/drift/ADR/ARCHITECTURE 增量)。无任何现有代码依赖 `reporting`(零 runtime 接线)。回滚 = `git revert` 相关 commit,或删除 `src/data_analysis_agent/reporting/` + 对应 tests + 还原 `ARCHITECTURE.md`/`drift_rules.py` + 删 ADR 0009。无行为副作用。

---

## Task 1: 架构前置 — ADR 0009 + drift 规则 + 包骨架

**Files:**

- New: `docs/adr/0009-reporting-pure-domain-layer.md`
- New: `src/data_analysis_agent/reporting/__init__.py`
- Modify: `scripts/drift_rules.py`(加 reporting 条目)
- Modify: `docs/ARCHITECTURE.md`(manifest 不动;依赖规则一节加 reporting 行)

**Interfaces:**

- Produces: drift 规则 `{who: "data_analysis_agent.reporting", forbid: [<全部内部顶层包,见下>]}` —— 防御性禁一切 `data_analysis_agent.*` 内部包(reporting 仅 stdlib)。
- Produces: `reporting/__init__.py` 内容为模块 docstring(无 re-export)。

- [ ] **Step 1: 写 ADR 0009**

新建 `docs/adr/0009-reporting-pure-domain-layer.md`,格式对齐 ADR 0005(状态/背景/决策/理由/影响)。要点:

- 决策:`reporting` 是纯 stdlib 领域层,是「报告契约可溯源 + 报告就绪度可机器判定」的不变量载体;仅依赖 stdlib;`tools` 未来单向依赖 `reporting`(Wave 3+);drift 规则强制。
- **为何不用 catch-all forbid**(`forbid: ["data_analysis_agent"]`):它会误伤包内相对导入 `from .model import SourceKind`(解析为 `data_analysis_agent.reporting.model`,前缀命中 `data_analysis_agent`),故必须枚举各顶层包而非用全量前缀。未来维护者勿"简化"为 catch-all。
- **`contract.py` 物理拆分说明**:spec §5.1 的逻辑模块 "model" 实际约 700 LOC,为绕开 `FILE_SIZE_LIMIT=600` 告警(`scripts/checks.py::check_file_sizes`,warning 非 error)拆为 `model.py`(Wave 1 上游)+ `contract.py`(Wave 2 契约/文档);`contract.py` 是对 spec §5.1「Likely modules」的物理细化。
- 状态 Accepted (2026-07-06)。

- [ ] **Step 2: 加 drift 规则**

在 `scripts/drift_rules.py::IMPORT_RULES` 末尾(`recovery` 条目后)加(注释说明 catch-all 不可用,见 ADR 0009):

```python
    # reporting 是纯 stdlib 领域层(报告契约/文档/QA);tools 可单向依赖它,
    # 其本身不得反向耦合任何内部包。不能改用 catch-all `forbid:["data_analysis_agent"]`,
    # 否则会误伤包内 `from .model import ...`(解析为 ...reporting.model,命中前缀)。
    # 见 ADR 0009。
    {
        "who": "data_analysis_agent.reporting",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.runtime",
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.session",
            "data_analysis_agent.kernel",
            "data_analysis_agent.context",
            "data_analysis_agent.security",
            "data_analysis_agent.sampling",
            "data_analysis_agent.persistence",
            "data_analysis_agent.state_machine",
            "data_analysis_agent.events",
            "data_analysis_agent.config",
            "data_analysis_agent.recovery",
            "data_analysis_agent.jsonl_store",
            "data_analysis_agent.artifacts",
        ],
    },
```

- [ ] **Step 3: 加包骨架**

新建 `src/data_analysis_agent/reporting/__init__.py`:

```python
"""报告交付领域层(纯 stdlib)。

UserNeed / DataContext / ProcessContext / TraceLink(Wave 1)+ ReportContract /
MetricSpec / EvidenceRef / ChartSpec / ReportDocument / QA(Wave 2)。不依赖任何
运行时模块;见 ADR 0009 与 spec 2026-07-06-report-delivery-optimization-design。
"""
```

- [ ] **Step 4: 更新 ARCHITECTURE.md 依赖规则一节**

在「依赖规则」段(第 23 行附近)末尾加一行:

```
- `reporting/*` ✗→ 任何 `data_analysis_agent.*`(纯 stdlib 领域层,被 tools 单向依赖;见 ADR 0009)
```

(manifest 段此 Task **不动**——尚无非 `__init__.py` 文件。)

- [ ] **Step 5: 跑质量闸**

```bash
.venv/bin/python scripts/quality_gate.py
```

Expected: PASS(`checks.py::_matches` 用 `module_dotted == who or module_dotted.startswith(who + ".")`;Task 1 后唯一文件是 `reporting/__init__.py`,其 dotted 名 `data_analysis_agent.reporting` 命中 who,但该文件仅 docstring、零 import,故零违规)。

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0009-reporting-pure-domain-layer.md \
  src/data_analysis_agent/reporting/__init__.py \
  scripts/drift_rules.py docs/ARCHITECTURE.md
git commit -m "feat(reporting): ADR 0009 + drift rule + package scaffold

Refs spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md"
```

---

## Task 2: Wave 1 — model.py 上游领域模型

**Files:**

- New: `src/data_analysis_agent/reporting/model.py`
- New: `tests/test_reporting_model.py`
- Modify: `docs/ARCHITECTURE.md`(manifest 加 1 行)

**Interfaces:**

- `SourceKind(str, Enum)`:`explicit_user | implicit_user | data_context | process_context | memory | template`
- `SourcedValue(frozen)`: `value: str | None`, `source: SourceKind`, `rationale: str = ""`
- `ExplicitRequirements(frozen)`:见 spec §4.1 字段(business_question/requested_outputs/named_metrics/named_dimensions/time_window/audience/language/format_constraints/must_include/must_avoid),tuple 容器
- `ImplicitRequirements(frozen)`:见 spec §4.1(likely_report_type/business_scenario/narrative_style/section_expectations/visual_expectations/decision_or_update_goal/cadence)
- `Uncertainty(frozen)`: `topic`, `why`, `needs_clarification: bool = False`
- `UserNeed(frozen)`: `raw_request: str` + explicit/implicit + `uncertainties: tuple[Uncertainty,...] = ()` + `clarification_needed: bool = False` + `to_dict()`/`from_dict()`
- `ColumnInfo(frozen)`: `name`, `dtype: str | None`, `candidate_role: str | None`
- `TableInfo(frozen)`: `name`, `path`, `columns: tuple[ColumnInfo,...]`, `n_rows`, `n_rows_sampled`, `sampled: bool`
- `DataContext(frozen)`:见 spec §4.2(tables/candidate_date_columns/available_date_range/candidate_metric_columns/candidate_dimensions/business_grain/missingness_risks/duplicate_key_risks/join_candidates/data_gaps)+ JSON
- `ProcessStep(frozen)`: `step_id`, `tool`, `summary`, `assumptions`, `failed: bool`, `recovery`, `evidence_ids`, `artifact_ids`
- `ProcessContext(frozen)`: `steps`, `rejected_paths`, `user_corrections`, `sensitive_mode: bool = False` + JSON
- `TraceLink(frozen)`: `target: str`, `source: SourceKind`, `source_ref: str`, `rationale: str = ""`
- **序列化约定**:`to_dict(obj) -> dict`(Enum 字段输出 `.value`;tuple → list 仅为 JSON 表示,`from_dict` 重建时转回 tuple);`from_dict(data)` 递归重建嵌套 frozen dataclass 与 Enum。**往返契约**:`from_dict(to_dict(x)) == x` 对所有上述 dataclass 成立(测试断言)。

- [ ] **Step 1: Write failing tests**

`tests/test_reporting_model.py`,每个 dataclass 至少一个用例:

```python
from dataclasses import replace, FrozenInstanceError
import pytest
from data_analysis_agent.reporting.model import (
    UserNeed, ExplicitRequirements, ImplicitRequirements, DataContext,
    ProcessContext, TraceLink, SourceKind, TableInfo, ColumnInfo,
)

def test_user_need_roundtrip():
    un = UserNeed(raw_request="上周销售日报",
                  explicit_requirements=ExplicitRequirements(),
                  implicit_requirements=ImplicitRequirements(likely_report_type="daily_kpi"),
                  uncertainties=())
    assert UserNeed.from_dict(un.to_dict()) == un   # enum + 嵌套递归往返

def test_data_context_roundtrip():
    dc = DataContext(tables=(TableInfo(name="sales.csv", columns=(ColumnInfo("date","datetime"),), n_rows=100),))
    assert DataContext.from_dict(dc.to_dict()) == dc

def test_frozen_immutability():
    un = UserNeed(raw_request="x", explicit_requirements=ExplicitRequirements(),
                  implicit_requirements=ImplicitRequirements())
    with pytest.raises(FrozenInstanceError):
        un.raw_request = "y"  # type: ignore[misc]
    assert replace(un, raw_request="z").raw_request == "z"

def test_trace_link_enum_roundtrip():
    tl = TraceLink(target="report_type", source=SourceKind.IMPLICIT_USER, source_ref="日报")
    assert TraceLink.from_dict(tl.to_dict()) == tl
    assert tl.to_dict()["source"] == "implicit_user"   # enum → .value
```

- [ ] **Step 2: Run → FAIL**(`ModuleNotFoundError: data_analysis_agent.reporting.model`)

- [ ] **Step 3: Implement model.py** —— 按 Interfaces 实现。`from __future__ import annotations`。枚举 `class SourceKind(str, Enum)`。所有容器用 `tuple`。模块级 `to_dict` dispatch + 每个需要的 dataclass 类方法 `from_dict`;Enum 经 `.value` 序列化、`SourceKind(value)` 反序列化。

- [ ] **Step 4: manifest 加行**(`docs/ARCHITECTURE.md` manifest 段):

```
src/data_analysis_agent/reporting/model.py = "报告领域层(Wave1):UserNeed/DataContext/ProcessContext/TraceLink + 显式/隐式需求分离(纯 stdlib,ADR 0009)"
```

- [ ] **Step 5: Run → PASS**(pytest + ruff + mypy + drift 全绿)

- [ ] **Step 6: Commit**

```bash
git add src/data_analysis_agent/reporting/model.py tests/test_reporting_model.py docs/ARCHITECTURE.md
git commit -m "feat(reporting): Wave 1 upstream domain model (UserNeed/DataContext/ProcessContext/TraceLink)

Refs spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md"
```

---

## Task 3: Wave 1 — requirement_parser.py 显式/隐式需求分离

**Files:**

- New: `src/data_analysis_agent/reporting/requirement_parser.py`
- New: `tests/test_reporting_requirement_parser.py`
- Modify: `docs/ARCHITECTURE.md`(manifest 加 1 行)

**Interfaces:**

- `parse_user_need(raw_request: str) -> UserNeed`
- 模块级启发式(私有):`_detect_report_type` / `_detect_cadence` / `_detect_audience` / `_detect_language` / `_detect_outputs` / `_collect_uncertainties`
- 行为契约:
  - "日报" → `implicit.likely_report_type=daily_kpi`、`cadence=daily`、section_expectations 含 top-line/next-actions
  - "周报" → `weekly_kpi`、`weekly`
  - "复盘/诊断" → `diagnostic`
  - "漏斗" → `funnel`;"同期群/留存" → `cohort`;"异常/风险" → `risk_anomaly`;"数据质量" → `data_quality`
  - "给领导看/给老板/汇报" → `audience=business_stakeholder`、`narrative_style=answer_first`
  - language:中文请求 → `zh-CN`
  - 显式:`requested_outputs` 从 "html/报告/H5/echarts" 推断 `html_report`
  - uncertainty:report_type 模糊 / time_window 缺 / comparison 缺 → 记 Uncertainty;`clarification_needed=True` 仅当 report_type 不可决且影响 cadence(保守,避免过度打断)。

- [ ] **Step 1: Write failing tests** —— 中文 fixtures:日报、周报、复盘、漏斗、异常检测、给领导看、模糊请求(断言 `clarification_needed`)。断言推断字段进 `ImplicitRequirements`(隐式),显式可 lexical 判定的进 `ExplicitRequirements`(显式)。

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** —— 纯 `re` + 关键词表 + CJK 2-gram shingle(无需共享 tokenizer)。

- [ ] **Step 4: manifest 加行**

```
src/data_analysis_agent/reporting/requirement_parser.py = "报告领域层(Wave1):确定性需求解析(raw_request → UserNeed,显式/隐式分离,CJK 启发式,无 LLM)"
```

- [ ] **Step 5: Run → PASS**

- [ ] **Step 6: Commit** `feat(reporting): deterministic requirement parser (explicit vs implicit, CJK heuristics)` + footer `Refs spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`

---

## Task 4: Wave 1 — context_collector.py

**Files:**

- New: `src/data_analysis_agent/reporting/context_collector.py`
- New: `tests/test_reporting_context.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**

- `build_data_context(profile: Mapping[str, object]) -> DataContext` —— 消费 `data_profile` 工具输出(`{kind, path, format, tables:[{name/sheet, columns:[{name, dtype}], n_rows_sampled, sampled}]}`)。启发式:`candidate_date_columns`(列名含 date/日期/时间/day/time/week/month)、`candidate_metric_columns`(dtype 含 int/float/数值/number)、`candidate_dimensions`(object/str/category)、`available_date_range`(留 None,由分析填)、`business_grain`(列名含 user/order/sku 等启发)。
- `build_process_context(events: Iterable[Mapping[str, object]], *, sensitive_mode: bool = False) -> ProcessContext` —— 输入摘要事件 dict(`{step_id, tool, summary, assumptions?, failed?, recovery?, evidence_ids?, artifact_ids?}`)。`sensitive_mode=True` → 返回 `ProcessContext(sensitive_mode=True)` 且 steps 为空(spec §4.3 隐私降级)。

- [ ] **Step 1: Write failing tests** —— 用 Explore 测绘得到的 data_profile 输出 shape 构造 fixture dict;断言 date/metric/dimension 候选正确归类;sensitive_mode 归零断言。

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** —— 纯 dict → frozen dataclass,无内部依赖。

- [ ] **Step 4: manifest 加行**

```
src/data_analysis_agent/reporting/context_collector.py = "报告领域层(Wave1):data_profile→DataContext、工具事件→ProcessContext(纯 dict 输入,sensitive_mode 隐私降级)"
```

- [ ] **Step 5: Run → PASS**

- [ ] **Step 6: Commit** `feat(reporting): context collector (data_profile → DataContext, events → ProcessContext)` + footer `Refs spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`

---

## Task 5: Wave 1 — traceability.py

**Files:**

- New: `src/data_analysis_agent/reporting/traceability.py`
- New: `tests/test_reporting_traceability.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**

- `link_to_contract_fields(user_need: UserNeed, data_context: DataContext, process_context: ProcessContext) -> tuple[TraceLink, ...]` —— 为未来契约字段(report_type/time_window/comparison/metrics/dimensions/data_sources 等)各产一条 TraceLink,`source_kind`/`source_ref` 指向 explicit requirement / implicit inference / data column / process step。无依据的字段 → 不产 link(让其后续在 QA 阶段被 flag)。
- `explain_link(link: TraceLink) -> str` —— 中文人读解释,如「字段 comparison.basis 来自隐式推断(用户未明示基线,按惯例取上期)」。
- `index_by_target(links: Iterable[TraceLink]) -> dict[str, tuple[TraceLink, ...]]`

- [ ] **Step 1: Write failing tests** —— 给定日报 UserNeed + 含日期列 DataContext,断言 `time_window` 字段有 data_context 来源 link、`report_type` 有 implicit_user 来源 link、无依据字段(如 comparison)无 link。

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement**

- [ ] **Step 4: manifest 加行**

```
src/data_analysis_agent/reporting/traceability.py = "报告领域层(Wave1):契约字段溯源映射(需求/数据/过程 → TraceLink,中读解释)"
```

- [ ] **Step 5: Run → PASS**

- [ ] **Step 6: Commit** `feat(reporting): traceability mapping (contract field → need/context/process source)` + footer `Refs spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`

---

## Task 6: Wave 2 — contract.py 契约/文档领域模型

**Files:**

- New: `src/data_analysis_agent/reporting/contract.py`
- New: `tests/test_reporting_contract.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**

- 枚举(均 `class X(str, Enum)`):`ReportType`/`Audience`/`BlockRole`
- frozen dataclass:`TimeWindow`/`Comparison`/`MetricSpec`/`ReportContract`/`EvidenceRef`/`ProcessRef`/`ChartFields`/`ChartSpec`/`ReportBlock`/`ReportDocument`
- `MetricSpec.source: SourceKind = SourceKind.IMPLICIT_USER`、`confirmed: bool = False`(复用 `model.SourceKind`)
- `ReportContract` 默认 `audience=Audience.BUSINESS_STAKEHOLDER`、`language="auto"`、`required_outputs=("html_report",)`(spec §4.4)
- `ReportContract.field_sources: tuple[tuple[str, SourceKind], ...] = ()` —— **每字段来源标注**(spec §4.4 "Contract fields that come from inference should preserve their source");Wave 1-2 仅承载,Wave 3 的 `report_contract` 工具负责填充,Wave 1-2 的 QA 暂只用 `MetricSpec.source` 做指标级判断(通用 inferred-as-explicit 规则 defer 到 Wave 3,见 Task 8)。
- `ChartSpec.interpretation: str | None`(QA 检「相邻解读」用)、`data_sufficient: bool = True`、`fallback_family: str | None`
- **`ReportBlock.kpi_cards: tuple[tuple[tuple[str, str], ...], ...]` = ()** —— 每张卡是 `(key, value)` 对的 tuple;**全不可变、可哈希、JSON 可往返**(评审 #1 冻结:不用 `Mapping[str,str]`,避免 frozen 内嵌可变 dict 的浅不可变漏洞)。
- `ReportDocument.contract: ReportContract | None`、`generated_at: str | None`(注入,不调 `datetime.now()`)、`data_scope: str | None`
- **序列化约定**:同 Task 2(Enum `.value`、tuple 往返、嵌套递归)。`from_dict(to_dict(x)) == x`。

- [ ] **Step 1: Write failing tests** —— 各 dataclass 构造 + 默认 + JSON 往返(含 `kpi_cards` 嵌套 tuple 往返、Enum 往返);`ReportContract` 默认值断言。

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** —— `from __future__ import annotations`;`from .model import SourceKind`(包内导入,drift 允许:`.model` 解析为 `data_analysis_agent.reporting.model`,不在任何 forbid 前缀中——forbid 均为其他顶层包)。`contract` 模块同样被 `reporting` who 规则覆盖(`startswith` 前缀匹配),但其内部 import 全是 stdlib 或 `.model`/`.contract` 自身,合法。

- [ ] **Step 4: manifest 加行**

```
src/data_analysis_agent/reporting/contract.py = "报告领域层(Wave2):ReportContract/MetricSpec/EvidenceRef/ChartSpec/ReportDocument 契约与文档模型(纯 stdlib,ADR 0009)"
```

- [ ] **Step 5: Run → PASS**

- [ ] **Step 6: Commit** `feat(reporting): Wave 2 contract & document domain model` + footer `Refs spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`

---

## Task 7: Wave 2 — chart_rules.py 图族选择 + 数据充分性

**Files:**

- New: `src/data_analysis_agent/reporting/chart_rules.py`
- New: `tests/test_reporting_chart_rules.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**

- `ChartFamily(str, Enum)`:`kpi_card | line | bar | grouped_bar | stacked_bar | dot | scatter | heatmap | waterfall | funnel | table`(与 spec §4.7 图族一致)
- 常量:`MIN_TREND_POINTS = 3`、`MIN_SCATTER_POINTS = 10`(可调)
- `select_family(*, n_points: int | None, n_categories: int | None, is_time_series: bool, comparison_basis: str | None, single_value: bool = False, ordered_stages: bool = False) -> ChartFamily` —— 确定性图族建议:`single_value` → `kpi_card`;`is_time_series and n_points >= MIN_TREND_POINTS` → `line`;`is_time_series and n_points < MIN_TREND_POINTS` → `grouped_bar`(fallback);`ordered_stages` → `funnel`;多系列比较 → `grouped_bar`;相关 → `scatter`;稀疏/兜底 → `table`
- `check_data_sufficiency(family: ChartFamily | str, *, n_points: int | None = None, n_observations: int | None = None) -> tuple[bool, str | None]` —— `(sufficient, reason)`;`line` & n_points<MIN_TREND_POINTS → `(False, "trend_needs_more_points")`;`scatter` & n_observations<MIN_SCATTER_POINTS → `(False, "scatter_needs_more_observations")`;其它 → `(True, None)`
- `suggest_fallback(family: ChartFamily | str, *, n_points, n_observations) -> ChartFamily | None` —— 不充分时给 fallback(line→grouped_bar 或 kpi_card;scatter→table);充分 → None

- [ ] **Step 1: Write failing tests** —— 覆盖 select_family 各分支 + 充分性边界(MIN_TREND_POINTS-1 / MIN / MIN+1)+ fallback;断言返回类型为 `ChartFamily`。

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement**

- [ ] **Step 4: manifest 加行**

```
src/data_analysis_agent/reporting/chart_rules.py = "报告领域层(Wave2):图族选择 + 数据充分性 + fallback(MIN_TREND/MIN_SCATTER,确定性)"
```

- [ ] **Step 5: Run → PASS**

- [ ] **Step 6: Commit** `feat(reporting): chart family selection + data sufficiency rules` + footer `Refs spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`

---

## Task 8: Wave 2 — qa.py 确定性报告 QA

**Files:**

- New: `src/data_analysis_agent/reporting/qa.py`
- New: `tests/test_reporting_qa.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**

- `Severity(str, Enum)`: `blocker | high | medium | info`
- `Readiness(str, Enum)`: `draft | needs_review | ready`
- `QAFinding(frozen)`: `severity`, `code`, `message`, `block_id: str | None`, `suggested_fix: str | None`
- `QAReport(frozen)`: `readiness`, `findings: tuple[QAFinding,...]`, `artifact_exists: bool`
- `run_qa(document: ReportDocument, *, artifact_exists: bool = False, n_points_by_chart: Mapping[str, int] | None = None, n_observations_by_chart: Mapping[str, int] | None = None) -> QAReport`

**`run_qa` 实现契约(评审 #2)**:`run_qa` 是唯一入口,故 spec §7 「draft: QA not run」状态**结构上不可能**(`run_qa` 必被调用)。当 `document.contract is None` 时,`contract.no_traceability` blocker 触发,**所有读取 contract 字段的下游规则必须短路**(各 `_check_*` 自检 `document.contract is not None`,或 `run_qa` 在 contract 为 None 时跳过 contract 依赖检查),不得抛 AttributeError。

**规则**(确定性,映射 spec §7;实现为 `_check_*` 私有函数,各返回 `list[QAFinding]`):

Blockers(spec §7 全覆盖):

- `contract.no_traceability` — `document.contract is None` 或 contract 四类 ref(explicit/implicit/data/process)全空
- `executive_summary.missing` — `audience==business_stakeholder` 且无 `EXECUTIVE_SUMMARY` block
- `direct_answer.missing` — 第一个非 header block 不是 executive_summary/finding 或无 body(尽力判)
- `finding.no_evidence` — FINDING block body 含数字/百分比但 `evidence_refs` 空
- `data_scope.missing` — `document.data_scope` 空
- `artifact.missing` — `artifact_exists=False`
- `chart_block.no_spec` — `CHART` block `chart is None`

High(spec §7 8/9;#1 通用项 defer 见下):

- `metric.inferred_drives_recommendation` — 某 MetricSpec `source != explicit_user and not confirmed` 且被 RECOMMENDATION block 引用(通过 metric.name 匹配 body/refs,**尽力,docstring 注明假阴性容忍**)
- `section.no_mapping` — FINDING/CHART block 的 user_need_refs/evidence_refs/process_refs 全空
- `chart.no_interpretation` — CHART block `chart.interpretation` 空
- `metric.ambiguous_no_def` — MetricSpec `numerator is None and denominator is None and aggregation is None`
- `trend.too_few_points` — line family 且 `n_points < MIN_TREND_POINTS`
- `scatter.too_few_observations` — scatter family 且 `n_observations < MIN_SCATTER_POINTS`
- `recommendation.no_evidence` — RECOMMENDATION block evidence_refs 与 process_refs 全空
- `causal.no_caveat` — FINDING body 含因果标记(`导致|引起|因为|由于|造成|caused by|drives|driven by`)且无相邻 CAVEAT(同 block caveats 空 + 无紧跟 CAVEAT block)
- `partial_period.undisclosed` — `contract.time_window.partial_period==True` 且无 CAVEAT block 含 `部分|partial`

Medium(spec §7 3/5;#2 表项 defer 见下):

- `heading.generic` — FINDING heading **精确等于**通用词集之一(`分析|详情|finding|result|数据|内容`,**非子串包含**——避免误判"关键指标分析"等正当标题)
- `chart.long_labels` — ChartSpec 字段 label 文本 > 20 字符
- `chart.repeated_family_no_rationale` — 同 family 的 chart > 3 且均无 `analytical_question`
- `caveat.not_adjacent` — 末尾 CAVEAT block 且未指向具体 block_id(尽力)

Info(spec §7 1/3;#3 离线 ECharts defer 见下):

- `source_metadata.missing` — 无 SOURCE_METADATA block
- `print_styling.unchecked` — 静态 info(总发,提示人工检查)

**Deferred 规则(评审 #4,显式豁免 + 理由,不许默默跳过)**:

- **General inferred-as-explicit(spec §7 High #1)** → defer 到 **Wave 3**。理由:通用判定需 `ReportContract.field_sources` 已被 `report_contract` 工具正确填充、并与 `UserNeed` 的 explicit/implicit 标签交叉校验;Wave 1-2 只有数据模型,无契约构建器。Wave 1-2 已:(a) 在 `ReportContract` 加 `field_sources` 字段为未来铺路;(b) 实现指标级 `metric.inferred_drives_recommendation` 作为该规则的首个子集。
- **Tables vs visual comparison(spec §7 Medium #2)** → defer 到 **Wave 5**。理由:判定"表明显劣于可视化"需数据 shape(数值列数/行数/比较结构)信息,纯 ReportDocument 模型不持有。Wave 5 结构化 `chart_render` 接入后可判。
- **Offline ECharts not configured(spec §7 Info #3)** → defer 到 **Wave 4**。理由:这是渲染器配置层 concern(`AgentConfig.echarts_src`),纯 ReportDocument 无法知悉;静态"总发 info"是无意义的噪声,故不在 Wave 1-2 发。Wave 4 HTML v2 可读取渲染器配置后发。

**Readiness 分类(spec §7,确定性)**:

- `draft` — 任一 blocker **或** `not artifact_exists`
- `needs_review` — 无 blocker,有 high
- `ready` — 无 blocker/high **且** artifact_exists

- [ ] **Step 1: Write failing tests** —— 为每条规则造 pass/fail fixture(含中文因果、部分周期);readiness 三态各一个端到端断言。**假阳性 fixture(评审 #9)**:`causal.no_caveat` 对非因果语境的"因为"(如"用户因为 X 才问 Y")**不触发**;`heading.generic` 对"关键指标分析"/"销售详情"等正当标题**不触发**(精确匹配)。`contract is None` 时 `run_qa` 不抛异常、返回 readiness=draft + `contract.no_traceability`。

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** —— 纯函数,无 I/O。因果/通用词用模块级 `frozenset[str]`。`run_qa` 聚合所有 `_check_*`;contract 依赖的 `_check_*` 自带 `if document.contract is None: return []` 短路。

- [ ] **Step 4: manifest 加行**

```
src/data_analysis_agent/reporting/qa.py = "报告领域层(Wave2):确定性 QA(readiness 三态 + blocker/high/medium/info 规则,无 LLM,ADR 0009)"
```

- [ ] **Step 5: Run → PASS**

- [ ] **Step 6: Commit** `feat(reporting): deterministic report QA (readiness + blocker/high/medium/info findings)` + footer `Refs spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`

---

## Task 9: 端到端 acceptance + 最终质量闸 + 独立代码审查闭环

**Files:**

- New: `tests/test_reporting_acceptance.py`
- Modify: `docs/ARCHITECTURE.md`(manifest 段核对 7 行齐全——Task 2-8 已逐个加)

**Acceptance(对应 spec §8 Wave 1/2):**

- 显式/推断需求分开表示,推断项标 source。
- DataContext 承载 files/sheets/schema/候选指标/日期范围/数据缺口。
- ProcessContext 承载 tool 序列/假设/失败路径/派生/artifact id,不依赖原始 chat。
- TraceLink 解释契约字段来源。
- QA 分类 draft/needs_review/ready,阻断断链契约,确定性无 LLM。

**End-to-end 场景(`test_reporting_acceptance.py`):**

1. `parse_user_need("给我看看上周销售日报,要能给领导看")` → UserNeed(report_type=daily_kpi, audience=business_stakeholder, cadence=daily)。
2. `build_data_context(profile_dict)` → DataContext(含候选日期/指标列)。
3. `link_to_contract_fields(...)` → 验证 report_type/time_window 有来源 link,comparison 无 link(待 QA flag)。
4. 构造 ReportContract + 一个**缺 evidence 的 finding** ReportDocument → `run_qa(..., artifact_exists=True)` → readiness=needs_review,findings 含 `finding.no_evidence` (high)。
5. 同文档 `artifact_exists=False` → readiness=draft。
6. 补全 evidence + 解读 + 数据范围 + executive summary + artifact → readiness=ready。

- [ ] **Step 1: Write the end-to-end test**

- [ ] **Step 2: Run → PASS**(实现已在 Task 2-8 完成,此测试是集成验收)

- [ ] **Step 3: 最终质量闸**

```bash
.venv/bin/python scripts/quality_gate.py
```

Expected: 全绿(ruff/format/mypy strict/pytest/drift)。drift 段确认 manifest **7** 行齐全、reporting drift 规则生效(可选验证:临时在 model.py 顶部加 `import data_analysis_agent.tools` 跑 drift 应捕 `reporting ✗→ tools`,验证后撤回)。

- [ ] **Step 4: 独立代码审查闭环(CLAUDE.md §2.9,强制)**

spawn **全新**只读 code-reviewer 子 Agent(仅给 spec/plan 摘要 + 文件路径,**不带编码上下文**),产出问题清单(严重级/位置/修复建议)。主 Agent 逐项修复 → **再 spawn 全新**子 Agent 复审(不续用上轮上下文)→ 循环至零遗留。审查重点:drift 规则完整性、manifest 同步、frozen/不可变、JSON 往返(enum `.value`)、`run_qa` 对 `contract is None` 短路、QA 规则假阳性/假阴性、确定性(无时间/随机/网络)、与 spec §7 规则对齐、中文因果/部分周期检测覆盖、deferred 规则是否在代码/注释中留痕。

- [ ] **Step 5: 最终 Commit + 报告**

```bash
git add tests/test_reporting_acceptance.py
git commit -m "test(reporting): end-to-end acceptance (Wave 1-2) + readiness tri-state

Closes Wave 1-2 of spec docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md"
```

产出最终报告:落地范围、覆盖的 spec acceptance、quality_gate 证据、独立审查结果、Wave 3+ 后续路线。

---

## Verification Matrix(对应 spec §9 子集)

| 区域                 | 自动检查                                                                 | 本计划覆盖 |
| -------------------- | ------------------------------------------------------------------------ | ---------- |
| User Need model      | `test_reporting_requirement_parser.py`(显式/隐式 fixture)                | ✅         |
| Data/Process context | `test_reporting_context.py` + sensitive_mode                             | ✅         |
| Traceability         | `test_reporting_traceability.py`                                         | ✅         |
| Report model         | `test_reporting_contract.py` 序列化(enum `.value`、kpi_cards 嵌套 tuple) | ✅         |
| QA                   | `test_reporting_qa.py`(三态 + 各规则 + 假阳性 + contract-None 短路)      | ✅         |
| 端到端               | `test_reporting_acceptance.py`                                           | ✅         |
| 质量门               | `.venv/bin/python scripts/quality_gate.py`                               | ✅ 每 Task |

## Self-Review(spec → Task 映射 + 已知 defer + 风险)

**Spec acceptance 映射:**

- spec §4.1(User Need)+ §8 Wave 1 acceptance ①② → Task 2(model)+ Task 3(parser)
- spec §4.2(DataContext)+ Wave 1 ③ → Task 2 + Task 4
- spec §4.3(ProcessContext)+ Wave 1 ④⑤ → Task 2 + Task 4(sensitive_mode)
- spec §4.4(ReportContract)+ §8 Wave 2 → Task 6
- spec §4.5(MetricSpec)+ §4.6(Evidence)+ §4.7(ChartSpec)+ §4.8(ReportDocument) → Task 6 + Task 7
- spec §7(QA 规则)+ Wave 2 readiness → Task 8
- spec §8 Wave 1 ⑥(TraceLink 解释契约字段) → Task 5
- spec §13(Wave 1 deliberately small, no runtime) → 全计划零 runtime 接线 ✅

**已 defer 的 QA 规则(spec §7 未全覆盖):**

| spec §7 项                        | 状态                      | 理由                                                            | 解锁 Wave |
| --------------------------------- | ------------------------- | --------------------------------------------------------------- | --------- |
| High #1 通用 inferred-as-explicit | defer(仅指标级子集已实现) | 需 `report_contract` 工具填 `field_sources` + 交叉校验 UserNeed | Wave 3    |
| Medium #2 表 vs 可视化            | defer                     | 需数据 shape,纯文档模型不持有                                   | Wave 5    |
| Info #3 离线 ECharts              | defer                     | 渲染器配置层 concern                                            | Wave 4    |

**已知启发式假阳性风险(测试已 pin):**

- `causal.no_caveat`:"因为"等词在非因果语境会误判 → 假阳性 fixture 锁定不触发。
- `heading.generic`:通用词作为子串会误判正当标题 → 改**精确匹配**,fixture 锁定"关键指标分析"不触发。
- `metric.inferred_drives_recommendation`:metric.name 与推荐 body 的匹配不精确 → docstring 注明假阴性容忍,Wave 3 契约构建器接入后强化。

**评审修订记录(本轮独立 plan review,APPROVE-WITH-FIXES,11 条全采纳):**

1. `kpi_cards` 类型冻结为 `tuple[tuple[tuple[str,str],...],...]`(去 "实现时定")。
2. `run_qa` 对 `contract is None` 显式短路,下游 `_check_*` 自检。
3. drift forbid 补 6 个遗漏包(state_machine/events/config/recovery/jsonl_store/artifacts)。
4. 三条缺失 QA 规则显式 defer + 理由(见上表)+ `field_sources` 字段铺路。
5. manifest 计数 8 → 7(两处)。
6. Task 2-9 commit 全加 `Refs spec ...` footer。
7. 新增本 Self-Review 段。
8. 钉死 enum 序列化约定(`.value` 往返,Task 2 Global + 测试)。
9. Task 8 加 `causal`/`heading` 假阳性 fixture。
10. 定义 `ChartFamily(str, Enum)`,`select_family` 返回它。
11. ADR 0009 说明 catch-all forbid 不可用 + `contract.py` 物理拆分理由。

## 不在本计划内(Wave 3+ 后续独立变更)

- Wave 3:`report_need`/`report_context`/`report_contract` 工具注册 + `ReportGenerationSkill` 升级(触及 runtime/工具注册表/skills);通用 inferred-as-explicit QA 规则在此 Wave 解锁。
- Wave 4:`html_report` v2 Report Document schema(触及现有渲染器,保留 v1);离线 ECharts Info 规则在此 Wave 解锁。
- Wave 5:结构化 `chart_render` 工具 + 图族渲染;表 vs 可视化 Medium 规则在此 Wave 解锁。
- Wave 6:报告模板(daily/weekly/diagnostic/data-quality/funnel/cohort/risk/recommendation)。
- Wave 7:报告行为 eval gate(扩展 evaluator + ≥20 eval 任务)。
- Wave 8:Web Workbench 报告 UX(依赖未来 web 包,当前被阻塞)。
