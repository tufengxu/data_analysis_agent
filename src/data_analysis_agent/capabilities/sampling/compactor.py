"""``ToolResultCompactor`` — harness-agnostic tool-result compaction seam (v2).

Contract (spec 5.2/5.3): input = raw tool-result content + length budget +
context-pressure signal (0..1, supplied by the adapter) + optional fidelity
config; output = compacted content, whether compaction happened, a recall
handle when the original was persisted, and sampling-method / fidelity
annotations.

The reference implementation preserves ``compact_result`` semantics exactly
(trigger threshold, pressure-adaptive gain gating, forced compaction past the
hard cap, degrade-never-worse-than-v1) and adds ResultStore recall handles.
The v1 ``agent_loop`` seam routes through this same implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import SamplingConfig
from .result_store import ResultStore
from .text_summary import compact_result, detect_table


@dataclass(frozen=True)
class CompactRequest:
    """One compaction ask from any harness's tool-result outlet."""

    content: str
    max_chars: int = 50_000
    context_pressure: float = 0.0
    config: SamplingConfig | None = None
    result_id: str | None = None
    tool_name: str = ""


@dataclass(frozen=True)
class CompactResult:
    content: str
    was_compacted: bool = False
    result_id: str | None = None
    sampling_method: str = "passthrough"
    fidelity_level: str = "mid"
    notes: tuple[str, ...] = ()


def recall_hint(result_id: str) -> str:
    """Recall marker appended to compacted results (byte-identical to the v1
    agent_loop seam so model-facing behavior does not shift)."""

    return f'[完整结果已缓存。回取: retrieve_result(result_id="{result_id}", offset=0, limit=50)]'


class ToolResultCompactor(Protocol):
    """The seam contract every base harness's tool-result outlet plugs into."""

    def compact(self, request: CompactRequest) -> CompactResult: ...


def _classify_method(original: str, compacted: str) -> str:
    if "[truncated" in compacted and "head+tail kept" in compacted:
        return "head-tail-truncate"
    try:
        return "table-summary" if detect_table(original) is not None else "text-digest"
    except Exception:
        return "text-digest"


class DefaultToolResultCompactor:
    """Reference implementation: ``compact_result`` + optional ResultStore recall.

    Fail-closed: any unexpected internal failure degrades to returning the
    original content un-compacted (never worse than doing nothing).
    """

    def __init__(self, store: ResultStore | None = None) -> None:
        self._store = store

    def compact(self, request: CompactRequest) -> CompactResult:
        config = request.config or SamplingConfig()
        try:
            out, was_compacted = compact_result(
                request.content,
                request.max_chars,
                config,
                request.context_pressure,
            )
        except Exception:
            # compact_result is already internally guarded; this is belt-and-
            # braces for the seam boundary itself.
            return CompactResult(
                content=request.content,
                was_compacted=False,
                sampling_method="degraded-passthrough",
                fidelity_level=config.fidelity_level,
                notes=("compactor-internal-error:原样返回",),
            )
        if not was_compacted:
            return CompactResult(
                content=out,
                was_compacted=False,
                sampling_method="passthrough",
                fidelity_level=config.fidelity_level,
            )

        result_id: str | None = None
        content = out
        if self._store is not None and request.result_id is not None:
            stored = self._store.put(
                request.result_id, request.content, {"tool": request.tool_name}
            )
            if stored:
                result_id = request.result_id
                content = out + "\n\n" + recall_hint(result_id)

        return CompactResult(
            content=content,
            was_compacted=True,
            result_id=result_id,
            sampling_method=_classify_method(request.content, out),
            fidelity_level=config.fidelity_level,
        )
