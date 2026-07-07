"""Wave 4 html_report v2: ReportDocument 渲染 + v1 零回归(golden)+ 逃逸/属性 XSS。

v2 路径叠加在 v1 之上(`_is_v2` 检测 `document` 键);v1 代码逐字不动。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from data_analysis_agent.reporting.contract import (
    BlockRole,
    ChartFamily,
    ChartFields,
    ChartSpec,
    ReportBlock,
    ReportContract,
    ReportDocument,
    ReportType,
)
from data_analysis_agent.tools.html_report import MAX_OPTION_CHARS, MAX_SECTIONS, HtmlReportTool

_V1_INPUT = {
    "title": "销售周报",
    "subtitle": "2026 W27",
    "summary": "GMV 环比上升。\n\n渠道 A 贡献增量。",
    "sections": [
        {
            "heading": "总览",
            "text": "总 GMV 12 万",
            "chart": {"option": {"x": {"data": ["A", "B"]}}},
        },
        {
            "heading": "明细",
            "table": {"columns": ["渠道", "GMV"], "rows": [["A", "7"], ["B", "5"]]},
        },
    ],
}


def _tool(tmp_path: Path) -> HtmlReportTool:
    return HtmlReportTool(artifact_dir=tmp_path)


def _doc_dict(
    *,
    title: str = "2026 销售日报",
    data_scope: str | None = "sales.csv,上周,100 行",
    blocks: tuple[ReportBlock, ...] | None = None,
) -> dict[str, object]:
    doc = ReportDocument(
        title=title,
        contract=ReportContract(
            question="q",
            report_type=ReportType.DAILY_KPI,
            explicit_requirement_refs=("u1",),
        ),
        data_scope=data_scope,
        blocks=blocks
        if blocks is not None
        else (
            ReportBlock(block_id="h", role=BlockRole.HEADER, heading="销售日报"),
            ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="GMV 环比上升"),
            ReportBlock(
                block_id="k",
                role=BlockRole.KPI_STRIP,
                kpi_cards=((("label", "GMV"), ("value", "12万"), ("delta", "+12%")),),
            ),
            ReportBlock(
                block_id="f",
                role=BlockRole.FINDING,
                heading="渠道归因",
                body="渠道 A 贡献增量",
                evidence_refs=("e1",),
                user_need_refs=("report_type",),
                caveats=("样本量较小",),
            ),
            ReportBlock(
                block_id="c",
                role=BlockRole.CHART,
                chart=ChartSpec(
                    family=ChartFamily.GROUPED_BAR,
                    fields=ChartFields(x="channel", y="gmv"),
                    interpretation="渠道 A 领先",
                    caption="按渠道 GMV",
                ),
                evidence_refs=("e1",),
            ),
            ReportBlock(
                block_id="t",
                role=BlockRole.TABLE,
                table_columns=("渠道", "GMV"),
                table_rows=(("A", "7"), ("B", "5")),
            ),
            ReportBlock(block_id="r", role=BlockRole.RECOMMENDATION, body="加大渠道 A"),
            ReportBlock(block_id="cv", role=BlockRole.CAVEAT, body="7-06 为部分周期"),
            ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="sales.csv"),
        ),
    )
    return doc.to_dict()


# ----------------------------- v1 零回归 -----------------------------


def test_v1_dispatch_and_output(tmp_path: Path):
    tool = _tool(tmp_path)
    assert tool._is_v2(_V1_INPUT) is False
    page = tool._render_page(_V1_INPUT)
    assert "ECharts 可视化报告" in page  # v1 footer
    assert "ReportDocument v2" not in page
    assert "qa-badge" not in page


def test_v1_output_byte_identical(tmp_path: Path):
    """v1 渲染输出(v1 代码逐字不动)——golden 守护,任何静默漂移 → 失败。"""
    tool = _tool(tmp_path)
    page = tool._render_page(_V1_INPUT)
    normalized = re.sub(r"生成时间:[^\n]*", "生成时间:T", page)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    assert digest == "0c591784fad54a2e228d1cb02a542b6a151764b4e12ba3a29b8f748e3a172635", (
        f"v1 输出 hash 变化,实际:{digest}"
    )


# ----------------------------- v2 分流 -----------------------------


def test_v2_dispatch_with_both_v1_and_document_keys(tmp_path: Path):
    tool = _tool(tmp_path)
    mixed = {**_V1_INPUT, "document": _doc_dict()}
    assert tool._is_v2(mixed) is True


def test_v2_renders_all_roles(tmp_path: Path):
    tool = _tool(tmp_path)
    page = tool._render_v2_page(_doc_dict(), {})
    for marker in [
        'class="card summary"',
        'class="kpi-strip"',
        'class="card finding"',
        'class="card chart-block"',
        'class="card recommendation"',
        'class="card caveat"',
        "ReportDocument v2",
    ]:
        assert marker in page, f"缺 {marker}"
    # table 渲染
    assert "<table>" in page and "<td>A</td>" in page


def test_v2_qa_badge_ready(tmp_path: Path):
    tool = _tool(tmp_path)
    clean = (
        ReportBlock(block_id="s", role=BlockRole.EXECUTIVE_SUMMARY, body="结论"),
        ReportBlock(
            block_id="r", role=BlockRole.RECOMMENDATION, body="建议 A", evidence_refs=("e1",)
        ),
        ReportBlock(block_id="src", role=BlockRole.SOURCE_METADATA, body="sales.csv"),
    )
    page = tool._render_v2_page(_doc_dict(blocks=clean), {})
    assert 'class="qa-badge qa-ready"' in page
    assert 'class="qa-banner' not in page  # ready 无 banner 元素


def test_v2_qa_badge_draft_with_blocker(tmp_path: Path):
    tool = _tool(tmp_path)
    page = tool._render_v2_page(_doc_dict(data_scope=None), {})
    assert 'class="qa-badge qa-draft"' in page
    assert 'class="qa-banner draft"' in page


def test_v2_traceability_data_attrs(tmp_path: Path):
    tool = _tool(tmp_path)
    page = tool._render_v2_page(_doc_dict(), {})
    assert 'data-block-id="f"' in page
    assert 'data-evidence-refs="e1"' in page
    assert 'data-user-need-refs="report_type"' in page


def test_v2_print_css_present(tmp_path: Path):
    tool = _tool(tmp_path)
    page = tool._render_v2_page(_doc_dict(), {})
    assert "@media print" in page


# ----------------------------- v2 逃逸(含属性 XSS) -----------------------------


def test_v2_escapes_element_text(tmp_path: Path):
    tool = _tool(tmp_path)
    doc = _doc_dict(
        title="<script>x</script>",
        blocks=(
            ReportBlock(
                block_id="s",
                role=BlockRole.EXECUTIVE_SUMMARY,
                body="<img src=x onerror=alert(1)>",
                heading="<b>h</b>",
            ),
        ),
    )
    page = tool._render_v2_page(doc, {})
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page
    assert "<img src=x onerror=alert(1)>" not in page
    assert "&lt;img" in page


def test_v2_escapes_attribute_values_xss(tmp_path: Path):
    """evidence_refs 含引号/尖括号 → 不得逃逸 data-evidence-refs 属性。"""
    tool = _tool(tmp_path)
    evil = '"><img src=x onerror=alert(1)>'
    doc = _doc_dict(
        blocks=(
            ReportBlock(
                block_id="f",
                role=BlockRole.FINDING,
                body="ok",
                evidence_refs=(evil,),
            ),
        ),
    )
    page = tool._render_v2_page(doc, {})
    assert "<img src=x onerror=alert(1)>" not in page
    # 属性值中的引号必须被转义
    assert f'data-evidence-refs="{evil}' not in page


# ----------------------------- v2 图表 -----------------------------


def test_v2_chart_from_options_map(tmp_path: Path):
    tool = _tool(tmp_path)
    page = tool._render_v2_page(
        _doc_dict(),
        {"c": {"x": {"data": ["A", "B"]}, "y": {"data": [7, 5]}}, "height": 200},
    )
    assert 'id="chart_c"' in page
    assert "render(" in page  # render call 注入


def test_v2_chart_placeholder_when_no_option(tmp_path: Path):
    tool = _tool(tmp_path)
    page = tool._render_v2_page(_doc_dict(), {})  # 无 charts
    assert "chart-placeholder" in page
    assert "图表族:grouped_bar" in page
    assert "渠道 A 领先" in page  # interpretation


# ----------------------------- v2 校验 -----------------------------


def test_v2_validates_document_required(tmp_path: Path):
    tool = _tool(tmp_path)
    assert not tool.validate_input({}).valid
    assert not tool.validate_input({"document": "not-dict"}).valid
    assert not tool.validate_input({"document": {"title": "  "}}).valid
    assert tool.validate_input({"document": _doc_dict()}).valid


def test_v2_file_name_bare_name_rule(tmp_path: Path):
    tool = _tool(tmp_path)
    for bad in ["../evil.html", "a/b.html", "CON.html", "x.", " "]:
        assert not tool.validate_input({"document": _doc_dict(), "file_name": bad}).valid, (
            f"{bad!r} 应被拒"
        )


async def test_v2_writes_artifact_and_path_containment(tmp_path: Path):
    tool = _tool(tmp_path)
    result = await tool.call({"document": _doc_dict()})
    assert not result.is_error
    out = Path(result.metadata["artifact_paths"][0])
    assert out.is_relative_to(tmp_path)
    assert out.exists()
    # 逃逸尝试
    evil = await tool.call({"document": _doc_dict(), "file_name": "../evil.html"})
    assert evil.is_error


# ----------------------------- v2 体积/结构上限(评审 High/Medium) -----------------------------


def test_v2_blocks_cap(tmp_path: Path):
    tool = _tool(tmp_path)
    doc = _doc_dict()
    doc["blocks"] = [
        {"block_id": f"b{i}", "role": "finding", "heading": "x"} for i in range(MAX_SECTIONS + 1)
    ]
    assert not tool.validate_input({"document": doc}).valid


def test_v2_chart_option_size_cap(tmp_path: Path):
    tool = _tool(tmp_path)
    huge = {"series": [{"data": "A" * (MAX_OPTION_CHARS + 1)}]}
    res = tool.validate_input({"document": _doc_dict(), "charts": {"c": huge}})
    assert not res.valid
    assert "too large" in res.error


def test_v2_chart_option_non_serializable_rejected(tmp_path: Path):
    tool = _tool(tmp_path)
    res = tool.validate_input({"document": _doc_dict(), "charts": {"c": {"bad": {1, 2, 3}}}})
    assert not res.valid
    assert "JSON-serializable" in res.error


def test_v2_malformed_block_role_fails_validation(tmp_path: Path):
    tool = _tool(tmp_path)
    doc = _doc_dict()
    doc["blocks"] = [{"block_id": "x", "role": "bogus_role", "heading": "x"}]
    res = tool.validate_input({"document": doc})
    assert not res.valid


def test_v2_blocks_non_dict_items_rejected(tmp_path: Path):
    """blocks 含非 dict 项(如字符串)→ 校验阶段拒绝(fail-fast,不待 render 崩)。"""
    tool = _tool(tmp_path)
    doc = _doc_dict()
    doc["blocks"] = ["not-a-dict"]
    res = tool.validate_input({"document": doc})
    assert not res.valid


def test_v2_chart_option_script_injection_blocked(tmp_path: Path):
    """chart option 含 </script> 与 U+2028 → 不得逃逸 <script> 块。"""
    tool = _tool(tmp_path)
    evil_option = {"title": "</script><script>alert(1)</script>", "u": " "}
    page = tool._render_v2_page(_doc_dict(), {"c": evil_option})
    assert "<script>alert(1)</script>" not in page
    assert "<\\/script>" in page  # option 内的 </ 已转义


def test_v2_traceability_attrs_escape_quote(tmp_path: Path):
    """data-block-id 含引号 → 属性值转义,不得逃逸。"""
    tool = _tool(tmp_path)
    doc = _doc_dict(
        blocks=(
            ReportBlock(
                block_id='x"><img src=x onerror=alert(1)>',
                role=BlockRole.FINDING,
                body="ok",
                evidence_refs=('a"b',),
            ),
        ),
    )
    page = tool._render_v2_page(doc, {})
    assert "<img src=x onerror=alert(1)>" not in page
    # 尖括号已转义 → 不构成元素;引号已转义 → 无法突破属性边界
    assert "<img " not in page
    assert '"><img' not in page
    assert "&quot;" in page  # block_id 里的引号被转义
