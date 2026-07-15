"""CausalReportTool: causal results -> ReportDocument (wires report_adapter live)."""

from __future__ import annotations

from data_analysis_agent.causal.experiment import build_action_plan, compute_readout
from data_analysis_agent.causal.model import (
    AssignmentMechanism,
    CausalContract,
    CausalIntent,
    ClaimLevel,
)
from data_analysis_agent.causal.qa import run_causal_qa
from data_analysis_agent.reporting.contract import BlockRole, ReportDocument
from data_analysis_agent.tools.causal_report import CausalReportTool
from data_analysis_agent.tools.html_report import HtmlReportTool


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


async def test_causal_report_builds_document_passing_qa_gate(tmp_path):
    """causal_report builds a ReportDocument from causal results; passed to
    html_report it goes through the QA gate and renders (closes the G3 dead-code
    gap: report_adapter is now reachable from the live tool path)."""
    readout = _readout()
    contract = _contract()
    qa = run_causal_qa(contract)
    plan = build_action_plan(readout, contract)

    tool = CausalReportTool()
    result = await tool.call(
        {
            "causal_contract": contract.to_dict(),
            "causal_qa": qa.to_dict(),
            "experiment_readout": readout.to_dict(),
            "causal_action_plan": plan.to_dict(),
        }
    )
    assert result.is_error is False
    doc_dict = result.metadata["document"]

    # The document is a valid ReportDocument (round-trips) with FINDING+CAVEAT.
    doc = ReportDocument.from_dict(doc_dict)
    roles = [b.role for b in doc.blocks]
    assert BlockRole.FINDING in roles
    assert BlockRole.CAVEAT in roles
    # Every FINDING is immediately followed by a CAVEAT (adapter invariant).
    for i, b in enumerate(doc.blocks):
        if b.role is BlockRole.FINDING:
            assert i + 1 < len(doc.blocks) and doc.blocks[i + 1].role is BlockRole.CAVEAT

    # End-to-end: feeding it to html_report runs the QA gate and renders.
    html = HtmlReportTool(artifact_dir=tmp_path)
    rendered = await html.call({"document": doc_dict})
    assert rendered.is_error is False
    files = list(tmp_path.glob("*.html"))
    assert len(files) == 1
    assert "qa-badge" in files[0].read_text(encoding="utf-8")


async def test_causal_report_observational_only_without_readout(tmp_path):
    """No experiment_readout (observational question): synthesizes an empty
    readout so the adapter still produces a FINDING/CAVEAT-bearing document."""
    contract = _contract()
    # Force an observational flavor: no randomization signal.
    contract = CausalContract(
        question="降价是否导致销量上升",
        claim_level=ClaimLevel.ASSOCIATIONAL,
        assignment_mechanism=AssignmentMechanism.SELF_SELECTION,
        treatment_column="discount",
        control_arm="",
        treatment_arms=("discounted",),
        outcome_columns=("sales",),
        intent=CausalIntent(has_randomization_signal=False),
    )
    qa = run_causal_qa(contract)
    tool = CausalReportTool()
    result = await tool.call({"causal_contract": contract.to_dict(), "causal_qa": qa.to_dict()})
    assert result.is_error is False
    doc_dict = result.metadata["document"]
    doc = ReportDocument.from_dict(doc_dict)
    # Observational readiness must never be experiment_ready; the document still
    # carries header + summary + a caveat (assumption/observational limit).
    roles = [b.role for b in doc.blocks]
    assert BlockRole.CAVEAT in roles
    # Observational document also passes the QA gate and renders (no DRAFT blocker).
    html = HtmlReportTool(artifact_dir=tmp_path)
    rendered = await html.call({"document": doc_dict})
    assert rendered.is_error is False
