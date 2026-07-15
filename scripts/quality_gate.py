"""Single source of truth for the project's quality bar (Definition of Done).

Runs ruff + format-check + mypy + pytest + deterministic drift checks, appends a
timing record to .quality/gate-runs.jsonl, and exits non-zero on any failure.

Modes:
    (default)  run the full gate, human-readable output.
    --hook     Claude Code Stop-hook mode: skip when no src/tests/docs changes;
               on failure emit a block decision so the agent must fix before stop.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks  # noqa: E402
import drift_rules  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
BIN = Path(sys.executable).parent


def _run(cmd: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    ok = proc.returncode == 0
    out = (proc.stdout + proc.stderr).strip()
    return ok, out


def _drift() -> tuple[bool, str]:
    problems: list[str] = []
    problems += checks.check_manifest(REPO / "docs/ARCHITECTURE.md", SRC, REPO)
    problems += checks.check_import_rules(SRC, REPO, drift_rules.IMPORT_RULES)
    for doc in drift_rules.DOC_FILES:
        text = (REPO / doc).read_text(encoding="utf-8")
        problems += checks.check_dead_links(text, REPO)
    warnings = checks.check_file_sizes(SRC, REPO, drift_rules.FILE_SIZE_LIMIT)
    msg_parts = []
    if warnings:
        msg_parts.append("warnings:\n  " + "\n  ".join(warnings))
    if problems:
        msg_parts.append("errors:\n  " + "\n  ".join(problems))
    return (not problems), "\n".join(msg_parts).strip()


def _eval() -> tuple[bool, str]:
    # Deterministic structural eval-task gate (no LLM): schema, >=20 tasks,
    # >=3 domains, ADR 0005 assertion-key whitelist. Cheap; catches regressions
    # where the eval corpus shrinks or a value-pinned/invalid assertion sneaks in.
    import eval_gate  # local: scripts dir is on sys.path

    ok, errors = eval_gate.run_gate(REPO / "examples" / "eval_tasks")
    return ok, "\n".join(errors)


def run_gate() -> tuple[bool, list[dict[str, object]]]:
    steps: list[tuple[str, object]] = [
        ("ruff", lambda: _run([str(BIN / "ruff"), "check", "src", "tests", "scripts"])),
        (
            "format",
            lambda: _run([str(BIN / "ruff"), "format", "--check", "src", "tests", "scripts"]),
        ),
        ("mypy", lambda: _run([str(BIN / "mypy"), "src"])),
        ("pytest", lambda: _run([str(BIN / "pytest"), "tests/", "-q"])),
        ("drift", _drift),
        ("eval", _eval),
    ]
    results: list[dict[str, object]] = []
    for name, fn in steps:
        start = time.perf_counter()
        ok, out = fn()  # type: ignore[operator]
        elapsed = round(time.perf_counter() - start, 3)
        results.append({"name": name, "ok": ok, "sec": elapsed, "out": out})
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {name} ({elapsed}s)")
        if not ok and out:
            print("\n".join("    " + line for line in out.splitlines()[-20:]))
    return all(r["ok"] for r in results), results


def _log(passed: bool, results: list[dict[str, object]], total: float) -> None:
    qdir = REPO / ".quality"
    qdir.mkdir(exist_ok=True)
    ok, head = _run(["git", "rev-parse", "--short", "HEAD"])
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "git_head": head if ok else "",
        "passed": passed,
        "total_sec": round(total, 3),
        "steps": [{k: r[k] for k in ("name", "ok", "sec")} for r in results],
    }
    with (qdir / "gate-runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _changed() -> bool:
    """True if src/tests/docs have tracked diffs or untracked files."""
    diff = subprocess.run(["git", "diff", "--quiet", "--", "src", "tests", "docs"], cwd=REPO)
    if diff.returncode != 0:
        return True
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "src", "tests", "docs"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return bool(untracked.stdout.strip())


def main() -> int:
    hook = "--hook" in sys.argv
    if hook and not _changed():
        return 0  # nothing relevant changed -> allow stop

    start = time.perf_counter()
    passed, results = run_gate()
    total = time.perf_counter() - start
    _log(passed, results, total)
    print(f"\n{'PASS' if passed else 'FAIL'} — quality gate ({round(total, 2)}s)")

    if hook and not passed:
        failed = [r["name"] for r in results if not r["ok"]]
        reason = (
            "质量闸未通过(" + ", ".join(map(str, failed)) + ")。"
            "运行 `.venv/bin/python scripts/quality_gate.py` 查看详情并修复后再收尾。"
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0  # block decision delivered via JSON; exit 0
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
