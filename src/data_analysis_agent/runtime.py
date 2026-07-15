"""Composition root: assemble a ready-to-run agent from configuration.

One place builds the agent. Both the CLI (__main__) and the offline evaluator
go through ``AgentRuntime.from_config`` so the agent they run is the SAME agent
— same tool set, same wiring — differing only by explicit knobs (client
override, extra skills, fixture paths) and the config's own feature switches
(kernel / memory / telemetry). This kills the class of "eval ran a different,
lighter agent than production" drift.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_loop import AgentLoop, AgentLoopConfig, ApprovalHandler
from .artifacts import ArtifactStore
from .config import AgentConfig
from .context.compression import ContextCompressor
from .kernel import KernelManager
from .memory import MemoryInjector, MemoryStore, ProfileStore
from .persistence import MessageStore
from .security.permissions import (
    PermissionBehavior,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from .security.sanitizer import has_numeric_leak
from .session import AgentSession
from .skills.base import Skill
from .skills.builtin import (
    CorrelationAnalysisSkill,
    DescriptiveAnalysisSkill,
    JointAnalysisSkill,
    ReportGenerationSkill,
    TrendAnalysisSkill,
)
from .skills.causal_skill import CausalDecisionAnalysisSkill
from .skills.loader import load_skills
from .skills.registry import SkillRegistry
from .telemetry import TrajectoryLogger
from .tools import (
    CausalActionPlanTool,
    CausalContractTool,
    CausalQATool,
    CausalReportTool,
    ChartRenderTool,
    DataProfileTool,
    ExperimentReadoutTool,
    FileReadTool,
    HtmlReportTool,
    NlQueryTool,
    PythonAnalysisTool,
    ReportContextTool,
    ReportContractTool,
    ReportNeedTool,
    ToolRegistry,
    VisualizationTool,
)
from .tools.retrieve_result import RetrieveResultTool

# Tools that never mutate state; auto-allowed in default permission mode.
READ_ONLY_TOOLS = (
    "read_file",
    "data_profile",
    "nl_query",
    "retrieve_result",
    "report_need",
    "report_context",
    "report_contract",
    "causal_contract",
    "causal_qa",
    "experiment_readout",
    "causal_action_plan",
    "causal_report",
)


def build_message_store(persist_path: str | Path | None) -> MessageStore | None:
    """Build a message store when persistence is requested."""
    return MessageStore(persist_path) if persist_path else None


def build_registry(
    config: AgentConfig | None = None,
    result_store: Any = None,
    kernel: KernelManager | None = None,
    artifact_dir: Path | None = None,
    analysis_paths: Sequence[str | Path] | None = None,
) -> ToolRegistry:
    """Build the full built-in tool set (same set everywhere it is assembled)."""
    registry = ToolRegistry()
    sampling_config = config.sampling_config() if config else None
    echarts_src = config.echarts_src if config else None
    paths = list(analysis_paths) if analysis_paths else None
    registry.register(FileReadTool(allowed_paths=paths))
    registry.register(DataProfileTool(allowed_paths=paths))
    registry.register(
        PythonAnalysisTool(
            sampling_config=sampling_config,
            kernel=kernel,
            allowed_paths=paths,
        )
    )
    registry.register(NlQueryTool())
    registry.register(VisualizationTool(artifact_dir=artifact_dir))
    registry.register(RetrieveResultTool(result_store=result_store))
    report_kwargs = {"echarts_src": echarts_src} if echarts_src else {}
    registry.register(HtmlReportTool(artifact_dir=artifact_dir, **report_kwargs))
    registry.register(ReportNeedTool())
    registry.register(ReportContextTool())
    registry.register(ReportContractTool())
    registry.register(CausalContractTool())
    registry.register(CausalQATool())
    registry.register(ExperimentReadoutTool())
    registry.register(CausalActionPlanTool())
    registry.register(CausalReportTool())
    registry.register(ChartRenderTool(artifact_dir=artifact_dir))

    if config:
        for pattern in config.deny_patterns:
            registry.add_deny_pattern(pattern)
        if config.permission_mode == "plan":
            registry.add_deny_pattern("python_analysis")
            registry.add_deny_pattern("visualization")
            registry.add_deny_pattern("html_report")
            registry.add_deny_pattern("chart_render")

    return registry


def build_skill_registry(
    skills_dir: Path | None = None, *, extra_skills: Sequence[Skill] = ()
) -> SkillRegistry:
    """Built-in skills (always) + active declarative skills + any extras."""
    skills = SkillRegistry()
    skills.register(DescriptiveAnalysisSkill())
    skills.register(CorrelationAnalysisSkill())
    skills.register(TrendAnalysisSkill())
    skills.register(ReportGenerationSkill())
    skills.register(JointAnalysisSkill())
    skills.register(CausalDecisionAnalysisSkill())
    if skills_dir is not None:
        for declarative in load_skills(skills_dir, statuses=("active",)):
            skills.register(declarative)
    for extra in extra_skills:
        skills.register(extra)
    return skills


def build_permission_engine(config: AgentConfig) -> PermissionEngine | None:
    """Build permission rules from runtime configuration.

    Default mode without deny rules preserves the non-interactive CLI behavior
    (no engine). Once permission config is present the engine is genuinely
    deny-first: read-only tools are allowed, everything else falls through to
    the engine's default ASK — answered by the approval handler when one is
    configured, denied otherwise. AUTO mode auto-approves by definition.
    """
    mode_map = {
        "default": PermissionMode.DEFAULT,
        "plan": PermissionMode.PLAN,
        "auto": PermissionMode.AUTO,
        "bypass": PermissionMode.BYPASS,
    }
    mode = mode_map.get(config.permission_mode, PermissionMode.DEFAULT)

    if mode == PermissionMode.DEFAULT and not config.deny_patterns:
        return None

    engine = PermissionEngine(mode=mode)
    for pattern in config.deny_patterns:
        engine.add_rule(PermissionRule(pattern, PermissionBehavior.DENY))

    if mode == PermissionMode.PLAN:
        engine.add_rule(PermissionRule("python_analysis", PermissionBehavior.DENY))
        engine.add_rule(PermissionRule("visualization", PermissionBehavior.DENY))
        engine.add_rule(PermissionRule("html_report", PermissionBehavior.DENY))
        engine.add_rule(PermissionRule("chart_render", PermissionBehavior.DENY))
        for name in READ_ONLY_TOOLS:
            engine.add_rule(PermissionRule(name, PermissionBehavior.ALLOW))
    elif mode == PermissionMode.AUTO:
        engine.add_rule(PermissionRule("*", PermissionBehavior.ALLOW))
    else:  # DEFAULT with deny patterns: deny-first, read-only allowed, rest ASK
        for name in READ_ONLY_TOOLS:
            engine.add_rule(PermissionRule(name, PermissionBehavior.ALLOW))

    return engine


def _build_memory_injector(config: AgentConfig) -> MemoryInjector | None:
    """The MemoryInjector when memory is on, else None.

    Returning the object (not just its callbacks) lets the composition root
    expose it on AgentRuntime so the CLI's /define and /pref commands write
    through the SAME store the read-side injection uses (in-session visible).
    """
    if not config.enable_memory:
        return None
    return MemoryInjector(
        ProfileStore(config.memory_dir()),
        MemoryStore(config.memory_dir(), leak_check=has_numeric_leak),
        budget_tokens=config.memory_inject_budget_tokens,
    )


@dataclass
class AgentRuntime:
    """All session-scoped components, assembled once."""

    session: AgentSession
    loop: AgentLoop
    kernel: KernelManager | None
    artifacts_dir: Path
    memory_injector: MemoryInjector | None = None

    async def shutdown(self) -> None:
        if self.kernel is not None:
            await self.kernel.shutdown()

    @classmethod
    def from_config(
        cls,
        config: AgentConfig,
        *,
        persist_path: str | Path | None = None,
        approval_handler: ApprovalHandler | None = None,
        client: Any = None,
        extra_skills: Sequence[Skill] = (),
        analysis_paths: Sequence[str | Path] | None = None,
    ) -> AgentRuntime:
        """Assemble loop + session from config. Feature switches (kernel/memory/
        telemetry) come from the config; one-off overrides are explicit kwargs."""
        loop_config = AgentLoopConfig(
            system_prompt=config.system_prompt,
            max_turns=config.max_turns,
            max_tokens=config.max_tokens,
            model=config.model,
            api_key=config.api_key,
        )
        sampling_config = config.sampling_config()
        kernel: KernelManager | None = None
        if config.persistent_kernel:
            kernel = KernelManager(
                sampling_config=sampling_config,
                work_dir=config.kernel_work_dir(persist_path),
            )
        artifacts_dir = config.artifacts_dir(persist_path)
        result_store = config.result_store(persist_path)
        registry = build_registry(
            config,
            result_store=result_store,
            kernel=kernel,
            artifact_dir=artifacts_dir,
            analysis_paths=analysis_paths,
        )
        compressor = ContextCompressor(
            budget_tokens=config.context_budget_tokens,
            enable_snip=True,
            enable_collapse=True,
        )
        store = build_message_store(persist_path)
        injector = _build_memory_injector(config)
        memory_injector = injector.render if injector is not None else None
        memory_recorder = injector.record_tool if injector is not None else None

        loop = AgentLoop(
            loop_config,
            registry,
            compressor=compressor,
            store=store,
            skill_registry=build_skill_registry(config.skills_dir(), extra_skills=extra_skills),
            permission_engine=build_permission_engine(config),
            client=client,
            sampling_config=sampling_config,
            result_store=result_store,
            approval_handler=approval_handler,
            artifact_store=ArtifactStore(artifacts_dir),
            memory_injector=memory_injector,
            memory_recorder=memory_recorder,
        )

        if store is not None and len(store) > 0:
            session = AgentSession.resume(loop, store)
        else:
            session = AgentSession(loop, store)
        if injector is not None:
            session.memory_adjudicator = injector.adjudicate
        if config.enable_telemetry:
            session.trajectory_logger = TrajectoryLogger(
                config.trajectories_dir(),
                session.meta.session_id,
                enable_inputs=config.enable_trajectory_inputs,
                analysis_paths=analysis_paths,
            )
        return cls(
            session=session,
            loop=loop,
            kernel=kernel,
            artifacts_dir=artifacts_dir,
            memory_injector=injector,
        )
