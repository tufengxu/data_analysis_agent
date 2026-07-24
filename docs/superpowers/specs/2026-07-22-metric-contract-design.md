# 2026-07-22 metric_contract 工具（支线 P1-4.3）

> roadmap P1-4.3：「represent metric name, numerator, denominator, filters,
> time window, grain, timezone, exclusions, owner confirmation status. Connect
> to memory `metric_definition`.」今天一个指标的口径散落在模型每次手写的
> pandas 里，跟 memory 里 `/define` 存的口径定义对不上也无感知。metric_contract
> 把一个指标规整成可审计的结构化口径 + 校验完整性 + 交叉核对 memory 定义。

## Intent

只读、无状态的口径规整工具。输入一个指标的结构化字段（+ 可选的 memory 已存定义），
产出一个可溯源、经校验的 `MetricSpec`（带 exclusions）+ memory 链接 + 校验 findings。
让模型在算指标前先把口径钉死，并与已确认的 memory 定义对齐。

## Ground truth（已核实当前源码）

- **`reporting/contract.py:103 MetricSpec`** 已存在且几乎是 roadmap 要的字段集：
  `name, source_columns, numerator, denominator, aggregation, filters, time_window,
grain, timezone, unit, confirmed, source`。**唯一缺 `exclusions`**（roadmap 明列）。
- **`reporting/model.py:117 Serializable`**：`from_dict` 只读 `data` 里存在的字段
  （`for f in fields(cls): if f.name in data`）→ 给 MetricSpec 加一个带默认值的字段
  是**向后兼容**的（旧 dict 重建走默认值，新 dict 多一个键）。
- **`reporting/qa.py:273 _check_metric_definitions`**：报告 QA 已检查
  `numerator/denominator/aggregation` 全空的指标 → metric_contract 复用同一完整性判据，
  在算之前就把这个错误拦住。
- **contract 工具范式 `tools/report_contract.py`**：薄封装、只读、无状态、无 path 白名单
  （纯数据规整，不读文件）、`is_read_only=True / is_destructive=False /
is_concurrency_safe=True`、产领域对象 `to_dict()` 入 metadata + 可读 `_render`。
- **memory `metric_definition`**（`memory/model.py:44 MemoryEntry`）：文本口径 + `confirmed`
  标志 + `key`（指标名）。`/define`（`__main__.py:58-78`）是写入路径；injector 把已存定义
  注入模型上下文。→ metric_contract **只读消费**：把模型上下文里已注入的定义作为可选输入，
  交叉核对（对齐 report_contract 取 user_need 作输入、不抓 runtime store 的范式）。
- **`build_registry`（runtime.py:330）不持有 memory store**（memory 在 injector 里）。
  给 tool 注入 live memory 要改 runtime 装配 + 线程穿引，扩 slice。故采用「输入传入」方案。
- **drift_rules**：tools 包只禁 `agent_loop`。metric_contract 不 import memory（纯 dict 输入），
  只 import `reporting.contract`（report_contract 已用，不违规则）→ **无需改 drift_rules**。
- 注册点同 data_quality/join_planner：`runtime.READ_ONLY_TOOLS`、
  `test_safety_baseline.test_every_builtin_tool_is_classified_for_local_safe`、
  `tools/__init__.__all__`、`ARCHITECTURE` manifest、`test_runtime._PROD_TOOLS`。

## 设计决策

1. **无状态、只读、无 path 白名单**（镜像 report_contract，非 data_quality 那种文件工具）。
   纯数据规整，不读文件、不写记忆、不执行代码。
2. **复用 `MetricSpec` 作口径域对象**，加一个 `exclusions: tuple[str, ...] = ()` 字段
   （additive，向后兼容；roadmap 明列；口径的 canonical 位置——这样 exclusions 能随
   ReportContract.metrics 流到 html_report，不会丢）。不另造 `MetricContract` dataclass
   （避免域模型膨胀；工具 metadata 把 spec + findings + memory_link 包起来即可）。
3. **memory 连接 = 接受可选 `memory_definition` 输入**（`{key, content, confirmed}`），
   交叉核对：
   - 存在且 `confirmed` → `owner_confirmed=True`，content 作权威来源；若 `key != name` →
     finding「指标名与 memory 记录不符」。
   - 存在但未确认 → finding「memory 中的口径未确认（light-confirm 待定）」。
   - 缺失 → finding「memory 无该指标定义；用 `/define <name>=<口径>` 固化」。
   - 文本口径无法可靠解析出 numerator/denominator，故**不做**模糊数值冲突判定——只 surface
     已存文本 + 名字一致性，让模型自己比对（诚实，不假装能 fuzzy-match 文本）。
4. **完整性校验 findings**（算之前的门，复用 QA 的判据）：
   - `name` 必填。
   - `numerator/denominator/aggregation` 至少一个非空（否则「口径不完整，无法计算」）。
   - `time_window` 非空但 `grain` 空 → advisory「有时间窗未声明粒度」。
   - `grain` 非空但 `timezone` 空 → advisory「有时间粒度未声明时区（默认 UTC?）」。
   - `denominator` 非空但 `numerator` 空 → advisory「有分母无分子」。
5. **`signature`**：`name|numerator|denominator|grain|aggregation` 规整后的稳定串，供跨 run
   判断「同名异口径」（口径漂移检测，镜像 memory `column_fingerprint` 的思路）。
6. **输出 shape** 镜像 report_contract：`ToolResult(content=可读摘要,
metadata={"metric_contract": {"metric": spec.to_dict(), "memory_link": {...},
"findings": [...], "signature": "..."}})`。findings 每条 `{severity: "error"|"warning"|"info",
code, message}`。

## 文件范围

- 改 `src/data_analysis_agent/reporting/contract.py`：`MetricSpec` 加 `exclusions: tuple[str, ...] = ()`。
- 新 `src/data_analysis_agent/tools/metric_contract.py`：`MetricContractTool` + 校验/memory 核对 helper。
- `src/data_analysis_agent/tools/__init__.py`：import `MetricContractTool` + `__all__`。
- `src/data_analysis_agent/runtime.py`：import；`READ_ONLY_TOOLS` 加 `"metric_contract"`；
  `build_registry` 注册（与其它 contract 工具并列）。
- `src/data_analysis_agent/config.py`：系统提示补一句——算指标前先 metric_contract 钉口径。
- `docs/ARCHITECTURE.md`：manifest 块加 `tools/metric_contract.py` 一行。
- 新 `tests/test_metric_contract.py`：覆盖 9 字段 + 完整性校验 + memory 三态（confirmed/unconfirmed/absent）
  - 名字冲突 + signature + 错误路径 + metadata 结构。
- `tests/test_runtime.py`：`_PROD_TOOLS` 加 `"metric_contract"`。
- **不改** `scripts/drift_rules.py`；**不改** AGENTS.md / CLAUDE.md；**不改** memory 模块（只读消费）。

## 验收

- 9 字段（含 exclusions）正确规整进 `MetricSpec` 并 `to_dict()` 往返。
- 完整性：name 缺 → error finding；numerator/denominator/aggregation 全空 → error finding；
  有 time_window 无 grain → warning；有 grain 无 timezone → warning；有 denominator 无 numerator → warning。
- memory 三态：confirmed → owner_confirmed=True；unconfirmed → warning finding；absent → info finding。
- 名字冲突（memory_definition.key != name）→ warning finding。
- signature 稳定：同口径同串；改 denominator → 串变。
- 错误路径：name 空/非字符串 → `is_error`。
- `MetricSpec` 加 exclusions 后，既有 ReportContract/metrics 往返测试（test_reporting_contract 等）仍绿。
- 质量门全绿；`test_every_builtin_tool_is_classified_for_local_safe` 仍绿（metric_contract 进 READ_ONLY_TOOLS）。
- 独立只读子 Agent 审查：blocking/major 清零（minor 可记 backlog）。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_metric_contract.py tests/test_reporting_contract.py -q
```

## 显式不在本 slice

- 给 tool 注入 live memory store（runtime 装配线程穿引）——若「模型手传 memory_definition」
  证明负担过重，后续 slice 再加 live 接入；当前输入方案对齐 report_contract 范式、无装配改动。
- 文本口径的 fuzzy 冲突判定（从 memory content 解析 numerator/denominator）——不可靠，不做。
- 把 MetricSpec.exclusions 接进 html_report 的 caveat 渲染（后续报告侧硬化）。
- P1-4 其余（4.6 nl_query schema-aware、4.7 Excel 多表）——独立 slice。
