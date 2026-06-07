# 数据采样式摘要(Sampling-based Compaction)— 方案设计

- 日期: 2026-06-06
- 状态: 已确认方向,待实现
- 关联调研: `docs/data_sampling_for_compaction.md`

## 1. 背景与主要矛盾

数据分析 Agent 在执行中会产出大体量结果(主要来自 `python_analysis` 打印的
DataFrame / Series,也包括 `file_read` 大文件、超大 stdout)。这些结果进入上下文的
**唯一接缝**是 `agent_loop._execute_tools`,当前处理是**盲头截断**:
`content[:max_result_size_chars] + "[truncated from N chars]"`(默认 50 000 字符)。

盲截断的代价:尾部数据直接丢失、零统计保真(无分位数/基数/高频项)、零代表性采样
(留下的是字符串前缀,可能是半截行)。调研报告证实:把全量明细塞进上下文不仅贵,还会因
context rot / lost-in-the-middle **主动降低结论质量**——摘要既省钱又是质量手段。

**主要矛盾**:在"内存有界 + pandas 可选"的真实约束下,用分层摘要(L0 元信息 + L1 统计 +
L2 代表性采样 + L3 序列化)替换盲截断,且**绝不因依赖缺失而崩溃或更差**。

### 与调研报告的务实裁剪(实事求是 > 本本主义)

- 报告主推的流式 sketch(t-digest / HyperLogLog / Count-Min)面向**流式/无界**场景。
  本项目是**内存/有界**(子进程把文件读入 DataFrame),pandas 的精确
  `describe / quantile / nunique / value_counts` 又快又准,**严格优于近似 sketch**。
- 因此:**内存态(沙箱)走精确计算**;**只有无结构、逐行流式的文本兜底**才用纯 Python
  蓄水池采样 + Counter top-k + 样本分位数等"轻量近似"。
- **零新依赖**:不引入 datasketches/ddsketch/t-digest(含 C++ 绑定,增打包与沙箱风险)。

## 2. 关键约束(已核验)

1. `python_exec` 子进程以 `PYTHONPATH=""` 运行 → **不能 import 本包**。沙箱摘要逻辑须以
   "读源码内联"方式注入。
2. **pandas 在本 venv 未安装**,即便沙箱也不能假定其存在 → 沙箱高保真路径必须
   `try import pandas`,**装了才启用,没装行为完全等于现状**。
3. `numpy/pandas` 属 `pyproject` 的 `[data]` 可选组 → **harness 侧代码必须纯 stdlib**。
4. 已有结构化回传通道:子进程打印 `__AGENT_RESULT__:{json}`,`python_exec.call` 解析其
   `outputs` 列表(当前用于 image)。复用此通道回传 `table_summary`。
5. 小结果常态路径(`print('hello')` 等)**必须保持行为不变**。

## 3. 架构

新增纯函数模块 `src/data_analysis_agent/sampling/`(无 I/O、可测):

```
sampling/
├── __init__.py          # 导出 SamplingConfig / compact_result / render / 模型
├── config.py            # SamplingConfig + fidelity 档位(low/mid/high)映射
├── model.py             # ColumnSummary / TableSummary 数据类 + to_dict()
├── render.py            # L3:render_summary_dict() —— 单一渲染器(harness 侧)
├── text_summary.py      # 纯 stdlib:compact_result() / summarize_text() —— harness 兜底
└── sandbox_summary.py   # 仅 pandas/numpy/stdlib、无包内 import —— 注入沙箱
```

### 3.1 数据模型(`model.py`)

- `ColumnSummary(name, kind, count, null_count, stats: dict)`
  - `kind ∈ {numeric, categorical, bool, datetime, other}`
  - numeric `stats`: min/max/mean/std/quantiles{p…}/n_outliers
  - categorical `stats`: cardinality/top_k(list[[value,count]])/tail_truncated
  - datetime `stats`: min/max
- `TableSummary(n_rows, n_cols, columns, sample_rows, outlier_rows, sampling_method,
fidelity_level, notes, truncated)` + `to_dict()`
- 沙箱侧产出 **同形 dict**(因不能 import model),harness 用 `render_summary_dict` 统一渲染。

### 3.2 配置(`config.py`)

`SamplingConfig`(dataclass,全部有默认值,可不接即用):

| 字段               | 默认                 | 说明                          |
| ------------------ | -------------------- | ----------------------------- |
| `trigger_chars`    | 8000                 | ≈2k token;低于此不触发,原样传 |
| `fidelity_level`   | "mid"                | low/mid/high                  |
| `max_sample_rows`  | 20                   | 代表性明细行数                |
| `top_k`            | 10                   | 类别列高频项数                |
| `quantiles`        | (.01,.25,.5,.75,.99) | 数值列分位数                  |
| `stratify`         | "auto"               | auto=有低基数类别列则分层     |
| `include_outliers` | True                 | IQR 离群行追加                |
| `max_outlier_rows` | 5                    | 离群行上限                    |
| `seed`             | 0                    | 采样确定性                    |

`SamplingConfig.for_fidelity(level)` 映射:low(rows10/k5/3 分位)、mid(默认)、
high(rows40/k20/7 分位)。token 配比按 TAP4LLM 取**采样:统计 ≈ 5:5**。

### 3.3 接缝 1 — 沙箱高保真(`python_exec`)

- `_wrap_code` 读取 `sandbox_summary.py` 源码并内联到包装脚本顶部(stdlib-only 顶层导入,
  pandas/numpy 在函数内惰性导入,故无 pandas 也能安全 exec)。
- 注入 glue:
  - `agent_summarize(obj)` —— 供生成代码显式调用;计算摘要 → 经 `agent_result` 发
    `{"type":"table_summary","summary":<dict>}`,并向 stdout 打印一行确认。
  - **自动 hook**:用户代码执行后,若全局存在 `result` 且为 DataFrame/Series 且
    `rows > trigger_rows`(由 config 注入)→ 自动 `agent_summarize(result)`,**抑制**其
    原始 `print` 的洪流(改由摘要承载)。否则一切照旧。
  - 全程 `try/except (ImportError, Exception)`:任何失败回退到原始行为(plain print)。
- `summarize_dataframe(df, …) -> dict`:L0 形状/schema;L1 `describe`+`np.quantile`+
  `nunique`+`value_counts` top-k(精确);L2 分层蓄水池采样(有低基数类别列按其分层,
  否则简单随机)+ IQR 离群行追加。
- `python_exec.call` 解析 `outputs`:遇 `table_summary` → 用 harness 的
  `render_summary_dict()` 渲染为 `ToolResult.content`,结构化 dict 入 `metadata`。

### 3.4 接缝 2 — harness 纯 stdlib 兜底(`agent_loop._execute_tools`)

- 用 `compact_result(content, max_chars, config) -> tuple[str, bool]` 替换 446–449 盲截断:
  - `len(content) <= trigger_chars` → 原样返回。
  - 否则 `summarize_text(content, config)`:
    - 识别 pandas-print / CSV / Markdown 表 → 重解析行 → `summarize_table_rows`
      (样本统计:蓄水池采样行、Counter top-k、样本分位数)→ `TableSummary` → 渲染。
    - 非表格 → 行级蓄水池采样 + 近重复去重(轻量 shingle 哈希)+ 头尾保留 → 文本摘要渲染。
  - **任何异常 → 退回"头 + 尾"截断**(优于现状纯头截断),`was_compacted=True`。
- 覆盖一切超大字符串结果,全局安全网,且对 `python_exec` 已生成的摘要(通常 < 阈值)放行。

### 3.5 配置透传

`AgentConfig` 增 `sampling`(或等价字段);`__main__` 构造 `PythonAnalysisTool(sampling_config=…)`
与 `AgentLoop(..., sampling_config=…)`。默认值合理,**不接也能跑**(向后兼容)。

## 4. L3 输出规范(对抗过度自信 + context rot)

每个摘要块:块首一行元信息(行列数 / 采样方法 / fidelity);列统计紧凑表;代表性样本行表;
离群行;块尾显式声明:

> ⚠ 本视图为 N 行的**采样/摘要**;精确聚合(求和/计数/比率/去重)请在 pandas/SQL 内计算,
> **勿据样本推断总量**。

关键统计置于块首/尾(对抗 lost-in-the-middle)。

## 5. 鲁棒性 / 降级链

真实 DataFrame 精确摘要 →(无 pandas / 非 DataFrame)文本结构化摘要 →(解析失败)头尾截断
→ **绝不崩、绝不更差**。沙箱与 harness 两条路径独立,互为冗余。

## 6. 测试策略(TDD)

`tests/test_sampling.py`:

- **文本兜底(纯 stdlib,当前环境全验证)**:阈值放行;pandas-print/CSV/Markdown 表识别;
  蓄水池采样种子确定性;Counter top-k 正确;离群保留;垃圾输入优雅退回头尾截断;非表格行采样。
- **沙箱摘要(`pytest.importorskip("pandas")`)**:精确分位数;分层覆盖全类别;离群行在内;
  `summarize_dataframe` dict 形状正确。
- **渲染**:`render_summary_dict` 含采样警告、列统计、样本行。
- **接线**:伪造超大 tool result 经 `compact_result` 被压缩、小结果原样;
  `python_exec` 产出大 `result` 时 content 为摘要(pandas 可用时,否则该断言 skip)。

实现期尝试 `uv pip install -e ".[data,dev]"` 真跑高保真路径(实践检验);离线失败则
pandas 组测试 skip,文本路径仍 100% 通过。回归:现有 `pytest tests/ -v` 全绿、`ruff`、`mypy`。

## 7. 非目标(YAGNI)

- 不做 query-aware 语义采样 / embedding column grounding(报告阶段三,后续迭代)。
- 不做窗口外存储 + 按需回取(Managed Agents 思路,后续迭代)。
- 不引入任何第三方 sketch 库。
- 不改 5 级 `ContextCompressor`(消息列表层)本身;本次只在结果接缝做采样摘要。
