"""v2 自进化能力域测试:契约 / 写入校验 / TurnRecord 转换 / 能力注册(无网络、无 LLM)。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from data_analysis_agent.capabilities import CapabilityRegistry
from data_analysis_agent.capabilities.evolution import (
    EVENT_TYPES,
    HARNESSES,
    TRAJECTORY_SCHEMA,
    TrajectoryEvent,
    TrajectoryWriter,
    default_root,
    digest,
    events_to_turn_records,
    load_v2_turns,
    register_all,
    verify_file,
)
from data_analysis_agent.telemetry.trajectory import TurnRecord

_TS = "2026-08-26T10:00:00Z"


def _event(
    event: str,
    data: dict[str, object],
    *,
    session_id: str = "s1",
    turn: int = 0,
    harness: str = "pi",
    ts: str = _TS,
) -> TrajectoryEvent:
    return TrajectoryEvent(
        event=event, ts=ts, session_id=session_id, turn=turn, harness=harness, data=data
    )


def _complete_turn_events(session_id: str = "s1", turn: int = 0) -> list[TrajectoryEvent]:
    return [
        _event(
            "turn_start",
            {"user_input_digest": digest("分析用户留存趋势")},
            session_id=session_id,
            turn=turn,
        ),
        _event(
            "model_input",
            {"summary": {"n_messages": 6, "n_tools": 9}},
            session_id=session_id,
            turn=turn,
        ),
        _event(
            "context_injection",
            {"source": "skill", "name": "retention_analysis", "chars": 512},
            session_id=session_id,
            turn=turn,
        ),
        _event(
            "tool_call",
            {"tool": "python_exec", "input_digest": digest("df.groupby('cohort').size()")},
            session_id=session_id,
            turn=turn,
            ts="2026-08-26T10:00:02Z",
        ),
        _event(
            "tool_result",
            {
                "tool": "python_exec",
                "ok": True,
                "output_digest": digest("retention table"),
                "chars": 1200,
            },
            session_id=session_id,
            turn=turn,
            ts="2026-08-26T10:00:05Z",
        ),
        _event(
            "turn_end",
            {"outcome": "complete", "final_output_digest": digest("结论文本")},
            session_id=session_id,
            turn=turn,
            ts="2026-08-26T10:00:06Z",
        ),
    ]


class TestDigest:
    def test_pattern_is_hex_and_length(self) -> None:
        value = digest("hello world")
        assert re.fullmatch(r"[0-9a-f]{12}:\d+", value)
        assert value.endswith(f":{len('hello world')}")

    def test_distinct_inputs_get_distinct_digests(self) -> None:
        assert digest("a") != digest("b")
        assert digest("").startswith("e3b0c44298fc:")


class TestContractConstants:
    def test_schema_and_vocabularies(self) -> None:
        assert TRAJECTORY_SCHEMA == "daa.trajectory.v1"
        assert set(EVENT_TYPES) == {
            "turn_start",
            "model_input",
            "tool_call",
            "tool_result",
            "context_injection",
            "turn_end",
        }
        assert HARNESSES == ("v1", "pi", "dsh")


class TestTrajectoryEvent:
    def test_roundtrip_dict_and_json(self) -> None:
        event = _event("tool_call", {"tool": "read_table", "input_digest": digest('{"p": 1}')})
        assert TrajectoryEvent.from_dict(event.to_dict()) == event
        # JSON 序列化无损(适配层以 JSON 交付契约)。
        assert TrajectoryEvent.from_dict(json.loads(json.dumps(event.to_dict()))) == event

    def test_validate_ok_returns_no_problems(self) -> None:
        assert _event("turn_start", {"user_input_digest": digest("q")}).validate() == []

    def test_rejects_unknown_event_type(self) -> None:
        problems = _event("mystery", {}).validate()
        assert any("unknown event type" in p for p in problems)

    def test_rejects_unknown_harness(self) -> None:
        problems = _event(
            "turn_start", {"user_input_digest": digest("q")}, harness="claude"
        ).validate()
        assert any("unknown harness" in p for p in problems)

    def test_rejects_negative_turn(self) -> None:
        problems = _event("turn_start", {"user_input_digest": digest("q")}, turn=-1).validate()
        assert any("non-negative integer" in p for p in problems)

    def test_rejects_empty_session_id_and_bad_ts(self) -> None:
        problems = _event(
            "turn_start", {"user_input_digest": digest("q")}, session_id=" "
        ).validate()
        assert any("session_id" in p for p in problems)
        problems = _event(
            "turn_start", {"user_input_digest": digest("q")}, ts="2026-08-26 10:00:00"
        ).validate()
        assert any("ISO-8601 UTC" in p for p in problems)

    def test_rejects_missing_required_data_keys(self) -> None:
        assert any("user_input_digest" in p for p in _event("turn_start", {}).validate())
        assert any("summary" in p for p in _event("model_input", {}).validate())
        assert any(
            "output_digest" in p
            for p in _event("tool_result", {"tool": "t", "ok": True}).validate()
        )

    def test_rejects_incomplete_model_summary(self) -> None:
        problems = _event("model_input", {"summary": {"n_messages": 3}}).validate()
        assert any("n_tools" in p for p in problems)

    def test_rejects_bad_outcome(self) -> None:
        problems = _event("turn_end", {"outcome": "crashed"}).validate()
        assert any("outcome" in p for p in problems)

    def test_rejects_raw_content_in_digest_field(self) -> None:
        problems = _event("turn_start", {"user_input_digest": "SELECT * FROM users"}).validate()
        assert any("raw-looking value" in p for p in problems)
        with pytest.raises(ValueError, match="invalid TrajectoryEvent"):
            TrajectoryEvent.from_dict(
                {
                    "event": "turn_start",
                    "ts": _TS,
                    "session_id": "s",
                    "turn": 0,
                    "harness": "v1",
                    "data": {"user_input_digest": "not a digest"},
                }
            )

    def test_rejects_oversized_string_as_raw_content(self) -> None:
        problems = _event("turn_end", {"outcome": "complete", "blob": "x" * 1024}).validate()
        assert any("looks like raw content" in p for p in problems)

    def test_from_dict_reports_missing_fields(self) -> None:
        with pytest.raises(ValueError, match="missing required field 'event'"):
            TrajectoryEvent.from_dict(
                {"ts": _TS, "session_id": "s", "turn": 0, "harness": "v1", "data": {}}
            )


class TestTrajectoryWriter:
    def test_writes_one_json_event_per_line(self, tmp_path: Path) -> None:
        writer = TrajectoryWriter(root=tmp_path)
        path = writer.write_event(_event("turn_start", {"user_input_digest": digest("q")}))
        writer.write_event(_event("turn_end", {"outcome": "complete"}))
        assert path == tmp_path / "s1.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert [json.loads(line)["event"] for line in lines] == ["turn_start", "turn_end"]

    def test_write_event_rejects_invalid_event(self, tmp_path: Path) -> None:
        writer = TrajectoryWriter(root=tmp_path)
        with pytest.raises(ValueError, match="unknown event type"):
            writer.write_event(_event("nope", {}))
        assert not (tmp_path / "s1.jsonl").exists()

    def test_write_event_rejects_unsafe_session_id(self, tmp_path: Path) -> None:
        writer = TrajectoryWriter(root=tmp_path)
        with pytest.raises(ValueError, match="safe file name"):
            writer.write_event(
                _event("turn_start", {"user_input_digest": digest("q")}, session_id="../evil")
            )

    def test_default_root_honors_daa_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DAA_HOME", str(tmp_path))
        assert default_root() == tmp_path / "trajectories" / "v2"
        monkeypatch.delenv("DAA_HOME")
        assert default_root() == Path.home() / ".daa" / "trajectories" / "v2"


class TestVerifyFile:
    def test_valid_file_is_ok(self, tmp_path: Path) -> None:
        writer = TrajectoryWriter(root=tmp_path)
        writer.write_event(_event("turn_start", {"user_input_digest": digest("q")}))
        writer.write_event(_event("turn_end", {"outcome": "error"}))
        report = verify_file(tmp_path / "s1.jsonl")
        assert report == {"ok": True, "events": 2, "problems": []}

    def test_corrupted_json_line_is_a_problem_not_a_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "s1.jsonl"
        writer = TrajectoryWriter(root=tmp_path)
        writer.write_event(_event("turn_start", {"user_input_digest": digest("q")}))
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        report = verify_file(path)
        assert report["ok"] is False
        assert report["events"] == 1
        assert len(report["problems"]) == 1
        assert "line 2" in report["problems"][0]

    def test_invalid_event_line_is_a_problem(self, tmp_path: Path) -> None:
        path = tmp_path / "s1.jsonl"
        path.write_text(json.dumps({"event": "tool_call"}) + "\n", encoding="utf-8")
        report = verify_file(path)
        assert report["ok"] is False
        assert any("tool_call" in p for p in report["problems"])

    def test_missing_file_reports_unreadable(self, tmp_path: Path) -> None:
        report = verify_file(tmp_path / "nope.jsonl")
        assert report["ok"] is False
        assert report["events"] == 0
        assert any("unreadable" in p for p in report["problems"])


class TestEventsToTurnRecords:
    def test_complete_turn_maps_to_v1_record(self) -> None:
        record = events_to_turn_records("s1", _complete_turn_events())
        assert isinstance(record, TurnRecord)
        assert record.session_id == "s1"
        assert record.ts_start == _TS
        assert record.ts_end == "2026-08-26T10:00:06Z"
        assert record.user_input.startswith("(digest-only: ")
        assert record.final_text_digest.startswith("(digest-only: ")
        assert record.terminal_reason == "COMPLETED"  # synthesizer is_eligible 依赖
        assert record.model_turns == 1
        assert record.active_skill == "retention_analysis"
        assert record.tokens == {"input": 0, "output": 0, "estimated": True}
        (call,) = record.tool_calls
        assert call.name == "python_exec"
        assert call.is_error is False
        assert call.result_chars == 1200
        assert call.input_digest == digest("df.groupby('cohort').size()")
        assert call.duration_ms == 3000  # tool_call 10:00:02 → tool_result 10:00:05

    def test_incomplete_turn_returns_none(self) -> None:
        events = [e for e in _complete_turn_events() if e.event != "turn_end"]
        assert events_to_turn_records("s1", events) is None
        assert events_to_turn_records("s1", []) is None

    def test_filters_other_sessions(self) -> None:
        events = _complete_turn_events()
        events.append(_event("turn_start", {"user_input_digest": digest("x")}, session_id="other"))
        record = events_to_turn_records("other", events)
        assert record is None

    def test_error_and_interrupted_outcomes_map_to_v1_vocab(self) -> None:
        for outcome, terminal in (("error", "MODEL_ERROR"), ("interrupted", "INTERRUPTED")):
            events = _complete_turn_events()
            events[-1] = _event("turn_end", {"outcome": outcome}, ts="2026-08-26T10:00:06Z")
            assert events_to_turn_records("s1", events).terminal_reason == terminal


class TestLoadV2Turns:
    def test_groups_by_turn_in_order(self, tmp_path: Path) -> None:
        writer = TrajectoryWriter(root=tmp_path)
        for event in _complete_turn_events(turn=0) + _complete_turn_events(turn=1):
            writer.write_event(event)
        records = load_v2_turns(tmp_path / "s1.jsonl")
        assert len(records) == 2
        assert records[0].turn_id != records[1].turn_id  # sha256(session|turn) 确定性去重
        assert all(r.terminal_reason == "COMPLETED" for r in records)

    def test_skips_bad_lines_and_incomplete_turns(self, tmp_path: Path) -> None:
        path = tmp_path / "s1.jsonl"
        writer = TrajectoryWriter(root=tmp_path)
        writer.write_event(_event("turn_start", {"user_input_digest": digest("q")}))
        writer.write_event(_event("turn_end", {"outcome": "complete"}))
        with path.open("a", encoding="utf-8") as handle:
            handle.write("garbage line\n")
            handle.write(json.dumps({"event": "turn_start"}) + "\n")  # 合法 JSON,非法事件
        assert len(load_v2_turns(path)) == 1

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_v2_turns(tmp_path / "nope.jsonl") == []


class TestEvolutionCapabilities:
    def _registry(self, tmp_path: Path) -> CapabilityRegistry:
        registry = CapabilityRegistry()
        names = register_all(registry, root=tmp_path)
        assert set(names) == {"evolution_record_event", "evolution_verify_trajectory"}
        for name in names:
            spec = registry.get(name)
            assert spec.domain == "evolution"
            assert spec.permission.value == "read_only"
        return registry

    async def test_record_event_success(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        envelope = await registry.execute(
            "evolution_record_event",
            {
                "event": "turn_start",
                "ts": _TS,
                "session_id": "s9",
                "turn": 0,
                "harness": "dsh",
                "data": {"user_input_digest": digest("问题")},
            },
        )
        assert envelope["ok"] is True
        assert envelope["data"]["written"] is True
        assert envelope["data"]["session_id"] == "s9"
        assert (tmp_path / "s9.jsonl").is_file()

    async def test_record_event_validation_error_lists_problems(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        envelope = await registry.execute(
            "evolution_record_event",
            {
                "event": "turn_start",
                "ts": _TS,
                "session_id": "s9",
                "turn": 0,
                "harness": "pi",
                "data": {"user_input_digest": "raw text, not a digest"},
            },
        )
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "validation_error"
        assert "raw-looking value" in envelope["error"]["message"]

    async def test_verify_trajectory_success(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        path = TrajectoryWriter(root=tmp_path).write_event(
            _event("turn_start", {"user_input_digest": digest("q")}, session_id="sv")
        )
        envelope = await registry.execute("evolution_verify_trajectory", {"path": str(path)})
        assert envelope["ok"] is True
        assert envelope["data"] == {"ok": True, "events": 1, "problems": []}

    async def test_verify_trajectory_missing_file_fails_closed(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        envelope = await registry.execute(
            "evolution_verify_trajectory", {"path": str(tmp_path / "missing.jsonl")}
        )
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "execution_error"


class TestEvolutionLayerPurity:
    def test_public_surface_never_reaches_v1_harness(self) -> None:
        """镜像 drift 规则:能力域公开符号的实现模块不来自任何 v1 harness 内部。"""

        import data_analysis_agent.capabilities.evolution as evolution_pkg

        forbidden_prefixes = (
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.session",
            "data_analysis_agent.state_machine",
            "data_analysis_agent.protocol",
            "data_analysis_agent.events",
            "data_analysis_agent.runtime",
            "data_analysis_agent.config",
            "data_analysis_agent.persistence",
            "data_analysis_agent.tools",
            "data_analysis_agent.kernel",
            "data_analysis_agent.sampling",
        )
        for name in dir(evolution_pkg):
            if name.startswith("_"):
                continue
            module = getattr(getattr(evolution_pkg, name), "__module__", "")
            for prefix in forbidden_prefixes:
                assert not module.startswith(prefix), f"evolution.{name} 来自 {module}"
