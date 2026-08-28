# Tasks: context-compression-upgrade

状态:进行中(2026-08-28 开工)。各阶段对应独立 PR,完成后在此勾选并附证据。

## P0 — 规格先行(PR-0)

- [ ] 0.1 openspec change 五件套(proposal/design/tasks/specs delta)
- [ ] 0.2 ADR 0012(docs/adr/0012-context-compression-upgrade.md)
- [ ] 0.3 调研文档入库(docs/data_context_compression_research.md)并过质量门
- [ ] 0.4 D0 解耦原则入规格(核心逻辑仅落能力层;适配器去触发镜像;压力为基座可插拔输入)

## P1 — PR-1:数值呈现 + L1 统计补强(P0-1/P0-2)

- [ ] 1.1 render._num 千分位/3 位有效/防科学计数法;_fmt_stats 渲染新字段与 top-k 占比
- [ ] 1.2 sandbox_summary:数值 cardinality + 等深直方图;datetime 粒度与跨度;离群行轮询全数值列;identifier-like 标注
- [ ] 1.3 text_summary 估算路径补 cardinality/直方图;model.py 文档同步
- [ ] 1.4 测试:千分位/直方图/粒度/轮询离群/identifier;现锁绿(v1↔v2 等价、三传输确定性)

## P2 — PR-2:触发压力自适应 + 页豁免 + 桩 digest + 适配器解耦(P0-3+D0)

- [ ] 2.1 SamplingConfig 增 trigger_pressure_scale/trigger_floor_chars;compact_result 计算有效触发
- [ ] 2.2 `[result_id=` 回取页豁免压缩
- [ ] 2.3 collapse digest 提取助手落位 capabilities/sampling;context/compression 调用(正则提取 + fail-closed 回退)
- [ ] 2.4 适配器解耦:pi/deepseek 触发镜像改为下界预筛 + 服从服务端 was_compacted;Pi 移除重复英文回取提示;check-ts + 两 smoke 绿
- [ ] 2.5 测试:压力触发边界、页豁免、桩新旧双路径;gating 四测不动

## P3 — PR-3:compaction 保数据态 + 重注入(P0-4+P2-2)

- [ ] 3.1 能力层:ResultStore.alive_ids() + 数据态文本块格式化助手;v1 侧:KernelManager.list_dataframes() 自省
- [ ] 3.2 RecoveryPolicy 注入 data_state_provider(消费能力层助手);摘要输入头尾拼接;handoff 模板;压缩后重注入 meta 消息
- [ ] 3.3 runtime 装配 provider
- [ ] 3.4 测试:头尾拼接、provider 注入/失败降级、重注入;test_recovery 现三测绿

## P4 — PR-4a:变量地图(P1-1 第一步)

- [ ] 4a.1 kernel_main 快照 + 多帧摘要(至多 3,variable 键)
- [ ] 4a.2 python_exec 透传 variable;render 变量标题
- [ ] 4a.3 测试:多帧/快照去重/上限;8MB 回归

## P5 — PR-4b:schema 去重 delta 渲染(P1-1 第二步,视 4a 效果)

- [ ] 5.1 每变量列指纹缓存;render_delta_summary;全量回退
- [ ] 5.2 测试:delta/全量双路径、阈值边界

## P6 — PR-5:查询下取(P1-2)

- [ ] 6.1 capabilities/sampling/slicing.py(mode/columns/filter)+ manifest 登记
- [ ] 6.2 retrieve_result 工具与 serving retrieve_spec 扩键(只增)
- [ ] 6.3 测试:各模式/谓词/非法输入;serving roundtrip

## P7 — PR-6:JSON 摘要(P1-3)

- [ ] 7.1 JSON/JSONL 检测 + 骨架 + 采样;render_json_digest;降级链
- [ ] 7.2 测试:对象数组/JSONL/嵌套/非法回退

## P8 — PR-7:fidelity 压力自适应(P1-4)

- [ ] 8.1 adaptive_fidelity 开关 + compactor 高压降档;serving 白名单加键
- [ ] 8.2 测试:降档/关闭/三传输一致性

## P9 — PR-8:评测闭环 + 格式臂(P2-1+P2-3)

- [ ] 9.1 compactor stats 累积器 + runtime 注入
- [ ] 9.2 evaluator 采样臂 + EvalRun 扩字段 + 两臂报告
- [ ] 9.3 examples/eval_tasks/context_fidelity/ ≥8 任务(numeric_anchor)
- [ ] 9.4 render_format=kv 开关(默认 markdown 不变)
- [ ] 9.5 eval_gate 结构门通过;demo_e2e 11 步 PASS

## P10 — 收尾

- [ ] 10.1 文档六件套同步(ARCHITECTURE/AGENTS/README/QUALITY_BAR/DEVELOPMENT/ADR)
- [ ] 10.2 research 文档加"落地状态"一节;本 tasks 状态行更新
