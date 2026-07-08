"""因果决策领域层:领域数据类与封闭词表枚举(纯 stdlib)。

所有数据类 ``@dataclasses.dataclass(frozen=True)`` + 继承 ``reporting.model.Serializable``
(复用通用 ``to_dict``/``from_dict``,enum→value、tuple 往返、嵌套 frozen 递归重建;
往返契约 ``Cls.from_dict(x.to_dict()) == x``)。集合一律 ``tuple[...]``,枚举
``class X(str, enum.Enum)``。时间字段由调用方注入,本模块不调 ``datetime.now()``。

依赖方向:causal → reporting(单向,仅复用 Serializable + SourceKind);禁止任何其他
内部包(见 ``scripts/drift_rules.py`` 与 ADR 0010)。
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import field

from data_analysis_agent.reporting.model import Serializable, SourceKind

__all__ = [
    "VariableRole",
    "AssignmentMechanism",
    "ClaimLevel",
    "CausalReadiness",
    "DecisionLevel",
    "OutcomeKind",
    "CausalIntent",
    "CausalQuestion",
    "VariableBinding",
    "CausalContract",
    "CausalFinding",
    "CausalQAReport",
    "EffectEstimate",
    "SRMResult",
    "GuardrailResult",
    "SegmentBreakdown",
    "ContrastResult",
    "ExperimentReadout",
    "ActionRecommendation",
    "ActionPlan",
]


# ----------------------------- 封闭词表枚举 -----------------------------


class VariableRole(str, enum.Enum):
    """因果契约中一个列所扮演的角色。"""

    OUTCOME = "outcome"
    TREATMENT = "treatment"
    CONTROL_ARM = "control_arm"
    GUARDRAIL = "guardrail"
    SEGMENT = "segment"
    COVARIATE = "covariate"
    ASSIGNMENT = "assignment"


class AssignmentMechanism(str, enum.Enum):
    """处理分配机制(决定可识别性与能否做实验读出)。"""

    RANDOMIZED = "randomized"
    QUASI_EXPERIMENT = "quasi_experiment"
    SELF_SELECTION = "self_selection"
    UNKNOWN = "unknown"


class ClaimLevel(str, enum.Enum):
    """一条结论的声称等级(因果防过度声称的核心标签)。"""

    DESCRIPTIVE = "descriptive"  # 仅描述
    ASSOCIATIONAL = "associational"  # 相关/观察性,未做识别
    CAUSAL_ASSUMPTION = "causal_assumption"  # 观察性 + 显式接受假设
    EXPERIMENTAL = "experimental"  # 随机化实验


class CausalReadiness(str, enum.Enum):
    """契约级因果就绪态(不是实验决策 ship/hold)。

    与 ``reporting.Readiness`` 的映射只在 ``report_adapter`` 里做,本层不依赖 reporting
    之外的概念。
    """

    NOT_CAUSAL = "not_causal"  # 无干预/处理,因果结论不适用
    BLOCKED = "blocked"  # 处理存在但分配不可知/不可识别
    NEEDS_ASSUMPTIONS = "needs_assumptions"  # 可识别但缺业务假设
    NEEDS_DATA = "needs_data"  # 假设齐但 outcome/guardrail 列未解析
    ASSUMPTION_READY = "assumption_ready"  # 观察性 + 显式接受假设
    EXPERIMENT_READY = "experiment_ready"  # 随机化 + 必需字段齐


class DecisionLevel(str, enum.Enum):
    """实验读出的有界决策。"""

    NEEDS_MORE_DATA = "needs_more_data"
    INCONCLUSIVE = "inconclusive"
    DO_NOT_SHIP = "do_not_ship"
    SHIP = "ship"


class OutcomeKind(str, enum.Enum):
    """结果变量的统计口径。"""

    AUTO = "auto"  # 自动判定:值⊆{0,1}→比例,否则均值
    PROPORTION = "proportion"  # 二元比例(强制;非二元由工具层拒)
    MEAN = "mean"  # 连续均值


# ----------------------------- 意图与契约 -----------------------------


@dataclasses.dataclass(frozen=True)
class CausalIntent(Serializable):
    """从用户措辞确定性抽取的因果意图信号(推断,非事实)。"""

    has_intervention: bool = False
    has_randomization_signal: bool = False
    wants_lift_or_effect: bool = False
    has_observation_marker: bool = False
    assignment_hint: AssignmentMechanism = AssignmentMechanism.UNKNOWN
    detected_outcome_terms: tuple[str, ...] = ()
    detected_treatment_terms: tuple[str, ...] = ()
    rationale: str = ""


@dataclasses.dataclass(frozen=True)
class CausalQuestion(Serializable):
    """归一化的因果问题:原始问句 + 抽取的意图 + 上下文引用。"""

    question: str
    intent: CausalIntent = field(default_factory=CausalIntent)
    user_need_refs: tuple[str, ...] = ()
    data_context_refs: tuple[str, ...] = ()
    process_context_refs: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class VariableBinding(Serializable):
    """把一个数据列绑定到因果角色 + 其来源(显式 vs 推断)。"""

    column: str
    role: VariableRole
    rationale: str = ""
    source: SourceKind = SourceKind.IMPLICIT_USER


@dataclasses.dataclass(frozen=True)
class CausalContract(Serializable):
    """因果契约:业务问题 → 处理/结果/单位/人群/分配/假设/阈值的显式化。

    缺项一律写 ``missing_context``,不做臆测填充。假设/混淆默认标
    ``IMPLICIT_USER``,除非用户显式确认。
    """

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
    business_assumptions: tuple[str, ...] = ()  # 可识别性/可忽略性假设
    external_events: tuple[str, ...] = ()  # 并发混淆/外部事件
    refutations: tuple[str, ...] = ()  # 已考虑的反驳
    variables: tuple[VariableBinding, ...] = ()
    field_sources: tuple[tuple[str, SourceKind], ...] = ()
    missing_context: tuple[str, ...] = ()
    intent: CausalIntent = field(default_factory=CausalIntent)


# ----------------------------- 因果 QA -----------------------------


@dataclasses.dataclass(frozen=True)
class CausalFinding(Serializable):
    """一条因果就绪检查发现(severity 值对齐 reporting.Severity)。"""

    severity: str  # "blocker" | "high" | "medium" | "info"
    code: str
    message: str
    suggested_fix: str | None = None


@dataclasses.dataclass(frozen=True)
class CausalQAReport(Serializable):
    """因果就绪 QA 报告:就绪态 + 发现列表。"""

    readiness: CausalReadiness
    findings: tuple[CausalFinding, ...] = ()
    contract_exists: bool = False


# ----------------------------- 实验读出 -----------------------------


@dataclasses.dataclass(frozen=True)
class EffectEstimate(Serializable):
    """单对比、单结果变量的效应估计(正态近似 z 检验)。

    ``degenerate=True`` 表示无法评估不确定性(SE=0 / 空组 / pooled∈{0,1}),
    此时 ``z``/``p_value``/``significant`` 为 None,决策应走 INCONCLUSIVE。
    """

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
    significant: bool | None = None  # CI 排除 0;退化时 None
    degenerate: bool = False
    notes: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class SRMResult(Serializable):
    """样本比例失衡(Sample Ratio Mismatch)卡方检验结果。

    Stage 1 只报 chi_square/df/critical/srm_detected,不报 p 值(免实现不完全伽马)。
    """

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
    """护栏指标的效应 + 是否破阈。"""

    column: str
    estimate: EffectEstimate
    unfavorable_direction: str  # "higher_is_worse" | "lower_is_worse"
    tolerance: float = 0.0
    breached: bool = False
    notes: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class SegmentBreakdown(Serializable):
    """分群描述(Stage 1 仅各臂样本量,不做分群级 z 检验)。"""

    column: str
    note: str = ""
    arm_sizes: tuple[tuple[str, int], ...] = ()


@dataclasses.dataclass(frozen=True)
class ContrastResult(Serializable):
    """一个处理臂 vs 对照的单对比结果 + 其护栏/分群/决策。"""

    treatment_arm: str
    outcome_estimate: EffectEstimate
    guardrails: tuple[GuardrailResult, ...] = ()
    segments: tuple[SegmentBreakdown, ...] = ()
    decision: DecisionLevel = DecisionLevel.INCONCLUSIVE
    decision_reasons: tuple[str, ...] = ()
    claim_level: ClaimLevel = ClaimLevel.EXPERIMENTAL


@dataclasses.dataclass(frozen=True)
class ExperimentReadout(Serializable):
    """一次实验读出的完整结果:多对比 + SRM + 聚合决策。"""

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


# ----------------------------- 行动计划 -----------------------------


@dataclasses.dataclass(frozen=True)
class ActionRecommendation(Serializable):
    """一条有界行动建议(code 取自封闭集合)。"""

    code: str  # "ship" | "hold" | "fix_srm" | "add_power" | "drop_arm" | "investigate_guardrail"
    target_arm: str | None = None
    rationale: str = ""
    precondition: str = ""


@dataclasses.dataclass(frozen=True)
class ActionPlan(Serializable):
    """实验读出转出的有界行动计划:决策 + 建议 + 假设 + 反驳 + 风险。

    每条建议都必须挂在证据/假设/监控/回滚上,不得无依据推荐行动。
    """

    decision: DecisionLevel
    recommendations: tuple[ActionRecommendation, ...] = ()
    assumptions: tuple[str, ...] = ()
    refutations: tuple[str, ...] = ()
    open_risks: tuple[str, ...] = ()
