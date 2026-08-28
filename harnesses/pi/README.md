# Pi 基座适配器(DataAnalysisAgent v2)

把 DataAnalysisAgent 的 19 个能力工具(经同一条 MCP stdio 通道)接入 **Pi Agent Core**
(`@earendil-works/pi-coding-agent`,pin `0.84.3`,API 核实日期 2026-08-26),并提供
工具结果压缩接缝与自进化轨迹记录。适配层只有胶水——能力实现全部在 Python 能力层。

## 前置

```bash
cd <repo>
uv pip install -e ".[data,dev,web,serving]"   # 提供 data-agent-capabilities
export PATH="$PWD/.venv/bin:$PATH"             # 扩展经 PATH 找到能力入口
```

## 安装 / 无 key 冒烟(已验证)

```bash
cd harnesses/pi
npm install
npm run typecheck   # tsc --noEmit
npm run smoke       # 不依赖任何 API key:19 工具清单、读表、因果分析、
                    # 超大结果压缩+分页召回、轨迹契约校验、翻译器单测
```

## 装配(系统提示 + 工具 + 接缝)

`src/preset.ts` 是装配清单:中文数据分析系统提示、19 个能力工具的注册名
(`daa_<capability>`,如 `daa_tabular_read_file` / `daa_retrieve_result`)、
压缩接缝配置(预筛下界 2000 / max_chars 50000 / 压力 0.5,可经
`DAA_COMPACT_FLOOR`(旧名 `DAA_COMPACT_TRIGGER` 兼容)/ `DAA_PI_MAX_CHARS` /
`DAA_PI_PRESSURE` 覆盖;真实触发阈值含压力自适应,只在能力层裁决)。

## 真实 key E2E(未验证 —— 本机无 ANTHROPIC_API_KEY)

```bash
export ANTHROPIC_API_KEY=sk-...
pi -e ./src/extension.ts "读取 /path/to/sales.csv,分析区域销量并生成自包含 HTML 报告,再给因果结论"
# 或长期使用:把 src/extension.ts 软链进项目的 .pi/extensions/
```

期望行为:模型调用 `daa_*` 工具完成分析;超 8000 字符的工具结果被
`tool_result` 钩子替换为采样摘要 + 回取句柄(`daa_retrieve_result` 分页取回);
会话轨迹以 `daa.trajectory.v1` 落 `~/.daa/trajectories/v2/`。

## 接缝说明

| 接缝 | Pi 扩展点 | 行为 |
|---|---|---|
| 工具暴露 | `pi.registerTool`(MCP schema 直传,JSON Schema 即 TypeBox 表示) | 19 个 `daa_*` 代理,单连接懒建,失败 fail-closed 返回错误 envelope |
| 结果压缩 | `pi.on("tool_result")` 返回补丁 | 超阈值 → `sampling_compact_result`,best-effort(出错保留原文) |
| 轨迹 | `pi.on("turn_start"/"turn_end"/"tool_execution_end"/"input")` | 翻译为 TrajectoryEvent,fire-and-forget 记录,绝不阻塞主循环 |

## 排障

- `mcp server data-agent-capabilities exited prematurely` → PATH 未含 repo `.venv/bin`。
- 工具描述显示 "schema unavailable" → 能力 server 未启动成功;先跑 `npm run smoke`。
- 调试:`DAA_MCP_DEBUG=1` 打印能力 server stderr。
