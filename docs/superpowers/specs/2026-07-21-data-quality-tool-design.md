# 2026-07-21 data_quality 工具（支线 P1-4.1）

> 新会话第一刀。data_profile 已核实**只做结构发现**（列/dtype/行数/sheet/目录列举），
> 不做质量检查。data_quality 是干净互补项：在写分析代码之前，先告诉模型这张表
> 哪里脏（缺失/重复/离群/常量列/类型异常/可疑标识符），把 roadmap P1-4.1 的九项
> 能力收敛成一个只读工具。

## Intent

给模型一个**确定性、只读、路径白名单**的数据质量检查工具：读单个表文件，输出
结构化质量元数据 + 可读摘要。与 data_profile（结构发现）正交，不重复其能力。

## Ground truth（已核实当前源码）

- `tools/data_profile.py`：`DataProfileTool` 只做结构（columns/dtype/`n_rows_sampled`/
  sheet 列表/目录 tabular 文件列举），**无任何质量检查**（无缺失/重复/离群统计）。
  确认互补，无重叠。
- 注册点 `runtime.py:106`：`registry.register(DataProfileTool(allowed_paths=paths))`；
  `READ_ONLY_TOOLS`（runtime.py:67）含 `"data_profile"`；`MUTATOR_TOOLS`（:85）是四个 mutator。
- `tests/test_safety_baseline.py:104` `test_every_builtin_tool_is_classified_for_local_safe`：
  `build_registry` 里**每个**内置工具名必须出现在 `READ_ONLY_TOOLS | MUTATOR_TOOLS`，
  否则 local_safe 会静默 DENY。→ 注册 data_quality **必须同时**加进 `READ_ONLY_TOOLS`。
- `tools/base.py`：`Tool` 基类契约——`name/description/input_schema` 抽象；
  `is_read_only/is_destructive/is_concurrency_safe` 默认 fail-closed（False/True/False）；
  `validate_input` 返回 `ValidationResult`；`call` 返回 `ToolResult(content, is_error, metadata)`。
- `tools/__init__.py`：`__all__` 显式导出每个工具类，新工具须加 import + `__all__`。
- `scripts/drift_rules.py`：已有规则 `{"who": "data_analysis_agent.tools",
"forbid": ["data_analysis_agent.agent_loop"]}`，按文件注释「who 匹配自身或以 who+. 开头」
  自动覆盖新模块 `tools/data_quality.py`。data_quality 是**既有 tools 包下的新模块**，
  不是新包 → **无需新增 drift 规则**。
- `docs/ARCHITECTURE.md:45` `<!-- manifest:start -->` 块：每行一个源文件登记；
  `scripts/checks.py` 机器校验，新模块不登记则 drift gate fail（`__init__.py` 不登记，照 web/ 惯例）。
- `config.py:39` 系统提示已提 data_profile（结构发现），未提质量检查。
- `.venv` 已装 `.[data,dev,web]`（pandas/openpyxl/pyarrow/fastapi 齐全）。

## 设计决策

1. **文件-only，无目录模式**。目录 tabular 文件列举是 data_profile 的发现职责；
   「目录的质量」无定义。data_quality 只接受单个文件路径。这是干净互补、不重叠的关键。
2. **镜像 data_profile 的安全姿态**：`allowed_paths` 白名单 + fail-closed
   （`_within_allowed` 对 `resolve()` 后的绝对路径判断）、`is_read_only=True`、
   `is_destructive=False`、`is_concurrency_safe=True`、输出 ABSOLUTE 路径、
   不执行任何模型代码、支持 CSV/TSV/Parquet/Excel(.xlsx/.xls)。
3. **pandas 硬依赖，无 stdlib 降级**。结构发现能用 stdlib csv 给列名+行数；
   质量统计（缺失/重复/离群/基数）没有有意义的 stdlib 降级。pandas 缺失时返回明确错误
   （同 data_profile 对 parquet/excel 的处理），不静默给无意义结果。
4. **全量读取 + 安全上限 + truncation 标记**。质量检查（缺失计数/重复检测）必须全量才准，
   故不采样（区别于 data_profile 的 1000 行采样）。`_MAX_ROWS = 1_000_000` 防 OOM：
   CSV/TSV 用 `read_csv(nrows=cap+1)`、Excel 用 `parse(nrows=cap+1)`（+1 区分「正好 cap 行」
   与「超过 cap」），超过则只读前 cap 行并在摘要与 metadata 双重标记 `truncated=True`，
   显式警告行依赖指标仅反映已加载行。Parquet 列式读取，不做行截断（`truncated` 恒 False）。
5. **Excel 多 sheet**：`sheet` 参数可选。省略 → 检查全部 sheet（镜像 data_profile 的「全部 sheet
   可发现」）。指定 → 只检查该 sheet（不存在则报错）。CSV/TSV/Parquet 忽略 `sheet`。
6. **九项能力收敛为 8 个可测 flag**（每个确定性、可断言），每列给基础统计 + 类型条件统计：
   - 表级：`n_rows, n_cols, n_duplicate_rows, duplicate_row_pct`。
   - 列基础（所有列）：`name, dtype, n_missing, missing_pct, n_unique, uniqueness,
is_constant`。
   - 列类型条件（按 dtype 显式路由，类型异常 flag 只作用于 object/string）：
     数值列（排除 complex/bool，bool 的 IQR 离群是无意义的噪声且 pandas 把 bool 当 numeric）
     给 `numeric:{min,max,mean,median,n_zeros,n_negative,n_outliers,outlier_pct}`（离群用 IQR 法：
     `<Q1-1.5·IQR` 或 `>Q3+1.5·IQR`，仅对非缺失值计算）；datetime 列给 `datetime:{min,max}`（ISO，
     不走 text 路径以免真实 datetime 列被误报 date_stored_as_text）；object/string 列给
     `text:{n_empty_string, numeric_ratio, date_ratio}`（ratio 取前 `_TYPE_SAMPLE=1000` 个非缺失值
     试解析，>0.9 触发 flag）；category/complex/bool/timedelta/period 列仅基础统计。
   - flags（advisory）：`constant`（n_unique==1 且 n_rows>0；全缺失列 n_unique==0 不算常量）、
     `all_unique`（n_unique==n_rows 且 n_rows>1）、`identifier_like`（all_unique 且列名命中 id 模式）、
     `duplicate_key_risk`（列名命中 id 模式 且 not all_unique 且 n_rows>1）、`high_missing`
     （missing_pct≥50）、`numeric_stored_as_text`、`date_stored_as_text`、`high_outliers`（outlier_pct≥5）。
   - id 名启发式：列名小写后按非字母数字分词，token 命中
     {id,identifier,code,key,no,num,index,uid,uuid} 之一（token 级匹配避免 "note" 误命中 "no"）。
     启发式结果只作 advisory flag，绝不阻断。
7. **系统提示最小提及**（config.py）：在已有 data_profile 句后补一句，让模型知道有此工具可调；
   不改既有工作流描述。属低风险行为引导，显式记录。
8. **输出 shape 镜像 data_profile**：`ToolResult(content=可读摘要,
metadata={"quality": {kind,path,format,truncated,tables:[{...}]}})`。每表一个 dict。

## 文件范围

- 新 `src/data_analysis_agent/tools/data_quality.py`：`DataQualityTool` + 模块级 helper。
- `src/data_analysis_agent/tools/__init__.py`：import `DataQualityTool` + 加 `__all__`。
- `src/data_analysis_agent/runtime.py`：import；`READ_ONLY_TOOLS` 加 `"data_quality"`；
  `build_registry` 注册 `DataQualityTool(allowed_paths=paths)`（紧跟 `DataProfileTool`）。
- `src/data_analysis_agent/config.py`：系统提示补一句 data_quality 引导。
- `docs/ARCHITECTURE.md`：manifest 块加 `tools/data_quality.py` 一行。
- 新 `tests/test_data_quality.py`：镜像 `test_data_profile.py` 结构 + 覆盖 8 flag + sheet + truncation + 错误路径。
- **不改** `scripts/drift_rules.py`（既有 tools 包规则自动覆盖）；**不改** AGENTS.md / CLAUDE.md
  （用户的 CodeGraph 段同步，非本 slice）。

## 验收

- `data_quality` 在 `build_registry(AgentConfig())` 中注册，且出现在 `READ_ONLY_TOOLS`
  → `test_every_builtin_tool_is_classified_for_local_safe` 仍绿（不破现有安全门）。
- CSV 缺失/重复/常量列/全唯一列/离群被正确检出，metadata 结构符合设计。
- `numeric_stored_as_text`（object 列多为数字串）、`date_stored_as_text`、
  `duplicate_key_risk`（id 名列有重复）三类启发式 flag 可被构造用例触发并断言。
- Excel：省略 `sheet` 检查全部 sheet；指定存在 sheet 只查该 sheet；指定不存在 sheet 报错。
- 路径越界 / 路径不存在 / 不支持后缀 / pandas 缺失（模拟）各返回 `is_error` 且消息清晰。
- 超过 `_MAX_ROWS` 的 CSV：`truncated=True`，摘要含截断警告。
- 质量门 `.venv/bin/python scripts/quality_gate.py` 全绿（ruff/format/mypy/pytest/drift/eval）。
- 独立只读子 Agent 审查：blocking/major 清零（minor 可记 backlog）。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_data_quality.py -q
.venv/bin/python -m mypy src/data_analysis_agent/tools/data_quality.py
```

## 显式不在本 slice

- P1-4.2 `join_planner`、P1-4.3 `metric_contract`（后续 slice，同模式）。
- `reporting/context_collector.py` 接 data_quality 输出（context_collector 消费 data_profile；
  质量信号是否进报告上下文留待报告交付侧决定，本 slice 不动 reporting 层）。
- 把质量 flag 注入 html_report / report_contract 的 caveat（后续报告侧硬化）。
- 跨文件质量对比（如两表 schema 漂移）——属 join_planner 范畴。
