"""stream_model retries transient errors before any block is yielded.

Uses a fake anthropic module (monkeypatched) so the test is self-contained and
does not depend on a working anthropic install in the environment.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from data_analysis_agent.protocol import client as client_mod
from data_analysis_agent.protocol.client import AnthropicApiClient, AnthropicClientError
from data_analysis_agent.protocol.messages import ModelResponse


class _FakeAPIError(Exception):
    pass


class _FakeRateLimitError(_FakeAPIError):
    pass


class _FakeTimeoutError(_FakeAPIError):
    pass


class _FakeAuthError(_FakeAPIError):
    pass


class _FakeBadRequestError(_FakeAPIError):
    def __init__(self, message: str = "", status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _fake_anthropic_module() -> types.ModuleType:
    mod = types.ModuleType("anthropic")
    mod.APIError = _FakeAPIError  # type: ignore[attr-defined]
    mod.RateLimitError = _FakeRateLimitError  # type: ignore[attr-defined]
    mod.APITimeoutError = _FakeTimeoutError  # type: ignore[attr-defined]
    mod.APIConnectionError = _FakeTimeoutError  # type: ignore[attr-defined]
    mod.AuthenticationError = _FakeAuthError  # type: ignore[attr-defined]
    mod.BadRequestError = _FakeBadRequestError  # type: ignore[attr-defined]
    return mod


class _StopEvent:
    type = "message_stop"


class _FakeStream:
    def __init__(self) -> None:
        self._events = [_StopEvent()]

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e

        return gen()


class _FakeMessages:
    def __init__(self, fail_times: int, error: Exception | None = None) -> None:
        self.fail_times = fail_times
        self.error = error or _FakeRateLimitError("rate limited")
        self.calls = 0

    def stream(self, **_: Any) -> _FakeStream:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return _FakeStream()


class _FakeAnthropicClient:
    def __init__(self, fail_times: int, error: Exception | None = None) -> None:
        self.messages = _FakeMessages(fail_times, error)


def _client(fail_times: int, error: Exception | None = None) -> AnthropicApiClient:
    c = AnthropicApiClient.__new__(AnthropicApiClient)  # bypass __init__ (no real key)
    c.model = "test-model"
    c._client = _FakeAnthropicClient(fail_times, error)  # type: ignore[attr-defined]
    return c


async def test_stream_model_retries_transient_before_first_block(monkeypatch):
    """A RateLimitError at stream establishment (before any yield) is retried;
    the second attempt succeeds and yields a ModelResponse."""
    monkeypatch.setattr(client_mod, "_import_anthropic", _fake_anthropic_module)
    client = _client(fail_times=1)

    events = [e async for e in client.stream_model(messages=[{"role": "user", "content": "hi"}])]

    assert client._client.messages.calls == 2  # type: ignore[attr-defined]
    assert any(isinstance(e, ModelResponse) for e in events)


async def test_stream_model_gives_up_after_max_attempts(monkeypatch):
    """Persistent transient errors exhaust retries (3 attempts) and surface as
    recoverable instead of looping forever."""
    monkeypatch.setattr(client_mod, "_import_anthropic", _fake_anthropic_module)
    # Don't really sleep the backoff — the test must not depend on wall-clock.
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    client = _client(fail_times=99)

    with pytest.raises(AnthropicClientError) as exc:
        async for _ in client.stream_model(messages=[{"role": "user", "content": "hi"}]):
            pass

    assert exc.value.is_recoverable is True
    assert client._client.messages.calls == 3  # type: ignore[attr-defined]


async def _no_sleep(*_: Any, **__: Any) -> None:
    return None


async def test_stream_model_non_retryable_not_retried(monkeypatch):
    """A non-retryable APIError (e.g. a generic APIError subclass not in the
    retryable set) is surfaced immediately without retry."""
    monkeypatch.setattr(client_mod, "_import_anthropic", _fake_anthropic_module)
    # A plain _FakeAPIError (no Auth/BadRequest/RateLimit/Timeout specialization)
    # hits the generic APIError branch and is NOT in retryable_types -> no retry.
    client = _client(fail_times=1, error=_FakeAPIError("server error"))

    with pytest.raises(AnthropicClientError) as exc:
        async for _ in client.stream_model(messages=[{"role": "user", "content": "hi"}]):
            pass

    assert client._client.messages.calls == 1  # type: ignore[attr-defined]
    assert exc.value.is_recoverable is True
    assert "API error" in str(exc.value)


class _Delta:
    type = "text_delta"

    def __init__(self, text: str) -> None:
        self.text = text


class _DeltaEvent:
    type = "content_block_delta"

    def __init__(self, text: str) -> None:
        self.delta = _Delta(text)


class _StreamYieldsThenRaises:
    """A stream that yields one text delta (so yielded_any becomes True) then
    raises mid-iteration — exercising the 'no retry after output started' guard."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __aenter__(self) -> _StreamYieldsThenRaises:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    def __aiter__(self):
        async def gen():
            yield _DeltaEvent("partial")
            raise self.error

        return gen()


class _MessagesYieldThenFail:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def stream(self, **_: Any):
        self.calls += 1
        return _StreamYieldsThenRaises(self.error)


async def test_stream_model_no_retry_after_output_started(monkeypatch):
    """Once a block has been yielded, a mid-stream failure is NOT retried
    (retrying would duplicate output); it surfaces as recoverable."""
    monkeypatch.setattr(client_mod, "_import_anthropic", _fake_anthropic_module)
    c = AnthropicApiClient.__new__(AnthropicApiClient)
    c.model = "test-model"
    c._client = type("X", (), {"messages": _MessagesYieldThenFail(_FakeRateLimitError("late"))})()  # type: ignore[attr-defined]

    yielded: list[Any] = []
    with pytest.raises(AnthropicClientError) as exc:
        async for block in c.stream_model(messages=[{"role": "user", "content": "hi"}]):
            yielded.append(block)

    # Exactly one attempt (no retry despite a retryable error — output had started).
    assert c._client.messages.calls == 1  # type: ignore[attr-defined]
    assert exc.value.is_recoverable is True
    assert len(yielded) == 1  # the one partial TextBlock, not duplicated
