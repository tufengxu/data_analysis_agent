# Design: v2-capability-core

## 1. 四层结构与依赖方向（强制）

```
能力核心层 capabilities/（Python，Harness 无关）
        ↓
统一暴露层 capabilities/serving/（MCP stdio server + CLI）
        ↓
基座适配层 harnesses/pi/、harnesses/deepseek/（仅胶水）
        ↓
Agent 组装（preset：系统提示、工具注册、技能注入、事件接线）
```

依赖规则（写入 `scripts/drift_rules.py` + 新增 `scripts/check_harness_adapters.py`，均并入质量门）：

- `data_analysis_agent.capabilities*` 禁 import：`agent_loop / session / state_machine / protocol / events / runtime / config / persistence / recovery / security / context / server / web / __main__`（规范 3.1 明令的五 harness 模块 + v1 装配/表现层）。`tools/`、`kernel/`、`artifacts/`、`skills/`（数据化部分）不在禁入表——它们本就是能力实现，tabular 域委托之。
- `capabilities.sampling*` 额外镜像 v1 `sampling` 旧规则（禁 `tools/agent_loop/protocol/skills/security/context`），保持纯 stdlib + 可选 pandas。
- `sandbox_summary.py` 自包含约束原样保留（禁 import `data_analysis_agent`、无 `from __future__`、pandas 惰性导入），有测试守护。
- 适配层（TS）不得内联能力实现：只准经 `data-agent-capabilities` 入口（MCP/CLI）调 Python；检查脚本以「spawn 目标白名单 + 代码规模上限」启发式判定。

## 2. 基座 API 现场核实记录（R9；核实日期 2026-08-26）

### 2.1 Pi Agent Core（核实源：github.com/earendil-works/pi、pi.dev、npm registry）

| 事实 | 值 |
|---|---|
| 仓库 | `earendil-works/pi`（`badlogic/pi-mono` 已重定向至此），MIT，默认分支 `main`，最新 tag `v0.84.3`（2026-08-24） |
| npm 包 | `@earendil-works/pi-ai` / `@earendil-works/pi-agent-core` / `@earendil-works/pi-coding-agent`，均 **0.84.3**（`@mariozechner/*` 已弃用） |
| Node | engines `>=22.19.0`；本机 v24.15.0 ✓ |
| 扩展机制 | TS 模块 default-export `function (pi: ExtensionAPI)`；`pi.registerTool({name, description, parameters: TypeBox, execute(toolCallId, params, signal, onUpdate, ctx) => ({content, details})})`；`pi.on("tool_call")` 可拦截/改写入参；`pi.on("tool_result")` 可返回补丁 `{content, details, isError}` 改写工具结果；`pi.on("context")` 注入/改写上下文；`pi.on("session_before_compact")` 定制压缩；`pi.on("before_agent_start")` 改系统提示 |
| MCP | **无内置 MCP 客户端**；仅社区扩展 `pi-mcp-adapter`（nicobailon，读 `mcp.json`） |
| 事件面 | `session.subscribe()` / 扩展 `pi.on(...)`：`turn_start/message_end/turn_end/agent_end` 等；JSON 模式 `pi --mode json`（stdout JSONL）；会话日志 `~/.pi/agent/sessions/**/*.jsonl`（version 3 树形 entries） |

### 2.2 DeepSeek Harness / dsh（核实源：github.com/deepseek-ai/deepseek-harness、npm registry、PyPI）

| 事实 | 值 |
|---|---|
| 状态 | 公开、MIT、**dev preview**（README 明示会有破坏性变更）；默认分支 `master`，最后 push 2026-08-21 |
| npm | 官方 CLI `@deepseek-ai/dsh` **0.1.1-rc.2**（bin `dsh`）；根 `engines: ^22.19.0 \|\| >=24.0.0`（本机 Node 24.15 ✓，发布包未强制） |
| 插件 | Cordis：`export function apply(ctx)` + `export const inject = ['tools', ...]`；`ctx.tools.register(defineTool({name, description, parameters: {prop: {type, required, description}}, execute(args, exec)}))` |
| MCP | **官方 client 插件** `@deepseek-ai/dsh-mcp-client`，`cordis.yml` 配 stdio（`command/args/env/cwd`），工具命名 `mcp__<serverName>__<rawName>`；**无 server 模式**（核实范围内未发现） |
| 工具结果改写 | `tools/post-execute` waterfall（accept/block/replace/add context）+ `ToolDefinition.finalizeContent` + `ctx.toolResultPruner` |
| 轨迹 | `session/event` 总线（`agent/*`、`tools/pre-execute|execute|post-execute`、`tools/result`、`approval/*`、`compaction/*`）+ append-only JSONL 持久化（`turn/start|end`、`tool/call`、`tool/result`、`assistant/message` …） |
| 审批 | `tools/pre-execute` → allow/deny/ask；`ctx.approval`（缺省 fail-closed → deny）；`ctx.permissionPresets` |
| Python SDK | PyPI `deepseek-harness-sdk` 0.1.1rc1（`DeepSeekHarness(...).run(...)`，上下文管理器，`cordis=` 可传配置；捆绑 Node 运行时 wheel） |

## 3. 关键设计决策

### D1 传输选型：MCP stdio 为主通道，CLI 为兜底/调试通道

- **能力侧**：`capabilities/serving/` 用官方 **`mcp` Python SDK**（FastMCP，stdio）实现 server。理由：dsh 官方 mcp-client 是完整 MCP 实现，自造协议实现握手/分页/能力协商风险高；官方 SDK 与之是久经测试的配对。新增 extra `serving`（`mcp>=1.2`），不进主依赖（v1 用户零影响）。
- **dsh 侧**：直接用官方 `@deepseek-ai/dsh-mcp-client` 插件（`cordis.yml` 一条 stdio 配置指向 `data-agent-capabilities mcp`）。「同一份能力」字面成立。
- **Pi 侧**：Pi 无内置 MCP；选**官方 MCP TypeScript SDK 客户端**（`@modelcontextprotocol/sdk` 的 `Client` + `StdioClientTransport`，command 固定字面量 `data-agent-capabilities`）而非依赖社区 `pi-mcp-adapter`。理由：(a) 避免引入第三方 dev-preview 基座扩展依赖（R8 锁定面最小）；(b) mcp.json 全局发现机制与本项目演示自包含诉求不符；(c) 官方 SDK 客户端久经测试、适配层代码量最小。两适配器共用同一客户端封装（`harnesses/shared/`），保证「两基座共用同一条 MCP stdio 通道调用能力」。（实施修订 2026-08-26：原计划自写 ~150 行最小客户端，落地时改用官方 SDK 客户端——spawn 全部发生在 SDK 内部，适配层零进程构造代码，安全面更小。）
- **深集成例外**（规范 3.1 允许）：Pi 的 `tool_result` 压缩接缝需要拿到工具结果原文，扩展内经同一 MCP 连接调用 `sampling_compact` 能力（不产生第二实现）。
- **CLI**：`data-agent-capabilities call <tool> --input '<json>'` 等子命令，兜底 + 调试 + 传输一致性测试通道。

### D2 能力契约（`capabilities/contracts.py`）

每个能力 = `CapabilitySpec`：`name`（snake_case，如 `tabular_read_file`）、`description`、`input_schema`（JSON Schema dict）、`output_kind`（`text | structured | artifact`）、`permission`（`read_only | writes_artifacts | executes_code`）、`error_semantics`（fail-closed：结构化 error dict，`{"ok": false, "error": {"code", "message"}}`，异常不外泄栈）。执行入口统一 `execute(name, input) -> dict`（含 `ok`、`content`、`metadata`）。LLM 无关、纯函数可测。

### D3 各域能力面（经 MCP/CLI 暴露的工具名）

- **tabular**（委托 v1 `tools/` + `kernel/`，async 调 `.call()` 并取 `ToolResult`）：`tabular_read_file`、`tabular_data_profile`、`tabular_data_quality`、`tabular_nl_query`、`tabular_join_plan`、`tabular_metric_contract`、`tabular_python_exec`（持久内核：能力侧维护 KernelManager 进程，MCP server 生命周期内跨调用存活；崩溃/超时重启并显式报告状态丢失——内核语义在能力层保留）。
- **reporting**：`reporting_render_chart`、`reporting_render_html`（抽取 v1 `tools/html_report.py` 生成核心进 `capabilities/reporting/html_report.py`，产物目录限定/转义/`</` 逃逸/PLAN deny 语义原样）、`reporting_report_need/context/contract`（复用纯领域层 `reporting/`）。
- **causal**：`causal_analyze`（建模/图/识别 = 「分析」子能力）与 `causal_estimate`（效应估计/实验 readout = 「推断」子能力）两个显式入口 + `causal_qa`、`causal_report`（复用物理迁移后的 `capabilities/causal/`）。
- **sampling**：`sampling_compact_result`（= `ToolResultCompactor.compact` 参考实现：输入原始文本/预算/压力信号 → 压缩文本 + `was_compacted` + 召回句柄）、`retrieve_result`（分页召回，两基座均为模型可调工具）。
- **evolution**：`evolution_record_event` / `evolution_verify_trajectory`（契约写入/校验；翻译器在适配层）。

### D4 `ToolResultCompactor` 接缝契约（规范 5.2/5.3）

```python
@dataclass(frozen=True)
class CompactRequest:
    content: str; max_chars: int
    context_pressure: float = 0.0          # 适配层传入（0..1）
    config: SamplingConfig | None = None   # fidelity 分级 + 阈值可配置
    result_id: str | None = None           # 预分配句柄（默认生成）

@dataclass(frozen=True)
class CompactResult:
    content: str; was_compacted: bool; result_id: str | None
    sampling_method: str; fidelity_level: str; notes: list[str]

class ToolResultCompactor(Protocol):
    def compact(self, request: CompactRequest) -> CompactResult: ...
```

参考实现 = 现 `compact_result`（触发阈值、压力自适应接受率、超硬上限强制、失败降级不劣于现状）+ 原文入 ResultStore 产生召回句柄。v1 `agent_loop` 的接缝改为调同一实现（行为不变）。

- **Pi 接入**：`pi.on("tool_result")` 补丁 `content`（压力信号由扩展从会话 token 估计传入）。
- **dsh 接入**：`tools/post-execute` waterfall replace 结果（压力信号取 session 近似值）。
- **ResultStore**：MCP server 进程内单例，落 `~/.daa/capabilities/result-store/`（`DAA_CAPABILITIES_HOME` 可覆盖），`retrieve_result` 经同一 server 分页。

### D5 `TrajectoryEvent` 契约（D6）

```json
{"schema": "daa.trajectory.v1", "event": "turn_start|model_input|tool_call|tool_result|context_injection|turn_end",
 "ts": "...Z", "session_id": "...", "turn": 3, "harness": "pi|dsh|v1", "data": {…}}
```

JSONL，一行一事件，落 `~/.daa/trajectories/v2/<session_id>.jsonl`。**记结构不记数值**沿用 ADR 0004（内容字段只存摘要/长度，不存原文数值）。Pi 翻译器吃 `pi.on` 事件流；dsh 翻译器吃 `session/event`（或读 JSONL journal）。能力侧提供 `evolution_verify_trajectory`（schema 校验）与 TrajectoryEvent → `TurnRecord` 转换器，喂既有离线 `evolution/` 管线（P6：管线在新格式上可跑）。

### D6 v1/v2 共存与迁移方式

- `sampling/`、`causal/`、`reporting/` 物理搬迁 + 原路径 re-export shim：v1 调用方零改动，4 个采样测试文件与既有测试全绿为硬门槛。
- tabular 委托而非搬迁：v1 `runtime.build_registry` 不动。
- 新 console script：`data-agent-capabilities`；v1 `data-agent`/`data-agent-web` 不变。
- manifest：`docs/ARCHITECTURE.md` 同步登记全部新模块与 shim。

### D7 质量门扩展（不使现有门退化）

1. drift 新规则（能力层依赖方向，见 §1）。
2. 新脚本 `scripts/check_harness_adapters.py`：适配器 spawn 白名单（仅 `data-agent-capabilities`/`node` 自身）+ `harnesses/*/src` 规模上限（每文件 <500 行，总量启发式）+ 禁止出现第二实现特征（如 Python 业务代码内联）。
3. TS 检查：`harnesses/check-ts.sh` 对每个有 `node_modules` 的适配器跑 `tsc --noEmit`；无 Node 环境时显式输出 SKIP 说明（polyglot 仓库的宽限），本机验收时必须实际执行且通过。
4. `pyproject.toml` 加 extra `serving` + console script → `uv lock` 更新。

### D8 桩模型冒烟（无真实 key）

- 两基座适配器各带 `smoke` 脚本：不起 LLM，直接驱动「MCP client → 能力 server → 全工具链」的编排路径（fixture CSV → profile → chart → html → causal），断言产物与压缩/召回生效；轨迹翻译器用录制事件流 fixture 回放验证。真实 key E2E 写入运行指南并标注「未验证」。

## 4. 目录布局

```
src/data_analysis_agent/capabilities/
    __init__.py  contracts.py
    tabular/  reporting/  causal/  evolution/  sampling/
    serving/  (__init__.py  registry.py  mcp_server.py  cli.py)
harnesses/
    shared/mcp-client.ts          # 两适配器共用的最小 MCP stdio 客户端
    pi/      (package.json tsconfig.json src/ README.md smoke/)
    deepseek/(package.json tsconfig.json src/ cordis.example.yml README.md smoke/)
    check-ts.sh
examples/v2/  (fixtures + demo 脚本)
docs/ARCHITECTURE.md（manifest 同步） docs/V2_RUNBOOK.md docs/THIRD_HARNESS_GUIDE.md
```

## 5. 风险与应对（承接任务规格 §10）

| 风险 | 应对 |
|---|---|
| 两基座 dev preview / API 漂移 | 版本 pin（Pi `@earendil-works/*` 0.84.3；dsh `@deepseek-ai/dsh` 0.1.1-rc.2）+ 本设计 §2 记录核实日期；适配层薄化 |
| dsh mcp-client 与自建 server 兼容性 | 用官方 `mcp` SDK 做 server；P3 传输一致性测试先在本机跑通 dsh mcp-client ↔ server 再上适配器 |
| 采样迁移破坏 v1 | 物理 move + shim，先跑 4 个采样测试文件再动升级；分步验证 |
| Pi 无内置沙箱/审批 | 能力层 fail-closed（产物目录限定、executes_code 声明）+ 适配层映射 Pi permission-gate 模式（read_only 放行，writes_artifacts 限产物目录） |
| polyglot 拖垮质量门 | TS 检查独立脚本 + 有条件执行；Python 侧命令不变 |

## 附录:第 8 节验收清单核验记录(2026-08-26)

**A. 架构与可移植性**
- [x] 四层结构与依赖方向落地;`drift` 步含 capabilities 依赖方向规则与 `check_harness_adapters`(适配器 spawn 白名单/规模/入口限制),最终门运行通过为证。
- [x] 能力核心层零 import 基座/harness/v1-harness 符号:`scripts/checks.py check_import_rules` 输出 NONE 为证。
- [x] `docs/THIRD_HARNESS_GUIDE.md`:接入第三基座 = 新增 `harnesses/<name>/` 适配目录 + 装配清单,能力层与 serving 零改动。

**B. 能力层与暴露层**
- [x] 六大能力域(五域目录 + serving)均有 CapabilitySpec(schema/权限/错误语义)与不依赖 LLM 的测试(test_capability_{contracts,causal,reporting,tabular,evolution,serving}.py);tabular/reporting/causal 委托 v1 已测试实现(语义对齐由同源实现保证)。
- [x] 传输一致性:tabular_read_file / causal_analyze / sampling_compact_result 三能力「进程内 vs MCP vs CLI」断言 ok/content/data 等价(`tests/test_capability_serving.py::TestTransportConsistency`,12 passed);真实 stdio 分帧 `examples/v2/smoke_stdio_mcp.py` PASS。

**C/D. 两个基座 Agent**
- [x] 同一端到端任务链(fixture CSV → 读表 → 画像 → 图表 + 自包含 HTML(真实路径产物)→ 因果分析/推断):`examples/v2/demo_e2e.py` 11/11 PASS,走与两基座相同的 MCP stdio 通道;dsh 侧另有真实模型 E2E(2026-08-26,见 §3 记录)。
- [x] 无 key 冒烟:Pi 26/26 PASS、dsh 19/19 PASS(`npm run smoke`);真实 key E2E:dsh 已验证;Pi 未验证(无 ANTHROPIC_API_KEY,运行方式 V2_RUNBOOK §4);`harnesses/deepseek/python/agent.py` 未验证(PyPI 网络不可达;API 签名经 wheel 源码核实)。
- [x] 采样压缩接缝与轨迹翻译器在两基座「实际生效」:dsh 真实运行 32KB→1.3KB 压缩 + 轨迹落 `~/.daa/trajectories/v2/`;Pi 侧接缝逻辑经冒烟全链路验证(缺真实模型编排一环)。

**E. 采样压缩**
- [x] `ToolResultCompactor` 契约 + 参考实现在 `capabilities/sampling/compactor.py`;Pi tool_result 钩子与 dsh tools/post-execute 均接入(同一实现,经 sampling_compact_result 能力)。
- [x] 超大结果:两适配器 smoke 各注入 2000 行表格 → was_compacted/sampling_method=table-summary/fidelity 标注 + `retrieve_result` 分页回取原文首行;dsh 真实运行同验。
- [x] v1 采样 4 个测试文件全绿(随全量 1278+ passed);`sandbox_summary` 自包含约束由 `TestSandboxSelfContainmentGuard` 守护(物理迁移后的真实文件)。

**F. 质量门**
- [x] `python scripts/quality_gate.py` 全绿(含 drift 新规则、manifest 同步、ts 步 checked=3 failed=0)。

**G. 文档**
- [x] OpenSpec 变更提案(design 决策 + 基座 API 核实记录 §2 + 传输选型理由 D1);ARCHITECTURE.md v2 章节并入 manifest 体系;V2_RUNBOOK.md 全部命令在本机(darwin/arm64)执行过并记录结果。
