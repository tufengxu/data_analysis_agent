"""Tests for evidence_ref resolution (audit §3.6 / P0-3).

Covers the QA resolver hook (tri-state, None=skip), ResultStore.contains
(read-only, no eviction), and the html_report resolver policy
(known result_id / artifact file → True; file-shaped but missing → False;
descriptive → None).
"""

from __future__ import annotations

from data_analysis_agent.reporting.contract import BlockRole, ReportBlock, ReportDocument
from data_analysis_agent.reporting.qa import Severity, run_qa
from data_analysis_agent.sampling.result_store import ResultStore
from data_analysis_agent.tools.html_report import (
    HtmlReportTool,
    _build_evidence_resolver,
    _looks_like_artifact_ref,
)


def _doc_with_evidence(*refs: str) -> ReportDocument:
    return ReportDocument(
        title="t",
        blocks=(
            ReportBlock(
                block_id="b1",
                role=BlockRole.FINDING,
                body="a finding with a figure 12%",
                evidence_refs=tuple(refs),
            ),
        ),
    )


# --- QA resolver hook --------------------------------------------------------


def test_resolver_fabricated_ref_yields_high_finding():
    doc = _doc_with_evidence("fake.json")
    # resolver says the file-shaped ref does NOT resolve → fabricated
    qa = run_qa(doc, artifact_exists=True, evidence_resolver=lambda r: False)
    codes = [f.code for f in qa.findings]
    assert "evidence.unresolved" in codes
    assert next(f for f in qa.findings if f.code == "evidence.unresolved").severity is Severity.HIGH


def test_resolver_resolved_ref_no_finding():
    doc = _doc_with_evidence("real.json")
    qa = run_qa(doc, artifact_exists=True, evidence_resolver=lambda r: True)
    assert "evidence.unresolved" not in [f.code for f in qa.findings]


def test_resolver_descriptive_ref_no_finding():
    # resolver returns None (descriptive free text) → not penalized
    doc = _doc_with_evidence("Q3 revenue trend")
    qa = run_qa(doc, artifact_exists=True, evidence_resolver=lambda r: None)
    assert "evidence.unresolved" not in [f.code for f in qa.findings]


def test_no_resolver_skips_resolution_check():
    # back-compat: resolver=None → no resolution findings at all
    doc = _doc_with_evidence("fake.json")
    qa = run_qa(doc, artifact_exists=True)
    assert "evidence.unresolved" not in [f.code for f in qa.findings]


def test_resolver_exception_is_treated_as_unknown_not_blocking():
    """A resolver hiccup must never block the report (defensive)."""
    doc = _doc_with_evidence("x.json")

    def boom(_r: str) -> bool | None:
        raise RuntimeError("store down")

    qa = run_qa(doc, artifact_exists=True, evidence_resolver=boom)
    assert "evidence.unresolved" not in [f.code for f in qa.findings]


def test_resolver_only_flags_fabricated_not_resolved_in_same_doc():
    doc = _doc_with_evidence("real.json", "fake.png", "descriptive note")
    statuses = {"real.json": True, "fake.png": False, "descriptive note": None}
    qa = run_qa(doc, artifact_exists=True, evidence_resolver=lambda r: statuses[r])
    unresolved = [f for f in qa.findings if f.code == "evidence.unresolved"]
    # only the fabricated one; resolved + descriptive are silent
    assert len(unresolved) == 1


# --- ResultStore.contains ----------------------------------------------------


def test_result_store_contains_present_and_absent(tmp_path):
    store = ResultStore(tmp_path / "rs")
    store.put("rid_1", "line0\nline1\n", {"tool": "python_analysis"})
    assert store.contains("rid_1") is True
    assert store.contains("missing") is False


def test_result_store_contains_expired_is_false_no_eviction(tmp_path):
    now = [1000.0]
    store = ResultStore(tmp_path / "rs", ttl_seconds=1, clock=lambda: now[0])
    store.put("rid_1", "data\n", {"tool": "t"})
    assert store.contains("rid_1") is True
    now[0] += 5  # past TTL
    assert store.contains("rid_1") is False
    # contains() must NOT have evicted — the entry is still in the index
    assert "rid_1" in store._index


# --- html_report resolver policy --------------------------------------------


def test_looks_like_artifact_ref():
    assert _looks_like_artifact_ref("chart.json") is True
    assert _looks_like_artifact_ref("/abs/path/fig.png") is True
    assert _looks_like_artifact_ref("~/x.svg") is True  # has ext
    assert _looks_like_artifact_ref("Q3 revenue") is False
    assert _looks_like_artifact_ref("ev_001") is False  # no ext, not abs → descriptive


def test_resolver_known_result_id_is_resolved(tmp_path):
    store = ResultStore(tmp_path / "rs")
    store.put("rid_1", "data\n", {"tool": "t"})
    resolver = _build_evidence_resolver(store, tmp_path)
    assert resolver("rid_1") is True


def test_resolver_artifact_file_exists_is_resolved(tmp_path):
    (tmp_path / "fig.png").write_bytes(b"x")
    resolver = _build_evidence_resolver(None, tmp_path)
    assert resolver("fig.png") is True


def test_resolver_missing_file_shaped_ref_is_fabricated(tmp_path):
    resolver = _build_evidence_resolver(None, tmp_path)
    assert resolver("does_not_exist.json") is False


def test_resolver_descriptive_ref_is_unknown(tmp_path):
    resolver = _build_evidence_resolver(None, tmp_path)
    assert resolver("Q3 revenue trend") is None


def test_html_report_tool_builds_resolver_from_stores(tmp_path):
    store = ResultStore(tmp_path / "rs")
    store.put("rid_1", "data\n", {"tool": "t"})
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "real.png").write_bytes(b"x")
    tool = HtmlReportTool(artifact_dir=tmp_path / "artifacts", result_store=store)
    assert tool._evidence_resolver("rid_1") is True
    assert tool._evidence_resolver("real.png") is True
    assert tool._evidence_resolver("missing.json") is False
    assert tool._evidence_resolver("a descriptive note") is None


def test_html_report_tool_resolver_works_without_result_store(tmp_path):
    # result_store=None (default) → resolver still does artifact-file resolution
    tool = HtmlReportTool(artifact_dir=tmp_path)
    assert tool._evidence_resolver("missing.json") is False
    assert tool._evidence_resolver("descriptive") is None


# --- delivery semantics: fabricated evidence is HIGH, not a blocker ----------


def test_fabricated_evidence_is_needs_review_not_draft():
    # evidence.unresolved is HIGH (not BLOCKER), consistent with evidence.empty_ref.
    # _classify maps a HIGH-only doc to NEEDS_REVIEW (renders with badge), NOT DRAFT
    # (refused). The full render path is covered by test_html_report_v2's
    # needs-review-renders test; this locks the severity→readiness mapping that
    # makes fabrication a visible badge, not a render refusal.
    from data_analysis_agent.reporting.qa import QAFinding, Readiness, Severity, _classify

    finding = QAFinding(Severity.HIGH, "evidence.unresolved", "x", "b1")
    assert _classify([finding], artifact_exists=True) is Readiness.NEEDS_REVIEW


# --- path safety: resolution confined to artifact_dir subtree ----------------


def test_resolver_does_not_resolve_files_outside_artifact_dir(tmp_path):
    # A file that EXISTS outside artifact_dir must NOT count as resolved — no
    # file-existence oracle, no system-file-as-evidence.
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    outside = tmp_path / "escape.json"  # exists, but in the parent of artifact_dir
    outside.write_text("{}")
    resolver = _build_evidence_resolver(None, artifacts)
    assert resolver("../escape.json") is False  # escapes subtree → False, not probed
    assert resolver("/etc/hosts") is False  # absolute outside → False, not probed


def test_resolver_resolves_real_artifact_inside_subtree(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "real.json").write_text("{}")
    resolver = _build_evidence_resolver(None, artifacts)
    assert resolver("real.json") is True


def test_resolver_tilde_not_expanded(tmp_path):
    # "~" must not be expanded to the home dir (no probing of user files).
    resolver = _build_evidence_resolver(None, tmp_path)
    # ".zshrc" is not a recognized artifact extension → descriptive → None (not probed)
    assert resolver("~/.zshrc") is None
    # "~/x.svg" HAS a recognized ext → must still be False (resolved under the
    # literal "tmp_path/~/" subtree, which doesn't exist), never expanded to $HOME.
    assert resolver("~/x.svg") is False
