"""DataAnalysisAgent - A ReAct-based data analysis agent.

Public API for programmatic usage.
"""

__version__ = "0.1.0"

from .agent_loop import AgentLoop, AgentLoopConfig
from .config import AgentConfig
from .context.compression import ContextCompressor
from .events import (
    AgentEvent,
    CompleteEvent,
    ErrorEvent,
    RequestStartEvent,
    StateChangeEvent,
    StreamTextEvent,
    ToolResultEvent,
    ToolUseEvent,
)
from .persistence import MessageStore
from .protocol.client import AnthropicApiClient, AnthropicClientError
from .protocol.messages import (
    ContentBlock,
    ModelResponse,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from .skills.builtin import (
    CorrelationAnalysisSkill,
    DescriptiveAnalysisSkill,
    TrendAnalysisSkill,
)
from .skills.registry import SkillRegistry
from .state_machine import (
    AgentSessionState,
    AgentState,
    ContinueReason,
    Message,
    TerminalReason,
    ToolUseContext,
    TurnState,
)
from .tools import (
    CanUseToolFn,
    FileReadTool,
    NlQueryTool,
    PermissionResult,
    PythonAnalysisTool,
    Tool,
    ToolRegistry,
    ToolResult,
    ValidationResult,
    VisualizationTool,
)

__all__ = [
    "__version__",
    # Agent core
    "AgentLoop",
    "AgentLoopConfig",
    "AgentConfig",
    "AgentState",
    "AgentSessionState",
    "TurnState",
    "ContinueReason",
    "TerminalReason",
    "Message",
    "ToolUseContext",
    # Protocol / LLM
    "AnthropicApiClient",
    "AnthropicClientError",
    "ContentBlock",
    "ModelResponse",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    # Events
    "AgentEvent",
    "StreamTextEvent",
    "ToolUseEvent",
    "ToolResultEvent",
    "StateChangeEvent",
    "RequestStartEvent",
    "ErrorEvent",
    "CompleteEvent",
    # Tools
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ValidationResult",
    "PermissionResult",
    "CanUseToolFn",
    "FileReadTool",
    "NlQueryTool",
    "PythonAnalysisTool",
    "VisualizationTool",
    # Skills
    "SkillRegistry",
    "DescriptiveAnalysisSkill",
    "CorrelationAnalysisSkill",
    "TrendAnalysisSkill",
    # Context / Persistence
    "ContextCompressor",
    "MessageStore",
]
