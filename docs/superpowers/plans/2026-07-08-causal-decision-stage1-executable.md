# Causal Decision MVP · Stage 1 — Executable Plan

> Status: executable baseline, 2026-07-08
>
> Roadmap owner: P1-10 Causal Decision MVP
>
> Baseline:
>
> - `docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md`, section P1-10.
> - `docs/superpowers/plans/2026-07-07-causal-decision-stage1.md` (the architecture
>   packet; this document is its executable refinement and supersedes it for
>   implementation detail. The 07-07 packet is kept as background).
>
> Scope: a directly executable plan for the first causal-decision slice. Every
> signature, rule, and formula below has been checked against the current source
> tree. Engineers (or an implementing agent) can work slice-by-slice from
> section 10 without re-deriving design decisions.
>
> Statistics: normal-approximation z-test, 95% CI (α = 0.05), pure stdlib,
> deterministic. Decision threshold lives on `CausalContract`.

## 0. Executive Decision

Stage 1 does **not** build a general causal-inference platform. It builds a
local, auditable decision workflow that (a) prevents correlation from being
mislabeled as causation and (b) turns randomized A/B experiment data into a
bounded, auditable business decision with caveats and an action plan.

Primary decisions:

- Build a pure-stdlib causal domain package `src/data_analysis_agent/causal/`
  that mirrors the existing reporting domain layer, reusing
  `reporting.model.Serializable` (single source of truth) and emitting
  `reporting.contract.ReportDocument` through one adapter module.
- Ship `CausalContract`, causal-readiness QA, A/B experiment readout
  (`experiment_readout`), and a bounded `causal_action_plan`, plus a
  `causal_decision_analysis` skill and 12 structural eval fixtures.
- Use only `math` for statistics. Defer DoWhy/EconML/CausalML, causal discovery,
  observational estimators, multiple-comparison correction, segment-level tests,
  and the chi-square p-value.

Rejected paths:

- Do not start by installing a causal library.
- Do not infer a causal graph silently from column names.
- Do not allow observation-only correlation to pass as causal evidence.
- Do not act on estimates when assignment integrity (SRM) is broken.
- Do not produce operational recommendations without mechanism, evidence,
  assumptions, monitoring, and rollback.

## 1. Acceptance Contract

```json
{
  "intent": "Implement a first-stage causal decision workflow that separates descriptive, associational, experimental, and causal-assumption claim levels, supports auditable A/B experiment readouts, and emits bounded action plans.",
  "non_goals": [
    "No Phase 2 causal inference platform.",
    "No DoWhy/EconML/CausalML dependency in Stage 1.",
    "No automatic causal discovery.",
    "No automatic business action without user review.",
    "No multiple-comparison correction or segment-level z-tests.",
    "No weakening of existing reporting, safety, or quality gates."
  ],
  "acceptance": [
    "causal/ domain objects are frozen dataclasses reusing reporting.Serializable, serialize deterministically, and roundtrip through to_dict/from_dict.",
    "causal intent parsing routes causal/experiment/action requests without treating inferred requirements as explicit facts.",
    "Causal-readiness QA returns a deterministic CausalReadiness and findings, and blocks EXPERIMENT_READY when required fields or assumptions are missing.",
    "experiment_readout returns per-contrast effects, CI, SRM state, guardrail state, and a bounded aggregate decision, with degenerate/edge inputs handled without spurious p-values.",
    "Observation-only requests are labeled associational/hypothesis unless accepted identification assumptions exist.",
    "Report integration renders contract, readiness, estimates, caveats (adjacent to each FINDING), decision, and action plan, and passes reporting QA including _check_causal.",
    "12 causal/experiment eval fixtures exist with structural assertions only (ADR 0005).",
    "Focused tests and the project quality gate pass."
  ],
  "forbidden": [
    "Do not claim causality from correlation.",
    "Do not hide identification assumptions in prose only.",
    "Do not make LLM judgment the only causal-readiness gate.",
    "Do not store volatile business outcomes as durable memory by default.",
    "Do not compute z/p on degenerate (zero-SE) data.",
    "Do not alter unrelated sandbox hardening or report-delivery work."
  ],
  "verify_commands": [
    ".venv/bin/python scripts/quality_gate.py",
    ".venv/bin/pytest tests/test_causal_model.py tests/test_causal_intent.py tests/test_causal_qa.py tests/test_experiment_readout.py tests/test_causal_tools.py tests/test_causal_report_adapter.py tests/test_causal_action_plan.py tests/test_causal_skill.py -v",
    ".venv/bin/python scripts/eval_gate.py report examples/eval_tasks"
  ],
  "release_gate": "Quality gate green plus independent clean-context code review with no must-fix findings."
}
```

## 2. Grounded Conventions (verified against current source)

These are the load-bearing facts the plan reuses; they are not assumptions.

- Seed dataset `examples/training_data/week1_seed_assets/data/mobile_app_ab_test.csv`:
  700 rows; columns `user_id, assign_date, variant, country, device, sessions,
purchase_count, revenue, retention_d7, crash_count`. `variant` is three arms
  — `control` (255), `variant_a` (218), `variant_b` (227): real imbalance and a
  real SRM signal. `retention_d7` is a count in {0,1,2} (not boolean).
  `crash_count` is a guardrail. `country`/`device` are segments.
- Reporting idiom: `@dataclasses.dataclass(frozen=True)` + `class X(Serializable)`
  (mixin at `src/data_analysis_agent/reporting/model.py:117-133`); collections
  are `tuple[...]`; enums are `class X(str, enum.Enum)`; timestamps are
  caller-injected (never `datetime.now()`).
- Tool layer: `Tool` ABC at `src/data_analysis_agent/tools/base.py:62-132`;
  `async def call(input_data, can_use_tool=None) -> ToolResult`. Registration
  is imperative inside `runtime.build_registry()` (`src/data_analysis_agent/
runtime.py:75-115`). A read-only tool MUST override the three `is_*` flags
  AND be added to `READ_ONLY_TOOLS` (`runtime.py:58-67`); overriding
  `is_read_only` alone is not sufficient for plan-mode auto-allow.
- `ToolResult(content: str, is_error: bool, metadata: dict)`. Convention:
  `metadata = {"<key>": <domain_obj>.to_dict()}`; use `metadata["artifact_paths"]`
  only when the tool writes files.
- Reporting QA: `reporting/qa.py` defines `Severity`, `Readiness`, `QAFinding`,
  `QAReport`, `run_qa`. `_check_causal` (`reporting/qa.py:366-386`) raises a
  HIGH `causal.no_caveat` finding when a `BlockRole.FINDING` block contains a
  strong causal lexicon marker and lacks inline `caveats` or an adjacent
  `BlockRole.CAVEAT` block.
- HTML section ids (verified in `src/data_analysis_agent/evolution/evaluator.py:307-323`
  against `tools/html_report.py`): `class="card summary"→executive_summary`,
  `class="card caveat"→caveat`, `class="card recommendation"→recommendation`,
  `class="card finding"→finding`, `class="card chart-block"→chart`,
  `class="kpi-strip"→kpi`.
- Eval assertion whitelist (`scripts/eval_gate.py:23-34`):
  `no_error_results, min_tool_calls, tool_call_count_max, final_text_contains,
final_text_regex, required_tools, artifact_produced, artifact_has_sections`.
  Values of `final_text_contains`/`final_text_regex` may not contain
  `[<>=!]=\s*\d` (ADR 0005: assert structure, not numbers).
- Drift rules (`scripts/drift_rules.py`): `{"who": pkg, "forbid": [enumerated
top-level packages]}`; a catch-all `["data_analysis_agent"]` is forbidden
  because it self-matches internal relative imports. `FILE_SIZE_LIMIT = 600`.
- Manifest (`docs/ARCHITECTURE.md`, between `<!-- manifest:start -->` and
  `<!-- manifest:end -->`): every new `.py` except `__init__.py` needs a line
  `src/.../<path>.py = "<desc>"`, or the quality gate fails.
- Tests are flat: `tests/test_reporting_*.py`, `tests/test_report_skill.py` →
  causal tests are `tests/test_causal_*.py`.

## 3. Architecture and Dependency Rule

New package `src/data_analysis_agent/causal/`, split to stay under the 600-LOC
file-size limit:

| Module              | Responsibility                                                          | Dependencies                                         |
| ------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------- |
| `model.py`          | All frozen dataclasses + enums (including `ActionPlan`)                 | stdlib + `reporting.model.Serializable`/`SourceKind` |
| `intent.py`         | Deterministic CN/EN causal/experiment/action intent parsing             | stdlib                                               |
| `qa.py`             | Causal-readiness QA + closed-vocabulary findings                        | stdlib + `model`                                     |
| `experiment.py`     | A/B statistics + SRM + guardrails + decision classifier                 | stdlib (`math`) + `model`                            |
| `report_adapter.py` | causal results → `ReportDocument` (the only module importing reporting) | `model` + `reporting.contract`                       |

Dependency direction: `causal → reporting` (one-way; reuse `Serializable` and
use `ReportDocument` as the render target). All other internal packages are
forbidden.

Drift rule (two edits in `scripts/drift_rules.py`):

```python
# New causal rule (enumerate top-level packages; do NOT use a catch-all; see ADR 0010).
{
    "who": "data_analysis_agent.causal",
    "forbid": [
        "data_analysis_agent.agent_loop", "data_analysis_agent.protocol",
        "data_analysis_agent.runtime", "data_analysis_agent.evolution",
        "data_analysis_agent.telemetry", "data_analysis_agent.memory",
        "data_analysis_agent.tools", "data_analysis_agent.skills",
        "data_analysis_agent.session", "data_analysis_agent.kernel",
        "data_analysis_agent.context", "data_analysis_agent.security",
        "data_analysis_agent.sampling", "data_analysis_agent.persistence",
        "data_analysis_agent.state_machine", "data_analysis_agent.events",
        "data_analysis_agent.config", "data_analysis_agent.recovery",
        "data_analysis_agent.jsonl_store", "data_analysis_agent.artifacts",
        "data_analysis_agent.__main__", "data_analysis_agent.web",
        # reporting is intentionally NOT forbidden: causal depends on it one-way
        # for the Serializable mixin and the ReportDocument render target.
    ],
},
```

Also add `"data_analysis_agent.causal"` to reporting's `forbid` list
(`scripts/drift_rules.py:146-169`) to keep the dependency DAG acyclic (causal
sits above reporting; the reverse edge must be forbidden). Do NOT edit tools'
forbid list — `tools → causal` is already permitted.

## 4. Domain Model (`causal/model.py`, paste-ready)

All `@dataclasses.dataclass(frozen=True)`, all inherit `Serializable`, all
collections `tuple[...]`.

```python
from __future__ import annotations
import dataclasses, enum
from dataclasses import field
from data_analysis_agent.reporting.model import Serializable, SourceKind

class VariableRole(str, enum.Enum):
    OUTCOME="outcome"; TREATMENT="treatment"; CONTROL_ARM="control_arm"
    GUARDRAIL="guardrail"; SEGMENT="segment"; COVARIATE="covariate"; ASSIGNMENT="assignment"

class AssignmentMechanism(str, enum.Enum):
    RANDOMIZED="randomized"; QUASI_EXPERIMENT="quasi_experiment"
    SELF_SELECTION="self_selection"; UNKNOWN="unknown"

class ClaimLevel(str, enum.Enum):
    DESCRIPTIVE="descriptive"; ASSOCIATIONAL="associational"
    CAUSAL_ASSUMPTION="causal_assumption"; EXPERIMENTAL="experimental"

class CausalReadiness(str, enum.Enum):  # contract-level readiness, not the experiment decision
    NOT_CAUSAL="not_causal"; BLOCKED="blocked"
    NEEDS_ASSUMPTIONS="needs_assumptions"; NEEDS_DATA="needs_data"
    ASSUMPTION_READY="assumption_ready"; EXPERIMENT_READY="experiment_ready"

class DecisionLevel(str, enum.Enum):
    NEEDS_MORE_DATA="needs_more_data"; INCONCLUSIVE="inconclusive"
    DO_NOT_SHIP="do_not_ship"; SHIP="ship"

class OutcomeKind(str, enum.Enum):
    AUTO="auto"; PROPORTION="proportion"; MEAN="mean"

@dataclasses.dataclass(frozen=True)
class CausalIntent(Serializable):
    has_intervention: bool = False
    has_randomization_signal: bool = False
    wants_lift_or_effect: bool = False
    assignment_hint: AssignmentMechanism = AssignmentMechanism.UNKNOWN
    detected_outcome_terms: tuple[str, ...] = ()
    detected_treatment_terms: tuple[str, ...] = ()
    rationale: str = ""

@dataclasses.dataclass(frozen=True)
class CausalQuestion(Serializable):
    question: str
    intent: CausalIntent = field(default_factory=CausalIntent)
    user_need_refs: tuple[str, ...] = ()
    data_context_refs: tuple[str, ...] = ()
    process_context_refs: tuple[str, ...] = ()

@dataclasses.dataclass(frozen=True)
class VariableBinding(Serializable):
    column: str
    role: VariableRole
    rationale: str = ""
    source: SourceKind = SourceKind.IMPLICIT_USER

@dataclasses.dataclass(frozen=True)
class CausalContract(Serializable):
    question: str
    claim_level: ClaimLevel = ClaimLevel.DESCRIPTIVE
    assignment_mechanism: AssignmentMechanism = AssignmentMechanism.UNKNOWN
    outcome_columns: tuple[str, ...] = ()
    treatment_column: str | None = None
    control_arm: str | None = None
    treatment_arms: tuple[str, ...] = ()
    guardrail_columns: tuple[str, ...] = ()
    segment_columns: tuple[str, ...] = ()
    unit_of_analysis: str | None = None
    expected_ratio: tuple[float, ...] = ()
    decision_threshold: float = 0.0
    min_sample_size: int = 30
    business_assumptions: tuple[str, ...] = ()   # identifiability / ignorability
    external_events: tuple[str, ...] = ()         # concurrent confounders
    refutations: tuple[str, ...] = ()             # refutations considered
    variables: tuple[VariableBinding, ...] = ()
    field_sources: tuple[tuple[str, SourceKind], ...] = ()
    missing_context: tuple[str, ...] = ()
    intent: CausalIntent = field(default_factory=CausalIntent)

@dataclasses.dataclass(frozen=True)
class CausalFinding(Serializable):
    severity: str   # "blocker"|"high"|"medium"|"info" (matches reporting.Severity values)
    code: str
    message: str
    suggested_fix: str | None = None

@dataclasses.dataclass(frozen=True)
class CausalQAReport(Serializable):
    readiness: CausalReadiness
    findings: tuple[CausalFinding, ...] = ()
    contract_exists: bool = False

@dataclasses.dataclass(frozen=True)
class EffectEstimate(Serializable):
    outcome_column: str
    outcome_kind: OutcomeKind
    control_n: int
    treatment_n: int
    control_mean: float | None = None
    treatment_mean: float | None = None
    effect: float | None = None
    relative_effect: float | None = None
    se: float | None = None
    ci_lower: float | None = None
    ci_upper: float | None = None
    z: float | None = None
    p_value: float | None = None
    significant: bool | None = None   # CI excludes 0; None when degenerate
    degenerate: bool = False
    notes: tuple[str, ...] = ()

@dataclasses.dataclass(frozen=True)
class SRMResult(Serializable):
    arms: tuple[str, ...]
    observed: tuple[int, ...]
    expected: tuple[float, ...]
    chi_square: float | None = None
    df: int | None = None
    critical_value: float | None = None
    srm_detected: bool = False
    alpha: float = 0.05
    notes: tuple[str, ...] = ()

@dataclasses.dataclass(frozen=True)
class GuardrailResult(Serializable):
    column: str
    estimate: EffectEstimate
    unfavorable_direction: str   # "higher_is_worse" | "lower_is_worse"
    tolerance: float = 0.0
    breached: bool = False
    notes: tuple[str, ...] = ()

@dataclasses.dataclass(frozen=True)
class SegmentBreakdown(Serializable):   # Stage 1: descriptive only (arm sizes), no per-segment tests
    column: str
    note: str = ""
    arm_sizes: tuple[tuple[str, int], ...] = ()

@dataclasses.dataclass(frozen=True)
class ContrastResult(Serializable):
    treatment_arm: str
    outcome_estimate: EffectEstimate
    guardrails: tuple[GuardrailResult, ...] = ()
    segments: tuple[SegmentBreakdown, ...] = ()
    decision: DecisionLevel = DecisionLevel.INCONCLUSIVE
    decision_reasons: tuple[str, ...] = ()
    claim_level: ClaimLevel = ClaimLevel.EXPERIMENTAL

@dataclasses.dataclass(frozen=True)
class ExperimentReadout(Serializable):
    contract_question: str
    control_arm: str
    outcome_column: str
    outcome_kind: OutcomeKind
    contrasts: tuple[ContrastResult, ...] = ()
    srm: SRMResult | None = None
    aggregate_decision: DecisionLevel = DecisionLevel.INCONCLUSIVE
    aggregate_reasons: tuple[str, ...] = ()
    min_sample_size: int = 30
    decision_threshold: float = 0.0
    total_n: int = 0
    notes: tuple[str, ...] = ()

@dataclasses.dataclass(frozen=True)
class ActionRecommendation(Serializable):
    code: str   # "ship"|"hold"|"fix_srm"|"add_power"|"drop_arm"|"investigate_guardrail"
    target_arm: str | None = None
    rationale: str = ""
    precondition: str = ""

@dataclasses.dataclass(frozen=True)
class ActionPlan(Serializable):
    decision: DecisionLevel
    recommendations: tuple[ActionRecommendation, ...] = ()
    assumptions: tuple[str, ...] = ()
    refutations: tuple[str, ...] = ()
    open_risks: tuple[str, ...] = ()
```

Differences from the 07-07 packet (intentional refinements): `CausalReadiness`
is a 6-state contract-level enum with an explicit mapping to
`reporting.Readiness` (section 8); `ContrastResult` is added so multi-arm
experiments (the real 3-arm seed) are first-class; `EffectEstimate`,
`SRMResult`, `GuardrailResult`, `SegmentBreakdown` make the readout auditable;
the `ActionPlan` dataclass lives in slice 1's `model.py` (only its tool is in
slice 6).

## 5. `intent.py` — Deterministic Intent Parsing

Closed-vocabulary, regex/keyword, no LLM. Explicit vs inferred are strictly
separated: detected items are tagged `SourceKind.IMPLICIT_USER`; only verbatim
user text counts as explicit.

- Intervention/treatment lexicon: `导致/引起/造成/驱动/影响/归因/是否导致/can it cause/does it drive`
  → `has_intervention=True`.
- Randomization signal: `A/B/ab测试/ab test/实验组/对照组/随机/分流/treatment/control/variant/uplift`
  → `has_randomization_signal=True` and `assignment_hint=RANDOMIZED`.
- Lift/effect lexicon: `提升/提高/下降/lift/increase/decline/是否有效` →
  `wants_lift_or_effect=True`.
- Missing treatment/outcome/unit/time/assignment → written to `missing_context`,
  never guessed.
- Observation-only phrasing (`因为/由于/相关/correlation`) with no randomization
  signal → `claim_level=ASSOCIATIONAL`; must not be upgraded to causal.

Tests (`tests/test_causal_intent.py`): `是否导致` / `为什么下降` /
`实验组是否提高留存` / `能否扩大投放` / `下一步怎么做` / a correlation-only
sentence. Assertions: experiment requests are detected but assumptions are not
marked explicit; correlation-only requests do not reach `EXPERIMENT_READY`.

## 6. `qa.py` — Causal-Readiness QA (deterministic)

Single entry `run_causal_qa(contract: CausalContract) -> CausalQAReport`.
Closed-vocabulary finding codes; severities aligned with reporting.

Classification (top-down precedence):

| Condition                                                      | Readiness           | Key finding (code / severity)                                                                   |
| -------------------------------------------------------------- | ------------------- | ----------------------------------------------------------------------------------------------- |
| No intervention/treatment                                      | `NOT_CAUSAL`        | `causal.not_causal` (info)                                                                      |
| Treatment present but assignment unknowable / not identifiable | `BLOCKED`           | `causal.assignment_unknown` (blocker)                                                           |
| Identifiable but no `business_assumptions`                     | `NEEDS_ASSUMPTIONS` | `causal.needs_assumptions` (high)                                                               |
| Assumptions present but outcome/guardrail columns unresolved   | `NEEDS_DATA`        | `causal.needs_data` (high)                                                                      |
| Observational + explicit accepted assumptions                  | `ASSUMPTION_READY`  | `causal.observational_assumption` (medium; note Stage 1 does not estimate with complex methods) |
| Randomized + required fields present                           | `EXPERIMENT_READY`  | (no blocker/high)                                                                               |

Additional checks (emit medium/info findings; do not alone change readiness):
confounding, selection bias, spillover, external events, partial period,
missing guardrails, and `stats.no_multiple_comparison_correction` (info, when
more than one treatment arm).

## 7. `experiment.py` — Statistics and Decision (math correctness bearer)

### 7.1 Constants

```python
import math
Z_975 = 1.959963984540054          # two-sided 95% normal quantile
SQRT2 = math.sqrt(2.0)
CHI2_CRIT_05 = {1:3.841, 2:5.991, 3:7.815, 4:9.488, 5:11.070,
                6:12.592, 7:14.067, 8:15.507, 9:16.919, 10:18.307}
```

### 7.2 Effect estimate `compute_effect(col, c_vals, t_vals, kind)`

- Filter non-numeric/NaN; compute `n_c, n_t`; if either is 0, return early with
  `degenerate=True`, note `empty_group`.
- AUTO detection: `_is_binary = non-empty and values ⊆ {0,1}` → PROPORTION,
  else MEAN.
- Forced `kind=PROPORTION` on non-binary data → raise `ValueError` (the tool
  layer turns this into a `ValidationResult.fail`); never silently fall back to
  MEAN.
- **Proportion (binary)**: pooled `p = (x_c+x_t)/(n_c+n_t)`; `effect = p_t − p_c`;
  `relative = effect/p_c` (`None` when `p_c = 0`); `pooled ∈ {0,1}` → `SE = 0` →
  degenerate. Otherwise `SE = sqrt(p(1−p)(1/n_c + 1/n_t))`, `z = effect/SE`,
  `p_value = erfc(|z|/sqrt(2))`, `CI = effect ± Z_975·SE`. Note `low_cell_count`
  when `pooled·(n_c+n_t) < 5`.
- **Mean (continuous)**: ddof=1 variance (the tool layer enforces
  `min_sample_size >= 2`, since ddof=1 needs n≥2); `effect = mean_t − mean_c`;
  `relative = effect/mean_c` (`None` when `mean_c = 0`);
  `SE = sqrt(var_t/n_t + var_c/n_c)`; `SE = 0` → degenerate (equal means →
  `effect = 0`; differing means → do NOT compute z/p, report `CI = [effect, effect]`);
  otherwise same formula as proportion.
- Every estimate carries note `welch_z_approx` (method is auditable).
- `significant`: `None` when degenerate; else `(ci_lower > 0) or (ci_upper < 0)`.
- `p_value` uses `math.erfc(abs(z)/math.sqrt(2))` (numerically stable; clamp to
  `[0,1]`). Do NOT use `2*(1−Φ)`, which loses precision for large `|z|`.

### 7.3 SRM `compute_srm(arms, observed, expected_ratio=None, alpha=0.05)`

Chi-square goodness-of-fit: normalize the expected ratio, `exp_i = r_i·N`. If
any `exp <= 0` → do not flag SRM, note `zero_expected_cell`. `k < 2` → note
`single_arm`. `N = 0` → note `empty`. `df = k−1` beyond the table (`>10`) →
note `df_exceeds_table`, do not flag. Otherwise `chi2 = Σ(o−e)²/e` and
`srm_detected = chi2 > CHI2_CRIT_05[df]`. Report only `chi2/df/critical/bool`
(no p-value; Stage 1 does not implement the regularized incomplete gamma).

### 7.4 Guardrail `compute_guardrail(col, c_vals, t_vals, kind, direction, tolerance)`

Reuses `compute_effect`. `higher_is_worse` (default; fits `crash_count`,
latency, error rate): `breached = ci_lower is not None and ci_lower > tolerance`.
`lower_is_worse`: `breached = ci_upper is not None and ci_upper < -tolerance`.
Degenerate → not breached.

### 7.5 Per-contrast decision `classify_contrast(...)`

```
1. control_n < min or treatment_n < min      → NEEDS_MORE_DATA  [below_min_sample_size]
2. estimate.degenerate                        → INCONCLUSIVE     [degenerate_zero_se]
3. srm.srm_detected                           → INCONCLUSIVE     [srm_contamination]    # do not trust estimates
4. any guardrail breached                     → DO_NOT_SHIP      [guardrail_breach:<col>...]
5. ci_upper < 0 (significant negative)        → DO_NOT_SHIP      [significant_negative_effect]
6. ci_lower > 0 and threshold met             → SHIP             [significant_positive_and_threshold_met]
7. otherwise                                  → INCONCLUSIVE     [ci_crosses_zero | threshold_not_met]
threshold met := threshold <= 0 or (relative_effect is not None and relative_effect >= threshold)
```

### 7.6 Aggregate `aggregate(contrasts, srm)`

```
srm_detected              → INCONCLUSIVE     [srm_contamination]
any DO_NOT_SHIP           → DO_NOT_SHIP      [at_least_one_do_not_ship]
any NEEDS_MORE_DATA       → NEEDS_MORE_DATA  [at_least_one_needs_more_data]
any INCONCLUSIVE          → INCONCLUSIVE     [at_least_one_inconclusive]
all SHIP                  → SHIP             [all_contrasts_ship]
```

Lattice note: `do_not_ship` ranks above `needs_more_data` (a contrast that
already passed the minimum-sample check and found harm overrides another
contrast's insufficient sample).

### 7.7 Readout-level precondition (runs first)

Missing outcome column, or missing control/treatment arm →
`aggregate_decision = NEEDS_MORE_DATA` (reason `missing_outcome_column` /
`missing_arm`), no contrasts computed. SRM can still be computed from group
sizes alone (skip if arms are missing).

### 7.8 Edge cases (must be covered in `tests/test_experiment_readout.py` as the contract)

Empty group (n=0); n=1 (rejected by the tool layer, `min_sample_size >= 2`);
proportion `pooled ∈ {0,1}` (degenerate); continuous both-variances-zero with
equal/with differing means; `p_c = 0` / `mean_c = 0` (`relative = None`); single
arm total (rejected by the tool layer); `k > 10` (df beyond table);
`expected_ratio` containing a zero; non-numeric outcome column; missing required
column. With more than one treatment arm, emit `stats.no_multiple_comparison_correction`
(info; Stage 1 does not do Bonferroni/Holm).

## 8. `report_adapter.py` — causal → ReportDocument

The only module that imports `reporting.contract`. Function
`to_report_document(readout, contract, qa_report, *, generated_at=None) -> ReportDocument`
(`generated_at` is injected by the caller; the adapter stays pure).

Readiness mapping (lives only here; `causal.qa` never imports reporting):

| CausalReadiness                     | → reporting.Readiness |
| ----------------------------------- | --------------------- |
| NOT_CAUSAL / BLOCKED                | DRAFT                 |
| NEEDS_ASSUMPTIONS / NEEDS_DATA      | NEEDS_REVIEW          |
| ASSUMPTION_READY / EXPERIMENT_READY | READY                 |

Block sequence (satisfies both `_check_causal` and `artifact_has_sections`):
HEADER → EXECUTIVE_SUMMARY (claim_level + aggregate_decision) → DATA_CONTEXT
(per-arm n, SRM state) → KPI_STRIP (effect/CI per contrast; renders as
`class="kpi-strip"` → section `kpi`) → for each `ContrastResult`: a FINDING
(lift/CI/relative; use neutral phrasing such as "lift of", "difference of",
"associated with" — avoid strong causal verbs) immediately followed by a CAVEAT
block (`BlockRole.CAVEAT` → `class="card caveat"` → section `caveat`; contents:
imbalance / segment / partial-period / external-event / SRM) → RECOMMENDATION
(action-plan summary) → CAVEAT (assumptions / refutation needs) →
SOURCE_METADATA (evidence_refs).

Critical: every FINDING must be immediately followed by a CAVEAT block (not
only inline caveats), or `_check_causal` raises `causal.no_caveat` (high) and
the `artifact_has_sections: ["caveat"]` assertion fails.

## 9. Tools (four, all read-only)

Each overrides `is_concurrency_safe=True / is_read_only=True / is_destructive=False`,
returns `ToolResult(content=_render(...), metadata={"<key>": <obj>.to_dict()})`,
is added to `READ_ONLY_TOOLS`, is exported from `tools/__init__.py`, and is
instantiated+registered in `runtime.build_registry()`. Template: `ReportContractTool`
(`src/data_analysis_agent/tools/report_contract.py`) — thin wrapper delegating
to pure functions in `causal.*`. None of these write files, so no
`artifact_paths`.

| Tool                 | Inputs                                                                                                                                                                                                                                                                                    | metadata key         | Object              |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- | ------------------- |
| `causal_contract`    | question (req), user_need?, data_context?, process_context?, business_assumptions?, external_events?                                                                                                                                                                                      | `causal_contract`    | `CausalContract`    |
| `causal_qa`          | causal_contract (req, dict)                                                                                                                                                                                                                                                               | `causal_qa`          | `CausalQAReport`    |
| `experiment_readout` | records or columns (oneOf), control_group (req), treatment_groups (req, list), outcome_column (req), outcome_kind? (auto/proportion/mean), guardrail_columns?, guardrail_directions?, segment_columns?, expected_ratio?, decision_threshold? (default 0.0), min_sample_size? (default 30) | `experiment_readout` | `ExperimentReadout` |
| `causal_action_plan` | experiment_readout (req, dict), causal_contract? (dict)                                                                                                                                                                                                                                   | `causal_action_plan` | `ActionPlan`        |

`experiment_readout` `input_schema` uses
`oneOf:[{required:[records]},{required:[columns]}]` with
`records: list[dict]` / `columns: dict[str, list]`. `validate_input`: exactly
one of records/columns; `control_group` ∈ arms present in the data; every
treatment ∈ arms present; outcome column present; when `outcome_kind=proportion`,
values must be ⊆ {0,1} (else `ValidationResult.fail`); `min_sample_size >= 2`;
arm count ≤ 11. The decision threshold is passed explicitly (the agent reads it
off the contract); the tool does not ingest the contract, to avoid coupling.

## 10. Skill — `causal_decision_analysis`

New file `src/data_analysis_agent/skills/causal_skill.py` (separate file to keep
`builtin.py` under the 600-LOC limit). Registered in
`runtime.build_skill_registry()` (`runtime.py:118-133`) via
`skills.register(CausalDecisionAnalysisSkill())`.

- `keywords` (CN+EN): `因果/导致/影响/归因/实验组/对照组/A/B/ab测试/ab test/随机/分流/variant/treatment/outcome/uplift/causal/experiment`.
- `allowed_tools`: `read_file, data_profile, report_need, report_context, causal_contract, causal_qa, experiment_readout, causal_action_plan, python_analysis, html_report`.
- `instructions` (multi-line numbered; enforces workflow + forbids overclaiming):
  1. `report_need` to parse the request (explicit/implicit separated);
  2. `data_profile` + `report_context` for candidate columns and business grain;
  3. `causal_contract` to build the causal contract (gaps go to `missing_context`, never guessed);
  4. `causal_qa` to run readiness checks — no causal conclusion unless `EXPERIMENT_READY`;
  5. randomized experiment → `experiment_readout` (use `python_analysis` to pull columns from the file and pass them as records/columns);
  6. otherwise label correlational/hypothesis and ask for assumptions or an experiment design;
  7. `causal_action_plan` for a bounded action recommendation with mechanism/evidence/assumptions/monitoring/rollback;
  8. `html_report` to render — every FINDING immediately followed by a CAVEAT; FINDING bodies use neutral phrasing, causal language reserved for CAVEAT/assumption blocks;
  9. **forbidden**: treating correlation as causation, and using LLM judgment as the only readiness gate.

## 11. Eval Fixtures (12, structural assertions only — ADR 0005, anti-overfitting)

Directory `examples/eval_tasks/`. All use
`dataset_fixture: examples/training_data/week1_seed_assets/data/mobile_app_ab_test.csv`.
Every assertion uses only whitelisted keys; values contain no numeric
comparison. `final_text_contains` is checked only at LLM run time (not by the
deterministic gate), so bilingual needles are used for robustness.

| task_id                         | Scenario                                          | Key assertions                                                                                                                                                                      |
| ------------------------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `causal_ab_revenue_lift`        | outcome=revenue, control vs variant_b             | required_tools[causal_contract,experiment_readout,causal_action_plan]; artifact_has_sections[executive_summary,finding,caveat,recommendation,kpi]; final_text_contains[caveat,假设] |
| `causal_ab_retention_means`     | outcome=retention_d7 ({0,1,2} → mean path)        | required_tools[experiment_readout]; artifact_has_sections[finding,caveat]                                                                                                           |
| `causal_ab_purchase_count_mean` | purchase_count zero-inflated mean                 | required_tools[experiment_readout]; artifact_has_sections[finding,caveat]                                                                                                           |
| `causal_ab_sessions_mean`       | sessions continuous                               | required_tools[experiment_readout]; final_text_contains[caveat]                                                                                                                     |
| `causal_ab_crash_guardrail`     | guardrail crash_count (higher_is_worse)           | required_tools[experiment_readout,causal_action_plan]; artifact_has_sections[caveat]; final_text_contains[guardrail]                                                                |
| `causal_ab_srm_flagged`         | raw imbalanced seed (255/218/227) → SRM fires     | required_tools[experiment_readout]; artifact_has_sections[caveat]; final_text_contains[SRM,不平衡]                                                                                  |
| `causal_ab_three_arm`           | control + variant_a + variant_b (multi-arm)       | required_tools[experiment_readout,causal_action_plan]; artifact_has_sections[executive_summary,recommendation]                                                                      |
| `causal_ab_segment_country`     | segment country (descriptive only)                | required_tools[experiment_readout]; artifact_has_sections[finding,caveat]                                                                                                           |
| `causal_qa_not_causal`          | descriptive question → NOT_CAUSAL                 | required_tools[causal_contract,causal_qa]; final_text_contains[描述,not causal]                                                                                                     |
| `causal_qa_needs_assumptions`   | A/B with no assumptions → NEEDS_ASSUMPTIONS       | required_tools[causal_qa]; final_text_contains[假设,assumption]                                                                                                                     |
| `causal_qa_experiment_ready`    | A/B + assumptions + guardrails → EXPERIMENT_READY | required_tools[causal_contract,causal_qa]; final_text_contains[ready,就绪]                                                                                                          |
| `causal_action_plan_hold`       | SRM/inconclusive → hold                           | required_tools[causal_action_plan]; artifact_has_sections[recommendation,caveat]; final_text_contains[暂缓,hold]                                                                    |

Example fixture (full set written at implementation time):

```json
{
  "task_id": "causal_ab_revenue_lift",
  "input": "A/B 测试(control 对照,variant_b 处理),用 mobile_app_ab_test.csv 判断 variant_b 是否提升 revenue。先建因果契约,再做实验读出并给出带 caveat 和假设的决策建议。",
  "dataset_fixture": "examples/training_data/week1_seed_assets/data/mobile_app_ab_test.csv",
  "assertions": {
    "no_error_results": true,
    "min_tool_calls": 2,
    "tool_call_count_max": 8,
    "required_tools": [
      "causal_contract",
      "experiment_readout",
      "causal_action_plan"
    ],
    "final_text_contains": ["caveat", "假设", "建议"],
    "artifact_has_sections": [
      "executive_summary",
      "finding",
      "caveat",
      "recommendation",
      "kpi"
    ]
  }
}
```

`eval_gate` change (`scripts/eval_gate.py:37-44`, `_DOMAIN_KEYWORDS` — two new
keys so causal tasks count toward domain coverage without lowering the
`MIN_DOMAINS = 3` bar):

```python
"experiment": ("实验","A/B","ab测试","ab test","experiment","随机","分流","variant","treatment","对照组"),
"product": ("产品","product","app"),
```

This edit touches a quality/eval gate; per the project SOP it requires explicit
user confirmation and the independent review loop at implementation time.

## 12. Implementation Slice Sequence (with dependencies and per-slice gate)

Per-slice gate: `cd DataAnalysisAgent && .venv/bin/python scripts/quality_gate.py`
(ruff/format/mypy/pytest/drift/manifest/file-size) plus the slice's focused
pytest. The eval gate runs from slice 8 on. Tests are flat:
`tests/test_causal_*.py`.

| #   | Slice            | New / changed                                                                                                                                                                                                    | Depends on                     | Focused tests                                                                        |
| --- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------ |
| 1   | Domain core      | `causal/__init__.py`, `causal/model.py`, `causal/intent.py`; ADR `0010-causal-decision-domain-layer.md`; drift (causal rule + add causal to reporting forbid); manifest entries + ARCHITECTURE dependency bullet | — (reporting exists)           | test_causal_model, test_causal_intent                                                |
| 2   | Causal QA        | `causal/qa.py`; manifest                                                                                                                                                                                         | 1                              | test_causal_qa                                                                       |
| 3   | Experiment stats | `causal/experiment.py`; manifest                                                                                                                                                                                 | 1                              | test_experiment_readout (cover all of §7.8)                                          |
| 4   | Three tools      | `tools/causal_contract.py`, `tools/causal_qa.py`, `tools/experiment_readout.py`; register + `READ_ONLY_TOOLS` + `tools/__init__.py`; manifest                                                                    | 1–3                            | test_causal_tools                                                                    |
| 5   | Report adapter   | `causal/report_adapter.py`; manifest                                                                                                                                                                             | 1, 3 (end-to-end test needs 4) | test_causal_report_adapter (assert each FINDING is immediately followed by a CAVEAT) |
| 6   | Action-plan tool | `tools/causal_action_plan.py`; register + `READ_ONLY_TOOLS` + `__init__.py`; manifest                                                                                                                            | 1, 3, 4                        | test_causal_action_plan                                                              |
| 7   | Skill            | `skills/causal_skill.py`; register in `build_skill_registry`; manifest                                                                                                                                           | 4, 6                           | test_causal_skill                                                                    |
| 8   | Eval fixtures    | 12 JSONs; `eval_gate` domain keywords                                                                                                                                                                            | 4, 6, 7                        | `.venv/bin/python scripts/eval_gate.py report examples/eval_tasks`                   |

Inter-slice invariants: tools (slice 4) depend on the domain (1–3); the adapter
strictly depends on 1 + 3 (end-to-end needs 4); the skill needs the tools; eval
needs everything. The `ActionPlan` dataclass is already in slice 1's `model.py`.

Each slice ends green before the next starts (closed-loop feedback control, per
the methodology).

## 13. Verification (end-to-end)

```bash
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/pytest tests/test_causal_model.py tests/test_causal_intent.py \
                  tests/test_causal_qa.py tests/test_experiment_readout.py \
                  tests/test_causal_tools.py tests/test_causal_report_adapter.py \
                  tests/test_causal_action_plan.py tests/test_causal_skill.py -v
.venv/bin/python scripts/eval_gate.py report examples/eval_tasks
```

Manual checks:

- Run `mobile_app_ab_test.csv`, revenue (control vs variant_b): the readout
  contains per-contrast effect/CI, SRM state, and a bounded decision.
- Three arms (255/218/227) trigger SRM → decision INCONCLUSIVE with
  `srm_contamination`.
- A correlation-only question routes to the skill but produces
  correlational/hypothesis output, never `EXPERIMENT_READY`.
- The generated HTML report has a CAVEAT block after every FINDING
  (`class="card caveat"`); `_check_causal` does not raise `causal.no_caveat`.

## 14. Review Requirements (mandatory independent loop)

After implementation + self-tests: spawn a fresh, context-independent read-only
review subagent each round (never reuse the previous reviewer's session). Its
input is only the requirement summary + the diff/file paths under review (no
coding-turn dialogue). Review focus: causal-overclaiming risk, QA determinism,
experiment math correctness (all of §7.8), drift/manifest correctness, no
unintended dependency additions, no memory/telemetry leakage, report caveat
adjacency. The implementing agent fixes each finding; a brand-new reviewer
re-checks until zero must-fix findings remain. The reviewer is report-only and
never edits code.

## 15. Risks and Mitigations

| Risk                                                     | Severity | Mitigation                                                                                |
| -------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------- |
| Correlation labeled as causation                         | Blocking | Causal-readiness QA + claim-level labels + `_check_causal` + FINDING-adjacent CAVEAT      |
| Readout ignores imbalance/guardrails                     | Major    | Chi-square SRM + guardrail breach + INCONCLUSIVE/DO_NOT_SHIP paths                        |
| Degenerate data yields spurious p-values                 | Major    | SE=0 / zero-variance differing means → do not compute z/p, mark degenerate → INCONCLUSIVE |
| Small-sample z-approximation is anti-conservative        | Medium   | Enforce min_sample ≥ 30 + `low_cell_count` info + label `welch_z_approx`                  |
| LLM invents confounders as fact                          | Major    | Assumptions/confounders tagged IMPLICIT_USER unless user-confirmed                        |
| First slice becomes a causal-library integration project | Major    | Stage 1 adds zero new dependencies (no DoWhy/EconML)                                      |
| Eval overfitting / grader gaming                         | Medium   | Structure-only assertions (ADR 0005), no numeric comparisons, bilingual needles           |
| `builtin.py` exceeds 600 LOC                             | Minor    | Skill in its own file `skills/causal_skill.py`                                            |

## 16. Explicitly Deferred (not in Stage 1)

DoWhy/EconML/CausalML adapters; automatic causal discovery; difference-in-differences;
synthetic controls; instrumental variables; heterogeneous treatment effects;
uplift targeting; experiment registry; power/MDE planning UI; long-running
post-launch monitoring service; multiple-comparison correction (Bonferroni/Holm);
segment-level z-tests (Stage 1 is descriptive only); chi-square SRM p-value;
small-sample t-critical table. These belong to later Phase 1 extensions or P2-12
once the Stage 1 contract, QA, and experiment readout are stable.

## 17. Implementation Gate

This document is the executable baseline. Production code is a separately
gated phase: start only on an explicit "go", implement slice-by-slice per
section 12, keep the quality gate green per slice, run the independent review
loop per section 14, and confirm with the user before the slice-8 `eval_gate`
edit.
