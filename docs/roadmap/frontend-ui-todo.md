# Frontend UI TODO — Web Workbench（交 Kimi-K3 实现与优化）

> 本文件汇总所有**前端 UI** 工作，供 Kimi-K3 专门实现/优化设计。后端（server/SSE/event codec、
> 报告塑造端点、artifact 预览路由）已就绪，前端在其上构建。创建：2026-07-21。
> 权威路线图：`docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md`（P1-3）。

## 现状：已有两套 FastAPI 表现层（需统一成一个产品 UI）

### A. Live-agent server（Wave 2 Slice 1，PR #11，已就绪）

- 代码：`src/data_analysis_agent/server/`
- `server/app.py`：`create_app(config, *, client=None)` → FastAPI
  - `GET /` → 返回 `server/static/index.html`（当前是**最小 SSE 消费页**，无样式拋光）
  - `POST /api/run/stream` → **SSE**（`text/event-stream`），body：`{query: str, paths: list[str], project?: str}`
    - `paths` 必须是绝对路径且非空（fail-closed，否则返回 error 帧）；空/空白会被拒
    - 无 `ANTHROPIC_API_KEY` 时返回 400
- `server/__main__.py`：`python -m data_analysis_agent.server`，强制绑 `127.0.0.1:8000`
- **这是 live agent 入口**：走 `AgentRuntime.from_config`，与 CLI 同源。

### B. 报告塑造 workbench（Wave 8，更早，已就绪）

- 代码：`src/data_analysis_agent/web/app.py`（注意：与 server 是**不同**的包）
- 确定性端点（无 LLM）：
  - `POST /api/report/need` `{raw_request}` → UserNeed
  - `POST /api/report/context` `{profile, events?, sensitive_mode?}` → DataContext + ProcessContext
  - `POST /api/report/contract` `{question, user_need?, data_context?, process_context?, report_type?, audience?, language?}` → ReportContract
  - `POST /api/qa` `{document, artifact_exists?, n_points_by_chart?, n_observations_by_chart?}` → `{readiness, findings[]}`
  - `GET /api/template?text=` → 匹配的报告模板（404 若无匹配）
  - `GET /artifacts/{name}` → HTML 报告产物预览（仅 `.html`，路径防护）
  - `POST /api/feedback` `{tags[], comment?, readiness?}` → 追加到 `feedback.jsonl`
- `web/static/index.html`：当前的"报告塑造器"UI（也是早期最小版）

> **统一建议**：产品态应是**一个 workbench**，把 A 的 live run + B 的报告塑造/QA/artifact 预览/反馈
> 合并到一个前端。是否合并 server/ 与 web/ 两个 FastAPI app（mount 或合一）由前端实现时定。

## SSE 事件 wire 契约（前端消费的稳定格式）

`POST /api/run/stream` 的每一帧是 `data: <json>\n\n`，json 来自 `server/event_codec.py`（字段名冻结）：

| type            | 字段                                                                        | 来源事件                                                           |
| --------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `request_start` | `model_id`, `turn_count`                                                    | RequestStartEvent                                                  |
| `stream_text`   | `text`（增量片段，需前端拼接）                                              | StreamTextEvent                                                    |
| `tool_use`      | `tool_use_id`, `tool_name`, `parameters`                                    | ToolUseEvent                                                       |
| `tool_result`   | `tool_use_id`, `tool_name`, `content`, `is_error`, `artifacts[]`            | ToolResultEvent                                                    |
| `state_change`  | `previous_state`, `new_state`, `reason`（`AWAITING_CONFIRMATION` = 等审批） | StateChangeEvent                                                   |
| `usage`         | `input_tokens`, `output_tokens`                                             | UsageEvent                                                         |
| `error`         | `error`(str), `is_recoverable`                                              | ErrorEvent（含 server 侧异常帧）                                   |
| `complete`      | `terminal_reason`, `final_text`                                             | CompleteEvent（终止帧）                                            |
| `system`        | `event`(类名)                                                               | StreamThinking/ToolProgress/SystemMessage/未来类型（兜底桶，勿丢） |

参考实现：`server/static/index.html` 里的 fetch + ReadableStream + `indexOf('\n\n')` 分帧解析。

## 待实现的前端工作项（按 roadmap P1-3）

### 1. 数据源与上传（P1-3.3）

- [ ] 上传 CSV/XLSX/XLS/Parquet 到 project 的 `uploads/`（后端需补上传端点——见"后端缺口"）
- [ ] 显式本地文件/目录授权；**目录授权需二次确认**（子文件全可读）
- [ ] 运行前展示已授权路径列表
- [ ] 当前 `paths` 是手输绝对路径（`server/static/index.html` 有逗号分隔输入框），需升级为文件选择器/拖拽

### 2. Live run 主面板（P1-3.5 / P1-3.6）

- [ ] 面板：数据源 / 提问输入 / 实时回答（流式 markdown 渲染）/ 工具时间线 / 产物 / 反馈
- [ ] 工具卡片：name / params / 状态 / 耗时 / 结果摘要 / 错误态 / 产物链接
- [ ] 终止态：terminal_reason / token 用量（`usage` + `complete`）/ 生成文件
- [ ] 流式文本拼成完整 markdown 并渲染（`stream_text` 增量拼接）

### 3. 报告意图表单 + draft QA（P1-3.6，接 B 的报告端点）

- [ ] 报告意图表单：audience / cadence / period / comparison baseline / report type / 输出语言
- [ ] live report plan 预览（渲染前）
- [ ] draft report QA 面板：展示 `/api/qa` 的 findings（missing 口径/caveat/unsupported claim/weak chart/missing next action），readiness 三态（draft/needs review/ready）

### 4. 审批 UI（P1-3.7）

- [ ] `state_change.new_state == "AWAITING_CONFIRMATION"` 时弹审批：展示 tool_name + params，用户 allow/deny
- [ ] **超时 = deny**（不 allow）—— 当前后端在 `local_safe` 预设下 ASK 无 handler 会 fail-closed；前端需接 approval 通道（见"后端缺口"）

### 5. 反馈 UI（P1-3.8）

- [ ] Good / Bad / Rephrase(needs-fix) 按钮
- [ ] 可选短评输入
- [ ] 接 B 的 `/api/feedback`（tags + comment + readiness）或 telemetry 反馈通道

### 6. 产物与 artifact 预览（P1-3.9）

- [ ] 产物列表（来自 `tool_result.artifacts[]`）
- [ ] HTML 报告在新标签打开（接 B 的 `/artifacts/{name}` 路由）
- [ ] 只展示 workspace/artifacts 内文件，禁止任意路径

### 7. 统一与拋光

- [ ] 决定 server/ 与 web/ 是否合并为一个 app（mount 或合一）
- [ ] 设计系统：配色/排版/暗色模式（参考 `~/.agents` 或品牌色）
- [ ] 响应式布局

## 后端缺口（前端实现时可能需要后端配合——记录，非本 TODO 范围）

> 这些是前端工作会暴露的后端待补项，发现时回写 `docs/roadmap/backlog.md`：

- [ ] **上传端点**：`POST /api/upload`（multipart）落 project `uploads/` —— 当前只有 `paths` 传绝对路径
- [ ] **审批通道**：`local_safe` 下 ASK 的 approval handler 需接前端（当前 server 未接 approval handler，mutator 会 fail-closed）—— 需把 server 的 `from_config` 接一个 Web approval handler（state_change → 前端 → 回传 allow/deny）
- [ ] **project 选择器**：前端选 project id 传 `project` 字段（后端已支持）
- [ ] unsafe 公开绑定旗标（P1-3.2，非 UI，安全相关，留 backlog）

## 约束

- **localhost-only**：server 强制 `127.0.0.1`；前端不要假设公网。
- **wire 契约稳定**：`server/event_codec.py` 的字段名冻结；新增字段只能 additive。
- **安全**：artifact 预览只能指向 workspace/artifacts 内；目录授权需确认；paths 不可为空。
- **两套 index.html**：`server/static/index.html`（live）与 `web/static/index.html`（报告塑造），统一时二选一或合并。

## 参考文件

- `src/data_analysis_agent/server/app.py`、`server/event_codec.py`、`server/static/index.html`
- `src/data_analysis_agent/web/app.py`、`web/schemas.py`、`web/static/index.html`
- `docs/superpowers/specs/2026-07-21-web-workbench-run-design.md`（Wave 2 Slice 1 spec）
- `docs/superpowers/plans/2026-07-07-report-delivery-wave8.md`（Wave 8 报告 workbench plan）
- 路线图 P1-3：`docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md` §P1-3
