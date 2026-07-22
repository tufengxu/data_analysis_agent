# 2026-07-22 overlay 域化接通（审计 §3.6 报告侧，Slice 2）

> 审计：`reporting/overlays.py` 的 `apply_overlay(template, domain)` 是**死代码**（零活路径
> 调用），域特化 caveat（retail/saas/finance/operations/risk/marketing）从不生效。
> `ReportContract` 也无 `domain` 字段承载业务域。本 slice 接通：contract 加 `domain`，
> `report_contract` 工具接 domain 输入 → `apply_overlay` 把域特化 required_caveats 叠到
> 选中的模板上，让模型按域建报告时知道要带哪些 caveat。

## Intent

让域 overlay 从死代码变活路径：用户/模型经 `report_contract(domain=...)` 声明业务域，
工具把域特化 caveat 主题（如 saas→mrr_churn、finance→currency_assumption）叠到模板的
`required_caveats`，surface 给模型建报告。

## Ground truth（已核实）

- **`reporting/overlays.py`** 已有完整机制：`DOMAINS=(retail,saas,finance,operations,risk,marketing)`、
  `_DOMAIN_OVERLAYS`（域→report_type→额外 caveat 主题）、`apply_overlay(template, domain)`
  （纯函数，`dataclasses.replace` 只追加 `required_caveats`，不改 section_roles/图族；未知域原样返回）。
- **`apply_overlay` 零活路径调用**（grep 全 src 仅 overlays.py 自身）—— 死代码，印证 audit。
- **`ReportContract`（contract.py:118-140）无 `domain` 字段**。
- **`report_contract` 工具**：`select_template(contract.report_type)` 选模板，`template.to_dict()`
  入 metadata（report_contract.py:167-170）。无 domain、无 apply_overlay。
- **`select_template`** 对 AD_HOC/未知 report_type 返回 None（report_contract.py:166）。
- ReportContract 是 `Serializable` frozen dataclass → 加带默认值的 `domain: str|None=None`
  additive 向后兼容（`from_dict` 只读存在字段，已由 PR #16 exclusions 字段验证同款安全性）。

## 设计决策

1. **ReportContract 加 `domain: str | None = None`**（additive，向后兼容）。位置：靠近其它
   可选上下文字段（如 `business_grain`）。
2. **report_contract 工具接 `domain` 输入**（可选 string）：设到 contract.domain；选中模板后
   若 domain 非空则 `template = apply_overlay(template, domain)`，再 to_dict 入 metadata。
   - domain 未知（不在 DOMAINS）→ `apply_overlay` 原样返回（no-op），不报错（advisory，宽松）。
   - select_template 返回 None（AD_HOC）→ 不 apply（无模板可叠），domain 仍记在 contract 上。
3. **不硬校验 domain**：advisory 字段，未知域 no-op 比报错更友好（模型可能传近义词）。
4. **不改 html_report**：模板 required_caveats 是 advisory（模型建报告时参考），report_contract
   surface 即可；QA 不强制域 caveat（域 caveat 是建议性增强，非结构 block）。保持 overlay 纯函数、
   只追加 caveat 主题的原设计。

## 文件范围

- `src/data_analysis_agent/reporting/contract.py`：`ReportContract` 加 `domain: str | None = None`。
- `src/data_analysis_agent/tools/report_contract.py`：input_schema 加 `domain`；`call()` 设
  contract.domain + `apply_overlay`；import apply_overlay。
- 新 `tests/test_overlay_domain.py`（或并入 test_report_tools）：domain 流经 contract、
  apply_overlay 叠 caveat、未知域 no-op、AD_HOC(None 模板)不崩。
- **不改** drift_rules / overlays.py（机制已就绪）/ AGENTS.md/CLAUDE.md。

## 验收

- `report_contract(domain="saas", report_type="daily_kpi", ...)` → metadata.template.required_caveats
  含 `mrr_churn`（saas daily_kpi overlay）；contract.domain == "saas"。
- 未知域 `"games"` → template 与无 domain 一致（no-op）；不报错。
- AD_HOC（select_template 返回 None）+ domain → metadata 无 template 键，contract.domain 仍记；不崩。
- ReportContract 加 domain 后既有 reporting 往返测试仍绿（additive 安全）。
- `apply_overlay` 不再是死代码（report_contract 是活调用点）。
- 质量门全绿；独立审查 blocking/major 清零。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_overlay_domain.py tests/test_report_tools.py -q
```

## 显式不在本 slice

- html_report 渲染时二次 apply（advisory，无需）。
- QA 强制域 caveat（域 caveat 是建议增强，非 block）。
- rephrase CJK/否定（Slice 3）。
