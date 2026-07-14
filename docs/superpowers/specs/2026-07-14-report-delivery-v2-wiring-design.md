# Report Delivery v2 Wiring + Enforced QA Gate — Design

> Status: design baseline, 2026-07-14
>
> Scope: wire the existing reporting/causal/chart_render domain layers into the
> LIVE agent delivery path so delivered HTML reports are QA-gated, traceable,
> contract-driven and business-ready — closing the audit's C1/C2 (and G5).
>
> Related audit: `research/daa-audit-2026-07-14/REPORT.md` (§1 C1/C2) + `REPORT-SUPPLEMENT.md`
> Related prior design: `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`
> Predecessor commit: `d55ebd0` (surgical batch: C3 + ResultStore + read_file whitelist)

## 0. Executive Decision

The reporting Wave 1-8 domain layer (`reporting/`, `chart_render`, `causal/report_adapter`)
is implemented and unit-tested but **not wired into the live delivery path**. The live
model renders via the v1 `html_report` schema (title/summary/sections), which runs **zero
QA**, carries **no contract/traceability**, and lets the model hand-write arbitrary ECharts
options. This is exactly the F1 failure the 2026-07-06 self-audit identified, and the audit
confirms it is **not yet closed** for delivered reports (C1/C2, critical/high).

Main contradiction:

- Strong side: `run_qa`, `ReportContract`, `templates`, `chart_render`, `causal/report_adapter`
  all exist as deterministic, tested, pure-stdlib domain code.
- Weak side: the system prompt, `ReportGenerationSkill`, and `html_report.input_schema` all
  still describe the v1 shape, so the model never produces a v2 `ReportDocument` and the QA
  gate never runs on a delivered artifact.

Decision:

- Make the v2 `ReportDocument` path the **primary** `html_report` input; v1 sections become a
  deprecated legacy fallback that renders WITHOUT a QA badge (clearly marked), so existing
  callers/eval still work but the contract path is the default the model is guided to.
- **Enforce** the QA gate at the render boundary: `run_qa` readiness `DRAFT` (blocker
  findings) → refuse to write the file (return `is_error` with the blocker list + guidance);
  `NEEDS_REVIEW` → write the file with the existing banner; `READY` → write.
- Rewrite `config.system_prompt` + `ReportGenerationSkill.instructions` to drive the contract
  workflow: `report_need → report_context → report_contract → analysis → chart_render →
html_report(document)`, with "no contract, no render".
- Wire `chart_render` as the chart path inside the report skill (replaces hand-written
  ECharts) and call `reporting.chart_rules.select_family` from `chart_render` so the F3 fix
  is live.
- Wire `causal/report_adapter.to_report_document` so causal requests (already routed by
  `CausalDecisionAnalysisSkill`) produce a v2 `ReportDocument` and render through the same
  QA-gated path.
- Validate `evidence_refs` resolve to known artifacts/result_ids and that chart numeric
  series carry a source, at the v2 render boundary (anti-entropy at the exit, ADR 0002).
- Add a **contract-level end-to-end test** (TR-1) that drives `AgentLoop.run()` with a
  sequence client emitting `report_contract`→`html_report(document)` tool_use, and asserts
  `run_qa` ran, readiness reached the artifact, and a DRAFT document is refused.

Rejected paths:

- Do NOT delete v1 yet (eval tasks + tests still use it); deprecate, don't remove.
- Do NOT make QA a soft warning only — C2 says the gate must block DRAFT delivery.
- Do NOT rely on prompt wording alone — the schema must make v2 the discoverable path.
- Do NOT block `NEEDS_REVIEW` from rendering (that would block legitimate high-finding
  reports; the banner + readiness badge is the right UX).

## 1. Acceptance Contract

```json
{
  "intent": "Wire reporting/causal/chart_render domain layers into the live delivery path with an enforced QA gate, so delivered HTML reports are QA-gated and traceable.",
  "non_goals": [
    "No new domain logic in reporting/ (the layers exist); this is wiring + enforcement + tests.",
    "No v1 removal (deprecate only).",
    "No prompt-injection sanitization of evolution/memory (separate spec, P0-sec-1).",
    "No Web workbench changes beyond what the v2 path already supports.",
    "No new report templates/overlays (existing 8 + overlays get wired, not extended)."
  ],
  "acceptance": [
    "html_report.input_schema advertises `document` (ReportDocument) as the primary input; v1 sections documented as legacy.",
    "html_report v2 path refuses to write the file when run_qa readiness == DRAFT (returns is_error listing blockers); NEEDS_REVIEW and READY render with the readiness badge.",
    "config.system_prompt and ReportGenerationSkill.instructions describe the contract workflow (report_need→report_context→report_contract→analysis→chart_render→html_report(document)) and state 'no contract, no render'.",
    "chart_render is referenced by ReportGenerationSkill.allowed_tools and its instructions as the chart path; reporting.chart_rules.select_family is called inside chart_render (not just defined).",
    "causal/report_adapter.to_report_document is reachable from a causal request through the live skill path (causal_skill instructs ending in html_report(document) built via the adapter).",
    "A contract-level E2E test drives AgentLoop.run() (sequence client) through report_contract→html_report(document) and asserts: run_qa was invoked on the produced document, a DRAFT document is refused (no artifact written), a READY document writes the artifact.",
    "evidence_refs on FINDING/CHART/RECOMMENDATION blocks are checked to resolve to a known artifact path or result_id at render time; unresolvable refs downgrade to a HIGH QA finding (do not silently render).",
    "Quality gate green (ruff/format/mypy/pytest/drift); independent fresh-context review zero must-fix."
  ],
  "forbidden": [
    "Do not silently render a DRAFT (blocker) report — the gate must refuse.",
    "Do not remove v1 or break existing v1 tests/eval tasks.",
    "Do not introduce an LLM into run_qa or the evidence check (stay deterministic, ADR 0009).",
    "Do not weaken the anti-entropy guarantee: an unsourced number must not pass as sourced.",
    "Do not add a second composition root or bypass AgentRuntime.from_config."
  ],
  "verify_commands": [
    ".venv/bin/python scripts/quality_gate.py",
    ".venv/bin/pytest tests/test_html_report_v2.py tests/test_reporting_qa.py tests/test_report_skill.py tests/test_integration.py -v"
  ],
  "review_scope": "Wiring correctness (schema/prompt/skill/adapter reachability), QA enforcement semantics (DRAFT refuse vs NEEDS_REVIEW render), evidence-validation false-positive risk, E2E test fidelity, v1 backward-compat.",
  "release_gate": "Quality gate green + independent review zero must-fix before merge to main."
}
```

## 2. Current State (the gaps this spec closes)

- `html_report.input_schema` (tools/html_report.py:297-355) declares only v1 fields; `document` is not advertised → model never discovers v2. (C1)
- `_call_v2` runs `run_qa` but renders+writes regardless of readiness → gate is advisory. (C2)
- `config.system_prompt` (config.py:31-46) + `ReportGenerationSkill.instructions` (skills/builtin.py:124-142) describe v1 sections, not the contract workflow.
- `reporting.chart_rules.select_family` is never called in production (visualization finding).
- `ReportGenerationSkill` instructs hand-written ECharts, bypassing `chart_render`.
- `causal/report_adapter.to_report_document` has no live caller (causal finding).
- `evidence_refs` are unvalidated strings; chart numeric values unvalidated (report-quality findings).
- No E2E test drives the live loop through contract→QA→render (TR-1).

## 3. Design

### 3.1 html_report v2 as primary path (`tools/html_report.py`)

- Extend `input_schema`: add `document` (object, ReportDocument) and `charts` ({block_id: echarts-option}) as the primary properties; keep `title`/`sections` as a legacy alias. Update `description` to say "pass a `document` (ReportDocument); the legacy title/sections form still renders but without the QA gate".
- `_is_v2` already keys on `"document" in input_data` — keep.
- No change to v1 `_render_page` (legacy, QA-less, clearly unbadged).

### 3.2 QA gate enforcement (`_call_v2`)

- After `run_qa(document, artifact_exists=True)`: if `readiness == DRAFT`, return `ToolResult(is_error=True, content="Report blocked: N blocker findings: <codes>. Resolve before rendering.")` WITHOUT writing the file. Include the blocker `suggested_fix` list so the model can self-correct.
- `NEEDS_REVIEW` / `READY`: write the file with the existing badge/banner.
- This makes "blocker" literal. The model receives an error it can act on (re-run report_contract / add exec summary / add data scope).

### 3.3 System prompt + ReportGenerationSkill rewrite

- `config.system_prompt`: append a "Report delivery" paragraph naming the contract workflow and the hard rule "call html_report with a `document` (ReportDocument) built from report_contract; a report without a contract is blocked by QA."
- `ReportGenerationSkill.instructions`: replace step 5 with the v2 flow — build the ReportDocument (executive summary, findings w/ evidence_refs, charts via chart_render, recommendations, caveats, data_scope) and call `html_report(document=..., charts=...)`. Add `chart_render` to `allowed_tools`.

### 3.4 chart_render wired + select_family live (`tools/chart_render.py`, `reporting/chart_rules.py`)

- `chart_render` internally calls `select_family` (or the spec's family-selection entry) so the deterministic family choice is the production path, not dead code. (If `chart_render` already takes a `family` from the model, add: when the model omits family, derive it via `select_family` from the data shape.)
- `ReportGenerationSkill` instructs: "produce charts via chart_render, not hand-written options".

### 3.5 causal → report (`skills/causal_skill.py`, `causal/report_adapter.py`)

- `CausalDecisionAnalysisSkill.instructions`: after `experiment_readout`/`causal_action_plan`, build the ReportDocument via `causal.report_adapter.to_report_document(...)` and render via `html_report(document=...)`. Add `html_report` (and the causal read-only tools) to `allowed_tools` if missing.
- This makes the adapter reachable from a live causal request.

### 3.6 evidence + numeric validation at render boundary (`tools/html_report.py` v2 path)

- Before rendering, for each FINDING/CHART/RECOMMENDATION block: collect `evidence_refs`; resolve each against (a) artifact paths known to `ArtifactStore` / `metadata["artifact_paths"]` from this turn, or (b) `result_id`s in the `ResultStore`. Unresolved ref → append a HIGH QA finding `evidence.unresolved_ref` (does not hard-block unless it makes readiness DRAFT via other blockers; the finding is visible to the model).
- Chart numeric validation: keep lightweight — if a chart block has `evidence_refs` empty AND the block carries numeric-looking body text, the existing `finding.no_evidence` rule already fires; no new heuristic that could false-positive. (Avoid over-engineering; the deterministic `run_qa` rules already cover the high-value checks.)

### 3.7 Contract-level E2E test (`tests/test_integration.py` or new `tests/test_report_delivery_e2e.py`)

- A `_SequenceClient` that emits: TextBlock(plan) → ToolUseBlock(report_need) → ... → ToolUseBlock(html_report with a `document`). Drive `AgentLoop.run()`. Assert:
  - For a READY document: artifact written, `run_qa` readiness in the rendered HTML badge == READY.
  - For a DRAFT document (no contract): `html_report` returns `is_error=True`, NO artifact file written.
- This is the TR-1 gap closure: the live loop's report path is now tested.

## 4. Task Breakdown (for the implementation plan)

1. html_report v2 schema + QA enforcement (3.1, 3.2) + unit tests (DRAFT refuse, NEEDS_REVIEW render).
2. evidence validation at v2 boundary (3.6) + tests.
3. system_prompt + ReportGenerationSkill rewrite (3.3) + chart_render/select_family wiring (3.4).
4. causal_skill → report_adapter wiring (3.5) + test.
5. E2E contract test (3.7).
6. Quality gate + independent fresh-context review; address findings; commit.

## 5. Risks

- **QA too strict → blocks legitimate reports**: mitigate by only hard-refusing DRAFT (blocker), not NEEDS_REVIEW (high). Blockers are structural (no contract, no exec summary, no data scope, no artifact) — genuinely undeliverable.
- **v1 eval tasks break**: v1 path unchanged; only the model is guided to v2. Eval tasks that assert v1 still pass.
- **evidence validation false positives**: unresolved-ref is a HIGH finding (advisory), not a hard block, and only fires when a ref is claimed but not found — conservative.
- **Model adoption**: even with schema + prompt, the model may sometimes use v1. That's acceptable (v1 still renders); the contract path is the guided default and the QA gate protects v2. Live-run verification is a follow-up (audit limitation §5.3).

## 6. Verification

- `.venv/bin/python scripts/quality_gate.py` green.
- New unit tests: DRAFT-refuse, NEEDS_REVIEW-render, evidence-unresolved, select_family-called.
- New E2E test: live-loop contract→QA→render (READY writes, DRAFT refused).
- Independent fresh-context review zero must-fix.
