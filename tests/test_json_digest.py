"""Tests for the JSON / JSONL structural digest (D7)."""

from __future__ import annotations

import json

from data_analysis_agent.sampling import SamplingConfig, compact_result, summarize_text
from data_analysis_agent.sampling import json_digest as jd

SMALL_TRIGGER = SamplingConfig(trigger_chars=200, seed=0)


def _jsonl(n: int = 100) -> str:
    lines = []
    for i in range(n):
        lines.append(
            json.dumps(
                {
                    "user": {"id": i, "name": f"user{i}"},
                    "tags": [f"t{i % 3}", f"t{i % 5}"],
                    "amount": i * 1.5,
                }
            )
        )
    return "\n".join(lines)


def test_parse_json_payload_variants():
    items = jd.parse_json_payload(_jsonl(10))
    assert items is not None and len(items) == 10
    array = jd.parse_json_payload(json.dumps([{"a": 1}, {"a": 2}]))
    assert array is not None and len(array) == 2
    single = jd.parse_json_payload(json.dumps({"k": [1, 2, 3], "x": "y"}))
    assert single is not None and len(single) == 1
    assert jd.parse_json_payload("plain prose\nmore prose") is None
    assert jd.parse_json_payload("[1, 2, 3]") is None  # scalar array → not ours


def test_build_json_digest_skeleton_and_sampling():
    items = jd.parse_json_payload(_jsonl(100))
    assert items is not None
    digest = jd.build_json_digest(items, SamplingConfig(seed=0))
    assert digest["n_items"] == 100
    paths = {p["path"]: p for p in digest["paths"]}
    assert paths["user.id"]["type"] == "int"
    assert paths["user.id"]["count"] == 100
    assert paths["amount"]["type"] == "float"
    arrays = {a["path"]: a for a in digest["arrays"]}
    assert arrays["tags"]["min"] == 2 and arrays["tags"]["max"] == 2
    assert len(digest["sampled"]) == 5


def test_summarize_text_jsonl_renders_skeleton():
    out = summarize_text(_jsonl(100), SMALL_TRIGGER)
    assert "JSON 结构摘要" in out
    assert "| user.id | int | 100 |" in out
    assert "代表元素" in out
    assert len(out) < len(_jsonl(100))
    # mandatory sampling caveat
    assert "完整内容已省略" in out


def test_compact_result_routes_json_payload():
    content = _jsonl(150)
    out, was = compact_result(content, 50_000, SMALL_TRIGGER)
    assert was is True
    assert "JSON 结构摘要" in out
    assert len(out) < len(content)


def test_invalid_json_falls_back_to_text_digest():
    content = "\n".join(f"log line {i} not json" for i in range(300))
    out = summarize_text(content, SMALL_TRIGGER)
    assert "文本结果摘要" in out
