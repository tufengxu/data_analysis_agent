import json
import logging
from pathlib import Path

from data_analysis_agent.config import AgentConfig
from data_analysis_agent.evolution.eval_harvester import (
    derive_tool_count_max,
    harvest_eval_tasks,
    resolve_fixture,
    rewrite_input_paths,
    stable_task_id,
)
from data_analysis_agent.evolution.synthesizer import load_corpus


def test_eval_tasks_dir_under_daa_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    cfg = AgentConfig()
    assert cfg.eval_tasks_dir() == (tmp_path / "daa" / "eval_tasks").resolve()


def _write_turn(dir_path, turn_id, user_input, refs):
    dir_path.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "turn",
        "session_id": "s",
        "turn_id": turn_id,
        "ts_start": "",
        "ts_end": "",
        "user_input": user_input,
        "active_skill": None,
        "tool_calls": [
            {
                "name": "data_profile",
                "is_error": False,
                "duration_ms": 10,
                "result_chars": 100,
                "input_digest": '{"path": "<path:sales.csv>"}',
                "referenced_files": list(refs),
            }
        ],
        "terminal_reason": "COMPLETED",
        "model_turns": 5,
        "tokens": {},
        "final_text_digest": "",
    }
    (dir_path / f"{turn_id}.jsonl").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _make_csv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a,b\n1,2\n", encoding="utf-8")


def test_derive_tool_count_max_headroom_and_cap():
    assert derive_tool_count_max(1) == 2
    assert derive_tool_count_max(4) == 6
    assert derive_tool_count_max(100) == 20  # capped


def test_stable_task_id_is_deterministic():
    a = stable_task_id("分析 sales", ("sales.csv",))
    b = stable_task_id("分析 sales", ("sales.csv",))
    assert a == b and len(a) == 12


def test_rewrite_input_paths_to_fixture():
    assert rewrite_input_paths("对 sales.csv 做统计", "sales.csv") == "对 fixtures/sales.csv 做统计"


def test_harvest_produces_task_and_freezes_fixture(tmp_path):
    traj = tmp_path / "traj"
    data_root = tmp_path / "data"
    _make_csv(data_root / "sales.csv")
    for i in range(3):
        _write_turn(traj, f"t{i}", f"销售分析 第{i}批 sales.csv", ("sales.csv",))

    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    written = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])

    assert len(written) == 3
    task = json.loads(written[0].read_text(encoding="utf-8"))
    assert task["dataset_fixture"] == "fixtures/sales.csv"
    assert "fixtures/sales.csv" in task["input"]
    assert task["assertions"] == {
        "no_error_results": True,
        "min_tool_calls": 1,
        "tool_call_count_max": 2,  # derive_tool_count_max(1 tool call) -> 2
    }
    assert (eval_dir / "fixtures" / "sales.csv").read_text(encoding="utf-8") == "a,b\n1,2\n"
    # ADR 0005: NO numeric value assertions beyond structure
    for key in task["assertions"]:
        assert key in {"no_error_results", "min_tool_calls", "tool_call_count_max"}


def test_harvest_idempotent(tmp_path):
    traj = tmp_path / "traj"
    data_root = tmp_path / "data"
    _make_csv(data_root / "sales.csv")
    _write_turn(traj, "t1", "销售分析 sales.csv", ("sales.csv",))

    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    first = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])
    second = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])
    assert [p.name for p in first] == [p.name for p in second]
    assert len(second) == 1  # no duplication


def test_harvest_skips_missing_referenced_file(tmp_path, caplog):
    traj = tmp_path / "traj"
    _write_turn(traj, "t1", "销售分析 sales.csv", ("sales.csv",))

    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    written = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [tmp_path / "nope"])
    assert written == []
    assert any("sales.csv" in r.message for r in caplog.records)


def test_harvest_eval_cli_writes_tasks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    traj = tmp_path / "daa" / "trajectories"
    data_root = tmp_path / "data"
    _make_csv(data_root / "sales.csv")
    _write_turn(traj, "t1", "销售分析 sales.csv", ("sales.csv",))

    from data_analysis_agent.evolution.__main__ import main

    rc = main(["harvest-eval", "--data-search-path", str(data_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "收割" in out
    assert (tmp_path / "daa" / "eval_tasks").is_dir()


def test_harvest_eval_cli_requires_data_search_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    from data_analysis_agent.evolution.__main__ import main

    rc = main(["harvest-eval"])
    assert rc == 1
    assert "--data-search-path" in capsys.readouterr().out


def test_evaluator_reads_multiple_dirs(tmp_path):
    from data_analysis_agent.evolution.evaluator import SkillEvaluator

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "ta.json").write_text(
        json.dumps({"task_id": "ta", "input": "销售 x", "assertions": {}}), encoding="utf-8"
    )
    (dir_b / "tb.json").write_text(
        json.dumps({"task_id": "tb", "input": "销售 y", "assertions": {}}), encoding="utf-8"
    )

    def run_fn(task, skill):
        from data_analysis_agent.evolution.evaluator import EvalRun

        return EvalRun(tool_call_count=2, has_error=False, final_text="ok")

    ev = SkillEvaluator([dir_a, dir_b], tmp_path / "skills", run_fn, min_samples=1)

    class FakeSkill:
        name = "sales"
        keywords = ["销售"]

    # _all_tasks is the multi-dir aggregation surface
    tasks = ev._all_tasks()
    assert {t.task_id for t in tasks} == {"ta", "tb"}


# --- §8 robustness / §6.3 idempotency hardening (final-fix) ---


def test_harvest_skips_task_when_fixture_copy_fails(tmp_path, monkeypatch, caplog):
    """Fix 1: a single OSError during fixture copy must skip THAT task only."""
    traj = tmp_path / "traj"
    data_root = tmp_path / "data"
    # two distinct fixtures so the two turns resolve to different basenames
    _make_csv(data_root / "sales.csv")
    _make_csv(data_root / "orders.csv")
    _write_turn(traj, "t_good", "销售分析 sales.csv", ("sales.csv",))
    _write_turn(traj, "t_bad", "订单分析 orders.csv", ("orders.csv",))

    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    fixtures_dir = eval_dir / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    real_write_bytes = Path.write_bytes

    def flaky_write_bytes(self, data):
        if self.name == "orders.csv":
            raise OSError("simulated disk full on fixture copy")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)
    caplog.set_level(logging.WARNING, logger="data_analysis_agent.evolution.eval_harvester")

    written = harvest_eval_tasks(corpus, eval_dir, fixtures_dir, [data_root])

    # the bad task is skipped; the good task survives
    names = {p.name for p in written}
    assert written, "at least the good task must survive"
    bad_id = stable_task_id("订单分析 orders.csv", ("orders.csv",))
    assert f"{bad_id}.json" not in names, "bad fixture task must not be written"
    # a warning mentions the failing task / basename
    assert any("orders.csv" in r.message or "t_bad" in r.message for r in caplog.records), (
        f"expected warning naming the failed task; got {[r.message for r in caplog.records]}"
    )


def test_harvest_skips_task_when_json_write_fails(tmp_path, monkeypatch, caplog):
    """Fix 1: a JSON-write failure must skip THAT task only, no exception escapes."""
    traj = tmp_path / "traj"
    data_root = tmp_path / "data"
    _make_csv(data_root / "sales.csv")
    _make_csv(data_root / "orders.csv")
    _write_turn(traj, "t_good", "销售分析 sales.csv", ("sales.csv",))
    _write_turn(traj, "t_bad", "订单分析 orders.csv", ("orders.csv",))

    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"

    real_write_text = Path.write_text

    bad_id = stable_task_id("订单分析 orders.csv", ("orders.csv",))
    bad_name = f"{bad_id}.json"

    def flaky_write_text(self, data, *args, **kwargs):
        if self.name == bad_name:
            raise OSError("simulated permission denied on eval JSON write")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)
    caplog.set_level(logging.WARNING, logger="data_analysis_agent.evolution.eval_harvester")

    written = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])

    names = {p.name for p in written}
    assert bad_name not in names, "task whose JSON write failed must not be in written"
    assert written, "the other task must survive"
    assert any(bad_id in r.message or "t_bad" in r.message for r in caplog.records), (
        f"expected warning for failed JSON write; got {[r.message for r in caplog.records]}"
    )


def test_resolve_fixture_logs_ambiguity(tmp_path, caplog):
    """Fix 2: basename resolved in ≥2 search paths → warn + return first."""
    from pathlib import Path as _Path

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    _make_csv(dir_a / "sales.csv")
    _make_csv(dir_b / "sales.csv")

    caplog.set_level(logging.WARNING, logger="data_analysis_agent.evolution.eval_harvester")
    result = resolve_fixture("sales.csv", [_Path(dir_a), _Path(dir_b)])

    assert result is not None
    assert result.parent == dir_a, "must return the FIRST match"
    assert any("sales.csv" in r.message and "ambiguous" in r.message for r in caplog.records), (
        f"expected ambiguity warning; got {[r.message for r in caplog.records]}"
    )


def test_stable_task_id_invariant_to_referenced_order():
    """Fix 3: id must be identical for any permutation of referenced files."""
    refs_a = ("sales.csv", "orders.csv", "regions.csv")
    refs_b = ("regions.csv", "orders.csv", "sales.csv")
    assert stable_task_id("分析", refs_a) == stable_task_id("分析", refs_b)
