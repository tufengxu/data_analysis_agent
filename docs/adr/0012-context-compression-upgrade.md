# 0012 — 上下文压缩升级:呈现契约、压力自适应与数据态保全

- 状态: Accepted (2026-08-28)
- 规格: openspec/changes/context-compression-upgrade/(决策全文见其 design.md D1-D9)

## 背景

第二轮调研(`docs/data_context_compression_research.md`,三路一手来源)验证了 sampling 模块
L0-L3 分层方向正确,同时暴露:数字呈现未利用千分位的 tokenizer 算术增益;L1 统计缺
cardinality/直方图/粒度;触发不受压力影响;collapse 桩零信号;compaction 后 kernel 变量/
schema/可回取 id 全部丢失;跨结果重复渲染同 schema;回取无谓词;无保真评测闭环。

## 决策

0. **单一事实源与基座可插拔(D0)**:全部压缩/采样核心逻辑仅落能力层;Pi/dsh 适配器删除 TS 侧
   触发镜像,改为下界预筛 + 服从服务端 `was_compacted` 裁决;上下文压力是各基座可插拔输入
   (缺失时退化为静态阈值语义);v1 会话层模块只消费能力层助手(digest 提取、数据态格式化)。
1. **呈现与数据分离(D1)**:千分位/3 位有效/防科学计数法只在 render 层,summary dict 保持原精度。
2. **压力自适应触发 + 回取页豁免(D2)**:有效触发随压力下探(floor 2000);`[result_id=` 开头的
   回取页永不压缩——显式取原文动作不可被再摘要。
3. **桩即指针(D3)**:collapse 桩带一行 digest 与 result_id,失败回退占位文案(fail-closed)。
4. **compaction 保数据态,依赖倒置(D4)**:handoff 模板 + 头尾拼接摘要输入 + data_state_provider
   构造器注入(recovery 零新增 import);压缩后重注入数据态 meta 消息。
5. **变量地图分两步(D5)**:先命名与全帧摘要(kernel 快照去重,每请求至多 3 帧),delta 渲染视效果
   另立 PR。
6. **下取不造 SQL(D6)**:retrieve 只做 mode/投影/单谓词,复杂查询引导回 kernel。
7. **JSON 骨架摘要,降级链不变(D7)**。
8. **自适应降档 + 确定性评测臂(D8/D9)**:压力 ≥0.75 自动 low 档(可关);评测以冻结 fixture +
   numeric_anchor 判分,无 LLM judge;render_format=kv 仅作 A/B 臂,默认 markdown 不变。

## 理由

千分位(R2L 分组)有一手量化证据(GPT-4 算术 84.4%→98.9%);JetBrains 对照证明带指针的
masking 近零损失;Anthropic 官方"可重取即可丢原文"证成桩/回取设计;CARE 反例证明统计与具象行
必须混合,故只增统计不改混合结构;工业界无人做每列完整画像,L1 补强是差异化。暂缓 sketch
(ADR 0001 仍有效)、LLMLingua、embedding 采样。

## 影响

capabilities/sampling(render/sandbox_summary/text_summary/config/compactor/result_store +
新 slicing.py)、context/compression、recovery/runtime、kernel(+manager)、python_exec、
retrieve_result、serving registry、evolution/evaluator、examples/eval_tasks、
harnesses/{pi,deepseek}(触发镜像去除);现锁(recall_hint 字节、v1↔v2 等价、三传输确定性、
_PROD_TOOLS)全部保持。
