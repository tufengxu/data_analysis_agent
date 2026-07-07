# 报告交付优化 · Wave 8 实现计划 — Web Workbench(FastAPI MVP)

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 Task 实现。

> **Baseline:** `docs/superpowers/specs/2026-07-06-report-delivery-optimization-design.md`(§5.4 Web Workbench、§8 Wave 8、§11 MVP 缓解)
> **Depends on:** Wave 1-7(reporting 域层 + QA + 工具 + html_report v2 + 模板 + eval gate)
> **Scope:** Wave 8。新建 `web/` 包(FastAPI + uvicorn,`[web]` 可选依赖)。**§11 MVP**:确定性面板(用户需求/报告意图/数据·过程上下文/QA/artifact 预览/模板选择),消费 reporting 域层。
> **defer**(spec §11 "rich editing can wait"):live-agent 事件流(需运行 agent + LLM,重)、富编辑表单、反馈遥测写入。

## 战略决策(框架选型)

- **FastAPI(Starlette+uvicorn)**:async 匹配 agent 事件流、pydantic>=2 天然集成(project 已用)、WebSocket/SSE 支持、Python web 事实标准。
- **新依赖**:`[web]` 可选 extra(fastapi、uvicorn),同 `[data]`/`[dev]` 模式;不进核心 dependencies(保持 CLI 用户的零 web 依赖)。
- **drift**:`web` 是表现层,消费 `reporting` + stdlib + fastapi;**禁依赖** agent_loop/runtime/protocol/evolution/telemetry/memory/tools(同 reporting 的纯方向,但 web 在 reporting 之上)。

## Goal

让用户经浏览器交互式塑造报告(spec §5.4):输入请求 → 看到显式/隐式需求 + 不确定点 → 上传/选数据 → 看上下文 → 建契约 → 看 section 骨架(模板)→ 跑 QA 看 readiness → 预览 HTML artifact。**全程确定性**(消费 Wave 1-7 纯函数,无需 LLM)。

## Architecture

`src/data_analysis_agent/web/`:

| 模块                | 责任                                                                           |
| ------------------- | ------------------------------------------------------------------------------ |
| `app.py`            | `create_app(artifact_dir=None) -> FastAPI`:装配路由 + 静态资源 + artifact 路由 |
| `routes.py`         | API 端点(见下)                                                                 |
| `schemas.py`        | Pydantic 请求/响应模型(薄包装 reporting 域类的 to_dict)                        |
| `__main__.py`       | `python -m data_analysis_agent.web` 启动 uvicorn                               |
| `static/index.html` | 单页 vanilla JS UI(无 build step;面板 + fetch 各端点)                          |

**端点**(全消费 reporting 纯函数,确定性):

- `GET /` → 提供 `static/index.html`(workbench UI)
- `POST /api/report/need` `{raw_request}` → UserNeed(explicit/implicit/uncertainties/clarification_needed)
- `POST /api/report/context` `{profile, events?, sensitive_mode?}` → DataContext + ProcessContext
- `POST /api/report/contract` `{question, user_need?, data_context?, ...}` → ReportContract(field_sources + refs + missing_context)
- `POST /api/qa` `{document, artifact_exists?, ...}` → QAReport(readiness + findings)
- `GET /api/template?text=...` → match_template → ReportTemplate(section_roles + 默认图族 + caveats)
- `GET /artifacts/{name}` → 安全预览 artifact_dir 下的 HTML 报告(path containment,只允许 .html;**沙箱属性**:`Content-Type: text/html` + `Content-Disposition: inline`)

**安全模型**:

- artifact 路由:`artifact_dir` 注入;`name` bare-name + `is_relative_to(artifact_dir)` 重检 + 仅 `.html`(镜像 html_report 的路径防护)。
- web 层不执行模型代码、不跑 agent;只调 reporting 纯函数 → 无代码注入面。
- 前端:所有 API 返回 JSON(UI 渲染);artifact 预览是已转义的 html_report 产物(本身安全)。

## Tech Stack

新增 `[web]` extra:`fastapi>=0.110`、`uvicorn>=0.27`。Pydantic v2(已有)。前端 vanilla JS(无 npm)。TestClient(fastapi 自带,基于 httpx——已有)做测试。

## Global Constraints

- **质量闸**:每 Task 末全绿(`[web]` 装上后)。
- **`[web]` 安装**:首次跑前 `uv pip install -e ".[web]"`(关沙箱);web 测试 `importorskip("fastapi")` 跳过若未装(同 pandas 可选模式)。
- **manifest**:新增 `web/app.py`/`routes.py`/`schemas.py`/`__main__.py` 4 行(`static/` 与 `__init__.py` 不登记)。
- **drift**:加 `web` who/forbid 条目(禁 agent_loop/runtime/protocol/evolution/telemetry/memory/tools/skills;**允许 reporting + fastapi**)。
- **向后兼容**:新包独立;CLI(`__main__.py`)不动;不装 `[web]` 不影响既有。
- **确定性**:端点只调 reporting 纯函数;无 LLM/时间/随机。
- **安全**:artifact 路径防护(同 html_report);前端 JSON 渲染(无服务端模板注入)。

## File Structure

| 文件                                            | 责任                                  | 动作 |
| ----------------------------------------------- | ------------------------------------- | ---- |
| `pyproject.toml`                                | `[web]` extra(fastapi/uvicorn)        | 改   |
| `scripts/drift_rules.py`                        | `web` who/forbid 条目                 | 改   |
| `docs/ARCHITECTURE.md`                          | manifest 4 行 + 依赖规则 1 行         | 改   |
| `src/data_analysis_agent/web/__init__.py`       | 包标记                                | 新建 |
| `src/data_analysis_agent/web/app.py`            | create_app FastAPI 装配               | 新建 |
| `src/data_analysis_agent/web/routes.py`         | API 端点                              | 新建 |
| `src/data_analysis_agent/web/schemas.py`        | Pydantic 请求/响应模型                | 新建 |
| `src/data_analysis_agent/web/__main__.py`       | uvicorn 启动入口                      | 新建 |
| `src/data_analysis_agent/web/static/index.html` | vanilla JS workbench UI               | 新建 |
| `tests/test_web_workbench.py`                   | TestClient 各端点 + artifact 路径防护 | 新建 |

**回滚**:全增量(新包 + pyproject extra + drift + manifest)。`git revert` 即可;不装 `[web]` 则 web 包是死代码,不影响 CLI。

---

## Task 1: [web] extra + drift 规则 + web 包骨架 + app 装配

**Files:** Modify `pyproject.toml`/`drift_rules.py`; New `web/{__init__,app,schemas}.py`; manifest; New `tests/test_web_workbench.py`(本 Task 起步)。

- [ ] Step 1: `pyproject.toml` 加 `[web]` extra(`fastapi>=0.110`,`uvicorn>=0.27`)+ console script `data-agent-web = data_analysis_agent.web.__main__:main`。
- [ ] Step 2: `uv pip install -e ".[web]"`(关沙箱)。
- [ ] Step 3: drift 加 `web` 条目;manifest 加 4 行 + 依赖规则行。
- [ ] Step 4: 写失败测试 `test_web_app_serves_ui` + `test_web_health`(create_app → TestClient GET / → 200 含 workbench)。
- [ ] Step 5: 实现 `app.create_app` + `schemas.py`(基础请求模型)+ 空 `static/index.html`。
- [ ] Step 6: gate → PASS。

## Task 2: API 端点(need/context/contract/qa/template)

**Files:** New `web/routes.py`; 追加 `schemas.py`; 追加测试;完善 `static/index.html` 面板。

- [ ] Step 1: 写失败测试:每端点 POST/GET 一个合法入参 → 200 + 响应含期望字段(need.implicit.likely_report_type;context.candidate_date_columns;contract.field_sources;qa.readiness;template.section_roles)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `routes.py`(5 端点全调 reporting 纯函数)+ schemas 完善 + UI 接各端点。
- [ ] Step 4: gate → PASS。

## Task 3: artifact 安全预览 + 最终闸 + 独立审查 + commit

- [ ] Step 1: 写失败测试:`test_artifact_preview_serves_html`(写一个 html 到 artifact_dir → GET /artifacts/x.html → 200 text/html)、`test_artifact_rejects_escape`(GET /artifacts/../evil → 404/拒)、`test_artifact_rejects_non_html`(GET /artifacts/x.json → 拒)。
- [ ] Step 2: Run → FAIL。
- [ ] Step 3: 实现 `/artifacts/{name}`(bare-name + is_relative_to + .html)。
- [ ] Step 4: 最终 `quality_gate` 全绿。
- [ ] Step 5: 独立代码审查闭环(§2.9):spawn 全新只读 reviewer,重点:drift web 合规、artifact 路径防护、确定性、前端 JSON 渲染无注入、向后兼容、新依赖合理。修复 → 复审至零遗留。
- [ ] Step 6: Commit。

## 不在本计划内(spec §11 "rich editing can wait")

- live-agent 事件流(需运行 agent + LLM,重;后续接入 WebSocket + 一个 run 端点)。**§11 "event stream" 诠释**:指 post-hoc 过程上下文摘要(`/api/report/context` 已交付);live WebSocket 流式随 correction+rerun 一同 defer(spec §8 权威任务列表不要求 live 事件)。
- 富编辑表单(改 intent/口径 后重跑——需 agent 回路)。§8 acceptance (2) "user can correct intent before rerun" → 本 MVP **只读展示**(PARTIAL)。
- 反馈遥测写入(反馈标签持久化;当前仅 UI 展示)。§8 acceptance (3) "feedback feeds telemetry" → DEFERRED。**§10 release criteria 含 feedback,故本 MVP 不可称 "release candidate"**;commit 须称 "Wave 8 MVP slice"。

### §8 Wave 8 acceptance 保真表

| acceptance                                   | 状态              | 说明                                                    |
| -------------------------------------------- | ----------------- | ------------------------------------------------------- |
| (1) 用户看到 draft/needs_review/ready 的原因 | **MET**           | /api/qa + QA 面板                                       |
| (2) 用户可在 rerun 前修正 intent/口径        | **PARTIAL**(只读) | 展示 need/contract;correction+rerun 需 agent 回路,defer |
| (3) 反馈进 telemetry                         | **DEFERRED**      | 反馈标签持久化 defer;UI 仅展示                          |

## Self-Review(独立计划评审 APPROVE-WITH-FIXES,10 条全采纳)

1. **drift web 禁入表完整 21 项**(镜像 reporting):agent_loop/protocol/runtime/evolution/telemetry/memory/tools/skills/session/kernel/context/security/sampling/persistence/state_machine/events/config/recovery/jsonl_store/artifacts/**main**/**reporting 例外允许**。
2. **reporting 禁入表加 `data_analysis_agent.web`**(防 reporting→web 循环)。
3. **Pydantic typed schemas**:响应模型用 `Literal` 枚举(readiness/report_type/severity/block_role)+ `list[...]`(非 tuple)。
4. **前端 XSS 姿态**:API JSON 经 `textContent`/`createElement` 渲染(**不用 innerHTML**);artifact 预览经 `<iframe sandbox="allow-scripts">`(源隔离)。CSP header 作 follow-up(local MVP 可缓)。
5. **`web/__main__.py` graceful**:`try: import fastapi except ImportError: sys.exit("install with: pip install -e .[web]")`。
6. **`/artifacts` 完整 5 项校验**(镜像 html_report:NUL/Path.name/点开头/点空格结尾/Windows 保留)+ `.html` only + is_relative_to;测试含编码遍历(`%2e%2e`/`%2F`)。
7. **`/api/report/contract` 路径**:web 直接调 reporting(parse_user_need/build_data_context/link_to_contract_fields/ReportContract 构造),**不 import tools**(drift 禁)——与 report_contract 工具逻辑一致但独立实现。
8. **`/api/qa` 测试含 draft vs ready 双 fixture**(两种 readiness 极端)。
9. **drift web 规则测试**:构造 web 模块 import agent_loop → drift 捕获。
10. **commit 称 "Wave 8 MVP slice"**(非 complete);§8 (2)(3) 部分实现须明示。
