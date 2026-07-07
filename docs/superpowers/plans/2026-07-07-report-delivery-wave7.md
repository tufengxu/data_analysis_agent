# 报告交付优化 · Wave 7 实现计划 — 报告行为 eval gate(断言词表 + 失败分类 + ≥20 任务 + 结构校验)

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 Task 实现。

> **Baseline:** `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`(§5.5 Evaluation Integration、§8 Wave 7)
> **红线:** ADR 0005「eval 断言只验方法/结构,**绝不**固化数值」——本 Wave 全部新断言必须 method-only。
> **Depends on:** Wave 1-6(报告工具链已就绪,html_report v2 产 artifact);eval 子系统 `evolution/evaluator.py`
> **Scope:** Wave 7。扩 eval 断言词表支持报告质量(method-only)+ 失败分类学 + ≥20 跨域报告 eval 任务 + 确定性结构校验脚本。**不改 agent_loop/events**(ToolResultEvent 已带 tool_name+artifacts)。

## Goal

让"报告质量回归"可被 eval 系统判定(spec §8 Wave 7 acceptance):

- ≥20 报告 eval 任务,跨多域(spec §8 #1)。
- 报告质量断言:`required_tools`(如 html_report 被调)+ `artifact_produced`(artifact 路径产出)+ 复用 `final_text_contains`(执行摘要/数据范围/caveat/下一步 关键词)。**全 method-only,不验数值**(ADR 0005)。
- eval 输出区分 code/tool 失败 vs 报告质量失败(spec §8 #2)——经失败分类学。
- 确定性结构校验脚本(spec §8 #5 "eval gate optional until cost/determinism controlled")——校验任务文件 schema/数量/域覆盖/方法非数值,不跑 LLM。

**显式 defer**(本 Wave 不做):

- traceability 断言(contract→need / findings→evidence):需 agent 在 final_text 外暴露结构化 contract/document,当前 EvalRun 只有 final_text。延后(记 Wave 7.5)。
- 实跑 eval(需 LLM/API key):沿用既有 `evolution evaluate` CLI,可选;本 Wave 只交付确定性结构校验 + 断言词表。

## Architecture

### 1. 断言词表扩展(`evolution/evaluator.py`,向后兼容)

`EvalRun` 加两字段(默认空,旧任务不受影响):

```python
@dataclass
class EvalRun:
    tool_call_count: int
    has_error: bool
    final_text: str
    tools_used: tuple[str, ...] = ()        # 新:从 ToolResultEvent.tool_name 累积
    artifact_paths: tuple[str, ...] = ()    # 新:从 ToolResultEvent.artifacts 累积
```

`make_agent_run_fn` 在 ToolResultEvent 分支额外记 `event.tool_name` 与 `event.artifacts`(events.py 已提供,无需改 events)。

`check_assertions` 加两条 method-only 断言:

- `required_tools`: list[str] —— 每个都必须出现在 `run.tools_used`(报告任务要求 html_report 被调)。
- `artifact_produced`: bool —— `bool(run.artifact_paths)`(html_report 产了文件)。

### 2. 失败分类学(新 `evolution/eval_taxonomy.py`)

```python
def classify_failures(failures: list[str]) -> dict[str, list[str]]
# → {"code_tool": [...], "report_quality": [...], "other": [...]}
```

按 failure 文本前缀/关键词归类:

- code_tool:`a tool result was an error`、`tool_call_count ...`(> max / < min)。
- report_quality:`final text missing`、`required tool missing`、`no artifact produced`。
- other:其余。
  让 eval 输出能区分"代码/工具失败"与"报告质量失败"(spec §8 #2)。

### 3. ≥20 报告 eval 任务(`examples/eval_tasks/reports/*.json`)

8 报告类型 × 3 域 = 24 任务(满足 spec §8 "≥20 across multiple domains"):

- 类型:daily_kpi / weekly_kpi / diagnostic / funnel / cohort / risk_anomaly / data_quality / recommendation
- 域:retail / marketing / SaaS(每类型 3 个域变体,共 24)
- 每任务:`task_id`、`input`(中文报告请求,带域与类型线索)、`dataset_fixture: "fixtures/sales.csv"`(共享 fixture,因断言验结构非数值)、`assertions`:
  ```json
  {
    "no_error_results": true,
    "min_tool_calls": 2,
    "tool_call_count_max": 12,
    "required_tools": ["html_report"],
    "artifact_produced": true,
    "final_text_contains": ["报告", "路径"]
  }
  ```
  (final_text_contains 只验报告产物存在的通用措辞,不验业务结论数值——ADR 0005)

### 4. 确定性结构校验(`scripts/eval_gate.py`)

独立脚本(不进 quality_gate——eval 任务是数据,非代码):`python scripts/eval_gate.py report [dir]`

- 校验指定目录(默认 `examples/eval_tasks`)所有 *.json:
  - schema:有 task_id + input + assertions(dict)。
  - 数量:≥20(可配置阈值)。
  - 域覆盖:input 跨 ≥3 域关键词(retail/marketing/SaaS/finance/...)。
  - **方法非数值**:扫描 assertions 不得含数值等式断言(如 `pass_rate ==`、`留存率 ==`);允许的键白名单(no_error_results/min_tool_calls/tool_call_count_max/final_text_contains/final_text_regex/required_tools/artifact_produced)。
- 输出:PASS/FAIL + 统计(任务数/域/违规项)。退出码 0/1。

## Tech Stack

Python stdlib(json/pathlib/re/argparse)。复用 `evolution/evaluator.py`。无新依赖。

## Global Constraints

- **质量闸**:每 Task 末全绿。
- **ADR 0005(硬红线)**:所有新断言 method/structure-only;`scripts/eval_gate.py` 必须扫描并拒绝数值等式断言。
- **向后兼容**:EvalRun 新字段有默认;check_assertions 新键可选;既有 descriptive_smoke + evaluate CLI 不受影响。
- **确定性**:eval_gate 不跑 LLM(只校验文件);断言词表是纯函数。
- **manifest**:新增 `evolution/eval_taxonomy.py`(1 行)。`scripts/eval_gate.py` 不在 manifest(scripts/ 不登记,见既有 scripts/checks.py 等)。
- **不改**:agent_loop / events / runtime / reporting / tools。

## File Structure

| 文件                                                 | 责任                                                                                                           | 动作 |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ---- |
| `src/data_analysis_agent/evolution/evaluator.py`     | EvalRun +tools_used/artifact_paths;make_agent_run_fn 捕获;check_assertions 加 required_tools/artifact_produced | 改   |
| `src/data_analysis_agent/evolution/eval_taxonomy.py` | classify_failures 失败分类学                                                                                   | 新建 |
| `docs/ARCHITECTURE.md`                               | manifest 加 eval_taxonomy 行                                                                                   | 改   |
| `examples/eval_tasks/reports/*.json`                 | 24 个报告 eval 任务(8 类型×3 域)                                                                               | 新建 |
| `scripts/eval_gate.py`                               | 确定性结构校验(schema/数量/域/方法非数值)                                                                      | 新建 |
| `tests/test_eval_report_assertions.py`               | check_assertions 新断言 + 分类学 + eval_gate 结构校验                                                          | 新建 |

**回滚**:全增量(evaluator 改为向后兼容追加 + 新文件)。`git revert` 即可。

---

## Task 1: EvalRun 扩字段 + check_assertions 新断言 + make_agent_run_fn 捕获

**Files:** Modify `evolution/evaluator.py`; New `tests/test_eval_report_assertions.py`。

- [ ] Step 1: 写失败测试:
  - `test_required_tools_pass`:run.tools_used 含 html_report → required_tools=["html_report"] 通过。
  - `test_required_tools_fail`:tools_used 不含 → 失败,失败信息含 "required tool missing"。
  - `test_artifact_produced_pass/fail`:artifact_paths 空 vs 非空。
  - `test_eval_run_defaults`:EvalRun 新字段默认空 tuple(向后兼容)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 扩 EvalRun(make_agent_run_fn 记 event.tool_name + event.artifacts)+ check_assertions(两条新规则,失败信息用分类学前缀:`required tool missing: X` / `no artifact produced`)。
- [ ] Step 4: gate → PASS(既有 evaluator 测试不回归)。

## Task 2: 失败分类学 + 确定性结构校验脚本

**Files:** New `evolution/eval_taxonomy.py`; New `scripts/eval_gate.py`; manifest; 追加测试。

- [ ] Step 1: 写失败测试:
  - `test_classify_code_tool`/`test_classify_report_quality`/`test_classify_other`。
  - `test_eval_gate_pass`(干净目录 ≥20 任务,多域,method-only → 退出 0)。
  - `test_eval_gate_rejects_numeric_assertion`(含 `留存率 == 12%` 断言 → FAIL + 报告违规)。
  - `test_eval_gate_rejects_too_few`(<20 → FAIL)。
  - `test_eval_gate_rejects_bad_schema`(缺 task_id → FAIL)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `eval_taxonomy.classify_failures` + `scripts/eval_gate.py`(argparse + 扫描 + 白名单断言键 + 数值等式检测)。
- [ ] Step 4: manifest + gate → PASS。

## Task 3: ≥20 报告 eval 任务 + 最终闸 + 独立审查 + commit

- [ ] Step 1: 生成 24 个 `examples/eval_tasks/reports/*.json`(8 类型×3 域;脚本化生成或手写,均 method-only)。
- [ ] Step 2: 跑 `scripts/eval_gate.py report examples/eval_tasks` → PASS(≥24、多域、method-only)。
- [ ] Step 3: 最终 `quality_gate.py` 全绿。
- [ ] Step 4: 独立代码审查闭环(§2.9):spawn 全新只读 reviewer,重点 **ADR 0005 method-only 合规**(无任何数值等式断言)、向后兼容(EvalRun/check_assertions 不破既有)、eval_gate 确定性、任务覆盖(8 类型×3 域)、分类学正确。修复 → 复审至零遗留。
- [ ] Step 5: Commit。

## 不在本计划内

- traceability 断言(需 agent 暴露结构化 contract;Wave 7.5)。
- 实跑 eval(沿用 evaluate CLI,需 API key;本 Wave 只确定性结构校验)。
- Wave 8:Web UX(阻塞)。

## Self-Review(独立计划评审 APPROVE-WITH-FIXES,8 条全采纳)

**运行时链已验证**(评审):agent_loop.py:380-386 把 tool_name+artifacts 填进 ToolResultEvent;html_report v1/v2 都 emit artifact_paths。新断言会真生效。

1. **白名单键扫描(R1,ADR 0005 阻塞项)**:eval_gate 用**键白名单**(no_error_results/min_tool_calls/tool_call_count_max/final_text_contains/final_text_regex/required_tools/artifact_produced),**非 regex**——`tool_call_count_max: 12` 是结构上限,合法。加负向测试 `test_eval_gate_allows_structural_caps` 证明不被误判。
2. **失败字符串精确前缀(R4,阻塞项)**:check_assertions 新分支产 **`required tool missing: {tool}`** 与 **`no artifact produced`**;测试用精确串钉住。
3. **分类学补 regex(R3)**:`final text did not match` 归 report_quality(与 `final text missing` 一致)。
4. **域关键词含 CJK(R2)**:零售/零售业、营销/市场营销、SaaS/订阅、金融/财务、运营、风险;24 任务的 input 用这些精确词。
5. **eval_gate 默认目录(R6)**:扫 `examples/eval_tasks`(递归 *.json,含既有 descriptive_smoke + 新 reports/);descriptive_smoke 无 required_tools 仍合法(opt-in)。加测试钉住向后兼容。
6. **诚实记录 final_text_contains 弱代理**:它只验 agent **会话回复**提及"报告/路径",**非** HTML artifact 含执行摘要/数据范围/caveat section。section 级 HTML 结构校验与 traceability 一同 defer Wave 7.5。
7. **funnel/cohort 任务措辞(R5)**:input 用"基于此销售数据生成漏斗报告框架",承认 sales.csv 非漏斗形,避免模型拒绝。
8. **向后兼容测试(G1)**:仅 `{no_error_results: true}` 的旧任务过 check_assertions + eval_gate。
