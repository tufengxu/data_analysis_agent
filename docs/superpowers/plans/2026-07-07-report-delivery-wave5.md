# 报告交付优化 · Wave 5 实现计划 — 结构化 chart_render 工具

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 Task 实现。

> **Baseline:** `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`(§4.7 ChartSpec、§5.2 chart_render、§8 Wave 5、§12 推荐默认 ECharts-first)
> **Depends on:** Wave 1-2(`reporting/chart_rules.py` + `contract.ChartFamily`/`ChartSpec`),commit `93d487a`;Wave 4(html_report v2 消费 `charts:{block_id:option}`),commit `686863f`
> **Scope:** Wave 5。新增 `chart_render` 只读+写产物工具:消费结构化 ChartSpec + 数据 → 按图族生成 ECharts option + 数据充分性检查 + 落盘 artifact + 返回 chart metadata。**取代"模型手写 ECharts option"的默认路径**(spec §5.2)。

## Goal

让"结构化图表请求 → 图表产物"无需模型手写 ECharts option(spec §8 Wave 5 acceptance):

- chart_render 接 ChartSpec 字段 + 数据 → 生成 ECharts option(按图族)+ 写 artifact(JSON)。
- 数据充分性检查(趋势点数 / 散点观测数)经 `chart_rules`,结果进 metadata。
- 返回 chart metadata(family/data_sufficient/n_points/evidence 钩子)供 Report QA 与 Evidence Map 消费。
- 既有 `visualization`(自由 Python 图表代码)仍可用但非默认(spec §5.2)。

## Architecture

新工具 `tools/chart_render.py`(只读计算 + 写产物到 artifact_dir):

| 输入                                                                                                                                  | 调用                                                                                                                                                          | 输出                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `block_id`、`family`、`data`、`fields?`、`title/subtitle/caption/interpretation?`、`units?`、`x_axis_name/y_axis_name?`、`file_name?` | 按图族生成 ECharts option(line/bar/grouped_bar/stacked_bar/scatter);`chart_rules.check_data_sufficiency` 判充分性;option 写 `<block_id>.json` 到 artifact_dir | `metadata={"chart_option": <option dict>, "artifact_paths": [<json>], "chart_meta": {family, data_sufficient, reason, n_points/n_observations, block_id, evidence_refs?}}` |

**图族 → ECharts option 生成**(本 Wave 覆盖报告最常用的 5 族,spec §6 模板所需):

- `line`:`{xAxis:{type:category,data:labels}, yAxis:{type:value}, series:[{name,type:"line",data:values}]}`(多 series 多线)
- `bar`:单 series type "bar"
- `grouped_bar`:多 series type "bar"(并列)
- `stacked_bar`:多 series type "bar" + `stack:"total"`
- `scatter`:`{xAxis:{type:value}, yAxis:{type:value}, series:[{type:"scatter", data:points}]}`

**defer 图族**(理由记录,非实现):`heatmap`/`waterfall`/`funnel` 报告中较少且 option 复杂,延后;`kpi_card`/`table` 是 ReportDocument 的 block role(非 chart_render 职责,由 html_report v2 直接渲染)。

**数据 shape**(统一):

```json
{
  "labels": ["A", "B", "C"],
  "series": [{ "name": "GMV", "values": [10, 20, 30] }],
  "points": [
    [1, 2],
    [3, 4]
  ],
  "x_axis_name": "...",
  "y_axis_name": "..."
}
```

**与 html_report v2 的衔接**:chart_render 返回 `chart_option`(metadata);模型把它放进 html_report v2 的 `charts:{block_id:option}` 即可渲染。artifact JSON 供审计/独立查看。

**依赖方向**:`tools` → `reporting`(chart_rules/contract;drift 已允许)。注册进 `build_registry`(非 read-only,写产物,不入 `READ_ONLY_TOOLS`)。

## Tech Stack

复用 stdlib(`json`/`pathlib`)+ Wave 1-2 `reporting.chart_rules`/`contract`。无新依赖。ECharts option 是纯 dict(浏览器侧渲染,本工具不渲染像素)。

## Global Constraints

- **质量闸**:每 Task 末全绿。
- **manifest**:新增 `tools/chart_render.py` 一行。
- **逃逸**:option 写入 JSON 文件(`json.dumps`,非 HTML 上下文);后续 html_report 嵌入时仍经 `_escape_json_for_script`(Wave 4 已覆盖)。chart_render 自身不产 HTML,无 XSS 面。
- **路径防护**:`file_name`/`block_id` 沿用 bare-name + `is_relative_to(artifact_dir)`(同 html_report)。
- **确定性**:无 I/O(仅写 artifact)、无 LLM、无时间/随机。option 由 family+data 唯一决定。
- **向后兼容**:`visualization` 不动;新工具独立。`html_report` v1/v2 不改。
- **数据充分性**:经 `chart_rules.check_data_sufficiency`;不充分时**仍生成 option**(不阻断),但 metadata 标 `data_sufficient=False` + reason,让 QA 层决定(blocking 留给 run_qa,本工具只报告)。

## File Structure

| 文件                                            | 责任                                                                         | 动作 |
| ----------------------------------------------- | ---------------------------------------------------------------------------- | ---- |
| `src/data_analysis_agent/tools/chart_render.py` | `ChartRenderTool`:结构化输入 → ECharts option + 充分性 + artifact + metadata | 新建 |
| `src/data_analysis_agent/tools/__init__.py`     | 导出 `ChartRenderTool`                                                       | 改   |
| `src/data_analysis_agent/runtime.py`            | `build_registry` 注册(非 read-only)                                          | 改   |
| `docs/ARCHITECTURE.md`                          | manifest 加 1 行                                                             | 改   |
| `tests/test_chart_render.py`                    | 各 family option 结构 + 充分性 + artifact 落盘 + 路径防护 + 校验             | 新建 |

**回滚**:全增量(1 新工具 + 注册 + manifest + 测试)。`git revert` 即可。

---

## Task 1: ChartRenderTool — 校验 + family 分发 + line/bar

**Files:** New `tools/chart_render.py`; New `tests/test_chart_render.py`; Modify manifest。

**Interfaces:**

- `ChartRenderTool(Tool)`:`name="chart_render"`、`is_read_only→False`(写产物)、`is_destructive→False`、`is_concurrency_safe→True`(纯计算 + 写独立文件)。
- `__init__(artifact_dir=None)`(同 html_report 的 artifact_dir 约定;None → mkdtemp)。
- `input_schema`:`block_id`(required str)、`family`(required,enum line/bar/grouped_bar/stacked_bar/scatter)、`data`(required object)、`fields?`/`title?`/`subtitle?`/`caption?`/`interpretation?`/`units?`/`x_axis_name?`/`y_axis_name?`/`file_name?`。
- `validate_input`:`block_id` 非空 str(bare-name,用于文件名与 v2 链接);`family` 在支持集;`data` 是 dict;按 family 检 `data` shape(line/bar/grouped_bar/stacked_bar 需 `labels`(list)+ `series`(list,每项 name+values);scatter 需 `points`(list of [x,y]))。
- `call`:按 family 调 `_build_line`/`_build_bar`/...→ option;`check_data_sufficiency`;写 `<block_id>.json`;返回 metadata。

- [ ] Step 1: 写失败测试:`test_chart_render_line`(line → option.series[0].type=="line"、xAxis.data==labels)、`test_chart_render_bar`、`test_chart_render_writes_artifact`(json 文件落盘 + path 在 artifact_dir 内)、`test_chart_render_validates_*`。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `_build_line`/`_build_bar`(option dict)+ 校验 + 落盘。
- [ ] Step 4: manifest + gate → PASS。

## Task 2: grouped_bar / stacked_bar / scatter + 充分性 + metadata

**Files:** 追加 chart_render.py; 追加测试。

- [ ] Step 1: 写失败测试:`test_chart_render_grouped_bar`(多 series type bar 并列)、`test_chart_render_stacked_bar`(series.stack=="total")、`test_chart_render_scatter`(series.type=="scatter",data=points)、`test_chart_render_data_sufficiency_line`(labels<MIN_TREND_POINTS → meta.data_sufficient=False + reason)、`test_chart_render_metadata_shape`(meta 含 family/n_points/block_id)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `_build_grouped_bar`/`_build_stacked_bar`/`_build_scatter` + `chart_rules.check_data_sufficiency` 集成 + metadata。
- [ ] Step 4: gate → PASS。

## Task 3: 注册 + 路径防护 + 最终闸 + 独立审查

- [ ] Step 1: 注册 `ChartRenderTool` 进 `build_registry`(用与 html_report 同一个 `artifact_dir`);导出;manifest;`_PROD_TOOLS` 加 `"chart_render"`。
- [ ] Step 2: 测试:`test_chart_render_registered`、`test_chart_render_path_containment`(block_id `../evil` 被拒)。
- [ ] Step 3: 最终 `quality_gate` 全绿。
- [ ] Step 4: 独立代码审查闭环(§2.9):spawn 全新只读 reviewer,重点:option 正确性(各 family)、充分性集成、路径防护、确定性、向后兼容(visualization 不动)、_PROD_TOOLS 同步。修复 → 复审至零遗留。
- [ ] Step 5: Commit。

## 不在本计划内

- `heatmap`/`waterfall`/`funnel`/`dot`(`lollipop`)图族(延后;spec §6 较少用,dot 可后续作 bar 变体)。覆盖 §6.1-§6.4 + §6.6 默认图族;§6.5 funnel/cohort heatmap 显式 defer。
- Wave 6:报告模板;Wave 7:eval gate;Wave 8:Web UX。

## Self-Review(独立计划评审 APPROVE-WITH-FIXES,10 条全采纳)

1. **fallback_family(spec §4.7 强制)**:`check_data_sufficiency` 返 `(False,_)` 时调 `chart_rules.suggest_fallback(...)`,把 `fallback_family` 进 `chart_meta`(可 None)。加测试(line<3、scatter<10 各一)。
2. **`is_concurrency_safe=False`**:与 html_report 一致(同 block_id 并发写同文件竞态),保守。
3. **block_id 文件名校验完整 5 项**:NUL / `Path(name)!=name`(拒 `..`、`a/b`)/ 点开头 / 点·空格结尾 / Windows 保留名(CON/PRN/.../COM1-9/LPT1-9)。**复制** html_report 的 bare-name 规则到 chart_render(不抽公共方法,避免动 html_report;与 Wave 4 v2 同策略)。加 NUL/Windows/点开头/点空格 测试。
4. **chart_render → html_report v2 组合测试**:chart_render 产 option → 喂 v2 `charts:{block_id:option}` → 渲染 HTML 含 `chart_{block_id}` div + option 在 `<script>`。
5. **`_PROD_TOOLS` 在 `tests/test_runtime.py`**(Wave 3 为 3 个 report 工具加的),非 `runtime.py`。Task 3 引用它加 `"chart_render"`。
6. **chart_meta → run_qa 衔接(文档化)**:`chart_meta.n_points`/`n_observations`/`data_sufficient` 与 `run_qa` 的 `n_points_by_chart`/`n_observations_by_chart` kwargs(qa.py)及 `ChartSpec.data_sufficient`(contract.py)对齐;模型负责把 meta 透传给 QA 或填 ChartSpec。
7. **chart_render 加进 plan-mode 显式 deny 列表**(runtime.py,与 visualization/html_report 并列)。
8. **覆盖措辞收紧**:覆盖 §6.1-§6.4 + §6.6 默认;§6.5 funnel/heatmap defer(见上)。
9. **补充测试**:scatter 充分性不足、多 series line、file_name 覆盖、artifact JSON 可独立 json.load 读回。
