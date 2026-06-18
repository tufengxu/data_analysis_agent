"""Tests for the persistent analysis kernel (manager + tool integration)."""

import pytest

from data_analysis_agent.kernel import (
    KernelCrashError,
    KernelManager,
    KernelStartError,
    KernelTimeoutError,
)
from data_analysis_agent.tools.python_exec import PythonAnalysisTool


@pytest.fixture
async def kernel(tmp_path):
    manager = KernelManager(work_dir=tmp_path / "k")
    yield manager
    await manager.shutdown()


async def test_state_persists_across_executes(kernel):
    first = await kernel.execute("x = 41", timeout=10)
    assert first.error is None

    second = await kernel.execute("print(x + 1)", timeout=10)
    assert second.error is None
    assert "42" in second.stdout


async def test_user_error_keeps_kernel_alive(kernel):
    failed = await kernel.execute("1 / 0", timeout=10)
    assert failed.error is not None
    assert "ZeroDivisionError" in failed.error

    ok = await kernel.execute("print('still alive')", timeout=10)
    assert ok.error is None
    assert "still alive" in ok.stdout


async def test_sys_exit_does_not_kill_kernel(kernel):
    failed = await kernel.execute("import sys\nsys.exit(3)", timeout=10)
    assert failed.error is not None

    ok = await kernel.execute("print('ok')", timeout=10)
    assert "ok" in ok.stdout


async def test_crash_raises_and_restart_recovers(kernel):
    with pytest.raises(KernelCrashError):
        await kernel.execute("import os\nos._exit(7)", timeout=10)

    await kernel.restart()
    ok = await kernel.execute("print('reborn')", timeout=10)
    assert "reborn" in ok.stdout


async def test_timeout_kills_kernel(kernel):
    with pytest.raises(KernelTimeoutError):
        await kernel.execute("while True:\n    pass", timeout=1)
    assert kernel.alive is False


async def test_tool_kernel_mode_state_persists(tmp_path):
    manager = KernelManager(work_dir=tmp_path / "k")
    tool = PythonAnalysisTool(kernel=manager)
    try:
        first = await tool.call({"code": "shared_value = 7\nprint('set')"})
        assert first.is_error is False

        second = await tool.call({"code": "print(shared_value * 6)"})
        assert second.is_error is False
        assert "42" in second.content
    finally:
        await manager.shutdown()


async def test_tool_timeout_restarts_and_reports_state_loss(tmp_path):
    manager = KernelManager(work_dir=tmp_path / "k")
    tool = PythonAnalysisTool(kernel=manager)
    try:
        result = await tool.call({"code": "while True:\n    pass", "timeout": 1})
        assert result.is_error is True
        assert "session variables lost" in result.content

        # Restarted kernel serves new requests.
        ok = await tool.call({"code": "print('recovered')"})
        assert "recovered" in ok.content
    finally:
        await manager.shutdown()


async def test_tool_falls_back_stateless_when_kernel_cannot_start(tmp_path, monkeypatch):
    manager = KernelManager(work_dir=tmp_path / "k")

    async def broken_execute(code, timeout):
        raise KernelStartError("boom")

    monkeypatch.setattr(manager, "execute", broken_execute)

    tool = PythonAnalysisTool(kernel=manager)
    result = await tool.call({"code": "print('fallback works')"})

    assert result.is_error is False
    assert "fallback works" in result.content
    assert tool._kernel_disabled is True


async def test_kernel_auto_summary_for_large_result(tmp_path):
    pd = pytest.importorskip("pandas")
    assert pd is not None
    manager = KernelManager(work_dir=tmp_path / "k")
    tool = PythonAnalysisTool(kernel=manager)
    try:
        code = "import pandas as pd\nresult = pd.DataFrame({'a': range(5000), 'b': range(5000)})\n"
        result = await tool.call({"code": code})
        assert result.is_error is False
        outputs = result.metadata.get("structured", {}).get("outputs", [])
        assert any(item.get("type") == "table_summary" for item in outputs)
    finally:
        await manager.shutdown()


def test_serialize_response_sheds_outputs_over_limit():
    from data_analysis_agent.kernel import kernel_main

    huge = {
        "id": "x",
        "ok": True,
        "stdout": "",
        "stderr": "",
        "error": None,
        "outputs": [{"type": "table_summary", "summary": "z" * 9_000_000}],
    }
    serialized = kernel_main._serialize_response(huge)
    assert len(serialized.encode("utf-8")) <= kernel_main._MAX_RESPONSE_BYTES
    assert "outputs dropped" in serialized


def test_serialize_response_clips_huge_error():
    from data_analysis_agent.kernel import kernel_main

    huge = {
        "id": "x",
        "ok": False,
        "stdout": "",
        "stderr": "",
        "error": "e" * 20_000_000,
        "outputs": [],
    }
    serialized = kernel_main._serialize_response(huge)
    assert len(serialized.encode("utf-8")) <= kernel_main._MAX_RESPONSE_BYTES


async def test_huge_user_exception_does_not_crash_kernel(kernel):
    """R2-M1 regression: a giant exception message must not blow the pipe and
    masquerade as a kernel crash (which would wipe session state)."""
    failed = await kernel.execute("raise ValueError('x' * 5_000_000)", timeout=20)
    assert failed.error is not None
    assert "ValueError" in failed.error

    ok = await kernel.execute("print('alive')", timeout=10)
    assert "alive" in ok.stdout


async def test_tool_kernel_crash_reports_state_loss(tmp_path):
    """C-1 regression: a kernel crash (not timeout) must restart and tell the
    model variables were lost — at the PythonAnalysisTool level."""
    manager = KernelManager(work_dir=tmp_path / "k")
    tool = PythonAnalysisTool(kernel=manager)
    try:
        await tool.call({"code": "x = 1"})  # establish state
        result = await tool.call({"code": "import os\nos._exit(7)"})  # hard crash
        assert result.is_error is True
        assert "session variables lost" in result.content
        # Kernel recovers for subsequent calls.
        ok = await tool.call({"code": "print('alive again')"})
        assert "alive again" in ok.content
    finally:
        await manager.shutdown()


async def test_tool_double_failure_downgrades_to_stateless(tmp_path, monkeypatch):
    """C-2 regression: crash + restart-also-fails → permanent stateless, no loop."""
    manager = KernelManager(work_dir=tmp_path / "k")
    tool = PythonAnalysisTool(kernel=manager)
    try:
        from data_analysis_agent.kernel.manager import KernelCrashError, KernelError

        async def boom_execute(code, timeout):
            raise KernelCrashError("dead")

        async def boom_restart():
            raise KernelError("cannot restart")

        monkeypatch.setattr(manager, "execute", boom_execute)
        monkeypatch.setattr(manager, "restart", boom_restart)

        result = await tool.call({"code": "print('x')"})
        assert result.is_error is True
        assert tool._kernel_disabled is True
        # Now permanently stateless: next call runs via one-shot subprocess.
        ok = await tool.call({"code": "print('stateless ok')"})
        assert "stateless ok" in ok.content
    finally:
        await manager.shutdown()
