from data_analysis_agent.config import AgentConfig


def test_eval_tasks_dir_under_daa_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    cfg = AgentConfig()
    assert cfg.eval_tasks_dir() == (tmp_path / "daa" / "eval_tasks").resolve()
