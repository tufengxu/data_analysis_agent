# DataAnalysisAgent Phase 1 / Phase 2 Execution Plan

> Status: planning baseline, 2026-07-05
>
> Scope: turn DataAnalysisAgent first into a complete single-user local
> production-grade, self-evolving data analysis agent; then evolve it into a
> distributed production-grade, self-evolving data analysis platform.
>
> Note on planned paths: future file paths are intentionally written as plain
> text rather than Markdown code spans until the files exist, so the project
> dead-link checker does not treat them as current files.

## 0. Executive Decision

The project should keep its existing harness-first architecture. The main work
is not to rebuild the agent; it is to finish the local product/control plane
around it, then use that stable local system as the reference implementation for
the distributed version.

Primary decision:

- Phase 1 target: a trustworthy local desktop/workbench product for one user,
  with explicit data authorization, visible agent execution, durable artifacts,
  governed memory, and a human-gated self-evolution loop.
- Phase 2 target: a distributed multi-user service that preserves Phase 1's
  contracts while adding multi-tenant security, isolated execution, queues,
  service-level observability, managed data connectors, and controlled skill
  rollout.
- Decision intelligence target: add causal decision support in two stages.
  Phase 1 builds a local, auditable causal-decision MVP around Causal Contract,
  causal-readiness QA, A/B experiment readout, and guarded action plans. Phase 2
  turns that into a governed causal inference and experimentation platform with
  stronger estimators, heterogeneous effects, quasi-experiments, and scalable
  experiment operations.
- Avoided path: do not jump directly to a web service, multi-agent orchestration,
  vector memory, or self-editing code before local safety, real evals, and human
  skill review are reliable.

### 0.1 Status Board

Status labels:

- In progress: already partially supported by current code or active report
  delivery waves.
- Planned next: should be built in Phase 1 after its dependencies are stable.
- Later: Phase 2 platformization work.
- Not started: no current implementation or plan artifact yet.

| Capability line | Status | Roadmap owner | Notes |
| --- | --- | --- | --- |
| Report contract, context, QA, chart/report delivery | In progress | P1-4 / report waves | `report_need`, `report_context`, `report_contract`, reporting QA, templates, and chart/render work are already present or underway. |
| Local Web Workbench | Planned next | P1-3 | Needs local safety/workspace foundation before full live-agent UX. |
| Causal Decision MVP | Planned next | P1-10 | First-stage executable plan exists at docs/superpowers/plans/2026-07-07-causal-decision-stage1.md. |
| Causal inference platformization | Later | P2-12 | Depends on Phase 1 causal contracts, experiment readout, evals, and governance. |
| Multi-user distributed platform | Later | P2-1..P2-11 | Must not weaken Phase 1 local safety contracts. |

## 1. Acceptance Contract

```json
{
  "intent": "Record a complete, actionable Phase 1 and Phase 2 roadmap in the repository.",
  "non_goals": [
    "No production code implementation in this planning task.",
    "No Trellis initialization or external project-management mutation.",
    "No change to existing uncommitted sandbox hardening work."
  ],
  "acceptance": [
    "A durable roadmap document exists under docs/roadmap/.",
    "Phase 1 and Phase 2 each have goals, non-goals, workstreams, dependencies, concrete tasks, acceptance checks, and verification commands.",
    "The plan explicitly covers local Web Workbench, data authorization, project workspace, safety, analysis tools, causal decision support, memory, telemetry, self-evolution, evaluation, quality gates, and distributed production architecture.",
    "The plan separates current files from planned future file locations.",
    "The repository quality gate remains green or any failure is explained with evidence."
  ],
  "forbidden": [
    "Do not describe current code as production-ready when known local safety gaps remain.",
    "Do not treat python_analysis as a security boundary.",
    "Do not promote candidate skills without eval evidence and human review in Phase 1.",
    "Do not scope Phase 2 as a thin wrapper around the CLI."
  ],
  "verify_commands": [
    ".venv/bin/python scripts/quality_gate.py",
    ".venv/bin/pytest tests/ -v",
    "python -m data_analysis_agent.evolution list"
  ],
  "review_scope": "Roadmap/spec fidelity, architecture sequencing, security boundaries, evolution gates, and whether tasks are executable.",
  "release_gate": "Quality gate green plus review of any roadmap-to-implementation spec before production code changes."
}
```

## 2. Current Ground Truth

The current project already has strong foundations:

- Runtime composition root: `src/data_analysis_agent/runtime.py`
- Agent event stream: `src/data_analysis_agent/events.py`
- Single-turn loop plus cross-turn session: `src/data_analysis_agent/agent_loop.py`,
  `src/data_analysis_agent/session.py`
- Persistent analysis kernel: `src/data_analysis_agent/kernel/manager.py`,
  `src/data_analysis_agent/kernel/kernel_main.py`
- Tool system: `src/data_analysis_agent/tools/`
- Artifact system: `src/data_analysis_agent/artifacts.py`
- Context compaction and result retrieval: `src/data_analysis_agent/context/`,
  `src/data_analysis_agent/sampling/`
- Memory and telemetry: `src/data_analysis_agent/memory/`,
  `src/data_analysis_agent/telemetry/`
- Offline evolution: `src/data_analysis_agent/evolution/`
- Quality gate: `scripts/quality_gate.py`
- Architecture manifest: `docs/ARCHITECTURE.md`

Known gaps that shape this roadmap:

- Default permissions are still local-CLI-friendly rather than local-production
  fail-closed.
- `read_file` does not yet enforce the same allowed-path contract as
  `data_profile` and `python_analysis`.
- `python_analysis` is a best-effort container, not a security boundary
  (`docs/adr/0008-sandbox-best-effort-not-security-boundary.md`).
- The local product surface is still CLI-first; no project workbench exists.
- The self-evolution loop is implemented and tested, but needs real traces,
  review UI, regression ledger, and operating cadence.
- Memory exists, but governance and user-facing memory management are thin.
- Analysis tools cover the core path, but production-grade data-quality,
  join-planning, metric-contract, chart-rendering, and report-QA tools are not
  complete.
- Current code can flag unsupported causal language in reports, but it does not
  yet model treatment, outcome, confounders, intervention, identification
  assumptions, estimators, refutations, or experiment decision rules.

## 3. Design Principles

1. Reuse the current harness. Web, eval, memory, and future services must call
   `AgentRuntime.from_config()` or an equivalent composition root, not reassemble
   their own lighter agent.
2. Make local safety visible. Data authorization, write locations, tool calls,
   approvals, and artifacts must be inspectable by the user.
3. Separate learning from acting. Self-evolution may generate candidate skills,
   eval tasks, and memory proposals; live behavior changes only after gates.
4. Remember structure, not volatile findings. Keep ADR 0004 as a hard boundary.
5. Prefer small durable contracts over broad framework churn. Add public APIs
   only when they stabilize a real workflow.
6. Do not let Phase 2 contaminate Phase 1. Multi-user distribution, remote
   connectors, and service orchestration are later unless they are needed to
   clarify Phase 1 boundaries.

## 4. Phase 1 Goal

Phase 1 is complete when a single user can run DataAnalysisAgent locally as a
dependable analysis workbench:

- authorize local CSV/Excel/Parquet files or directories;
- describe an analysis need in natural language;
- watch the agent's tool-use process in real time;
- receive reproducible artifacts and reports;
- distinguish descriptive, correlational, experimental, and causal claims;
- use a local causal-decision workflow for A/B readouts and guarded action
  recommendations;
- provide feedback;
- accumulate governed memory and trajectories;
- harvest real eval tasks;
- synthesize candidate skills;
- evaluate and manually review candidates;
- promote/retire skills with rollback and evidence.

## 5. Phase 1 Non-Goals

- No multi-tenant auth.
- No public network service.
- No remote data connectors by default.
- No autonomous code patching.
- No automatic skill promotion without human review.
- No claim that observation-only correlation is causal evidence.
- No complex observational causal estimator as the default Phase 1 path.
- No claim that the local Python sandbox is a security boundary.
- No broad frontend framework unless the local HTML/SSE approach proves
  inadequate.

## 6. Phase 1 Workstreams

### P1-0. Planning, Specs, and Release Discipline

Purpose: every broad change must have a spec, an acceptance contract, and a
verification path.

Tasks:

- P1-0.1 Create per-workstream implementation specs under
  docs/superpowers/specs/YYYY-MM-DD-*.md before production code.
- P1-0.2 For each implementation wave, create an executable plan under
  docs/superpowers/plans/YYYY-MM-DD-*.md.
- P1-0.3 For architecture/security/persistence/concurrency/Web changes, run the
  independent code review loop after implementation.
- P1-0.4 Keep `docs/ARCHITECTURE.md` manifest synchronized when source modules
  are added or removed.
- P1-0.5 Update `docs/QUALITY_BAR.md` only when quality gates change, not for
  ordinary feature plans.

Acceptance:

- Every Phase 1 code wave has a spec, a plan, focused tests, quality gate run,
  and review report.

Verification:

- `.venv/bin/python scripts/quality_gate.py`
- Review current branch diff and linked spec/plan paths.

### P1-1. Local Production Safety Baseline

Purpose: make local single-user usage safe by default, especially around data
access, file writes, telemetry, and approvals.

Tasks:

- P1-1.1 Add allowed-path enforcement to `FileReadTool`.
  - Current file: `src/data_analysis_agent/tools/file_read.py`
  - Planned change: accept `allowed_paths`, resolve symlinks, reject out-of-scope
    reads, mirror `DataProfileTool._within_allowed`.
  - Runtime wiring: pass `analysis_paths` from
    `src/data_analysis_agent/runtime.py`.
  - Tests: path in allowlist, path outside allowlist, symlink escape, missing
    file, relative path behavior.
- P1-1.2 Change local production default permission policy.
  - Current file: `src/data_analysis_agent/runtime.py`
  - Planned behavior: Web/workbench mode always has a permission engine.
  - Recommended default: allow read-only authorized tools, ask for
    `python_analysis`, `visualization`, and `html_report`, deny unknown tools.
  - Keep explicit CLI compatibility only behind a documented mode if needed.
- P1-1.3 Add a distinct config preset for local production.
  - Planned files: config/runtime additions, CLI flags.
  - Candidate names: `local_safe`, `local_dev`, `plan`, `auto`.
  - `local_safe` should be the Web Workbench default.
- P1-1.4 Tighten artifact write boundaries.
  - Ensure all user-visible file creation lands under an artifact/workspace dir.
  - `visualization` must not accept arbitrary output paths outside artifacts.
  - `html_report` already has a better containment model; use it as reference.
- P1-1.5 Keep `python_analysis` threat model explicit.
  - Do not market it as secure against adversarial users.
  - For Phase 1, protect against accidental local damage; for Phase 2, replace
    or wrap it with true isolation.
- P1-1.6 Add telemetry privacy controls.
  - Defaults for Web Workbench should display what will be stored.
  - Add retention controls for trajectories, result store, artifacts, and memory.
  - Add a sensitive-mode switch that disables trajectory input capture and memory
    writes for a run.
- P1-1.7 Add doctor checks.
  - Planned command: data-agent doctor
  - Checks: API key present, data extras installed, DAA_HOME writable, artifact
    dir writable, ECharts mode, permission preset, authorized paths, kernel
    health, local Web port availability.

Acceptance:

- A user cannot accidentally let the agent read outside the authorized files or
  directories in local production mode.
- Tool calls that execute code or write artifacts are visible and confirmable.
- Sensitive-mode runs do not write trajectories or memory unexpectedly.

Verification:

- New tests for file read allowlist and permission defaults.
- `.venv/bin/python scripts/quality_gate.py`
- Manual: run a Web/CLI local-safe session and confirm out-of-scope file reads
  fail.

### P1-2. Local Project Workspace

Purpose: make every analysis reproducible and inspectable.

Tasks:

- P1-2.1 Define a project workspace layout.
  - Suggested root: ~/.daa/projects/<project_id>/ or a user-chosen project dir.
  - Required subdirs: uploads, artifacts, sessions, results, trajectories,
    memory, eval_tasks, logs, manifests.
- P1-2.2 Add a project manifest.
  - Planned file inside each project: project.json
  - Fields: project_id, created_at, authorized_paths, uploads, artifact_dir,
    persist_path, result_store_dir, trajectories_dir, memory_dir, eval_tasks_dir,
    config preset, model id, retention policy.
- P1-2.3 Add per-run manifests.
  - Planned file: runs/<run_id>.json
  - Fields: user request, selected files, authorized paths, session id, event
    counts, tool calls, artifacts, feedback, memory writes, eval harvest
    eligibility, terminal reason, token usage, warnings.
- P1-2.4 Wire workspace paths into `AgentRuntime.from_config()`.
  - Persist path, kernel work dir, artifacts dir, result store, trajectories,
    memory, and eval tasks must follow the workspace when present.
- P1-2.5 Add CLI commands.
  - Planned commands: data-agent project init/status/list/open/history.
  - Keep commands read-only unless they explicitly create a project.

Acceptance:

- A completed analysis can be re-opened from its project folder with all
  artifacts and run metadata intact.
- The workspace tells the user which local files were authorized and which
  files were produced.

Verification:

- Unit tests for workspace path construction.
- Integration test for one run writing session, artifact, result store, and
  manifest to the same project.

### P1-3. Local Web Workbench

Purpose: provide the first real product surface for local interaction without
turning Phase 1 into a distributed service.

Tasks:

- P1-3.1 Add a local Web entrypoint.
  - Planned package: src/data_analysis_agent/web/
  - Planned files: server.py, event_codec.py, schemas.py, assets/index.html,
    assets/app.js, assets/style.css.
  - Planned command: data-agent web or python -m data_analysis_agent.web.
- P1-3.2 Use localhost-only serving.
  - Bind only to 127.0.0.1 by default.
  - Do not expose public LAN binding without an explicit unsafe flag and warning.
- P1-3.3 Implement data selection.
  - Upload CSV/XLSX/XLS/Parquet into workspace uploads.
  - Allow explicit local path or directory authorization.
  - Show authorized paths before running.
  - For directories, require confirmation because all child files become
    readable by authorized tools.
- P1-3.4 Implement analysis run creation.
  - Input: natural language request, selected files/dirs, config preset,
    sensitive-mode toggle.
  - Backend creates/reuses an `AgentRuntime` with workspace paths and
    `analysis_paths`.
- P1-3.5 Stream agent events to the browser.
  - Prefer Server-Sent Events for MVP.
  - Event codec maps `RequestStartEvent`, `StreamTextEvent`, `ToolUseEvent`,
    `ToolResultEvent`, `StateChangeEvent`, `UsageEvent`, `ErrorEvent`,
    `CompleteEvent` into stable JSON.
- P1-3.6 Build the HTML interaction surface.
  - Panels: data sources, prompt input, live answer, tool timeline, artifacts,
    feedback.
  - Tool cards: name, params, status, duration if available, result summary,
    error state, artifacts.
  - Final state: terminal reason, token usage, generated files.
- P1-3.7 Add approval UI.
  - When permission engine returns ASK, show tool and params in the browser.
  - User chooses allow/deny.
  - A no-response timeout should deny, not allow.
- P1-3.8 Add feedback UI.
  - Good, bad, rephrase/needs-fix.
  - Feed existing telemetry feedback path.
  - Capture optional short comment.
- P1-3.9 Serve artifacts safely.
  - Only serve files inside workspace artifacts.
  - Do not serve arbitrary file paths directly.
  - HTML report opens in a new tab from artifact route.

Acceptance:

- User can open a local page, select/upload a CSV or Excel file, enter a
  natural-language request, watch live tool calls, see final answer, open
  generated artifacts, and submit feedback.
- Web uses the same runtime/tool registry as CLI.
- No Web endpoint can read or serve files outside authorized workspace/artifact
  boundaries.

Verification:

- Unit tests for event codec.
- Unit tests for upload/path authorization.
- Integration test with fake client: Web run emits SSE events through complete.
- Manual smoke: data-agent web, upload sample CSV, run a simple analysis, open
  artifact.

### P1-4. Data Analysis Tool Hardening

Purpose: move from general code execution toward reliable analysis workflows.

Tasks:

- P1-4.1 Data quality tool.
  - Planned capability: missingness, duplicate rows, duplicate keys, type
    anomalies, numeric outliers, categorical cardinality, date parseability,
    constant columns, suspicious IDs.
  - Output must be structured metadata plus readable summary.
- P1-4.2 Join planner.
  - Planned capability: inspect multiple files/sheets, candidate join keys,
    uniqueness, row multiplication risk, null-key risk, recommended join order.
- P1-4.3 Metric contract tool.
  - Planned capability: represent metric name, numerator, denominator, filters,
    time window, grain, timezone, exclusions, owner confirmation status.
  - Connect to memory `metric_definition`.
- P1-4.4 Safer chart renderer.
  - Replace code-string-first visualization with structured chart requests.
  - Tool should write artifacts directly and report paths through metadata.
  - Keep custom code path only as advanced explicit mode, gated by approval.
- P1-4.5 Report QA.
  - Check that reports include source files, metric definitions, time windows,
    limitations, caveats, and next actions.
  - Flag unsupported causal claims or unverified external claims.
- P1-4.6 Improve `nl_query`.
  - Treat current heuristic generation as assistive, not authoritative.
  - Add schema-aware column selection using `data_profile` output.
  - Avoid embedding connection strings or secrets into generated code.
- P1-4.7 Excel-first workflows.
  - Multi-sheet discovery, sheet selection, cross-sheet joins, workbook summary,
    common date/amount/account columns, hidden empty header rows.

Acceptance:

- A non-expert user can ask common business-analysis questions and receive
  source-grounded, caveated, reproducible output without manually writing code.
- Generated reports make data quality and metric caveats visible.

Verification:

- Add fixture datasets under examples/eval_tasks or generated seed assets.
- Add method/structure assertions for data quality, join planning, chart
  creation, and report QA.

### P1-5. Memory Governance

Purpose: make memory useful without silently accumulating stale or sensitive
facts.

Tasks:

- P1-5.1 Add memory management CLI.
  - Planned commands: data-agent memory list/search/show/confirm/forget/export.
- P1-5.2 Add Web memory panel.
  - Show surfaced memory during a run.
  - Let user confirm, correct, or forget a memory item.
- P1-5.3 Add memory scopes.
  - Global preference, project preference, dataset profile, metric definition,
    open concern.
  - Retrieval should respect scope priority: current project/dataset before
    global.
- P1-5.4 Add conflict handling.
  - Same metric name with incompatible definitions must become an explicit
    conflict, not silent overwrite.
- P1-5.5 Add retention/export.
  - User can export memory JSONL.
  - User can delete project-local memory.
  - Sensitive-mode run must not write memory.
- P1-5.6 Add memory audit metadata.
  - Source session, source run, confirmation status, last surfaced, accepted
    uses, conflict status.

Acceptance:

- User can see why a remembered item was used and can remove or correct it.
- Metric definitions are never silently confirmed merely because they were
  surfaced.

Verification:

- Existing memory tests plus new scope/conflict/forget/export tests.

### P1-6. Real Self-Evolution Loop

Purpose: turn the current evolution skeleton into an operating loop that learns
from real local use.

Tasks:

- P1-6.1 Generate real local trajectories.
  - Use `examples/training_data/week1_seed_assets/` to run realistic analysis
    tasks through the actual agent.
  - Store trajectories in a controlled test DAA_HOME to avoid polluting user
    memory.
- P1-6.2 Harvest eval tasks regularly.
  - Run harvest-eval against authorized fixture directories.
  - Log skipped files and ambiguous basenames.
- P1-6.3 Improve candidate synthesis prompts.
  - Include `input_digest`, tool sequence, failure patterns, user feedback,
    artifact types, and metric caveats.
  - Require candidate skills to avoid hard-coded values and dataset-specific
    file names.
- P1-6.4 Add review workbench.
  - CLI and later Web view for candidate skills.
  - Show source trajectories, generated instructions, allowed tools, eval
    verdict, risks, and proposed status change.
- P1-6.5 Add regression ledger.
  - Track every active skill's eval tasks, pass rate, tool cost, failures,
    promotion date, reviewer, rollback history.
- P1-6.6 Add promotion policy.
  - Candidate -> active only when eval passes and user approves.
  - Candidate -> retired when eval fails or user rejects.
  - Active -> retired when regression gate fails.
- P1-6.7 Add operating cadence.
  - Daily/weekly local command: harvest-eval, synthesize, evaluate, review.
  - No automatic live behavior changes without approval.
- P1-6.8 Keep self-editing code out of Phase 1.
  - Candidate output is declarative skill JSON only.
  - Source code patches require separate human-reviewed engineering work.

Acceptance:

- At least one active skill can be traced back to real trajectories, eval tasks,
  review decision, and promotion record.
- Candidate skills cannot silently enter the live registry.

Verification:

- `python -m data_analysis_agent.evolution harvest-eval --data-search-path ...`
- `python -m data_analysis_agent.evolution synthesize`
- `python -m data_analysis_agent.evolution evaluate`
- New review CLI tests.

### P1-7. Behavior Evaluation and Quality Gates

Purpose: extend quality beyond code correctness into agent behavior.

Tasks:

- P1-7.1 Keep commit gate fast.
  - `scripts/quality_gate.py` remains ruff/format/mypy/pytest/drift.
- P1-7.2 Add optional eval gate.
  - Planned command: scripts/eval_gate.py
  - Runs offline/local behavior evals on selected tasks.
  - Not part of every commit until cost and determinism are controlled.
- P1-7.3 Add smoke suites.
  - Web smoke with fake client.
  - Real tool smoke on seed assets.
  - HTML report generation smoke.
  - Memory write/read smoke.
  - Evolution cold-start smoke.
- P1-7.4 Add quality metrics.
  - Tool error rate, max turns hit, final status, token usage, artifact count,
    user feedback, eval pass rate, run duration.
- P1-7.5 Add failure taxonomy.
  - Data access failure, schema misunderstanding, wrong metric, tool crash,
    sandbox denial, report quality failure, hallucinated claim, over-cost.

Acceptance:

- A release can state both code quality and behavior quality evidence.

Verification:

- Quality gate plus optional eval gate output.

### P1-8. Local Observability and Operations

Purpose: make failures diagnosable for a local user.

Tasks:

- P1-8.1 Add structured local logs.
  - Separate user-visible run manifest from developer logs.
  - Do not log secrets.
- P1-8.2 Add run status.
  - Current state, active tool, elapsed time, cancel status, last error.
- P1-8.3 Add cancellation.
  - Web cancel button should close event stream, shut down kernel as needed,
    and ledger-close the session.
- P1-8.4 Add cleanup commands.
  - Remove expired result-store entries, old artifacts, old trajectories,
    project temp files.
- P1-8.5 Add crash recovery notes.
  - Kernel restarted, variables lost, fallback to stateless, permission denied,
    context compacted.

Acceptance:

- A failed local run has enough metadata to diagnose what happened without
  reading raw terminal output.

### P1-9. Packaging and User Documentation

Purpose: make the local product installable and usable.

Tasks:

- P1-9.1 Update README after features land.
  - Do not document planned Web commands as available until implemented.
- P1-9.2 Add docs for local-safe mode.
- P1-9.3 Add docs for Web Workbench.
- P1-9.4 Add docs for project workspace.
- P1-9.5 Add docs for evolution workflow.
- P1-9.6 Add troubleshooting guide.
  - Missing API key, missing pandas/openpyxl, denied file path, no artifacts,
    kernel restart, ECharts offline, port in use.

Acceptance:

- A new user can install, run Web Workbench, analyze a sample file, inspect
  artifacts, and understand where data is stored.

### P1-10. Causal Decision MVP

Status: planned next.

Purpose: move from objective data reporting to decision-support workflows while
strictly separating correlation, experiment evidence, causal assumptions, and
recommended actions.

Dependencies:

- P1-4 report/data-quality/report-QA foundations.
- P1-7 behavior eval and failure taxonomy.
- Existing reporting traceability model and process context.
- Week-1 mobile app A/B seed asset as the first experiment-readout fixture.

Tasks:

- P1-10.1 Add a Causal Contract design and domain model.
  - Required concepts: decision question, treatment/action, outcome, unit,
    time window, population, assignment mechanism, candidate confounders,
    business assumptions, external events, data limitations, and decision
    threshold.
  - Phase 1 model must be pure stdlib and deterministic, mirroring the reporting
    domain-layer approach.
- P1-10.2 Add causal-intent parsing.
  - Detect requests such as "为什么下降", "是否导致", "能否提升",
    "实验组是否有效", "下一步怎么做".
  - Route them to a causal-decision workflow without treating inferred intent as
    explicit fact.
- P1-10.3 Add causal-readiness QA.
  - Block causal-ready labels when treatment/outcome/unit/time window are
    missing.
  - Mark observation-only analysis as correlation or hypothesis unless an
    experiment or accepted identification strategy exists.
  - Require caveats for confounding, selection bias, spillover, seasonality,
    external events, and sample-ratio mismatch.
- P1-10.4 Add A/B experiment readout MVP.
  - Analyze randomized experiment data: group balance, sample ratio mismatch,
    conversion/mean lift, confidence interval, guardrail metrics, segment
    checks, and decision recommendation.
  - Output must include "ship / do not ship / inconclusive / needs more data"
    with explicit decision criteria.
- P1-10.5 Add action-plan output.
  - Translate causal or experimental findings into operational actions only when
    evidence and assumptions support them.
  - Each action must include expected mechanism, target population, risk,
    monitoring metric, rollback trigger, and next experiment.
- P1-10.6 Add report integration.
  - Render causal contracts, readiness state, estimates, refutations/caveats,
    experiment decisions, and action plans into Report Document / HTML output.
- P1-10.7 Add eval fixtures.
  - Use the existing mobile_app_ab_test seed dataset first.
  - Add at least 8-12 causal/experiment tasks covering: randomized experiment,
    suspicious imbalance, segment heterogeneity, observation-only correlation,
    missing outcome, missing treatment, external-event caveat, and inconclusive
    decision.
- P1-10.8 Keep external causal libraries out of the first implementation slice.
  - Phase 1 may use pandas/numpy calculations for A/B readout.
  - DoWhy/EconML/CausalML integration is deferred to Phase 2 or a later Phase 1
    extension after the contract/QA surface is stable.

Acceptance:

- A report can clearly say whether it is descriptive, correlational,
  experimental, or causal-assumption-based.
- A/B experiment tasks produce auditable estimates, caveats, and a bounded
  decision recommendation.
- Observation-only tasks cannot pass as causal-ready without explicit accepted
  assumptions.
- Every recommended action is tied to evidence, assumptions, and monitoring.

Verification:

- Focused tests for causal contract serialization, causal-intent parsing,
  readiness QA, A/B readout calculations, and report adapter output.
- Seed-task behavior evals for experiment and observation-only cases.
- `.venv/bin/python scripts/quality_gate.py`

## 7. Phase 1 Suggested Milestones

### Milestone 1A: Safety and Workspace Baseline

Must include:

- `FileReadTool` allowlist.
- local-safe permission preset.
- workspace layout and project/run manifests.
- doctor command.
- tests and quality gate.

Exit criteria:

- Out-of-scope file read attempts fail.
- A run writes all durable state under one workspace.

### Milestone 1B: Local Web MVP

Must include:

- localhost Web server.
- upload/path authorization.
- natural-language prompt.
- SSE event timeline.
- artifact listing.
- feedback buttons.

Exit criteria:

- User can complete one CSV and one Excel analysis from the browser.
- Web run uses the same runtime/tool registry as CLI.

### Milestone 1C: Analysis Tooling Upgrade

Must include:

- data quality tool.
- join planner.
- safer chart renderer.
- report QA.
- schema-aware NL query improvements.

Exit criteria:

- Seed business tasks can be solved with fewer generic code-generation steps
  and better caveat coverage.

### Milestone 1D: Governed Self-Evolution

Must include:

- real trajectory generation.
- eval harvesting from real runs.
- candidate skill review UI/CLI.
- regression ledger.
- promotion/retire policy.

Exit criteria:

- At least one skill is promoted with trace -> eval -> review -> active
  evidence.

### Milestone 1E: Local Release Candidate

Must include:

- docs.
- optional eval gate.
- cleanup tools.
- local operations guide.
- full review loop.

Exit criteria:

- A single user can use the system as a durable local workbench for real
  analysis projects.

### Milestone 1F: Causal Decision MVP

Status: planned next after Milestone 1C foundations.

Must include:

- Causal Contract domain model.
- causal-intent routing.
- causal-readiness QA.
- A/B experiment readout MVP.
- action-plan output.
- causal report integration.
- causal/experiment eval fixtures.

Exit criteria:

- The agent can turn an experiment dataset into a decision-ready readout with
  caveats and an action plan.
- The agent refuses to upgrade correlation-only evidence into causal claims.

## 8. Phase 1 Completion Checklist

- [ ] Local-safe mode is the default for Web Workbench.
- [ ] `read_file`, `data_profile`, `python_analysis`, artifacts, result store,
      and uploads are path-scoped.
- [ ] Web Workbench runs only on localhost by default.
- [ ] User can upload/select CSV and Excel files.
- [ ] User can authorize a directory with explicit confirmation.
- [ ] Browser shows live model/tool/event progress.
- [ ] Browser supports approval decisions for ASK tools.
- [ ] Browser shows artifact list and opens reports.
- [ ] Browser supports feedback.
- [ ] Every run has a manifest.
- [ ] Sensitive-mode run suppresses telemetry/memory writes.
- [ ] Causal Decision MVP marks descriptive/correlation/experimental/causal
      claim levels separately.
- [ ] A/B experiment readout supports balance checks, lift estimates, caveats,
      and bounded decision recommendation.
- [ ] Observation-only causal requests produce hypothesis/readiness output
      unless identification assumptions are explicit.
- [ ] Memory can be listed, confirmed, corrected, forgotten, and exported.
- [ ] At least 30-50 eval tasks exist across representative local scenarios.
- [ ] Candidate skills need eval and human approval before activation.
- [ ] Active skills have regression ledger entries.
- [ ] Quality gate is green.
- [ ] Release notes state the sandbox threat model honestly.

## 9. Phase 2 Goal

Phase 2 is complete when DataAnalysisAgent is a distributed production platform:

- multiple users and projects;
- authenticated access;
- isolated execution workers;
- managed data connectors;
- durable task queues;
- trace and artifact services;
- governed memory service;
- centralized eval and skill registry;
- canary skill rollout and rollback;
- production observability and SLOs;
- governed causal inference, experimentation, and action-strategy services;
- compliance-ready audit trails.

## 10. Phase 2 Non-Goals

- No weakening Phase 1 safety contracts for speed.
- No running untrusted code in the Phase 1 best-effort Python sandbox.
- No automatic global skill rollout without canary and rollback.
- No shared tenant memory without ACL and audit.
- No direct exposure of local file paths or secrets in traces.

## 11. Phase 2 Prerequisites

Do not start distributed production build until Phase 1 has:

- local-safe path and permission model;
- project/run manifest contract;
- Web event codec contract;
- artifact contract;
- memory governance contract;
- eval task and skill promotion contract;
- at least one real skill promotion history;
- Phase 1 Causal Contract and causal-readiness QA, if decision intelligence is
  in scope for the Phase 2 build;
- documented threat model and release checklist.

## 12. Phase 2 Workstreams

### P2-1. Service Architecture and Control Plane

Purpose: separate product API, scheduling, execution, storage, and evolution.

Tasks:

- P2-1.1 Define service boundaries.
  - API gateway.
  - Session service.
  - Project service.
  - Run/job service.
  - Agent execution worker.
  - Artifact service.
  - Memory service.
  - Evaluation service.
  - Skill registry service.
  - Policy service.
- P2-1.2 Move from in-process `AgentRuntime` to job-scoped runtime factory.
- P2-1.3 Add task queue.
  - Required: enqueue, cancel, retry, timeout, dead letter, idempotency key.
- P2-1.4 Add event bus.
  - Stable event schema based on Phase 1 Web event codec.
  - Frontend subscribes by run id.
- P2-1.5 Add API versioning.
  - Version every public request/event schema.

Acceptance:

- A run can be created through API, executed by a worker, streamed to UI, and
  recovered after API process restart.

### P2-2. Secure Distributed Execution

Purpose: replace Phase 1 best-effort local sandbox with real isolation.

Tasks:

- P2-2.1 Container or microVM execution.
  - Per job or per tenant.
  - Read-only data mounts.
  - Artifact write-only/output mount.
  - No network by default.
- P2-2.2 Resource limits.
  - CPU, memory, disk, wall-clock, process count, output size.
- P2-2.3 Network policy.
  - Default deny.
  - Connector-specific allow rules.
  - Egress logging.
- P2-2.4 Secret isolation.
  - Workers receive short-lived scoped credentials only.
  - No secrets in prompts, traces, artifacts, or logs.
- P2-2.5 Execution image management.
  - Versioned images.
  - Dependency allowlist.
  - Security scanning.
- P2-2.6 Kernel lifecycle.
  - Decide per-run vs per-session kernel.
  - Define cleanup and state snapshot policy.

Acceptance:

- A malicious or broken analysis cannot read another tenant's data, exhaust
  host resources, or exfiltrate without policy approval.

### P2-3. Multi-Tenant Identity, Authorization, and Policy

Purpose: enforce tenant, project, data, and tool boundaries.

Tasks:

- P2-3.1 Add identity model.
  - Organization, user, role, project, service account.
- P2-3.2 Add RBAC/ABAC.
  - Permissions for data source, run, artifact, memory, skill, eval, admin.
- P2-3.3 Add policy engine.
  - Tool allow/ask/deny by tenant/project/data sensitivity.
  - Network and connector rules.
  - Memory write rules.
- P2-3.4 Add audit logs.
  - Who accessed what data.
  - Which tool ran with which policy result.
  - Which skill version was active.
  - Which artifacts were exported.
- P2-3.5 Add approval workflows.
  - Sensitive tool calls.
  - Skill promotion.
  - Connector authorization.

Acceptance:

- Tenant A cannot see Tenant B's data, runs, memory, artifacts, or skills
  unless policy explicitly permits it.

### P2-4. Distributed Storage Layer

Purpose: replace local JSONL/tempdir state with durable production stores.

Tasks:

- P2-4.1 Session store.
  - Transactional, append-only, ledger-closed.
- P2-4.2 Run/event store.
  - Event stream persisted by run id.
  - Supports replay and debugging.
- P2-4.3 Artifact store.
  - Object storage, signed URLs, TTL, retention.
- P2-4.4 Result store.
  - Large result paging, query, TTL, tenant isolation.
- P2-4.5 Memory store.
  - Scoped, versioned, auditable.
- P2-4.6 Skill registry store.
  - Candidate/active/retired versions, eval reports, rollout status.
- P2-4.7 Eval store.
  - Tasks, fixtures, outcomes, regression history.

Acceptance:

- Service restart does not lose runs, artifacts, memory, skills, or eval state.

### P2-5. Managed Data Connectors

Purpose: support real production data without handing raw credentials to the
agent.

Tasks:

- P2-5.1 Connector abstraction.
  - Files, object storage, SQL warehouse, spreadsheets, BI exports.
- P2-5.2 Credential vault integration.
- P2-5.3 Metadata catalog.
  - Schema, table names, columns, statistics, freshness, sensitivity labels.
- P2-5.4 Query budget and cost controls.
- P2-5.5 Connector-specific sampling/profile tools.
- P2-5.6 Read-only default.
- P2-5.7 Optional writeback/export with explicit approval.

Acceptance:

- Agent can profile and analyze authorized production data sources without
  seeing raw long-lived credentials.

### P2-6. Production Memory Service

Purpose: turn local memory into a governed, scoped, auditable learning layer.

Tasks:

- P2-6.1 Scope hierarchy.
  - Global system, organization, team, project, user, dataset, connector.
- P2-6.2 Memory types.
  - Dataset profile, metric definition, analysis preference, open concern,
    failure heuristic, report style, connector note.
- P2-6.3 Retrieval policy.
  - Permission-aware, freshness-aware, conflict-aware.
- P2-6.4 Write policy.
  - Some memory requires user confirmation.
  - Sensitive projects may disable memory.
- P2-6.5 Forget/export/audit.
- P2-6.6 Memory evaluation.
  - Track when memory helped, hurt, or was corrected.
- P2-6.7 Anti-homogenization.
  - Avoid one team's preferences contaminating another team's workflows.

Acceptance:

- Memory improves repeated analysis while preserving tenant boundaries and
  providing user control.

### P2-7. Production Self-Evolution Platform

Purpose: make improvement continuous but controlled.

Tasks:

- P2-7.1 Trace lake.
  - Normalize trajectories from many users/runs.
  - Redact secrets and sensitive values.
- P2-7.2 Failure taxonomy.
  - Classify failures across runs.
- P2-7.3 Candidate generation.
  - Candidate skills, memory proposals, eval tasks, prompt improvements.
- P2-7.4 Eval farm.
  - Run A/B across frozen tasks at scale.
  - Track cost, latency, pass rate, tool error, report QA.
- P2-7.5 Human review queues.
  - Data analyst review, security review, engineering review as needed.
- P2-7.6 Skill registry and rollout.
  - Candidate -> canary -> active -> retired.
  - Versioned rollout by org/project/user cohort.
- P2-7.7 Rollback.
  - Automatic rollback on regression thresholds.
- P2-7.8 Dynamic curriculum.
  - Generate new evals from common failures and uncovered tasks.
- P2-7.9 Code evolution only after mature gates.
  - Source code patches require separate code review, tests, security scan,
    canary, rollback.

Acceptance:

- The platform can improve skills from aggregate evidence without letting an
  unsafe or low-quality candidate affect all users.

### P2-8. Behavior Evaluation and Quality Platform

Purpose: make agent behavior measurable in production.

Tasks:

- P2-8.1 Golden eval suites by domain.
  - Finance, sales, ops, support, product analytics, HR, supply chain.
- P2-8.2 Report-quality rubrics.
  - Source grounding, metric correctness, caveats, actionability, clarity.
- P2-8.3 Safety evals.
  - Data exfiltration, unauthorized path/data access, prompt injection,
    dangerous tool use.
- P2-8.4 Cost and latency evals.
- P2-8.5 Online shadow runs.
- P2-8.6 Canary comparisons.
- P2-8.7 Human feedback calibration.
- P2-8.8 Eval dashboard.

Acceptance:

- Every production release and skill rollout has measurable behavior evidence,
  not just unit tests.

### P2-9. Observability, Reliability, and Operations

Purpose: run the system like a production service.

Tasks:

- P2-9.1 Structured logs.
- P2-9.2 Distributed tracing.
- P2-9.3 Metrics.
  - Queue delay, run duration, token cost, tool latency, error rate, kernel
    restart rate, artifact failures, connector failures, permission denials.
- P2-9.4 SLOs.
  - Availability, run start latency, completion rate, artifact delivery.
- P2-9.5 Alerts.
- P2-9.6 Admin dashboard.
- P2-9.7 Incident runbooks.
- P2-9.8 Backup/restore.
- P2-9.9 Disaster recovery.

Acceptance:

- Operators can diagnose failed or slow runs without reading raw user data.

### P2-10. Frontend and Collaboration Product

Purpose: evolve Phase 1 Web Workbench into a production collaborative product.

Tasks:

- P2-10.1 Authenticated Web app.
- P2-10.2 Project dashboard.
- P2-10.3 Run history and replay.
- P2-10.4 Artifact browser.
- P2-10.5 Commenting and approvals.
- P2-10.6 Shared metric definitions.
- P2-10.7 Memory review UI.
- P2-10.8 Skill review UI.
- P2-10.9 Admin/policy UI.
- P2-10.10 Export/share controls.

Acceptance:

- Teams can collaborate on analysis outputs without breaking governance.

### P2-11. Deployment, Compliance, and Cost

Purpose: make the distributed system operable in real environments.

Tasks:

- P2-11.1 Deployment targets.
  - Local dev, staging, production.
- P2-11.2 Infrastructure as code.
- P2-11.3 Secret management.
- P2-11.4 Dependency/image scanning.
- P2-11.5 Data retention policies.
- P2-11.6 Compliance audit exports.
- P2-11.7 Cost accounting by org/project/run/model/tool.
- P2-11.8 Budget enforcement.
- P2-11.9 Model routing and fallback.
- P2-11.10 Disaster recovery drills.

Acceptance:

- Production deployment has repeatable environment setup, auditable security
  posture, and cost visibility.

### P2-12. Causal Inference and Experimentation Platform

Status: later.

Purpose: evolve the Phase 1 Causal Decision MVP into a governed production
capability for causal inference, quasi-experiments, heterogeneous treatment
effects, and experiment operations.

Tasks:

- P2-12.1 Add a causal inference service boundary.
  - Version causal contracts, causal graphs, estimators, refutations,
    experiment readouts, and decisions.
  - Store assumptions and user approvals as first-class audit objects.
- P2-12.2 Integrate mature causal libraries behind adapters.
  - Candidate families: DoWhy-style identify/estimate/refute workflow,
    EconML-style heterogeneous treatment effect estimation, CausalML-style
    uplift modeling, and causal-discovery libraries for hypothesis generation.
  - Library output must be normalized into project-owned causal result models.
- P2-12.3 Add observational causal methods.
  - Matching/weighting, regression adjustment, difference-in-differences,
    interrupted time series, synthetic controls, instrumental variables where
    justified.
  - Each method requires explicit data requirements and falsification checks.
- P2-12.4 Add heterogeneous effect and targeting strategy.
  - Estimate which users, accounts, regions, products, or channels respond most
    to an action.
  - Require guardrails against unfair or unstable targeting.
- P2-12.5 Add experiment design and operations.
  - Power/MDE planning, randomization unit, stratification, holdout,
    guardrails, stopping rules, spillover risks, and launch checklist.
- P2-12.6 Add experiment registry and result service.
  - Track hypothesis, treatment, owner, launch date, data source, metrics,
    decision, follow-up action, and post-launch monitoring.
- P2-12.7 Add causal eval suites.
  - Synthetic datasets with known ground truth.
  - Realistic business fixtures with known limitations.
  - Regression checks for overclaiming, bad adjustment, leakage, and unsupported
    action recommendations.

Acceptance:

- Causal inference outputs are auditable from business question to assumptions,
  estimator, sensitivity/refutation checks, and final decision.
- The platform supports both randomized experiments and clearly labeled
  observational causal analyses.
- No causal strategy can be marked production-ready without method-specific
  checks and human review.

## 13. Phase 2 Suggested Milestones

### Milestone 2A: Service Skeleton

- API, job queue, worker, event stream, durable stores.
- Reuse Phase 1 event/run/workspace contracts.

### Milestone 2B: Secure Execution

- Container or microVM isolation.
- Mount policies.
- Network deny-by-default.
- Resource limits.

### Milestone 2C: Multi-Tenant Governance

- Identity, RBAC/ABAC, audit, policy engine.

### Milestone 2D: Managed Connectors

- Read-only connectors, metadata catalog, cost controls.

### Milestone 2E: Distributed Memory and Evaluation

- Memory service, eval farm, skill registry, canary rollout.

### Milestone 2F: Production Product and Operations

- Collaborative frontend, dashboards, SLOs, alerts, compliance, cost controls.

### Milestone 2G: Causal Strategy Platform

- causal inference service.
- experiment registry.
- estimator adapters.
- heterogeneous treatment effect workflows.
- causal eval suite.
- human review and audit.

## 14. Phase 2 Completion Checklist

- [ ] All Phase 1 contracts exist and are stable.
- [ ] API and worker processes are separate.
- [ ] Runs are queued and resumable.
- [ ] Execution is isolated per job/tenant.
- [ ] Data connectors use scoped credentials.
- [ ] Event streams are durable and replayable.
- [ ] Artifacts are tenant-isolated.
- [ ] Memory is ACL-scoped and auditable.
- [ ] Eval farm exists.
- [ ] Skill registry supports versioning, canary, rollback.
- [ ] Policy engine gates tools, data, memory, and network access.
- [ ] Causal inference and experiment workflows have versioned assumptions,
      estimators, refutations, decisions, and audit trails.
- [ ] Observability covers logs, metrics, traces, and alerts.
- [ ] SLOs and incident runbooks exist.
- [ ] Cost attribution and budgets exist.
- [ ] Compliance/audit export exists.

## 15. Recommended Implementation Order

1. P1-1 local safety baseline.
2. P1-2 project workspace.
3. P1-3 Local Web Workbench MVP.
4. P1-4 data-analysis tool hardening.
5. P1-7 eval/behavior gates.
6. P1-10 Causal Decision MVP.
7. P1-5 memory governance.
8. P1-6 governed self-evolution.
9. P1-8 local operations.
10. P1-9 docs and release candidate.
11. Phase 2 only after Phase 1 completion evidence exists.

## 16. Review Requirements

Every implementation wave touching the following areas requires independent
clean-context code review:

- permissions;
- sandbox or kernel;
- file access;
- artifact serving;
- Web server endpoints;
- persistence;
- memory writes;
- telemetry and redaction;
- evolution promotion;
- eval gates;
- causal contracts, estimators, experiment analysis, and action recommendations;
- distributed execution;
- authentication/authorization;
- connectors and secrets.

Reviewer must remain report-only. Implementation agent fixes. A new reviewer
re-checks until no must-fix findings remain.

## 17. Baseline Risks

| Risk | Phase | Severity | Mitigation |
| --- | --- | --- | --- |
| Treating best-effort sandbox as secure | 1/2 | Blocking | Keep ADR 0008 visible; add true isolation in Phase 2 |
| Web layer accidentally becomes public service | 1 | Blocking | Bind localhost only; unsafe flag requires warning |
| Future planned paths break drift checks | 1 | Major | Do not backtick non-existing paths until created |
| Skill overfits one dataset | 1/2 | Major | Real eval tasks, sample gates, human review |
| Memory stores stale values | 1/2 | Major | ADR 0004: structure not volatile numbers |
| Telemetry captures sensitive details | 1/2 | Major | Sensitive mode, redaction, retention, audit |
| Correlation is mislabeled as causation | 1/2 | Blocking | Causal Contract + causal-readiness QA + report caveats |
| Experiment decision ignores imbalance or guardrails | 1/2 | Major | A/B readout must check balance, SRM, guardrails, and inconclusive state |
| Observational causal estimator gives false confidence | 2 | Blocking | Method-specific assumptions, refutations, sensitivity checks, and human review |
| Distributed rollout spreads bad skill | 2 | Blocking | Canary, rollback, eval farm, policy |
| Multi-tenant data leakage | 2 | Blocking | RBAC/ABAC, isolated execution, scoped credentials |

## 18. Next Action

The next concrete implementation spec should be:

Title: Phase 1A Local Safety and Workspace Baseline

Must cover:

- allowed-path enforcement for `read_file`;
- local-safe permission preset;
- project workspace layout;
- run manifest schema;
- doctor command;
- tests and quality gate;
- review packet.

Do not start the Web Workbench implementation until Phase 1A has a stable
project/workspace and path-authorization contract. The Web layer should sit on
top of that contract, not invent its own.
