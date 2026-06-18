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

Seed the set with ~10–20 tasks covering the built-in skills, then fold in tasks
固化自 high-scoring trajectories (trajectory-as-test).
