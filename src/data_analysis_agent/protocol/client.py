"""Anthropic API client with streaming support, retry logic, and error recovery."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any, cast

from .messages import ContentBlock, ModelResponse, TextBlock, ToolUseBlock


class AnthropicClientError(Exception):
    """Base exception for API client errors."""

    def __init__(self, message: str, is_recoverable: bool = False):
        super().__init__(message)
        self.is_recoverable = is_recoverable


def _import_anthropic() -> Any:
    """Lazy import anthropic to allow module loading without the dependency."""
    try:
        import anthropic

        return anthropic
    except ImportError as e:
        raise ImportError(
            "The 'anthropic' package is required. Install it with: pip install anthropic"
        ) from e


def _import_tenacity() -> Any:
    """Lazy import tenacity."""
    try:
        from tenacity import (
            retry,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )

        return retry, retry_if_exception_type, stop_after_attempt, wait_exponential
    except ImportError as e:
        raise ImportError(
            "The 'tenacity' package is required. Install it with: pip install tenacity"
        ) from e


class AnthropicApiClient:
    """Stateless wrapper around Anthropic Messages API.

    Each call carries the full conversation history (the API is stateless).
    Streaming is the default mode; non-streaming is a fallback.
    """

    DEFAULT_MODEL = "claude-sonnet-4-6-20260401"
    DEFAULT_MAX_TOKENS = 8192
    ESCALATED_MAX_TOKENS = 64000

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or self.DEFAULT_MODEL
        anthropic = _import_anthropic()
        if not self.api_key:
            raise AnthropicClientError(
                "ANTHROPIC_API_KEY not set. Provide api_key or set env var.",
            )
        self._client = anthropic.AsyncAnthropic(api_key=self.api_key)

    async def call_model(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> ModelResponse:
        """Non-streaming model call (fallback mode)."""
        retry, retry_if_exception_type, stop_after_attempt, wait_exponential = _import_tenacity()

        request_max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS

        @retry(  # type: ignore[untyped-decorator]
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type(
                (
                    _import_anthropic().RateLimitError,
                    _import_anthropic().APITimeoutError,
                )
            ),
        )
        async def _call() -> ModelResponse:
            params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": request_max_tokens,
                "messages": messages,
            }
            if system:
                params["system"] = system
            if tools:
                params["tools"] = tools
            if tool_choice:
                params["tool_choice"] = tool_choice

            anthropic = _import_anthropic()
            try:
                raw = await self._client.messages.create(**params)
            except anthropic.AuthenticationError as e:
                raise AnthropicClientError(f"Authentication failed: {e}") from e
            except anthropic.BadRequestError as e:
                if "prompt is too long" in str(e).lower() or e.status_code == 413:
                    raise AnthropicClientError(
                        "Prompt too long",
                        is_recoverable=True,
                    ) from e
                raise AnthropicClientError(f"Bad request: {e}") from e
            except anthropic.APIError as e:
                raise AnthropicClientError(
                    f"API error: {e}",
                    is_recoverable=True,
                ) from e

            return ModelResponse(
                content=[ContentBlock.from_api_dict(b) for b in raw.content],
                stop_reason=raw.stop_reason,
                model=raw.model,
                usage={
                    "input_tokens": raw.usage.input_tokens,
                    "output_tokens": raw.usage.output_tokens,
                },
            )

        return cast(ModelResponse, await _call())

    async def stream_model(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> AsyncIterator[ModelResponse | ContentBlock]:
        """Streaming model call yielding partial ContentBlocks.

        Yields incremental ContentBlocks (TextBlock, ToolUseBlock) during
        streaming, and a final ModelResponse with stop_reason.

        Retries transient errors (rate limit / timeout / connection) ONLY before
        any block has been yielded — once output has started, retrying would
        duplicate it, so a mid-stream failure propagates as recoverable for the
        agent loop's recovery policy. Mirrors call_model's retry intent.
        """
        max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": True,
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice

        anthropic = _import_anthropic()
        retryable_types = tuple(
            t
            for t in (
                getattr(anthropic, "RateLimitError", None),
                getattr(anthropic, "APITimeoutError", None),
                getattr(anthropic, "APIConnectionError", None),
            )
            if t is not None
        )

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            current_text = ""
            current_tool_use: dict[str, Any] | None = None
            current_tool_input_json = ""
            content_blocks: list[ContentBlock] = []
            stop_reason: str | None = None
            model_id = self.model
            usage: dict[str, int] = {}
            yielded_any = False

            try:
                async with self._client.messages.stream(**params) as stream:
                    async for event in stream:
                        event_type = event.type

                        if event_type == "content_block_start":
                            block = event.content_block
                            if block.type == "text":
                                current_text = ""
                            elif block.type == "tool_use":
                                current_tool_use = {
                                    "id": block.id,
                                    "name": block.name,
                                }
                                current_tool_input_json = ""

                        elif event_type == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta":
                                current_text += delta.text
                                yielded_any = True
                                yield TextBlock(text=delta.text)
                            elif delta.type == "input_json_delta":
                                current_tool_input_json += delta.partial_json

                        elif event_type == "content_block_stop":
                            if current_tool_use is not None:
                                import json

                                try:
                                    tool_input = json.loads(current_tool_input_json)
                                except json.JSONDecodeError:
                                    tool_input = {}
                                tool_block = ToolUseBlock(
                                    id=current_tool_use["id"],
                                    name=current_tool_use["name"],
                                    input=tool_input,
                                )
                                content_blocks.append(tool_block)
                                yielded_any = True
                                yield tool_block
                                current_tool_use = None
                                current_tool_input_json = ""
                            elif current_text:
                                content_blocks.append(TextBlock(text=current_text))
                                current_text = ""

                        elif event_type == "message_start":
                            # Streaming usage: input tokens arrive at message_start,
                            # output tokens accumulate on message_delta. The int guard
                            # avoids writing a non-int (a future SDK shape change) that
                            # would masquerade as real usage and bypass the estimate
                            # fallback; a missing attr degrades to estimate, not crash.
                            with contextlib.suppress(AttributeError):
                                value = event.message.usage.input_tokens
                                if isinstance(value, int):
                                    usage["input_tokens"] = value

                        elif event_type == "message_delta":
                            if event.delta.stop_reason:
                                stop_reason = event.delta.stop_reason
                            with contextlib.suppress(AttributeError):
                                value = event.usage.output_tokens
                                if isinstance(value, int):
                                    usage["output_tokens"] = value

                        elif event_type == "message_stop":
                            pass

            except Exception as e:
                if isinstance(e, anthropic.AuthenticationError):
                    raise AnthropicClientError(f"Authentication failed: {e}") from e
                if isinstance(e, anthropic.BadRequestError):
                    if (
                        "prompt is too long" in str(e).lower()
                        or getattr(e, "status_code", 0) == 413
                    ):
                        raise AnthropicClientError(
                            "Prompt too long",
                            is_recoverable=True,
                        ) from e
                    raise AnthropicClientError(f"Bad request: {e}") from e
                if isinstance(e, anthropic.APIError):
                    # Retry transient errors only if nothing has been yielded yet
                    # (mid-stream retry would duplicate output). After the last
                    # attempt, or once output started, surface as recoverable so
                    # the agent loop's recovery policy can react.
                    if (
                        not yielded_any
                        and retryable_types
                        and isinstance(e, retryable_types)
                        and attempt < max_attempts
                    ):
                        await asyncio.sleep(min(2**attempt, 10))
                        continue
                    raise AnthropicClientError(
                        f"API error: {e}",
                        is_recoverable=True,
                    ) from e
                raise

            yield ModelResponse(
                content=content_blocks,
                stop_reason=stop_reason,
                model=model_id,
                usage=usage,
            )
            return
