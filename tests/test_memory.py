"""Tests for Stage B: domain memory (profiles, store, injector, light-confirm)."""

from typing import Any

from data_analysis_agent.agent_loop import AgentLoop, AgentLoopConfig
from data_analysis_agent.memory import (
    DatasetProfile,
    MemoryEntry,
    MemoryInjector,
    MemoryStore,
    ProfileStore,
    assess,
    build_profile,
    column_fingerprint,
)
from data_analysis_agent.memory.model import CONFIRM_AFTER_USES
from data_analysis_agent.protocol.messages import ModelResponse, TextBlock
from data_analysis_agent.tools.registry import ToolRegistry


def _make_csv(path, header="region,month,sales", rows=("华东,1,120", "华北,1,80")):
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# --- fingerprint + profile generation ---------------------------------------


def test_fingerprint_is_order_independent():
    assert column_fingerprint(["a", "b", "c"]) == column_fingerprint(["c", "a", "b"])
    assert column_fingerprint(["a", "b"]) != column_fingerprint(["a", "b", "c"])


def test_build_profile_extracts_structure(tmp_path):
    csv = _make_csv(tmp_path / "sales.csv")
    profile = build_profile(csv)
    assert profile is not None
    assert profile.columns == ["region", "month", "sales"]
    assert profile.structure["n_cols"] == 3
    assert profile.column_fingerprint == column_fingerprint(["region", "month", "sales"])


def test_build_profile_rejects_non_tabular(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    assert build_profile(txt) is None


# --- layered staleness (decision 4) -----------------------------------------


def test_assess_fresh_stale_invalid(tmp_path):
    csv = _make_csv(tmp_path / "sales.csv")
    profile = build_profile(csv)
    assert profile is not None
    assert assess(profile, csv) == "fresh"

    # Same schema, newer data → stale (stats need recompute, structure kept).
    import os

    future = profile.stats_mtime + 1000
    os.utime(csv, (future, future))
    assert assess(profile, csv) == "stale"

    # Added column → invalid (whole profile void).
    _make_csv(csv, header="region,month,sales,channel", rows=("华东,1,120,线上",))
    assert assess(profile, csv) == "invalid"


def test_profile_store_record_lifecycle(tmp_path):
    csv = _make_csv(tmp_path / "sales.csv")
    store = ProfileStore(tmp_path / "mem")
    p1 = store.record(csv)
    assert p1 is not None

    # Re-record after column change rebuilds (new fingerprint).
    _make_csv(csv, header="region,month,sales,channel", rows=("华东,1,120,线上",))
    p2 = store.record(csv)
    assert p2 is not None
    assert p2.column_fingerprint != p1.column_fingerprint

    # Persisted and reloadable.
    reloaded = ProfileStore(tmp_path / "mem")
    assert reloaded.get(csv) is not None
    assert reloaded.get(csv).column_fingerprint == p2.column_fingerprint


# --- MemoryStore + light-confirm --------------------------------------------


def test_memory_store_search_and_upsert(tmp_path):
    store = MemoryStore(tmp_path / "mem")
    store.put(
        MemoryEntry(
            kind="metric_definition", key="活跃用户", content="过去7天有登录", confirmed=False
        )
    )
    store.put(MemoryEntry(kind="analysis_pref", key="图表语言", content="图表标签用中文"))

    hits = store.search("活跃用户怎么算")
    assert any(e.key == "活跃用户" for e in hits)
    assert store.search("完全无关的查询") == []


def test_metric_auto_confirms_after_uses(tmp_path):
    store = MemoryStore(tmp_path / "mem")
    store.put(MemoryEntry(kind="metric_definition", key="GMV", content="不含退款", confirmed=False))
    assert store.get("metric_definition", "GMV").confirmed is False
    for _ in range(CONFIRM_AFTER_USES):
        store.touch("metric_definition", "GMV")
    assert store.get("metric_definition", "GMV").confirmed is True


def test_memory_store_persists(tmp_path):
    store = MemoryStore(tmp_path / "mem")
    store.put(MemoryEntry(kind="open_concern", key="缺失值", content="sales 列有空值"))
    reloaded = MemoryStore(tmp_path / "mem")
    assert reloaded.get("open_concern", "缺失值") is not None


# --- MemoryInjector ----------------------------------------------------------


def _injector(tmp_path) -> MemoryInjector:
    return MemoryInjector(ProfileStore(tmp_path / "p"), MemoryStore(tmp_path / "p"))


def test_injector_renders_profile_when_query_mentions_file(tmp_path):
    csv = _make_csv(tmp_path / "sales.csv")
    inj = _injector(tmp_path)
    inj.profiles.record(csv)

    rendered = inj.render("帮我分析 sales.csv 的区域分布")
    assert "sales.csv" in rendered
    assert "region" in rendered
    # Unrelated query → no profile injected.
    assert "sales.csv" not in inj.render("今天天气如何")


def test_injector_record_tool_captures_profile(tmp_path):
    csv = _make_csv(tmp_path / "sales.csv")
    inj = _injector(tmp_path)
    inj.record_tool("read_file", {"file_path": str(csv)}, {})
    assert inj.profiles.get(csv) is not None
    # Non-read_file or non-tabular → no capture.
    inj.record_tool("python_analysis", {"code": "x=1"}, {})
    inj.record_tool("read_file", {"file_path": str(tmp_path / "a.txt")}, {})
    assert len(inj.profiles.all()) == 1


def test_injector_light_confirm_wording_and_touch(tmp_path):
    inj = _injector(tmp_path)
    inj.remember_metric("活跃用户", "过去7天有登录", session_id="s1")

    first = inj.render("活跃用户的趋势")
    assert "基于历史推断" in first  # unconfirmed wording shown
    # Second surfacing reaches the confirm threshold → wording drops.
    second = inj.render("活跃用户分布")
    assert "基于历史推断" not in second
    assert inj.memory.get("metric_definition", "活跃用户").confirmed is True


def test_injector_truncates_to_budget(tmp_path):
    inj = MemoryInjector(
        ProfileStore(tmp_path / "p"), MemoryStore(tmp_path / "p"), budget_tokens=20
    )
    for i in range(30):
        inj.memory.put(
            MemoryEntry(kind="analysis_pref", key=f"pref{i}", content="分析偏好 " * 10 + str(i))
        )
    rendered = inj.render("分析偏好")
    assert "截断" in rendered


# --- agent_loop integration --------------------------------------------------


class _SequenceClient:
    model = "dummy"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def stream_model(
        self, messages, system=None, tools=None, max_tokens=None, tool_choice=None
    ):
        self.calls.append({"system": system})
        response = self.responses.pop(0)
        for block in response.content:
            yield block
        yield response


async def test_memory_injected_into_system_prompt(tmp_path):
    inj = _injector(tmp_path)
    inj.memory.put(MemoryEntry(kind="analysis_pref", key="图表语言", content="图表标签一律用中文"))
    client = _SequenceClient([ModelResponse(content=[TextBlock("ok")], stop_reason="end_turn")])
    agent = AgentLoop(
        AgentLoopConfig(api_key="test"),
        ToolRegistry(),
        client=client,
        memory_injector=inj.render,
    )

    [e async for e in agent.run("按图表语言偏好分析")]
    assert "图表标签一律用中文" in client.calls[0]["system"]


def test_dataset_profile_roundtrip():
    p = DatasetProfile(
        path="/x/sales.csv",
        column_fingerprint="abc",
        structure={"n_cols": 1, "columns": [{"name": "a", "dtype": "int64"}]},
        statistics={"n_rows_sampled": 10, "nulls": {"a": 0}},
        stats_mtime=1.0,
    )
    assert DatasetProfile.from_dict(p.to_dict()).columns == ["a"]


def test_config_dirs_survive_readonly_home(tmp_path, monkeypatch):
    """M1 regression: a read-only DAA_HOME must not crash startup."""
    import os

    from data_analysis_agent.config import AgentConfig

    home = tmp_path / "daa"
    home.mkdir()
    monkeypatch.setenv("DAA_HOME", str(home))
    os.chmod(home, 0o500)  # read+execute, no write
    try:
        config = AgentConfig()
        # Must return paths without raising, even though mkdir fails.
        assert config.skills_dir().name == "skills"
        assert config.memory_dir().name == "memory"
        assert config.trajectories_dir().name == "trajectories"
    finally:
        os.chmod(home, 0o700)


def test_from_dict_metric_missing_confirmed_defaults_false():
    """P0-B regression: a metric row without `confirmed` must default to False."""
    e = MemoryEntry.from_dict({"kind": "metric_definition", "key": "活跃用户", "content": "7天"})
    assert e.confirmed is False
    # Non-metric without confirmed keeps the trivially-confirmed default.
    pref = MemoryEntry.from_dict({"kind": "analysis_pref", "key": "x", "content": "y"})
    assert pref.confirmed is True


def test_profile_stale_flag_clears_on_refresh(tmp_path):
    """P0-C regression: a staled profile that re-verifies fresh must clear stale."""
    import os

    csv = _make_csv(tmp_path / "sales.csv")
    store = ProfileStore(tmp_path / "mem")
    p = store.record(csv)
    assert p is not None
    p.stale = True  # simulate a prior failed stats refresh
    store._rewrite()

    # Re-record with the file unchanged → assess fresh → stale must reset.
    os.utime(csv, (p.stats_mtime, p.stats_mtime))
    refreshed = store.record(csv)
    assert refreshed is not None and refreshed.stale is False


def test_memory_store_unreadable_file_degrades(tmp_path, monkeypatch):
    """P0-A regression: an unreadable memory.jsonl must not crash construction."""
    d = tmp_path / "mem"
    d.mkdir()
    (d / "memory.jsonl").write_text("{}", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.open", boom)
    store = MemoryStore(d)  # must not raise
    assert store.all() == []
