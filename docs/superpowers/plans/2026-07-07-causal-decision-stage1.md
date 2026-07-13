# Causal Decision MVP · Stage 1 Executable Plan

> Status: planned next, 2026-07-07
>
> Roadmap owner: P1-10 Causal Decision MVP
>
> Baseline: docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md, section
> P1-10.
>
> Scope: planning-to-implementation packet for the first causal-decision slice.
> This plan intentionally avoids production code changes until the user approves
> implementation.

## 0. Executive Decision

Stage 1 should not try to become a general causal inference platform. The first
slice must build a local, auditable decision workflow that prevents correlation
from being mislabeled as causation and makes randomized experiment readouts
usable for business action.

Primary decision:

- Build a pure stdlib causal domain layer first, mirroring the reporting domain
  layer.
- Support Causal Contract, causal-readiness QA, A/B experiment readout, action
  plans, and report integration.
- Use pandas/numpy through python_analysis or deterministic helper code only for
  simple experiment calculations.
- Defer DoWhy, EconML, CausalML, causal discovery, and observational estimators
  until the contract and QA surface is stable.

Rejected paths:

- Do not start by installing a causal library.
- Do not infer a causal graph silently from column names.
- Do not allow observation-only correlation to pass as causal evidence.
- Do not produce operational recommendations without assumptions, evidence,
  monitoring, and rollback criteria.

## 1. Acceptance Contract

```json
{
  "intent": "Implement a first-stage causal decision workflow that separates descriptive, correlational, experimental, and causal-assumption-based claims, and supports auditable A/B experiment readouts with bounded action plans.",
  "non_goals": [
    "No Phase 2 causal inference platform.",
    "No DoWhy/EconML/CausalML dependency in the first slice.",
    "No automatic causal discovery.",
    "No automatic business action without user review.",
    "No weakening of existing reporting, safety, or quality gates."
  ],
  "acceptance": [
    "Causal Contract domain objects serialize deterministically and distinguish treatment, outcome, unit, population, time window, assignment mechanism, confounders, assumptions, external events, and decision threshold.",
    "Causal intent parsing routes causal, experiment, and action requests without treating inferred requirements as explicit facts.",
    "Causal-readiness QA blocks causal-ready labels when required causal fields or assumptions are missing.",
    "A/B experiment readout returns group balance, sample ratio mismatch state, primary metric lift, confidence interval, guardrail notes, segment caveats, and a bounded decision.",
    "Observation-only requests are labeled as correlation or hypothesis unless accepted identification assumptions exist.",
    "Report integration can render causal contract, readiness state, estimates, caveats, decision, and action plan.",
    "Focused tests and the project quality gate pass."
  ],
  "forbidden": [
    "Do not claim causality from correlation.",
    "Do not hide identification assumptions in prose only.",
    "Do not make LLM judgment the only causal readiness gate.",
    "Do not store volatile business outcomes as durable memory by default.",
    "Do not alter unrelated sandbox hardening or report-delivery work."
  ],
  "verify_commands": [
    ".venv/bin/python scripts/quality_gate.py",
    ".venv/bin/pytest tests/test_causal_model.py tests/test_causal_intent.py tests/test_causal_qa.py tests/test_experiment_readout.py tests/test_causal_report_adapter.py -v"
  ],
  "release_gate": "Quality gate green plus independent clean-context code review with no must-fix findings."
}
```

## 2. Current Inputs

Project foundations to reuse:

- reporting pure domain layer and traceability model
- report_need / report_context / report_contract tool pattern
- report QA readiness pattern
- chart/report document model
- week-1 mobile_app_ab_test seed dataset
- evolution evaluator's method/structure assertion philosophy
- quality gate and architecture drift checks

Known gaps:

- no causal domain package
- no causal tool registry entries
- no causal skill
- no causal readiness rules beyond report-level "causal claim needs caveat"
- no experiment readout API
- no causal-specific eval tasks

## 3. Target User Outcome

The user should be able to ask:

- "实验组是否提高了 D7 留存?"
- "这次活动是否导致收入提升?"
- "收入提升是否只来自少数国家,下一步该怎么做?"
- "某个运营动作有没有必要扩大?"

The agent should respond with:

- claim level: descriptive / correlational / experimental / causal-assumption
- Causal Contract
- data-readiness and causal-readiness state
- estimates and uncertainty
- caveats and refutation needs
- decision recommendation: ship / do not ship / inconclusive / needs more data
- action plan with monitoring and rollback

## 4. Stage 1 Architecture

Planned package:

- src/data_analysis_agent/causal/

Dependency rule:

- causal domain modules may depend on stdlib only.
- tools may depend on causal.
- reporting adapters may convert causal results into reporting contracts and
  report blocks.
- causal must not depend on agent_loop, runtime, protocol, tools, memory,
  telemetry, or evolution.

Planned module responsibilities:

| Module | Responsibility |
| --- | --- |
| model.py | CausalQuestion, CausalContract, VariableRole, AssignmentMechanism, ClaimLevel, CausalReadiness, EffectEstimate, ExperimentReadout, ActionPlan |
| intent.py | deterministic causal/experiment/action intent parsing from user text |
| qa.py | deterministic causal-readiness QA and claim-level guardrails |
| experiment.py | A/B experiment calculations and decision classification |
| report_adapter.py | causal result to ReportDocument / ReportBlock helpers |

Planned tools:

| Tool | Responsibility | Stage 1 status |
| --- | --- | --- |
| causal_contract | build/validate CausalContract from user intent + data context | required |
| causal_qa | run causal-readiness checks | required |
| experiment_readout | summarize randomized A/B experiment data | required |
| causal_action_plan | produce bounded action plan from readout + assumptions | optional in first commit, required before Stage 1 complete |

Planned skill:

| Skill | Responsibility |
| --- | --- |
| causal_decision_analysis | route experiment/causal/action requests through causal_contract -> causal_qa -> experiment_readout or observation-only readiness -> report/action output |

## 5. Domain Model Requirements

### 5.1 CausalQuestion

Required fields:

- raw_request
- decision_question
- treatment
- outcome
- unit
- population
- time_window
- claim_level_requested
- explicit_requirement_refs
- implicit_requirement_refs
- uncertainties

### 5.2 CausalContract

Required fields:

- question
- treatment
- outcome
- unit
- population
- time_window
- assignment_mechanism: randomized / quasi_experimental / observational / unknown
- candidate_confounders
- business_assumptions
- external_events
- data_requirements
- identification_strategy: randomized_experiment / correlation_only / needs_design / deferred
- decision_threshold
- guardrail_metrics
- missing_context
- field_sources

### 5.3 CausalReadiness

Required states:

- not_causal: descriptive or correlation-only output
- needs_contract: missing treatment/outcome/unit/time
- needs_assumptions: causal claim requested but assumptions are not explicit
- experiment_ready: randomized assignment is plausible and required fields exist
- causal_assumption_ready: observational causal analysis has explicit accepted
  assumptions, but Stage 1 should not estimate with complex methods
- blocked: severe missing data or contradictory assumptions

### 5.4 ExperimentReadout

Required fields:

- treatment_group
- control_group
- sample_sizes
- sample_ratio_mismatch
- primary_metric
- effect_absolute
- effect_relative
- confidence_interval
- p_value_or_unavailable
- guardrail_results
- segment_notes
- caveats
- decision: ship / do_not_ship / inconclusive / needs_more_data
- decision_reason

### 5.5 ActionPlan

Required fields:

- action
- target_population
- expected_mechanism
- evidence_refs
- assumption_refs
- risk
- monitoring_metrics
- rollback_trigger
- next_experiment
- owner_note

## 6. File Plan

Future source files:

- src/data_analysis_agent/causal/__init__.py
- src/data_analysis_agent/causal/model.py
- src/data_analysis_agent/causal/intent.py
- src/data_analysis_agent/causal/qa.py
- src/data_analysis_agent/causal/experiment.py
- src/data_analysis_agent/causal/report_adapter.py
- src/data_analysis_agent/tools/causal_contract.py
- src/data_analysis_agent/tools/causal_qa.py
- src/data_analysis_agent/tools/experiment_readout.py

Existing files likely touched:

- src/data_analysis_agent/tools/registry.py
- src/data_analysis_agent/runtime.py
- src/data_analysis_agent/skills/builtin.py
- src/data_analysis_agent/skills/__init__.py
- docs/ARCHITECTURE.md
- scripts/drift_rules.py
- pyproject.toml only if extra dependencies are later required; Stage 1 should
  avoid changing dependencies.

Future tests:

- tests/test_causal_model.py
- tests/test_causal_intent.py
- tests/test_causal_qa.py
- tests/test_experiment_readout.py
- tests/test_causal_report_adapter.py
- tests/test_causal_tools.py
- tests/test_causal_skill.py

Future eval fixtures:

- examples/eval_tasks/causal_decision_smoke.json
- examples/eval_tasks/experiment_readout_smoke.json

## 7. Implementation Tasks

### Task 1. Causal domain package and ADR

Files:

- new causal package
- docs/adr/0010-causal-decision-domain-layer.md
- docs/ARCHITECTURE.md
- scripts/drift_rules.py
- tests/test_causal_model.py

Steps:

- [ ] Write ADR for causal as a pure stdlib domain layer.
- [ ] Add drift rule: causal may not import runtime/tools/protocol/memory/telemetry/evolution.
- [ ] Add architecture manifest entries.
- [ ] Add frozen dataclasses and enum vocabularies.
- [ ] Add to_dict/from_dict deterministic roundtrip tests.

Acceptance:

- Causal domain objects are immutable and roundtrip cleanly.
- Quality gate passes.

### Task 2. Causal intent parsing

Files:

- src/data_analysis_agent/causal/intent.py
- tests/test_causal_intent.py

Steps:

- [ ] Add deterministic keyword/phrase parsing for causal, experiment, and action
      requests.
- [ ] Keep explicit and inferred user intent separate.
- [ ] Add Chinese and English fixtures:
      - "是否导致"
      - "为什么下降"
      - "实验组是否提高"
      - "能否扩大投放"
      - "下一步怎么做"
      - correlation-only phrasing
- [ ] Emit uncertainties for missing treatment, outcome, unit, time window, and
      assignment mechanism.

Acceptance:

- Parser identifies experiment requests without marking assumptions as explicit.
- Correlation-only requests do not become causal-ready.

### Task 3. Causal Contract tool

Files:

- src/data_analysis_agent/tools/causal_contract.py
- src/data_analysis_agent/tools/registry.py
- src/data_analysis_agent/runtime.py if tool assembly is centralized there
- tests/test_causal_tools.py

Steps:

- [ ] Add read-only causal_contract tool.
- [ ] Input: raw question, optional user_need, optional data_context, optional
      process_context, optional user-supplied business assumptions.
- [ ] Output: CausalContract metadata plus readable summary.
- [ ] Validate non-empty question.
- [ ] Include missing_context rather than guessing.

Acceptance:

- Tool is read-only and concurrency-safe.
- Missing treatment/outcome/unit/time window appear as missing_context.

### Task 4. Causal-readiness QA

Files:

- src/data_analysis_agent/causal/qa.py
- src/data_analysis_agent/tools/causal_qa.py
- tests/test_causal_qa.py
- tests/test_causal_tools.py

Steps:

- [ ] Add QA finding model with severity: blocker/high/medium/info.
- [ ] Add readiness classifier.
- [ ] Block causal-ready when treatment/outcome/unit/time window are absent.
- [ ] Mark observation-only as correlation/hypothesis unless accepted
      identification assumptions exist.
- [ ] Add checks for confounding, selection bias, external event, spillover,
      partial period, missing guardrails.

Acceptance:

- QA gives deterministic readiness and findings.
- Observation-only causal claims cannot pass as experiment_ready.

### Task 5. A/B experiment readout MVP

Files:

- src/data_analysis_agent/causal/experiment.py
- src/data_analysis_agent/tools/experiment_readout.py
- tests/test_experiment_readout.py
- tests/test_causal_tools.py

Steps:

- [ ] Define minimal table input shape for the tool:
      - records or columnar rows
      - group column
      - outcome column
      - optional denominator column
      - optional guardrail columns
      - optional segment columns
- [ ] Implement group sample sizes.
- [ ] Implement sample ratio mismatch heuristic.
- [ ] Implement difference in means or proportions.
- [ ] Implement confidence interval with stdlib math where possible.
- [ ] Add decision classifier:
      - ship when positive effect clears threshold and guardrails pass
      - do_not_ship when negative effect or guardrail failure is material
      - inconclusive when confidence interval crosses zero or sample is thin
      - needs_more_data when required columns or sample size are inadequate
- [ ] Add caveats for imbalance, missing guardrails, segment mix, outliers, and
      partial-period risk.

Acceptance:

- Randomized experiment fixture produces auditable effect estimate and bounded
  decision.
- Suspicious imbalance fixture produces warning or inconclusive decision.

### Task 6. Report integration

Files:

- src/data_analysis_agent/causal/report_adapter.py
- tests/test_causal_report_adapter.py
- optional updates to report skill instructions

Steps:

- [ ] Convert CausalContract + CausalReadiness + ExperimentReadout + ActionPlan
      into report blocks.
- [ ] Include claim level and readiness state visibly.
- [ ] Include caveats next to the relevant finding, not only at the end.
- [ ] Include action plan with monitoring and rollback trigger.

Acceptance:

- Generated ReportDocument can pass existing report QA when artifact exists.
- Causal caveats are explicit and adjacent to claims.

### Task 7. Causal skill routing

Files:

- src/data_analysis_agent/skills/builtin.py
- src/data_analysis_agent/skills/__init__.py
- tests/test_causal_skill.py
- tests/test_runtime.py if runtime tool allowlists need coverage

Steps:

- [ ] Add CausalDecisionAnalysisSkill.
- [ ] Keywords include 因果, 导致, 影响, 归因, 实验组, 对照组, A/B, ab test,
      uplift, treatment, outcome.
- [ ] Allowed tools include data_profile, report_need, report_context,
      causal_contract, causal_qa, experiment_readout, python_analysis,
      html_report.
- [ ] Instructions enforce workflow:
      1. parse/report need
      2. profile/context
      3. build causal contract
      4. run causal QA
      5. if randomized experiment, run experiment readout
      6. otherwise label as correlation/hypothesis and ask for assumptions or
         experiment design
      7. produce bounded action/report

Acceptance:

- Causal request routes to causal skill.
- Skill instructions explicitly forbid causal overclaiming.

### Task 8. Eval fixtures and behavior checks

Files:

- examples/eval_tasks/causal_decision_smoke.json
- examples/eval_tasks/experiment_readout_smoke.json
- tests/test_evaluator.py if new assertion keys are needed

Steps:

- [ ] Add at least 8-12 task fixtures.
- [ ] Cover randomized experiment, imbalance, guardrail failure, correlation-only,
      missing treatment, missing outcome, external-event caveat, inconclusive.
- [ ] Assertions check method/structure, not exact volatile numbers.

Acceptance:

- Behavior eval can distinguish experiment-readout success from unsupported
  causal overclaiming.

## 8. Verification Matrix

| Area | Automated checks | Manual checks |
| --- | --- | --- |
| Causal model | roundtrip, frozen equality, enum vocabulary | inspect naming and field completeness |
| Intent parsing | Chinese/English fixtures | inspect inferred vs explicit separation |
| Causal Contract tool | read-only validation, missing_context | inspect readable summary |
| Causal QA | readiness states and findings | inspect observation-only refusal |
| Experiment readout | balance, SRM, lift, CI, decision states | compare against hand-calculated small fixtures |
| Report adapter | report blocks and QA compatibility | inspect caveat placement |
| Skill routing | keyword routing and allowlist | inspect instruction order |
| Eval fixtures | method/structure assertions | inspect fixture realism |

Mandatory command:

- `.venv/bin/python scripts/quality_gate.py`

Focused commands:

- `.venv/bin/pytest tests/test_causal_model.py -v`
- `.venv/bin/pytest tests/test_causal_intent.py -v`
- `.venv/bin/pytest tests/test_causal_qa.py -v`
- `.venv/bin/pytest tests/test_experiment_readout.py -v`
- `.venv/bin/pytest tests/test_causal_tools.py -v`
- `.venv/bin/pytest tests/test_causal_skill.py -v`

## 9. Review Requirements

After implementation and self-tests:

- run independent clean-context report-only review
- reviewer must inspect:
  - causal overclaiming risk
  - deterministic QA behavior
  - math correctness for experiment readout
  - drift/manifest correctness
  - no unintended dependency additions
  - no memory/telemetry leakage
  - report caveat placement
- implementation agent fixes findings
- run a fresh independent re-review until no must-fix findings remain

## 10. Release Criteria

Stage 1 is complete only when:

- causal domain layer exists and is manifest/drift guarded
- causal_contract, causal_qa, and experiment_readout tools are registered
- causal skill routes relevant requests
- randomized experiment readout works on fixtures
- observation-only causal requests are blocked from causal-ready status
- report adapter produces auditable decision reports
- causal/experiment eval fixtures exist
- quality gate passes
- independent review loop has no must-fix findings

## 11. Risks and Mitigations

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Correlation is mislabeled as causation | Blocking | Causal-readiness QA and claim-level labels |
| Experiment readout ignores imbalance | Major | SRM/balance checks and inconclusive state |
| Action plan sounds too confident | Major | Require mechanism, evidence, assumptions, monitoring, rollback |
| LLM invents confounders as facts | Major | Mark business assumptions and confounders as inferred unless user-confirmed |
| First slice becomes a causal library integration project | Major | No new causal dependencies in Stage 1 |
| Numeric test brittleness | Medium | Use small deterministic fixtures and method/structure assertions |

## 12. Explicitly Deferred

- DoWhy/EconML/CausalML adapters
- automatic causal discovery
- difference-in-differences
- synthetic controls
- instrumental variables
- heterogeneous treatment effects
- uplift targeting
- experiment registry
- power/MDE planning UI
- long-running post-launch monitoring service

These belong to later Phase 1 extensions or P2-12 after the Stage 1 contract,
QA, and experiment readout are stable.
