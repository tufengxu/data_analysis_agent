# 2026-07-21 Web Workbench — Live Agent Run + Event Codec (Wave 2, Slice 1)

> P1-3 的第一刀:把 agent 的实时事件流接到浏览器。后续 slice 再做上传/授权/审批/反馈 UI。
> 现有 `web/app.py` 是报告塑造 workbench(确定性,无 LLM),live 端点是 stub;本 slice 在
> 新 `server/` 包里实现真正的 agent 驱动(web 保持纯 reporting 表现层不变)。

## Intent

证明 P1-3 的核心契约:**Web run 走与 CLI 完全相同的 runtime/tool registry**(roadmap Milestone 1B
退出标准)。浏览器发一个 NL 请求 → 后端用 `AgentRuntime.from_config` 跑 → 把 agent 事件流
经稳定的 event codec 以 SSE 推给浏览器。localhost-only。

## Ground truth(已核实)

- `web/app.py`:FastAPI 报告 workbench,端点 `/api/report/{need,context,contract}` `/api/qa`
  `/api/template` `/artifacts/{name}` `/api/feedback`;`/api/run/stream` 与 `/api/report/rerun`
  是 stub(返回 not_implemented)。
- `scripts/drift_rules.py`:`web` 禁依赖 `runtime`/`config`/`session`/`agent_loop`/...;这是刻意
  (web = reporting 表现层)。driving runtime 属另一层 → 新 `server/` 包。
- `AgentRuntime.from_config(config, *, client=, analysis_paths=, project=, ...)` 是唯一组合根;
  `runtime.session.send(query)` 是异步事件流生成器。`ApprovalHandler` 是 `(tool_name, tool_input) -> bool`。
- 事件类型(`events.py`):RequestStart/StreamText/StreamThinking/ToolUse/ToolResult/ToolProgress/
  StateChange/Usage/SystemMessage/Error/Complete。

## 设计决策

1. **新 `server/` 包**,不是放宽 web 的 drift:
   - `server/event_codec.py`:`encode(event) -> dict` 把 AgentEvent 映射成稳定 JSON(roadmap §P1-3.5
     事件 codec 契约)。纯函数,确定性,无 LLM。
   - `server/app.py`:`create_app(config?)` → FastAPI;`POST /api/run/stream` 用 SSE
     (`StreamingResponse` media_type `text/event-stream`)跑 runtime 并推 `data: {json}\n\n`。
   - `server/__main__.py`:uvicorn 入口,强制 `host="127.0.0.1"`(roadmap §P1-3.2;公开暴露需显式
     unsafe 旗标 + warning,本 slice 不做 unsafe 旗标)。
   - 复用 `web/static` 的 artifact 预览路由思路;本 slice 起一个最小 `server/static/index.html`
     (SSE 连接 + 流式文本 + 工具事件列表,无样式拋光)。
2. **同一 runtime**:`/api/run/stream` 内 `AgentRuntime.from_config(config, client=<real or injected>,
analysis_paths=body.paths, project=...)`;复用 CLI 的全部工具/技能/权限。request 结束 `await runtime.shutdown()`。
3. **审批(MVP 取舍)**:本 slice 不做审批 UI。默认**不设 preset**(= 今天 CLI 的全允许行为),
   保证端到端跑通;`local_safe` 下 ASK 无 handler 会 fail-closed(正确,审批 UI 是后续 slice)。
4. **drift 规则**:加 `server` 包:允许 `server→runtime/web/workspace/config/events`;禁 `server→agent_loop`
   (走 runtime 接缝,不直接耦合循环)。`web` 规则不动。
5. **测试**:`event_codec` 单测(每类事件 → 期望 dict);server smoke 用 fake client
   (`AgentRuntime.from_config(config, client=fake)` 注入)→ `/api/run/stream` 推到 Complete。

## 文件范围

- 新 `src/data_analysis_agent/server/__init__.py`、`event_codec.py`、`app.py`、`__main__.py`、`static/index.html`
- `scripts/drift_rules.py`:加 `server` 规则
- `docs/ARCHITECTURE.md`:manifest 登记 server 模块
- 新 `tests/test_event_codec.py`、`tests/test_web_server.py`

## 验收

- `/api/run/stream` POST `{query, paths}` → SSE 流,每行 `data: <json>`;事件经 codec;流到 Complete 后关。
- Web run 用与 CLI 同一 `AgentRuntime.from_config`(同一工具/技能集)。
- `python -m data_analysis_agent.server` 绑 `127.0.0.1`(非 0.0.0.0)。
- `event_codec` 每类 AgentEvent 输出稳定 dict(单测锁定字段名)。
- 质量门绿;独立审查零 blocking/major。

## 显式不在本 slice(后续 slice)

上传 + 目录授权确认(P1-3.3)、审批 UI(P1-3.7)、反馈 UI(P1-3.8)、报告意图表单 + draft QA 面板(P1-3.6)、
artifact 预览路由接入、unsafe 公开绑定旗标、project 工作区选择器。
