# 2026-07-22 nl_query schema-aware + secret 防护（支线 P1-4.6）

> roadmap P1-4.6：① 启发式当辅助非权威；② 用 data_profile 输出做 schema-aware 列选择；
> ③ 不在生成代码里嵌连接串/secrets。当前 `nl_query` 生成的 pandas/SQL 代码全用
> `numeric_cols[0]`/`column_name` 占位（非 schema-aware），且 SQL 源把 `data_source`
> 连接串（可能含 `user:pass@host`）直接内联进 `create_engine(...)` → 代码/轨迹泄露凭据。

## Intent

让 `nl_query` 生成的代码（a）用真实列名（来自可选 data_profile schema 输出），（b）SQL 源
不再内联含凭据的连接串。仍是只读、启发式、辅助性（模型经 python_analysis 精修）。

## Ground truth（已核实）

- **`tools/nl_query.py`**：`call()` 按 source_type 分派 `_generate_pandas/_generate_sql/_generate_dataframe`。
  全部代码生成、**只读**（`is_read_only=True`，不执行）。
- **secret 嵌入**：`_generate_sql` line 246 `f"engine = create_engine(r'{data_source}')"` ——
  `data_source` 对 SQL 是连接串，若含凭据（`scheme://user:pass@host`）则明文进生成代码（模型可见、
  落 trajectory）。pandas 源 line 146 `f"df = {read_func}(r'{data_source}')"` 是文件路径，无凭据问题。
- **非 schema-aware**：`_generate_pandas`/`_generate_dataframe` 用运行时 `df.select_dtypes(...)`
  取 `numeric_cols[0]`/`cat_cols[0]`；SQL 用 `column_name` 占位。从不参考真实列名。
- **input_schema** 必填 `query/data_source/source_type`；无 schema 字段。
- **data_profile 输出 shape**：`tables[*].columns = [{"name","dtype"}, ...]` —— 可直接作 schema 输入。
- **PANDAS_PATTERNS** 关键词→操作（nlargest/mean/sum/count/groupby/...）已就绪，可复用做列匹配。

## 设计决策

1. **可选 `schema` 输入**（array of `{name, dtype}` 或 `{columns: [...]}`，data_profile 直传）。
   提供 → 生成代码用**真实列名**；不提供 → 保持现行为（运行时 select_dtypes / 占位），向后兼容。
2. **schema-aware 列选择**：从 schema 算 `numeric_names`/`categorical_names`（dtype 串判数值：
   `int*/float*/number/decimal` 为数值；余为非数值）。对 aggregation/sort/filter/nlargest 等意图，
   **按 query 关键词匹配列名**（列名小写 token 在 query 中出现，或 query token == 列名）选最佳数值列；
   无匹配则用第一个真实数值列名。生成代码引用真实列名（模型可见可改），保留 `if numeric_cols` 守卫。
3. **SQL secret 防护**：`_has_embedded_credentials(data_source)` 检测 `://[^/@]+:[^/@]+@`（user:pass@）。
   - 命中：生成代码改用 `os.environ["DB_URL"]` 间接（**不内联**），display/trajectory 只显示脱敏串
     `scheme://***@host`；content 加 warning「检测到嵌入凭据，已改用 DB_URL 环境变量；请勿硬编码」。
   - 未命中（sqlite、或无凭据）：保持内联（无 secret）。
4. **辅助性标注**：description 明示「启发式草稿，非权威；用 python_analysis 精修」。既有文案已含此意，
   轻微强化。
5. **只读/不执行不变**：nl_query 仍只生成代码，不跑；secret 防护是「生成的代码不含凭据」，不动执行模型。

## 文件范围

- `src/data_analysis_agent/tools/nl_query.py`：input_schema 加 `schema`；`_columns_from_schema` +
  `_pick_column(query, candidates)` + `_has_embedded_credentials` + `_redact_connection_string` helper；
  `_generate_pandas`/`_generate_dataframe` 用 schema 真实列名；`_generate_sql` 用 env 间接 + 脱敏。
- 新增/扩展测试 `tests/test_nl_query.py`（若无则建）：schema-aware 真实列名、列名匹配、无 schema 回退、
  SQL 凭据检测→env 间接 + 脱敏、无凭据 sqlite 内联不变、向后兼容。
- **不改** drift_rules / AGENTS.md/CLAUDE.md。

## 验收

- schema-aware：`schema=[{name:"revenue",dtype:"int64"},{name:"product",dtype:"object"}]` + query
  "top 10 by revenue" → 生成代码含 `df.nlargest(10, 'revenue')`（真实列名，非 numeric_cols[0]）。
- 列名匹配：query "average price" + schema 含 `price`(float) → `df['price'].mean()` 或选 price 列。
- 无 schema → 保持现 `numeric_cols[0]`/select_dtypes 行为（回归测试）。
- SQL 凭据：`data_source="postgresql://u:secret@host/db"` → 生成代码用 `os.environ["DB_URL"]`，
  不含 `u:secret`；content warning + 脱敏显示 `postgresql://***@host/db`。
- SQL 无凭据：`data_source="sqlite:///local.db"` → 内联 `create_engine('sqlite:///local.db')` 不变。
- 既有 nl_query 测试全绿（向后兼容）；质量门全绿；独立审查 blocking/major 清零。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_nl_query.py -q
```

## 显式不在本 slice

- LLM 驱动的 NL2SQL（仍是关键词启发式；roadmap 说"assistive not authoritative"，不引入 LLM）。
- 实际执行生成的代码（仍交 python_analysis；nl_query 保持只读生成）。
- P1-4.7 Excel 多表工作流（独立 slice）。
