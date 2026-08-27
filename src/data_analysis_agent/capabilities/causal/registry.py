"""causal 能力域注册表(v2 委托式迁移)。

三个能力,显式维护 v1 因果决策支持的「分析 / 推断」子能力边界:

- ``causal_analyze``(分析子能力):问题 → 因果意图识别 → CausalContract 建模 →
  因果就绪 QA。镜像 v1 ``causal_contract`` + ``causal_qa`` 工具(输入 schema 与
  行为一致),委托纯 stdlib 领域层 ``causal.intent``/``causal.model``/``causal.qa``。
- ``causal_estimate``(推断子能力):效应估计 / 实验 readout → 有界行动计划。镜像
  v1 ``experiment_readout`` + ``causal_action_plan``,委托 ``causal.experiment``。
- ``causal_report``:contract + qa + readout(+action_plan)→ ReportDocument,经
  ``causal.report_adapter`` 适配后走 reporting QA 闸交付。镜像 v1 ``causal_report``。

全部只读、确定性;推断能力绝不放大分析能力的结论(观察性证据到不了
experiment_ready,未就绪不给因果结论)。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from data_analysis_agent.causal.experiment import build_action_plan, compute_readout
from data_analysis_agent.causal.intent import infer_claim_level, parse_causal_intent
from data_analysis_agent.causal.model import (
    ActionPlan,
    AssignmentMechanism,
    CausalContract,
    CausalQAReport,
    ExperimentReadout,
    OutcomeKind,
)
from data_analysis_agent.causal.qa import run_causal_qa
from data_analysis_agent.causal.report_adapter import to_report_document

from ..contracts import (
    CapabilityError,
    CapabilityHandler,
    CapabilityOutput,
    CapabilityRegistry,
    CapabilitySpec,
    OutputKind,
    Permission,
)

_ASSIGN_MAP: dict[str, AssignmentMechanism] = {
    "randomized": AssignmentMechanism.RANDOMIZED,
    "quasi_experiment": AssignmentMechanism.QUASI_EXPERIMENT,
    "self_selection": AssignmentMechanism.SELF_SELECTION,
    "unknown": AssignmentMechanism.UNKNOWN,
}

_MAX_TREATMENT_ARMS = 10  # df 表上限(k≤11 → df≤10),镜像 experiment_readout 工具
_OUTCOME_KINDS = ("auto", "proportion", "mean")

_ERROR_CODES = ("validation_error", "execution_error")

# ----------------------------- 输入 schema(镜像 v1 工具) -----------------------------

_ANALYZE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "The user's causal/decision question.",
        },
        "business_assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Identifiability/ignorability/SUTVA assumptions (explicit only if user-confirmed)."
            ),
        },
        "external_events": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concurrent confounders in the analysis window.",
        },
        "treatment_column": {"type": "string"},
        "control_arm": {"type": "string"},
        "treatment_arms": {"type": "array", "items": {"type": "string"}},
        "outcome_columns": {"type": "array", "items": {"type": "string"}},
        "guardrail_columns": {"type": "array", "items": {"type": "string"}},
        "segment_columns": {"type": "array", "items": {"type": "string"}},
        "assignment_mechanism": {
            "type": "string",
            "enum": ["randomized", "quasi_experiment", "self_selection", "unknown"],
        },
        "decision_threshold": {"type": "number"},
        "min_sample_size": {"type": "integer"},
    },
    "required": ["question"],
}

_ESTIMATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "Optional contract question carried onto the readout label.",
        },
        "records": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Row-wise records; alternative to columns.",
        },
        "columns": {
            "type": "object",
            "description": "Columnar data {column_name: [values]}; alternative to records.",
        },
        "control_group": {
            "type": "string",
            "description": "Control arm value in group_column.",
        },
        "treatment_groups": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Treatment arm values in group_column.",
        },
        "group_column": {"type": "string", "description": "Column holding the arm label."},
        "outcome_column": {
            "type": "string",
            "description": "Column holding the outcome metric.",
        },
        "outcome_kind": {
            "type": "string",
            "enum": ["auto", "proportion", "mean"],
            "description": "auto: detect binary->proportion else mean.",
        },
        "guardrail_columns": {"type": "array", "items": {"type": "string"}},
        "guardrail_directions": {
            "type": "object",
            "description": "{guardrail_column: higher_is_worse|lower_is_worse}.",
        },
        "segment_columns": {"type": "array", "items": {"type": "string"}},
        "expected_ratio": {
            "type": "array",
            "items": {"type": "number"},
            "description": "Expected allocation ratios across [control, *treatments].",
        },
        "decision_threshold": {
            "type": "number",
            "description": "Relative-effect threshold for ship (default 0.0).",
        },
        "min_sample_size": {
            "type": "integer",
            "description": "Per-arm minimum (default 30, min 2).",
        },
        "causal_contract": {
            "type": "object",
            "description": "Optional causal_contract output (for action-plan assumptions).",
        },
    },
    "required": ["control_group", "treatment_groups", "group_column", "outcome_column"],
    "oneOf": [{"required": ["records"]}, {"required": ["columns"]}],
}

_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "causal_contract": {
            "type": "object",
            "description": "causal_analyze output's causal_contract object.",
        },
        "causal_qa": {
            "type": "object",
            "description": "causal_analyze output's causal_qa object.",
        },
        "experiment_readout": {
            "type": "object",
            "description": (
                "causal_estimate output's experiment_readout object (required for an "
                "experiment; pass an empty stub {contrasts:[]} for an observational-only "
                "readout)."
            ),
        },
        "causal_action_plan": {
            "type": "object",
            "description": "Optional causal_estimate output's causal_action_plan object.",
        },
    },
    "required": ["causal_contract", "causal_qa"],
}

# ----------------------------- 小工具(镜像 v1 工具的同名私有助手) -----------------------------


def _as_float(v: object, default: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _as_int(v: object, default: int) -> int:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else default


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v)


def _resolve_columns(
    records: Sequence[dict[str, Any]] | None,
    columns: dict[str, Sequence[Any]] | None,
) -> dict[str, list[Any]]:
    if isinstance(columns, dict):
        return {str(k): list(v) for k, v in columns.items()}
    out: dict[str, list[Any]] = {}
    for row in records or ():
        if isinstance(row, dict):
            for k, v in row.items():
                out.setdefault(str(k), []).append(v)
    return out


def _outcome_values(
    records: Sequence[dict[str, Any]] | None,
    columns: dict[str, Sequence[Any]] | None,
    outcome_col: str,
) -> list[Any]:
    if isinstance(columns, dict):
        return list(columns.get(outcome_col, ()))
    return [row.get(outcome_col) for row in records or () if isinstance(row, dict)]


def _missing_context(
    intent: Any,
    treatment_column: str | None,
    treatment_arms: tuple[str, ...],
    outcome_columns: tuple[str, ...],
    control_arm: str | None,
    assignment: AssignmentMechanism,
) -> list[str]:
    causal = intent.has_intervention or intent.has_randomization_signal
    missing: list[str] = []
    if causal and not treatment_column and not treatment_arms:
        missing.append("treatment(处理列/处理臂)")
    if causal and not outcome_columns:
        missing.append("outcome(结果列)")
    if assignment is AssignmentMechanism.RANDOMIZED and control_arm is None:
        missing.append("control_arm(对照臂)")
    return missing


# ----------------------------- 渲染(镜像 v1 工具的 _render) -----------------------------


def _render_contract(contract: CausalContract) -> str:
    lines = [
        f"question: {contract.question}",
        f"claim_level: {contract.claim_level.value}",
        f"assignment_mechanism: {contract.assignment_mechanism.value}",
        f"intent: {contract.intent.rationale}",
    ]
    if contract.treatment_column:
        lines.append(f"treatment_column: {contract.treatment_column}")
    if contract.treatment_arms:
        lines.append(f"treatment_arms: {', '.join(contract.treatment_arms)}")
    if contract.control_arm:
        lines.append(f"control_arm: {contract.control_arm}")
    if contract.outcome_columns:
        lines.append(f"outcome_columns: {', '.join(contract.outcome_columns)}")
    if contract.guardrail_columns:
        lines.append(f"guardrail_columns: {', '.join(contract.guardrail_columns)}")
    if contract.business_assumptions:
        lines.append(
            f"business_assumptions: {len(contract.business_assumptions)} (inferred unless confirmed)"
        )
    if contract.missing_context:
        lines.append(f"missing_context: {', '.join(contract.missing_context)}")
    return "\n".join(lines)


def _render_qa(report: Any) -> str:
    lines = [f"readiness: {report.readiness.value}"]
    for f in report.findings:
        lines.append(f"[{f.severity}] {f.code}: {f.message}")
    return "\n".join(lines)


def _render_readout(readout: Any) -> str:
    lines = [
        f"outcome: {readout.outcome_column} ({readout.outcome_kind.value})",
        f"control: {readout.control_arm}  total_n: {readout.total_n}",
    ]
    if readout.srm is not None:
        flag = "SRM!" if readout.srm.srm_detected else "ok"
        lines.append(f"SRM: {flag} (chi2={readout.srm.chi_square}, df={readout.srm.df})")
    for c in readout.contrasts:
        est = c.outcome_estimate
        ci = f"[{est.ci_lower:.4g}, {est.ci_upper:.4g}]" if est.ci_lower is not None else "n/a"
        lines.append(
            f"contrast {c.treatment_arm}: effect={est.effect} ci={ci} decision={c.decision.value}"
        )
    lines.append(f"aggregate_decision: {readout.aggregate_decision.value}")
    if readout.aggregate_reasons:
        lines.append(f"reasons: {', '.join(readout.aggregate_reasons)}")
    return "\n".join(lines)


def _render_plan(plan: Any) -> str:
    lines = [f"decision: {plan.decision.value}"]
    for rec in plan.recommendations:
        line = f"- {rec.code}" + (f" ({rec.target_arm})" if rec.target_arm else "")
        if rec.rationale:
            line += f": {rec.rationale}"
        lines.append(line)
    if plan.open_risks:
        lines.append("risks: " + "; ".join(plan.open_risks))
    return "\n".join(lines)


# ----------------------------- handlers -----------------------------


async def _causal_analyze(inputs: dict[str, Any]) -> CapabilityOutput:
    """分析子能力:build CausalContract,再跑确定性因果就绪 QA(镜像 v1 两工具)。"""

    question = inputs.get("question")
    if not isinstance(question, str) or not question.strip():
        raise CapabilityError(
            "validation_error", "question is required and must be a non-empty string"
        )

    intent = parse_causal_intent(question)
    assumptions = tuple(inputs.get("business_assumptions") or ())
    external_events = tuple(inputs.get("external_events") or ())
    claim_level = infer_claim_level(intent, bool(assumptions))

    assign_override = inputs.get("assignment_mechanism")
    if isinstance(assign_override, str) and assign_override in _ASSIGN_MAP:
        assignment = _ASSIGN_MAP[assign_override]
    elif intent.has_randomization_signal:
        assignment = AssignmentMechanism.RANDOMIZED
    else:
        assignment = AssignmentMechanism.UNKNOWN

    treatment_column = inputs.get("treatment_column")
    treatment_column = (
        treatment_column if isinstance(treatment_column, str) and treatment_column else None
    )
    control_arm = inputs.get("control_arm")
    control_arm = control_arm if isinstance(control_arm, str) and control_arm else None
    treatment_arms = tuple(inputs.get("treatment_arms") or ())
    outcome_columns = tuple(inputs.get("outcome_columns") or ())
    guardrail_columns = tuple(inputs.get("guardrail_columns") or ())
    segment_columns = tuple(inputs.get("segment_columns") or ())
    decision_threshold = _as_float(inputs.get("decision_threshold"), 0.0)
    min_sample_size = _as_int(inputs.get("min_sample_size"), 30)

    missing = _missing_context(
        intent, treatment_column, treatment_arms, outcome_columns, control_arm, assignment
    )

    contract = CausalContract(
        question=question,
        claim_level=claim_level,
        assignment_mechanism=assignment,
        outcome_columns=outcome_columns,
        treatment_column=treatment_column,
        control_arm=control_arm,
        treatment_arms=treatment_arms,
        guardrail_columns=guardrail_columns,
        segment_columns=segment_columns,
        decision_threshold=decision_threshold,
        min_sample_size=min_sample_size,
        business_assumptions=assumptions,
        external_events=external_events,
        missing_context=tuple(missing),
        intent=intent,
    )
    qa_report = run_causal_qa(contract)
    content = _render_contract(contract) + "\n\n" + _render_qa(qa_report)
    return CapabilityOutput(
        content=content,
        data={"causal_contract": contract.to_dict(), "causal_qa": qa_report.to_dict()},
    )


async def _causal_estimate(inputs: dict[str, Any]) -> CapabilityOutput:
    """推断子能力:effect estimate / experiment readout → bounded action plan。"""

    records = inputs.get("records")
    columns = inputs.get("columns")
    if (records is None) == (columns is None):
        raise CapabilityError("validation_error", "provide exactly one of records or columns")
    for key in ("control_group", "group_column", "outcome_column"):
        v = inputs.get(key)
        if not isinstance(v, str) or not v.strip():
            raise CapabilityError(
                "validation_error", f"{key} is required and must be a non-empty string"
            )
    tg = inputs.get("treatment_groups")
    if not isinstance(tg, list) or not tg or not all(isinstance(x, str) and x for x in tg):
        raise CapabilityError(
            "validation_error", "treatment_groups must be a non-empty list of non-empty strings"
        )
    if len(tg) > _MAX_TREATMENT_ARMS:
        raise CapabilityError(
            "validation_error",
            f"at most {_MAX_TREATMENT_ARMS} treatment arms (SRM df-table limit)",
        )
    ms = inputs.get("min_sample_size", 30)
    if not isinstance(ms, int) or isinstance(ms, bool) or ms < 2:
        raise CapabilityError(
            "validation_error",
            "min_sample_size must be an integer >= 2 (ddof=1 variance needs n>=2)",
        )
    kind = inputs.get("outcome_kind", "auto")
    if kind not in _OUTCOME_KINDS:
        raise CapabilityError(
            "validation_error", f"outcome_kind must be one of {list(_OUTCOME_KINDS)}"
        )
    if kind == "proportion":
        outcome_col = inputs["outcome_column"]
        values = _outcome_values(records, columns, outcome_col)
        numeric_vals = [v for v in values if _is_number(v)]
        if numeric_vals and not all(v in (0, 0.0, 1, 1.0) for v in numeric_vals):
            raise CapabilityError(
                "validation_error",
                f"outcome_kind=proportion requires outcome_column {outcome_col} values ⊆ {{0,1}}",
            )

    contract_dict = inputs.get("causal_contract")
    contract: CausalContract | None
    if isinstance(contract_dict, dict):
        try:
            contract = CausalContract.from_dict(contract_dict)
        except (TypeError, KeyError, ValueError) as exc:
            raise CapabilityError("validation_error", f"malformed causal_contract: {exc}") from exc
    else:
        contract = None

    try:
        readout = compute_readout(
            contract_question=inputs.get("question", ""),
            control_arm=inputs["control_group"],
            treatment_arms=tuple(inputs["treatment_groups"]),
            group_column=inputs["group_column"],
            outcome_column=inputs["outcome_column"],
            columns=_resolve_columns(records, columns),
            outcome_kind=OutcomeKind(kind),
            guardrail_columns=tuple(inputs.get("guardrail_columns") or ()),
            guardrail_directions=inputs.get("guardrail_directions") or None,
            segment_columns=tuple(inputs.get("segment_columns") or ()),
            expected_ratio=(
                tuple(inputs["expected_ratio"]) if inputs.get("expected_ratio") else None
            ),
            decision_threshold=_as_float(inputs.get("decision_threshold"), 0.0),
            min_sample_size=_as_int(inputs.get("min_sample_size"), 30),
        )
    except ValueError as exc:
        raise CapabilityError("execution_error", f"experiment_readout: {exc}") from exc
    plan = build_action_plan(readout, contract)
    content = _render_readout(readout) + "\n\n" + _render_plan(plan)
    return CapabilityOutput(
        content=content,
        data={"experiment_readout": readout.to_dict(), "causal_action_plan": plan.to_dict()},
    )


async def _causal_report(inputs: dict[str, Any]) -> CapabilityOutput:
    """contract + qa + readout(+action_plan)→ ReportDocument(镜像 v1 causal_report)。"""

    contract_dict = inputs.get("causal_contract")
    if not isinstance(contract_dict, dict):
        raise CapabilityError(
            "validation_error", "causal_contract is required and must be the contract object"
        )
    qa_dict = inputs.get("causal_qa")
    if not isinstance(qa_dict, dict):
        raise CapabilityError("validation_error", "causal_qa is required and must be the qa object")
    try:
        contract = CausalContract.from_dict(contract_dict)
        qa_report = CausalQAReport.from_dict(qa_dict)
    except (TypeError, KeyError, ValueError) as exc:
        raise CapabilityError("validation_error", f"malformed causal payload: {exc}") from exc

    readout_dict = inputs.get("experiment_readout")
    # 观察性问题没有 experiment_readout;合成空 readout 让 adapter 仍能产出带
    # FINDING/CAVEAT 的文档(镜像 v1 causal_report 工具)。
    if isinstance(readout_dict, dict):
        try:
            readout = ExperimentReadout.from_dict(readout_dict)
        except (TypeError, KeyError, ValueError) as exc:
            raise CapabilityError(
                "validation_error", f"malformed experiment_readout: {exc}"
            ) from exc
    else:
        readout = ExperimentReadout(
            contract_question=contract.question,
            control_arm="",
            outcome_column="",
            outcome_kind=OutcomeKind.AUTO,
            aggregate_reasons=("observational_only",),
        )

    plan: ActionPlan | None = None
    plan_dict = inputs.get("causal_action_plan")
    if isinstance(plan_dict, dict):
        try:
            plan = ActionPlan.from_dict(plan_dict)
        except (TypeError, KeyError, ValueError) as exc:
            raise CapabilityError(
                "validation_error", f"malformed causal_action_plan: {exc}"
            ) from exc

    document = to_report_document(
        readout=readout,
        contract=contract,
        qa_report=qa_report,
        action_plan=plan,
    )
    return CapabilityOutput(
        content=(
            "ReportDocument 已构建(FINDING 紧跟 CAVEAT,中性措辞)。"
            "把它传给 html_report(document=...) 经 QA 闸渲染。"
        ),
        data={"document": document.to_dict()},
    )


# ----------------------------- 注册 -----------------------------


def register_all(registry: CapabilityRegistry) -> list[str]:
    """把 causal 域的三个能力注册进 ``registry``,返回注册名列表(注册顺序)。"""

    entries: list[tuple[CapabilitySpec, CapabilityHandler]] = [
        (
            CapabilitySpec(
                name="causal_analyze",
                description=(
                    "【分析子能力】把因果/决策问题归一化为 CausalContract 并跑因果就绪 QA:"
                    "识别因果意图,推断 claim_level(descriptive/associational/"
                    "causal_assumption/experimental)与分配机制,显式化处理/结果/护栏/假设,"
                    "缺项写入 missing_context(不臆测),随后返回确定性 6 态就绪(not_causal/"
                    "blocked/needs_assumptions/needs_data/assumption_ready/experiment_ready)"
                    "+闭词汇 finding。观察性/相关证据永远到不了 experiment_ready;未就绪不得"
                    "给因果结论。只读;先于 causal_estimate(推断子能力)使用。"
                ),
                input_schema=_ANALYZE_SCHEMA,
                domain="causal",
                output_kind=OutputKind.STRUCTURED,
                permission=Permission.READ_ONLY,
                error_codes=_ERROR_CODES,
            ),
            _causal_analyze,
        ),
        (
            CapabilitySpec(
                name="causal_estimate",
                description=(
                    "【推断子能力】效应估计与实验读出:对随机化 A/B 数据做 per-contrast 效应 +"
                    " 95% CI、样本比例失衡(SRM)、护栏与有界聚合决策(ship/do_not_ship/"
                    "inconclusive/needs_more_data),并把读出转成挂在证据上的有界行动计划"
                    "(机制/假设/监控/回滚/反驳)。正态近似 z 检验,确定性;绝不把 SRM 污染或"
                    "inconclusive 的证据升级为 ship。只读;承接 causal_analyze(分析子能力)"
                    "的产出。"
                ),
                input_schema=_ESTIMATE_SCHEMA,
                domain="causal",
                output_kind=OutputKind.STRUCTURED,
                permission=Permission.READ_ONLY,
                error_codes=_ERROR_CODES,
            ),
            _causal_estimate,
        ),
        (
            CapabilitySpec(
                name="causal_report",
                description=(
                    "把 causal_analyze 的 contract+qa 与 causal_estimate 的 readout"
                    "(+可选 action_plan)转成 ReportDocument(FINDING 紧跟 CAVEAT、中性措辞、"
                    "因果语留给 caveat),以便经 html_report(document=...) 的 reporting QA 闸"
                    "渲染交付。只读。"
                ),
                input_schema=_REPORT_SCHEMA,
                domain="causal",
                output_kind=OutputKind.TEXT,
                permission=Permission.READ_ONLY,
                error_codes=_ERROR_CODES,
            ),
            _causal_report,
        ),
    ]
    for spec, handler in entries:
        registry.register(spec, handler)
    return [spec.name for spec, _ in entries]
