"""表格能力域注册:委托 v1 tools + 持久内核(v2 认可的迁移模式).

每个能力包装「一个」v1 工具实例(tools/kernel 是能力侧代码,非 harness 内部),
以 harness 无关的 :class:`~data_analysis_agent.capabilities.contracts.CapabilitySpec`
契约对外服务。

``tabular_python_exec`` 完整保留 v1 持久内核语义:由 :class:`KernelHolder` 在
registry 生命周期内持有**同一个** :class:`PythonAnalysisTool` 实例(惰性创建、
绝不按调用重建),变量/DataFrame 跨调用存活;崩溃/超时重启并显式报告状态丢失、
启动失败降级受限子进程 —— 这些行为都在 PythonAnalysisTool/KernelManager 内部,
本层只持有实例,不重新实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...kernel.manager import KernelManager
from ...tools.base import Tool
from ...tools.data_profile import DataProfileTool
from ...tools.data_quality import DataQualityTool
from ...tools.file_read import FileReadTool
from ...tools.join_planner import JoinPlannerTool
from ...tools.metric_contract import MetricContractTool
from ...tools.nl_query import NlQueryTool
from ...tools.python_exec import PythonAnalysisTool
from ..contracts import (
    CapabilityError,
    CapabilityHandler,
    CapabilityOutput,
    CapabilityRegistry,
    CapabilitySpec,
    Permission,
)

#: 每个委托能力实际可能抛出的错误码(handler 依次产生;not_found 由 registry 兜底)。
_DECLARED_ERRORS = ("validation_error", "permission_denied", "execution_error")

_TABULAR_DOMAIN = "tabular"

_PYTHON_EXEC_DESCRIPTION = (
    "Execute Python code for data analysis in the persistent kernel — variables "
    "and DataFrames survive across calls within one serving process. Available "
    "libraries: pandas, numpy, matplotlib, seaborn, plotly. Use this for data "
    "transformation, statistical analysis, and visualization."
)


class KernelHolder:
    """registry/进程生命周期内持有唯一的持久内核 PythonAnalysisTool。

    惰性创建:注册阶段零副作用,首次 ``tabular_python_exec`` 调用才起内核;
    之后 ``tool`` 永远返回同一实例(内核状态因此跨调用存活)。降级链(启动失败
    → 永久 stateless 降级;超时/崩溃 → 重启 + 显式状态丢失报告)在工具内部。
    """

    def __init__(self, allowed_paths: list[str | Path] | None = None) -> None:
        self._allowed_paths = allowed_paths
        self._tool: PythonAnalysisTool | None = None

    @property
    def tool(self) -> PythonAnalysisTool:
        """The single tool instance, created on first use."""
        if self._tool is None:
            self._tool = PythonAnalysisTool(
                allowed_paths=self._allowed_paths,
                kernel=KernelManager(),
            )
        return self._tool

    async def shutdown(self) -> None:
        """Kill the kernel subprocess (its work_dir / artifacts are kept)."""
        kernel = self._tool.kernel if self._tool is not None else None
        if kernel is not None:
            await kernel.shutdown()


async def _delegate(tool: Tool, input_data: dict[str, Any]) -> CapabilityOutput:
    """把一次 v1 工具调用适配成能力契约结果。

    镜像 v1 tool-gate 顺序(权限自检 → 输入校验 → ``call``):``Tool.call()``
    假定校验已在上游完成 —— 跳过 ``validate_input`` 会整体绕过 python_exec
    的沙箱黑名单/AST 校验,因此这里必须先校验再调用。
    """
    perm = tool.check_permissions(input_data)
    if not perm.allowed:
        raise CapabilityError("permission_denied", f"Permission denied: {perm.reason}")
    validation = tool.validate_input(input_data)
    if not validation.valid:
        raise CapabilityError("validation_error", validation.error or "invalid input")
    result = await tool.call(input_data)
    if result.is_error:
        raise CapabilityError("execution_error", result.content or "tool failed")
    return CapabilityOutput(
        content=result.content,
        metadata=dict(result.metadata),
        artifacts=tuple(str(p) for p in result.metadata.get("artifact_paths", ())),
    )


def _bound(tool: Tool) -> CapabilityHandler:
    """Bind one tool instance into a capability handler (shared, stateless)."""

    async def handler(input_data: dict[str, Any]) -> CapabilityOutput:
        return await _delegate(tool, input_data)

    return handler


def _spec(tool: Tool, name: str) -> CapabilitySpec:
    """CapabilitySpec mirroring the delegated tool's description + schema."""
    return CapabilitySpec(
        name=name,
        description=tool.description,
        input_schema=tool.input_schema,
        domain=_TABULAR_DOMAIN,
        permission=Permission.READ_ONLY,
        error_codes=_DECLARED_ERRORS,
    )


def _python_exec_schema() -> dict[str, Any]:
    """Mirror of PythonAnalysisTool.input_schema, built without instantiation."""
    return {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Maximum execution time in seconds "
                    f"(default {PythonAnalysisTool.DEFAULT_TIMEOUT})"
                ),
            },
        },
        "required": ["code"],
    }


def register_all(
    registry: CapabilityRegistry,
    *,
    allowed_roots: list[Path] | None = None,
    kernel: KernelHolder | None = None,
) -> list[str]:
    """注册全部 7 个表格能力,按声明顺序返回能力名。

    ``allowed_roots`` 作为路径白名单下发给每个需要它的工具(缺省 cwd);
    ``kernel`` 允许调用方注入自定义 holder(便于生命周期管理/测试),
    缺省时为本次注册新建一个 —— 一次 register_all 一个持久内核,不跨 registry
    共享状态。
    """
    roots: list[str | Path] = [
        Path(p).expanduser().resolve() for p in (allowed_roots or [Path.cwd()])
    ]
    holder = kernel if kernel is not None else KernelHolder(allowed_paths=roots)

    async def python_exec_handler(input_data: dict[str, Any]) -> CapabilityOutput:
        # holder.tool 惰性创建一次,之后恒为同一实例 —— 内核状态跨调用存活。
        return await _delegate(holder.tool, input_data)

    read_file = FileReadTool(allowed_paths=roots)
    data_profile = DataProfileTool(allowed_paths=roots)
    data_quality = DataQualityTool(allowed_paths=roots)
    join_planner = JoinPlannerTool(allowed_paths=roots)
    metric_contract = MetricContractTool()
    nl_query = NlQueryTool()

    entries: list[tuple[CapabilitySpec, CapabilityHandler]] = [
        (_spec(read_file, "tabular_read_file"), _bound(read_file)),
        (_spec(data_profile, "tabular_data_profile"), _bound(data_profile)),
        (_spec(data_quality, "tabular_data_quality"), _bound(data_quality)),
        (_spec(join_planner, "tabular_join_plan"), _bound(join_planner)),
        (_spec(metric_contract, "tabular_metric_contract"), _bound(metric_contract)),
        (_spec(nl_query, "tabular_nl_query"), _bound(nl_query)),
        (
            CapabilitySpec(
                name="tabular_python_exec",
                description=_PYTHON_EXEC_DESCRIPTION,
                input_schema=_python_exec_schema(),
                domain=_TABULAR_DOMAIN,
                permission=Permission.EXECUTES_CODE,
                error_codes=_DECLARED_ERRORS,
            ),
            python_exec_handler,
        ),
    ]
    names: list[str] = []
    for spec, handler in entries:
        registry.register(spec, handler)
        names.append(spec.name)
    return names
