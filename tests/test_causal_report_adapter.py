"""causal.report_adapter:ReportDocument 生成、FINDING→CAVEAT 邻接、reporting QA 兼容。"""

from __future__ import annotations

from data_analysis_agent.causal.experiment import build_action_plan, compute_readout
from data_analysis_agent.causal.model import (
    AssignmentMechanism,
    CausalContract,
    CausalIntent,
    CausalReadiness,
    ClaimLevel,
)
from data_analysis_agent.causal.qa import run_causal_qa
from data_analysis_agent.causal.report_adapter import to_report_document, to_reporting_readiness
from data_analysis_agent.reporting.contract import BlockRole
from data_analysis_agent.reporting.qa import Readiness, run_qa

# ----------------------------- readiness 映射 -----------------------------


def test_readiness_mapping_all_six_states():
    assert to_reporting_readiness(CausalReadiness.NOT_CAUSAL) is Readiness.DRAFT
    assert to_reporting_readiness(CausalReadiness.BLOCKED) is Readiness.DRAFT
    assert to_reporting_readiness(CausalReadiness.NEEDS_ASSUMPTIONS) is Readiness.NEEDS_REVIEW
    assert to_reporting_readiness(CausalReadiness.NEEDS_DATA) is Readiness.NEEDS_REVIEW
    assert to_reporting_readiness(CausalReadiness.ASSUMPTION_READY) is Readiness.READY
    assert to_reporting_readiness(CausalReadiness.EXPERIMENT_READY) is Readiness.READY


# ----------------------------- 夹具 -----------------------------


def _readout():
    group = ["control"] * 50 + ["t"] * 50
    outcome = [0, 1, 0, 1, 0] * 10 + [1, 1, 1, 0, 1] * 10  # control 0.4, t 0.8
    return compute_readout(
        contract_question="A/B 实验 variant_b 是否提升 revenue",
        control_arm="control",
        treatment_arms=("t",),
        group_column="variant",
        outcome_column="revenue",
        columns={"variant": group, "revenue": outcome},
        min_sample_size=30,
    )


def _contract():
    return CausalContract(
        question="A/B 实验 variant_b 是否提升 revenue",
        claim_level=ClaimLevel.EXPERIMENTAL,
        assignment_mechanism=AssignmentMechanism.RANDOMIZED,
        treatment_column="variant",
        control_arm="control",
        treatment_arms=("t",),
        outcome_columns=("revenue",),
        guardrail_columns=("crash_count",),
        business_assumptions=("无溢出效应", "SUTVA"),
        external_events=("无",),
        intent=CausalIntent(has_randomization_signal=True),
    )


def _doc(*, with_plan: bool = True):
    readout = _readout()
    contract = _contract()
    qa = run_causal_qa(contract)
    # 用真实的 build_action_plan(端到端),而非手搓不现实的 ActionRecommendation
    plan = build_action_plan(readout, contract) if with_plan else None
    return to_report_document(
        readout=readout,
        contract=contract,
        qa_report=qa,
        action_plan=plan,
        generated_at="2026-07-08T00:00:00Z",
    )


# ----------------------------- 块序列与邻接不变量 -----------------------------


def test_block_sequence_roles_present():
    roles = [b.role for b in _doc().blocks]
    assert roles[0] is BlockRole.HEADER
    for needed in (
        BlockRole.EXECUTIVE_SUMMARY,
        BlockRole.DATA_CONTEXT,
        BlockRole.KPI_STRIP,
        BlockRole.RECOMMENDATION,
        BlockRole.SOURCE_METADATA,
        BlockRole.CAVEAT,
    ):
        assert needed in roles


def test_every_finding_followed_by_caveat():
    doc = _doc()
    for i, b in enumerate(doc.blocks):
        if b.role is BlockRole.FINDING:
            assert i + 1 < len(doc.blocks), "FINDING at end without CAVEAT"
            assert doc.blocks[i + 1].role is BlockRole.CAVEAT, "FINDING not followed by CAVEAT"


def test_kpi_strip_has_one_card_per_contrast():
    doc = _doc()
    kpi = next(b for b in doc.blocks if b.role is BlockRole.KPI_STRIP)
    assert len(kpi.kpi_cards) == 1  # 单对比


def test_recommendation_block_has_evidence_refs():
    # 回归(独立审查):ship/hold 等决策无 target_arm 时,RECOMMENDATION 仍须非空
    # evidence_refs,否则 reporting QA 触发 recommendation.no_evidence。
    doc = _doc()
    rec = next(b for b in doc.blocks if b.role is BlockRole.RECOMMENDATION)
    assert len(rec.evidence_refs) > 0


def test_finding_body_uses_neutral_phrasing():
    doc = _doc()
    finding = next(b for b in doc.blocks if b.role is BlockRole.FINDING)
    # 不含强因果动词(_check_causal 的强词表)
    for marker in ("导致", "引起", "造成", "驱动", "caused by", "drives"):
        assert marker not in (finding.body or "").lower()


# ----------------------------- reporting QA 兼容 -----------------------------


def test_generated_document_has_no_causal_no_caveat_finding():
    doc = _doc()
    report = run_qa(doc, artifact_exists=True)
    codes = {f.code for f in report.findings}
    # 适配器产出的因果 FINDING 不会触发 reporting 的 causal.no_caveat
    assert "causal.no_caveat" not in codes


# ----------------------------- 确定性 -----------------------------


def test_deterministic_same_input_same_output():
    assert _doc() == _doc()
