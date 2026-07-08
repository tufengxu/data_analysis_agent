"""因果决策领域层(纯 stdlib)。

CausalQuestion / CausalIntent / CausalContract / CausalReadiness / QA /
EffectEstimate / SRMResult / ContrastResult / ExperimentReadout / ActionPlan。
单向依赖 ``reporting``(复用 ``Serializable`` mixin 与 ``SourceKind``),不依赖任何
运行时/工具/技能/进化/记忆模块;见 ADR 0010 与 plan
docs/superpowers/plans/2026-07-08-causal-decision-stage1-executable.md。
"""
