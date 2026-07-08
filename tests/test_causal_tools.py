"""causal_contract / causal_qa / experiment_readout 工具:校验、调用、注册接线。"""

from __future__ import annotations

import pytest

from data_analysis_agent.causal.model import (
    AssignmentMechanism,
    CausalContract,
    CausalReadiness,
    ClaimLevel,
    DecisionLevel,
)
from data_analysis_agent.runtime import READ_ONLY_TOOLS, build_registry
from data_analysis_agent.tools.causal_contract import CausalContractTool
from data_analysis_agent.tools.causal_qa import CausalQATool
from data_analysis_agent.tools.experiment_readout import ExperimentReadoutTool

# ----------------------------- 注册接线 -----------------------------


def test_tools_registered_in_build_registry():
    registry = build_registry()
    assert registry.get_tool("causal_contract") is not None
    assert registry.get_tool("causal_qa") is not None
    assert registry.get_tool("experiment_readout") is not None


def test_tools_in_read_only_allowlist():
    for name in ("causal_contract", "causal_qa", "experiment_readout"):
        assert name in READ_ONLY_TOOLS


def test_tools_declare_read_only():
    for tool in (CausalContractTool(), CausalQATool(), ExperimentReadoutTool()):
        assert tool.is_concurrency_safe({}) is True
        assert tool.is_read_only({}) is True
        assert tool.is_destructive({}) is False


# ----------------------------- causal_contract -----------------------------


def test_causal_contract_rejects_empty_question():
    assert CausalContractTool().validate_input({"question": ""}).valid is False
    assert CausalContractTool().validate_input({"question": "  "}).valid is False


async def test_causal_contract_builds_experiment_contract():
    tool = CausalContractTool()
    result = await tool.call(
        {
            "question": "A/B 实验,variant_b 是否提升 revenue",
            "outcome_columns": ["revenue"],
            "treatment_column": "variant",
            "control_arm": "control",
            "treatment_arms": ["variant_b"],
            "business_assumptions": ["无溢出效应"],
        }
    )
    assert result.is_error is False
    contract = CausalContract.from_dict(result.metadata["causal_contract"])
    assert contract.claim_level is ClaimLevel.EXPERIMENTAL
    assert contract.assignment_mechanism is AssignmentMechanism.RANDOMIZED
    assert contract.outcome_columns == ("revenue",)
    assert contract.control_arm == "control"


async def test_causal_contract_surfaces_missing_context():
    tool = CausalContractTool()
    result = await tool.call({"question": "新功能是否导致了收入变化?"})
    contract = CausalContract.from_dict(result.metadata["causal_contract"])
    # 因果问题但未提供处理/结果 → missing_context 非空,且不臆测
    assert len(contract.missing_context) > 0
    assert contract.treatment_column is None
    assert contract.outcome_columns == ()


async def test_causal_contract_correlation_is_associational():
    tool = CausalContractTool()
    result = await tool.call({"question": "收入与广告支出相关吗?"})
    contract = CausalContract.from_dict(result.metadata["causal_contract"])
    # 纯相关 → ASSOCIATIONAL,绝不 EXPERIMENTAL
    assert contract.claim_level is ClaimLevel.ASSOCIATIONAL


# ----------------------------- causal_qa -----------------------------


def test_causal_qa_rejects_non_dict_contract():
    assert CausalQATool().validate_input({"causal_contract": "not-a-dict"}).valid is False
    assert CausalQATool().validate_input({}).valid is False


async def test_causal_qa_experiment_ready_end_to_end():
    # contract → qa:齐字段+假设+随机化 → EXPERIMENT_READY
    contract_result = await CausalContractTool().call(
        {
            "question": "A/B 实验,variant_b 是否提升 revenue",
            "outcome_columns": ["revenue"],
            "treatment_column": "variant",
            "control_arm": "control",
            "treatment_arms": ["variant_b"],
            "guardrail_columns": ["crash_count"],
            "business_assumptions": ["无溢出效应", "SUTVA"],
        }
    )
    qa_result = await CausalQATool().call(
        {"causal_contract": contract_result.metadata["causal_contract"]}
    )
    assert qa_result.is_error is False
    report = qa_result.metadata["causal_qa"]
    assert report["readiness"] == CausalReadiness.EXPERIMENT_READY.value


# ----------------------------- experiment_readout -----------------------------


def _records() -> list[dict]:
    recs: list[dict] = []
    for v in [0, 1, 0, 1, 0] * 10:  # control p≈0.5
        recs.append({"variant": "control", "y": v})
    for v in [1, 1, 1, 0, 1] * 10:  # treatment p≈0.8
        recs.append({"variant": "t", "y": v})
    return recs


def test_experiment_readout_requires_records_or_columns():
    tool = ExperimentReadoutTool()
    assert (
        tool.validate_input(
            {
                "control_group": "c",
                "treatment_groups": ["t"],
                "group_column": "v",
                "outcome_column": "y",
            }
        ).valid
        is False
    )  # 既无 records 又无 columns
    assert (
        tool.validate_input(
            {
                "records": [{}],
                "columns": {"v": []},
                "control_group": "c",
                "treatment_groups": ["t"],
                "group_column": "v",
                "outcome_column": "y",
            }
        ).valid
        is False
    )  # 同时给两者


def test_experiment_readout_proportion_non_binary_rejected():
    tool = ExperimentReadoutTool()
    res = tool.validate_input(
        {
            "records": [{"v": "c", "y": 2.5}, {"v": "t", "y": 3.5}],
            "control_group": "c",
            "treatment_groups": ["t"],
            "group_column": "v",
            "outcome_column": "y",
            "outcome_kind": "proportion",
        }
    )
    assert res.valid is False


def test_experiment_readout_min_sample_floor():
    tool = ExperimentReadoutTool()
    res = tool.validate_input(
        {
            "records": [{"v": "c", "y": 1}],
            "control_group": "c",
            "treatment_groups": ["t"],
            "group_column": "v",
            "outcome_column": "y",
            "min_sample_size": 1,
        }
    )
    assert res.valid is False  # min_sample_size 必须 >= 2


async def test_experiment_readout_ship_path_with_records():
    tool = ExperimentReadoutTool()
    result = await tool.call(
        {
            "records": _records(),
            "control_group": "control",
            "treatment_groups": ["t"],
            "group_column": "variant",
            "outcome_column": "y",
            "decision_threshold": 0.0,
            "min_sample_size": 30,
        }
    )
    assert result.is_error is False
    readout = result.metadata["experiment_readout"]
    assert readout["aggregate_decision"] == DecisionLevel.SHIP.value
    assert len(readout["contrasts"]) == 1


async def test_experiment_readout_accepts_columns_form():
    records = _records()
    columns: dict[str, list] = {
        "variant": [r["variant"] for r in records],
        "y": [r["y"] for r in records],
    }
    tool = ExperimentReadoutTool()
    result = await tool.call(
        {
            "columns": columns,
            "control_group": "control",
            "treatment_groups": ["t"],
            "group_column": "variant",
            "outcome_column": "y",
        }
    )
    assert result.is_error is False
    assert result.metadata["experiment_readout"]["contrasts"][0]["treatment_arm"] == "t"


async def test_experiment_readout_missing_arm_returns_error_result():
    tool = ExperimentReadoutTool()
    result = await tool.call(
        {
            "records": [{"variant": "control", "y": 1}, {"variant": "control", "y": 0}],
            "control_group": "control",
            "treatment_groups": ["ghost"],
            "group_column": "variant",
            "outcome_column": "y",
        }
    )
    # compute_readout 返回 NEEDS_MORE_DATA(不抛);工具不标 error
    assert result.is_error is False
    assert (
        result.metadata["experiment_readout"]["aggregate_decision"]
        == DecisionLevel.NEEDS_MORE_DATA.value
    )


@pytest.mark.parametrize(
    "field",
    ["control_group", "group_column", "outcome_column"],
)
def test_experiment_readout_required_strings(field):
    tool = ExperimentReadoutTool()
    base = {
        "records": [{"v": "c", "y": 1}],
        "control_group": "c",
        "treatment_groups": ["t"],
        "group_column": "v",
        "outcome_column": "y",
    }
    base[field] = ""
    assert tool.validate_input(base).valid is False
