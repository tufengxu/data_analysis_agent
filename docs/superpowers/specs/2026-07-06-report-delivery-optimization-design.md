# Report Delivery Optimization Design

> Status: design baseline, 2026-07-06
>
> Scope: refine the optimization plan for DataAnalysisAgent's analysis-report
> delivery quality, especially visualization-rich HTML reports for business
> review, daily reports, weekly reports, and reusable team updates.
>
> Related audit: `docs/roadmap/2026-07-06-analysis-report-quality-audit.md`
>
> Related roadmap: `docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md`

## 0. Executive Decision

Report delivery must become a first-class subsystem. The immediate goal is not
to make the current HTML prettier; it is to ensure the agent can reliably produce
a report that has the right business structure, evidence, chart choices, metric
context, caveats, and next actions before the renderer turns it into HTML.

Main contradiction:

- Current strength: DataAnalysisAgent already has a harness-first runtime,
  persistent Python execution, data profiling, artifact delivery, memory,
  telemetry, and an H5 renderer.
- Current weakness: report intent, report schema, chart semantics, report QA, and
  behavior evals are not first-class contracts.

Decision:

- Build around User Need, Data/Process Context, Report Contract, and Report
  Document models. The Report Contract is not the first step; it is the normalized
  result of user-need parsing plus available data and execution-process context.
- Keep `html_report` as the safe rendering baseline, but evolve it to v2 schema
  support rather than replacing it immediately.
- Replace code-string-first report visualization with structured chart specs for
  the default path.
- Add deterministic report QA before a report can be labeled ready.
- Reuse the existing week-1 seed assets as the first report-quality eval source.

Rejected paths:

- Do not start with CSS polish or dashboard decoration.
- Do not depend only on prompt wording to create report discipline.
- Do not use an LLM judge as the only report-quality gate.
- Do not make the Web layer a thin CLI stream wrapper; the Web layer must expose
  user intent, data/process context, report intent, draft plan, QA status,
  artifact preview, and report feedback.

Trellis status:

- `trellis status -p <repo>` returned "Not a TrellisVCS repository".
- No Trellis state was initialized or mutated.

## 1. Acceptance Contract

```json
{
  "intent": "Define a concrete optimization plan that turns DataAnalysisAgent report delivery from renderer-led output into contract-driven, QA-gated, business-ready HTML report delivery.",
  "non_goals": [
    "No production code implementation in this document.",
    "No rewrite of AgentLoop or runtime composition.",
    "No distributed/multi-user report service in Phase 1.",
    "No claim that current reports are production-ready."
  ],
  "acceptance": [
    "The design names the target report pipeline from user intent to HTML artifact.",
    "The design decomposes work into implementable waves with files/modules likely touched.",
    "The design defines user-need parsing, data/process context collection, report contract, chart contract, report document, QA, templates, Web integration, and eval requirements.",
    "The design states verification commands and release gates.",
    "Future planned paths are described without pretending they exist today."
  ],
  "forbidden": [
    "Do not solve report quality by only changing prompts.",
    "Do not create a Report Contract that is disconnected from the user's explicit/implicit needs or the observed analysis process.",
    "Do not let a report pass readiness when key claims lack evidence or charts lack interpretation.",
    "Do not make user-visible Web reports bypass the same runtime/tool registry as CLI.",
    "Do not let candidate report skills auto-promote without eval evidence and human review."
  ],
  "verify_commands": [
    ".venv/bin/python scripts/quality_gate.py",
    ".venv/bin/pytest tests/ -v",
    "future optional behavior gate: scripts/eval_gate.py report"
  ],
  "release_gate": "A report-delivery release candidate must pass code quality gate plus report-focused behavior evals and manual HTML report inspection."
}
```

## 2. Target User Outcome

A user should be able to upload or authorize local Excel/CSV files, describe a
business question in natural language, watch the analysis process, and receive an
HTML report that can be forwarded or used in a meeting with minimal editing.

The system should also preserve enough context to explain why the report took
its final shape:

- what the user explicitly asked for
- what the agent inferred from language, selected files, report type, and prior
  feedback
- what data existed and what data was missing
- what analysis steps were actually executed
- which intermediate results, failed attempts, assumptions, and discarded paths
  affected the final claims

For daily/weekly reporting, the output must feel like it was written by someone
who understands business review habits:

- clear period and data scope
- top-line status first
- KPI movement against prior period or target
- drivers and segment cuts
- charts that support specific claims
- caveats where interpretation could change
- practical next actions
- concise language suitable for personal/team reports

The product promise is not "the agent made a file"; it is "the agent produced a
usable report".

## 3. Target Pipeline

Report-mode runs should follow this pipeline:

```text
User request + selected files
        ↓
User Need Parse
        ↓
Data Context Collection
        ↓
Historical + Current Process Context
        ↓
Report Contract
        ↓
Report Plan / analysis spine / narrative spine
        ↓
analysis execution + process trace capture
        ↓
Evidence & Process Map
        ↓
Metric specs + chart specs + table specs
        ↓
Report Document
        ↓
Report QA
        ↓
HTML renderer
        ↓
artifact manifest + Web preview + feedback
        ↓
trajectory / eval harvesting
```

Hard rule:

- The Report Contract must carry traceability back to user requirements and
  collected context. If a report section exists only because the model "felt like
  it", the contract is weak.
- A report can be rendered as draft before QA passes, but it must not be labeled
  ready if blocker or high-severity QA findings remain.

## 4. Data Model Baseline

The new report subsystem should introduce a pure domain layer that depends on
stdlib and local low-level helpers only. It should not depend on `agent_loop`,
`protocol`, or `evolution`.

### 4.1 User Need Model

Purpose:

- Parse the user's real reporting need before turning it into a formal contract.
- Separate explicit requirements from inferred requirements so the system does
  not pretend guesses are facts.

Required concepts:

```json
{
  "raw_request": "用户原始请求",
  "explicit_requirements": {
    "business_question": "",
    "requested_outputs": [],
    "named_metrics": [],
    "named_dimensions": [],
    "time_window": null,
    "audience": null,
    "language": null,
    "format_constraints": [],
    "must_include": [],
    "must_avoid": []
  },
  "implicit_requirements": {
    "likely_report_type": "",
    "business_scenario": "",
    "narrative_style": "",
    "section_expectations": [],
    "visual_expectations": [],
    "decision_or_update_goal": "",
    "cadence": null
  },
  "uncertainties": [],
  "clarification_needed": false
}
```

Examples:

- "日报" implies period awareness, concise top-line summary, status movement,
  and next actions.
- "复盘" implies driver analysis, caveats, what changed, what to do next, and
  evidence-backed recommendations.
- "给领导看" implies business-stakeholder language, answer-first structure, and
  low process noise.
- "适合汇报" implies chart readability, segment headings, and no exploratory
  notebook leftovers.

Policy:

- Inferred requirements must be marked as inferred.
- High-impact ambiguity should produce a concise clarification question or a
  visible assumption in the contract.
- User refinements and feedback are intent evidence. They should update the User
  Need Model before a rerun or revised report.

### 4.2 Data Context

Purpose:

- Collect what the report can legitimately know about the data before analysis
  and before final claims.

Required concepts:

- authorized paths and selected files
- workbook sheets and table names
- column names and dtypes
- row counts or sampled row counts
- candidate date columns and available date range
- candidate metric columns
- candidate dimensions and business grain
- missingness, duplicate/key risks, type risks, outliers when known
- join candidates and join risks for multiple files/sheets
- data gaps relative to the user need

Policy:

- Data Context begins with `data_profile`, then grows with analysis results.
- It should not store volatile business conclusions as durable memory by default.
- It should preserve enough source metadata for report audit and Web inspection.

### 4.3 Process Context

Purpose:

- Preserve the analysis process that led to the final report. The execution
  trajectory is evidence about analytical reasoning, not just debug logging.

Required concepts:

- tool sequence
- tool inputs at a safe/summarized level
- derived datasets or intermediate tables
- assumptions made during analysis
- failed tool calls and recovery steps
- rejected hypotheses or abandoned chart/report paths
- user approvals, denials, corrections, and rephrases
- evidence ids created by each step
- artifact ids created by each step

Important boundary:

- Process Context is not mind reading. It reflects observable signals: user
  wording, selected data, tool choices, intermediate computations, and feedback.
  It can support inferred intent, but it must not claim to know hidden user
  psychology.

Policy:

- Web can expose a friendly "analysis process" view, while raw traces remain
  internal or downloadable for audit.
- Process Context should help explain why a chart, section, caveat, or
  recommendation appears in the report.
- Sensitive-mode runs must be able to reduce or disable process-context
  persistence.

### 4.4 Report Contract

Purpose:

- Normalize the user need, data context, and process context into a report job
  contract before expensive analysis and rendering happen.

Required concepts:

```json
{
  "question": "用户原始分析问题",
  "report_type": "daily_kpi | weekly_kpi | diagnostic | recommendation | data_quality | funnel | cohort | risk_anomaly | ad_hoc",
  "audience": "business_stakeholder | technical",
  "language": "zh-CN | en-US | auto",
  "data_sources": [],
  "authorized_scope": [],
  "time_window": {
    "start": null,
    "end": null,
    "grain": null,
    "timezone": null,
    "partial_period": false
  },
  "comparison": {
    "basis": "previous_period | target | plan | peer | historical_range | unavailable",
    "description": ""
  },
  "metrics": [],
  "dimensions": [],
  "business_grain": "",
  "explicit_requirement_refs": [],
  "implicit_requirement_refs": [],
  "data_context_refs": [],
  "process_context_refs": [],
  "required_outputs": ["html_report"],
  "known_constraints": [],
  "missing_context": []
}
```

Defaults:

- Chinese user request defaults to Chinese report copy and chart labels.
- Missing audience defaults to business stakeholder.
- Missing time window can be inferred from date columns, but the report must say
  what was inferred.
- Missing comparison defaults to previous comparable period when possible;
  otherwise the report must say no baseline is available.
- Missing metric definitions are acceptable only for low-risk obvious columns;
  ambiguous business metrics require confirmation, a visible assumption, or a QA
  warning.
- Contract fields that come from inference should preserve their source:
  explicit user requirement, implicit user-need inference, data context, process
  context, memory, or template.

### 4.5 Metric Spec

Purpose:

- Prevent口径 drift and make metrics reusable by memory and templates.

Required concepts:

- metric name
- source columns
- numerator
- denominator
- aggregation
- filters and exclusions
- time window
- grain
- timezone
- unit
- confirmation status
- source: user confirmed, inferred from data, inferred from memory, or template

Policy:

- Confirmed metric definitions may be used without interruption, but still need
  to be visible when they affect interpretation.
- Unconfirmed inferred metrics can be used for a draft, but report QA must flag
  them when they drive recommendations.
- Conflicting metric definitions must produce a conflict state, not silent
  overwrite.

### 4.6 Evidence & Process Map

Purpose:

- Tie each claim to computed evidence and to the analysis process that produced
  it, so the report does not become a polished hallucination.

Required concepts:

- evidence id
- source tool call id where available
- source data path or table/sheet
- transformation summary
- process step id
- assumption ids
- rejected alternative ids where relevant
- computed fields
- row count or sample count
- limitations
- artifact path when evidence is a chart/table

Policy:

- Every quantitative finding must cite at least one evidence id internally.
- Every major recommendation should cite either evidence ids or process ids that
  explain why the recommendation is supported.
- The visible report does not need to show raw ids, but source metadata must be
  preserved for audit and Web inspection.

### 4.7 Chart Spec

Purpose:

- Make charts evidence objects, not decorative output.

Required concepts:

- analytical question
- supported claim
- user need refs
- evidence refs
- process refs when chart choice follows an analysis decision
- chart family: KPI card, line, bar, grouped bar, stacked bar, dot/lollipop,
  scatter, heatmap, waterfall, funnel, table
- fields: x, y, color/series, label, size, time, denominator
- grain and filters
- time window and comparison baseline
- units and formatting
- data sufficiency rule
- fallback chart if data is sparse
- title/subtitle/caption policy
- accessibility notes: labels, color, legend, mobile width

Policy:

- Trend charts need enough points. If not, use grouped bars, KPI cards, a table,
  or narrative comparison.
- Scatter charts need enough meaningful observations and consistent grain.
- A chart without adjacent interpretation cannot pass report QA.
- Tables are valid evidence, but a table should not replace an obvious visual
  comparison unless exact lookup is the point.

### 4.8 Report Document

Purpose:

- Represent the report before rendering.

Required roles:

- header
- executive summary
- optional KPI strip
- data and metric context
- finding sections
- chart blocks
- table blocks
- recommendations
- caveats and further questions
- source metadata
- traceability metadata: which user need, data context, evidence, and process
  refs support each major block
- QA status

Important distinction:

- Section role is not the same as visible heading. A finding can have a
  story-specific heading while still satisfying the "key finding" role.

## 5. Proposed Module Boundaries

Planned paths below are future implementation targets, not current files.

### 5.1 Pure report domain package

Planned package:

- src/data_analysis_agent/reporting/

Likely modules:

- model: dataclasses for UserNeed, DataContext, ProcessContext, ReportContract,
  MetricSpec, EvidenceRef, ProcessRef, ChartSpec, ReportDocument, ReportBlock,
  QAFinding.
- requirement_parser: deterministic helpers and model-facing schemas for
  explicit/implicit report requirements.
- context_collector: structures for data profiles, current-run process context,
  and historical trajectory summaries.
- templates: curated report archetype definitions and section-role defaults.
- planner: deterministic helpers for inferring report type, missing fields, and
  report spine from User Need plus data/process context.
- traceability: maps user needs, data context, process context, evidence, and
  report blocks.
- qa: deterministic report quality checks.
- chart_rules: chart family selection and data sufficiency checks.
- render_adapter: translation from ReportDocument to the existing HTML renderer
  input during the migration period.

Dependency rule:

- reporting may depend on stdlib and low-level shared utilities.
- tools may depend on reporting.
- runtime may register tools that wrap reporting.
- reporting must not depend on agent_loop, protocol, runtime, or evolution.

### 5.2 Tools

Planned tools:

- report_need
  - parses explicit and implicit report requirements
  - returns User Need with uncertainty and clarification flags
  - read-only
  - no file writes
  - used before report_contract

- report_context
  - summarizes Data Context and Process Context from profiles, tool events, and
    existing run metadata
  - read-only
  - no file writes
  - used before and after analysis execution

- report_contract
  - validates or canonicalizes the Report Contract from User Need plus
    data/process context
  - read-only
  - no file writes
  - used before heavy report analysis

- report_qa
  - validates a Report Document or renderer input
  - read-only
  - returns blocker/high/medium/info findings
  - used before final handoff

- chart_render
  - accepts structured Chart Spec plus reviewed data
  - writes chart artifact directly to artifact dir
  - returns artifact paths and chart metadata
  - replaces generic visualization as the default report-chart path

- html_report
  - remains the existing tool name
  - adds v2 Report Document support
  - preserves current v1 sections input for compatibility

Migration rule:

- Existing `html_report` callers keep working.
- New report-mode skills should prefer Report Document input.
- `visualization` remains available for exploratory and custom chart code, but
  report skills should not default to it.

### 5.3 Skills and Prompting

Current file to evolve:

- `src/data_analysis_agent/skills/builtin.py`
- `src/data_analysis_agent/config.py`

Changes:

- Update `ReportGenerationSkill` so it explicitly requires: contract, profile,
  data/process context, evidence, chart specs, report document, QA, then render.
- Require report-mode skills to distinguish explicit user requirements from
  inferred requirements and to carry the mapping into the contract.
- Add report-type routing terms for daily report, weekly report, KPI readout,
  business复盘, diagnostic memo, funnel, cohort, anomaly/risk, and data-quality
  profile.
- Add declarative report templates as data, not hard-coded branches inside the
  agent loop.
- Keep the system prompt short; put detailed behavior in skills/templates so it
  can be evaluated and evolved.

### 5.4 Web Workbench Integration

The Web layer should consume report state, not merely display final HTML.

Required report UX:

- User need panel: explicit requirements, inferred requirements, open
  uncertainties, and assumptions.
- Report intent panel: report type, audience, period, baseline, language.
- Data scope panel: selected files/sheets, authorized dirs, row/column summary.
- Process context panel: high-level tool timeline, derived tables, assumptions,
  rejected paths, and artifacts.
- Live report plan: pending/confirmed contract fields and section spine.
- Evidence & process map: each major claim connected to tool output, derived
  data, process step, or artifact.
- QA panel: blocker/high/medium warnings with fix suggestions.
- Artifact preview: open generated HTML inside a safe artifact route.
- Feedback tags: wrong口径, weak chart, missing caveat, unsupported conclusion,
  wrong business framing, too long, too thin, ready to share.

### 5.5 Evaluation Integration

Current evaluator:

- `src/data_analysis_agent/evolution/evaluator.py`

Current seed assets:

- `examples/training_data/week1_seed_assets/`
- `examples/eval_tasks/`

Required changes:

- Add report-focused eval tasks from week-1 assets.
- Add assertions for artifact presence and report structure.
- Add assertions that Contract fields map back to explicit/implicit user
  requirements where possible.
- Add assertions that final claims have Evidence & Process Map links.
- Add a report-quality failure taxonomy.
- Add optional human/LLM rubric review, but keep deterministic checks as the
  release gate.

## 6. Report Templates

Templates should be curated first, then later refined by trajectories and human
review. They should define roles and checks, not fixed prose.

### 6.1 Daily KPI Report

Required roles:

- period-aware title
- executive summary
- KPI strip
- movement drivers
- anomalies or risks
- next actions
- caveats for partial periods, missing data, or changed metric definitions

Default charts:

- KPI cards for headline metrics
- line chart only when enough temporal points exist
- grouped bar for latest versus previous comparable period
- ranked horizontal bar for segment contributors

### 6.2 Weekly Business Review

Required roles:

- weekly summary
- wins and concerns
- metric movement versus previous week or target
- segment/driver analysis
- follow-up actions
- open questions

Default charts:

- KPI cards
- week-over-week grouped bars
- driver ranking bars
- issue/risk table for follow-up

### 6.3 Diagnostic Memo

Required roles:

- what changed
- verified drivers
- rejected or unverified explanations
- segment evidence
- decision implication
- next investigation

Default charts:

- before/after comparison
- ranked driver bars
- segment matrix
- tables only for supporting cases

### 6.4 Data Quality Profile

Required roles:

- suitability judgment for the requested analysis
- data scope
- missingness
- duplicates/key uniqueness
- type/date parseability
- outliers
- join risks
- cleanup actions

Default charts:

- missingness bar
- anomaly concentration bar
- table of blocking data-quality issues

### 6.5 Funnel/Cohort Report

Required roles:

- definition of stage or cohort
- denominators
- conversion/drop-off or retention
- segment comparison
- bottleneck
- action recommendation

Default charts:

- ordered stage bar or funnel
- cohort heatmap
- line or indexed trend when enough points exist

### 6.6 Risk/Anomaly Report

Required roles:

- detection rule
- flagged population
- severity and concentration
- false-positive caveat
- operational follow-up

Default charts:

- ranked concentration bar
- scatter for outliers when enough observations exist
- case table for exact follow-up

## 7. Report QA Rules

Report QA should return structured findings:

- severity: blocker, high, medium, info
- code
- message
- affected block id
- suggested fix
- readiness impact

Blockers:

- Report Contract has no traceability to user need and data/process context
- no executive summary for business stakeholder report
- no direct answer in the opening section
- quantitative finding has no evidence reference
- report has no data scope
- rendered report artifact missing
- report contains chart block without chart artifact/spec

High severity:

- inferred user requirement is treated as explicit fact
- major report section does not map to a user need, evidence item, or process step
- chart lacks adjacent interpretation
- metric is ambiguous and lacks definition
- trend chart has too few points
- scatter chart has too few observations
- recommendation lacks evidence
- causal claim lacks caveat
- partial period is not disclosed

Medium severity:

- generic section headings where insight headings are possible
- tables used where visual comparison is clearly better
- long labels likely to overflow
- too many repeated chart families without rationale
- caveats grouped at the end when they should qualify a specific finding

Info:

- optional source metadata missing from visible report
- print/export styling not checked
- offline ECharts not configured

Readiness labels:

- draft: QA not run or blocker findings exist
- needs_review: no blockers, but high findings exist
- ready: no blocker or high findings, and artifact exists

## 8. Implementation Waves

### Wave 0. Planning Freeze

Purpose:

- Treat this document as the design baseline before production changes.

Deliverables:

- This spec.
- A future implementation plan under docs/superpowers/plans/.

Acceptance:

- Quality gate passes.
- The implementation plan lists exact touched files for Wave 1.

### Wave 1. User Need, Data Context, Process Context, and Traceability

Purpose:

- Create the upstream understanding layer that feeds the Report Contract.

Likely touched areas:

- new reporting package
- tool registration later, but not required in the first commit
- tests for User Need, Data Context, Process Context, and traceability mapping

Tasks:

- Define dataclasses and JSON conversion helpers for UserNeed, DataContext,
  ProcessContext, and TraceLink.
- Add deterministic helpers for explicit versus implicit requirement extraction.
- Add structures for data-profile-derived context and process-summary context.
- Add traceability helpers that map requirements and context into contract fields.
- Add fixtures for Chinese daily report, weekly review, diagnostic memo, and
  business复盘 requests.

Acceptance:

- Explicit and inferred requirements are represented separately.
- The model can record uncertainties and clarification-needed state.
- Data Context can represent selected files/sheets, schema, candidate metrics,
  date range, and data gaps.
- Process Context can represent tool sequence, assumptions, failed paths,
  derived tables, and artifact ids without depending on raw chat logs.
- Traceability links can explain why a contract field exists.
- No runtime behavior changes yet.

Verification:

- `.venv/bin/pytest tests/ -v`
- `.venv/bin/python scripts/quality_gate.py`

### Wave 2. Report Domain Model and QA Skeleton

Purpose:

- Add Report Contract, Metric Spec, Evidence & Process Map, Chart Spec, Report
  Document, and deterministic QA on top of the upstream understanding layer.

Likely touched areas:

- reporting package
- tests for report model serialization and QA rules

Tasks:

- Define dataclasses and JSON conversion helpers.
- Define readiness states.
- Implement minimal QA rules for executive summary, data scope, evidence links,
  process traceability, chart interpretation, metric definitions, and
  recommendations.
- Add fixture Report Documents for pass/fail cases.

Acceptance:

- QA can classify draft, needs_review, and ready.
- QA can block a report whose Contract is disconnected from user need or
  data/process context.
- QA findings are deterministic and do not require an LLM.
- No runtime behavior changes yet.

Verification:

- `.venv/bin/python scripts/quality_gate.py`

### Wave 3. Report Need/Context/Contract Tools and Skill Integration

Purpose:

- Make need parsing, context summarization, and contract creation visible to the
  model and testable by the harness.

Likely touched areas:

- tools package
- runtime registry
- built-in report skill
- prompt contract tests

Tasks:

- Add report_need and report_context tools, or one staged report_prepare tool if
  the smaller interface proves easier to operate.
- Add a report_contract tool that validates/canonicalizes report contract input
  from User Need plus Data/Process Context.
- Add report-type defaulting rules.
- Update `ReportGenerationSkill` instructions to require contract before final
  report rendering.
- Update report-mode instructions to preserve explicit versus inferred
  requirements.
- Add tests that report-mode routing keeps required tools available.

Acceptance:

- A report request can produce a contract before rendering.
- Contract fields can point back to user requirements and context refs.
- Missing critical context is surfaced as missing_context or a concise question.
- Existing non-report analyses are not forced through report contract.

### Wave 4. HTML Report v2 Schema

Purpose:

- Let `html_report` render a richer Report Document while preserving v1 inputs.

Likely touched areas:

- `src/data_analysis_agent/tools/html_report.py`
- tests for HTML report
- possibly CSS template internals

Tasks:

- Add v2 input branch for Report Document.
- Render header, Executive Summary, KPI strip, finding blocks, chart/table
  blocks, recommendations, caveats, and QA status.
- Preserve block-level traceability metadata for Web inspection.
- Preserve existing escaping and path containment.
- Add print-friendly CSS and optional offline ECharts guidance.

Acceptance:

- v1 tests keep passing.
- v2 fixture renders expected sections and escapes text.
- A report with blocker QA can render as draft but is visibly marked draft.

### Wave 5. Structured Chart Spec and Renderer

Purpose:

- Stop using generic chart code as the default report chart path.

Likely touched areas:

- new chart rendering tool
- report chart rules
- artifact tests

Tasks:

- Add chart spec validation.
- Render common chart families needed by report templates.
- Save artifacts directly under artifact dir.
- Add data sufficiency checks for trend/scatter.
- Return chart metadata for Evidence & Process Map and Report Document.

Acceptance:

- Structured chart requests can generate artifacts without free-form Python code.
- Report QA can inspect chart metadata.
- Custom `visualization` remains available but is not the default report path.

### Wave 6. Report Templates

Purpose:

- Make common business report shapes repeatable.

Likely touched areas:

- reporting templates
- built-in or declarative skills
- tests for template selection

Tasks:

- Add curated templates for daily KPI, weekly KPI, diagnostic memo, data quality,
  funnel/cohort, risk/anomaly, and recommendation reports.
- Add domain overlays for retail, marketing, SaaS, support, finance, operations,
  risk, and supply chain.
- Template output should be a section-role spine and requirement expectations,
  not final prose.

Acceptance:

- Similar report requests map to consistent report types.
- Template selection can be tested without LLM calls.
- Template roles can be traced back to inferred or explicit user needs.
- Report skills use templates but can still adapt to user-specific requirements.

### Wave 7. Report-Focused Eval Gate

Purpose:

- Prevent code quality from masking report-quality regressions.

Likely touched areas:

- examples/eval_tasks
- evolution evaluator or a new optional eval gate script
- seed asset conversion helper

Tasks:

- Convert representative week-1 seed tasks into report eval tasks.
- Add report assertions: artifact exists, executive summary exists, data scope
  exists, metric context exists when needed, chart interpretation exists, caveat
  exists, next action exists.
- Add traceability assertions: contract maps to user need, findings map to
  evidence/process refs, and inferred requirements are marked as inferred.
- Track report-quality failure taxonomy.
- Keep the eval gate optional until runtime cost and determinism are controlled.

Acceptance:

- At least 20 report-focused eval tasks exist across multiple domains.
- Eval output separates code/tool failures from report-quality failures.
- Release notes can state behavior-eval evidence, not only pytest status.

### Wave 8. Web Workbench Report UX

Purpose:

- Make the report process interactive and reviewable.

Likely touched areas:

- future web package
- event codec
- run manifest
- feedback telemetry

Tasks:

- Add user-need and report-intent form fields.
- Show explicit/inferred requirements, open uncertainties, and assumptions.
- Show data context and high-level process context.
- Show report contract and section plan before final render.
- Show evidence/process map for final report claims.
- Show QA panel with readiness state.
- Open HTML artifacts through safe artifact route.
- Capture report-specific feedback tags and comments.

Acceptance:

- User can see why a report is draft, needs_review, or ready.
- User can correct report intent, implicit assumptions, or metric口径 before
  rerun.
- Feedback can feed telemetry and future skill/eval harvesting.

## 9. Verification Matrix

| Area | Automated checks | Manual checks |
| --- | --- | --- |
| User Need model | explicit/inferred parsing fixture tests | inspect Chinese report requests for hidden assumptions |
| Data/process context | context serialization tests, sensitive-mode fixture tests | inspect process summary for usefulness and privacy |
| Traceability | contract-field-to-source mapping tests | verify every major contract field has an explainable source |
| Report model | serialization tests, QA fixture tests | inspect model readability |
| Report contract | tool validation tests, skill routing tests | ask report request with missing period/baseline and inspect assumptions |
| Evidence & Process Map | claim/evidence/process linkage tests | inspect unsupported claims and rejected explanations |
| HTML v2 | render tests, XSS/path tests, legacy compatibility tests | open generated HTML on desktop/mobile width |
| Chart spec | chart validation tests, artifact tests | inspect chart labels, units, readability |
| Templates | deterministic template-selection tests | compare daily/weekly/diagnostic output shape |
| Eval gate | report eval tasks, failure taxonomy output | review failed sample reports |
| Web UX | event codec tests, fake-client Web smoke | upload CSV/Excel, inspect need/context/QA flow, then revise intent |

Baseline command:

- `.venv/bin/python scripts/quality_gate.py`

Future behavior command:

- scripts/eval_gate.py report

## 10. Release Criteria

Do not claim "production-grade report delivery" until:

- User Need, Data Context, Process Context, and traceability models exist and
  feed the Report Contract.
- Explicit user requirements, inferred requirements, data facts, process facts,
  memory facts, and template defaults are distinguishable in the contract.
- Report Contract exists and is used by report-mode skills.
- Report Document or equivalent v2 schema exists.
- HTML v2 renders at least executive summary, KPI strip, findings, charts/tables,
  caveats, recommendations, and QA status.
- Report QA can block or warn on missing evidence, missing口径, weak charts, and
  unsupported recommendations.
- Report QA can block disconnected contracts, unsupported inferred
  requirements, and major sections without user-need/evidence/process links.
- Structured chart rendering covers the common report families.
- At least 20 report evals pass across multiple domains.
- Web Workbench can expose user need, data/process context, report contract,
  evidence/process map, artifacts, QA, and feedback.
- Quality gate and report behavior gate pass on the release candidate.

## 11. Risks and Mitigations

Risk: the report model becomes too abstract and slows implementation.

- Mitigation: start with the minimum roles needed for daily/weekly/KPI and
  diagnostic reports; add specialized roles only when evals demand them.

Risk: process traces are mistaken for hidden user intent.

- Mitigation: Process Context can only support inferred requirements from
  observable signals. It must never relabel inferred intent as explicit user
  intent, and QA should flag high-impact inferred requirements without
  confirmation or visible assumptions.

Risk: context capture stores too much sensitive or irrelevant process detail.

- Mitigation: store summarized process context by default, preserve raw traces
  only behind explicit audit/debug modes, and provide sensitive-mode controls to
  reduce or disable process-context persistence.

Risk: the pre-contract layer becomes a second planning project.

- Mitigation: Wave 1 only models User Need, Data Context, Process Context, and
  trace links. Runtime tool exposure waits until these structures pass fixture
  tests.

Risk: report QA becomes annoying and blocks useful drafts.

- Mitigation: separate draft rendering from ready labeling. Block readiness, not
  exploration.

Risk: structured chart rendering under-delivers compared with custom Python.

- Mitigation: keep `visualization` as an advanced fallback, but require explicit
  approval and QA for report use.

Risk: templates make reports feel rigid.

- Mitigation: templates define section roles and chart defaults, not final prose.
  The model can still adapt headings and wording to the user's question.

Risk: LLM behavior remains inconsistent despite tools.

- Mitigation: make report artifacts, QA output, and eval assertions deterministic
  enough to catch regressions without relying only on model self-discipline.

Risk: Web MVP grows too large.

- Mitigation: the Web MVP only needs user-need summary, report intent,
  data/process context summary, event stream, artifact preview, and QA panel.
  Rich editing can wait.

## 12. Open Questions Before Coding

These can be decided during Wave 1 implementation planning:

- Should report model JSON use snake_case everywhere, matching Python and tool
  inputs, or expose a user-facing localized layer?
- How much process context should be stored by default: summary only, raw trace
  with retention controls, or raw trace only when audit mode is enabled?
- Should historical trajectories influence the current Report Contract by
  default, or only when memory is explicitly enabled for the run?
- Which inferred requirements require user confirmation before a report can be
  labeled ready?
- Should KPI cards be rendered by `html_report` directly or compiled from
  Report Document into normal sections for the first migration?
- Should chart rendering use ECharts-only output first, or static PNG charts
  first for offline reliability?
- Should report QA live as a tool from day one, or begin as an internal function
  called by `html_report` tests?

Recommended defaults:

- Use snake_case internally.
- Store summarized process context by default.
- Let historical trajectories inform defaults only when memory is enabled and
  source labels are preserved.
- Require confirmation or visible assumptions for inferred requirements that
  affect metric口径, audience, comparison baseline, or recommendations.
- Render KPI cards directly in HTML v2.
- Use ECharts for embedded report charts first because the current renderer
  already depends on it, while still supporting static image artifacts later.
- Implement QA as a pure function first, then expose it as a tool once the model
  needs to call it explicitly.

## 13. Immediate Next Step

Before writing production code, create a Wave 1 implementation plan under
docs/superpowers/plans/ with exact touched files, tests, and rollback strategy.

Wave 1 should stay deliberately small:

- User Need, Data Context, Process Context, and TraceLink dataclasses
- JSON conversion
- explicit versus inferred requirement helpers
- context summary fixtures from data profile and process trace examples
- traceability mapping helpers from need/context to future contract fields
- fixture tests
- no runtime registration yet

This keeps the first implementation slice verifiable and prevents the effort
from ballooning into Web, chart rendering, and prompt changes all at once.
