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

import re
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .config import SamplingConfig
from .result_store import ResultStore
from .text_summary import compact_result, detect_table

# Rendered-summary meta line ("- rows=12,000 · cols=8 · method=…") and the
# recall handle embedded by DefaultToolResultCompactor.
_SUMMARY_HEAD_RE = re.compile(r"- rows=([\d,]+) · cols=(\d+)")
_RESULT_ID_RE = re.compile(r'retrieve_result\(result_id="([^"]+)"')


def collapse_digest(content: str) -> str | None:
    """One-line digest of an already-compacted tool result (D3).

    Session layers (context collapse in any harness) call this when masking
    old tool results: it keeps a provenance pointer — row/col shape plus the
    recall handle — at near-zero token cost. Returns ``None`` on any miss so
    the caller falls back to its plain placeholder (fail-closed).
    """

    rid = _RESULT_ID_RE.search(content)
    if rid is None:
        return None
    head = _SUMMARY_HEAD_RE.search(content)
    if head is not None:
        return (
            f"[collapsed: table {head.group(1)} rows × {head.group(2)} cols · "
            f'retrieve_result(result_id="{rid.group(1)}")]'
        )
    return f'[collapsed: summarized result · retrieve_result(result_id="{rid.group(1)}")]'


def data_state_block(
    frames: list[dict[str, Any]] | None = None,
    results: list[dict[str, Any]] | None = None,
) -> str:
    """Format the runtime data state as a compact re-injectable block (D4).

    ``frames``: ``[{"name", "rows", "cols"}]`` (kernel DataFrames);
    ``results``: ``[{"id", "tool", "bytes"}]`` (live ResultStore entries).
    The format lives in the capability layer so every harness's compaction
    re-injects the same shape; inputs are plain dicts, so any base can feed
    them from its own introspection. Returns "" when there is nothing to
    report.
    """
    lines: list[str] = []
    if frames:
        lines.append("kernel 数据变量:")
        for frame in frames[:20]:
            name = str(frame.get("name", "?"))
            rows, cols = frame.get("rows"), frame.get("cols")
            if isinstance(rows, int) and isinstance(cols, int):
                lines.append(f"- {name}: {rows:,} 行 × {cols} 列")
            else:
                lines.append(f"- {name}: shape 未知")
    if results:
        lines.append("可回取结果(TTL 内,原文已落盘):")
        for entry in results[:20]:
            rid = str(entry.get("id", "?"))
            tool = str(entry.get("tool", "") or "?")
            size = entry.get("bytes")
            size_text = f", {size // 1024}KB" if isinstance(size, int) else ""
            lines.append(f"- {rid}(tool={tool}{size_text})")
    return "\n".join(lines)


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

    # D8: pressure at/above this downgrades fidelity to low (adaptive_fidelity
    # pins the configured level when False).
    ADAPTIVE_PRESSURE_THRESHOLD = 0.75

    def _effective_config(self, request: CompactRequest) -> SamplingConfig:
        config = request.config or SamplingConfig()
        pressure = min(1.0, max(0.0, request.context_pressure))
        if (
            config.adaptive_fidelity
            and pressure >= self.ADAPTIVE_PRESSURE_THRESHOLD
            and config.fidelity_level != "low"
        ):
            low = SamplingConfig.for_fidelity("low")
            return replace(
                config,
                fidelity_level="low",
                max_sample_rows=low.max_sample_rows,
                top_k=low.top_k,
                quantiles=low.quantiles,
            )
        return config

    def compact(self, request: CompactRequest) -> CompactResult:
        config = self._effective_config(request)
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
