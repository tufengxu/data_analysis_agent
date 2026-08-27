# 第三基座接入指南

接入一个新的 Agent 基座 = **新增 `harnesses/<name>/` 适配目录 + 装配清单**;能力核心层
(`src/data_analysis_agent/capabilities/`)与 serving 层**零改动**。以下是全部步骤。

## 1. 你需要的东西(全部现成)

- **MCP stdio 通道**:子进程拉起 `data-agent-capabilities mcp`(PATH 解析;JSON-RPC 2.0,
  换行分帧)。工具清单与 schema:发 tools/list 请求;调用:发 tools/call 请求。
  结果文本是 DAA envelope JSON:`{ok, capability, content, data, artifacts, metadata, error}`。
  参考实现:`harnesses/shared/capability-client.ts`(官方 MCP SDK)。
- **CLI 兜底通道**:`data-agent-capabilities call <name> --input '<json>'`。

## 2. 适配目录模板(`harnesses/<name>/`)

```
harnesses/<name>/
  package.json        # 依赖:@modelcontextprotocol/sdk(或基座自带 MCP 客户端)
  tsconfig.json       # strict;include ../shared/capability-client.ts
  src/extension.ts    # 或该基座的插件/扩展形态:工具代理 + 结果压缩接缝 + 轨迹翻译
  src/translate.ts    # 基座事件 → daa.trajectory.v1(纯函数,可单测)
  src/preset.ts       # 装配清单:系统提示 + 工具注册名映射 + 接缝配置
  smoke/smoke.ts      # 无 key 冒烟(模板照抄 harnesses/pi/smoke/smoke.ts)
  README.md           # 安装/冒烟/真实 key 路径(未验证项标明)
```

## 3. 三个接缝(必须全部接入)

| 接缝 | 契约 | 基座侧需要的能力 |
|---|---|---|
| 工具暴露 | tools/list + tools/call 代理(19 个能力) | 注册自定义工具 + JSON Schema 直传 |
| 结果压缩 | 超阈值(默认 8000 字符)→ 调 `sampling_compact_result`(传 `result_id`=`toolCallId`,自估 `context_pressure` 0..1)→ 替换结果文本并附回取提示;任何失败保留原文 | 工具结果改写钩子 |
| 分页召回 | `retrieve_result` 暴露为模型可调工具 | (随工具暴露自动获得) |
| 轨迹 | 基座事件 → `daa.trajectory.v1` dict → `evolution_record_event`(fire-and-forget,绝不阻塞主循环;`*_digest` = sha256[:12]+":"+字节数) | 事件流/会话钩子 |

## 4. 硬约束(质量门自动检查)

- 适配层只有胶水:每文件 ≤500 行;禁内联 Python 能力代码;对 Python 只准经
  `data-agent-capabilities` 入口(`scripts/check_harness_adapters.py` 强制)。
- 权限映射:能力声明的 permission(read_only/writes_artifacts/executes_code)映射到基座
  审批机制;基座无审批时,写产物类默认限制在 `DAA_CAPABILITIES_ARTIFACTS` 目录内。
- 版本 pin + 锁文件;README 记录「已核实的基座 API 版本与日期」。

## 5. 验收

1. `bash harnesses/check-ts.sh` 过(把新目录纳入);
2. `npm run smoke` 全 PASS(模板含 19 工具/压缩/召回/轨迹契约断言);
3. `python scripts/quality_gate.py` 全绿;
4. 有真实 key 时按 README 跑一次端到端(读 CSV → 图表 + HTML 报告 → 因果结论)。
