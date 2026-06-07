# 数据分析 Agent 内部"采样式摘要"机制深度调研报告

## TL;DR
- 没有单一方法能同时做到"高压缩"和"高保真";正确做法是**分层组合**:用 sketch 算法(t-digest/HyperLogLog/Count-Min)以近常量空间无损保留分布、基数与高频项的统计量,用分层+异常保留的代表性采样保留明细行的"可读样本",用 schema/metadata-first 摘要保留结构,再按数据形态(表格/半结构化/文本)差异化序列化给 LLM。
- 经典统计与数据库领域已有成熟、可单遍/流式构建且可合并的算法(蓄水池采样、KLL/t-digest/DDSketch 分位数、HyperLogLog 基数、Count-Min 频率、coreset),它们是"采样式摘要"模块的算法地基;LLM 工程侧(Anthropic compaction、微软 TAP4LLM、微软 LIDA、lost-in-the-middle / context rot 研究)给出了"怎么喂给模型"的工程范式。
- 两个应用场景应采用不同策略:**上下文中间压缩**追求"可还原 + 可继续推理",应偏重统计 sketch + 结构化笔记 + 窗口外存储;**最终结论生成**追求"全局有效结论无偏",应偏重分层/加权采样 + 异常保留 + 分位数摘要,并显式告诉 LLM 这是采样结果及其误差界。

## Key Findings

1. **"采样"与"摘要"是两类互补手段。** 采样(reservoir / stratified / weighted)产出原始明细的子集,LLM 可直接读到"真实的行";摘要 / sketch(t-digest / HLL / CMS)产出统计结构,体积近乎常数但丢失个体明细。生产级方案需要二者叠加。

2. **分位数 sketch 是表格数值列摘要的核心。** t-digest 对极端分位数(p99/p1)误差极小且全浮点精度;DDSketch 提供相对误差保证且完全可合并;KLL 是理论最优 rank-error 的流式分位数算法。三者都是单遍、可合并的,天然适合流式/分布式取数结果。

3. **基数与频率用专门 sketch。** HyperLogLog 原论文(Flajolet 等 2007)指出其可"用仅 1.5 千字节内存估计远超 10^9 的基数,典型精度 2%",标准误差约 1.04/√m;Count-Min Sketch 用次线性空间估计频率并解决 heavy-hitters(高频项)问题。这正好覆盖"列有多少不同值""哪些值最高频"两个 LLM 极常问的问题。

4. **数据库 AQP 已经把"采样代替全量"工程化。** BlinkDB(EuroSys 2013 最佳论文)摘要明确:"可在多达 17 TB 数据上于 2 秒内回答查询(比 Hive 快逾 200×),误差 2–10%"——这是"用采样数据生成全局结论"思路的直接、可量化的先例。

5. **LLM 侧的核心约束是 context rot / lost-in-the-middle。** Chroma 研究报告《Context Rot》(2025)原文:"我们评测了 18 个 LLM,包括 GPT-4.1、Claude 4、Gemini 2.5 和 Qwen3……结果显示模型并不均匀地使用上下文;随着输入变长,其性能越来越不可靠。"结论:把全部明细塞进上下文不仅贵,还会主动降低结论质量——这从根本上证成了"采样式摘要"的必要性。

6. **LLM 工程界已有专门的表格采样 + 摘要方案。** 微软 TAP4LLM(EMNLP Findings 2024)系统比较了采样、增强、打包三阶段,论文原文称其"相比将原始表直接输入 LLM,平均提升准确率 7.93%";微软 LIDA 用一个 SUMMARIZER 模块把数据集压成"紧凑但信息密集的自然语言摘要"作为所有下游 LLM 操作的 grounding。schema-first / describe()-first 是社区通用做法。

## Details

### 一、主线 A:经典统计 / 数据库的采样与摘要理论

#### A1. 概率采样方法

- **简单随机采样(SRS)**:无偏、实现简单,是一切的基线。缺点:对偏态分布和稀有类别保真差,小样本下可能完全漏掉离群值和长尾类别。

- **蓄水池采样(Reservoir Sampling,Vitter 1985)**:在不预先知道总量 N 的流式数据上,用 O(k) 常量空间单遍抽取 k 个等概率无放回样本。Vitter 的 Algorithm Z 把朴素版本加速约一个数量级,期望时间 O(n(1+log(N/n))),为最优(常数因子内)。**这是 Agent 处理"边取数边来、不知道总行数"的中间结果的首选采样原语。** 加权变体(A-Res / Efraimidis-Spirakis)支持按重要性加权抽样。

- **分层采样(Stratified Sampling)**:先按关键维度(类别列、时间桶、数值分桶)分层,再层内抽样。**比例分配**让每层样本量正比于层大小;**Neyman / 最优分配** nh ∝ Nh·Sh,把更多样本投到方差大的层,在固定总样本下最小化估计方差(Neyman 1934)。这是"既要覆盖所有类别、又要保留高方差区域细节"的理论工具。注意 Neyman 假设每层数据充裕;当某些层很小("bounded strata")时需用 VOILA / S-VOILA 等变体,后者还给出了流式分层采样的局部方差最优算法。

- **系统采样(Systematic)**:按固定间隔抽取,实现简单,但若数据有周期性会产生偏差。
- **聚类采样(Cluster)**:抽取整组而非个体,降低取数成本但增大方差。
- **加权采样(Weighted / Importance)**:按重要性(如金额、频次、leverage score)赋权抽样,是 coreset 的基础。

#### A2. 数据草图 / Sketch 算法

- **HyperLogLog(基数估计,Flajolet 等 2007)**:用调和平均与 m 个寄存器,标准误差约 1.04/√m;原论文称用 1.5KB(对应 m=2048、约 2% 标准误差)即可估计远超 10^9 的基数,可合并。Ertl(2017)给出对全基数范围无偏的改进估计器(基于极大似然)。
- **Count-Min Sketch(频率估计,Cormode-Muthukrishnan 2005)**:二维计数数组 + 多个 pairwise-independent 哈希,空间 O((1/ε)·ln(1/δ)),宽 w=⌈e/ε⌉、深 d=⌈ln(1/δ)⌉,查询取多行最小值;只会高估不会低估,适合 heavy-hitters。
- **Bloom Filter**:成员存在性测试,可用于去重/"这个值出现过吗"。
- **MinHash / LSH(Broder 1997)**:用紧凑签名估计 Jaccard 相似度,签名位匹配概率 = Jaccard 相似度;LSH 把近重复检测从 O(n²) 降到接近线性。**文本类结果去重的标准工具**,大模型训练语料去重普遍使用(Milvus、GPT-3 语料去重等)。
- **AMS / Theta Sketch**:AMS 估计 frequency moments(如 L2);Theta Sketch(Apache DataSketches)支持集合并/交/差运算的基数估计,比 HLL 更灵活。

#### A3. 分位数与分布摘要

- **直方图(equi-width / equi-depth)**:最直观的分布摘要。等深(等频)直方图即分位数桶,对偏态更鲁棒。
- **t-digest(Dunning 2019)**:用 1 维 k-means 式聚类构建质心,scale function 让极端分位数(q→0/1)桶更小因而误差极小;对极端分位数达 ppm 级精度、中部分位数通常 <1000 ppm,可合并、全浮点精度。缺点:无最坏情况理论保证(worst-case error 可无界)。
- **KLL sketch(Karnin-Lang-Liberty 2016)**:理论最优,空间 O((1/ε)·log log(1/δ)) 即可保证 rank 误差 εn;Apache DataSketches 有生产实现。
- **GK(Greenwald-Khanna 2001)**:确定性,O((1/ε)·log(εn)) 空间,无随机失败概率。
- **DDSketch(Datadog,PVLDB 2019)**:论文称其为"首个具备形式化保证、完全可合并的相对误差分位数 sketch……已被 Datadog 大规模生产使用"。Datadog 工程说明:相对精度设 1%、期望分位值为 100 时,"计算出的分位值保证落在 99–101 之间"。对重尾分布优于 rank-error sketch。
- **Q-digest**:基于二叉树的分位数摘要,适合整数域。

**选型要点**:要极端分位数精度→t-digest;要相对误差保证和可合并→DDSketch;要最坏情况理论保证→KLL / GK。

#### A4. 数据缩减 / 代表性子集

- **Coreset(核心集,Har-Peled-Mazumdar 2004)**:加权小子集 C,使任意候选解的代价在 (1±ε) 内被保持。k-means coreset 大小可做到 O(k/ε²),与 n 无关;通过 importance / sensitivity sampling 构建,支持 merge-and-reduce 流式维护。**"用极小子集近似全量聚类/统计"的最强理论工具。**
- **k-means / 聚类代表点**:每簇取质心或最近真实点作代表,天然兼顾"覆盖 + 多样性"。TAP4LLM 的 centroid-based sampling 即此思路。
- **Leverage score sampling**:按行/列对低秩结构的"杠杆"赋权抽样,保留矩阵关键结构,用于降维与列选择。
- **DPP(行列式点过程)**:按子集相似度矩阵的行列式概率抽样,天然惩罚冗余、鼓励多样性,用于文档摘要、图像检索、推荐的多样性子集选择;精确采样代价高(O(n³)),有 Nyström / leverage 近似与贪心确定性变体。
- **原型选择 + 异常保留**:代表性样本(prototypes)+ 显式保留离群点(outliers),保证摘要既覆盖"典型"又不丢"异常"。

#### A5. 数据库近似查询处理(AQP)

- **BlinkDB**:预计算多维多分辨率分层样本,运行时按查询和误差/时延约束动态选样本;摘要数据:17 TB 数据 <2 秒、比 Hive 快逾 200×、误差 2–10%(TPC-H 与 Conviva 真实负载评测)。
- **Online Aggregation**:边扫描边给出不断收窄的估计值与置信区间,用户可随时停止——对 Agent"渐进式给结论"很有借鉴。
- **Wavelet / 直方图 synopses、data cubes、materialized samples**:预聚合与预采样,把全量压成可快速查询的小结构。

#### A6. 维度归约 / 列压缩

- **PCA / 随机投影**:把高维数值降到少数主成分,保留方差结构(但牺牲列可解释性)。
- **特征选择 / 关键列识别**:按方差、与目标相关性、互信息、缺失率筛列;TAP4LLM 的 column grounding 即按查询相关性选列。
- **列裁剪(projection pushdown)**:只取查询/任务相关列,是"减少列"维度最直接的工程手段。

### 二、主线 B:LLM / Agent 工程的上下文压缩与数据摘要实践

#### B1. Context compaction / compression

Anthropic 在《Effective context engineering for AI agents》中明确:**compaction** 是把接近上下文上限的对话总结后开启新窗口;在 Claude Code 中把消息历史传给模型总结,保留架构决策、未解 bug、实现细节,丢弃冗余工具输出,再带最近 5 个文件继续。原则:"先最大化 recall 捕获全部相关信息,再迭代提高 precision"。其风险被明确点出:"过度激进的 compaction 会丢失当时看似次要、后来才显关键的上下文"。LangChain 把上下文工程归纳为 write / select / compress / isolate 四类。Anthropic 的 Managed Agents 进一步主张把上下文作为活在窗口外、可用 getEvents() 按需切片的会话日志对象,避免 compaction 的不可逆决策——这对"中间压缩需可还原"的需求极有参考价值。

#### B2. Agentic 数据分析工具如何处理大结果集

社区主流是 **schema + sample rows** 模式:把 df.head() / df.describe() / df.info() 和列名类型给 LLM,而非全量明细。LangChain 的 create_pandas_dataframe_agent 默认只把有限头部行(number_of_head_rows)放进 prompt,让模型生成在完整 df 上执行的代码——**计算在确定性的 pandas/SQL 里做,LLM 只看摘要和 schema**。对超大 df,实践是先 filter/聚合再喂,或分块迭代(社区反复踩到"把 50 万行全塞进 prompt 撑爆 token"的坑)。

#### B3. LLM 处理表格的方法

两篇 survey(arXiv:2402.17944、2402.05121)总结:表格须先 serialization 成线性文本;常见格式有 CSV、Markdown、JSON、HTML、XML、NL+分隔符;**Markdown 是文献中最常用格式**,而 HTML/XML 被 GPT 类模型理解更好但更费 token。LLM 难以理解二维结构、且数值推理易错(建议转交代码/SQL)。

**微软 TAP4LLM(EMNLP Findings 2024)**是最贴合本需求的工作,分三模块(经一手核验论文 v3):
- **Table Sampling**:分 rule-based(random、evenly[顶/底交替向中间取以均匀覆盖]、content snapshot)、embedding-based(semantic-based + column grounding、centroid-based[K-Means]、hybrid[语义 + 质心,权重 α=0.3、β=0.7])、LLM-based(decomposer,强但贵)三类。**结论:带列对齐的语义采样(semantic w/ column grounding)在所有数据集上最优;hybrid 综合最强(FEVEROUS 65.34%、TabFact 61.37% 为最佳);直接全量编码或截断到 4k 最差。** 高精度任务用语义采样,低时延/低算力用 rule-based(如 content snapshot)。
- **Table Augmentation**:补充元数据——维度/度量分类、语义字段类型、表大小、**统计特征(progression / string / number-range / distribution 四组,含 variance、range、cardinality、change rate、major)**、表头层级;以及检索增强(Wikipedia 文档、术语解释)和自一致性自提示。统计特征与文档引用增益最大(TabFact +2.87%、SQA +5.13%、ToTTo +4.07%);表大小与表头层级几乎无益甚至有害。
- **Table Packing**:控制 token 分配。**最优"表内容:增强信息"token 配比约为 5:5 或 4:6;过度偏向增强(如 3:7)会因边际递减而掉点。** 采样后每问平均 token 用量 SQA 637 / FEVEROUS 512 / TabFact 417 / HybridQA 742。整体平均带来 7.93% 提升。

#### B4. schema-first / metadata-first

先给列名/类型/统计摘要(describe / info)而非原始明细,是降低 token 与 context rot 的第一原则。LIDA 的 SUMMARIZER 把数据集压成"紧凑但信息密集的自然语言摘要"作为所有后续操作的 grounding context,正是此模式的代表;其报告在 2200+ 可视化上的错误率 <3.5%(基线 >10%)。

#### B5. 长上下文性能退化

- **Lost in the Middle(Liu et al., TACL 2024)**:多文档 QA 与 key-value 检索中,相关信息在开头/结尾时性能最高、在中间时显著下降,长上下文模型也不能幸免。
- **Context Rot(Chroma 2025)**:评测 18 个前沿模型(GPT-4.1、Claude 4、Gemini 2.5、Qwen3),所有模型随输入变长而非均匀退化;degradation 在各长度增量都出现,不只在接近上限处(50K token 时 1M 窗口模型也会"腐烂")。
- 工程含义:**摘要不仅省钱,更是质量手段。** 把高信号、低体积的采样摘要放在上下文的开头/结尾,避免把关键统计埋在中间。

#### B6. RAG 对文本类结果的借鉴

文本/检索结果应借鉴 RAG 的 chunking + summarization + re-ranking:先分块,再对每块摘要,用 embedding 重排序选 top-k 高相关块,用 MinHash/LSH 去近重复,最后只把多样且高相关的片段给 LLM。这与表格的"分层采样 + 异常保留"在思想上同构:覆盖主题多样性(类比分层)+ 保留高相关/异常片段(类比异常保留)。

#### B7. 可视化/统计摘要作为 LLM 输入

把分布的统计量(均值、分位数、直方图桶、基数、top-k 频率项)用自然语言或紧凑结构描述给 LLM,比给原始明细更省、更准。LIDA 证明"data→紧凑摘要→LLM"管线可靠。

### 三、方法原理对比表

| 方法 | 适用数据形态 | 压缩维度 | 保真侧重 | 流式可行性 | 复杂度 | 工程落地难度 |
|---|---|---|---|---|---|---|
| 简单随机采样 | 表格/半结构/文本 | 减行 | 整体均值 | 高(蓄水池) | O(n) | 低 |
| 蓄水池采样 | 流式表格/任意 | 减行 | 等概率样本 | 原生单遍 | O(n),空间 O(k) | 低 |
| 分层采样(Neyman) | 表格(有类别/分桶) | 减行 | 类别覆盖 + 方差 | 中(需层统计,S-VOILA 可流式) | O(n) | 中 |
| 加权/重要性采样 | 表格/文本 | 减行 | 高价值行 | 中 | O(n) | 中 |
| HyperLogLog | 任意(列基数) | 极致(→KB) | 基数 | 原生单遍可合并 | O(n) | 低(有库) |
| Count-Min Sketch | 任意(频率) | 极致 | 高频项 | 原生单遍可合并 | O(n) | 低(有库) |
| MinHash/LSH | 文本/集合 | 高 | 相似度/去重 | 单遍构签名 | O(n·k) | 中 |
| t-digest | 数值列 | 高(→KB) | 极端分位数 | 原生单遍可合并 | O(n) | 低(有库) |
| DDSketch | 数值列 | 高 | 相对误差分位数 | 原生单遍可合并 | O(n) | 低(有库) |
| KLL / GK | 数值列 | 高 | rank 误差保证 | 原生单遍 | O(n) | 中 |
| 直方图(等深) | 数值/类别列 | 中高 | 分布形状 | 单遍可近似 | O(n) | 低 |
| Coreset | 数值表/向量 | 高 | 聚类/统计代价 | merge-reduce 可流式 | 中高 | 高 |
| DPP / 多样性采样 | 文本/向量/行 | 中 | 多样性 | 弱(矩阵运算) | O(n³)~近似优化 | 高 |
| PCA/降维 | 数值表(列) | 减列 | 方差结构 | 中(增量 PCA) | 中 | 中 |
| 列选择/裁剪 | 表格(列) | 减列 | 任务相关列 | 高 | 低 | 低 |
| LLM 摘要(compaction) | 文本/任意 | 高 | 语义要点 | N/A | 一次 LLM 调用 | 中 |

### 四、三种数据形态的差异化推荐策略

#### 形态 1:结构化表格(DataFrame / SQL 结果集)

**减行**:
1. 先算全列统计摘要(schema + describe:每列 count / 类型 / 缺失率 / min / max / 均值 / 标准差 + t-digest 分位数 + HLL 基数 + CMS top-k 频率项)。
2. 分层采样代表性明细行:按关键类别列/数值分桶分层,Neyman 分配,层内蓄水池采样;**显式追加离群行**(由 t-digest 的 p1/p99 或 IQR 规则识别)。
3. 序列化:小样本用 Markdown(社区最常用),需要强结构理解时用 HTML/XML(更准但更费 token)。

**减列**:列裁剪(只留任务相关列)+ 关键列识别(方差/相关性/缺失率)+ 必要时 PCA。对宽表优先减列。

#### 形态 2:半结构化(JSON / 嵌套 / API / 日志)

1. **结构摘要优先**:推断并给出 JSON Schema / 路径骨架(键路径、类型、出现频率、数组长度分布),而非原始嵌套全量。
2. **数组/列表**:按蓄水池采样取代表元素 + 给数组长度的分位数(t-digest);对对象数组可拍平成表后走表格策略。
3. **日志**:用 Count-Min / HLL 估计模板频率与基数,用 MinHash/LSH 聚类相似日志行,每簇取代表 + 计数;保留异常/错误级别行(异常保留)。
4. 序列化:保留键路径的紧凑 JSON 或 YAML,附"此为采样,数组原长 N"的元信息。

#### 形态 3:搜索类 / 文本类结果

1. **去重**:MinHash/LSH 去近重复片段。
2. **分块 + 摘要**:chunk → 每块摘要。
3. **多样性 + 相关性选择**:embedding 重排序选高相关,DPP / MMR 保多样性,避免同质冗余。
4. **位置策略**:把最重要片段放上下文开头/结尾(对抗 lost-in-the-middle)。
5. 给 LLM 的是"摘要 + 少量高相关原文片段 + 来源标识",而非全部命中。

### 五、Agent 内部"采样式摘要"模块分层设计

建议四层流水线(对任意中间结果统一入口):

**L0 — 元信息层(永远保留,体积最小)**:数据形态、总行/列数(或流式累计计数)、schema(列名 + 类型)、本结果是否为采样及采样方法。

**L1 — 统计摘要层(sketch,单遍可合并)**:
- 数值列:t-digest 或 DDSketch(分位数 p1/p25/p50/p75/p99)+ min/max/mean/std。
- 任意列:HyperLogLog(基数)+ Count-Min / top-k(高频项)。
- 类别列:value_counts top-k + 长尾基数估计。
- 这一层体积近乎常数,**无损保留分布/基数/高频项**,是"准确还原全貌"的主力。

**L2 — 代表性明细层(采样,给 LLM"看得见的真实行")**:
- 分层(Neyman)+ 蓄水池采样 → 覆盖整体分布与所有类别。
- 显式异常保留 → 由 L1 分位数/IQR 识别离群行并追加。
- 可选 coreset / 质心点 → 兼顾多样性。

**L3 — 序列化层(让 LLM 可读可推理)**:
- 表格→Markdown/HTML;半结构→紧凑 JSON/YAML;文本→摘要 + 片段。
- 每个数值列附 L1 统计;每个采样块标注"采样自 N 行,方法=X,误差界=ε"。

#### 压缩率 vs 保真度可调权衡

- 单一旋钮 `fidelity_level ∈ {low, mid, high}` 映射到:采样行数 k、sketch 精度(t-digest 压缩参数 δ、HLL 寄存器数 m、CMS 的 w/d)、top-k 的 k、token 预算。
- 借鉴 TAP4LLM:**"采样明细 : 统计/增强"token 配比约 5:5 或 4:6 最优**,不要把 token 全给明细或全给元数据。

#### 两个场景的差异化

| 维度 | 场景1:上下文中间压缩 | 场景2:最终结论生成采样 |
|---|---|---|
| 目标 | 可还原、可继续推理 | 全局结论无偏 |
| 偏重 | L0+L1(结构 + 统计)+ 结构化笔记 | L1+L2(分位数 + 分层/加权/异常采样) |
| 不可逆性 | 尽量可逆(存原始于窗口外,Anthropic Managed Agents getEvents 思路) | 可有损,但须报告误差界 |
| 关键风险 | compaction 丢失后续才显关键的细节 | 采样偏差导致错误全局结论 |
| 对 LLM 的标注 | "这是压缩历史" | "这是 N 行的采样,置信区间 ±x%" |

#### 流式 / 单遍场景

数据太大无法全量加载时:**蓄水池采样(代表行)+ t-digest/DDSketch(分位数)+ HyperLogLog(基数)+ Count-Min(频率)全部单遍并行构建,且都可合并**——天然适配分布式分片 map→merge。这正是 Datadog、BlinkDB、Apache DataSketches 的生产模式。

#### 让摘要对 LLM "可用于推理"

- 优先紧凑且自描述的格式;数值统计用明确标签("p99 latency = 1234ms")。
- 显式声明采样事实与误差,防止 LLM 把样本当全量得出过度自信结论。
- 关键统计放上下文开头/结尾(对抗 context rot)。

## Recommendations

**阶段一(MVP,1–2 周):** 实现 L0+L1+L2 最小版:对每个表格中间结果输出 schema + describe + t-digest 分位数 + HLL 基数 + top-k 频率 + 分层蓄水池采样的 N 行(N 默认 20–50)+ 异常行,序列化为 Markdown。直接用现成库(t-digest、Apache DataSketches、ddsketch)。阈值:当结果 > 某 token 阈值(如 2k token)时触发摘要,否则原样传。

**阶段二(差异化,2–4 周):** 区分两个场景;给中间压缩加"窗口外存储 + 按需回取"(参考 Anthropic Managed Agents);给结论生成加误差界标注与分层/Neyman 分配。半结构化与文本形态接入(JSON schema 推断、MinHash 去重 + 重排序)。

**阶段三(优化):** 引入 query-aware 的语义采样与 column grounding(参考 TAP4LLM,带列对齐的语义采样最优);调参 token 配比到 5:5~4:6;A/B 评测结论准确率与 token 成本。

**触发阈值与改变决策的基准:** (1) 若下游结论准确率在采样下相比全量下降 > 3–5%,提高 fidelity_level 或改用 Neyman 分配并增大高方差层样本;(2) 若 token 成本仍是瓶颈,降 top-k 与采样行数、增大 sketch 压缩;(3) 若数值结论(求和/计数/比率)需精确,**不要让 LLM 从样本算,改为在确定性 pandas/SQL 上算后只把结果给 LLM**(社区共识);求基数用 HLL、求频率用 CMS,而非让 LLM 从样本推断。

## Caveats

- **采样必然有偏差风险**:对极稀有事件、精确求和/去重计数,采样会错;此类必须走精确计算(或 HLL 给基数、CMS 给频率)而非让 LLM 从样本推断。
- **t-digest 无最坏情况保证**:对抗性/极端分布可能误差大;需严格保证时用 KLL/GK/DDSketch(后者有相对误差形式化保证)。
- **部分 LLM 工程结论来自厂商博客而非同行评审**(Anthropic、Datadog、Chroma、Morph 等),存在营销倾向;但其核心技术主张与学术研究(lost-in-the-middle、KLL、t-digest 等)一致,可信度较高。Context rot 研究由 Chroma(向量库厂商)发布,需注意其商业立场,但 18 模型评测方法与 Liu et al. 的学术结论互相印证。
- **TAP4LLM 的"HTML/XML 最佳"结论是引用自 Sui et al. 2023 前作,而非该论文新做的格式对比实验**;Markdown 仍是社区最常用格式,具体最优格式取决于模型与任务,应自行 A/B。
- **新一代模型可能缓解 lost-in-the-middle**:有 2025 年研究报告 Gemini 2.5 Flash 在简单事实检索(needle-in-haystack)上已不显著受位置影响;故位置策略的收益会随模型演进而变化,应定期复测。
- 本报告未能就 OpenAI Code Interpreter 的内部大文件处理机制、以及 RAG 重排序的最新具体方案做一手核验(搜索预算耗尽),相关论断基于通用工程实践与社区资料,落地前建议补充验证。