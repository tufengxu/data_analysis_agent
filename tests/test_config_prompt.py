"""The default system prompt must advertise the data-discovery workflow.

These are behavioural contracts, not wording tests: the agent only does
multi-sheet / multi-file work reliably if the prompt tells it to discover
structure first (data_profile), use absolute paths, and that Excel is supported.
"""

from data_analysis_agent.config import AgentConfig


def test_system_prompt_mentions_data_profile_and_excel():
    prompt = AgentConfig().system_prompt
    assert "data_profile" in prompt
    assert "Excel" in prompt or "xlsx" in prompt


def test_system_prompt_mentions_absolute_paths_and_joining():
    prompt = AgentConfig().system_prompt.lower()
    assert "absolute" in prompt
    # join/merge guidance for multi-file work
    assert "merge" in prompt or "join" in prompt
