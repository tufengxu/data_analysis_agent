# Tasks: v2-capability-core

状态:全部完成(2026-08-26)。证据:`pytest 1278+ passed`、`demo_e2e.py PASS(11/11)`、
两适配器 smoke PASS、`check-ts.sh checked=3 failed=0`、质量门全绿;dsh 真实 E2E 已验证,
Pi 真实 E2E 未验证(无 ANTHROPIC_API_KEY,路径见 docs/V2_RUNBOOK.md §4)。

## P1 契约与骨架

- [x] 1.1 `capabilities/__init__.py` + `contracts.py`(CapabilitySpec/Registry/fail-closed dispatch)+ 空能力域包骨架过质量门
- [x] 1.2 drift 新规则(capabilities 依赖方向)+ manifest 登记骨架
- [x] 1.3 `checks.check_harness_adapters`(spawn 白名单 + 规模启发式 + 500 行上限)并入质量门

## P2 能力迁移

- [x] 2.1 `sampling/` 物理 move → `capabilities/sampling/` + 原路径 shim;drift 规则镜像;4 个采样测试文件全绿(75 tests)
- [x] 2.2 `ToolResultCompactor` 契约 + 参考实现 + 测试(18 tests);v1 agent_loop 接缝改调同一实现(marker 字节级一致有断言);ResultStore 多进程共享
- [x] 2.3 `causal/` 委托式迁移(纯领域层本体即实现);`causal_analyze`(分析)/`causal_estimate`(推断)两个子能力入口
- [x] 2.4 `reporting/` 委托式迁移;chart/html 经 ChartRenderTool/HtmlReportTool(产物目录限定继承)
- [x] 2.5 `capabilities/tabular/`(7 能力委托 v1 tools;KernelHolder 持久内核跨调用存活,x=41→42 断言)
- [x] 2.6 `capabilities/evolution/`:TrajectoryEvent 契约(digest 强制格式)+ 写入/校验 + →TurnRecord 转换器(36 tests)

## P3 serving

- [x] 3.1 `serving/registry.py`:全量装配(19 能力,含 sampling_compact_result/retrieve_result)
- [x] 3.2 `serving/mcp_server.py`(官方 mcp SDK 2.x 低层 API,stdio)+ extra `serving` + uv lock(mcp 2.1.1)
- [x] 3.3 `serving/cli.py` + console script `data-agent-capabilities`(mcp/call/list/compact/retrieve)
- [x] 3.4 传输一致性测试(3 能力:tabular_read_file/causal_analyze/sampling_compact_result,进程内 vs MCP vs CLI)+ 真实 stdio 冒烟脚本 PASS

## P4 双适配器

- [x] 4.1 `harnesses/shared/capability-client.ts`(官方 @modelcontextprotocol/sdk;spawn 固定字面量程序,适配层零进程构造;设计修订记录于 design.md D1)
- [x] 4.2 `harnesses/pi/`:扩展(registerTool 代理 19 工具 + tool_result 压缩 + 轨迹翻译)+ preset 装配清单 + tsc 过 + 无 key 冒烟 PASS(26 检查)
- [x] 4.3 `harnesses/deepseek/`:Cordis 插件(tools/post-execute 压缩 + session/event 轨迹翻译,签名按安装版核实)+ cordis.example.yml + tsc 过 + 无 key 冒烟 PASS(19 检查)+ 真实 E2E 已验证(32KB→1.3KB 压缩、轨迹落盘)
- [x] 4.4 `harnesses/check-ts.sh` 并入质量门(ts 步,checked=3)

## P5 采样接缝接入

- [x] 5.1 Pi `tool_result` 钩子调 `sampling_compact_result` + retrieve 工具(冒烟断言:was_compacted/sampling_method/result_id/回取首页)
- [x] 5.2 dsh `tools/post-execute` 调压缩 + retrieve 工具(冒烟断言 + 真实运行证据)
- [x] 5.3 超大结果注入演示:两适配器 smoke 各注入 2000/2000 行表格;dsh 真实运行 32KB 表

## P6 自进化接线

- [x] 6.1 Pi 事件流→TrajectoryEvent 翻译器(input/turn_start/turn_end/tool_execution_end;冒烟 12 项纯函数断言 + 有效/无效事件契约验证)
- [x] 6.2 dsh session/event→TrajectoryEvent 翻译器(turn/start·tool/call·tool/result·turn/end;冒烟 fixture 回放)
- [x] 6.3 离线管线消费 v2 轨迹(load_v2_turns → TurnRecord[COMPLETED],demo_e2e 断言)

## P7 端到端与验收

- [x] 7.1 `examples/v2/`(sales.csv fixture + demo_e2e.py + smoke_stdio_mcp.py)全链路 PASS
- [x] 7.2 第 8 节验收清单逐项核验:见 design.md 附录「验收清单核验记录」

## P8 收尾

- [x] 8.1 `docs/ARCHITECTURE.md` v2 章节 + manifest 全量同步;`docs/V2_RUNBOOK.md`;`docs/THIRD_HARNESS_GUIDE.md`;README/AGENTS 更新
- [x] 8.2 `python scripts/quality_gate.py` 全绿(含 ts 步实际执行);`.gitignore` 收敛(node_modules/产物目录)
