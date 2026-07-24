"""Tests for the recovery-policy seam (RecoveryPolicy).

The escalation ladder used to be reachable only by driving the whole ``run()``
loop with a carefully truncating mock client. As its own object, each rung is
asserted directly: given a state (+ error), the policy returns the next state's
transition (or None to give up). The compression *mechanism* is stubbed — these
tests pin the *policy* (which lever, in what order, when to stop).
"""

from data_analysis_agent.protocol.client import AnthropicClientError
from data_analysis_agent.protocol.messages import TextBlock
from data_analysis_agent.recovery import RecoveryPolicy
from data_analysis_agent.state_machine import AgentState, ContinueReason, Message

# --- compressor / client stubs: only what RecoveryPolicy touches -------------


class _Compacted:
    def __init__(self, messages: list[Message]) -> None:
        self.messages = messages


class _Collapse:
    def __init__(self, staged):
        self.staged_indices = list(staged)


class _AutoCompact:
    def __init__(self, removed):
        self._removed = list(removed)

    def preview_removed(self, messages):
        return self._removed


class _FakeCompressor:
    def __init__(self, *, staged=(), removed=()):
        self.collapse = _Collapse(staged) if staged else None
        self.auto_compact = _AutoCompact(removed)
        self.drained = False
        self.forced = False
        self.forced_summary = "<unset>"

    def drain_collapse(self, messages):
        self.drained = True
        return _Compacted([*messages, Message(role="user", content="[drained]")])

    def force_auto_compact(self, messages, summary=None):
        self.forced = True
        self.forced_summary = summary
        return _Compacted([*messages, Message(role="user", content="[compacted]")])


class _Resp:
    def __init__(self, blocks):
        self.content = blocks


class _FakeClient:
    model = "dummy"

    def __init__(self, *, text=None, fail=False):
        self._text = text
        self._fail = fail
        self.called = False

    async def call_model(self, messages, max_tokens):
        self.called = True
        if self._fail:
            raise RuntimeError("summarizer unavailable")
        return _Resp([TextBlock(text=self._text or "")])


def _policy(compressor, client=None, max_tokens=8192, sleep=None):
    kw = {} if sleep is None else {"sleep": sleep}
    return RecoveryPolicy(compressor, client or _FakeClient(), max_tokens, **kw)


async def _no_sleep(_delay: float) -> None:
    """No-op sleep for fast transient-retry tests (avoids real backoff)."""
    return None


def _state(**kw):
    return AgentState(messages=[Message(role="user", content="analyze sales.csv")], **kw)


# --- attempt_recovery: the 413 ("prompt too long") ladder -------------------


async def test_attempt_recovery_drains_staged_collapse_first():
    # A staged collapse exists → drain it (zero cost) before any compaction.
    compressor = _FakeCompressor(staged=[1, 2])
    # Client would raise if touched — proves summarize is NOT called on this rung.
    policy = _policy(compressor, _FakeClient(fail=True))
    out = await policy.attempt_recovery(_state(), AnthropicClientError("prompt is too long"))
    assert out is not None
    assert out.transition == ContinueReason.COLLAPSE_DRAIN_RETRY
    assert compressor.drained and not compressor.forced


async def test_attempt_recovery_reactive_compacts_when_no_staged_collapse():
    compressor = _FakeCompressor(staged=(), removed=())  # nothing dropped → summary None
    policy = _policy(compressor)
    out = await policy.attempt_recovery(_state(), AnthropicClientError("too long"))
    assert out is not None
    assert out.transition == ContinueReason.REACTIVE_COMPACT_RETRY
    assert out.has_attempted_reactive_compact is True
    assert compressor.forced and compressor.forced_summary is None


async def test_attempt_recovery_gives_up_after_reactive_already_attempted():
    compressor = _FakeCompressor(staged=())
    policy = _policy(compressor)
    out = await policy.attempt_recovery(
        _state(has_attempted_reactive_compact=True),
        AnthropicClientError("prompt is too long"),
    )
    assert out is None
    assert not compressor.forced


async def test_attempt_recovery_retries_transient_errors():
    # A transient API error (429/timeout/overloaded, is_recoverable) is NOT
    # ignored — it gets a bounded loop-level backoff retry (TRANSIENT_RETRY),
    # distinct from the prompt-too-long compact path (no drain/force).
    compressor = _FakeCompressor(staged=[1])
    policy = _policy(compressor, sleep=_no_sleep)
    out = await policy.attempt_recovery(
        _state(), AnthropicClientError("API error: rate limit exceeded")
    )
    assert out is not None
    assert out.transition == ContinueReason.TRANSIENT_RETRY
    assert out.transient_recovery_count == 1
    assert not compressor.drained and not compressor.forced


async def test_attempt_recovery_transient_gives_up_after_limit():
    # After TRANSIENT_RECOVERY_LIMIT retries, a transient error gives up (None).
    policy = _policy(_FakeCompressor(), sleep=_no_sleep)
    out = await policy.attempt_recovery(
        _state(transient_recovery_count=RecoveryPolicy.TRANSIENT_RECOVERY_LIMIT),
        AnthropicClientError("API error: request timed out"),
    )
    assert out is None


async def test_attempt_recovery_transient_backoff_grows():
    # backoff delay grows exponentially (1, 2, 4, ...) — verify the policy asks
    # the injected sleep for increasing delays across retries.
    delays: list[float] = []

    async def _record(delay: float) -> None:
        delays.append(delay)

    policy = _policy(_FakeCompressor(), sleep=_record)
    state = _state()
    for _ in range(RecoveryPolicy.TRANSIENT_RECOVERY_LIMIT):
        out = await policy.attempt_recovery(
            state, AnthropicClientError("API error: 503 overloaded")
        )
        assert out is not None
        state = out
    assert delays == [1, 2, 4]
    # one more → exhausted → None
    assert await policy.attempt_recovery(state, AnthropicClientError("API error: 503")) is None


# --- handle_max_tokens: escalate → bounded retries → give up ----------------


def test_handle_max_tokens_escalates_cap_first():
    policy = _policy(_FakeCompressor(), max_tokens=8192)
    out = policy.handle_max_tokens(_state())
    assert out is not None
    assert out.transition == ContinueReason.MAX_OUTPUT_TOKENS_ESCALATE
    assert out.max_output_tokens_override == RecoveryPolicy.RECOVERY_MAX_TOKENS
    # A meta continuation nudge is appended.
    assert out.messages[-1].is_meta and "continue" in out.messages[-1].content.lower()


def test_handle_max_tokens_recovers_after_escalation():
    policy = _policy(_FakeCompressor(), max_tokens=8192)
    # Already escalated to the recovery cap → next truncation is a bounded retry.
    state = _state(max_output_tokens_override=RecoveryPolicy.RECOVERY_MAX_TOKENS)
    out = policy.handle_max_tokens(state)
    assert out is not None
    assert out.transition == ContinueReason.MAX_OUTPUT_TOKENS_RECOVERY
    assert out.max_output_tokens_recovery_count == 1


def test_handle_max_tokens_gives_up_at_recovery_limit():
    policy = _policy(_FakeCompressor(), max_tokens=8192)
    state = _state(
        max_output_tokens_override=RecoveryPolicy.RECOVERY_MAX_TOKENS,
        max_output_tokens_recovery_count=RecoveryPolicy.MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
    )
    assert policy.handle_max_tokens(state) is None


# --- summarize_for_compact: best-effort, degrades to None -------------------


async def test_summarize_returns_none_when_nothing_dropped():
    client = _FakeClient(text="should not be used")
    policy = _policy(_FakeCompressor(removed=()), client)
    assert await policy._summarize_for_compact(_state().messages) is None
    assert client.called is False  # no model call when there's nothing to summarize


async def test_summarize_degrades_to_none_on_client_failure():
    dropped = [Message(role="user", content="read schema of sales.csv")]
    policy = _policy(_FakeCompressor(removed=dropped), _FakeClient(fail=True))
    assert await policy._summarize_for_compact(dropped) is None  # best-effort


async def test_summarize_returns_model_text():
    dropped = [Message(role="user", content="read schema of sales.csv")]
    policy = _policy(_FakeCompressor(removed=dropped), _FakeClient(text="schema: id,amount"))
    assert await policy._summarize_for_compact(dropped) == "schema: id,amount"
