"""因果决策领域层:确定性因果就绪 QA。

``run_causal_qa(contract)`` 返回 ``CausalQAReport``(6 态 ``CausalReadiness`` + 闭词汇
``CausalFinding``)。无 LLM、无 I/O、无时间/随机——给定 ``CausalContract``,结果唯一。

核心反过度声称不变量:
- 观察性/相关问题**永远**到不了 ``EXPERIMENT_READY``;
- 因果声称(干预/随机化)要么有随机化 + 必需字段 + 假设(→ EXPERIMENT_READY),
  要么有已知观察性机制 + 显式假设(→ ASSUMPTION_READY),否则停在 NEEDS_*/BLOCKED。

``CausalReadiness`` 与 ``reporting.Readiness`` 的映射刻意留到 ``report_adapter`` 做,
本模块不依赖 reporting 之外的概念。
"""

from __future__ import annotations

from data_analysis_agent.causal.model import (
    AssignmentMechanism,
    CausalContract,
    CausalFinding,
    CausalQAReport,
    CausalReadiness,
    ClaimLevel,
)

__all__ = ["run_causal_qa"]

_INFO = "info"
_MEDIUM = "medium"
_HIGH = "high"
_BLOCKER = "blocker"

_SPILLOVER_TERMS = ("溢出", "干扰", "sutva", "interference", "spillover", "无溢出", "无干扰")


def _has_causal_intent(contract: CausalContract) -> bool:
    """是否构成因果问题(排除纯描述/纯相关 → NOT_CAUSAL)。"""
    return (
        contract.intent.has_intervention
        or contract.intent.has_randomization_signal
        or contract.claim_level in (ClaimLevel.CAUSAL_ASSUMPTION, ClaimLevel.EXPERIMENTAL)
    )


def _is_randomized(contract: CausalContract) -> bool:
    # 只认权威的 assignment_mechanism 字段。CausalContractTool 已在缺省 override 时把
    # intent 的随机化信号编码进该字段;一旦设定,该字段即权威。若再 OR 上文本信号,
    # 会让显式声明的观察性机制(self_selection/quasi_experiment)因问句含"实验组"而被
    # 误判为随机化 → 观察性证据泄到 EXPERIMENT_READY(违反保证 #1)。
    return contract.assignment_mechanism is AssignmentMechanism.RANDOMIZED


def _is_observational(contract: CausalContract) -> bool:
    return contract.assignment_mechanism in (
        AssignmentMechanism.SELF_SELECTION,
        AssignmentMechanism.QUASI_EXPERIMENT,
    )


def _has_treatment(contract: CausalContract) -> bool:
    return contract.treatment_column is not None or bool(contract.treatment_arms)


def _has_outcome(contract: CausalContract) -> bool:
    return bool(contract.outcome_columns)


def _mentions(assumptions: tuple[str, ...], terms: tuple[str, ...]) -> bool:
    joined = " ".join(assumptions).lower()
    return any(t in joined for t in terms)


def _extra_checks(contract: CausalContract, findings: list[CausalFinding]) -> None:
    """附加的 medium/info 检查:不改变 readiness,只补充审计性 finding。"""
    identifiable = _is_randomized(contract) or _is_observational(contract)
    if identifiable and _has_treatment(contract) and not contract.guardrail_columns:
        findings.append(
            CausalFinding(
                _MEDIUM,
                "causal.no_guardrail",
                "未声明护栏指标;建议补充业务护栏(如崩溃/延迟/流失)以约束 ship 决策",
                "在 guardrail_columns 中补充护栏列",
            )
        )
    if identifiable and not contract.external_events:
        findings.append(
            CausalFinding(
                _INFO,
                "causal.external_events_unchecked",
                "未声明外部事件;建议确认分析窗口内无并发混淆(节假日/竞品/系统变更)",
                "在 external_events 中声明或显式标注无",
            )
        )
    if _is_randomized(contract) and not _mentions(contract.business_assumptions, _SPILLOVER_TERMS):
        findings.append(
            CausalFinding(
                _INFO,
                "causal.spillover_unchecked",
                "随机化实验未显式声明无溢出/SUTVA 假设",
                "在 business_assumptions 中补充无溢出/无干扰假设",
            )
        )
    if len(contract.treatment_arms) > 1:
        findings.append(
            CausalFinding(
                _INFO,
                "stats.no_multiple_comparison_correction",
                "多处理臂未做多重比较校正(Stage1 不做 Bonferroni/Holm)",
                "多臂时按需自行校正,或仅解读先验指定的主对比",
            )
        )


def run_causal_qa(contract: CausalContract) -> CausalQAReport:
    """对 ``contract`` 做确定性因果就绪分类 + 闭词汇 finding。"""
    findings: list[CausalFinding] = []

    # 1. 非因果问题(描述/纯相关)→ NOT_CAUSAL
    if not _has_causal_intent(contract):
        findings.append(
            CausalFinding(
                _INFO,
                "causal.not_causal",
                "非因果问题:输出为描述/相关,不适用因果结论",
                "若需因果结论,显式说明处理/结果/分配机制",
            )
        )
        _extra_checks(contract, findings)
        return CausalQAReport(CausalReadiness.NOT_CAUSAL, tuple(findings), contract_exists=True)

    randomized = _is_randomized(contract)
    identifiable = randomized or _is_observational(contract)

    # 2. 处理存在但分配机制不可知 → BLOCKED
    if not identifiable:
        findings.append(
            CausalFinding(
                _BLOCKER,
                "causal.assignment_unknown",
                "因果声称存在但分配机制未知,无法识别处理效应",
                "说明分配机制(randomized/self_selection/quasi_experiment)或改为相关表述",
            )
        )
        _extra_checks(contract, findings)
        return CausalQAReport(CausalReadiness.BLOCKED, tuple(findings), contract_exists=True)

    has_assumptions = bool(contract.business_assumptions)
    has_treatment = _has_treatment(contract)
    has_outcome = _has_outcome(contract)

    # 3. 可识别但缺业务假设 → NEEDS_ASSUMPTIONS(随机化与观察性都要求显式假设)
    if not has_assumptions:
        findings.append(
            CausalFinding(
                _HIGH,
                "causal.needs_assumptions",
                "缺可识别性/可忽略性业务假设(如无溢出、一致性、稳定单位处理值)",
                "在 business_assumptions 中补充假设",
            )
        )
        _extra_checks(contract, findings)
        return CausalQAReport(
            CausalReadiness.NEEDS_ASSUMPTIONS, tuple(findings), contract_exists=True
        )

    # 4. 假设齐但必需字段未解析 → NEEDS_DATA
    missing_fields: list[str] = []
    if not has_treatment:
        missing_fields.append("处理列/处理臂")
    if not has_outcome:
        missing_fields.append("结果列")
    if randomized and contract.control_arm is None:
        missing_fields.append("对照臂")
    if missing_fields:
        findings.append(
            CausalFinding(
                _HIGH,
                "causal.needs_data",
                "必需字段未解析:" + "/".join(missing_fields),
                "解析数据上下文补充缺失列",
            )
        )
        _extra_checks(contract, findings)
        return CausalQAReport(CausalReadiness.NEEDS_DATA, tuple(findings), contract_exists=True)

    # 5/6. 就绪
    if randomized:
        _extra_checks(contract, findings)
        return CausalQAReport(
            CausalReadiness.EXPERIMENT_READY, tuple(findings), contract_exists=True
        )
    findings.append(
        CausalFinding(
            _MEDIUM,
            "causal.observational_assumption",
            "观察性因果:假设已记录;Stage1 不做复杂观察性估计,仅标注假设、不做处理效应数值声称",
            "如需因果效应数值,等待 Phase2 观察性估计量(DiD/匹配/合成控制)",
        )
    )
    _extra_checks(contract, findings)
    return CausalQAReport(CausalReadiness.ASSUMPTION_READY, tuple(findings), contract_exists=True)
