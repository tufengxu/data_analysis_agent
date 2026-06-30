"""End-to-end coverage of the three target scenarios through the real sandbox.

These run model-style code through PythonAnalysisTool's stateless subprocess
(the permanent fallback path, no kernel needed), so they prove the whole chain:
the openpyxl dependency, the AST allow-list for read_excel, and path scoping all
line up for single-sheet, multi-sheet and multi-file joint analysis.
"""

import pytest

from data_analysis_agent.tools.data_profile import DataProfileTool
from data_analysis_agent.tools.python_exec import PythonAnalysisTool


async def _run(code: str, tmp_path) -> str:
    tool = PythonAnalysisTool(allowed_paths=[tmp_path], kernel=None)
    result = await tool.call({"code": code})
    assert not result.is_error, result.content
    return result.content


async def test_single_sheet_excel_reads(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    xlsx = tmp_path / "data.xlsx"
    pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}).to_excel(xlsx, index=False)

    content = await _run(
        f"import pandas as pd\ndf = pd.read_excel(r'{xlsx}')\nprint('SUM', int(df['a'].sum()))",
        tmp_path,
    )
    assert "SUM 6" in content


async def test_multi_sheet_excel_reads_every_sheet(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    xlsx = tmp_path / "wb.xlsx"
    with pd.ExcelWriter(xlsx) as writer:
        pd.DataFrame({"id": [1, 2]}).to_excel(writer, sheet_name="s1", index=False)
        pd.DataFrame({"id": [3]}).to_excel(writer, sheet_name="s2", index=False)

    content = await _run(
        "import pandas as pd\n"
        f"sheets = pd.read_excel(r'{xlsx}', sheet_name=None)\n"
        "print('SHEETS', sorted(sheets))\n"
        "print('TOTAL', sum(len(v) for v in sheets.values()))",
        tmp_path,
    )
    assert "s1" in content and "s2" in content
    assert "TOTAL 3" in content


async def test_multi_file_join_csv_and_excel(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    orders = tmp_path / "orders.csv"
    orders.write_text("cust_id,amount\n1,10\n2,20\n1,5\n", encoding="utf-8")
    customers = tmp_path / "customers.xlsx"
    pd.DataFrame({"cust_id": [1, 2], "name": ["A", "B"]}).to_excel(customers, index=False)

    content = await _run(
        "import pandas as pd\n"
        f"o = pd.read_csv(r'{orders}')\n"
        f"c = pd.read_excel(r'{customers}')\n"
        "m = o.merge(c, on='cust_id', how='left')\n"
        "print('ROWS', len(m))\n"
        "print('A_TOTAL', int(m[m['name'] == 'A']['amount'].sum()))",
        tmp_path,
    )
    assert "ROWS 3" in content
    assert "A_TOTAL 15" in content


async def test_profile_then_analyze_uses_reported_absolute_path(tmp_path):
    """The intended workflow: discover with data_profile, then feed its reported
    ABSOLUTE path straight into python_analysis — the two halves must connect."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    xlsx = tmp_path / "sales.xlsx"
    pd.DataFrame({"region": ["E", "W", "E"], "amount": [10, 20, 30]}).to_excel(xlsx, index=False)

    profiler = DataProfileTool(allowed_paths=[tmp_path])
    profiled = await profiler.call({"path": str(xlsx)})
    assert not profiled.is_error
    abs_path = profiled.metadata["profile"]["path"]  # exactly what the model copies

    content = await _run(
        f"import pandas as pd\ndf = pd.read_excel(r'{abs_path}')\n"
        "print('TOTAL', int(df['amount'].sum()))",
        tmp_path,
    )
    assert "TOTAL 60" in content
