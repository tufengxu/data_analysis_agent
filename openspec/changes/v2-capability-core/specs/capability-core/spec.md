# capability-core 规范增量

## ADDED Requirements

### Requirement: 能力核心层依赖方向

系统**应**提供 `src/data_analysis_agent/capabilities/` 能力核心层，且其任何模块**不得** import `data_analysis_agent` 的 harness 内部模块（`agent_loop`、`session`、`state_machine`、`protocol`、`events`、`runtime`、`config`、`persistence`、`recovery`、`security`、`context`、`server`、`web`）或任何基座代码。该约束**应**由质量门中的自动检查强制。

#### Scenario: 依赖方向检查

- **WHEN** 质量门运行 drift 检查
- **THEN** `capabilities*` 对禁入模块的任何 import 导致失败
- **AND** 通过时输出检查通过证据

### Requirement: 能力契约

每个能力域**应**在 `contracts.py` 意义下声明：snake_case 名称、JSON Schema 输入、输出类别（text/structured/artifact）、权限类别（read_only/writes_artifacts/executes_code）与 fail-closed 错误语义（结构化 error dict，不外泄栈）。能力执行**应**不依赖 LLM 即可测试。

#### Scenario: fail-closed 错误

- **WHEN** 能力执行抛出任意异常
- **THEN** 返回 `{"ok": false, "error": {"code": ..., "message": ...}}` 而非崩溃调用方

### Requirement: 传输一致性

同一能力经进程内直调、MCP stdio、CLI 三种传输调用时**应**产出等价输出（content 与关键字段一致）；至少 3 个能力有自动化一致性测试。

#### Scenario: 三传输等价

- **WHEN** 对同一输入分别用进程内、MCP stdio、CLI 调用 `tabular_read_file`
- **THEN** 三者返回的规范化输出一致

### Requirement: 第三基座接入成本

接入第三个基座**应**只需新增一个 `harnesses/<name>/` 适配目录 + 装配清单；能力核心层与 serving 层零改动。仓库**应**含第三基座接入指南。

### Requirement: v1 不回归

v1 公共 API（`data_analysis_agent.sampling` 等）、`data-agent` CLI 与既有测试**应**保持可用与全绿；被物理迁移的包（sampling/causal/reporting）原路径**应**为纯 re-export shim。

## ADDED Requirements（sampling 迁移升级）

### Requirement: ToolResultCompactor 接缝契约

能力层**应**提供 harness 无关的 `ToolResultCompactor` 契约（输入：原始工具结果、长度预算、上下文压力信号、可选 SamplingConfig；输出：压缩内容、是否压缩、召回句柄、sampling_method、fidelity 标注）。`compact_result` 的现有语义（触发阈值、压力自适应接受率、超硬上限强制、失败降级不劣于现状）**应**作为参考实现保留。

#### Scenario: 超大结果压缩

- **WHEN** 输入超过 trigger 阈值的表格文本
- **THEN** 输出结构化摘要（含 sampling_method/fidelity 标注）且原文可经召回句柄分页取回

### Requirement: sandbox_summary 自包含约束

`capabilities/sampling/sandbox_summary.py` **不得** import 本包、不得使用 `from __future__`，pandas/numpy 惰性导入，输出兼容单一渲染器 shape；**应**有测试守护。

### Requirement: 基座侧可配置

fidelity 分级与触发阈值**应**暴露为基座侧可配置项（默认值与 v1 一致）；上下文压力信号**应**为适配层可传入的契约参数。

## ADDED Requirements（evolution 接线）

### Requirement: TrajectoryEvent 契约

能力层**应**定义 harness 无关的 `TrajectoryEvent`（覆盖每轮模型输入摘要、工具调用与结果摘要、上下文注入；记结构不记数值）。Pi 与 dsh 各**应**有一个事件流→契约的翻译器；进化管线**应**仍离线运行且能在新格式上跑。

#### Scenario: 轨迹校验

- **WHEN** 翻译器产出的 JSONL 交给 `evolution_verify_trajectory`
- **THEN** 契约校验通过且可转换为离线管线可消费的 TurnRecord

## ADDED Requirements（serving 与适配层）

### Requirement: MCP stdio + CLI 双传输

每个能力**应**经 MCP stdio server（官方 `mcp` SDK）与 CLI 子命令（`data-agent-capabilities`）可调用；两基座适配层**应**默认共用同一条 MCP stdio 通道。

### Requirement: 适配层仅胶水

`harnesses/*` **应**只含协议翻译（工具 schema 映射、结果编码、事件转发、权限对接）；**不得**内联能力实现（以 spawn 白名单 + 规模启发式自动检查并入质量门）。

### Requirement: 无 key 冒烟

每基座**应**提供不依赖真实 API key 的编排冒烟验证（桩模型/录制事件流等），真实 key E2E 路径写入运行指南并标注验证状态。
