"""Tests for the artifact delivery seam (store + agent_loop passthrough)."""

import base64
from typing import Any

import pytest

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.artifacts import ArtifactStore
from data_analysis_agent.events import ToolResultEvent
from data_analysis_agent.protocol.messages import ModelResponse, TextBlock, ToolUseBlock
from data_analysis_agent.tools.base import Tool, ToolResult
from data_analysis_agent.tools.registry import ToolRegistry
from data_analysis_agent.tools.visualization import VisualizationTool

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfakedata"
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()


class _SequenceClient:
    model = "dummy"

    def __init__(self, responses):
        self.responses = list(responses)

    async def stream_model(
        self, messages, system=None, tools=None, max_tokens=None, tool_choice=None
    ):
        response = self.responses.pop(0)
        for block in response.content:
            yield block
        yield response


class _ChartTool(Tool):
    """Returns an image via metadata, like python_exec's sandbox image flow."""

    @property
    def name(self) -> str:
        return "chart_tool"

    @property
    def description(self) -> str:
        return "renders a chart"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, input_data: dict[str, Any], can_use_tool=None) -> ToolResult:
        return ToolResult(
            content="chart rendered",
            metadata={"images": [{"format": "png", "data": _PNG_B64}]},
        )


def test_artifact_store_roundtrip(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    path = store.save_image("tu_1_0", "png", _PNG_B64)
    assert path is not None
    assert path.read_bytes() == _PNG_BYTES

    assert store.save_image("tu_bad", "png", "!!!not-base64!!!") is None
    assert store.save_image("tu_empty", "png", "") is None


def test_artifact_store_sanitizes_names(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    path = store.save_image("../../evil name", "p/n!g", _PNG_B64)
    assert path is not None
    assert path.parent == store.dir  # no traversal outside the store


async def test_agent_loop_persists_metadata_images(tmp_path):
    registry = ToolRegistry()
    registry.register(_ChartTool())
    client = _SequenceClient(
        [
            ModelResponse(
                content=[ToolUseBlock(id="tu_img", name="chart_tool", input={})],
                stop_reason="tool_use",
            ),
            ModelResponse(content=[TextBlock("done")], stop_reason="end_turn"),
        ]
    )
    agent = AgentLoop(
        AgentLoopConfig(api_key="test"),
        registry,
        client=client,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )

    events = [event async for event in agent.run("draw a chart")]
    result = next(e for e in events if isinstance(e, ToolResultEvent))

    assert len(result.artifacts) == 1
    saved = result.artifacts[0]
    assert saved.endswith(".png")
    assert "[产物已保存" in result.content
    with open(saved, "rb") as fh:
        assert fh.read() == _PNG_BYTES


async def test_agent_loop_without_artifact_store_keeps_old_behavior():
    registry = ToolRegistry()
    registry.register(_ChartTool())
    client = _SequenceClient(
        [
            ModelResponse(
                content=[ToolUseBlock(id="tu_img", name="chart_tool", input={})],
                stop_reason="tool_use",
            ),
            ModelResponse(content=[TextBlock("done")], stop_reason="end_turn"),
        ]
    )
    agent = AgentLoop(AgentLoopConfig(api_key="test"), registry, client=client)

    events = [event async for event in agent.run("draw a chart")]
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.artifacts == ()


async def test_visualization_default_path_uses_artifact_dir(tmp_path):
    tool = VisualizationTool(artifact_dir=tmp_path / "artifacts")
    result = await tool.call(
        {"chart_type": "line", "data_source": "d.csv", "x_column": "x", "y_column": "y"}
    )
    code = result.metadata["generated_code"]
    assert str(tmp_path / "artifacts") in code
    assert "plt.savefig(" in code


async def test_visualization_explicit_path_wins(tmp_path):
    tool = VisualizationTool(artifact_dir=tmp_path / "artifacts")
    result = await tool.call({"chart_type": "line", "output_path": "/data/out/c.png"})
    assert "/data/out/c.png" in result.metadata["generated_code"]


async def test_visualization_to_python_exec_artifact_chain(tmp_path):
    """M1 regression: generated chart code must reach metadata["images"]."""
    pytest.importorskip("matplotlib")
    pytest.importorskip("pandas")
    from data_analysis_agent.tools.python_exec import PythonAnalysisTool

    csv = tmp_path / "d.csv"
    csv.write_text("x,y\n1,2\n3,4\n")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    viz = VisualizationTool(artifact_dir=artifact_dir)
    generated = await viz.call(
        {"chart_type": "line", "data_source": str(csv), "x_column": "x", "y_column": "y"}
    )
    code = generated.metadata["generated_code"]
    assert "agent_result(" in code  # emits the image output marker

    runner = PythonAnalysisTool(allowed_paths=[tmp_path])
    result = await runner.call({"code": code, "timeout": 60})

    assert result.is_error is False
    images = result.metadata.get("images", [])
    assert images and images[0]["format"] == "png"


async def test_kernel_path_image_chain(tmp_path):
    """R2-m4 regression: the image chain must also work in kernel mode."""
    pytest.importorskip("matplotlib")
    pytest.importorskip("pandas")
    from data_analysis_agent.kernel import KernelManager
    from data_analysis_agent.tools.python_exec import PythonAnalysisTool

    csv = tmp_path / "d.csv"
    csv.write_text("x,y\n1,2\n3,4\n")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    viz = VisualizationTool(artifact_dir=artifact_dir)
    generated = await viz.call(
        {"chart_type": "line", "data_source": str(csv), "x_column": "x", "y_column": "y"}
    )
    manager = KernelManager(work_dir=tmp_path / "k")
    runner = PythonAnalysisTool(allowed_paths=[tmp_path], kernel=manager)
    try:
        result = await runner.call({"code": generated.metadata["generated_code"], "timeout": 60})
    finally:
        await manager.shutdown()

    assert result.is_error is False
    images = result.metadata.get("images", [])
    assert images and images[0]["format"] == "png"
    # The chart already lives in the artifact dir; its path is carried along
    # so the artifact seam can reuse it without writing a duplicate.
    assert str(artifact_dir) in images[0]["path"]
