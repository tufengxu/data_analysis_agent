# Eval tasks (golden set)

Each `*.json` is one evaluation task for `evolution evaluate`:

```json
{
  "task_id": "unique_id",
  "input": "自然语言任务(可引用 fixtures/ 下的冻结数据集)",
  "dataset_fixture": "fixtures/sales.csv",
  "assertions": {
    "no_error_results": true,
    "min_tool_calls": 1,
    "tool_call_count_max": 8,
    "final_text_contains": ["..."],
    "final_text_regex": "..."
  }
}
```

**Assertions verify method/structure, never specific numbers** — data drifts, so
`留存率==12%`-style assertions would rot. Assert "no error / produced a chart /
ran the right tools" instead. Fixtures under `fixtures/` are frozen so reruns are
reproducible.

### 例外:`numeric_anchor`(仅冻结 fixture)

一项**带 `dataset_fixture` 的任务**,其数据冻结、不漂移,允许用 `numeric_anchor` 锚定一个
计算值——这是 ADR 0005 的**意图合规**例外,闭合「跑通但算错数」盲区。`eval_gate` 会拒绝任何
无 `dataset_fixture` 的 `numeric_anchor`。

```json
{
  "assertions": {
    "no_error_results": true,
    "required_tools": ["python_analysis"],
    "numeric_anchor": [
      {"value": 5000, "tolerance": 0.001, "label": "revenue 总额 = 5000"}
    ]
  }
}
```

- `value` / `tolerance`:必填,数值;命中窗口 = `abs(value) * tolerance`(value≈0 用绝对地板兜底)。
- `label`:可选,失败时回显,便于定位是哪个锚没命中。
- 机制:`check_assertions` 从本次运行捕获的 `python_analysis` 结果文本中正则解析数值
  (含负号捕获——`-5000` 不会被当成 5000 放过;连字符/区间/日期里的 `-` 不误绑为负号),
  要求至少一个落在窗口内。确定性浮点比较,无 LLM judge。
  已知不识别:科学计数法(`5e3`)、千分位逗号(`5,000`)、无前导零小数(`.5`)。

Seed the set with ~10–20 tasks covering the built-in skills, then fold in tasks
固化自 high-scoring trajectories (trajectory-as-test).
