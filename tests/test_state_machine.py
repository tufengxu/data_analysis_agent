"""Tests for the state machine module."""

from data_analysis_agent.state_machine import AgentState, ContinueReason, Message


def test_agent_state_immutable_update():
    """Test that state updates create new instances."""
    state = AgentState(turn_count=1)
    new_state = state.with_turn_count(2)

    assert state.turn_count == 1
    assert new_state.turn_count == 2
    assert state is not new_state


def test_agent_state_with_messages():
    """Test message list updates."""
    msg = Message(role="user", content="hello")
    state = AgentState()
    new_state = state.with_messages([msg])

    assert len(new_state.messages) == 1
    assert new_state.messages[0].role == "user"
    assert len(state.messages) == 0


def test_message_to_anthropic_format():
    """Test message serialization."""
    msg = Message(role="user", content="test")
    d = msg.to_anthropic_format()
    assert d == {"role": "user", "content": "test"}


def test_continue_reason_enum():
    """Test ContinueReason values exist."""
    assert ContinueReason.NEXT_TURN is not None
    assert ContinueReason.MAX_OUTPUT_TOKENS_ESCALATE is not None
    assert ContinueReason.REACTIVE_COMPACT_RETRY is not None
