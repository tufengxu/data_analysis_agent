# 2026-07-22 rephrase 启发式升级（审计小项，Slice 3）

> 审计小项：「rephrase 启发式升级（CJK/否定变体；现人审门+泄露守卫兜底）」。
> `telemetry/feedback.py:looks_like_rephrase` 是 implicit "bad" 信号（快速跟进 + 否定词 →
> 视为纠正），驱动 metric_definition light-confirm 不前进。当前 `_NEGATION_MARKERS` 覆盖窄，
> 且**裸 "no" 子串误报**（"no" ⊂ "note/know/now"）。本 slice 扩 CJK/否定变体 + 修英文词边界。

## Intent

提升 `looks_like_rephrase` 的 recall（多抓真实纠正）和 precision（少误报中性查询），
尤其 CJK 纠正/否定变体和英文短词的子串误报。仍由人审门 + 泄露守卫兜底，故保持低假阳倾向。

## Ground truth（已核实）

- **`telemetry/feedback.py`**：`_NEGATION_MARKERS = (不对/不是/重新/重来/错了/不行/再试,
no/wrong/redo/again/not what)`；`REPHRASE_GAP_SECONDS=60.0`；`looks_like_rephrase(next_input,
gap_seconds)` = `gap≤60 且 next_input.lower() 含任一 marker`（纯子串匹配）。
- **裸 "no" 误报**：`"note this".lower()` 含 "no" → True（假阳）。同理 "again" ⊂ "against"。
- **CJK 覆盖窄**：缺「不准确/不正确/错的/有错/重做/重算/反过来/应该是/其实是/等等/不可以」等常见纠正。
- **调用点**：`session.py:103 looks_like_rephrase(...)` → 决定 `memory_adjudicator(not is_rephrase)`
  （rephrase → metric 不前进 + FeedbackRecord(kind="rephrase")）。
- **既有测试** `tests/test_telemetry.py:test_looks_like_rephrase`：「不对,重新分析」(5s) True、
  「redo this please」(5s) True、(300s) False、「继续分析下一个区域」(5s) **False（neutral，不能破）**。
- `_NEGATION_MARKERS` 仅 feedback.py 自用，测试不导入 → 可安全重构。

## 设计决策

1. **CJK 子串匹配**（无词边界概念；marker ≥2 字符保持具体）：扩展为
   `不对/不是/不准确/不正确/不可以/不行/错了/错的/有错/重新/重来/重做/重算/再试/再算/反过来/应该是/其实是/等等`。
   - 全部 ≥2 字符，避免单字（如「不」「别」）过宽误报。
   - 与既有 neutral 测试「继续分析下一个区域」无交集（已逐一核对）。
2. **英文词边界匹配**（regex `\b...\b`）：扩展为
   `no/nope/wrong/redo/again/try again/not right/not what/that's wrong/that is wrong/wait no`。
   - **修裸 "no" 误报**：`\bno\b` 不命中 "note/know/now"；`\bagain\b` 不命中 "against"。
   - `\b` 对 CJK 无意义，故 CJK 走子串、英文走词边界，两路。
3. **保留 `looks_like_rephrase(next_input, gap_seconds)` 签名与语义**（gap 门 + 任一 marker 命中）；
   既有 4 条断言全绿。
4. **不过度扩**：不加单字 CJK、不加无词边界英文短词（no/ok 等单独词仍走词边界）。保持
   「deliberately crude, low false-positive」原注释意图。

## 文件范围

- `src/data_analysis_agent/telemetry/feedback.py`：拆 `_CJK_NEGATION_MARKERS`（子串）+
  `_ENGLISH_NEGATION_MARKERS`（词边界 regex）；`looks_like_rephrase` 两路匹配。
- 新增/扩展测试：`tests/test_telemetry.py` 加 CJK 变体命中 + 英文词边界（"note this" False，
  "no, wrong" True）+ 新英文变体 + neutral 不破。
- **不改** drift_rules / session.py（调用点不变）/ AGENTS.md/CLAUDE.md。

## 验收

- CJK 变体：「不准确，重算」「这个有错，重做」「不正确，改一下」「再改改这个」(5s) → True。
  （歧义 opener「等等/应该是/反过来/再算」审查后刻意不收——见设计决策 1。）
- 英文词边界：「note this change」(5s) → **False**（修误报）；「no, that's wrong」(5s) → True；
  「try again」(5s) → True；「against my expectation」(5s) → False（again 词边界）。
- 既有 4 条断言仍绿（含 neutral「继续分析下一个区域」False）。
- gap 门不变：>60s → False。
- 质量门全绿；独立审查 blocking/major 清零。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_telemetry.py -q
```

## 显式不在本 slice

- rephrase 的语义判断（相似度/difflib 比对 prev vs next）——当前只看 next+gap+marker，升级不引入
  相似度（会显著抬高复杂度与假阳面，且人审门已兜底）。
- gap 阈值调优（60s 保持）。
