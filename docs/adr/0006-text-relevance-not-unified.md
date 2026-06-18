# 0006 — 文本相关性匹配刻意不统一(各 matcher 本地分词/打分)

- 状态: Accepted (2026-06-18)

## 背景

阶段二落地后,代码库里有 4 处做"文本相关性匹配"的逻辑:

| 站点                                        | 分词器                                  | 打分                                                   | 返回       |
| ------------------------------------------- | --------------------------------------- | ------------------------------------------------------ | ---------- |
| `memory/store.search`                       | `[\w一-鿿]+` 整段 CJK 作一 token        | key 子串(+3)+ token overlap×2 + 逐 token 子串 + 复用度 | top-k 排序 |
| `evolution/synthesizer.keywords`/`_overlap` | 拉丁词 + CJK **bigram 切片** − 停用词表 | overlap coefficient(显式弃用 Jaccard),阈值 0.4         | 聚类       |
| `evolution/evaluator.relevant_tasks`        | 无分词                                  | 纯布尔子串                                             | 过滤       |
| `skills/registry.match_best`                | `str.split()` 空格切分 + phrase 子串    | phrase 子串(+3)+ token in text(+1)+ 优先级             | 单个最优   |

一次架构复审(`improve-codebase-architecture` 技能,候选⑤)提议把这些抽成一个统一的
`text_relevance` 模块,集中"CJK 感知分词 + 相似度"。本 ADR 记录为何**不这么做**,
以免未来复审反复提同一建议。

## 决策

**不抽取统一的文本相关性模块。各 matcher 的分词与打分保持在各自调用点本地实现。**

唯一的就地改进(不构成统一的理由):`skills/registry.match_best` 原用 `query.split()`
对中文无效(中文无空格,整条 query 变成一个无法匹配的 chunk),补一个**本地** `_CJK_RUN`
正则 + `_cjk_bigrams` 把 CJK run 切成 2-gram;与 split 出来的 term 去重,保证纯 ASCII
路由字节不变。该正则**故意本地定义**,不跨包复用 synthesizer 的同名正则(`skills ✗→
evolution` 依赖规则),也不抽公共 util——这正是本 ADR 的立场在代码里的体现。

## 理由

1. **删除测试(deletion test)**:假想的 `text_relevance` 一旦删掉,复杂度不会"集中",
   只会把按用途调校的逻辑散回 4 个调用点——而那本就是它们该在的地方。复杂度没有被一个
   深模块吸收,说明它不是一个真接缝。
2. **深浅判据(Ousterhout)**:4 处需要 3 种不同分词粒度(整段 / bigram / 空格)和 4 种
   不同打分(加权 overlap+子串+recency / overlap coefficient+阈值 / 布尔 / phrase+token
   加权+优先级)。统一接口必须暴露"分词模式 × 打分策略 × 停用词 × 阈值"的并集——**接口
   复杂度 ≈ 四处实现之和,即浅模块**。统一只会把易错的"调校参数"与"机制"分离,读懂一次
   相关性判断反而要在 util 和调用点之间来回跳,locality 变差。
3. **独立演化的证据**:每处的 CJK 问题都是各自修的(store 加子串兜底;synthesizer 改
   bigram + overlap coefficient;registry 本次加 bigram)。它们因需求不同而分别演化;
   过早统一会把这些本应独立的修复耦死。
4. **唯一真正同源的原子**只是 store 的一行 `_WORD` 正则,且仅一个调用方——
   "一个 adapter = 假想接缝,不是真接缝"。

## 影响

- 4 个 matcher 各自保留本地分词/打分;`skills/registry.py` 新增的 `_CJK_RUN` /
  `_cjk_bigrams` 为本地私有,不导出、不共享。
- 纯 ASCII 技能路由行为与改动前完全一致(`_cjk_bigrams` 对 ASCII 返回空集);中文路由
  新增"经技能描述 bigram 命中"的能力,且 phrase(+3)始终压过泛化 bigram(+1)噪声。
- **重启条件**:若未来确实出现 ≥3 处需要**同一种**带停用词的相似度算法(而非各自不同的
  调校),再重开本 ADR、抽共享模块。在此之前,本地化是 locality 最优解。
