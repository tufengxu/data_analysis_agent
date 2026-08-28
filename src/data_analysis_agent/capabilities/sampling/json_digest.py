"""JSON / JSONL structural digest (D7).

Detects JSON payloads (object array, single object, JSONL) and produces a
schema skeleton — key paths with types and counts, array-length stats —
plus a reservoir of representative elements. Any parse miss returns ``None``
so the caller falls back to the line-level text digest; the degradation
chain is unchanged. Pure stdlib; the render lives in :mod:`render`.
"""

from __future__ import annotations

import json
import random
import statistics
from typing import Any

from .config import SamplingConfig

_MAX_DEPTH = 3
_MAX_PATHS = 40
_MAX_ARRAYS = 10
_MAX_ELEMENT_CHARS = 400


def parse_json_payload(text: str) -> list[dict[str, Any]] | None:
    """Return the item list when ``text`` is JSON / JSONL, else ``None``.

    A single top-level object wraps into a one-item list (large API
    responses are a real digest case); arrays must hold objects only —
    anything else is left to the table/text paths.
    """
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        parsed: Any = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = _parse_jsonl(stripped)
    return _as_item_list(parsed)


def _parse_jsonl(text: str) -> Any:
    items: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            return None  # not JSONL either — let the caller degrade
    return items


def _as_item_list(parsed: Any) -> list[dict[str, Any]] | None:
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
        return parsed
    return None


def build_json_digest(items: list[dict[str, Any]], config: SamplingConfig) -> dict[str, Any]:
    """Skeleton + representative elements for the parsed item list."""
    paths: dict[str, dict[str, Any]] = {}
    arrays: dict[str, list[int]] = {}
    for item in items:
        _walk(item, "", 0, paths, arrays)

    top_paths = sorted(paths.items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:_MAX_PATHS]
    rng = random.Random(config.seed)
    k = min(5, len(items))
    sampled = rng.sample(items, k) if k < len(items) else list(items)
    return {
        "n_items": len(items),
        "paths": [{"path": path, **info} for path, info in top_paths],
        "arrays": [
            {
                "path": path,
                "min": min(lengths),
                "max": max(lengths),
                "median": statistics.median(lengths),
            }
            for path, lengths in sorted(arrays.items())[:_MAX_ARRAYS]
            if lengths
        ],
        "sampled": [_clip_element(el) for el in sampled],
    }


def _walk(
    value: Any,
    prefix: str,
    depth: int,
    paths: dict[str, dict[str, Any]],
    arrays: dict[str, list[int]],
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            info = paths.setdefault(path, {"type": _type_name(child), "count": 0})
            info["count"] += 1
            if depth < _MAX_DEPTH:
                _walk(child, path, depth + 1, paths, arrays)
    elif isinstance(value, list):
        arrays.setdefault(prefix or "(root)", []).append(len(value))
        # one representative element keeps the skeleton bounded
        if value and depth < _MAX_DEPTH:
            _walk(value[0], (prefix or "") + "[]", depth + 1, paths, arrays)


def _type_name(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "null"


def _clip_element(element: dict[str, Any]) -> str:
    text = json.dumps(element, ensure_ascii=False, default=str)
    if len(text) <= _MAX_ELEMENT_CHARS:
        return text
    return text[:_MAX_ELEMENT_CHARS] + "…"
