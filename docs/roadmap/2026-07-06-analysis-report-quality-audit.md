# DataAnalysisAgent Analysis Report Delivery Quality Audit

> Status: pre-Phase-1 audit baseline, 2026-07-06
>
> Scope: evaluate whether the current DataAnalysisAgent can deliver
> visualization-rich HTML analysis reports that are ready for business reporting,
> daily reports, weekly reports, and team review without user-side rework.
>
> Related roadmap: `docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md`
>
> Optimization design:
> `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`

## 0. Executive Judgment

Current DataAnalysisAgent can produce a technically valid HTML report artifact,
but it cannot yet guarantee a production-grade business analysis report.

The main contradiction is:

- Strong side: the harness can compute with Python, persist artifacts, and render
  a self-contained H5 report through `html_report`.
- Weak side: the product layer has no report contract, no domain-specific report
  templates, no chart-quality contract, and no report QA or behavior eval gate.

This means the current system is closer to "safe report renderer plus LLM free-form
judgment" than to "business analyst that understands what a stakeholder needs in
a report". It can satisfy simple or well-specified report requests, but it will
not consistently produce no-rework daily or weekly reports.

## 1. Audit Contract

Intent:

- Inspect the current report-delivery path before Phase 1 optimization.
- Judge report quality from a user-facing business-report perspective, not only
  from code correctness.
- Produce a durable baseline that tells Phase 1 what to fix first.

Non-goals:

- No production code change in this audit.
- No LLM benchmark run or paid API trace generation in this audit.
- No claim that current reports are production-ready merely because the HTML
  renderer and tests pass.

Acceptance:

- The audit names the current report pipeline and evidence paths.
- The audit uses a business-report quality rubric.
- The audit records strict findings, impact, and concrete Phase 1 fixes.
- The audit identifies how the local Web Workbench should expose and improve
  report quality rather than only wrap the CLI.

## 2. Evidence Read

Primary code and docs inspected:

- `src/data_analysis_agent/config.py`: default prompt includes local data
  analysis, `data_profile`, absolute paths, Excel, and `html_report`.
- `src/data_analysis_agent/skills/builtin.py`: built-in `report_generation`
  skill instructs the model to compute first, design executive summary plus
  findings, then call `html_report` once.
- `src/data_analysis_agent/tools/html_report.py`: deterministic H5 renderer with
  escaped text, chart option JSON escaping, artifact-dir confinement, table caps,
  and optional local ECharts embedding.
- `src/data_analysis_agent/tools/visualization.py`: code-string chart generator
  for matplotlib, seaborn, and plotly.
- `src/data_analysis_agent/tools/data_profile.py`: read-only structural discovery
  for CSV, TSV, Parquet, Excel workbooks, and directories.
- `src/data_analysis_agent/runtime.py`: CLI and eval share the same tool registry,
  including `html_report`.
- `src/data_analysis_agent/evolution/evaluator.py`: current evaluator checks
  method and structure, not numeric values.
- `tests/test_html_report.py`: tests rendering, escaping, path safety, artifact
  surfacing, plan-mode denial, and local ECharts inlining.
- `tests/test_artifacts.py`: tests image artifact persistence and visualization
  to Python execution artifact flow.
- `examples/training_data/week1_seed_assets/`: 20 synthetic business CSV datasets
  and 100 business-analysis seed tasks across 16 domains.
- `examples/eval_tasks/`: only one current golden eval task, a descriptive
  smoke test.

## 3. Target Quality Bar

A report is "ready to deliver" only if a busy business reader can open the HTML
file and immediately use it in a review, daily report, weekly report, or team
update.

Minimum target:

- It answers the user's explicit question first, not after methodology setup.
- It states data scope: files, sheets, filters, time window, grain, and row counts
  when they affect trust.
- It states metric definitions: numerator, denominator, exclusions, aggregation,
  time zone, and comparison baseline where relevant.
- It follows a report shape appropriate to the job: KPI readout, diagnostic memo,
  risk/anomaly report, funnel/cohort report, data-quality profile, or decision
  recommendation.
- It includes an executive summary, key findings, visual evidence, interpretation,
  caveats, and recommended next steps.
- Every chart has a reason to exist, a readable title/subtitle or caption,
  labeled axes, units, denominator/sample size when needed, and adjacent
  interpretation.
- Tables are used for exact lookup or audit detail, not as a lazy replacement for
  visual comparison.
- Claims are tied to computed evidence and do not overclaim causality.
- Language is report-ready: concise, stakeholder-oriented, Chinese or English as
  requested, and free of tool/process clutter.
- The artifact is durable and auditable: report file plus supporting run metadata
  and generated artifact paths.

## 4. Current Scorecard

Scores use 1 to 5, where 5 means ready for local production use and 3 means usable
but not reliable without human review.

| Dimension | Score | Judgment |
| --- | ---: | --- |
| HTML artifact rendering safety | 4.0 | Strong escaping, path confinement, table caps, and artifact metadata. Still depends on CDN by default unless local ECharts is configured. |
| Artifact delivery chain | 3.5 | HTML reports and charts surface as paths. A project-level report bundle and manifest are still missing. |
| Data discovery before analysis | 3.5 | `data_profile` is a solid pre-analysis affordance for files, sheets, and directories. |
| Business report structure | 2.0 | Prompt and skill mention executive summary and sections, but no enforceable report contract or report type exists. |
| Domain and scenario conventions | 1.5 | Seed assets cover domains, but runtime has no template registry for SaaS, support, retail, finance, risk, funnel, or operations reports. |
| Visualization judgment | 1.5 | `visualization` emits generic chart code. `html_report` accepts arbitrary ECharts options and does not validate chart semantics. |
| Metric and口径 handling | 2.0 | Memory can store metric definitions, but report generation does not require visible metric contracts. |
| Narrative and actionability | 2.0 | Relies on the model's free-form reasoning. No deterministic QA checks for "so what", caveats, recommendations, or next actions. |
| Daily/weekly report readiness | 1.5 | No cadence, period comparison, KPI strip, status signals, variance-to-plan, owner/action format, or reusable report package. |
| Behavior evaluation | 1.0 | Current eval corpus has one smoke task; no report-quality evals or rubric assertions. |

Overall: approximately 2.3/5 for business-report delivery. The renderer is ahead
of the report intelligence layer.

## 5. Findings

### F1. The HTML report tool is a renderer, not a report planner

Severity: High

Evidence:

- `html_report` accepts `title`, optional `summary`, and a list of sections with
  optional chart/table.
- Validation checks shape, path safety, chart option serializability, table row
  width, and size caps.
- It does not know report type, audience, cadence, business question, metric
  definitions, caveat placement, or whether the section order answers the user's
  need.

Impact:

- The tool can render a weak report perfectly.
- A bad or thin section plan can pass all validation.
- Users may receive a nice-looking artifact that still needs manual rewriting
  before it can be used in a meeting.

Phase 1 fix:

- Add a report planning layer before rendering.
- Introduce a report contract with audience, report type, decision question,
  period, comparison baseline, required metrics, cuts, caveats, and output
  language.
- Make `html_report` v2 render a richer report schema instead of accepting only
  generic sections.

### F2. The current report schema cannot express presentation-ready business reports

Severity: High

Missing schema concepts:

- KPI cards with current value, delta, target or status.
- Metric definitions and denominators.
- Data scope and reporting period.
- Comparison baseline: previous period, target, plan, peer group, cohort, or
  historical range.
- Finding severity or priority.
- Recommendation owner, expected impact, confidence, and next step.
- Caveats tied to specific findings.
- Appendix or hidden source metadata for auditability.
- Report type: daily report, weekly report, data quality profile, diagnostic
  memo, executive KPI readout, risk report, or decision memo.

Impact:

- The model has to encode important business structure as plain text.
- The renderer cannot help enforce a reliable reading path.
- Web UX cannot preview, validate, or edit report components cleanly because the
  underlying object model is too generic.

Phase 1 fix:

- Define an internal report model under a future reporting package.
- Keep renderer compatibility by translating the richer model into the current
  H5 surface at first.
- Add required roles: executive summary, metric context, findings, visuals,
  next steps, caveats.

### F3. Chart generation is code-first and not report-quality-first

Severity: High

Evidence:

- `visualization` validates only `chart_type` and returns Python code to be run
  later.
- The generated chart code uses generic defaults: simple seaborn/plotly calls,
  generic title, no metric contract, no baseline semantics, no chart selection
  rationale, and no final-context QA.
- `html_report` accepts arbitrary ECharts options and does not validate axes,
  units, legend, sorting, denominator, sample size, or label readability.

Impact:

- The agent may choose a line chart for a discrete comparison, a pie chart for
  too many categories, or a table where a ranked bar is needed.
- Charts can be visually present but analytically weak.
- The Web layer would make the weakness more visible, not less visible.

Phase 1 fix:

- Replace the default chart path with structured chart requests.
- Add a chart contract: analytical question, selected chart family, fields,
  grain, time window, comparison, denominator, fallback if sparse, and supported
  claim.
- Add deterministic chart QA checks before report rendering.
- Keep custom Python chart code as an advanced, approval-gated path.

### F4. The prompt and built-in report skill are directionally right but too thin

Severity: High

Evidence:

- The default prompt tells the model it can produce H5 reports and should call
  `data_profile` before writing analysis code.
- `ReportGenerationSkill` says to compute every statistic first, design an
  executive summary plus one section per finding, and call `html_report` once.

Missing instructions:

- How to classify report type.
- When to ask for missing context versus assume a default.
- What a daily report, weekly report, or复盘 report must contain.
- How to express metric口径 and data limitations.
- How to tie a finding to a chart and a recommended action.
- How to avoid overclaiming causality.
- How to build a report spine before rendering.

Impact:

- Good model behavior is possible but not guaranteed.
- Different runs can produce inconsistent report shapes for similar tasks.
- A user cannot rely on the agent to "understand the report job" without
  providing a very explicit prompt.

Phase 1 fix:

- Add declarative report skills or templates for common report archetypes.
- Add routing keywords and instructions for daily report, weekly report,
  KPI readout, diagnostic memo, funnel analysis, cohort analysis, risk/anomaly,
  and data quality profile.
- Update prompt tests so report-quality behaviors become behavioral contracts,
  not comments.

### F5. Metric memory exists, but report generation does not force口径 discipline

Severity: Medium

Evidence:

- Memory supports `metric_definition`, `analysis_pref`, and `open_concern`.
- Inferred metric definitions start unconfirmed and use a light-confirm loop.
- Report generation does not require surfacing metric definitions in the report.

Impact:

- The system may remember useful口径 but fail to display it where the reader needs
  it.
- Conversely, it may compute metrics without asking whether a high-stakes口径 is
  correct.

Phase 1 fix:

- Add a metric contract tool and connect it to memory.
- For report mode, require visible metric context before relying on metrics that
  are ambiguous, inferred, unconfirmed, or business-critical.
- Add Web UI affordances for confirming or correcting metrics during a run.

### F6. There is no report-quality eval gate

Severity: High

Evidence:

- Current evaluator checks method and structure, such as no error, tool-call
  counts, and final text contains/regex.
- The committed golden eval set currently contains one descriptive smoke task.
- Week-1 seed assets contain 100 richer business tasks, but they are not yet wired
  into report-quality evaluation.

Impact:

- A regression can still pass the code quality gate while report quality gets
  worse.
- Candidate skills can be evaluated for coarse workflow shape, not for
  report-readiness.

Phase 1 fix:

- Add report-quality fixtures from seed assets.
- Add deterministic assertions: used data_profile, computed exact aggregates,
  generated report artifact, included executive summary, included metric context,
  included caveat, included next action, included chart artifact or chart spec.
- Add a rubric layer for human or optional LLM review, but do not depend only on
  an LLM judge.
- Add report-quality failure taxonomy to behavior evaluation.

### F7. The Web layer should not be only a visual wrapper around the CLI

Severity: Medium

The proposed Web Workbench is necessary, but it should expose report quality
state, not just stream tool logs.

Required Web additions for report quality:

- Report intent form: audience, cadence, period, comparison baseline, report
  type, and output language.
- Live report plan preview before final render.
- Tool timeline plus evidence map: which computation supports which finding.
- Draft report QA panel: missing口径, missing caveat, unsupported claim, weak
  chart, missing next action.
- Artifact preview route for HTML report.
- Feedback tags specific to reports: wrong口径, missing chart, weak conclusion,
  unsupported recommendation, too long, too thin, wrong business framing.

Without these additions, the Web layer improves interactivity but not final report
quality.

### F8. Current visual style is usable but not yet polished for executive delivery

Severity: Medium

Evidence:

- The H5 template is mobile-friendly, readable, escaped, and simple.
- It uses a generic card layout, fixed report chrome, and generic "摘要" label.

Limitations:

- No table of contents or section anchors.
- No KPI card strip.
- No print/export mode.
- No chart/table density modes.
- No report theme tokens or chart color governance.
- No multi-section visual map.
- No generated-source or run metadata affordance.

Phase 1 fix:

- Keep the current renderer as the safe baseline.
- Add report-specific layout primitives: executive summary, KPI strip, finding
  sections, full-width chart blocks, compact notes, caveats, and next steps.
- Add print-friendly CSS and offline ECharts packaging for local reports.

## 6. Report Archetypes Phase 1 Must Support

### A. Daily or weekly KPI readout

Minimum structure:

- Title with period.
- Executive Summary.
- KPI cards: latest value, previous period delta, target/status when available.
- Driver sections: what moved, why it matters, which segment changed.
- Risks or anomalies.
- Next actions and owners when inferable.
- Caveats: missing data, partial period, metric changes, small sample sizes.

Common charts:

- Line chart for enough time points.
- Grouped bar for current versus previous period.
- Horizontal ranked bar for segment contributors.
- KPI cards for top-line values.
- Table only for exact audit detail.

### B. Business diagnostic memo

Minimum structure:

- Direct answer to "what changed".
- Verified drivers.
- Rejected explanations or not-yet-verified hypotheses.
- Segment evidence.
- Recommended next investigation.
- Caveats near the finding they affect.

### C. Funnel or cohort report

Minimum structure:

- Funnel definition or cohort definition.
- Stage denominators and conversion/drop-off.
- Segment comparison.
- Bottleneck or change point.
- Action recommendation.

Common charts:

- Stage bar or funnel for ordered progression.
- Cohort heatmap for retention matrix.
- Line or indexed trend for movement over time.

### D. Risk or anomaly report

Minimum structure:

- Rule used to flag anomaly.
- Affected rows, segments, or accounts.
- Concentration and severity.
- False-positive caveats.
- Suggested operational follow-up.

Common charts:

- Ranked bar for risk concentration.
- Scatter for outliers when enough observations exist.
- Table for exact flagged cases.

### E. Data quality profile

Minimum structure:

- Whether the data is suitable for the requested analysis.
- Row/column scope.
- Missingness, duplicates, key uniqueness, type anomalies, outliers.
- Join risks if multiple files or sheets are present.
- Analysis-safe fields and fields requiring cleanup.

This should be a first-class report type, not a generic "describe" output.

## 7. Required Report Contract

Every report-mode run should produce or infer this contract before heavy analysis.

Required fields:

- user question
- report type
- audience
- output language
- data source files or directories
- authorized data scope
- reporting period and cadence
- comparison baseline
- primary metrics and metric definitions
- business grain: user, account, customer, order, ticket, campaign, SKU, site, or
  another domain unit
- required cuts or segments
- known exclusions or filters
- expected artifact type: HTML report, chart image, table, or mixed

Defaulting policy:

- If audience is not specified, default to business stakeholder.
- If language is Chinese and the user writes Chinese, produce Chinese labels and
  prose.
- If period is absent but a date column exists, infer the available period and
  state it.
- If comparison baseline is absent, choose previous comparable period when
  available; otherwise state that no baseline was available.
- If the business question is ambiguous, ask one concise clarification before
  running expensive analysis only when the ambiguity affects the metric or
  decision.

## 8. Required HTML Report Shape

The first production-grade HTML report shape should include:

- Header: title, period, generated time, data scope summary.
- Executive Summary: 2 to 4 bullets or short paragraphs, answer first.
- KPI strip: optional, only when headline actuals and deltas exist.
- Metric and Data Context: visible only when needed for interpretation, otherwise
  compact.
- Findings: one section per major claim, each with evidence and "so what".
- Visual Evidence: chart/table blocks embedded in the relevant finding, not
  dumped at the end.
- Recommendations: action, owner or role, expected effect, and confidence when
  supportable.
- Caveats and Further Questions: only decision-relevant caveats, not generic
  disclaimers.
- Appendix/source metadata: preserved for audit, but not necessarily visible in
  the executive reading path.

## 9. Report QA Checks

A report QA tool or validator should fail or warn on:

- No direct answer in the first visible section.
- No executive summary for stakeholder reports.
- Quantitative claim without computed evidence reference.
- Chart without adjacent interpretation.
- Chart without units, axis labels, or denominator when needed.
- Trend chart with too few time points.
- Scatter chart with too few observations.
- Metric used without口径 when ambiguous.
- Recommendation not tied to evidence.
- Causal claim from observational data without caveat.
- Missing data scope or reporting period.
- No caveats in a report where missingness, sampling, partial periods, or joins
  affect interpretation.
- Report only contains summary plus one chart/table, unless the user requested a
  brief.

## 10. Phase 1 Work Needed Before Report Claims Are Production-Grade

### RQ-1. Report contract and planner

Add a planning component that turns the user request and data profile into a
report contract and report spine before analysis code is written.

Acceptance:

- Report-mode runs have a structured contract.
- Missing business-critical fields are either asked about or explicitly defaulted.
- The final report maps back to the contract.

### RQ-2. Report template registry

Add domain/report archetype templates for:

- daily KPI readout
- weekly KPI readout
- data quality profile
- diagnostic memo
- business recommendation
- funnel analysis
- cohort analysis
- risk/anomaly report
- operations dashboard-style summary

Acceptance:

- Similar report requests produce consistent section roles and chart families.
- Templates can be learned or refined from trajectories later, but initial
  templates should be curated and deterministic.

### RQ-3. Structured chart renderer and chart contract

Replace generic `visualization` as the default report chart path with structured
chart specs.

Acceptance:

- Chart specs include chart family, fields, grain, units, baseline, data
  sufficiency, and supported claim.
- The renderer writes artifacts directly and reports artifact paths.
- Custom code path remains possible but is explicitly advanced and gated.

### RQ-4. HTML report schema v2

Extend the report renderer to understand report roles rather than only generic
sections.

Acceptance:

- Supports executive summary, KPI cards, finding blocks, chart/table blocks,
  caveats, recommendations, and source metadata.
- Maintains current safety posture: escaping, path confinement, option escaping,
  size caps.
- Keeps backward compatibility for current `html_report` inputs until migration
  is complete.

### RQ-5. Report QA tool

Add deterministic report QA before final artifact handoff.

Acceptance:

- QA output appears in Web and CLI when report quality is incomplete.
- A report can be marked "draft", "needs review", or "ready" with reasons.
- High-severity QA failures should prevent "ready" labeling.

### RQ-6. Report behavior evals

Turn seed assets into report-focused evals.

Acceptance:

- Add report tasks covering at least retail, marketing, SaaS, support, finance,
  risk, operations, and supply chain.
- Include HTML report generation tasks, not only chat answers.
- Assertions verify method/structure plus artifact presence and report section
  requirements.
- Keep optional human or LLM rubric review separate from deterministic gates.

### RQ-7. Web Workbench report UX

The local Web Workbench should make report production interactive.

Acceptance:

- User can specify report type, period, audience, and baseline.
- User can see live tool execution and a report draft/plan.
- User can open the HTML artifact and give report-specific feedback.
- Feedback is captured into telemetry with report-quality tags.

## 11. Recommended Phase 1 Insertion Point

Do not wait until after the Web layer to fix report quality. The Web layer should
be built around the report contract from the beginning.

Recommended sequence:

1. Add report contract and report QA spec before Web implementation.
2. Implement Web MVP with contract fields and artifact preview.
3. Upgrade chart/report renderer after the basic Web loop works.
4. Add report behavior evals from seed assets.
5. Use real Web feedback to refine report templates and candidate skills.

Reason:

- If Web MVP ships first without report contract, it will mostly visualize the
  current CLI behavior.
- If report contract lands first, the Web surface can make the user's intent and
  QA state visible, which directly improves perceived intelligence.

## 12. Release Gate For "Ready-To-Report" Claims

The project should not claim production-grade report delivery until these are
true:

- At least 20 report-focused behavior evals exist across multiple domains.
- The agent can generate an HTML report artifact in those evals.
- Reports include executive summary, evidence-backed findings, chart/table
  interpretation, metric context when needed, caveats, and next actions.
- Report QA can detect missing report sections and unsupported claims.
- The local Web Workbench can preview/open the report and collect feedback.
- Quality gate plus optional eval gate both pass for the release candidate.

## 13. Bottom Line

Current DataAnalysisAgent has the right harness foundation and a credible first
HTML report renderer. It does not yet have the product/report intelligence layer
needed for "懂用户心思、懂业务汇报、用户无需加工".

The next optimization should treat report quality as a first-class subsystem:
contract, planner, templates, chart contract, report renderer v2, QA, evals, and
Web feedback loop. Otherwise Phase 1 will produce an interactive tool that still
hands users reports they must rewrite themselves.
