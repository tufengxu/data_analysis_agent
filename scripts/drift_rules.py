"""Data-driven rules for the deterministic drift checks.

Edit here to evolve architecture guarantees. ``who`` matches a module whose
dotted name equals it or starts with ``who + "."``; ``forbid`` lists dotted
prefixes that such modules must not import.
"""

from __future__ import annotations

IMPORT_RULES: list[dict[str, object]] = [
    # v2 capability core: harness 无关。可委托 v1 能力侧包(tools/kernel/...),
    # 但禁入一切 harness 内部(规范 3.1 五模块 + 装配/表现层)与三个物理迁移域的
    # v1 re-export shim(防循环 import)。
    {
        "who": "data_analysis_agent.capabilities",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.session",
            "data_analysis_agent.state_machine",
            "data_analysis_agent.protocol",
            "data_analysis_agent.events",
            "data_analysis_agent.runtime",
            "data_analysis_agent.config",
            "data_analysis_agent.persistence",
            "data_analysis_agent.recovery",
            "data_analysis_agent.security",
            "data_analysis_agent.context",
            "data_analysis_agent.server",
            "data_analysis_agent.web",
            "data_analysis_agent.__main__",
            # sampling 物理迁移后其 v1 路径是 shim,能力层禁反向 import(防循环)。
            # causal/reporting 采用委托式迁移:纯领域层本体即实现,能力层单向复用。
            "data_analysis_agent.sampling",
        ],
    },
    {
        "who": "data_analysis_agent.sampling",
        "forbid": [
            "data_analysis_agent.tools",
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.skills",
            "data_analysis_agent.security",
            "data_analysis_agent.context",
        ],
    },
    # v2 物理迁移后的 sampling 能力域:镜像 v1 sampling 规则(纯 stdlib + 可选 pandas,
    # 禁耦合 tools/skills/security/context 等)。
    {
        "who": "data_analysis_agent.capabilities.sampling",
        "forbid": [
            "data_analysis_agent.tools",
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.skills",
            "data_analysis_agent.security",
            "data_analysis_agent.context",
            "data_analysis_agent.kernel",
        ],
    },
    {
        "who": "data_analysis_agent.capabilities.sampling.sandbox_summary",
        "forbid": ["data_analysis_agent"],
    },
    # (v1 ``sampling/sandbox_summary.py`` 现为 shim,自包含约束由 capabilities 路径守护)
    {
        "who": "data_analysis_agent.tools",
        "forbid": ["data_analysis_agent.agent_loop"],
    },
    {
        "who": "data_analysis_agent.protocol",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
        ],
    },
    {
        "who": "data_analysis_agent.kernel",
        "forbid": [
            "data_analysis_agent.tools",
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.skills",
            "data_analysis_agent.security",
            "data_analysis_agent.context",
        ],
    },
    {
        "who": "data_analysis_agent.kernel.kernel_main",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.artifacts",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.jsonl_store",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.pii",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.disk_cap",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.telemetry",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.protocol",
            "data_analysis_agent.security",
        ],
    },
    {
        "who": "data_analysis_agent.memory",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.protocol",
            "data_analysis_agent.security",
        ],
    },
    {
        # synthesizer stays protocol-free (reflection injected); only the
        # offline entry point evolution/__main__ may reach protocol.
        "who": "data_analysis_agent.evolution.synthesizer",
        "forbid": [
            "data_analysis_agent.protocol",
            "data_analysis_agent.agent_loop",
        ],
    },
    # The loop reaches telemetry/memory/evolution only through callbacks, never
    # by import — these rules enforce that decoupling as a standing invariant.
    {
        "who": "data_analysis_agent.agent_loop",
        "forbid": [
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.runtime",
        ],
    },
    # session may hold a TrajectoryLogger (telemetry) but not memory/evolution.
    {
        "who": "data_analysis_agent.session",
        "forbid": [
            "data_analysis_agent.evolution",
            "data_analysis_agent.memory",
            "data_analysis_agent.runtime",
        ],
    },
    {
        "who": "data_analysis_agent.tools",
        "forbid": [
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.runtime",
        ],
    },
    {"who": "data_analysis_agent.memory", "forbid": ["data_analysis_agent.evolution"]},
    {"who": "data_analysis_agent.telemetry", "forbid": ["data_analysis_agent.evolution"]},
    # The tool-authorization seam is depended-on BY agent_loop; importing back
    # would be circular and re-couple the seam it exists to decouple.
    {
        "who": "data_analysis_agent.security.tool_gate",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.runtime",
            "data_analysis_agent.session",
        ],
    },
    # The recovery-policy seam is likewise depended-on BY agent_loop.
    {
        "who": "data_analysis_agent.recovery",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.runtime",
            "data_analysis_agent.session",
        ],
    },
    # reporting 是纯 stdlib 领域层(报告契约/文档/QA);tools 可单向依赖它,
    # 其本身不得反向耦合任何内部包。不能改用 catch-all `forbid:["data_analysis_agent"]`,
    # 否则会误伤包内 `from .model import ...`(解析为 ...reporting.model,命中前缀)。
    # 见 ADR 0009。
    {
        "who": "data_analysis_agent.reporting",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.runtime",
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.session",
            "data_analysis_agent.kernel",
            "data_analysis_agent.context",
            "data_analysis_agent.security",
            "data_analysis_agent.sampling",
            "data_analysis_agent.persistence",
            "data_analysis_agent.state_machine",
            "data_analysis_agent.events",
            "data_analysis_agent.config",
            "data_analysis_agent.recovery",
            "data_analysis_agent.jsonl_store",
            "data_analysis_agent.artifacts",
            "data_analysis_agent.__main__",
            "data_analysis_agent.web",
            "data_analysis_agent.causal",
        ],
    },
    # causal 是纯 stdlib 因果决策领域层(契约/QA/实验统计/报告适配),单向依赖 reporting
    # (复用 Serializable mixin + 作为 ReportDocument 渲染目标);禁依赖其余一切内部包。
    # reporting 不在禁入表(刻意:causal 在 reporting 之上,同向依赖)。不能改用 catch-all
    # `forbid:["data_analysis_agent"]`(会误伤包内 `from .model import ...` 相对导入)。见 ADR 0010。
    {
        "who": "data_analysis_agent.causal",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.runtime",
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.session",
            "data_analysis_agent.kernel",
            "data_analysis_agent.context",
            "data_analysis_agent.security",
            "data_analysis_agent.sampling",
            "data_analysis_agent.persistence",
            "data_analysis_agent.state_machine",
            "data_analysis_agent.events",
            "data_analysis_agent.config",
            "data_analysis_agent.recovery",
            "data_analysis_agent.jsonl_store",
            "data_analysis_agent.artifacts",
            "data_analysis_agent.__main__",
            "data_analysis_agent.web",
        ],
    },
    # web 是表现层(FastAPI workbench),消费 reporting 域层 + fastapi/starlette/pydantic;
    # 禁依赖一切运行时/工具/技能/进化/记忆等内部包(同 reporting 的纯方向,但 web 在
    # reporting 之上,故 reporting 不在 web 的禁入表)。见 Wave 8 plan。
    {
        "who": "data_analysis_agent.web",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.runtime",
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.session",
            "data_analysis_agent.kernel",
            "data_analysis_agent.context",
            "data_analysis_agent.security",
            "data_analysis_agent.sampling",
            "data_analysis_agent.persistence",
            "data_analysis_agent.state_machine",
            "data_analysis_agent.events",
            "data_analysis_agent.config",
            "data_analysis_agent.recovery",
            "data_analysis_agent.jsonl_store",
            "data_analysis_agent.artifacts",
            "data_analysis_agent.__main__",
        ],
    },
    # server 是 live-agent workbench 表现层(Wave2):组合 runtime(与 CLI 同源)+ SSE 事件流,
    # 复用 web 的纯 reporting 表现层不变。禁直接耦合 agent_loop/protocol/tools 等(走 runtime
    # 接缝)。web 不在禁入表(server 在 web 之上,可单向复用其静态/预览)。事件 codec 映射 events。
    {
        "who": "data_analysis_agent.server",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
            "data_analysis_agent.session",
            "data_analysis_agent.kernel",
            "data_analysis_agent.context",
            "data_analysis_agent.security",
            "data_analysis_agent.sampling",
            "data_analysis_agent.persistence",
            "data_analysis_agent.state_machine",
            "data_analysis_agent.evolution",
            "data_analysis_agent.telemetry",
            "data_analysis_agent.memory",
            "data_analysis_agent.recovery",
            "data_analysis_agent.reporting",
            "data_analysis_agent.causal",
        ],
    },
]

# Documents scanned for dead repo-path references.
DOC_FILES: list[str] = [
    "README.md",
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/V2_RUNBOOK.md",
    "docs/THIRD_HARNESS_GUIDE.md",
]

# god-file warning threshold (lines of code). Phase 1: warn only.
FILE_SIZE_LIMIT = 600
