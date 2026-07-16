"""Phase 2: quality-gate CI step-summary writer (GITHUB_STEP_SUMMARY)."""

from __future__ import annotations

from quality_gate import _write_step_summary


def test_step_summary_noop_without_env(monkeypatch):
    """No GITHUB_STEP_SUMMARY (local run) → no file, no error."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    _write_step_summary([{"name": "ruff", "ok": True, "sec": 0.1, "out": ""}], True, 0.1)


def test_step_summary_writes_table_and_failure_detail(monkeypatch, tmp_path):
    out = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
    results = [
        {"name": "ruff", "ok": True, "sec": 0.1, "out": ""},
        {"name": "mypy", "ok": False, "sec": 0.2, "out": "src/x.py:1: error: bad [attr]"},
    ]
    _write_step_summary(results, passed=False, total=0.3)
    text = out.read_text(encoding="utf-8")
    assert "| step | result | time |" in text
    assert "`ruff`" in text and "`mypy`" in text  # both rows present
    assert "**FAIL**" in text
    assert "src/x.py:1: error: bad" in text  # failure detail tail surfaced
    assert "<details>" in text  # collapsible detail block


def test_step_summary_pass_has_no_failure_detail(monkeypatch, tmp_path):
    out = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
    _write_step_summary([{"name": "ruff", "ok": True, "sec": 0.1, "out": ""}], True, 0.1)
    text = out.read_text(encoding="utf-8")
    assert "**PASS**" in text
    assert "<details>" not in text  # no failure block on a green run
