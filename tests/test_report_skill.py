"""Wave 3 ReportGenerationSkill: 路由 + 隔离 + allowed_tools + instructions + contract→QA 集成。"""

from __future__ import annotations

from data_analysis_agent.reporting.contract import ReportContract, ReportDocument
from data_analysis_agent.reporting.qa import run_qa
from data_analysis_agent.runtime import build_skill_registry
from data_analysis_agent.skills.builtin import ReportGenerationSkill
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
            ],
            "n_rows_sampled": 100,
            "sampled": True,
        }
    ],
}


def test_report_skill_keywords_route():
    reg = build_skill_registry()
    queries = [
        "上周销售日报",
        "本周周报",
        "做个销售复盘",
        "分析注册到付费的漏斗",
        "检测支付异常",
        "看看这批数据的数据质量",
    ]
    for q in queries:
        skill = reg.match_best(q)
        assert skill is not None and skill.name == "report_generation", (
            f"{q!r} 应路由到 report_generation,实际 {getattr(skill, 'name', None)}"
        )


def test_report_skill_routing_isolation():
    """非报告分析不被强制走报告技能(spec §8 Wave 3 #4)。"""
    reg = build_skill_registry()
    # 描述性/趋势查询不应被报告技能抢
    assert reg.match_best("描述性统计与数据分布").name != "report_generation"
    assert reg.match_best("趋势 时间序列 季节性 预测").name != "report_generation"
    # 正向:路由到对应技能
    assert reg.match_best("描述性统计 数据概览 分布").name == "descriptive_analysis"
    assert reg.match_best("趋势分析 时间序列 预测").name == "trend_analysis"


def test_report_skill_allowed_tools():
    tools = set(ReportGenerationSkill().allowed_tools)
    assert {
        "report_need",
        "report_context",
        "report_contract",
        "data_profile",
        "python_analysis",
        "visualization",  # spec §5.2:可用但不默认
        "html_report",
    } <= tools


def test_report_skill_routing_tie_breaks_by_registration_order():
    """周报趋势:Report(周报 +4)与 Trend(趋势 +4)平局 → 注册序在前者胜
    (Trend 先注册于 build_skill_registry)。钉住行为,防止未来重排序静默漂移。"""
    reg = build_skill_registry()
    skill = reg.match_best("周报趋势")
    assert skill is not None
    assert skill.name == "trend_analysis"


def test_report_skill_instructions_contract_first():
    instr = ReportGenerationSkill().instructions
    assert "report_contract" in instr
    assert "html_report" in instr
    assert instr.index("report_contract") < instr.index("html_report")
    # 显式 vs 隐式区分措辞(spec §5.3)
    assert "EXPLICIT" in instr or "明示" in instr


async def test_contract_to_qa_integration():
    """report_need→context→contract 产出的 contract 经 QA 不被判断链(spec §8 Wave 3 #1/#2)。"""
    need = await ReportNeedTool().call({"raw_request": "上周销售日报,给领导看"})
    ctx = await ReportContextTool().call({"profile": _PROFILE})
    contract_result = await ReportContractTool().call(
        {
            "question": "上周销售日报",
            "user_need": need.metadata["user_need"],
            "data_context": ctx.metadata["data_context"],
        }
    )
    contract = ReportContract.from_dict(contract_result.metadata["contract"])
    doc = ReportDocument(title="销售日报", contract=contract, data_scope="sales.csv,上周,100 行")
    qa = run_qa(doc, artifact_exists=False)
    # 收紧断言:仅检断链(其他 blocker 如 artifact.missing 仍会触发,属正常)
    assert "contract.no_traceability" not in {f.code for f in qa.findings}
