"""Tests for the tool system."""

import tempfile
from pathlib import Path

from data_analysis_agent.tools.file_read import FileReadTool
from data_analysis_agent.tools.nl_query import NlQueryTool
from data_analysis_agent.tools.python_exec import PythonAnalysisTool
from data_analysis_agent.tools.registry import ToolRegistry


def test_tool_registry_register():
    """Test tool registration."""
    registry = ToolRegistry()
    registry.register(FileReadTool())

    assert len(registry.list_tools()) == 1
    assert "read_file" in registry.list_tools()


def test_tool_registry_get_tool():
    """Test tool lookup."""
    registry = ToolRegistry()
    tool = FileReadTool()
    registry.register(tool)

    found = registry.get_tool("read_file")
    assert found is tool
    assert registry.get_tool("nonexistent") is None


def test_tool_registry_deny_pattern():
    """Test deny pattern filtering."""
    registry = ToolRegistry()
    registry.register(FileReadTool())
    registry.register(PythonAnalysisTool())
    registry.add_deny_pattern("python_*")

    tools = registry.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "read_file"


def test_file_read_tool_schema():
    """Test FileReadTool schema."""
    tool = FileReadTool()
    assert tool.name == "read_file"
    assert tool.is_read_only({}) is True
    assert tool.is_concurrency_safe({}) is True


def test_file_read_tool_validate():
    """Test FileReadTool validation."""
    tool = FileReadTool()
    assert tool.validate_input({"file_path": "/tmp/test.txt"}).valid is True
    assert tool.validate_input({}).valid is False
    assert tool.validate_input({"file_path": ""}).valid is False


async def test_file_read_tool_execute():
    """Test FileReadTool execution."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line1\nline2\nline3\n")
        temp_path = f.name

    tool = FileReadTool(allowed_paths=[Path(temp_path).parent])
    result = await tool.call({"file_path": temp_path})

    assert "line1" in result.content
    assert "line2" in result.content
    assert result.is_error is False

    Path(temp_path).unlink()


async def test_file_read_tool_offset_limit():
    """Test FileReadTool pagination."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line0\nline1\nline2\nline3\n")
        temp_path = f.name

    tool = FileReadTool(allowed_paths=[Path(temp_path).parent])
    result = await tool.call({"file_path": temp_path, "offset": 1, "limit": 2})

    assert "line1" in result.content
    assert "line2" in result.content
    assert "line0" not in result.content

    Path(temp_path).unlink()


async def test_file_read_tool_limit_zero_returns_nothing():
    """limit<=0 reads zero lines (matches old lines[start:start+0] semantics;
    the streaming loop's append-then-break used to return one extra line)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line0\nline1\nline2\n")
        temp_path = f.name

    tool = FileReadTool(allowed_paths=[Path(temp_path).parent])
    for bad_limit in (0, -3):
        result = await tool.call({"file_path": temp_path, "limit": bad_limit})
        assert result.is_error is False
        assert "line0" not in result.content
        assert "line1" not in result.content

    Path(temp_path).unlink()


async def test_file_read_tool_rejects_path_outside_allowed(tmp_path):
    """read_file is path-scoped like data_profile: a path outside allowed_paths
    is rejected before any read (closes the roadmap-admitted gap)."""
    inside = tmp_path / "ok.txt"
    inside.write_text("hello\n", encoding="utf-8")
    outside = tmp_path.parent / "sibling_secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    try:
        tool = FileReadTool(allowed_paths=[inside.parent])
        ok = await tool.call({"file_path": str(inside)})
        assert ok.is_error is False
        assert "hello" in ok.content

        denied = await tool.call({"file_path": str(outside)})
        assert denied.is_error is True
        assert "outside allowed analysis paths" in denied.content
    finally:
        outside.unlink(missing_ok=True)


async def test_file_read_tool_resolves_symlink_before_whitelist(tmp_path):
    """A symlink inside an allowed dir but pointing outside is rejected by its
    resolved location (resolve() follows the link before the check)."""
    target = tmp_path / "outside_target.txt"
    target.write_text("private\n", encoding="utf-8")
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    link = allowed_dir / "link.txt"
    link.symlink_to(target)
    try:
        tool = FileReadTool(allowed_paths=[allowed_dir])
        result = await tool.call({"file_path": str(link)})
        assert result.is_error is True
        assert "outside allowed analysis paths" in result.content
    finally:
        link.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


def test_python_analysis_tool_validation():
    """Test PythonAnalysisTool validation."""
    tool = PythonAnalysisTool()
    assert tool.validate_input({"code": "print(1)"}).valid is True
    assert tool.validate_input({}).valid is False
    # Check that blocked patterns are rejected
    assert tool.validate_input({"code": "__import__('os')"}).valid is False


def test_python_analysis_tool_rejects_unsafe_file_access():
    """PythonAnalysisTool should fail closed on direct unsafe filesystem access."""
    tool = PythonAnalysisTool()

    assert tool.validate_input({"code": "open('/tmp/secret.txt').read()"}).valid is False
    assert (
        tool.validate_input(
            {"code": "from pathlib import Path\nPath('/etc/passwd').read_text()"}
        ).valid
        is False
    )


def test_python_analysis_tool_rejects_invalid_timeout():
    """PythonAnalysisTool should constrain requested execution time."""
    tool = PythonAnalysisTool()

    assert tool.validate_input({"code": "print(1)", "timeout": 0}).valid is False
    assert tool.validate_input({"code": "print(1)", "timeout": 999}).valid is False


async def test_python_analysis_tool_execute():
    """Test PythonAnalysisTool execution."""
    tool = PythonAnalysisTool()
    result = await tool.call({"code": "print('hello world')"})

    assert "hello world" in result.content
    assert result.is_error is False


async def test_nl_query_tool():
    """Test NlQueryTool."""
    tool = NlQueryTool()
    result = await tool.call(
        {
            "query": "Show top 10 products",
            "data_source": "/data/sales.csv",
            "source_type": "csv",
        }
    )

    assert "Generated query code" in result.content
    assert result.is_error is False
