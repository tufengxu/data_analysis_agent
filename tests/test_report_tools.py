"""Wave 3 报告工具: call 行为 + 只读 + 注册 + plan 模式 + 契约溯源/missing_context 闭环。"""

from __future__ import annotations

from data_analysis_agent.reporting.contract import ReportContract, ReportDocument
from data_analysis_agent.reporting.model import SourceKind
from data_analysis_agent.reporting.qa import run_qa
from data_analysis_agent.runtime import build_registry
from data_analysis_agent.tools.report_context import ReportContextTool
from data_analysis_agent.tools.report_contract import ReportContractTool
from data_analysis_agent.tools.report_need import ReportNeedTool

_PROFILE = {
    "kind": "file",
    "path": "/data/sales.csv",
    "format": "csv",
    "tables": [
        {
            "columns": [
                {"name": "order_date", "dtype": "datetime64"},
                {"name": "amount", "dtype": "float64"},
                {"name": "channel", "dtype": "object"},
            ],
            "n_rows_sampled": 100,
            "sampled": True,
        }
    ],
}


def test_report_need_read_only_and_validation():
    tool = ReportNeedTool()
    assert tool.is_read_only({})
    assert not tool.is_destructive({})
    assert tool.is_concurrency_safe({})
    assert tool.validate_input({}).valid is False
    assert tool.validate_input({"raw_request": "  "}).valid is False
    assert tool.validate_input({"raw_request": "日报"}).valid is True


async def test_report_need_parses():
    tool = ReportNeedTool()
    result = await tool.call({"raw_request": "上周销售日报,给领导看"})
    un = result.metadata["user_need"]
    assert un["implicit_requirements"]["likely_report_type"] == "daily_kpi"
    assert un["explicit_requirements"]["audience"] == "business_stakeholder"
    assert "daily_kpi" in result.content


def test_report_context_read_only_and_validation():
    tool = ReportContextTool()
    assert tool.is_read_only({})
    assert not tool.is_destructive({})
    assert tool.is_concurrency_safe({})
    assert tool.validate_input({}).valid is False
    assert tool.validate_input({"profile": "not-a-dict"}).valid is False


async def test_report_context_builds():
    tool = ReportContextTool()
    result = await tool.call({"profile": _PROFILE})
    dc = result.metadata["data_context"]
    assert "order_date" in dc["candidate_date_columns"]
    assert "amount" in dc["candidate_metric_columns"]
    assert result.metadata["process_context"]["steps"] == []


async def test_report_context_sensitive_mode_drops_steps():
    tool = ReportContextTool()
    result = await tool.call(
        {
            "profile": _PROFILE,
            "events": [{"step_id": "s1", "tool": "python_analysis", "summary": "agg"}],
            "sensitive_mode": True,
        }
    )
    pc = result.metadata["process_context"]
    assert pc["sensitive_mode"] is True
    assert pc["steps"] == []


def test_report_contract_read_only_and_validation():
    tool = ReportContractTool()
    assert tool.is_read_only({})
    assert not tool.is_destructive({})
    assert tool.is_concurrency_safe({})
    assert tool.validate_input({}).valid is False
    assert tool.validate_input({"question": "  "}).valid is False


async def test_report_contract_traceability():
    need_result = await ReportNeedTool().call({"raw_request": "上周销售日报"})
    ctx_result = await ReportContextTool().call({"profile": _PROFILE})
    result = await ReportContractTool().call(
        {
            "question": "上周销售日报",
            "user_need": need_result.metadata["user_need"],
            "data_context": ctx_result.metadata["data_context"],
        }
    )
    contract = result.metadata["contract"]
    assert len(contract["field_sources"]) > 0
    refs = (
        contract["explicit_requirement_refs"],
        contract["implicit_requirement_refs"],
        contract["data_context_refs"],
        contract["process_context_refs"],
    )
    assert any(refs), "至少一类 ref 非空(否则 QA 会断链)"
    # 闭环:report_contract 产物经 run_qa 不触发 contract.no_traceability
    doc = ReportDocument(
        title="x", contract=ReportContract.from_dict(contract), data_scope="sales.csv"
    )
    qa = run_qa(doc, artifact_exists=True)
    assert "contract.no_traceability" not in {f.code for f in qa.findings}


async def test_report_contract_missing_context():
    need_result = await ReportNeedTool().call({"raw_request": "销售日报"})
    result = await ReportContractTool().call(
        {"question": "销售日报", "user_need": need_result.metadata["user_need"]}
    )
    missing = set(result.metadata["contract"]["missing_context"])
    assert missing, "无时间/对比词 → missing_context 非空"
    assert "time_window" in missing or "comparison" in missing


async def test_report_contract_report_type_override():
    result = await ReportContractTool().call({"question": "x", "report_type": "funnel"})
    assert result.metadata["contract"]["report_type"] == "funnel"


async def test_report_contract_invalid_report_type_falls_back():
    result = await ReportContractTool().call({"question": "x", "report_type": "bogus"})
    assert result.metadata["contract"]["report_type"] == "ad_hoc"


def test_report_tools_registered():
    registry = build_registry()
    names = {t.name for t in registry.get_tools("default")}
    assert {"report_need", "report_context", "report_contract"} <= names


def test_report_tools_available_in_plan_mode():
    registry = build_registry()
    plan_names = {t.name for t in registry.get_tools("plan")}
    assert {"report_need", "report_context", "report_contract"} <= plan_names
    assert "html_report" not in plan_names  # 仍被拒


def test_report_context_rejects_non_dict_profile():
    tool = ReportContextTool()
    assert not tool.validate_input({"profile": []}).valid
    assert not tool.validate_input({"profile": "x"}).valid
    assert not tool.validate_input({"profile": 123}).valid


async def test_report_contract_malformed_user_need_falls_back():
    # 残缺 user_need dict(缺 explicit/implicit_requirements)→ 回退到 parse_user_need,
    # 不抛 TypeError(评审 High)
    result = await ReportContractTool().call(
        {"question": "上周销售日报", "user_need": {"raw_request": "日报"}}
    )
    contract = result.metadata["contract"]
    assert contract["report_type"] == "daily_kpi"  # 从 question 重新解析


async def test_report_contract_audience_override():
    result = await ReportContractTool().call({"question": "x", "audience": "technical"})
    assert result.metadata["contract"]["audience"] == "technical"


async def test_report_contract_invalid_audience_falls_back():
    result = await ReportContractTool().call({"question": "x", "audience": "bogus"})
    assert result.metadata["contract"]["audience"] == "business_stakeholder"


async def test_report_contract_field_sources_roundtrip():
    need = await ReportNeedTool().call({"raw_request": "上周销售日报"})
    ctx = await ReportContextTool().call({"profile": _PROFILE})
    result = await ReportContractTool().call(
        {
            "question": "上周销售日报",
            "user_need": need.metadata["user_need"],
            "data_context": ctx.metadata["data_context"],
        }
    )
    contract = ReportContract.from_dict(result.metadata["contract"])
    # field_sources 嵌套 tuple[tuple[str, SourceKind], ...] 往返重建为 Enum(评审覆盖缺口)
    assert len(contract.field_sources) > 0
    assert all(isinstance(fs[1], SourceKind) for fs in contract.field_sources)
    assert all(isinstance(fs[0], str) for fs in contract.field_sources)


async def test_report_contract_populates_dimensions_and_grain():
    ctx = await ReportContextTool().call({"profile": _PROFILE})
    result = await ReportContractTool().call(
        {"question": "x", "data_context": ctx.metadata["data_context"]}
    )
    contract = result.metadata["contract"]
    assert contract["business_grain"] == "order"
    assert "channel" in contract["dimensions"]  # object 列归 dimension
