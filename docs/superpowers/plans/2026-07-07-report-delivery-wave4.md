# 报告交付优化 · Wave 4 实现计划 — html_report v2 Report Document 分支

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 Task 实现。

> **Baseline:** `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`(§5.2 末条、§8 Wave 4、§4.8 ReportDocument)
> **Depends on:** Wave 1-2(`reporting/` 领域层 + QA),commit `93d487a`
> **Scope:** Wave 4。给 `html_report` 加 **v2 Report Document 分支**(叠加式,v1 路径不动),按 block role 渲染 + QA readiness 徽章 + traceability data 属性。**这是迄今最险的 wave**:触及现有 v1 渲染器(审计 HTML 安全 4.0/5),必须保 v1 零回归 + 复用既有逃逸/路径防护。

## Goal

让 `html_report` 能消费 `ReportDocument`(Wave 1-2 契约层),按 role 渲染出业务可读结构(执行摘要 / KPI strip / 发现 / 图表 / 表 / 推荐 / caveat / 来源 / QA 状态),而非 v1 的扁平 sections。**v1 调用方零影响**(spec §8 Wave 4 acceptance: v1 tests keep passing)。

spec §8 Wave 4 acceptance:

- v1 测试全绿。
- v2 fixture 渲染期望 section + 转义文本。
- 有 blocker QA 的报告能渲染为 draft 但**显眼标注 draft**。

## Architecture

**叠加分支**:在 `call` / `validate_input` / `_render_page` 检测 `document` 键 → v2 路径;否则走 v1(现有代码逐字不动)。

v2 输入 shape:

```json
{
  "document": <ReportDocument dict(Wave 1-2 to_dict 形态)>,
  "charts": {"<block_id>": <ECharts option dict>},   // 可选:CHART 块的实际图表数据
  "file_name": "..."                                  // 可选,沿用 v1 bare-name 规则
}
```

- `document` 必填(dict);`charts` 可选(CHART 块若无对应 option → 渲染 ChartSpec 元数据占位:family/interpretation/caption,Wave 5 接真正的 chart_render)。
- 复用 v1 的 `_escape_json_for_script` / `_text_to_html` / `_render_table` / `_echarts_tag` / 路径防护(`validate_input` bare-name + `call` is_relative_to 重检)。

渲染流程:

1. `ReportDocument.from_dict(document)` + 可选 `run_qa(document, artifact_exists=True)` → readiness(draft/needs_review/ready)。
2. 按 `document.blocks` 的 role 依次渲染:
   - `HEADER` → header(标题 + 可选 period/generated/data_scope)
   - `EXECUTIVE_SUMMARY` → 绿边 summary 卡(同 v1 .summary 样式)
   - `KPI_STRIP` → KPI 卡行(从 `kpi_cards` tuples 渲染,每张卡 label/value/delta/status)
   - `DATA_CONTEXT` → 紧凑卡
   - `FINDING` → 卡(heading + body + 可选 chart/table + 内嵌 caveats)
   - `CHART` → 图表卡(option via `charts[block_id]`,或 ChartSpec 元数据占位)+ interpretation
   - `TABLE` → 表卡(复用 `_render_table`,从 `table_columns`/`table_rows`)
   - `RECOMMENDATION` → 推荐卡(action/owner/effect)
   - `CAVEAT` → 警示卡(橙边)
   - `SOURCE_METADATA` → 附录卡
3. **QA 徽章**:页顶 readiness badge(draft=红 / needs_review=橙 / ready=绿)+ findings 计数;draft/needs_review 时加显眼 banner。
4. **traceability**:每个卡 `data-block-id` / `data-evidence-refs` / `data-user-need-refs` 属性(Web 检查用,读者不可见)。
5. **print CSS**:`@media print` 规则(去掉阴影、适配纸张)。

## Tech Stack

复用 v1 stdlib(`html`/`json`/`string.Template`/`pathlib`/`datetime`)+ Wave 1-2 `reporting`(`ReportDocument`/`run_qa`)。无新依赖。`tools` → `reporting` 已合法(drift,Wave 3 验证过)。

## Global Constraints

- **质量闸**:每 Task 末 `.venv/bin/python scripts/quality_gate.py` 全绿。
- **v1 零回归**:**不修改** v1 的 `_PAGE`/`_render_section`/`_render_table`/`_validate_chart`/`_validate_table`/`validate_input`(v1 路径)/ `call`(v1 路径)任何现有行。v2 走独立方法 + 在入口处分流。既有 `tests/test_html_report.py` 必须逐字通过。
- **逃逸**:v2 所有文本(KPI value、heading、body、caption、caveat、推荐)经 `html.escape`;chart option 经 `_escape_json_for_script`。**不得**把任何 ReportDocument 字段不经转义插入 HTML。
- **路径防护**:v2 沿用 v1 的 `file_name` bare-name 校验 + `call` 中 `is_relative_to(artifact_dir)` 重检。
- **确定性**:`generated_at` 沿用 v1 `datetime.now(timezone.utc)`(v1 已如此);v2 不引入额外非确定性。
- **manifest**:`html_report.py` 已登记,不新增模块(本 wave 只改它 + 测试)。
- **提交**:Wave 4 收尾统一 commit(见末 Task)。

## File Structure

| 文件                                           | 责任                                                                                                 | 动作             |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ---------------- |
| `src/data_analysis_agent/tools/html_report.py` | 加 v2 分支:`_is_v2`/`_validate_v2`/`_render_v2_page`/role 渲染器/QA 徽章/traceability 属性/print CSS | 改(叠加,不动 v1) |
| `tests/test_html_report_v2.py`                 | v2 fixture(role 渲染 + 转义 + QA 徽章 + draft banner + traceability + v1 fallback)                   | 新建             |

**回滚**:仅改 `html_report.py`(叠加分支)+ 新增测试。`git revert` 即可恢复 v1。

---

## Task 1: v2 入口分流 + 输入校验

**Files:** Modify `html_report.py`; New `tests/test_html_report_v2.py`。

**Interfaces:**

- `_is_v2(input_data) -> bool`:`"document" in input_data and isinstance(input_data["document"], dict)`。
- `validate_input` 首行:`if self._is_v2(input_data): return self._validate_v2(input_data)`;否则现有 v1 校验不动。
- `_validate_v2(input_data)`:`document` 是 dict;`document.title` 非空 str;`document.blocks`(若有)是 list;`charts`(若有)是 dict 且每个 value 是 dict;`file_name` 沿用 v1 bare-name 规则(抽公共方法 `_validate_file_name`,v1/v2 共用)。
- `call` 首行:`if self._is_v2(input_data): return await self._call_v2(input_data)`;否则 v1。

- [ ] Step 1: 写失败测试:`test_v1_unchanged_by_v2_input_keys`(v1 输入无 `document` 键→走 v1)、`test_v2_requires_document_dict`、`test_v2_file_name_bare_name_rule`。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `_is_v2`/`_validate_v2`(抽 `_validate_file_name` 共用,v1 改为调它,**行为不变**)。`call` 分流(此 Task 先让 `_call_v2` raise NotImplementedError,下一 Task 实现)。
- [ ] Step 4: gate → PASS(v1 全绿 + v2 校验测试过)。

## Task 2: v2 渲染 — role blocks + 转义

**Files:** Modify `html_report.py`; 追加测试。

**Interfaces:**

- `_render_v2_page(document_dict, charts) -> str`:用 v1 `_PAGE` 模板(标题来自 document.title),body 由 `_render_v2_blocks` 产。
- `_render_v2_blocks(document, charts, render_calls) -> str`:按 block.role 分发:
  - HEADER → header 段(标题已在 _PAGE header;HEADER block 的 heading/body 作为副标题/period)
  - EXECUTIVE_SUMMARY → `<section class="card summary">…</section>`
  - KPI_STRIP → `<section class="card kpi-strip">` + 每个 kpi_card → `<div class="kpi"><span class="kpi-label">…</span><span class="kpi-value">…</span>…</div>`
  - FINDING → `<section class="card finding">` + heading + body(`_text_to_html`)+ 内嵌 caveats + 可选 chart/table(若 block 带_chart/_table——v2 FINDING 一般不含 chart,CHART 独立 block)
  - CHART → `<section class="card chart-block">`:`charts.get(block_id)` 有则 ECharts(复用 `_render_section` 的 chart 段逻辑,抽 `_render_chart_html(chart_id, option, height, caption, render_calls)`)+ interpretation;无则占位(family/interpretation/caption)
  - TABLE → `<section class="card">` + `_render_table({"columns": block.table_columns, "rows": block.table_rows})`
  - RECOMMENDATION / CAVEAT / DATA_CONTEXT / SOURCE_METADATA → 各自卡(CAVEAT 橙边)
- 所有文本字段 `html.escape`;chart option `_escape_json_for_script`。

- [ ] Step 1: 写失败测试:`test_v2_renders_roles`(给定含 HEADER/EXECUTIVE_SUMMARY/KPI_STRIP/FINDING/CHART/TABLE/RECOMMENDATION/CAVEAT/SOURCE_METADATA 的 document dict → 输出含各 role 的 HTML 锚点:class="kpi-strip"/class="finding"/class="chart-block"/class="caveat" 等)、`test_v2_escapes_text`(title/body/heading 含 `<script>` → 转义)、`test_v2_chart_from_options_map`(CHART block_id 在 charts map → render call 出现)、`test_v2_chart_placeholder_when_no_option`(无 option → 占位含 ChartSpec.interpretation)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `_render_v2_page`/`_render_v2_blocks` + 各 role 渲染器。抽 `_render_chart_html` 供 v1/v2 共用(v1 `_render_section` 改为调它,**行为不变**)。
- [ ] Step 4: gate → PASS。

## Task 3: QA readiness 徽章 + draft banner + traceability + print CSS

**Files:** Modify `html_report.py`; 追加测试。

**Interfaces:**

- `_render_v2_page` 顶部插 readiness badge:`run_qa(ReportDocument.from_dict(document), artifact_exists=True)` → readiness + findings 计数;badge HTML(class="qa-badge qa-draft/qa-needs-review/qa-ready")。draft/needs_review 时加 `<div class="qa-banner">⚠ …</div>`。
- 每个卡加 `data-block-id`/`data-evidence-refs`(join `,`)/`data-user-need-refs`。
- `<style>` 加 `@media print` 规则 + KPI/caveat/badge 样式(扩展 `_PAGE` 的 `<style>`,**v1 样式不动**,只追加新规则)。

- [ ] Step 1: 写失败测试:`test_v2_qa_badge_ready`(干净 document → class="qa-ready")、`test_v2_qa_badge_draft_with_blocker`(缺 data_scope → class="qa-draft" + qa-banner)、`test_v2_traceability_data_attrs`(FINDING block 的卡含 data-block-id/data-evidence-refs)、`test_v2_print_css`(@media print 在输出中)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 badge/banner/traceability/print CSS。
- [ ] Step 4: gate → PASS。

## Task 4: 最终闸 + 独立审查 + commit

- [ ] Step 1: 最终 `quality_gate` 全绿(ruff/format/mypy/pytest/drift);`tests/test_html_report.py`(v1)逐字通过。
- [ ] Step 2: 独立代码审查闭环(§2.9):spawn 全新只读 reviewer,重点 **v1 零回归**(对比 v1 测试输出)、**逃逸**(v2 所有字段转义)、**路径防护**(v2 file_name + is_relative_to)、QA 徽章正确性、traceability 不泄漏敏感、确定性。修复 → 复审至零遗留。
- [ ] Step 3: Commit。

## Verification

- v1 测试(`tests/test_html_report.py`)逐字通过 —— **硬门**。
- v2 fixture 渲染期望 role + 转义 + QA 徽章 + draft banner。
- drift 绿(tools→reporting 已合法)。

## 不在本计划内

- Wave 5:结构化 `chart_render` 工具(本 wave 的 chart 占位由它替换)。
- Wave 6/7/8:模板 / eval gate / Web UX。

## Self-Review(独立计划评审 APPROVE-WITH-FIXES,7 条全采纳)

1. **v2 用独立 `_PAGE_V2` 模板**(独立 `<style>`),v1 `_PAGE` 逐字不动 → v1 HTML 输出不变。
2. **不抽 v1 代码**:不抽 `_validate_file_name` / `_render_chart_html`;v2 自带 file_name 校验(少量重复)与 chart 渲染(复用模块级 `_escape_json_for_script` / `_text_to_html` / `_render_table` 这些已存在的模块级/静态方法,不改它们)。v1 `validate_input`/`call`/`_render_section` 逐字不动。
3. **v1 字节级 golden 测试**:`tests/test_html_report_v2.py` 加 `test_v1_output_byte_identical` —— 渲染 `_BASE_INPUT`(子串截掉 `生成时间` 行的非确定部分),SHA256 钉为常量。v1 代码任何静默漂移 → hash 变 → fail。
4. **逃逸枚举闭合 + 含属性值**:`document.title`、所有 block heading/body/caveats、KPI 各 (key,value)、ChartSpec subtitle/caption/interpretation/units、**以及 `data-block-id`/`data-evidence-refs`/`data-user-need-refs` 属性值** 全部 `html.escape(...)`;属性值用 `html.escape(..., quote=True)`。加属性值 XSS 测试(`evidence_refs=['"<img src=x onerror=alert(1)>']` → 不得逃逸属性)。
5. **KPI 渲染算法**:逐 (key,value) pair 渲染为 `<div class="kpi-pair"><span class="kpi-k">key</span><span class="kpi-v">value</span></div>`(匹配 `kpi_cards: tuple[tuple[tuple[str,str],...],...]` 类型,无固定 schema)。
6. **CHART `chart_id`**:v2 用 `f"chart_{block_id}"`(block_id 已唯一,避免与 v1 `chart_N` 命名空间碰撞,render_calls 可溯源到 block)。
7. **Banner 静态模板**:`"readiness={state.value} · {n_blocker} blocker / {n_high} high findings"`,仅 state + 整数计数,无模型控制字符串插值。
8. **分流边界测试**:input 同时含 v1 键(title/sections)与 `document` 键 → 走 v2(`_is_v2` 看 `document` 键存在)。
