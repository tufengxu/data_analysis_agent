# Proposal: v2-capability-core — 双 Harness 基座上的能力化重建

## Why

v1（`src/data_analysis_agent/`）的六大能力（tabular / reporting / causal / evolution / sampling 压缩 / serving 化）全部与自有 harness（`agent_loop` / `session` / `protocol` 等）耦合在同一包内，无法快速落到其他 Agent 基座。本次以「新增式重建」在既有 hatchling 包内建成 **Harness 无关的能力核心层（capability core）**，并以 **Pi Agent Core** 与 **DeepSeek Harness（dsh）** 两个外部基座落地为可移植性的实证检验。v1 保持可用、行为与测试不回归。

## What Changes

1. **能力核心层**：新增 `src/data_analysis_agent/capabilities/`（`contracts.py` + `tabular/` `reporting/` `causal/` `evolution/` `sampling/` 五个能力域目录 + `serving/`）。迁移策略：
   - `sampling/`、`causal/`、`reporting/`（纯 stdlib 领域层）**物理搬迁**进能力层，原路径变为纯 re-export shim（v1 公共 API 与行为不变）；
   - `tabular/` 以契约包装 + 委托 v1 `tools/` + `kernel/` 实现（规范仅禁 `agent_loop/session/state_machine/protocol/events`，`tools/`、`kernel/` 本就是能力实现）；
   - `evolution/`（能力侧）= harness 无关 `TrajectoryEvent` 契约 + 轨迹写入/校验 + → `TurnRecord` 转换器；进化管线本体仍离线运行。
2. **统一暴露层（serving）**：每个能力经 **MCP stdio server** 与 **CLI 子命令**（新 console script `data-agent-capabilities`）两种传输调用。MCP server 用官方 `mcp` Python SDK（新增 extra `serving`）。
3. **基座适配层**：`harnesses/pi/`（TS 扩展：`pi.registerTool` 代理 + 自带最小 MCP stdio 客户端 + `pi.on("tool_result")` 压缩接缝 + 轨迹记录）与 `harnesses/deepseek/`（Cordis 插件 + 官方 `dsh-mcp-client` 走 MCP + `tools/post-execute` 压缩接缝 + `session/event` 轨迹翻译）。适配层只有胶水。
4. **采样压缩升级（最高优先级资产）**：`ToolResultCompactor` 接缝契约成为能力层一等公民；fidelity 分级/阈值暴露为基座侧可配置；上下文压力信号泛化为契约参数；ResultStore 分页召回在两基座暴露为 retrieve 工具。
5. **质量门扩展**：新增依赖方向自动检查（能力层禁 import harness 符号；适配器禁内联能力实现 + 只准经 `data-agent-capabilities` 入口调 Python）与 TS 侧 `tsc --noEmit` 检查，均并入 `scripts/quality_gate.py`，不使现有门退化。
6. **文档**：v2 架构并入 `docs/ARCHITECTURE.md` manifest、两 Agent 运行指南、第三基座接入指南、`examples/v2/` 演示脚本。

## Capabilities Impacted

| 能力域 | 来源（v1） | 去向（v2） |
|---|---|---|
| tabular | `tools/{file_read,data_profile,data_quality,nl_query,join_planner,metric_contract}.py`、`kernel/` | `capabilities/tabular/`（契约 + 委托） |
| reporting | `tools/{html_report,chart_render,report_contract}.py`、`reporting/` | `capabilities/reporting/`（物理迁移 + 抽取 HTML 生成核心） |
| causal | `causal/` + `skills/causal_skill.py` | `capabilities/causal/`（物理迁移） |
| evolution | `telemetry/`、`memory/`、`skills/`、`evolution/` | `capabilities/evolution/`（契约化接线；管线本体留 v1 离线） |
| sampling | `sampling/` + `context/compression.py` 接缝 | `capabilities/sampling/`（物理迁移 + `ToolResultCompactor` 契约化） |

## Non-Goals

- 不迁移/重写 v1 `server/`、`web/` UI；
- 不做分布式/云端部署，不引入数据库或常驻服务；
- 不在能力层做基座特化分支；
- 不做进化管线算法升级（仅契约化与接线）；
- 不删改 v1 死代码。

## Success Criteria（DoD 摘要）

完整清单见任务规格第 8 节；核心硬指标：

- 四层依赖方向落地且有自动检查并入质量门；
- 六大能力域有契约与不依赖 LLM 的测试；tabular/reporting/causal 对齐 v1 已测试语义；
- ≥3 个能力的「进程内直调 vs MCP stdio vs CLI」传输一致性测试；
- 两基座各自完成同一端到端任务（fixture CSV → 分析 → 图表 + 自包含 HTML 报告 → 因果结论），且调用同一份能力实现；
- 每基座有不依赖真实 API key 的编排冒烟；
- 超大工具输出在两基座被压缩为结构化摘要且可经 retrieve 分页取回；v1 采样 4 个测试文件全绿；
- `python scripts/quality_gate.py` 全绿 + TS 侧类型检查通过。
