# 报告交付优化 · Wave 6 实现计划 — 报告模板(数据 + 确定性选择器)

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 Task 实现。

> **Baseline:** `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`(§5.3 模板为数据、§6 报告模板、§8 Wave 6)
> **Depends on:** Wave 1-2(`reporting/contract.py` ReportType/BlockRole/ChartFamily、`requirement_parser.py`),commit `93d487a`
> **Scope:** Wave 6。新增 `reporting/templates.py`:8 个报告类型的 curated 模板(section-role spine + 默认图族 + 必备 caveat)+ 确定性选择器。**纯数据 + 纯函数**,无渲染、无工具注册、无 I/O。低风险。

## Goal

让"相似报告请求 → 一致的 section-role 骨架 + 图表默认 + 必备 caveat"成为可测、确定性的数据(spec §8 Wave 6 acceptance):

- 8 个 ReportType 各有 curated 模板(daily_kpi/weekly_kpi/diagnostic/recommendation/data_quality/funnel/cohort/risk_anomaly)。
- `select_template(report_type)` 与 `match_template(text)` 确定性选择,无 LLM。
- 模板输出 = section-role spine + 图表默认 + 必备 caveat 主题,**非最终散文**(spec §5.3/§8)。
- 角色 traceable 到推断/显式用户需求(经 requirement_parser 的报告类型检测)。

## Architecture

`reporting/templates.py`(纯 stdlib,依赖本包 `contract`/`requirement_parser`):

```python
@dataclass(frozen=True)
class ReportTemplate(Serializable):
    report_type: ReportType
    name: str                       # 展示名
    section_roles: tuple[BlockRole, ...]   # role spine(顺序即阅读顺序)
    default_chart_families: tuple[ChartFamily, ...]
    required_caveats: tuple[str, ...]      # caveat 主题(如 "partial_period")
    description: str

TEMPLATES: dict[ReportType, ReportTemplate]   # 8 个 curated
select_template(report_type: ReportType | str) -> ReportTemplate | None
match_template(text: str) -> ReportTemplate | None   # 经 parse_user_need 的报告类型检测
```

模板内容(对齐 spec §6.1-§6.6):

- **daily_kpi**:roles=HEADER/EXECUTIVE_SUMMARY/KPI_STRIP/FINDING(drivers)/FINDING(risks)/RECOMMENDATION(next-actions)/CAVEAT;charts=KPI_CARD/LINE/GROUPED_BAR/BAR;caveats=partial_period/missing_data
- **weekly_kpi**:EXECUTIVE_SUMMARY/KPI_STRIP/FINDING(wow)/FINDING(segment)/RECOMMENDATION/CAVEAT;charts=KPI_CARD/GROUPED_BAR/BAR/TABLE;caveats=partial_period
- **diagnostic**:EXECUTIVE_SUMMARY(what-changed)/FINDING(drivers)/FINDING(rejected)/FINDING(segment)/RECOMMENDATION/CAVEAT;charts=GROUPED_BAR/BAR/TABLE;caveats=causal_limitation
- **data_quality**:EXECUTIVE_SUMMARY(suitability)/DATA_CONTEXT/FINDING(missingness)/FINDING(duplicates)/FINDING(types)/FINDING(outliers)/FINDING(join-risks)/RECOMMENDATION(cleanup);charts=BAR/TABLE;caveats=[]
- **funnel**:EXECUTIVE_SUMMARY/DATA_CONTEXT(definition)/FINDING(drop-off)/FINDING(segment)/FINDING(bottleneck)/RECOMMENDATION;charts=FUNNEL/HEATMAP/LINE;caveats=denominator
- **cohort**:EXECUTIVE_SUMMARY/DATA_CONTEXT(definition)/FINDING(retention)/FINDING(segment)/RECOMMENDATION;charts=HEATMAP/LINE;caveats=small_sample
- **risk_anomaly**:EXECUTIVE_SUMMARY/FINDING(rule)/FINDING(population)/FINDING(concentration)/CAVEAT(false-positive)/RECOMMENDATION;charts=BAR/SCATTER/TABLE;caveats=false_positive
- **recommendation**:EXECUTIVE_SUMMARY/FINDING(options)/FINDING(expected-impact)/RECOMMENDATION;charts=BAR;caveats=[]

注:funnel/cohort 的 FUNNEL/HEATMAP 图族 chart_render 暂不支持(Wave 5 defer),但模板列出的是"期望图族"指南(模型可用 visualization 自定义或待图族补齐),非 chart_render 当前能力约束。

## Tech Stack

复用 Wave 1-2 `reporting.contract`(ReportType/BlockRole/ChartFamily)+ `requirement_parser.parse_user_need`。无新依赖。

## Global Constraints

- **质量闸**:每 Task 末全绿。
- **manifest**:新增 `reporting/templates.py` 一行。
- **drift**:templates 在 reporting 包内,仅依赖本包 + stdlib(合规)。
- **确定性**:TEMPLATES 是模块级常量;select/match 无随机/时间/LLM。
- ** Serializable**:ReportTemplate 继承 `model.Serializable`(Enum `.value` 往返)。
- **本 Wave 不改**:tools/runtime/skills/html_report(模板是数据,消费方后续接入)。

## File Structure

| 文件                                             | 责任                                                         | 动作 |
| ------------------------------------------------ | ------------------------------------------------------------ | ---- |
| `src/data_analysis_agent/reporting/templates.py` | ReportTemplate + 8 模板 + select/match                       | 新建 |
| `docs/ARCHITECTURE.md`                           | manifest 加 1 行                                             | 改   |
| `tests/test_reporting_templates.py`              | 8 模板存在 + role spine 对齐 §6 + select/match 确定性 + 往返 | 新建 |

**回滚**:全增量。`git revert` 即可。

---

## Task 1: ReportTemplate + 8 模板 + 选择器 + 测试

**Files:** New `reporting/templates.py`; New `tests/test_reporting_templates.py`; Modify manifest。

- [ ] Step 1: 写失败测试:
  - `test_all_report_types_have_template`:(ad_hoc 除外)每个 ReportType 在 TEMPLATES。
  - `test_daily_kpi_spine`:daily_kpi.section_roles 含 EXECUTIVE_SUMMARY/KPI_STRIP/RECOMMENDATION。
  - `test_each_template_starts_with_executive_summary`(业务受众先给结论)。
  - `test_select_template`:select_template(ReportType.DAILY_KPI) → name 含 "daily";select_template("weekly_kpi") 命中;select_template("ad_hoc")/bogus → None。
  - `test_match_template_from_text`:match_template("上周销售日报") → daily_kpi;match_template("做个复盘") → diagnostic;match_template("检测异常") → risk_anomaly;match_template("看看数据质量") → data_quality。
  - `test_template_roundtrip`:ReportTemplate Serializable 往返。
  - `test_required_caveats`:daily_kpi 含 partial_period;risk_anomaly 含 false_positive。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `templates.py`(ReportTemplate + TEMPLATES + select/match)。
- [ ] Step 4: manifest + gate → PASS。

## Task 2: 最终闸 + 独立审查 + commit

- [ ] Step 1: 最终 `quality_gate` 全绿。
- [ ] Step 2: 独立代码审查闭环(§2.9):spawn 全新只读 reviewer,重点:模板与 spec §6 对齐、role spine 合理、确定性、选择器正确、required_caveats 合理、Serializable 往返。修复 → 复审至零遗留。
- [ ] Step 3: Commit。

## 不在本计划内

- 域 overlay(retail/SaaS/support/finance/operations/risk/supply chain;spec §8 Wave 6 可选,延后)。
- 模板被 skill/planner 消费(后续 Wave 接入;本 Wave 只交付数据 + 选择器)。**§8 Wave 6 acceptance #4("skills use templates but can still adapt")由本 Wave 的数据+选择器基座满足**;skill/planner 消费延后并在此追踪。
- Wave 7:eval gate;Wave 8:Web UX(阻塞)。

## Self-Review(独立计划评审 APPROVE-WITH-FIXES,6 条全采纳)

1. **HEADER 矛盾(采纳 path 2)**:**全部 8 模板以 HEADER 开头 + EXECUTIVE_SUMMARY 存在**(§4.8 header 是必备 role,§6.1 period-aware title)。测试改存在性:`EXECUTIVE_SUMMARY in section_roles`(非"首个")。
2. **match_template None 守卫**:`likely_report_type is None → return None`(在 `ReportType(...)` 之前)。加测试 `match_template("分析下这份数据") → None`。
3. **§6 role gap 在 description 注明 fold**:weekly_kpi CAVEAT 吸收 open-questions;cohort description 注 bottleneck 是 funnel 特性故省;diagnostic RECOMMENDATION 吸收 next-investigation。
4. **3 个不变量测试**:每个模板 section_roles 非空;default_chart_families 全为 ChartFamily 成员;match_template 无关键词文本返 None。
5. **recommendation 模板出处**:`templates.py` 注释"constructed from ReportType.RECOMMENDATION;spec §6 无对应 archetype"。
