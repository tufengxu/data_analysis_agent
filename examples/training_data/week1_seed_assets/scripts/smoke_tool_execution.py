"""Smoke-test week-1 assets with the real DataAnalysisAgent tools."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATASET_PATH = "examples/training_data/week1_seed_assets/data/retail_sales_orders.csv"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from data_analysis_agent.tools import FileReadTool, PythonAnalysisTool  # noqa: E402


async def main() -> None:
    read_tool = FileReadTool()
    read_result = await read_tool.call({"file_path": DATASET_PATH, "limit": 5})
    if read_result.is_error:
        raise AssertionError(read_result.content)

    absolute_path = (PROJECT_ROOT / DATASET_PATH).resolve()
    python_tool = PythonAnalysisTool(allowed_paths=[PROJECT_ROOT])
    code = (
        "import csv\n"
        f"with open(r'{absolute_path}', newline='', encoding='utf-8') as f:\n"
        "    rows = list(csv.DictReader(f))\n"
        "summary = {}\n"
        "for row in rows:\n"
        "    summary[row['channel']] = summary.get(row['channel'], 0.0) + float(row['revenue'])\n"
        "top_channel, top_revenue = max(summary.items(), key=lambda item: item[1])\n"
        "print({'rows': len(rows), 'top_channel': top_channel, "
        "'top_revenue': round(top_revenue, 2)})\n"
    )
    analysis_result = await python_tool.call({"code": code})
    if analysis_result.is_error:
        raise AssertionError(analysis_result.content)

    print(
        json.dumps(
            {
                "ok": True,
                "read_file_preview_chars": len(read_result.content),
                "python_analysis_output": analysis_result.content,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
