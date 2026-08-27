"""tabular 能力域测试:委托 v1 tools + 持久内核(无 LLM、无网络)。"""

from __future__ import annotations

from pathlib import Path

from data_analysis_agent.capabilities import CapabilityRegistry, Permission
from data_analysis_agent.capabilities.tabular import KernelHolder, register_all

TABULAR_NAMES = [
    "tabular_read_file",
    "tabular_data_profile",
    "tabular_data_quality",
    "tabular_join_plan",
    "tabular_metric_contract",
    "tabular_nl_query",
    "tabular_python_exec",
]

_CSV = "region,sales,qty\nnorth,100,1\nsouth,250,2\neast,50,3\n"


def _make_registry(
    tmp_path: Path,
    kernel: KernelHolder | None = None,
) -> CapabilityRegistry:
    """注册到 scoped registry(白名单 = tmp_path),并断言 7 个名字全部就位。"""
    registry = CapabilityRegistry()
    names = register_all(registry, allowed_roots=[tmp_path], kernel=kernel)
    assert names == TABULAR_NAMES
    return registry


def _write_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(_CSV, encoding="utf-8")
    return csv_path


def test_register_all_returns_all_seven_names() -> None:
    registry = CapabilityRegistry()
    names = register_all(registry)

    assert names == TABULAR_NAMES
    for name in names:
        assert registry.has(name)
        spec = registry.get(name)
        assert spec.domain == "tabular"
        expected = (
            Permission.EXECUTES_CODE if name == "tabular_python_exec" else Permission.READ_ONLY
        )
        assert spec.permission is expected


async def test_read_file_success(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path)
    csv_path = _write_csv(tmp_path)

    env = await registry.execute("tabular_read_file", {"file_path": str(csv_path)})

    assert env["ok"] is True
    assert env["capability"] == "tabular_read_file"
    assert str(csv_path) in env["content"]
    assert "region,sales,qty" in env["content"]
    assert "north,100,1" in env["content"]


async def test_read_file_validation_error_on_bad_input(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path)

    for bad_input in ({}, {"file_path": ""}, {"file_path": 123}):
        env = await registry.execute("tabular_read_file", bad_input)
        assert env["ok"] is False
        assert env["error"]["code"] == "validation_error"
        assert env["error"]["message"]


async def test_read_file_nonexistent_file_is_execution_error(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path)

    env = await registry.execute("tabular_read_file", {"file_path": str(tmp_path / "nope.csv")})

    assert env["ok"] is False
    assert env["error"]["code"] == "execution_error"
    assert "not found" in env["error"]["message"]


async def test_data_profile_mentions_columns(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path)
    csv_path = _write_csv(tmp_path)

    env = await registry.execute("tabular_data_profile", {"path": str(csv_path)})

    assert env["ok"] is True
    for column in ("region", "sales", "qty"):
        assert column in env["content"]
    tables = env["metadata"]["profile"]["tables"]
    assert [c["name"] for c in tables[0]["columns"]] == ["region", "sales", "qty"]


async def test_python_exec_validation_error_on_missing_code(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path)

    env = await registry.execute("tabular_python_exec", {})

    # 先校验后调用(镜像 v1 tool-gate):缺 code 不触达内核。
    assert env["ok"] is False
    assert env["error"]["code"] == "validation_error"


async def test_python_exec_state_persists_across_calls(tmp_path: Path) -> None:
    holder = KernelHolder(allowed_paths=[tmp_path])
    registry = _make_registry(tmp_path, kernel=holder)
    try:
        first = await registry.execute("tabular_python_exec", {"code": "x = 41"})
        assert first["ok"] is True

        # 同一 holder 恒返回同一工具实例(绝不按调用重建)。
        assert holder.tool is holder.tool

        second = await registry.execute("tabular_python_exec", {"code": "print(x + 1)"})
        assert second["ok"] is True
        assert "42" in second["content"]
    finally:
        await holder.shutdown()
