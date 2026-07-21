# 2026-07-21 join_planner 工具（支线 P1-4.2）

> data_profile 给结构（列名预览「spot shared join keys」），config/skill 提示模型
> 「自己比较列名和值重叠决定 join key」——但今天模型只能**手写 pandas 猜测-试错**
> 才能确定 join key、关系类型和行乘积风险。join_planner 把这一步变成确定性的只读
> advisory：读多张表，给出候选 join key、唯一性/关系类型、行乘积风险、null-key 风险、
> 值覆盖、推荐 join 顺序。收敛 roadmap P1-4.2 的五项能力。

## Intent

跨表只读 join 顾问。输入一组文件路径，输出每对可连接表的候选键、关系（1:1/1:N/N:1/N:N）、
行乘积风险、null-key 风险、值覆盖率和推荐 join 顺序，让模型在写 merge 代码前就知道
用什么键、会不会行乘积、会不会丢行。

## Ground truth（已核实当前源码）

- `src/` 内**无任何 join-planning 逻辑**（grep join_planner/candidate key/value overlap 均空）。
- `tools/data_profile.py` 目录模式只给列名预览（结构层「spot shared join keys」），**不做值级
  join 分析**——互补，无重叠。
- `config.py:45` 系统提示 + `skills/builtin.py:233` JointAnalysisSkill 都让模型「比较列名和值
  重叠自行决定 join key」——即当前是模型手写代码猜测。join_planner 提供确定性 advisory 替代猜测。
- `tests/test_excel_join_integration.py` 测的是 PythonAnalysisTool 读 Excel 的沙箱执行链，非 join 规划。
- 注册点同 data_quality：`runtime.build_registry`、`READ_ONLY_TOOLS`、
  `test_safety_baseline.test_every_builtin_tool_is_classified_for_local_safe`（每个内置工具必须
  在 READ_ONLY_TOOLS ∪ MUTATOR_TOOLS）、`tools/__init__.__all__`、`ARCHITECTURE` manifest、
  `test_runtime._PROD_TOOLS`。`scripts/drift_rules.py` 既有 `who=...tools → forbid agent_loop`
  自动覆盖新模块（不是新包，无需加规则）。
- 安全基线镜像 `tools/data_quality.py`：`allowed_paths` 白名单 fail-closed、`call()` 先 `resolve()`
  再 `_within_allowed`（symlink/`..` 已验证）、`is_read_only=True / is_destructive=False /
is_concurrency_safe=True`、ABSOLUTE 路径、pandas 硬依赖。

## 设计决策

1. **输入 = `paths`（list，≥1 个文件路径）**。每个路径读成若干表：Excel → 每个 sheet 一张表
   （`sheet_name` 作表名）；CSV/TSV/Parquet → 一张表（文件名作表名）。**总表数 < 2 → 报错**
   （`need ≥2 tables to plan a join`）。这同时覆盖「多文件」和「单 workbook 跨 sheet」两种场景。
2. **候选 join key = 跨表同名列（精确匹配，区分大小写）**，出现在 ≥2 张表里。
   - case-sensitive 是确定性、无惊喜的默认；case-insensitive / 跨名值重叠（`orders.cust_id`
     ↔ `customers.id`）是更"模糊"的能力，留 follow-up（spec 显式降级）。
3. **每张表每列基础统计**：`n_rows, n_cols, n_truncated, columns:[列名]`；候选键列额外给
   `n_unique, n_null, is_unique`。`is_unique = (n_unique == n_non_null) 且 n_non_null > 0`
   （全空列不算 unique）。
4. **关系类型（每对共享键的表 A↔B）**：基于 `is_unique` 组合 → `1:1`(两边都 unique) /
   `1:N`(A unique, B 非) / `N:1`(A 非, B unique) / `N:N`(两边都非)。
5. **值覆盖**：`values_A/B` = 各自非空键值集合；`overlap_count=|A∩B|`、
   `left_coverage=|A∩B|/|A|`、`right_coverage=|A∩B|/|B|`。覆盖低（如 <0.5）的同名列 →
   警告「同名但值重叠低，可能不是真 join key」。**高基数键（n_unique > `_MAX_OVERLAP_VALUES=200_000`）
   跳过精确重叠**（避免内存爆炸），标 `overlap: "skipped: high-cardinality"`。
6. **行乘积风险**：`estimated_join_rows = Σ_{v∈A∩B} freq_A(v)·freq_B(v)`（用 value_counts 交集，
   对所有关系类型都成立）。`multiplication_factor = estimated_join_rows / max(n_rows_A, n_rows_B)`。
   `row_multiplication_risk = high` 当关系为 N:N 或 multiplication_factor > 2；否则 `none`/`moderate`。
   N:N 是模型最常踩的坑（无意中行乘积），必须显式标 high。
7. **推荐 join 顺序**：`base` = 行数最多的表（启发式，标注）；贪心：已连接集合从 base 起，
   每步挑一个剩余表，它与已连接集合有共享候选键，**优先选 incoming 侧 unique 的键**（N:1，不乘积），
   连入并记一步 `{table, via_key, relationship, risk}`。无共享键的剩余表排最后并警告
   「与已连接表无同名列，需显式键或值匹配」。输出 `recommended_order:[表名]` + `join_steps:[...]`。
8. **warnings** 汇总：N:N 键、低覆盖同名键、null 比例高的键（key 列 null > 50%）、无候选键、
   任一表被截断、总表数超 `_MAX_TABLES=20`（截断 + 警告）。
9. **读取边界**：每表 `_MAX_ROWS=1_000_000` cap（CSV/TSV `nrows=cap+1`、Excel `parse(nrows=cap+1)`、
   Parquet 不截断），超过则该表 `n_truncated=True` 并进 warnings（行数依赖指标仅反映前 cap 行）。
   与 data_quality 同款诚实截断。
10. **输出 shape** 镜像 data_quality：`ToolResult(content=可读摘要,
metadata={"join_plan": {tables, candidate_keys, recommended_order, join_steps, warnings}})`。

## 文件范围

- 新 `src/data_analysis_agent/tools/join_planner.py`：`JoinPlannerTool` + 模块级 helper。
- `src/data_analysis_agent/tools/__init__.py`：import `JoinPlannerTool` + `__all__`。
- `src/data_analysis_agent/runtime.py`：import；`READ_ONLY_TOOLS` 加 `"join_planner"`；
  `build_registry` 注册（紧跟 `DataQualityTool`）。
- `src/data_analysis_agent/config.py`：多文件/multi-sheet 那句补一句 join_planner 引导。
- `docs/ARCHITECTURE.md`：manifest 块加 `tools/join_planner.py` 一行。
- 新 `tests/test_join_planner.py`：覆盖五项能力 + Excel 跨 sheet + 跨文件 + 错误路径 + metadata。
- `tests/test_runtime.py`：`_PROD_TOOLS` 加 `"join_planner"`。
- **不改** `scripts/drift_rules.py`；**不改** AGENTS.md / CLAUDE.md。

## 验收

- 两 CSV 共享一列：检出候选键，关系类型正确（orders.cust_id N:1 customers.cust_id）、
  `estimated_join_rows` 等于 orders 行数（1:N 不乘积）、覆盖率正确。
- N:N（两表键都有重复）→ `row_multiplication_risk=high`，`estimated_join_rows` > 两表行数。
- null-key：键列含大量 null → warning + per-table `n_null`。
- Excel 单 workbook 两 sheet 共享列 → 跨 sheet 候选键。
- `recommended_order` 以行数最多的表为 base；`join_steps` 每步含 via_key/relationship/risk。
- 无任何同名列 → `candidate_keys=[]`，warnings 含「no shared-name candidate keys」。
- 错误路径：路径越界 / 路径不存在 / 不支持后缀 / pandas 缺失 / 总表数 < 2 各返回 `is_error` 且消息清晰。
- 超过 `_MAX_ROWS` 的表：该表 `n_truncated=True` + warning。
- 质量门 `.venv/bin/python scripts/quality_gate.py` 全绿；`test_every_builtin_tool_is_classified_for_local_safe`
  仍绿（join_planner 进 READ_ONLY_TOOLS）。
- 独立只读子 Agent 审查：blocking/major 清零（minor 可记 backlog）。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_join_planner.py -q
```

## 显式不在本 slice

- P1-4.3 `metric_contract`（下一 slice，同模式）。
- 跨名值重叠（`cust_id`↔`id`）和 case-insensitive 键匹配——更模糊，留 follow-up。
- 自动执行 merge（join_planner 只规划不执行；执行仍走 python_analysis，保持只读 + 模型掌控）。
- 把 join_plan 注入 report_context / 报告 caveat（后续报告侧硬化）。
