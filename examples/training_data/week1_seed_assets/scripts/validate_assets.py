"""Validate week-1 seed assets."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[2]
ALLOWED_TOOLS = {"read_file", "python_analysis", "nl_query", "visualization"}
REQUIRED_TASK_FIELDS = {
    "task_id",
    "dataset_id",
    "dataset_path",
    "path_policy",
    "domain",
    "business_context",
    "analysis_type",
    "difficulty",
    "user_request",
    "expected_tools",
    "expected_workflow",
    "oracle_spec",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Invalid JSONL at line {line_no}: {exc}") from exc
    return rows


def count_csv_rows(path: Path) -> tuple[int, list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return len(rows), reader.fieldnames or []


def validate() -> dict[str, Any]:
    manifest_path = ROOT / "dataset_manifest.json"
    seed_tasks_path = ROOT / "seed_tasks.jsonl"

    assert manifest_path.exists(), "dataset_manifest.json is missing"
    assert seed_tasks_path.exists(), "seed_tasks.jsonl is missing"

    manifest = load_json(manifest_path)
    tasks = load_jsonl(seed_tasks_path)

    assert len(manifest) == 20, f"Expected 20 datasets, got {len(manifest)}"
    assert len(tasks) == 100, f"Expected 100 seed tasks, got {len(tasks)}"

    dataset_ids = {item["dataset_id"] for item in manifest}
    assert len(dataset_ids) == 20, "dataset_id values must be unique"

    csv_reports = []
    for item in manifest:
        csv_path = ROOT / item["relative_path"]
        assert csv_path.exists(), f"CSV missing: {csv_path}"
        row_count, columns = count_csv_rows(csv_path)
        assert row_count == item["row_count"], (
            f"{csv_path.name}: manifest row_count={item['row_count']} actual={row_count}"
        )
        missing_columns = set(item["columns"]) - set(columns)
        assert not missing_columns, f"{csv_path.name}: missing columns {sorted(missing_columns)}"
        assert item["date_column"] in columns, f"{csv_path.name}: date column missing"
        csv_reports.append({
            "file_name": csv_path.name,
            "rows": row_count,
            "columns": len(columns),
        })

    task_ids = [task["task_id"] for task in tasks]
    assert len(task_ids) == len(set(task_ids)), "task_id values must be unique"

    by_dataset = Counter(task["dataset_id"] for task in tasks)
    assert set(by_dataset) == dataset_ids, "tasks must cover every dataset"
    assert all(count == 5 for count in by_dataset.values()), "each dataset must have 5 tasks"

    analysis_types = Counter()
    tool_counts = Counter()
    for task in tasks:
        missing = REQUIRED_TASK_FIELDS - set(task)
        assert not missing, f"{task.get('task_id', '<unknown>')}: missing fields {sorted(missing)}"
        assert task["dataset_id"] in dataset_ids, f"{task['task_id']}: unknown dataset"
        assert (PROJECT_ROOT / task["dataset_path"]).exists(), (
            f"{task['task_id']}: dataset path missing"
        )
        assert task["user_request"].strip(), f"{task['task_id']}: empty user_request"
        assert isinstance(task["expected_tools"], list) and task["expected_tools"], (
            f"{task['task_id']}: expected_tools must be non-empty"
        )
        unknown_tools = set(task["expected_tools"]) - ALLOWED_TOOLS
        assert not unknown_tools, f"{task['task_id']}: unknown tools {sorted(unknown_tools)}"
        assert isinstance(task["expected_workflow"], list) and len(task["expected_workflow"]) >= 3, (
            f"{task['task_id']}: expected_workflow must have at least 3 steps"
        )
        analysis_types[task["analysis_type"]] += 1
        tool_counts.update(task["expected_tools"])

    report = {
        "ok": True,
        "datasets": len(manifest),
        "seed_tasks": len(tasks),
        "analysis_types": dict(sorted(analysis_types.items())),
        "tool_coverage": dict(sorted(tool_counts.items())),
        "csv_reports": csv_reports,
    }
    (ROOT / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    print(json.dumps(validate(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
