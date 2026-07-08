"""因果决策领域层:确定性因果/实验/行动意图解析。

``parse_causal_intent(text)`` 从用户措辞抽取因果意图信号(是否有干预、是否有随机化
信号、是否求效应、是否仅观察性表述、检测到的结果/处理词),并给出分配机制提示。
``infer_claim_level(intent, has_explicit_assumptions)`` 把意图映射到封闭 claim_level。

无 LLM、无外部依赖:纯子串匹配 + 全小写化(中文字符不受 .lower() 影响)。与
``reporting.requirement_parser``(ADR 0006 无共享 tokenizer)同源哲学。推断项一律只
进入 ``CausalIntent`` 并默认标 ``IMPLICIT_USER``——这是 anti-hallucination 的第一道闸:
推断不得冒充事实,假设/混淆未由用户确认前不得当显式。
"""

from __future__ import annotations

from data_analysis_agent.causal.model import (
    AssignmentMechanism,
    CausalIntent,
    ClaimLevel,
)

__all__ = ["parse_causal_intent", "infer_claim_level"]

# 干预/处理意图词:命中 → has_intervention(用户在问"X 是否影响/导致 Y")。
_INTERVENTION: tuple[str, ...] = (
    "导致",
    "引起",
    "造成",
    "驱动",
    "促使",
    "归因",
    "是否导致",
    "能否导致",
    "会不会",
    "影响",
    "作用",
    "有没有用",
    "cause",
    "causes",
    "caused",
    "drive",
    "drives",
    "affect",
    "affects",
    "impact",
    "lead to",
    "led to",
    "result in",
)

# 随机化/实验信号词:命中 → has_randomization_signal + assignment_hint=RANDOMIZED。
_RANDOMIZATION: tuple[str, ...] = (
    "实验组",
    "对照组",
    "处理组",
    "控制组",
    "随机",
    "分流",
    "随机分组",
    "分组",
    "实验",
    "ab测试",
    "a/b测试",
    "a/b test",
    "ab test",
    "ab-test",
    "a/b",
    "experiment",
    "randomized",
    "randomised",
    "random",
    "treatment",
    "control",
    "variant",
    "uplift",
    "rct",
    "placebo",
)

# 效应/提升意图词:命中 → wants_lift_or_effect。
_LIFT: tuple[str, ...] = (
    "提升",
    "提高",
    "增加",
    "下降",
    "降低",
    "上升",
    "变化",
    "增长",
    "下滑",
    "是否有效",
    "有没有效果",
    "效果",
    "改善",
    "回升",
    "涨",
    "跌",
    "lift",
    "increase",
    "decrease",
    "improve",
    "improvement",
    "decline",
    "change",
    "effect",
    "growth",
    "drop",
    "delta",
    "gain",
)

# 仅观察性表述:命中 → has_observation_marker(不得升级为 causal,默认 ASSOCIATIONAL)。
_OBSERVATION: tuple[str, ...] = (
    "相关",
    "关联",
    "正相关",
    "负相关",
    "伴随",
    "协同变化",
    "同步变化",
    "correlation",
    "correlated",
    "associated",
    "association",
    "relationship",
    "related to",
)

# 常见结果/业务指标词(信息性,供契约构建者匹配列名)。
_OUTCOME_TERMS: tuple[str, ...] = (
    "留存",
    "转化",
    "转化率",
    "收入",
    "营收",
    "购买",
    "消费",
    "复购",
    "付费",
    "活跃",
    "点击",
    "点击率",
    "崩溃",
    "会话",
    "订单",
    "客单价",
    "retention",
    "conversion",
    "revenue",
    "purchase",
    "gmv",
    "click",
    "crash",
    "sessions",
    "dau",
    "mau",
    "d1",
    "d7",
    "d30",
    "ctr",
)

# 处理/干预手段词(信息性,供契约构建者匹配处理列/动作)。
_TREATMENT_TERMS: tuple[str, ...] = (
    "实验组",
    "处理组",
    "干预",
    "投放",
    "活动",
    "策略",
    "版本",
    "新版",
    "功能",
    "优惠券",
    "推送",
    "补贴",
    "广告",
    "variant",
    "treatment",
    "campaign",
    "intervention",
    "feature",
    "version",
    "push",
    "coupon",
    "promo",
    "rollout",
)


def _contains(haystack: str, needle: str) -> bool:
    return needle in haystack


def _collect(haystack: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    """返回在 ``haystack`` 中出现且去重(保序)的词条。"""
    seen: list[str] = []
    for term in terms:
        if term in haystack and term not in seen:
            seen.append(term)
    return tuple(seen)


def parse_causal_intent(text: str) -> CausalIntent:
    """从 ``text`` 确定性抽取因果意图。

    全小写化后做子串匹配(中文不受影响,英文统一)。绝不臆测:未命中即 False/空。
    """
    if not isinstance(text, str):  # 防御:非字符串按空意图处理
        return CausalIntent()
    hay = text.lower()
    has_intervention = any(_contains(hay, w) for w in _INTERVENTION)
    has_randomization = any(_contains(hay, w) for w in _RANDOMIZATION)
    wants_lift = any(_contains(hay, w) for w in _LIFT)
    has_observation = any(_contains(hay, w) for w in _OBSERVATION)
    outcome_terms = _collect(hay, _OUTCOME_TERMS)
    treatment_terms = _collect(hay, _TREATMENT_TERMS)

    assignment_hint = (
        AssignmentMechanism.RANDOMIZED if has_randomization else AssignmentMechanism.UNKNOWN
    )

    fired: list[str] = []
    if has_intervention:
        fired.append("intervention")
    if has_randomization:
        fired.append("randomization")
    if wants_lift:
        fired.append("lift")
    if has_observation:
        fired.append("observation")
    rationale = "detected: " + ",".join(fired) if fired else "no causal signal"

    return CausalIntent(
        has_intervention=has_intervention,
        has_randomization_signal=has_randomization,
        wants_lift_or_effect=wants_lift,
        has_observation_marker=has_observation,
        assignment_hint=assignment_hint,
        detected_outcome_terms=outcome_terms,
        detected_treatment_terms=treatment_terms,
        rationale=rationale,
    )


def infer_claim_level(intent: CausalIntent, has_explicit_assumptions: bool) -> ClaimLevel:
    """把意图 + 是否有显式假设 映射到封闭 claim_level(确定性)。

    优先级:随机化 → EXPERIMENTAL;干预 + 显式假设 → CAUSAL_ASSUMPTION;
    干预或观察性表述(无随机化)→ ASSOCIATIONAL;否则 DESCRIPTIVE。
    """
    if intent.has_randomization_signal:
        return ClaimLevel.EXPERIMENTAL
    if intent.has_intervention and has_explicit_assumptions:
        return ClaimLevel.CAUSAL_ASSUMPTION
    if intent.has_intervention or intent.has_observation_marker:
        return ClaimLevel.ASSOCIATIONAL
    return ClaimLevel.DESCRIPTIVE
