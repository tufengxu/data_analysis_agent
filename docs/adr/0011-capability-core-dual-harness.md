# 0011 — v2 能力核心层与双基座适配(四层结构、单一 MCP 通道、接缝契约)

- 状态: Accepted (2026-08-27)
- 变更提案: `openspec/changes/v2-capability-core/`(proposal/design/tasks + 验收核验附录)

## 背景

v1 的六大能力(表格分析 / 可视化报告 / 因果分析与推断 / 自进化 / 采样上下文压缩)全部
与自有 harness(`agent_loop` / `session` / `protocol` 等)耦合在同一包内,无法快速落到
其他 Agent 基座。任务规格要求:在本仓内建成 Harness 无关的能力核心层,同一份能力代码
(零改动)经适配层接入 Pi Agent Core 与 DeepSeek Harness 两个基座,且 v1 行为与测试
零回归。

## 决策

**四层结构,依赖只准向下**(drift 强制,`scripts/drift_rules.py` + `checks.py`):

```
能力核心层 capabilities/(contracts + tabular/reporting/causal/evolution/sampling)
        ↓ 统一暴露层 capabilities/serving/(MCP stdio server + CLI data-agent-capabilities)
        ↓ 基座适配层 harnesses/{shared,pi,deepseek}/(仅胶水)
        ↓ Agent 组装(preset:系统提示 + 工具注册 + 接缝配置)
```

1. **能力契约**:`contracts.py` 的 `CapabilitySpec`(snake_case 名 / JSON Schema /
   permission:read_only|writes_artifacts|executes_code / fail-closed 错误码词表)+
   `CapabilityRegistry.dispatch` 单一执行包络——MCP、CLI、进程内直调三传输同源,
   一致性由 `tests/test_capability_serving.py` 断言。
2. **迁移方式按域选择**(迁移优先于重写):
   - sampling:**物理迁移**至 `capabilities/sampling/`,v1 路径为纯 re-export shim
     (任务规格 5.2 明令;sandbox_summary 自包含约束保留并有测试守护);
   - causal/reporting:**委托式迁移**——纯领域层本体即实现(ADR 0009/0010 的纯 stdlib
     不变量由既有 drift 规则继续守护),能力层只加契约注册;
   - tabular:**委托 v1 tools/kernel**(KernelHolder 持久内核跨调用存活,内核重启/
     降级语义留在原实现);
   - evolution:契约化(`daa.trajectory.v1` TrajectoryEvent,记结构不记数值,延续
     ADR 0004;digest 字段格式强制)+ →TurnRecord 转换器,离线管线本体不动。
3. **传输选型**:两基座默认共用同一条 MCP stdio 通道。server 用官方 Python `mcp` SDK
   (2.x 低层 API,动态 schema 来自 CapabilitySpec);dsh 用官方 `dsh-mcp-client` 插件;
   Pi 无内置 MCP,用官方 TypeScript SDK 客户端(`harnesses/shared/`,spawn 固定字面量
   程序 `data-agent-capabilities`,经 PATH 解析)而非社区 pi-mcp-adapter(锁定面最小、
   演示自包含)。CLI 为兜底/调试通道。
4. **ToolResultCompactor 接缝**:harness 无关契约(`CompactRequest{content, max_chars,
   context_pressure, config, result_id, tool_name}` → `CompactResult{content,
   was_compacted, result_id, sampling_method, fidelity_level}`);参考实现 = v1
   `compact_result` 语义原样(阈值/压力自适应增益门/硬上限强制/失败降级);v1
   `agent_loop` 与两基座适配层(Pi `tool_result` 钩子 / dsh `tools/post-execute`
   waterfall)共用同一实现。ResultStore 支持**多进程共享**(index mtime 检测重载,
   best-effort)——两基座各自的能力 server 进程写同一 store 目录互见。
5. **适配层仅胶水,机器强制**:`checks.check_harness_adapters` 限制适配器只能经
   `data-agent-capabilities` 入口调 Python(spawn 白名单)、单文件 ≤500 行、禁内联
   能力实现;质量门新增 `ts` 步(`harnesses/check-ts.sh`,无 Node 环境显式 SKIP)。
6. **版本 pin 与现场核实**(核实日期 2026-08-26,记录于变更提案 design.md §2):
   Pi `@earendil-works/pi-coding-agent@0.84.3`;dsh `@deepseek-ai/dsh@0.1.1-rc.2`
   (dev preview,预期破坏性变更,适配层薄化以缩小爆破半径)。

## 验证基线(2026-08-26/27)

- 质量门七步全绿(pytest 1279 passed,含 v1 全量不回归;CI 同步绿)。
- 端到端:`examples/v2/demo_e2e.py` 11/11 PASS;真实 stdio 分帧冒烟 PASS。
- dsh 真实 E2E 已验证(模型经 `mcp__daa__*` 完成任务,32KB 表格结果压缩至 1.3KB,
  轨迹落 `~/.daa/trajectories/v2/`);Pi 真实 E2E 未验证(缺 ANTHROPIC_API_KEY,
  运行方式见 `docs/V2_RUNBOOK.md` §4)。

## 实现偏离(相对任务规格草案,均记录于 design.md)

1. TS 客户端由「自写 ~150 行最小实现」改为**官方 MCP TypeScript SDK**——spawn 全部
   发生在 SDK 内部,适配层零进程构造代码,安全面更小。
2. causal/reporting 由「物理迁移 + shim」改为**委托式迁移**——纯领域层已受 drift
   守护,搬迁只增加噪声;契约与暴露才是能力层增量。
3. `mcp` Python SDK 落地为 **2.x**(2.1.1):低层 `add_request_handler` 为
   `(ctx, params)` 签名、params 模型用 `PaginatedRequestParams`/
   `CallToolRequestParams`、构造参数 snake_case——与 1.x FastMCP 教程不同,升级时留意。
