# 报告交付优化 · Wave 3 实现计划 — 报告需求/上下文/契约工具 + 技能升级

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 Task 逐项实现。

> **Baseline:** `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`(§5.2 Tools、§5.3 Skills、§8 Wave 3)
> **Depends on:** Wave 1-2 已落地(`reporting/` 领域层,commit `93d487a`)
> **Scope:** Wave 3。把 Wave 1-2 的领域层通过 3 个只读工具暴露给模型,并升级 `ReportGenerationSkill` 强制 contract-before-render。触及 runtime(工具注册表 + 技能),但**不改** `html_report`/`agent_loop` 核心逻辑。

## Goal

让"需求解析 → 上下文采集 → 契约归一化"对模型可见、对 harness 可测(spec §8 Wave 3 acceptance):

- 一个报告请求能在渲染前产出 Report Contract。
- Contract 字段能回指用户需求/数据/过程来源(填 `field_sources` + 各 ref)。
- 关键缺失上下文以 `missing_context` 或简短澄清形式出现。
- 现有非报告分析**不被强制**走报告契约。

并解锁 Wave 1-2 defer 的通用 inferred-as-explicit QA 规则的入口(`report_contract` 填 `field_sources`)。

## Architecture

3 个**只读**工具(在 `tools/`),薄封装 Wave 1-2 `reporting` 函数:

| 工具              | 输入                                                                                                                                | 调用                                                             | 输出                                                     |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------- |
| `report_need`     | `raw_request: str`                                                                                                                  | `requirement_parser.parse_user_need`                             | UserNeed dict + 摘要                                     |
| `report_context`  | `profile: object`、`events?: array`、`sensitive_mode?: bool`                                                                        | `context_collector.build_data_context` + `build_process_context` | DataContext + ProcessContext dict + 摘要                 |
| `report_contract` | `question: str`、`user_need?: object`、`data_context?: object`、`process_context?: object`、`report_type?/audience?/language?` 覆盖 | `traceability.link_to_contract_fields` + 构造 `ReportContract`   | ReportContract dict(含 `field_sources` + 四类 ref)+ 摘要 |

**依赖方向**:`tools` → `reporting`(drift 允许:`tools` 的 forbid 列表是 `agent_loop/evolution/telemetry/memory/runtime`,不含 `reporting`;`reporting` 禁 `tools` 是反向单向)。无需改 drift 规则。

`ReportGenerationSkill` 升级:instructions 改为 contract-before-render 五步;keywords 加日报/周报/复盘/漏斗/同期群/异常/数据质量/KPI 等;`allowed_tools` 加 `data_profile/report_need/report_context/report_contract`。

## Tech Stack

Python 3.10+(工具用 `Any`/`Mapping`,无新依赖),复用 Wave 1-2 `reporting` 包。pytest、ruff、mypy strict。

## Global Constraints

- **质量闸**:每 Task 末 `.venv/bin/python scripts/quality_gate.py` 全绿。
- **manifest 同步**:3 个新工具文件各加一行 `docs/ARCHITECTURE.md` manifest。
- **drift**:`tools` → `reporting` 已合法(不改正文);实现后跑 drift 确认无回归。
- **向后兼容**:不改 `html_report.py`/`agent_loop.py`/`visualization.py`/其他 4 个内置技能。仅 `ReportGenerationSkill` 与 `runtime.build_registry`/`READ_ONLY_TOOLS`/`tools/__init__.py` 增量。
- **只读**:3 个工具 `is_read_only→True`、`is_destructive→False`、`is_concurrency_safe→True`,加入 `READ_ONLY_TOOLS`(default/plan 模式自动放行)。
- **确定性**:无 I/O、无 LLM、无时间/随机。工具是 `reporting` 纯函数的薄封装。
- **提交**:每 Task 末 conventional commit + footer 引用 spec(Wave 3 收尾统一 commit,见 Task 7)。

## File Structure

| 文件                                               | 责任                                                                               | 动作 |
| -------------------------------------------------- | ---------------------------------------------------------------------------------- | ---- |
| `src/data_analysis_agent/tools/report_need.py`     | `ReportNeedTool`:raw_request → UserNeed                                            | 新建 |
| `src/data_analysis_agent/tools/report_context.py`  | `ReportContextTool`:profile/events → DataContext + ProcessContext                  | 新建 |
| `src/data_analysis_agent/tools/report_contract.py` | `ReportContractTool`:need+context → ReportContract(field_sources+refs)             | 新建 |
| `src/data_analysis_agent/tools/__init__.py`        | 导出 3 个新工具类                                                                  | 改   |
| `src/data_analysis_agent/runtime.py`               | `build_registry` 注册 3 工具 + `READ_ONLY_TOOLS` 加 3 名                           | 改   |
| `src/data_analysis_agent/skills/builtin.py`        | `ReportGenerationSkill`:instructions/keywords/allowed_tools                        | 改   |
| `docs/ARCHITECTURE.md`                             | manifest 加 3 行                                                                   | 改   |
| `tests/test_report_tools.py`                       | 3 工具 call + read_only + 注册 + plan 模式可用                                     | 新建 |
| `tests/test_report_skill.py`                       | 路由关键词 + allowed_tools + instructions 契约 + 端到端(contract→QA 不被断链 flag) | 新建 |

**回滚**:全增量(3 新工具 + 2 新测试 + manifest/runtime/skill 增量)。`git revert` 即可;无现有代码依赖新工具。

---

## Task 1: ReportNeedTool

**Files:** New `tools/report_need.py`; New `tests/test_report_tools.py`(本 Task 起步); Modify manifest。

**Interfaces:**

- `ReportNeedTool(Tool)`:`name="report_need"`、`is_read_only/is_concurrency_safe→True`、`is_destructive→False`。
- `input_schema`:`{"type":"object","properties":{"raw_request":{"type":"string"}},"required":["raw_request"]}`。
- `call(input_data)`:取 `raw_request`(str),调 `parse_user_need`;返回 `ToolResult(content=<摘要>, metadata={"user_need": user_need.to_dict()})`。
- 摘要文本:显式需求要点、推断报告类型/cadence、uncertainty 列表、`clarification_needed` 标志。
- `validate_input`:`raw_request` 必须是非空 str。

- [ ] Step 1: 写失败测试 `tests/test_report_tools.py`:`test_report_need_parses`(call → metadata.user_need.implicit_requirements.likely_report_type == "daily_kpi" 对 "上周销售日报")、`test_report_need_read_only`、`test_report_need_validates_empty`。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `report_need.py`。
- [ ] Step 4: manifest 加行 + 跑 gate → PASS。

## Task 2: ReportContextTool

**Files:** New `tools/report_context.py`; 追加测试; manifest。

**Interfaces:**

- `input_schema`:`{"properties":{"profile":{"type":"object"},"events":{"type":"array"},"sensitive_mode":{"type":"boolean"}},"required":["profile"]}`。
- `call`:调 `build_data_context(profile)` + `build_process_context(events or [], sensitive_mode=sensitive_mode)`;返回 `metadata={"data_context":...,"process_context":...}` + 摘要(候选列/业务粒度/sensitive 标志)。
- `validate_input`:`profile` 必须是 dict。

- [ ] Step 1: 写失败测试:用 data_profile fixture dict → 候选日期/指标列归类;sensitive_mode=True → process_context.steps 为空。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现。
- [ ] Step 4: manifest + gate → PASS。

## Task 3: ReportContractTool

**Files:** New `tools/report_contract.py`; 追加测试; manifest。

**Interfaces:**

- `input_schema`:`question`(required)、`user_need?/data_context?/process_context?`(object)、`report_type?/audience?/language?`(string 覆盖)。
- `call`:
  1. 若提供 `user_need` → `UserNeed.from_dict`;否则用 `parse_user_need(question)`。
  2. 若提供 `data_context` → `DataContext.from_dict`;否则 `DataContext()`。
  3. 若提供 `process_context` → `ProcessContext.from_dict`;否则 `ProcessContext()`。
  4. `report_type`:覆盖 > `user_need.implicit.likely_report_type`(string 经 `ReportType(...)`,无效值 fallback `AD_HOC`)> `AD_HOC`。
  5. `link_to_contract_fields(user_need, data_context, process_context)` → `field_sources = tuple((lk.target, lk.source) for lk in links)`。**注**:`TraceLink.source_ref`/`rationale` 在 `field_sources: tuple[tuple[str, SourceKind], ...]` schema 边界处丢失(Wave 1-2 schema cap,非本 Wave bug),未来扩展时升级 schema。
  6. **四类 ref 确定性桶式映射**(评审 #1,用 traceability 已产出的数据,非字段存在性猜测):遍历 `links`,按 `link.source` 分桶,值为 `link.source_ref`——
     - `EXPLICIT_USER` → `explicit_requirement_refs`
     - `IMPLICIT_USER` → `implicit_requirement_refs`
     - `DATA_CONTEXT` → `data_context_refs`
     - `PROCESS_CONTEXT` → `process_context_refs`
     - `MEMORY`/`TEMPLATE` → 不入 ref(Wave 1-2 无对应来源)
  7. **`missing_context` 填充**(评审 #2,spec §8 #3):把 `user_need.uncertainties` 的 `topic` 与 `data_context.data_gaps` 合并去重为 `missing_context: tuple[str, ...]`。
  8. 构造 `ReportContract`:question/report_type/audience/language;`data_sources` 从 data_context.tables 路径;上述四类 ref + `field_sources` + `missing_context`。
  9. 返回 `metadata={"contract": contract.to_dict()}` + 摘要。
- `validate_input`:`question` 必须非空 str。

- [ ] Step 1: 写失败测试:`test_report_contract_traceability`(question + user_need + data_context → contract.field_sources 非空、四类 ref 中至少一类非空 → 该 contract 经 `run_qa` 不触发 `contract.no_traceability`)、`test_report_contract_report_type_override`、`test_report_contract_missing_context`(对 "销售日报" 这类无时间/对比词的请求,`contract.missing_context` 非空,含 `time_window`/`comparison` topic)、`test_report_contract_validates_empty`。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现(从 `reporting` import `UserNeed/DataContext/ProcessContext/ReportContract/ReportType/Audience` + `parse_user_need` + `link_to_contract_fields`;从 `.base` import Tool 基类)。
- [ ] Step 4: manifest + gate → PASS。

## Task 4: 注册工具 + READ_ONLY_TOOLS + 导出

**Files:** Modify `tools/__init__.py`(导出 3 类); Modify `runtime.py`(`build_registry` 注册 3 工具 + `READ_ONLY_TOOLS` 加 3 名); 追加测试。

- [ ] Step 1: 写失败测试:`test_report_tools_registered`(`build_registry()` 后 `get_tools("default")` 含 3 个);`test_report_tools_in_read_only_set`(`READ_ONLY_TOOLS` 含 3 名 → plan 模式 `assemble_tool_pool("plan")` 保留它们)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 改 `tools/__init__.py` + `runtime.py`。
- [ ] Step 4: gate → PASS(drift 应仍绿:tools→reporting 合法)。

## Task 5: ReportGenerationSkill 升级

**Files:** Modify `skills/builtin.py::ReportGenerationSkill`; New `tests/test_report_skill.py`。

**变更:**

- `instructions`:contract-before-render 五步(先 report_need → data_profile+report_context → report_contract → python_analysis 计算 → html_report ONCE);**显式区分用户明示需求与推断需求,并把二者映射注入 report_contract**(评审 #4,spec §5.3)。
- `keywords`:加 `日报/周报/月报/复盘/漏斗/同期群/留存/异常/风险/数据质量/KPI/daily/weekly/funnel/cohort/risk/anomaly/data quality/diagnostic/复盘`。
- `allowed_tools`:加 `data_profile, report_need, report_context, report_contract`。

- [ ] Step 1: 写失败测试:`test_report_skill_keywords_route`(日报/周报/复盘/漏斗/异常等命中 ReportGenerationSkill);`test_report_skill_routing_isolation`(评审 #3,spec §8 #4:`match_best("描述性统计/数据概览")` → DescriptiveAnalysisSkill、`match_best("趋势/时间序列")` → TrendAnalysisSkill,**不是** ReportGenerationSkill);`test_report_skill_allowed_tools`(含 3 新工具 + data_profile + html_report);`test_report_skill_instructions_contract_first`(instructions 含 "report_contract" 在 "html_report" 之前,且含明示/推断区分措辞)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 改 builtin.py。
- [ ] Step 4: gate → PASS。

## Task 6: contract→QA 集成 + 最终闸 + 独立审查

**Files:** 追加 `tests/test_report_skill.py` 集成用例。

- [ ] Step 1: 集成测试(评审 #5):`report_need("上周销售日报,给领导看")` → `report_context(profile)` → `report_contract(question, user_need, data_context)` → 得到 contract;断言 `run_qa(ReportDocument(contract=contract, ...), artifact_exists=False)` 的 findings 中**无 code == `contract.no_traceability` 的项**(收紧断言:仅检断链,不要求 QA 全绿——`data_scope.missing`/`artifact.missing` 等其他 blocker 仍会触发,属正常)。
- [ ] Step 2: 最终 `quality_gate` 全绿。
- [ ] Step 3: 独立代码审查闭环(§2.9):spawn 全新只读 reviewer(给 spec/plan + 文件路径),重点审 工具只读性/确定性/drift tools→reporting 合法性/contract 工具的 field_sources 与 refs 正确性/skill 路由不破坏既有技能/向后兼容。修复 → 复审至零遗留。
- [ ] Step 4: Commit。

## Verification

- `tests/test_report_tools.py` + `tests/test_report_skill.py` 全绿;既有测试不回归。
- `quality_gate.py` 五关全绿(ruff/format/mypy/pytest/drift)。
- drift 段确认 `tools/report_*.py` → `reporting` 不违规、manifest 3 行齐全。

## 不在本计划内(Wave 4+)

- Wave 4:`html_report` v2 Report Document schema。
- Wave 5:结构化 `chart_render` 工具。
- Wave 6:报告模板;Wave 7:eval gate;Wave 8:Web UX。
