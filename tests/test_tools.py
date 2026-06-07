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

    tool = FileReadTool()
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

    tool = FileReadTool()
    result = await tool.call({"file_path": temp_path, "offset": 1, "limit": 2})

    assert "line1" in result.content
    assert "line2" in result.content
    assert "line0" not in result.content

    Path(temp_path).unlink()


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
