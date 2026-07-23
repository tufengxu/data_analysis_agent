# 2026-07-23 data_profile Excel header-health（支线 P1-4.7）

> roadmap P1-4.7「hidden empty header rows」：Excel 工作表常有标题行/空行/合并单元格标题
> 在真正的表头之上。`data_profile`（及所有下游）用 `pd.read_excel(header=0)`，遇到这种表
> 会把标题当列名、真表头掉进数据行 → profile 错、后续 data_quality/join_planner/nl_query
> 全错。本 slice 给 data_profile 的 Excel 路径加 **header-health 检测**：发现真实表头不在
> row 0 时，按检测到的 offset 重新解析（profile 真实列）+ 报 `header_offset` + 警告。

## Intent

让 data_profile 对「脏表头」Excel（标题行/空行在前）诚实且正确：检测真实表头行，按它
重新 profile，并告诉模型「此 sheet 真表头在 row N，读取时用 header=N/skiprows=N」。

## Ground truth（已核实）

- **`tools/data_profile.py:125 _profile_excel`**：`workbook.parse(sheet, nrows=_SAMPLE_ROWS+1)`，
  默认 `header=0`，逐 sheet profile。无 header offset 检测。
- **实证（pandas 3.0.3）**：脏表（row0 标题「2024 销售月报」+ row1 空 + row2 真表头 + 数据）
  → `header=0` 把标题当列、`Unnamed: 1/2`、真表头进数据行；`header=2` 正确。
  `header=None, nrows=N` 给原始网格，row0=标题(1 非空)、row1=0 非空、row2=真表头(3 非空)。
- **roadmap P1-4.7 其它子项已覆盖**：multi-sheet discovery ✓（data_profile 逐 sheet）、
  cross-sheet joins ✓（join_planner）、sheet selection ✓（data_quality 接 `sheet`）。
  本 slice 只做缺的 **hidden empty header rows（#6）**。workbook summary（#4）/common
  date·amount·account 列检测（#5）留 follow-up（独立 heuristic slice）。
- **data_profile 表 shape**：`_table(sheet, columns, n_rows_sampled, sampled)` →
  `{sheet, n_cols, columns, n_rows_sampled, sampled}`。additive 加 `header_offset`/warning 安全。
- **下游消费者**：`reporting/context_collector.py` 读 data_profile 的 columns；additive 字段不破坏。
  data_profile 既有 Excel 测试用干净 sheet（`to_excel(index=False)`），offset=0，不受影响。

## 设计决策

1. **检测启发式（三道 gate，全部满足才判位移；保守优先）**：对每 sheet 读前 `_HEADER_SCAN_ROWS=8` 行（`header=None`）；逐行数非空单元格。`row0_count`=row0 非空数；`best`=扫描行最大非空数、`best_row`=首个达峰行。判位移当且仅当：
   - **density**：`best >= row0 + 2`（row0 明显更稀疏）。
   - **blank-row**：`best_row` 上方区域 `range(0, best_row)` 存在全空行（干净数据表无全空行；全空行是无歧义 layout 标志）。
   - **candidate all-string**：`best_row` 非空单元格全为 string（真表头是列名字符串；含数值/时间的数据行被拒）。
   - 干净表（任何正常形态）→ offset 0；真位移（标题+空行+表头+typed 数据）→ 检出。
   - **已知不可约残留**（degenerate，非现实干净表）：表头有 ≥2 无名列 + 全空 spacer 行 + 全文本数据 → 仍可能误判（密度+全空行+全字符串都无法区分「稀疏无名表头 over 全文本数据」与「标题 over 全文本表头」）。记录为已知限制，不阻塞。
   - 四轮独立审查逐轮收紧：density `>row0`→`>=row0+2`（修 margin-1 假阳）→ blank-row gate（修 margin≥2 假阳，弃 dtype-below 因其有末行边角）→ candidate all-string（修「稀疏表头+空行+numeric 数据」假阳）。
2. **检测到 offset>0 → 用 `header=offset` 重新解析**该 sheet（profile 真实列、真实行数）。
   重解析失败（offset 越界等）→ 回退 header=0、offset=0、不报错。
3. **additive metadata**：每个 Excel table 加 `header_offset: int`（默认 0）。offset>0 时额外
   在**可读摘要**加一行警告「⚠ 真表头检测在 row N（上方有标题/空行）；读取用
   `pd.read_excel(..., header=N)` 或 `skiprows=N`」。
4. **不动 CSV/Parquet/目录模式**——只影响 Excel sheet profile 路径。CSV 的 header 问题
   另属（且 csv 无标题行惯例问题同此），本 slice 收口 Excel（roadmap P1-4.7 原文）。
5. **保守优先**：检测宁可漏（offset 0，维持现状）不可误（把干净表标脏）。`best_count > row0_count`
   严格大于 + `≥2` 双门保证干净表不误判。

## 文件范围

- `src/data_analysis_agent/tools/data_profile.py`：`_HEADER_SCAN_ROWS` 常量；
  `_detect_header_offset(raw_grid) -> int` helper；`_profile_excel` 逐 sheet 先 `header=None`
  探 offset、再按 offset 重解析；`_table` 加 `header_offset`；`_render_table` offset>0 加警告行。
- 扩展 `tests/test_data_profile.py`：脏表（标题+空+表头+数据）→ 检测 offset、profile 真实列、
  警告出现；干净表 → offset 0 不变（回归）；多 sheet 混合（一脏一净）。
- **不改** drift_rules / context_collector（additive）/ AGENTS.md/CLAUDE.md。

## 验收

- 脏表（标题 row0 + 空 row1 + 真表头 row2 + 数据）→ `header_offset==2`，profiled columns 是
  真表头（非标题/Unnamed），摘要含 `header=N` 警告。
- 干净表 → `header_offset==0`，行为与现状字节一致（既有 test_data_profile 全绿）。
- 多 sheet 工作簿一脏一净 → 脏 sheet offset>0+警告，净 sheet offset 0。
- 单列表 / 全空 sheet / offset 越界 → 不崩，offset 0 回退。
- 质量门全绿；独立审查 blocking/major 清零。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_data_profile.py -q
```

## 显式不在本 slice

- workbook summary（#4）/ common date·amount·account 列检测（#5）——独立 heuristic slice。
- CSV 同款 header 检测（收口 Excel；CSV 无标题行惯例问题，且 read_csv 有不同语义）。
- context_collector 消费 header_offset（先 surface，消费侧接活留后续）。
