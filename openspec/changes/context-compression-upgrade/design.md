# Design: context-compression-upgrade

## 1. 结构与依赖方向

本 change 不新增分层,只在既有结构内增强:

```
capabilities/sampling/   ← P0-1/P0-2/P1-2/P1-3/P2-3(纯能力层,保持 stdlib+可选 pandas,
                            禁 import tools/context/kernel/agent_loop;新增 slicing.py)
context/compression.py   ← P0-3 桩 digest(v1 会话层)
recovery.py + runtime.py ← P0-4(依赖倒置:data_state_provider 构造器注入,recovery 零新增 import)
kernel/ + tools/python_exec.py ← P1-1(变量级摘要;harness 侧,可 import 能力层)
evolution/ + examples/eval_tasks/ ← P2-1(离线顶层 sink,可依赖 capabilities/config)
```

依赖规则(`scripts/drift_rules.py`)不变,改动全部落在既允许边界内。

## 2. 外部事实核实记录(2026-08-27,详见 docs/data_context_compression_research.md)

- 千分位强制 R2L 数字分组,GPT-4 算术 84.4%→98.9%(arXiv:2402.14903,一手);
- JetBrains 500 实例对照:占位符 masking 多数设置持平或优于 LLM 摘要,桩应保留可回取指针(一手);
- Anthropic 官方:"old tool results can be removed because they can be re-fetched";Claude Code tool response 默认 25k token 顶(一手);
- Codex compact 为 handoff 模板,要求保留 "critical data…references"(源码核实);
- 每列完整统计画像在工业界公开实践中缺位(Power BI 仅 min/max)→ L1 补强是差异化而非偏差;
- CARE 反例:纯充分统计量降低 LLM 表现 → 统计 + 具象行混合的既有设计保持不变。

## 3. 关键设计决策

### D0 单一事实源与基座可插拔(用户追加的一等目标)

数据上下文管理 + 采样策略是基座无关资产,同一套须可插拔用于 v1 自研 harness、Pi、dsh:

- **核心逻辑只落 `capabilities/sampling/`**。本 change 全部新行为(触发语义、统计字段、digest/数据态助手、slicing、JSON digest、fidelity 自适应、render_format)物理落位能力层;v1 会话层模块(context/compression、recovery、runtime)只 import 能力层助手做接线,不重复实现。
- **适配器去镜像**:Pi(`extension.ts` 静态 `compactionTriggerChars` 门)与 dsh(`compaction.ts#shouldCompact` 静态门)现状在 TS 侧镜像触发逻辑,压力自适应落地后即成过时的第二事实源。改造为:适配器只做**廉价下界预筛**(长度 > `trigger_floor_chars` 下界,可配置,默认取能力层 floor 同值),随后无条件询问 `sampling_compact_result`,并以服务端裁决为准(`was_compacted=false` → 逐字节保留原文;服务端 passthrough 返回原文,故遵守是平凡的)。页豁免等服务端规则因此对适配器自动生效。
- **压力信号是各基座的可插拔输入**:v1 由 `_context_pressure` 实测;Pi 用 seam 静态配置;dsh 暂传 0。信号缺失时行为退化为今天的静态阈值语义,不破坏等价性。基座能提供真实压力时即自动获得压力自适应收益。
- **边界声明**:会话级压缩管线(五级压缩、collapse、compaction 摘要)本身是各基座自有资产——本 change 的可移植部分是 (a) 工具结果压缩行为(既有接缝),(b) collapse digest 提取与数据态格式化助手(新增,落能力层,任何基座可复用),(c) data_state_provider 契约样本(kernel 自省实现留 v1,其他基座可插自己的 provider)。
- **等价性证据链**:v1 seam 与能力层参考实现输出逐字节等价测试 + 三传输(inproc/MCP/CLI)一致性测试 + demo_e2e MCP compact 步 + check-ts/适配器 smoke。

### D1 数值呈现契约(render 层,不动 sandbox 数据精度)
整数 abs≥1000 加千分位分隔;统计浮点 3 位有效数字(与摘要误差契约自洽);`%.3g` 产生 `e` 记法时回退定点。只在 render 层格式化,summary dict 数据保持原精度(下游消费者不受影响)。p50=49.5 等既有断言不受影响。

### D2 触发压力自适应 + 回取页豁免
`effective_trigger = max(trigger_floor_chars, trigger_chars * (1 - trigger_pressure_scale * pressure))`,默认 scale=0.5、floor=2000。压力为 0 时与现行为逐字节一致(既有 gating 测试不动)。以 `[result_id=` 开头的内容(retrieve_result 回取页的页头标记)豁免压缩:回取是模型的显式取原文动作,再摘要会破坏回取语义;该豁免同时解除 `_MAX_PAGE_CHARS(7500) < trigger(8000)` 在触发下探后的耦合风险。落地同步改造两适配器(D0):下界预筛替换静态镜像门,Pi 移除与中文回取提示重复的英文追加(服务端已附提示时不再追加)。

### D3 collapse 桩 digest(提取逻辑落能力层)
ContextCollapse 替换 tool_result 时,从被压内容提取:摘要头元信息行(`- rows=… · cols=… · method=…`)与 recall_hint 中的 result_id,生成 `[collapsed: table N rows × M cols · retrieve_result(result_id="…")]`;任一提取失败回退现文案 `[Earlier tool result collapsed]`(fail-closed,纯文本解析无新依赖)。提取函数落位 `capabilities/sampling/`(供任何基座的会话层复用),`context/compression.py` 仅调用。

### D4 compaction 保数据态(依赖倒置,助手落能力层)
`RecoveryPolicy` 构造器增可选 `data_state_provider: Callable[[], Awaitable[str | None]]`;runtime 装配 = kernel `list_dataframes()`(经 manager.execute 跑自省代码,harness 注入代码不受用户代码 AST 门禁约束)+ `ResultStore.alive_ids()`;任一失败整体 None,模板对应分节省略。**变量清单/存活 id → 数据态文本块的格式化函数落 `capabilities/sampling/`**(其他基座可复用);kernel 自省与 provider 装配留 v1(provider 契约见 D0)。摘要输入由 `digest[-24k:]` 改为头 8k + 中段省略标记 + 尾 16k(早期 schema 结论不再被切掉)。摘要 prompt 固定分节:任务目标/已读文件与 schema/kernel 变量清单/关键数值结论/未决事项/可回取 result_id。压缩成功后追加 `is_meta` 的 `[数据状态(压缩后重注入)]` 消息(同 provider,失败跳过)。

### D5 kernel 变量地图(分两步)
4a:kernel_main 持 `_frame_snapshot[name → (id, nrows, ncols)]`,`_auto_summarize` 遍历 namespace(排除 `_` 前缀/dunder/harness 注入名),摘要新增或 shape 变化的帧,按行数降序至多 3 个(8MB 序列化上限保护);outputs 项增 `variable` 键(向后兼容,harness 只认 type/summary 两个键);render 标题带变量名。`result` 变量保持现优先级;stateless 子进程路径维持仅 result。4b(独立 PR,视 4a 效果):同变量同列指纹时 `render_delta_summary` 只渲染变化列统计 + 新样本行。

### D6 查询下取(单谓词,不造 SQL)
新 `capabilities/sampling/slicing.py`(纯 stdlib):复用 detect_table 解析缓存文本;mode=head|tail|sample(k,seed)、columns 投影、filter 单谓词(op ∈ >,>=,<,<=,==,!=;数值列数值比较,否则字符串比较);输出沿用页头格式与 `_MAX_PAGE_CHARS` 上限;解析/求值失败 fail-closed 返回明确错误文案。v1 工具与 v2 serving 能力共用(serving 输入 schema 只增键)。

### D7 JSON 形态摘要
text_summary 增 JSON 检测(整体 json.loads 或逐行 JSONL)→ 骨架:键路径(点表示,深度≤3)、类型、数组长度分布(n/min/max/中位)+ 代表元素(蓄水池 5)+ 计数;render 增 `render_json_digest`;任何失败退回现有文本 digest(降级链不变)。

### D8 fidelity 压力自适应 + 评测闭环
自适应:`SamplingConfig.adaptive_fidelity=True`(默认开),compactor 在 pressure≥0.75 且开启时用 `for_fidelity("low")` 覆盖;显式置 False 保持用户档位;serving config_overrides 白名单加键。评测:compactor 增可选 stats 累积器(压缩次数/前后字符数);evaluator 增采样臂(control=trigger_chars 置 10^9 不压缩;treatment=默认档与 low 档),复用 eval_config_for;新增 context_fidelity 任务集(numeric_anchor 判分,不加新断言键);调参阈值沿用 research 文档(准确率降 >3-5% → 升 fidelity)。

### D9 序列化格式 A/B 开关
`SamplingConfig.render_format = "markdown" | "kv"`(默认 markdown,行为不变);kv 模式样本行渲染为 `col1=v1; col2=v2`(研究证据矛盾:Table Meets LLM 称 HTML 最优 vs 实务基准 Markdown-KV 最准 → 只能自测,故仅作为评测臂引入,不改默认)。

## 4. 风险与应对

| 风险 | 应对 |
|---|---|
| 渲染格式变化破坏既有文本断言 | 改动前逐条核对敏感度表(test_sampling L183 等);千分位阈值≥1000 避开 p50=49.5 |
| 触发下探使回取页被再摘要 | D2 页豁免先行,并有专门测试 |
| 适配器下界预筛增加 2k-8k 内容的往返 | 下界可配置;服务端 passthrough 原样返回,正确性不受影响;smoke 验证耗时可接受 |
| 适配器改造破坏 TS 门/smoke | 改动最小化(替门 + 提示去重);check-ts 与两 smoke 入 PR-2 验收 |
| 桩解析依赖摘要头格式 | 正则容错 + 回退现文案(fail-closed) |
| recovery/provider 引入循环依赖 | 构造器注入,recovery 零新增 import;drift 门验证 |
| 多帧摘要撑爆 8MB 响应 | 每请求至多 3 帧,序列化上限回归测试 |
| 下取谓词演化为半吊子 SQL | 单谓词硬边界写入 Non-Goals,validate_input 拒绝复合表达式 |
| text_summary 超 600 LOC 告警 | JSON 摘要必要时拆独立模块(manifest 登记) |
| 评测臂引入非确定性 | 沿用冻结 fixture + numeric_anchor 确定性判分,无 LLM judge |

## 5. 验收清单核验记录

(随 PR 落地逐项填写:PR 编号 + 证据[测试名/命令输出]。)
