"""Phase 1b: domain-memory write-back loop.

Three write paths that were previously dead:
- explicit /define and /pref slash commands (zero-LLM capture)
- offline memory_miner (trajectories -> memory)
and the corrected light-confirm (rephrase-gated) read semantics.
"""

from dataclasses import replace

from data_analysis_agent.__main__ import apply_memory_command, parse_memory_command
from data_analysis_agent.config import AgentConfig
from data_analysis_agent.memory import MemoryEntry, MemoryInjector, MemoryStore, ProfileStore
from data_analysis_agent.runtime import AgentRuntime


def _injector(tmp_path) -> MemoryInjector:
    return MemoryInjector(ProfileStore(tmp_path / "p"), MemoryStore(tmp_path / "p"))


def make_memory_entry(kind: str, key: str, content: str, *, confirmed: bool) -> MemoryEntry:
    return MemoryEntry(kind=kind, key=key, content=content, confirmed=confirmed)


class _FakeClient:
    model = "dummy"


# --- 1B-3: explicit capture -------------------------------------------------


def test_remember_pref_writes_confirmed_analysis_pref(tmp_path):
    inj = _injector(tmp_path)
    inj.remember_pref("图表统一用中文标签", session_id="s1")
    entry = inj.memory.get("analysis_pref", "图表统一用中文标签")
    assert entry is not None
    assert entry.kind == "analysis_pref"
    assert entry.content == "图表统一用中文标签"
    assert entry.confirmed is True  # a user-stated preference is trusted


def test_remember_metric_default_is_unconfirmed(tmp_path):
    """The miner path: an inferred metric starts unconfirmed (light-confirm)."""
    inj = _injector(tmp_path)
    inj.remember_metric("活跃用户", "近30天有登录的用户", session_id="s1")
    entry = inj.memory.get("metric_definition", "活跃用户")
    assert entry is not None
    assert entry.confirmed is False


def test_remember_metric_confirmed_flag(tmp_path):
    inj = _injector(tmp_path)
    inj.remember_metric("GMV", "不含退款的成交额", confirmed=True)
    assert inj.memory.get("metric_definition", "GMV").confirmed is True


def test_parse_memory_command_define():
    assert parse_memory_command("/define 活跃用户=近30天有登录") == (
        "metric_definition",
        "活跃用户",
        "近30天有登录",
    )


def test_parse_memory_command_pref():
    kind, key, content = parse_memory_command("/pref 图表统一用中文标签")
    assert kind == "analysis_pref"
    assert content == "图表统一用中文标签"
    assert key  # a non-empty derived key


def test_parse_memory_command_non_command_returns_none():
    assert parse_memory_command("分析 sales.csv 的区域分布") is None


def test_parse_memory_command_malformed_define_returns_none():
    assert parse_memory_command("/define 活跃用户") is None  # missing '='
    assert parse_memory_command("/define ") is None


def test_apply_define_marks_metric_confirmed(tmp_path):
    inj = _injector(tmp_path)
    msg = apply_memory_command(inj, parse_memory_command("/define 活跃用户=近30天有登录"))
    assert "活跃用户" in msg
    entry = inj.memory.get("metric_definition", "活跃用户")
    assert entry is not None and entry.confirmed is True  # explicit user definition


def test_apply_pref_writes_pref(tmp_path):
    inj = _injector(tmp_path)
    apply_memory_command(inj, parse_memory_command("/pref 千分位展示金额"))
    prefs = [e for e in inj.memory.all() if e.kind == "analysis_pref"]
    assert any(e.content == "千分位展示金额" for e in prefs)


# --- 1B-2: rephrase-gated light-confirm -------------------------------------


def test_store_touch_does_not_confirm_only_recency(tmp_path):
    from data_analysis_agent.memory.model import CONFIRM_AFTER_USES

    store = MemoryStore(tmp_path / "m")
    store.put(make_memory_entry("metric_definition", "GMV", "不含退款", confirmed=False))
    for _ in range(CONFIRM_AFTER_USES + 3):
        store.touch("metric_definition", "GMV")
    # surfacing must NOT confirm (the old bug)
    assert store.get("metric_definition", "GMV").confirmed is False


def test_store_note_accepted_use_confirms_metric(tmp_path):
    from data_analysis_agent.memory.model import CONFIRM_AFTER_USES

    store = MemoryStore(tmp_path / "m")
    store.put(make_memory_entry("metric_definition", "GMV", "不含退款", confirmed=False))
    for _ in range(CONFIRM_AFTER_USES):
        store.note_accepted_use("metric_definition", "GMV")
    assert store.get("metric_definition", "GMV").confirmed is True


def test_render_alone_does_not_confirm(tmp_path):
    inj = _injector(tmp_path)
    inj.remember_metric("活跃用户", "过去7天有登录")
    inj.render("活跃用户的趋势")
    inj.render("活跃用户分布")
    # rendering (surfacing) twice must NOT confirm without adjudication
    assert inj.memory.get("metric_definition", "活跃用户").confirmed is False


def test_accepted_uses_confirm_after_threshold(tmp_path):
    from data_analysis_agent.memory.model import CONFIRM_AFTER_USES

    inj = _injector(tmp_path)
    inj.remember_metric("活跃用户", "过去7天有登录")
    for _ in range(CONFIRM_AFTER_USES):
        inj.render("活跃用户的趋势")
        inj.adjudicate(accepted=True)
    assert inj.memory.get("metric_definition", "活跃用户").confirmed is True
    assert "基于历史推断" not in inj.render("活跃用户月度")


def test_rephrase_blocks_confirmation(tmp_path):
    inj = _injector(tmp_path)
    inj.remember_metric("活跃用户", "过去7天有登录")
    for _ in range(5):
        inj.render("活跃用户的趋势")
        inj.adjudicate(accepted=False)  # user kept rephrasing → never accepted
    assert inj.memory.get("metric_definition", "活跃用户").confirmed is False


async def test_session_adjudicates_previous_turn(monkeypatch, tmp_path):
    """The session must call the adjudicator with accepted = not rephrase."""
    import data_analysis_agent.session as session_mod
    from data_analysis_agent.session import AgentSession

    calls: list[bool] = []

    class _FakeLoop:
        last_final_messages: list = []

        async def run(self, user_input, history=None):
            if False:  # pragma: no cover - make this an async generator
                yield None

    sess = AgentSession(_FakeLoop(), memory_adjudicator=lambda accepted: calls.append(accepted))

    async def _drain(text):
        async for _ in sess.send(text):
            pass

    # turn 1: no previous turn → no adjudication
    await _drain("分析活跃用户")
    assert calls == []

    # turn 2 is a rephrase → adjudicate(False)
    monkeypatch.setattr(session_mod, "looks_like_rephrase", lambda *_a, **_k: True)
    await _drain("不对，重新算")
    assert calls == [False]

    # turn 3 is normal → adjudicate(True)
    monkeypatch.setattr(session_mod, "looks_like_rephrase", lambda *_a, **_k: False)
    await _drain("再看下月度")
    assert calls == [False, True]


# --- 1B-1: offline memory miner ---------------------------------------------


def _write_turn(dir_path, turn_id, **fields):
    import json

    dir_path.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "turn",
        "session_id": "s1",
        "turn_id": turn_id,
        "ts_start": "",
        "ts_end": "",
        "user_input": "",
        "active_skill": None,
        "tool_calls": [],
        "terminal_reason": "COMPLETED",
        "model_turns": 3,
        "tokens": {},
        "final_text_digest": "",
    }
    rec.update(fields)
    path = dir_path / f"{turn_id}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def _append_feedback(path, turn_id, kind):
    import json

    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"type": "feedback", "turn_id": turn_id, "kind": kind}, ensure_ascii=False)
            + "\n"
        )


def _miner(traj, store, extract):
    from data_analysis_agent.evolution.memory_miner import MemoryMiner

    return MemoryMiner(traj, store, extract)


def test_miner_writes_extracted_memories_metric_unconfirmed(tmp_path):
    traj = tmp_path / "traj"
    _write_turn(traj, "t1", user_input="活跃用户怎么算")
    store = MemoryStore(tmp_path / "m")

    def extract(_turn):
        return [{"kind": "metric_definition", "key": "活跃用户", "content": "近30天有登录的用户"}]

    written = _miner(traj, store, extract).mine()

    assert len(written) == 1
    entry = store.get("metric_definition", "活跃用户")
    assert entry is not None
    assert entry.confirmed is False  # mined metric is light-confirm pending
    assert entry.source_session == "s1"


def test_miner_skips_non_completed_turns(tmp_path):
    traj = tmp_path / "traj"
    _write_turn(traj, "t1", terminal_reason="ERROR")
    store = MemoryStore(tmp_path / "m")
    called: list = []

    def extract(turn):
        called.append(turn)
        return [{"kind": "analysis_pref", "key": "k", "content": "c"}]

    _miner(traj, store, extract).mine()
    assert called == []
    assert store.all() == []


def test_miner_skips_turns_with_bad_feedback(tmp_path):
    traj = tmp_path / "traj"
    path = _write_turn(traj, "t1", user_input="x")
    _append_feedback(path, "t1", "bad")
    store = MemoryStore(tmp_path / "m")

    def extract(_turn):
        return [{"kind": "analysis_pref", "key": "k", "content": "c"}]

    assert _miner(traj, store, extract).mine() == []
    assert store.all() == []


def test_miner_drops_invalid_candidates(tmp_path):
    traj = tmp_path / "traj"
    _write_turn(traj, "t1", user_input="x")
    store = MemoryStore(tmp_path / "m")

    def extract(_turn):
        return [
            {"kind": "bogus", "key": "k", "content": "c"},  # bad kind
            {"kind": "analysis_pref", "key": "k2"},  # missing content
            {"kind": "analysis_pref", "key": " ", "content": "c"},  # empty key
            {"kind": "analysis_pref", "key": "good", "content": "千分位"},  # valid
        ]

    written = _miner(traj, store, extract).mine()
    assert [e.key for e in written] == ["good"]


def test_miner_dedups_via_upsert(tmp_path):
    traj = tmp_path / "traj"
    _write_turn(traj, "t1", user_input="x")
    _write_turn(traj, "t2", user_input="y")
    store = MemoryStore(tmp_path / "m")

    def extract(_turn):
        return [{"kind": "metric_definition", "key": "GMV", "content": "不含退款"}]

    _miner(traj, store, extract).mine()
    metrics = [e for e in store.all() if e.kind == "metric_definition"]
    assert len(metrics) == 1


def test_miner_isolates_extract_exceptions(tmp_path):
    traj = tmp_path / "traj"
    _write_turn(traj, "t1", user_input="x")
    store = MemoryStore(tmp_path / "m")

    def extract(_turn):
        raise RuntimeError("LLM blew up")

    assert _miner(traj, store, extract).mine() == []  # no crash, nothing written


def test_extract_json_array_tolerates_prose_fences_and_object():
    from data_analysis_agent.evolution.__main__ import _extract_json_array

    plain = '[{"kind": "analysis_pref", "key": "k", "content": "c"}]'
    assert _extract_json_array(plain) == [{"kind": "analysis_pref", "key": "k", "content": "c"}]

    fenced = (
        '这是结果:```json\n[{"kind":"metric_definition","key":"GMV","content":"不含退款"}]\n```'
    )
    assert _extract_json_array(fenced)[0]["key"] == "GMV"

    assert _extract_json_array("no json at all") == []

    # a single object (not wrapped in an array) is tolerated → one-element list
    obj = '{"kind": "analysis_pref", "key": "k", "content": "c"}'
    assert _extract_json_array(obj) == [{"kind": "analysis_pref", "key": "k", "content": "c"}]


def test_runtime_exposes_memory_injector(tmp_path, monkeypatch):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    on = replace(AgentConfig(), api_key="x", persistent_kernel=False, enable_telemetry=False)
    runtime = AgentRuntime.from_config(on, client=_FakeClient())
    assert runtime.memory_injector is not None

    off = replace(on, enable_memory=False)
    runtime_off = AgentRuntime.from_config(off, client=_FakeClient())
    assert runtime_off.memory_injector is None
