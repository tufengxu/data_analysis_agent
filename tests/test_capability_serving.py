"""serving 层测试:注册表全量装配 + 三传输一致性(进程内 / MCP / CLI)。

MCP 传输用 mcp 官方 ClientSession 经 anyio 内存流直连低层 server(与 stdio 框架
层共用同一 Server/handler 代码);stdio 框架层由 examples/v2 冒烟脚本另行验证。
CLI 传输用 typer CliRunner 进程内驱动同一 console-script app。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from data_analysis_agent.capabilities.contracts import CapabilityRegistry
from data_analysis_agent.capabilities.serving.cli import app
from data_analysis_agent.capabilities.serving.registry import build_registry

pytest.importorskip("mcp.client.session", reason="serving extra 未安装")

EXPECTED_CAPABILITIES = {
    "tabular_read_file",
    "tabular_data_profile",
    "tabular_data_quality",
    "tabular_join_plan",
    "tabular_metric_contract",
    "tabular_nl_query",
    "tabular_python_exec",
    "causal_analyze",
    "causal_estimate",
    "causal_report",
    "reporting_report_need",
    "reporting_report_context",
    "reporting_report_contract",
    "reporting_render_chart",
    "reporting_render_html",
    "sampling_compact_result",
    "retrieve_result",
    "evolution_record_event",
    "evolution_verify_trajectory",
}


def _big_csv(rows: int = 1500) -> str:
    lines = ["region,product,units,price"]
    for i in range(rows):
        lines.append(f"r{i % 7},p{i % 13},{i},{(i % 11) * 1.5:.2f}")
    return "\n".join(lines)


@pytest.fixture()
def env_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    dirs = {
        "DAA_CAPABILITIES_HOME": str(tmp_path / "store"),
        "DAA_CAPABILITIES_ARTIFACTS": str(tmp_path / "artifacts"),
        "DAA_CAPABILITIES_EVOLUTION_ROOT": str(tmp_path / "traj"),
        "DAA_CAPABILITIES_ALLOWED_ROOTS": str(tmp_path),
    }
    for key, value in dirs.items():
        monkeypatch.setenv(key, value)
    return dirs


@pytest.fixture()
def full_registry(env_dirs: dict[str, str]) -> CapabilityRegistry:
    return build_registry()


@asynccontextmanager
async def mcp_session() -> AsyncIterator[Any]:
    """ClientSession ↔ 低层 server,anyio 内存流直连(同一任务组内启停)。"""

    import anyio
    from mcp.client.session import ClientSession

    from data_analysis_agent.capabilities.serving.mcp_server import build_mcp_server

    registry = build_registry()
    server = build_mcp_server(registry)
    client_to_server_send, client_to_server_recv = anyio.create_memory_object_stream(0)
    server_to_client_send, server_to_client_recv = anyio.create_memory_object_stream(0)

    async with anyio.create_task_group() as tg:

        async def _run_server() -> None:
            await server.run(
                client_to_server_recv,
                server_to_client_send,
                server.create_initialization_options(),
                raise_exceptions=False,
            )

        tg.start_soon(_run_server)
        async with ClientSession(server_to_client_recv, client_to_server_send) as session:
            init_result = await session.initialize()
            assert init_result.server_info.name == "data-analysis-agent-capabilities"
            yield session
        tg.cancel_scope.cancel()


async def _mcp_call(session: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    assert result.content, name
    return json.loads(result.content[0].text)


class TestAssembly:
    def test_all_domains_registered(self, full_registry: CapabilityRegistry) -> None:
        names = {spec.name for spec in full_registry.specs()}
        assert names >= EXPECTED_CAPABILITIES

    async def test_compact_and_retrieve_roundtrip(self, full_registry: CapabilityRegistry) -> None:
        envelope = await full_registry.dispatch(
            "sampling_compact_result",
            {
                "content": _big_csv(),
                "context_pressure": 0.9,
                "result_id": "srv-1",
                "tool_name": "demo",
            },
        )
        assert envelope["ok"] is True
        assert envelope["data"]["was_compacted"] is True
        rid = envelope["data"]["result_id"]
        page = await full_registry.dispatch(
            "retrieve_result", {"result_id": rid, "offset": 0, "limit": 10}
        )
        assert page["ok"] is True
        assert page["data"]["returned_lines"] == 10
        assert page["content"].startswith("[result_id=")

    async def test_retrieve_missing_is_not_found(self, full_registry: CapabilityRegistry) -> None:
        envelope = await full_registry.dispatch("retrieve_result", {"result_id": "nope"})
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "not_found"

    async def test_unknown_capability_fail_closed(self, full_registry: CapabilityRegistry) -> None:
        envelope = await full_registry.dispatch("does_not_exist", {})
        assert envelope["error"]["code"] == "not_found"


class TestMcpTransport:
    async def test_tools_list_contains_all_capabilities(self) -> None:
        async with mcp_session() as session:
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            assert names >= EXPECTED_CAPABILITIES

    async def test_call_tool_envelope(self, tmp_path: Path, env_dirs: dict[str, str]) -> None:
        fixture = tmp_path / "mini.csv"
        fixture.write_text("a,b\n1,x\n2,y\n3,z\n", encoding="utf-8")
        async with mcp_session() as session:
            envelope = await _mcp_call(session, "tabular_read_file", {"file_path": str(fixture)})
            assert envelope["ok"] is True, envelope
            assert "a,b" in envelope["content"]

    async def test_unknown_tool_is_error_result(self) -> None:
        async with mcp_session() as session:
            result = await session.call_tool("no_such_tool", {})
            assert result.is_error is True
            envelope = json.loads(result.content[0].text)
            assert envelope["error"]["code"] == "not_found"


class TestCliChannel:
    @pytest.fixture()
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_list_command(self, runner: CliRunner, env_dirs: dict[str, str]) -> None:
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        names = {spec["name"] for spec in payload["capabilities"]}
        assert names >= EXPECTED_CAPABILITIES

    def test_call_command_roundtrip(
        self, runner: CliRunner, env_dirs: dict[str, str], tmp_path: Path
    ) -> None:
        fixture = tmp_path / "cli.csv"
        fixture.write_text("a,b\n1,x\n2,y\n", encoding="utf-8")
        result = runner.invoke(
            app,
            ["call", "tabular_read_file", "--input", json.dumps({"file_path": str(fixture)})],
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.stdout.strip().splitlines()[-1])
        assert envelope["ok"] is True
        assert "a,b" in envelope["content"]

    def test_call_command_failure_exits_nonzero(
        self, runner: CliRunner, env_dirs: dict[str, str]
    ) -> None:
        result = runner.invoke(app, ["call", "nope_nope", "--input", "{}"])
        assert result.exit_code == 1
        envelope = json.loads(result.stdout.strip().splitlines()[-1])
        assert envelope["ok"] is False

    def test_compact_and_retrieve_commands(
        self, runner: CliRunner, env_dirs: dict[str, str]
    ) -> None:
        compact = runner.invoke(
            app,
            ["compact", "--pressure", "0.95", "--result-id", "cli-1"],
            input=_big_csv(),
        )
        assert compact.exit_code == 0, compact.output
        envelope = json.loads(compact.stdout.strip().splitlines()[-1])
        assert envelope["ok"] is True
        assert envelope["data"]["was_compacted"] is True

        retrieve = runner.invoke(app, ["retrieve", "cli-1", "--limit", "5"])
        assert retrieve.exit_code == 0, retrieve.output
        page = json.loads(retrieve.stdout.strip().splitlines()[-1])
        assert page["ok"] is True
        assert page["data"]["total_lines"] == 1501


class TestTransportConsistency:
    """DoD B:≥3 个能力,进程内直调 vs MCP vs CLI 输出等价。"""

    def test_consistency_across_transports(
        self,
        full_registry: CapabilityRegistry,
        env_dirs: dict[str, str],
        tmp_path: Path,
    ) -> None:
        import asyncio

        fixture = tmp_path / "consistency.csv"
        fixture.write_text("a,b\n1,x\n2,y\n3,z\n4,x\n5,y\n", encoding="utf-8")
        runner = CliRunner()
        cases: list[tuple[str, dict[str, Any]]] = [
            ("tabular_read_file", {"file_path": str(fixture)}),
            (
                "causal_analyze",
                {
                    "question": "打折是否导致销量上升?",
                    "context": {"columns": ["discount", "sales", "region"], "n_rows": 1200},
                },
            ),
            (
                "sampling_compact_result",
                {"content": _big_csv(300), "context_pressure": 0.95},
            ),
        ]

        async def _run() -> None:
            async with mcp_session() as session:
                for name, input_data in cases:
                    inproc = await full_registry.dispatch(name, dict(input_data))
                    assert inproc["ok"] is True, (name, inproc)
                    via_mcp = await _mcp_call(session, name, dict(input_data))
                    cli_result = runner.invoke(
                        app,
                        ["call", name, "--input", json.dumps(input_data, ensure_ascii=False)],
                    )
                    assert cli_result.exit_code == 0, (name, cli_result.output)
                    via_cli = json.loads(cli_result.stdout.strip().splitlines()[-1])
                    # 规范化对比:ok / content / data 三传输一致
                    assert via_mcp["ok"] == inproc["ok"], name
                    assert via_cli["ok"] == inproc["ok"], name
                    assert via_mcp["content"] == inproc["content"], name
                    assert via_cli["content"] == inproc["content"], name
                    assert via_mcp["data"] == inproc["data"], name
                    assert via_cli["data"] == inproc["data"], name

        asyncio.run(_run())
