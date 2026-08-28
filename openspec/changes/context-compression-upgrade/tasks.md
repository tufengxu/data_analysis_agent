# Tasks: context-compression-upgrade

状态:主体完成(2026-08-28)。P0-P2 全部落地(PR #56-#63);唯一缓行项 P5(4b delta 渲染),理由与复评条件已记录于 docs/data_context_compression_research.md 落地状态一节。

## P0 — 规格先行(PR-0)

- [x] 0.1 openspec change 五件套(proposal/design/tasks/specs delta)(PR #55)
- [x] 0.2 ADR 0012(docs/adr/0012-context-compression-upgrade.md)(PR #55)
- [x] 0.3 调研文档入库(docs/data_context_compression_research.md)并过质量门(PR #55,gate 七步绿)
- [x] 0.4 D0 解耦原则入规格(核心逻辑仅落能力层;适配器去触发镜像;压力为基座可插拔输入)(PR #55,design.md D0 + sampling spec"压缩决策单一事实源")

## P1 — PR-1:数值呈现 + L1 统计补强(P0-1/P0-2)

- [x] 1.1 render._num 千分位/3 位有效/防科学计数法;_fmt_stats 渲染新字段与 top-k 占比(test_render_new_stat_fields_and_number_format、test_render_avoids_scientific_notation_in_common_range)
- [x] 1.2 sandbox_summary:数值 cardinality + 等深直方图;datetime 粒度与跨度;离群行轮询全数值列;identifier-like 标注(test_sandbox_enriched_stats_and_granularity、test_sandbox_outliers_round_robin_across_numeric_columns)
- [x] 1.3 text_summary 估算路径补 cardinality/直方图;model.py 文档同步(test_text_summary_histogram_and_cardinality、identifier note、outlier_col)
- [x] 1.4 测试:千分位/直方图/粒度/轮询离群/identifier;现锁绿(v1↔v2 等价、三传输确定性)(tests/test_sampling.py + test_tool_result_compactor + test_capability_serving 全绿)

## P2 — PR-2:触发压力自适应 + 页豁免 + 桩 digest + 适配器解耦(P0-3+D0)

- [x] 2.1 SamplingConfig 增 trigger_pressure_scale/trigger_floor_chars;compact_result 计算有效触发(test_pressure_scaled_trigger_compacts_earlier、test_trigger_pressure_floor_bounds_scaling)
- [x] 2.2 `[result_id=` 回取页豁免压缩(test_recall_page_exempt_from_compaction)
- [x] 2.3 collapse_digest 助手落位 capabilities/sampling/compactor.py;context/compression 调用(TestCollapseDigest ×3、test_collapse_stub_carries_digest_and_recall_handle;无标记回退旧文案测试保持)
- [x] 2.4 适配器解耦:pi(preset ask_floor/extension 门与提示去重)/dsh(shouldAsk + askFloorFromEnv,DAA_COMPACT_FLOOR 新名旧名兼容);check-ts + pi/dsh 两 smoke 绿
- [x] 2.5 gating 四测不动(全部保持绿);serving config_overrides 白名单加 trigger_pressure_scale/trigger_floor_chars

## P3 — PR-3:compaction 保数据态 + 重注入(P0-4+P2-2)

- [x] 3.1 能力层:data_state_block(compactor.py)+ ResultStore.alive_ids();v1 侧:KernelManager.list_dataframes() 自省探针(test_kernel ×2、test_alive_ids_newest_first_and_expiry、TestDataStateBlock)
- [x] 3.2 RecoveryPolicy 注入 data_state_provider;_head_tail 头尾拼接;handoff 六节模板 + 运行时数据态分节;压缩后重注入 meta 消息(test_reactive_compact_reinjects_data_state 等 ×4)
- [x] 3.3 runtime._build_data_state_provider 装配(kernel + result_store → AgentLoop → RecoveryPolicy)
- [x] 3.4 test_recovery 现三测绿(空丢弃不调模型/失败降级 None/逐字透传均保持)

## P4 — PR-4a:变量地图(P1-1 第一步)

- [x] 4a.1 kernel_main 快照(name→id/rows/cols)+ 多帧摘要(至多 3 最大优先,variable 键;result 路径也带 variable;小帧只入快照不摘要)(test_kernel ×3)
- [x] 4a.2 python_exec._compose_result 透传 variable;render_summary_dict(variable=) 标题(test_render_variable_title)
- [x] 4a.3 快照去重/上限/被截帧下轮补摘要;8MB 序列化回归(kernel 测试套保持绿)

## P5 — PR-4b:schema 去重 delta 渲染(P1-1 第二步)——⏸ 本轮缓行

- [ ] 5.1 每变量列指纹缓存;render_delta_summary;全量回退(缓行:先用评测臂观测 4a 后剩余重复量再决定投入)
- [ ] 5.2 测试:delta/全量双路径、阈值边界(同上)

## P6 — PR-5:查询下取(P1-2)

- [x] 6.1 capabilities/sampling/slicing.py(单谓词/投影/head-tail-sample/fail-closed)+ v1 shim + manifest 登记(tests/test_slicing.py ×6)
- [x] 6.2 retrieve_result 工具与 serving retrieve_spec 扩键(只增;ResultStore.fetch_content 取全文)
- [x] 6.3 测试:各模式/谓词/非法输入/非表格降级(test_retrieve_tool ×4);serving roundtrip 含 filter(test_retrieve_slice_capability_roundtrip)

## P7 — PR-6:JSON 摘要(P1-3)

- [x] 7.1 json_digest.py(JSON/JSONL/单对象检测 + 键路径骨架 + 数组长度分布 + 蓄水池代表元素)+ render_json_digest;summarize_text 路由;任何解析失败回退文本 digest(manifest 已登记)
- [x] 7.2 测试:tests/test_json_digest.py ×5(变体检测/骨架与采样/渲染/compact 路由/非法回退)

## P8 — PR-7:fidelity 压力自适应(P1-4)

- [x] 8.1 SamplingConfig.adaptive_fidelity(默认开)+ DefaultToolResultCompactor 高压(≥0.75)降档 low(trigger/seed 覆盖保留);AgentConfig.sampling_adaptive_fidelity + serving 白名单加键
- [x] 8.2 测试:TestAdaptiveFidelity ×4(降档/中压不变/显式锁档/保留 trigger 覆盖);显式 high 用例改锁档语义;三传输一致性保持绿

## P9 — PR-8:评测闭环 + 格式臂(P2-1+P2-3)

- [x] 9.1 CompactionStats(compacted/passthrough/前后字符 + ratio)+ runtime 注入并暴露(TestCompactionStats ×2)
- [x] 9.2 compare_sampling_arms(control/default/low)+ EvalRun 扩字段 + register_compare_sampling_cli(降幅>3.5% 给升档指引)(test_compare_sampling_arms_reports_ratio_and_drop、test_sampling_arm_control_transform_disables_compaction)
- [x] 9.3 context_fidelity/ 8 任务 + fixtures/sales_large.csv(1200 行,锚点脚本计算;修正了空组合任务)
- [x] 9.4 SamplingConfig.render_format=markdown|kv(默认不变,仅样本行段)(test_render_kv_format_arm;text_summary/python_exec/serving 白名单接线)
- [x] 9.5 eval_gate PASS;quality_gate 七步绿(demo_e2e 在收尾 PR 复验)

## P10 — 收尾

- [x] 10.1 文档同步:AGENTS(压缩契约段)/README(retrieve 行)/DEVELOPMENT(compare-sampling)/ARCHITECTURE(manifest 增量登记已于各 PR 完成)/ADR 0012;QUALITY_BAR 无需改(七步闸未变)
- [x] 10.2 research 文档加"落地状态"一节(含 4b 缓行理由);本 tasks 状态行已更新;demo_e2e 复验 PASS
