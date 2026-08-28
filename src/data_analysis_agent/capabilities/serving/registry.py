"""全量能力装配:五域 register_all + sampling 压缩/召回两个 serving 级能力。

环境变量(全部可选):
    DAA_CAPABILITIES_HOME          ResultStore 目录(默认 ~/.daa/capabilities/result-store)
    DAA_CAPABILITIES_ARTIFACTS     报告/图表产物根目录(默认 <cwd>/daa-capabilities-artifacts)
    DAA_CAPABILITIES_ALLOWED_ROOTS tabular 路径白名单(冒号分隔,默认 cwd)
    DAA_CAPABILITIES_EVOLUTION_ROOT 轨迹 v2 写入根(默认 DAA_HOME/trajectories/v2)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from data_analysis_agent.capabilities.causal import register_all as register_causal
from data_analysis_agent.capabilities.contracts import (
    CapabilityError,
    CapabilityOutput,
    CapabilityRegistry,
    CapabilitySpec,
    OutputKind,
    Permission,
)
from data_analysis_agent.capabilities.evolution import register_all as register_evolution
from data_analysis_agent.capabilities.reporting import register_all as register_reporting
from data_analysis_agent.capabilities.sampling import (
    CompactRequest,
    DefaultToolResultCompactor,
    ResultStore,
    SamplingConfig,
)
from data_analysis_agent.capabilities.sampling.result_store import RetrievedPage
from data_analysis_agent.capabilities.sampling.slicing import render_slice, slice_stored_table
from data_analysis_agent.capabilities.tabular import register_all as register_tabular


def default_store_dir() -> Path:
    env = os.environ.get("DAA_CAPABILITIES_HOME")
    if env:
        return Path(env)
    return Path.home() / ".daa" / "capabilities" / "result-store"


def default_artifact_root() -> Path:
    env = os.environ.get("DAA_CAPABILITIES_ARTIFACTS")
    return Path(env) if env else Path.cwd() / "daa-capabilities-artifacts"


def default_allowed_roots() -> list[Path]:
    env = os.environ.get("DAA_CAPABILITIES_ALLOWED_ROOTS")
    if env:
        return [Path(part) for part in env.split(":") if part.strip()]
    return [Path.cwd()]


def default_evolution_root() -> Path | None:
    env = os.environ.get("DAA_CAPABILITIES_EVOLUTION_ROOT")
    return Path(env) if env else None


def _sampling_config_from(input_data: dict[str, Any]) -> SamplingConfig:
    """fidelity_level 档位 + 显式阈值覆盖(基座侧可配置项,默认值与 v1 一致)。"""

    level = input_data.get("fidelity_level")
    config = SamplingConfig.for_fidelity(str(level)) if level else SamplingConfig()
    overrides = input_data.get("config_overrides")
    if isinstance(overrides, dict):
        allowed = {
            field
            for field in (
                "trigger_chars",
                "trigger_pressure_scale",
                "trigger_floor_chars",
                "adaptive_fidelity",
                "render_format",
                "max_sample_rows",
                "top_k",
                "quantiles",
                "stratify",
                "include_outliers",
                "max_outlier_rows",
                "seed",
                "trigger_rows",
                "gate_ratio_low_pressure",
                "gate_ratio_high_pressure",
            )
            if field in overrides
        }
        config = SamplingConfig(**{**config.__dict__, **{k: overrides[k] for k in allowed}})
    return config


def register_sampling_capabilities(
    registry: CapabilityRegistry, *, store: ResultStore
) -> list[str]:
    """`sampling_compact_result`(ToolResultCompactor 接缝)与 `retrieve_result`(分页召回)。"""

    compactor = DefaultToolResultCompactor(store)

    async def _compact(input_data: dict[str, Any]) -> CapabilityOutput:
        content = input_data.get("content")
        if not isinstance(content, str) or not content:
            raise CapabilityError("validation_error", "content (非空字符串) is required")
        try:
            max_chars = int(input_data.get("max_chars", 50_000))
        except (TypeError, ValueError):
            raise CapabilityError("validation_error", "max_chars must be an integer") from None
        request = CompactRequest(
            content=content,
            max_chars=max_chars,
            context_pressure=float(input_data.get("context_pressure", 0.0)),
            config=_sampling_config_from(input_data),
            result_id=input_data.get("result_id") or None,
            tool_name=str(input_data.get("tool_name", "")),
        )
        result = compactor.compact(request)
        return CapabilityOutput(
            content=result.content,
            data={
                "was_compacted": result.was_compacted,
                "result_id": result.result_id,
                "sampling_method": result.sampling_method,
                "fidelity_level": result.fidelity_level,
                "original_chars": len(content),
                "compacted_chars": len(result.content),
            },
            metadata={"domain": "sampling"},
        )

    async def _retrieve(input_data: dict[str, Any]) -> CapabilityOutput:
        result_id = input_data.get("result_id")
        if not isinstance(result_id, str) or not result_id:
            raise CapabilityError("validation_error", "result_id is required")
        try:
            offset = int(input_data.get("offset", 0))
            limit = int(input_data.get("limit", 50))
        except (TypeError, ValueError):
            raise CapabilityError("validation_error", "offset/limit must be integers") from None
        if offset < 0 or not 1 <= limit <= 500:
            raise CapabilityError("validation_error", "offset >= 0 and 1 <= limit <= 500 required")
        query = input_data.get("query")
        mode = input_data.get("mode", "page")
        if mode != "page":
            content = store.fetch_content(result_id)
            if content is None:
                raise CapabilityError("not_found", f"result_id 不存在或已过期: {result_id}")
            try:
                table = slice_stored_table(
                    content,
                    result_id=result_id,
                    tool="",
                    mode=str(mode),
                    columns=input_data.get("columns")
                    if isinstance(input_data.get("columns"), list)
                    else None,
                    filter_text=input_data.get("filter")
                    if isinstance(input_data.get("filter"), str)
                    else None,
                    limit=limit,
                )
            except ValueError as exc:
                raise CapabilityError("validation_error", str(exc)) from None
            return CapabilityOutput(
                content=render_slice(table),
                data={
                    "mode": table.mode,
                    "matched_rows": table.matched,
                    "total_rows": table.total,
                    "returned_rows": len(table.rows),
                },
                metadata={"domain": "sampling"},
            )
        page: RetrievedPage | None = store.get(
            result_id, offset=offset, limit=limit, query=query if isinstance(query, str) else None
        )
        if page is None:
            raise CapabilityError("not_found", f"result_id 不存在或已过期: {result_id}")
        return CapabilityOutput(
            content=page.text,
            data={
                "total_lines": page.total_lines,
                "matched_lines": page.matched_lines,
                "returned_lines": page.returned_lines,
                "offset": page.offset,
                "truncated": page.truncated,
            },
            metadata={"domain": "sampling"},
        )

    compact_spec = CapabilitySpec(
        name="sampling_compact_result",
        description=(
            "压缩超大工具结果为结构化采样摘要(分层/离群/代表行;压力自适应增益门控),"
            "原文入 ResultStore 产生召回句柄。fidelity_level 与阈值可配置。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "原始工具结果文本"},
                "max_chars": {"type": "integer", "description": "长度预算(默认 50000)"},
                "context_pressure": {"type": "number", "description": "上下文压力 0..1"},
                "fidelity_level": {"type": "string", "enum": ["low", "mid", "high"]},
                "config_overrides": {"type": "object", "description": "SamplingConfig 显式覆盖"},
                "result_id": {"type": "string", "description": "预分配召回句柄"},
                "tool_name": {"type": "string"},
            },
            "required": ["content"],
        },
        domain="sampling",
        output_kind=OutputKind.STRUCTURED,
        permission=Permission.READ_ONLY,
        error_codes=("validation_error", "execution_error"),
    )
    retrieve_spec = CapabilitySpec(
        name="retrieve_result",
        description=(
            "按行分页回取被压缩前的原始工具结果(支持 query 过滤);"
            "或结构化切片:mode=head|tail|sample + columns 投影 + 单谓词 filter。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "result_id": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["page", "head", "tail", "sample"]},
                "columns": {"type": "array", "items": {"type": "string"}},
                "filter": {"type": "string", "description": "col op value (op: > >= < <= == !=)"},
            },
            "required": ["result_id"],
        },
        domain="sampling",
        output_kind=OutputKind.TEXT,
        permission=Permission.READ_ONLY,
        error_codes=("validation_error", "not_found", "execution_error"),
    )
    registry.register(compact_spec, _compact)
    registry.register(retrieve_spec, _retrieve)
    return [compact_spec.name, retrieve_spec.name]


def build_registry(
    *,
    artifact_root: Path | None = None,
    allowed_roots: list[Path] | None = None,
    store_dir: Path | None = None,
    evolution_root: Path | None = None,
) -> CapabilityRegistry:
    """全量装配(进程内直调 / MCP / CLI 三传输共用同一入口)。"""

    registry = CapabilityRegistry()
    names: list[str] = []
    names += register_tabular(registry, allowed_roots=allowed_roots or default_allowed_roots())
    names += register_causal(registry)
    names += register_reporting(registry, artifact_root=artifact_root or default_artifact_root())
    store = ResultStore(store_dir or default_store_dir())
    names += register_sampling_capabilities(registry, store=store)
    names += register_evolution(registry, root=evolution_root or default_evolution_root())
    _ = names
    return registry
