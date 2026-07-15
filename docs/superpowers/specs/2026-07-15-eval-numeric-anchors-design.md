# Eval Numeric Anchors (frozen-fixture correctness) — Design Stub

> Status: queued follow-up (not yet implemented). Captured 2026-07-15.
> Scope: close the audit's "eval can't detect a confidently-wrong number"
> (test-rigor TR-2 / G3-G5 correctness verification) for the eval harness.

## Problem

`check_assertions` (evolution/evaluator.py) verifies only METHOD/STRUCTURE
(no_error, tool-call counts, required_tools, artifact_produced, sections). A
candidate skill that runs cleanly, calls the right tools, and produces a report
— but computes the wrong number (e.g. a cohort denominator off by one column) —
passes eval green. ADR 0005 forbids asserting specific numbers because "data
drifts", so a generic numeric assertion would rot.

The exemption: a **frozen fixture** (eval task with `dataset_fixture`) does not
drift, so a numeric anchor against it is ADR-0005-compliant and closes the
silent-wrong-number gap for exactly the cases where it's safe.

## Design (6 parts)

1. **EvalRun**: add `computed_outputs: tuple[str, ...] = ()` — the concatenated
   `python_analysis` tool-result contents captured during the run.
2. **make_agent_run_fn**: when a `ToolResultEvent` for `python_analysis` arrives,
   append its content to `computed_outputs` (cap each capture to e.g. 20k chars).
3. **`numeric_anchor` assertion** (new): a list of
   `{value: float, tolerance: float, optional label}`. `check_assertions` parses
   numbers (`\d+(?:\.\d+)?`) from `computed_outputs` and asserts at least one
   parsed value is within `abs(value) * tolerance` of each anchor (absolute diff
   guard when value≈0).
4. **ADR 0005 discipline**: `eval_gate.validate_task` enforces that
   `numeric_anchor` is ONLY allowed on a task with a `dataset_fixture`
   (frozen data); a numeric anchor without a fixture is a gate FAIL (keeps the
   "assert method not values" invariant for everything else).
   - `eval_gate._ALLOWED_ASSERTION_KEYS` += `"numeric_anchor"`.
   - `_NUMERIC_PIN_RE` (value-pin guard) must NOT trip on the anchor's own
     `value` field — exclude `numeric_anchor` from the value-pin scan.
5. **Sample eval task**: one task under `examples/eval_tasks/` with a frozen
   small CSV fixture + a `numeric_anchor` (e.g. revenue sum) to prove the path
   end-to-end (needs a live model run, so mark it `@pytest.mark.live`-style or
   keep it as a structural gate task).
6. **Tests**: unit-level `check_assertions` — a run whose `computed_outputs`
   contains the anchored number passes; a run missing it (or with a wrong
   number) fails the anchor. Plus an `eval_gate` test that `numeric_anchor`
   without `dataset_fixture` is rejected.

## Why a dedicated follow-up

This is a 6-part FEATURE (capture → parse → tolerance → fixture discipline →
sample → tests), not a bug fix. It was scoped out of the 2026-07-14 audit fix
series (P0+P1+P2+P3-2, PR#1) to keep that changeset reviewable and to give the
feature its own spec + independent review rather than rush it into the tail of a
long session.

## Non-goals

- No change to `decide_promotion` semantics (a numeric-anchor failure is a
  `check_assertions` failure → the run `passed=False`, feeding the existing
  promote/retire/needs_review logic unchanged).
- No LLM judge — numeric anchors are deterministic float comparisons.
