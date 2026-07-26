# 2026-07-22 evidence_refs 逐 ref 解析到真实产物（审计 §3.6 / P0-3，Slice 1）

> 审计 §3.6 / P0-3：`ReportBlock.evidence_refs` 从不被解析校验到真实计算产物——模型可填
> 看似合理但不存在的 ref，溯源形同虚设（违反 ADR 0002 anti-entropy）。当前 QA
> (`_check_evidence_refs_nonempty`) 只查 ref 非空字符串，`html_report` 硬编码
> `artifact_exists=True`，从不真正解析。本 slice 把 evidence_refs 接到真实产物边界。

## Intent

让报告 QA 在渲染前解析每条 evidence_ref：能落到真实 ArtifactStore 产物文件或已知
ResultStore result_id 的判 resolved；形似产物引用却解析不到的判 fabricated（HIGH finding，
溯源形同虚设的出口）。纯描述性 ref（散文）不惩罚（避免假阳）。

## Ground truth（已核实当前源码）

- **`reporting/qa.py`** 纯函数（docstring：「无 LLM、无 I/O、无时间/随机依赖」）。`run_qa`
  签名 `(document, *, artifact_exists=False, n_points_by_chart=None, n_observations_by_chart=None)`。
  `artifact_exists` 只是「HTML 产物是否生成」的单 bool（False → `artifact.missing` blocker），
  **与 evidence 无关**。`_check_evidence_refs_nonempty`（qa.py:206）只查 ref 非空/非空白。
- **`ReportBlock.evidence_refs: tuple[str, ...]`**（contract.py:209）—— ref 是模型自由填的**字符串**
  （result_id / artifact 文件名/路径 / 或纯描述性散文）。EvidenceRef 结构体（contract.py:143）是
  另一类型，block 不用它。
- **`artifacts.py:ArtifactStore`** 只有 `save_image`，**无索引、无 exists API**——产物就是 `artifact_dir`
  里的文件（chart_render 写 JSON、ArtifactStore 写图片、html_report 写 HTML）。
- **`sampling/result_store.py:ResultStore`** 有 `_index: dict[result_id, rec]`（持久化 index.jsonl）。
  `get(rid)` 有 TTL 淘汰**副作用**（过期会 `_drop`）——不适合作只读存在性探测。**需加 `contains()`**。
- **`tools/html_report.py:726`** `run_qa(gate_doc, artifact_exists=True)`：硬编码 True，从不解析。
  HtmlReportTool 有 `self.artifact_dir`，**无 result_store**（build_registry 只传 artifact_dir+echarts_src）。
- **`runtime.build_registry`** 已有 `result_store` 参数（line 101），已传给 `RetrieveResultTool`(124)；
  HtmlReportTool(126) 未传。→ 注入 result_store 是 additive 一行接线（有 RetrieveResultTool 先例）。

## 设计决策

1. **QA 保持纯函数**：解析能力以 `evidence_resolver: Callable[[str], bool | None] | None = None`
   **注入**（对齐 `n_points_by_chart` 注入风格，QA 自身不做 I/O）。
   - 三态返回：`True`=resolved（真实产物/result_id）；`False`=fabricated（形似产物引用却解析不到）；
     `None`=descriptive（纯描述，无法/不校验）。
   - `None`（resolver 未提供，如 QA 单测）→ 跳过解析检查（向后兼容，不假阳）。
2. **新 QA 检查 `_check_evidence_refs_resolve`**：resolver 返回 `False` 的 ref → **HIGH** finding
   （code `evidence.unresolved`，带 block_id + suggested_fix「填入真实 artifact 文件名/result_id
   或移除该 ref」）。`True`/`None` 不报。**不替代** `_check_evidence_refs_nonempty`（空 ref 仍是它管）。
3. **`ResultStore.contains(rid) -> bool`**：只读存在性，**无淘汰副作用**
   (`rid in self._index and (now - created_at <= ttl_seconds)`)。
4. **resolver 策略**（html_report 构建，用 result_store + artifact_dir）：
   - `result_store` 可用且 `contains(ref)` → `True`（已知 result_id）。
   - ref 形似产物文件（有已知扩展名 `.json/.png/.jpg/.jpeg/.svg/.html/.htm/.csv/.tsv/.xlsx/.parquet`
     或绝对路径）→ **限定 artifact_dir 子树**解析：`(artifact_dir/ref).resolve()` 若逃出子树（`/etc/hosts`、`../x.json`、`~`）→ **False 且不查存在性**（杜绝文件存在性 oracle + 系统文件冒充证据）；在子树内则查 `.exists()` → True/False。
   - 否则（纯描述性散文，无形似标志）→ `None`（不校验、不惩罚）。
   - **严重度 = HIGH**（非 BLOCKER），与同域 `evidence.empty_ref` 一致；fabricated ref → `readiness=NEEDS_REVIEW`（带 unresolved-evidence 徽章渲染，**不拒渲染**——只拒 BLOCKER 是既有 QA 哲学，模型自由文本 ref 不承担假阳阻断交付的风险）。
5. **HtmlReportTool 注入 `result_store=None`**：build_registry 传入；None 时 resolver 仍可用
   artifact_dir 路径解析（result_id 路径跳过）。`_call_v2` 把 resolver 传给 `run_qa`。
6. **scope 收口**：只做 evidence_refs **产物解析**（backlog 原文「ArtifactStore/ResultStore 接到
   渲染边界」）。**不做**审计 P0-3 的「数值校验」（chart option 数值与 kernel 输出一致——独立的
   更难问题，另立 slice）。

## 文件范围

- `src/data_analysis_agent/reporting/qa.py`：`run_qa` 加 `evidence_resolver` 参数；
  新增 `_check_evidence_refs_resolve(document, resolver)`；接入 `run_qa` 检查链。
- `src/data_analysis_agent/sampling/result_store.py`：加 `contains(rid) -> bool`（只读）。
- `src/data_analysis_agent/tools/html_report.py`：`__init__` 加 `result_store=None`；
  `_call_v2` 构建 resolver 传给 `run_qa`（两处 QA 调用：gate 拒绝 + 渲染后）。
- `src/data_analysis_agent/runtime.py`：`build_registry` 把 `result_store` 传给 `HtmlReportTool`。
- 新 `tests/test_evidence_resolution.py`（或并入 test_reporting_qa）：QA 三态 + resolver None 跳过 +
  result_store.contains + html_report 端到端（tmp artifact_dir + result_store，fabricated ref → DRAFT 拒绝；
  真实 ref → 渲染）。
- **不改** drift_rules（qa/html_report/result_store 既有依赖关系不变）；**不改** AGENTS.md/CLAUDE.md。

## 验收

- QA：resolver 返回 False 的 ref → HIGH `evidence.unresolved` finding（带 block_id）；True/None 不报；
  resolver=None → 无解析 finding（向后兼容）。
- fabricated 形似文件 ref（`fake.json`/`/abs/missing.png`）→ HIGH；纯描述性 ref（`Q3 revenue`）→ 不报。
- result_store.contains：存在且未过期 → True；不存在或已过期 → False；无淘汰副作用（不 _drop）。
- html_report 端到端：document 含 fabricated ref → `readiness=NEEDS_REVIEW`（带 unresolved-evidence 徽章渲染，**不拒渲染**）；真实 artifact_dir 文件/known result_id ref → 无 unresolved finding。
- 路径安全：ref `/etc/hosts`、`../escape.json`、`~/.zshrc` → resolver 返回 False（**不查存在性**，非 True）；只有 artifact_dir 子树内文件可判 True。
- 既有 reporting QA/html_report 测试全绿（resolver 默认 None = 跳过，行为不变）。
- 质量门全绿；独立只读子 Agent 审查 blocking/major 清零。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_reporting_qa.py tests/test_html_report_v2.py -q
```

## 显式不在本 slice

- 审计 P0-3 的**数值校验**（chart option 数值与 kernel 输出一致 / 数值带来源标注）——独立更难 slice。
- EvidenceRef 结构体（contract.py:143）接入 block.evidence_refs（当前 block 用 str，本 slice 仍用 str）。
- overlay 域化（Slice 2）、rephrase CJK/否定（Slice 3）。
