# sampling 规范增量(context-compression-upgrade)

## ADDED Requirements

### Requirement: 压缩决策单一事实源(基座可插拔)

数据上下文压缩与采样策略应为基座无关资产:触发语义(含压力自适应与回取页豁免)、统计字段、渲染格式、下取与摘要行为 SHALL 仅实现在能力层(capabilities/sampling);自研 v1 harness 的会话层模块 SHALL 仅经导入能力层助手消费这些行为,不得重复实现;外部基座适配器(Pi/dsh)SHALL NOT 镜像触发判定逻辑,只允许做不低于能力层触发下界的廉价长度预筛,并 SHALL 以能力层返回的 `was_compacted` 与内容为最终裁决(裁决为不压缩时逐字节保留原文)。上下文压力信号 SHALL 保持为各基座可插拔输入;信号缺失(如传 0)时行为 SHALL 退化为静态阈值语义。

#### Scenario: 适配器服从服务端裁决

- **WHEN** Pi 或 dsh 适配器遇到长度超过预筛下界但低于能力层有效触发的工具结果
- **THEN** 能力层应返回 `was_compacted=false` 且内容与原文逐字节一致
- **AND** 适配器应保留原文不做本地替换

#### Scenario: 三基座行为等价

- **WHEN** 同一内容分别经 v1 agent_loop 接缝与 `data-agent-capabilities` 入口(MCP/CLI)压缩
- **THEN** 两路径输出应逐字节一致(既有等价测试守护)

## MODIFIED Requirements

### Requirement: ToolResultCompactor 接缝契约

系统应提供 harness 无关的 `ToolResultCompactor` 契约:输入原始工具结果、长度预算、上下文压力信号(0..1)与可选 SamplingConfig;输出压缩内容、是否压缩、召回句柄、sampling_method 与 fidelity 标注。触发阈值应随上下文压力下探:`effective_trigger = max(trigger_floor_chars, trigger_chars * (1 - trigger_pressure_scale * pressure))`,压力为 0 时行为与既有语义逐字节一致;以 `[result_id=` 开头的回取页内容应豁免压缩;`adaptive_fidelity` 开启且压力 ≥0.75 时应以 low 档 fidelity 覆盖采样配置。既有保证(压力自适应接受率、超硬上限强制压缩、失败降级不劣于原样返回、recall 句柄字节格式)应全部保留。

#### Scenario: 高压下提前触发压缩

- **WHEN** 内容长度介于有效触发阈值与静态 trigger_chars 之间且上下文压力 > 0
- **THEN** 压缩器应对该内容执行摘要而非原样放行

#### Scenario: 回取页豁免

- **WHEN** 工具结果内容以 `[result_id=` 开头(retrieve_result 回取页)
- **THEN** 压缩器应原样返回该内容,不做任何摘要

#### Scenario: 高压自动降档

- **WHEN** `adaptive_fidelity=True` 且 `context_pressure ≥ 0.75`
- **THEN** 输出 fidelity_level 应为 `low` 且输出不长于用户配置档位
- **AND** `adaptive_fidelity=False` 时应保持用户配置档位不变

## ADDED Requirements

### Requirement: 数值呈现契约

渲染层应以 LLM 友好格式呈现数值:绝对值 ≥1000 的整数使用千分位分隔符;统计浮点数使用 3 位有效数字;格式化产生科学计数法时应回退为定点表示。summary 数据结构中的数值精度应不受渲染格式化影响。

#### Scenario: 大数渲染

- **WHEN** 数值列统计含 max=100000
- **THEN** 渲染文本应包含 `max=100,000`

### Requirement: L1 列统计补强

精确路径(沙箱)的数值列统计应包含 cardinality 与等深直方图;datetime 列统计应包含粒度推断(相邻时间差中位数映射到 second/minute/hour/day/month)与时间跨度;离群行应轮询全部数值列直至达到上限并标注来源列;当数值列 cardinality 等于非空计数且计数 >50 时应在 notes 标注 identifier-like;类别列 top-k 渲染应附占比。文本估算路径应在低成本范围内镜像上述字段。

#### Scenario: identifier 列识别

- **WHEN** 某数值列每个非空值均唯一且非空计数 >50
- **THEN** 摘要 notes 应包含 `identifier-like` 标注

#### Scenario: 离群行多列覆盖

- **WHEN** 表含多个数值列且首列无 IQR 离群而次列存在
- **THEN** 离群行应来自次列且标注来源列名

### Requirement: kernel 变量地图

持久内核应对每个执行请求后新增或 shape 发生变化的顶层 DataFrame/Series 生成带变量名的采样摘要(每请求至多 3 个,按行数降序,`result` 变量保持既有优先级),并以变量快照(name → id/shape)跨请求去重;摘要输出项应携带 `variable` 键且渲染标题应包含变量名;无变化帧的请求不应重复输出摘要。

#### Scenario: 新变量被摘要

- **WHEN** 用户代码在 kernel 中新建 DataFrame `orders`(行数 > trigger_rows)且未命名为 `result`
- **THEN** 该请求的 table_summary 输出应携带 `variable="orders"` 且渲染标题含 `orders`

#### Scenario: 未变化帧不重复摘要

- **WHEN** 后续请求未改动 `orders`(id 与 shape 均不变)
- **THEN** 该请求不应再次输出 `orders` 的摘要

### Requirement: 结构化回取(查询下取)

retrieve_result 应支持对已缓存表格结果的结构化切片:`mode=head|tail|sample(k)`、`columns` 列投影与单谓词 `filter`(op ∈ >,>=,<,<=,==,!=;数值列按数值比较,否则按字符串比较);输出应沿用既有页头格式与页大小上限;谓词不合法或缓存内容不可解析时应 fail-closed 返回明确错误而非抛出异常。

#### Scenario: 谓词过滤

- **WHEN** 以 `filter="units>100"` 回取含 units 列的缓存表
- **THEN** 返回页应仅含满足谓词的行且页头标注匹配数

### Requirement: JSON 形态摘要

文本摘要路径应检测 JSON(整体解析)与 JSONL(逐行解析)输入,产出结构骨架(键路径、类型、数组长度分布)、代表元素采样(至多 5)与计数;任何解析失败应退回既有文本 digest(降级链不变)。

#### Scenario: JSONL 输入

- **WHEN** 工具结果为多行 JSON 对象(每行一个对象,超过触发阈值)
- **THEN** 摘要应包含键路径骨架与数组/行计数
- **AND** 不应包含全量原始对象

### Requirement: 压缩计量与评测臂

压缩器应支持可选的统计累积(压缩次数、压缩前后字符数);evolution 评测应支持采样配置臂对照(control=禁用压缩的触发阈值,treatment=指定 fidelity 档),并对冻结 fixture 任务集产出两臂 pass-rate 与压缩比对比;评测判分应保持确定性(numeric_anchor,无 LLM judge)。渲染层应支持 `render_format=markdown|kv` 配置,默认 markdown 行为不变。

#### Scenario: 两臂对比

- **WHEN** 对同一 fidelity 任务集分别以 control/treatment 臂运行
- **THEN** 评测输出应包含两臂 pass-rate 与 treatment 平均压缩比
