# Proposal: context-compression-upgrade — 数据结果上下文压缩/摘要/采样的深层优化

## Why

第一轮调研(`docs/data_sampling_for_compaction.md`)落地的 sampling 模块(L0-L3 分层摘要、压力门控、CCR-lite 回取)经第二轮调研(`docs/data_context_compression_research.md`,2026-08-27,三路一手来源)验证方向正确,但暴露 14 项差距(G1-G14):数字呈现未利用千分位对 tokenizer 的算术增益(84.4%→98.9%);L1 统计缺 cardinality/直方图/粒度等关键字段;压缩触发不受上下文压力影响;会话 collapse 桩零信号;**compaction 后数据态(kernel 变量/schema/可回取 id)全部丢失**——这是长会话质量的最大风险;跨结果重复渲染同 schema 造成重复计税;回取只有行分页无谓词下取;无 JSON 形态摘要;无压缩-保真评测闭环。本 change 把调研提案(P0-1…P2-3)落为可执行改造,全部聚焦数据上下文管理主线。

## What Changes

0. **解耦原则(贯穿全部条目)**:数据上下文管理与采样策略是基座无关资产——所有新核心逻辑(触发语义、统计补强、digest/数据态助手、下取、JSON 摘要、fidelity 自适应、格式开关)只落 `capabilities/sampling/`;三基座经同一接缝(`ToolResultCompactor` / `data-agent-capabilities` MCP+CLI)获得相同行为;Pi/dsh 适配器现存 TS 侧触发镜像(`shouldCompact`/静态阈值)改为"廉价下界预筛 + 服从服务端裁决",消除第二事实源;v1 会话层模块(context/compression、recovery)只消费能力层助手,不重复实现。
1. **数值呈现优化**(P0-1):render 层大整数千分位、统计值 3 位有效、避免科学计数法。
2. **L1 统计补强**(P0-2):数值列 cardinality(identifier 识别)+ 等深直方图;datetime 粒度与跨度;离群行轮询全部数值列;top-k 附占比。
3. **触发压力自适应 + 回取页豁免 + collapse 桩 digest**(P0-3):effective_trigger 随压力下探(有下限);`[result_id=` 开头的回取页豁免压缩;被 collapse 的旧 tool_result 桩携带一行摘要与回取句柄。
4. **compaction 保数据态**(P0-4+P2-2):摘要输入头尾拼接;结构化 handoff 模板;data_state_provider 注入(kernel 变量清单 + 存活 result_id);压缩后重注入数据态 meta 消息。
5. **kernel 变量地图**(P1-1,分两步):4a 摘要所有新增/变化 DataFrame 并带变量名;4b 同变量同 schema 时 delta 渲染(视 4a 效果决定)。
6. **retrieve_result 查询下取**(P1-2):新 slicing 模块支持 mode=head|tail|sample、columns 投影、单谓词 filter。
7. **JSON/半结构化摘要**(P1-3):检测 JSON/JSONL 产出结构骨架 + 代表元素采样。
8. **fidelity 压力自适应**(P1-4):pressure≥0.75 自动降 low 档,可显式关闭。
9. **压缩-保真评测闭环**(P2-1+P2-3):compactor stats 累积 + evolution 采样臂(全量 vs 压缩)+ ≥8 个 fidelity 评测任务 + render_format A/B 开关。

## Capabilities Impacted

| 能力域/模块 | 改动性质 |
|---|---|
| capabilities/sampling(render/sandbox_summary/text_summary/config/compactor/result_store) | 增强:新统计字段、触发语义、fidelity 自适应、slicing 新模块、JSON digest |
| context/compression.py | 增强:collapse 桩 digest |
| recovery.py + runtime.py + kernel/manager.py | 增强:handoff 模板、data_state_provider、list_dataframes 自省 |
| kernel/kernel_main.py + tools/python_exec.py | 增强:变量级摘要与快照 |
| tools/retrieve_result.py + capabilities/serving/registry.py | 增强:查询下取参数(输入 schema 只增不改) |
| evolution/evaluator.py + examples/eval_tasks/ | 增强:采样臂评测与 fidelity 任务集 |
| harnesses/pi + harnesses/deepseek | 解耦:触发镜像改为下界预筛并服从服务端 `was_compacted`;Pi 移除与中文回取提示重复的英文追加 |

## Non-Goals

- 不引入 sketch 库(ADR 0001:内存态精确统计优先)、LLMLingua 类 token 删除、embedding 语义采样;
- 不做 fidelity 的任务阶段感知(stage hint),本轮只做压力自适应;
- retrieve_result 不做完整 SQL,仅单谓词 + 投影 + 采样模式;
- 不改 recall_hint 字节格式、`_PROD_TOOLS` 工具集、MCP 输入 schema 既有键(只增);
- 不 archive 既有 v2-capability-core change(独立家务);
- 不做位置策略的系统化改造(仅 P2-2 的压缩后重注入)。

## Success Criteria(DoD 摘要)

- 每个 PR 过 `scripts/quality_gate.py` 七步;现锁全部保持绿:recall_hint 字节等价、v1↔v2 输出逐字节等价、三传输确定性、estimate_tokens 权重、`_PROD_TOOLS`、`examples/v2/demo_e2e.py` 11 步 PASS;
- **三基座行为等价**:触发语义(含压力自适应与页豁免)改动后,`check-ts.sh` 与两适配器 smoke 绿;适配器不再含触发逻辑镜像;
- sandbox 自包含守护测试(零包内 import/零顶层 pandas)持续有效;
- 新行为均有先行测试:千分位/直方图/粒度/轮询离群、压力触发与页豁免、桩 digest、handoff 模板与 provider 降级、变量摘要去重、下取谓词、JSON 骨架、fidelity 降档、评测两臂;
- PR-8 后可产出「全量 vs 压缩」两臂 pass-rate 差 + 压缩比报告(实跑需 API key,属人工步骤);
- 文档六件套同步,research 文档标注落地状态。
