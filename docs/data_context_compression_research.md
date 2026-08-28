# 数据结果上下文压缩/摘要/采样:第二轮深度调研与改造提案(2026-08-27)

本文是 `docs/data_sampling_for_compaction.md`(第一轮调研,sampling 模块的设计依据)的后续:先盘点当前实现已落地什么,再汇总三条线(工业界产品、context engineering、数据编码/统计压缩科学)的一手调研发现,最后给出映射到当前架构、按优先级排序的改造点。

> 调研方法:三路并行,全部要求追一手来源(官方文档/工程博客/论文原文/开源源码),二手转述单独标注。文末附来源清单。
> 两处对第一轮调研文档的**勘误**:Chroma《Context Rot》署名作者为 Kelly Hong、Anton Troynikov、Jeff Huber(非 "La Cava et al.",后者是二手错误转述);"Table Meets LLM" 第一作者为 Yuan Sui(非 "Gao et al.")。

---

## 一、现状盘点:当前系统已有什么

### 1.1 工具结果层(`capabilities/sampling/`,v2 物理实现 + v1 shim)

| 层 | 现状 | 位置 |
|---|---|---|
| L0 元信息 | n_rows/n_cols、sampling_method、fidelity_level | `model.py` |
| L1 列统计 | 数值:min/max/mean/std/分位数(p01/25/50/75/99)/n_outliers(IQR);类别:cardinality/top_k(默认 10)/tail_truncated;datetime:min/max;null_count | `sandbox_summary.py`(内核路径全量精确)、`text_summary.py`(harness 兜底为解析样本估算) |
| L2 代表行 | auto 分层(首个低基数类别列,比例分配)+ 蓄水池,默认 20 行;离群行仅取**第一个数值列**的 IQR 离群,默认 5 行 | 同上 |
| L3 渲染 | Markdown;结尾固定采样警告("勿据样本推断总量");单元格 40 字符截断;top-5 值 16 字符 | `render.py` |
| 触发与门控 | >8000 chars 才摘要;增益门控随 context_pressure 自适应(accept ratio 0.65→0.90);超 max_chars(50k)强制压缩;兜底 head+tail 截断 | `text_summary.compact_result`、`compactor.py` |
| 回取(CCR-lite) | 原文落盘 ResultStore(TTL 1h、总 64MB、单条 8MB);`retrieve_result(result_id, offset, limit, query)` 按行分页 + 子串过滤;页 7500 chars | `result_store.py`、`tools/retrieve_result.py` |
| 内核接线 | `result` 变量 >50 行自动摘要;`agent_summarize(df)` 显式调用;sandbox 自包含(无包内 import,pandas 可选) | `tools/python_exec.py`、`kernel/` |

### 1.2 会话层(`context/compression.py` + `recovery.py`)

- 五级管线:50k chars 单消息上限 → 40 条消息 snip → microcompact 合并 → **staged collapse(413 时零成本 drain,tool_result 打桩)** → reactive auto-compact(LLM 摘要,摘要输入取被丢弃段的**末尾 24k chars**,prompt 要求保留"已读文件与 schema、关键数值结论、偏好约束、未决事项")。
- CJK 感知的 token 估算(ASCII 0.25/char,其他 1.0/char)。

### 1.3 持久层(`memory/profiler.py`)

- 数据集画像:列指纹分层失效(fresh/stale/invalid);统计层薄弱——仅 1000 行采样的行数 + nulls + dtypes,与 sampling 模块的丰富统计**互不复用**。

### 1.4 与业界对照的初步定位

- 分层(L0-L3)+ "统计 + 代表行混合"与学术共识一致(TAP4LLM、CARE 反例证明纯统计量有害,需混合具象行——当前设计恰好如此)。
- CCR-lite 回取与 Anthropic context editing 的官方原则同构:"old tool results can be removed because they can be re-fetched"。
- 压力自适应增益门控在公开实践中**没有发现先例**(业界均为静态阈值);这是当前项目的差异化点,值得保留并扩展。
- 采样警告(防"据样本推断总量")与 AQP 文献的误差诚实原则一致。

---

## 二、三线调研核心发现

### 2.1 工业界:生产级数据 Agent 怎么喂数据上下文

**两条路线泾渭分明**:

1. **code-interpreter 路线**(OpenAI CI/ADA、Anthropic analysis tool/code execution、Open Interpreter、Julius):**不把数据放进 context**。文件落沙箱,模型写代码感知数据,上下文里只有代码 + stdout。OpenAI 官方描述模型在 "persistent session" 中通过执行迭代;Anthropic 官方数字:programmatic tool calling 把 200KB 原始数据降为 1KB 结果(200:1),平均 token -37%。
2. **BI copilot 路线**(Databricks Genie、Power BI Copilot、Snowflake Cortex Analyst、Hex、Deepnote、Vanna、DB-GPT):**schema 语义化 + 检索裁剪 + 值限量**,不放行。Genie:Unity Catalog 表/列描述进上下文,值走 prompt matching(entity matching 上限 120 列 × 1024 distinct 值 × 127 字符);Power BI:显式做 schema reduction,描述截 200 字符,min/max 聚合作为 data points;Snowflake:`sample_values` + `is_enum`,高基数列外包 Cortex Search;Hex:embedding 检索相关表/列(官方评估 82%→96%)。

**关键量化数字**(全为一手):
- Claude Code tool response 默认硬顶 **25,000 tokens**(Anthropic《Writing tools for agents》)。
- Vanna:DDL 预算 **14,000 tokens**,超了停止追加;并明文授权模型发 `intermediate_sql` 查列 distinct 值(按需取数的设计典范)。
- Open Interpreter:**2800 字符**截断保尾部 + 引导语指向二级摘要工具。
- Gemini CLI:40k chars 阈值,截断保 head 20%/tail 80%,截断时必附 "Full output saved to: {file}"(摘要 + 指针并存)。
- PandasAI:`<table>` 标签 + 列 schema + **head() 默认 5 行 CSV**,无列统计,单元格 200 字符截断。
- PET-SQL(schema 剪枝):每表随机 **3 行值**内联(消融:去掉掉 2.4-6.4% EX);两段式剪枝把 prompt 平均表数 **4.89→1.60**,精度反升(74.4%→78.2%)。
- **没有任何一家公开放"每列完整统计画像"(基数/空值率/分位数/top-k)进 context**——最接近的是 Power BI 的 min/max。当前项目的 L1 层在公开实践里是超前的,这是机会点而非偏差。

### 2.2 Context engineering:压缩/compaction 的原理与量化

**Context rot 是硬约束**:
- Chroma《Context Rot》(18 模型):性能随输入变长持续、非线性下降;**1 个 distractor 就降低所有 18 个模型性能**;约 **300 token 的 focused 上下文一致优于约 113,000 token 的完整上下文**(LongMemEval)。
- Lost in the Middle(U 形位置效应):相关信息在中间时,性能可**低于 closed-book**。
- 但 Chroma 在带 distractor 的 NIAH 中**未观察到位置效应**——位置效应是任务相关的;Anthropic 官方长上下文指南仍然建议:长数据/文档放顶部、指令/查询放底部("queries at the end can improve response quality by up to 30 percent")。
- 2026 年 1M-token 多跳研究:单针检索基本解决,**三跳链推理明显退化**;"Nominal context-window length is a poor proxy for usable multi-hop capability"。

**压缩比 vs 保真度的共识区间**:
- 2-5x 的"去噪式"压缩通常无损甚至**增益**(LongLLMLingua 4x 压缩下 LongBench +17.1%;ACON peak token -26~54% 且成功率上升;JetBrains masking 成本 -52% 解题率 +2.6%)。
- >10x 高激进压缩必然有损;Chroma 的 300 vs 113k(~370x)之所以更好,本质是"只留相关"而非压缩魔法。
- **JetBrains 对照研究(500 个 SWE-bench 实例)的反直觉结论**:占位符 masking 在 5 个设置中 4 个持平或优于 LLM 摘要;LLM 摘要让轨迹变长 13-15%、摘要调用破坏缓存复用(可占总成本 >7%)。→ **低成本手段优先,LLM 摘要是最后手段**。

**什么内容该摘要 / 该指针化 / 该保原文**(官方口径):
- 可摘要替换:已完成探索、过期中间推理、过程性日志。
- 可指针化(引用 + 按需取回):**可重取的 tool result**(Anthropic 原话)、结构化知识("记结构与口径,不记数值快照"——与本项目 ADR 0004 同构);前提是**取回成本低且可验证**(provenance 必须伴随摘要)。
- 必须保原文:正在操作的对象、最终交付引用的**数值事实**(数据分析场景:统计量/表格/口径定义——摘要会引入不可审计的转录误差)、任务目标与硬约束。

**Compaction 时机共识**:token 阈值做触发器(Claude Code 200K/967K 边界;Anthropic server compaction 默认 150k;Gemini CLI 50%;给摘要调用本身留 20k 预算);压缩点落在**轮次/任务边界**;保留最近 N 轮或尾部 30% 原文;压缩后**重注入稳定上下文**(Claude Code compaction 后从磁盘重读最近 5 个文件;CLAUDE.md 重注入);把 cache 断点放在 system prompt 末尾保缓存。Codex 的 compact prompt 是 handoff 风格,明确要求保留 "critical data, examples, or references needed to continue"。
- KV cache 视角:**任何中途压缩都是前缀缓存重置**——"晚压缩、少压缩、在轮次边界压缩"优于"频繁小摘要"。

**结构化笔记 > 一次性摘要**:Anthropic 推荐 structured note-taking(progressive summarization,scratchpad 先摘录后综合);ACE(Stanford,ICLR 2026)指出摘要式管理的两大失败模式:**brevity bias**(越摘越短丢洞见)与 **context collapse**(反复重写磨损细节),把上下文当"演进 playbook"管理可 +10.6%。

### 2.3 数据编码/统计压缩科学:怎么把表压得又小又真

**序列化格式的证据是矛盾的(必须自测)**:
- 学术(Table Meets LLM,WSDM'24,GPT-3.5/4):**HTML/XML 标记语言最优**(预训练语料偏置),比 NL+Sep 高 6.76%;**问题放表后会平均掉 6.81%**(问题必须在数据前)。
- 实务基准(GPT-4.1-nano,1000 行):**Markdown-KV 最准(60.7%)**,Markdown-Table 是"每 token 准确率"甜点(51.9% @ 25k tokens),**CSV 最便宜(19.5k)但最差之一(44.3%)**,XML 比 CSV 贵 3.9 倍。
- 结论:格式影响 2-4x token 与可观精度差,但最优格式依赖模型与任务;Markdown-Table 是合理默认,应保留 A/B 开关。

**数字格式有硬证据**:
- Tokenization 研究(arXiv:2402.14903):主流 tokenizer 从左到右 3 位切分导致位值不对齐;千分位逗号强制 R2L 分组,**GPT-4 算术 84.4%→98.9%**。→ 所有呈现给 LLM 的大数应带千分位。
- 有效位数/科学计数法 vs 定点:文献空白(可辩护推断:定点 + 千分位优先于 `1.5e4`;保留位数应与摘要误差契约一致,3 位有效数字自洽)。

**sketch 的定位要澄清**:
- KLL(2.5KB 表达 10 亿行分布,rank 误差 1.65%@K=200)、frequent-items(top-k 误差 N/(L+1))、Theta/HLL(基数 RSE≈1/√k)、DDSketch(**相对误差**保证)——输出天然是几十 token 的文本,**但"sketch 输出→LLM 文本"没有任何现成项目**(空白)。
- **对本项目的关键判断**:内核是 pandas 全量在内存,精确统计永远优于近似 sketch;sketch 只在接入流式/超大/SQL 数据源时才有意义。当前 L1 用精确分位数是正确选择,不需要引入 datasketches 依赖。

**"充分统计量"对 LLM 不一定充分(CARE 反例)**:
- CARE(arXiv:2511.16016):把因果算法输出(形式上的充分统计量)喂 LLM **降低**表现(ASIA F1 0.984→0.840)。LLM 未被训练消费浓缩统计量,变量名语义先验常更强。
- 正例:交互历史压成充分统计量喂 LLM 才出现稳健探索(arXiv:2403.15371)。
- → **统计画像 + 具象样本行必须混合呈现**(当前设计已如此,验证正确);top-k 值的语义信息(真实类别名)比纯数字更值钱。

**行采样的证据**:
- TAP4LLM 六种采样:semantic(查询相关)+column grounding 最优;centroid(K-Means)适合多样性;**metadata + 10 行以上收益递减**;表内容:增强信息 token 配比 **5:5~4:6 最优**。
- head() vs 随机:无严格学术对照;实践共识是随机更公平(head 常受排序/分桶污染)。当前 auto-hook 覆盖了"result 才摘要",但模型手写 `df.head()` 打印时仍会全文进上下文(文本路径靠 8k 阈值兜底)。
- TabSQLify(NAACL'24):用 text-to-SQL 抽**问题相关子表**再喂 LLM——"查询下推"式压缩的代表,与 retrieve_result 的进化方向一致。

---

## 三、差距分析:调研发现 × 当前实现

| # | 调研发现 | 当前状态 | 差距 |
|---|---|---|---|
| G1 | 千分位逗号提升算术准确率(84.4→98.9%) | `_num` 整数无千分位,浮点 `.4g`;sandbox `_round` 6 位有效 | 渲染未做数字格式优化 |
| G2 | 长数据放顶部/指令放底部(+30%);system prompt 位置注意力 sticky | 摘要随 tool_result 出现在会话中部;compaction 后无数据态重注入 | 无位置策略 |
| G3 | compaction 应保留 "critical data/references";Codex handoff 模板;ACE 防 context collapse | 摘要输入仅取被丢弃段**末尾 24k chars**(头部 schema 可能被切掉);prompt 无结构化模板;无 kernel 变量清单/结果 id 清单注入 | 长会话数据态丢失风险 |
| G4 | masking 优于/持平 LLM 摘要;占位符应保留可回取指针 | ContextCollapse 打桩为 "[Earlier tool result collapsed]",**无 digest/无 result_id** | 桩是纯文本,信号为零 |
| G5 | 每列统计画像在工业界是空白/机会;TFDV/ydata 字段集是事实标准 | L1 已有分位数/top-k;**缺**:数值列 cardinality(识别 ID/常量列)、top-k 占比、直方图桶(分布形状)、datetime 粒度;离群行仅第一个数值列 | L1 覆盖不全 |
| G6 | schema 应放一次 + 去重引用(PET-SQL 4.89→1.60 表;BI 侧 schema 只注入一次) | 每个大结果重复渲染全量列统计表;同一 df 反复摘要无 delta;**摘要不带变量名**(无 provenance) | 跨结果重复计税 |
| G7 | 按需取数应"查询下推"(Vanna intermediate SQL、TabSQLify、PTC 200KB→1KB) | retrieve_result 只做行分页 + 子串过滤;无列投影/谓词/采样模式 | 回取粒度粗 |
| G8 | 值要限量、枚举化或按需取;宽表要列裁剪 | 宽表样本行渲染全部列(40 字符截断);无任务相关列优先级 | 宽表体验差 |
| G9 | 触发阈值:业界 2.8k-40k 字符不等,均静态 | 8k chars 静态触发;压力只影响 accept ratio 不影响 trigger | 7.9k 的表在 90% 压力下仍原样放行 |
| G10 | 格式最优解模型相关,需 A/B | 固定 Markdown | 无实验开关 |
| G11 | JSON/半结构化:结构摘要优先 | 文本兜底按行采样,JSON 打印无 schema 骨架 | 形态 2 未落地 |
| G12 | CARE:统计 + 具象行混合;TAP4LLM 5:5 配比 | 已混合 ✓ | 验证通过,无需改 |
| G13 | "可重取即可丢原文";摘要必须带 provenance | CCR-lite ✓;recall_hint ✓ | 验证通过 |
| G14 | 压缩-保真需要量化评测闭环(TAP4LLM/JetBrains 方法论) | 有 trigger/gate 单测,无端到端"摘要问答准确率"评测 | 无保真度评测 |

---

## 四、改造点提案(按优先级)

> 约束提醒:所有改动遵守 `capabilities/*` 不 import v1 harness;sandbox 自包含(无包内 import、pandas 可选、无 `__future__`);项目零新依赖(sketch 库暂不引入);fail-closed 降级链不破坏;每项过 `scripts/quality_gate.py` 并同步 manifest/文档。

### P0-1 数字呈现优化(render.py)
- 大整数加千分位分隔(`1,234,567`);统计值 3 位有效数字与误差契约对齐;样本行内数值同样格式化;避免裸科学计数法(`1.5e4` → `15,000`)。
- 证据:arXiv:2402.14903(R2L 分组,GPT-4 算术 84.4%→98.9%)。
- 落点:`capabilities/sampling/render.py` 的 `_num/_fmt_stats/_cell`。纯渲染层,sandbox 不动(数据保持原精度,只在呈现层格式化)。
- 风险:极小;注意 CJK 语境下千分位无歧义。

### P0-2 L1 统计层补强(sandbox_summary.py + model.py + render.py)
- 数值列增加 `cardinality`(识别 ID 列/常量列:`card==n_rows` → 标注 "identifier-like");top-k 附占比(%);等深直方图(由已有分位数插值出 ~8 桶计数,一行呈现分布形状/双峰/偏态);datetime 列增加粒度推断(中位相邻差 → second/hour/day/…)与跨度;离群行改为**轮询所有数值列**(仍受 max_outlier_rows 约束,注明来源列)。
- 证据:Power BI min/max 聚合、TFDV/ydata 字段集(事实标准)、CARE(保持统计 + 行混合)。
- 落点:kernel 路径精确计算(sandbox_summary);文本兜底路径同步字段(model/render)。
- 风险:sandbox 自包含约束内均为纯 numpy/pandas 已用操作;体积增量控制在每列 +10~20 chars。

### P0-3 压缩触发压力自适应 + collapse 桩带 digest(text_summary.py + context/compression.py)
- trigger 压力缩放:`effective_trigger = trigger_chars * (1 - k*pressure)`(k≈0.5,下限 2k chars)——高压时更早开始摘要,低压时尽量保原文(缓存/保真)。
- ContextCollapse 的 tool_result 桩从 "[Earlier tool result collapsed]" 升级为携带一行 digest + 回取句柄:从被压缩内容的头部元信息行(已有 `rows=… · method=…`)与 recall_hint 中的 result_id 提取,形如 `[collapsed: table 12,000 rows × 8 cols · retrieve_result(result_id=…)]`。
- 证据:JetBrains(masking 保留可回取指针近零损失)、Anthropic("re-fetchable" 原则)、G9/G4。
- 风险:桩解析依赖摘要头格式,需容错(解析失败退回现占位符)。

### P0-4 compaction 保数据态(recovery.py + agent_loop 接缝)
- `_summarize_for_compact` 的输入不再只取末尾 24k chars:改为**头尾拼接**(头部含用户原始请求与早期 schema 结论,尾部含最近工具结果),或按消息分桶采样。
- 摘要 prompt 升级为结构化 handoff 模板(Codex 风格),固定分节:①任务目标与硬约束 ②已读文件与各表 schema(列名/类型/行数)③kernel 现存 DataFrame 变量清单(名 → shape/列指纹)④关键数值结论与已确认口径(metric_contract)⑤未决事项 ⑥可回取 result_id 清单。
- 其中 ③ 需要 kernel manager 提供变量自省接缝(一次性 `%who_ls` 等价请求,失败降级为空节);⑥ 可从 ResultStore 索引取仍存活条目。
- 证据:Codex compact 模板("critical data, examples, or references needed to continue")、Claude Code compaction 后重读 5 个文件、ACE 的 context collapse 失败模式、Anthropic "摘要必须保留 task objectives/key constraints/decisions/unresolved issues"。
- 这是长会话质量的最大杠杆:数据分析会话 compaction 后最致命的丢失不是对话,而是"有哪些数据、长什么样、在哪取回"。

### P1-1 kernel 变量地图 + 跨结果 schema 去重(kernel_main.py + manager.py + python_exec.py + render.py)
- auto-hook 从"仅 `result`"扩展为"摘要所有**新增或变化**的顶层 DataFrame/Series"(用 `id()`/行数+列指纹缓存判定,避免每轮重复摘要同一 df);摘要头部带变量名(`### orders (DataFrame, 1.2M×8)`),建立"变量名 → 数据"的可追踪 provenance。
- 同 schema 重复结果:render 层做 delta(与近期同列集结果比对,只渲染变化列统计 + "schema 同 result_xxx")。serving 侧可在 ResultStore 加 schema 指纹索引。
- 证据:PET-SQL 两段式剪枝(4.89→1.60 表)、BI 侧 schema 只注入一次、字典编码类比(重复结构用引用代替)。
- 收益:多轮分析中最大宗的重复 token 消耗;同时给 P0-4 的 ③ 提供数据源。

### P1-2 retrieve_result 查询下取(retrieve_result.py + result_store.py)
- 在行分页之上增加结构化回取参数(对已缓存的表格文本):`columns=[...]` 列投影、`filter="col>值"` 简单谓词、`mode=head|tail|sample(k)`。stdlib 实现于缓存文本重解析(复用 text_summary 的表格解析器)。
- 工具描述同步引导:"要精确聚合请在 kernel 里算;要明细切片用本工具谓词模式"。
- 证据:Vanna intermediate SQL(按需查 distinct)、TabSQLify(查询下推)、Anthropic PTC(200KB→1KB)、MCP resource_link 思想。
- 风险:谓词语法保持极简(单列单比较),避免造出半吊子 SQL。

### P1-3 半结构化(JSON/JSONL)形态摘要(text_summary.py)
- 检测 JSON/JSONL 输出 → 生成结构骨架(键路径、类型、数组长度分布)+ 代表元素采样 + 计数;失败退回现有文本 digest。
- 证据:第一轮调研文档形态 2 的既定方向;工业界无现成可抄,stdlib 可实现。
- 价值:API/日志类数据源接入的前置能力。

### P1-4 fidelity 阶段/压力自适应(config.py + agent_loop)
- `context_pressure > 0.7` 时自动降 fidelity(low);报告生成阶段(reporting 域工具激活时)对关键表升 high。实现上:compact 请求带 stage hint,compactor 在 config 之上做档位覆盖。
- 证据:ACON(observation/history 解耦压缩策略)、当前 accept_ratio 压力门控的自然延伸。

### P2-1 压缩-保真评测闭环(examples/eval_tasks + evolution/)
- 新增"摘要问答"评测任务集:同一批数据问题,分别在(a)全量上下文(b)当前摘要(c)不同 fidelity 档位下作答,度量准确率差与压缩比;纳入 evolution A/B 或独立脚本。
- 证据:TAP4LLM 消融方法论、JetBrains 对照设计。没有这个闭环,以上所有调参都是盲调。

### P2-2 位置与缓存策略
- "数据地图"(P1-1 的变量清单 + 各表一行摘要)在 compaction 后重注入到靠近 system prompt 的位置(或 system prompt 末尾 cache 断点处),长会话中让 schema 常驻强注意力区。
- 证据:Anthropic 长上下文指南(长数据顶部 +30%)、system prompt sticky、cache 断点放 system 末尾的官方建议。

### P2-3 序列化格式 A/B 开关(render.py + config)
- render 支持 `format=markdown|compact-kv`(紧凑键值:每行 `col1=v1; col2=v2`),默认 Markdown,配置切换,配合 P2-1 评测定夺。
- 证据:Table Meets LLM(HTML 最优)与实务基准(Markdown-KV 最准)结论相反 → 只能自测。

### 暂缓项(明确不做/后置)
- **sketch 库(KLL/t-digest/HLL)**:pandas 全量在内存时精确统计永远更优;仅在接入流式/SQL 直连数据源时再引入(届时 KLL/DDSketch 输出文本化是空白且可差异化的方向)。
- **LLMLingua 类 token 删除**:对数值风险高(删位 = 数据损坏),仅未来对叙述性内容考虑。
- **embedding 语义采样(TAP4LLM semantic)**:依赖向量模型,与零依赖约束冲突;centroid 采样可在 P1-1 后以纯 pandas K-Means 轻量试验。

---

## 四点五、落地状态(2026-08-28,openspec change context-compression-upgrade)

| 提案 | 状态 | PR |
|---|---|---|
| P0-1 数字呈现(千分位/3 位有效/防科学计数法) | ✅ 已落地 | #56 |
| P0-2 L1 统计补强(cardinality/直方图/粒度/全列离群/identifier) | ✅ 已落地 | #56 |
| P0-3 触发压力自适应 + 回取页豁免 + collapse 桩 digest | ✅ 已落地 | #57 |
| P0-4 + P2-2 compaction 保数据态(handoff 模板/头尾摘要/重注入) | ✅ 已落地 | #58 |
| P1-1(4a) kernel 变量地图(快照去重 + variable 溯源) | ✅ 已落地 | #59 |
| P1-1(4b) 同 schema delta 渲染 | ⏸ 缓行(见下) | — |
| P1-2 retrieve_result 查询下取(单谓词/投影/采样) | ✅ 已落地 | #60 |
| P1-3 JSON/JSONL 结构骨架摘要 | ✅ 已落地 | #61 |
| P1-4 fidelity 压力自适应(≥0.75 降档 low) | ✅ 已落地 | #62 |
| P2-1 压缩-保真评测闭环(三臂 + CompactionStats + 8 任务) | ✅ 已落地 | #63 |
| P2-3 render_format=kv A/B 臂 | ✅ 已落地 | #63 |
| D0 解耦(适配器去触发镜像,用户追加的一等目标) | ✅ 已落地 | #57 |

**4b 缓行理由**:delta 渲染的收益取决于真实会话中"同变量同 schema 重复摘要"的频率,
4a 的 variable 溯源 + D4 数据态已消除大部分重复计税;应先用 compare-sampling 评测臂
观测 4a 后的剩余重复量,再决定是否投入(避免为不确定收益引入渲染复杂度)。

暂缓项维持不变:sketch 库(ADR 0001)、LLMLingua、embedding 语义采样。

## 五、Caveats

- Chroma 为向量库厂商,Context Rot 有商业立场,但其 18 模型评测与 Liu et al. 学术结论互相印证;位置效应部分(Chroma 未观察到 vs Liu U 形)存在任务相关性张力,落地时以 Anthropic 官方建议(数据顶部/指令底部)为准并定期复测。
- 格式对比证据互相矛盾(模型/任务依赖),P2-3 的 A/B 是唯一可靠裁决。
- CARE 反例基于因果发现场景,外推到通用列统计需谨慎;但"统计 + 具象行混合"的稳健性同时被 TAP4LLM 与 CARE 支持。
- 千分位/有效位数结论中,有效位数部分是工程推断(文献空白),按"与误差契约一致"原则执行即可。
- JetBrains 结论基于 SWE-bench(编码域),对数据分析会话的迁移假设"过程性工具输出可 mask"成立,但数据类 tool result 的桩必须带回取指针(否则违反"数值事实必须可审计"原则)。

## 六、主要一手来源

**Context engineering / 官方工程资料**
- Anthropic《Effective context engineering for AI agents》 https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic《Writing tools for agents》(Claude Code tool response 25k token 限额) https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic《Advanced tool use》(PTC 200KB→1KB、token -37%) https://www.anthropic.com/engineering/advanced-tool-use
- Anthropic context editing(beta) https://platform.claude.com/docs/en/build-with-claude/context-editing ;server compaction https://platform.claude.com/docs/en/build-with-claude/compaction ;prompt caching https://platform.claude.com/docs/en/build-with-claude/prompt-caching ;长上下文技巧(数据顶部/查询底部 +30%) https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/long-context-tips
- Claude Code 文档(context window/compaction 幸存表/best practices) https://code.claude.com/docs/en/context-window 、https://code.claude.com/docs/en/best-practices
- Chroma《Context Rot》(Hong, Troynikov, Huber 2025) https://www.trychroma.com/research/context-rot
- Lost in the Middle(Liu et al.) https://arxiv.org/abs/2307.03172 ;1M token 多跳研究 https://arxiv.org/html/2605.02173v1
- JetBrains Research《Efficient Context Management》(masking vs 摘要对照) https://blog.jetbrains.com/research/2025/12/efficient-context-management/
- ACE(arXiv:2510.04618)、ACON(arXiv:2510.00615)、LLMLingua 系列 https://github.com/microsoft/llmlingua

**编码 agent 源码级证据**
- OpenAI Codex:DEFAULT_MAX_OUTPUT_TOKENS=10k、truncate_middle、compact handoff 模板(github.com/openai/codex,codex-rs/)
- Gemini CLI:40k chars 阈值、head 20%/tail 80%、/compress 保留尾部 30%、reverse token budget 50k(github.com/google-gemini/gemini-cli)
- Aider:头尾切分 + 弱模型摘要(github.com/Aider-AI/aider,aider/history.py)
- Open Interpreter:2800 字符保尾截断 + summarize 引导(github.com/openinterpreter/openinterpreter)

**工业界数据 Agent**
- OpenAI ChatGPT plugins(沙箱执行模型) https://openai.com/index/chatgpt-plugins ;Anthropic analysis tool https://www.anthropic.com/news/analysis-tool ;code execution tool https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool
- Databricks Genie https://docs.databricks.com/aws/en/genie/best-practices 、https://docs.databricks.com/aws/en/genie-agents/tune-quality
- Power BI Copilot grounding(schema reduction、描述 200 字符截断、min/max 聚合) https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-semantic-models
- Snowflake semantic view YAML(sample_values/is_enum) https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec
- PandasAI 序列化器(github.com/sinaptik-ai/pandas-ai,pandasai/helpers/dataframe_serializer.py);Vanna DDL 14k 预算 + intermediate_sql(github.com/vanna-ai/vanna);Hex Data Manager https://hex.tech/blog/data-manager/ ;Deepnote https://deepnote.com/docs/sql-generation
- PET-SQL(每表 3 行值、4.89→1.60 表) https://arxiv.org/html/2403.09732v1 ;DIN-SQL https://arxiv.org/abs/2304.11015 ;RESDSQL https://arxiv.org/html/2302.05965v3
- MCP 分页规范 https://modelcontextprotocol.io/specification/2025-06-18/server/utilities/pagination

**数据编码 / 统计压缩**
- Table Meets LLM(WSDM'24,HTML 最优、问题在表前 −6.81%) https://arxiv.org/html/2305.13062v4
- TAP4LLM(采样+增强+打包,5:5~4:6 配比) https://aclanthology.org/2024.findings-emm.603/
- 数字 tokenization 与千分位(84.4%→98.9%) https://arxiv.org/abs/2402.14903
- TabSQLify https://arxiv.org/abs/2404.10150 ;ATF(压 70% cells,OOD 升/域内降) https://arxiv.org/html/2506.23463v3 ;CARE(充分统计量反例) https://arxiv.org/html/2511.16016
- 实务 11 格式基准 https://www.improvingagents.com/blog/best-input-data-format-for-llms/ ;Table Serialization Kitchen https://www.daniel-gomm.com/blog/2025/Table-Serialization-Kitchen/
- Apache DataSketches:KLL 精度/大小 https://datasketches.apache.org/docs/KLL/KLLAccuracyAndSize.html ;KLL vs t-digest https://datasketches.apache.org/docs/QuantilesStudies/KllSketchVsTDigest.html ;Frequent Items https://datasketches.apache.org/docs/Frequency/FrequentItemsOverview.html ;DDSketch https://www.vldb.org/pvldb/vol12/p2195-masson.pdf
- TFDV https://www.tensorflow.org/tfx/data_validation/get_started ;ydata-profiling https://github.com/ydataai/ydata-profiling
