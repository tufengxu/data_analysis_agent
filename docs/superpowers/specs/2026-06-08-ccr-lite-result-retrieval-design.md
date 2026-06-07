# CCR-lite:大工具结果可回取 + 收益门控 — 方案设计(Phase 1)

- 日期: 2026-06-08
- 状态: 已确认方向,待写实现计划
- 关联调研: `research/headroom/borrowing-judgment-for-dataanalysisagent.md`(借鉴判断)
- 一手源码核对: `research/headroom/headroom/`(headroom 本体,Apache-2.0)

## 1. 背景与主要矛盾

DataAnalysisAgent 的结果级压缩(`sampling/`)对 DataFrame 已是高保真精确摘要,但**有损且
原文不可回取**:一旦 `compact_result` 把大结果摘要进上下文,模型无法用一个句柄取回完整原始结果。
对"先看概览、后续才发现某行/某异常/某字段重要"的长任务,这是采样不可逆的最大风险。

此外当前 `compact_result` 只要超过 `trigger_chars` 就压缩,**没有收益门控**:可能出现原文刚过阈值、
摘要后只略短、信息却变少的副作用。

**主要矛盾 = "压得多" vs "可还原/不丢关键细节"。** headroom 调研给出的最有价值答案是 CCR
(Compress-Cache-Retrieve):把"丢什么"换成"现在要什么,其余推迟到被请求时"。本期把该思想内化为
DataAnalysisAgent 自己的 tool-result 可回取层,并补上收益门控。

## 2. 一手源码核对得到的设计校准

- **收益门控应随上下文压力自适应**(`headroom/transforms/content_router.py:1924-1940`):
  headroom 用 `context_pressure = tokens_before / model_limit` 在两阈值间线性插值。本设计借此**洞见**,
  适配为"事后收益门控"(见 §6),数值方向相应调整。
- **headroom 的 retrieve 真实 schema 仅 `hash` + 可选 `query`,无分页**(`headroom/ccr/tool_injection.py`),
  这正是"token 反弹"风险源。本设计的 **offset/limit + query 分页是有意改进**,保留。
- **marker 自描述风格**借自 headroom(`[N items compressed to M. Retrieve more: hash=...]`)。
- **刻意分歧**:headroom 默认把 Read 排除出压缩(保护代码);DataAnalysisAgent **不排除
  `file_read`** —— 读 CSV 正是要摘要 + 可回取的对象。

## 3. 范围(Phase 1)

做:**持久化 ResultStore + `retrieve_result` 工具 + pressure-adaptive 收益门控 + agent_loop 接线**。

明确不做(YAGNI,留后续):

- 不引入 `CompactResult` 数据类,保持 `compact_result -> (content, was_compacted)`,改动最小。
- 不做 ContentRouter / JSON / 日志专用摘要(P2)。
- 不做本地 metrics 上报(后续)。
- 不做 exclude-tools / 代码保护(P2)。
- 不动 `python_analysis` 沙箱 DataFrame 路径(高保真主干,保留最高优先级)。

## 4. 架构与新增/改动单元(遵守依赖规则 + 同步 manifest)

| 文件                        | 动作 | 职责                                                      | 依赖约束                                                       |
| --------------------------- | ---- | --------------------------------------------------------- | -------------------------------------------------------------- |
| `sampling/result_store.py`  | 新增 | 持久化结果存储:put/get/TTL/容量回收/resume 加载           | **纯 stdlib 叶子**(json/pathlib/hashlib/time/re),零包内 import |
| `tools/retrieve_result.py`  | 新增 | `retrieve_result` 工具,按行分页 + query                   | `tools/*` 可 import `sampling`(取 ResultStore)                 |
| `sampling/config.py`        | 改   | 加 `gate_ratio_low_pressure` / `gate_ratio_high_pressure` | 叶子不变                                                       |
| `sampling/text_summary.py`  | 改   | `compact_result` 加 `context_pressure` 参数 + 门控        | 叶子不变                                                       |
| `agent_loop.py`             | 改   | 持有 store;压缩时存原文 + 尾部 marker;计算并传 pressure   | 持有 store 实例下发                                            |
| `config.py` / `__main__.py` | 改   | 装配 store + 注册 retrieve 工具                           | —                                                              |

依赖规则关键点:`sampling/*` 不得 import `tools/agent_loop`,故 `result_store.py` 必须纯叶子;
retrieve 工具放 `tools/`(允许 import sampling);`agent_loop` 建 store 实例并同时下发给压缩接缝与工具。

## 5. ResultStore(持久化、按行回取)

### 5.1 API

```python
class ResultStore:
    def __init__(self, store_dir: Path, *, ttl_seconds: int = 3600,
                 max_total_bytes: int = 64 * 1024 * 1024,
                 max_entry_bytes: int = 8 * 1024 * 1024,
                 clock: Callable[[], float] = time.time) -> None: ...

    def put(self, result_id: str, content: str, meta: dict[str, Any]) -> bool:
        """存原文。返回是否真的存了(超 max_entry_bytes 则不存,返回 False)。"""

    def get(self, result_id: str, *, offset: int = 0, limit: int = 50,
            query: str | None = None) -> RetrievedPage | None:
        """按行回取一页;找不到/过期返回 None。"""
```

`RetrievedPage`:`dataclass(result_id, total_lines, matched_lines, offset, returned_lines, text, truncated, tool)`。

### 5.2 存储布局与持久化

- `store_dir/index.jsonl`:每次 put 追加一行 `{id, file, sha256, bytes, lines, created_at, tool}`。
- `store_dir/<sha256(result_id)[:32]>.txt`:该结果原文(文件名哈希化,避免 tool_use_id 中的非法字符)。
- **store_dir 推导**:`__main__` 有 `persist_path` 时取其同级 `<persist_dir>/results/`(跨 resume/fork
  可回取);无 persist_path 时退回进程级 tempdir(仅本会话,随退出清理)。
- **resume**:`__init__` 读 `index.jsonl` 重建内存索引,顺带回收过期条目。

### 5.3 回收(对照 headroom 无默认 TTL 导致存储膨胀的反面教材)

- 每次 `put` 与 `__init__`:删除 `created_at` 早于 `now - ttl_seconds` 的条目(文件 + index)。
- 超 `max_total_bytes`:按 `created_at` 由旧到新淘汰直到回到上限内。
- 单条超 `max_entry_bytes`:不存,`put` 返回 False(上层据此不加 marker)。

### 5.4 get 行为

- 读原文 → 按 `\n` 切行。`query` 非空:先用**子串(大小写不敏感)过滤**保留命中行(记 `matched_lines`)。
- 再 `[offset : offset+limit]`,`limit` 由工具侧封顶(见 §7)。
- 页文本再做**字节封顶**:截断到 `< SamplingConfig.trigger_chars`(默认 8000,留余量取 7500 chars)
  并标注 `…[页过大已截断,缩小 limit 或用 query]`。封顶值必须 < trigger_chars,以保证回取页本身
  不会再被 `compact_result` 二次摘要。
- 页首一行元信息:`result_id=… | lines X–Y of N (query=… matched M) | tool=…`。

## 6. 收益门控(pressure-adaptive)

`compact_result` 增加 `context_pressure: float = 0.0`(0=上下文空闲,1=接近预算上限):

```python
def compact_result(content, max_chars, config=None, context_pressure=0.0):
    config = config or SamplingConfig()
    if len(content) <= config.trigger_chars:
        return content, False
    try:
        out = summarize_text(content, config)
    except Exception:
        out = _head_tail_truncate(content, config.trigger_chars)
    # pressure-adaptive 接受阈值:空闲严(省得多才压),接近满松(省一点也压)
    p = min(1.0, max(0.0, context_pressure))
    accept_ratio = config.gate_ratio_low_pressure + (
        config.gate_ratio_high_pressure - config.gate_ratio_low_pressure
    ) * p
    fits = len(content) <= max_chars
    if len(out) > len(content) * accept_ratio and fits:
        return content, False          # 收益不足且原文不超硬上限 → passthrough 原文
    if max_chars and len(out) > max_chars:
        out = _head_tail_truncate(out, max_chars)
    return out, True
```

- 默认 `gate_ratio_low_pressure=0.65`(空闲:摘要须 ≤ 原文 65% 才压),`gate_ratio_high_pressure=0.90`
  (接近满:≤ 90% 即压)。
- `len(content) > max_chars` 时(否则会被截断)**必须压缩**,不受门控影响。
- 语义方向与 headroom 的 `min_ratio` 数值相反(headroom 那是"压缩目标",此处是"事后接受阈值"),
  但"压力越大越激进"的意图一致。

## 7. retrieve_result 工具

- `name = "retrieve_result"`;read-only / concurrency-safe / 非破坏。
- `input_schema`:`result_id`(string,必填)、`offset`(int,默认 0)、`limit`(int,默认 50)、
  `query`(string,可选)。
- `validate_input`:`result_id` 必填;`offset ≥ 0`;`1 ≤ limit ≤ 500`(硬上限,防 token 反弹)。
- `call`:取构造时注入的 `ResultStore`,`get(...)` → 命中返回 `RetrievedPage.text`;
  未命中返回 `ToolResult(is_error=True, "result_id 不存在或已过期(TTL=1h)。可用 python_analysis 重算。")`。
- `description`:说明"用于回取被摘要前的完整工具结果原文;按行分页,可用 query 过滤;
  精确聚合请改用 python_analysis 在 pandas 内计算"。

## 8. agent_loop 接线(`_execute_tools`,当前 line 443)

```python
tool_result = await tool.call(block.input)
pressure = self._context_pressure(state.messages)  # est_tokens / compressor.budget_tokens
content, was = compact_result(
    tool_result.content, tool.max_result_size_chars, self.sampling_config, pressure
)
if was and self.result_store is not None:
    stored = self.result_store.put(block.id, tool_result.content, {"tool": block.name})
    if stored:
        content += (
            f'\n\n[完整结果已缓存。回取: retrieve_result('
            f'result_id="{block.id}", offset=0, limit=50)]'
        )
```

- `_context_pressure(messages)`:`sum(estimate_tokens(message_to_text(m)) for m in messages) /
self.compressor.budget_tokens`,clamp [0,1](复用 `context/compression.py` 已有的 `estimate_tokens`)。
- `block.id`(tool_use_id)作 result_id;只在**有损压缩**(`was=True`)且**成功落存**时加 marker。
- store 为 None 或落存失败 → 不加 marker,行为同现状(降级链不断)。
- retrieve 工具自身的返回也走本接缝;因 limit≤500 + 页字节封顶,通常 < trigger_chars 原样通过。

## 9. 配置

- `SamplingConfig` 增 `gate_ratio_low_pressure=0.65`、`gate_ratio_high_pressure=0.90`。
- `AgentConfig` 增 `result_store_ttl_seconds=3600`、`result_store_max_total_mb=64`、
  `result_store_max_entry_mb=8`,并提供 `result_store(persist_path)` 工厂(无 persist_path → tempdir)。
- `AgentLoop.__init__` 增 `result_store: ResultStore | None = None`。
- `__main__`:建一个 `ResultStore`,传给 `AgentLoop(result_store=…)` 与
  `registry.register(RetrieveResultTool(result_store=…))`。

## 10. 错误与降级链

- 摘要解析失败 → 现有 head+tail 截断(不变)。
- store 不可用/未配置/单条超限 → 无 marker、行为同现状。
- retrieve 失败/过期 → 工具 `is_error`,不影响主流程;不破坏 ledger closure。
- store_dir 不可写(只读盘)→ ResultStore 退化为内存模式或禁用,记一次告警,不崩。

## 11. 测试(TDD,须过质量闸)

`tests/test_result_store.py`:put/get 行往返;offset/limit 分页;query 子串过滤(matched 计数);
TTL 过期回收;总量淘汰(最旧先出);单条超限不存(返回 False);跨实例 resume(新实例读 index 命中);
文件名哈希化(tool_use_id 含特殊字符);页字节封顶。
`tests/test_tools.py`(扩展)或新 `tests/test_retrieve_tool.py`:validate_input(缺 id / limit 越界 /
offset 负);call 命中分页;未命中 error;query 过滤。
`tests/test_sampling.py`(扩展):门控低收益 passthrough(原文 ≤ max_chars);超 max_chars 仍压;
pressure=1 时更易接受(松)、pressure=0 时更易 passthrough(严)。
接线:压缩即存 + marker;小结果不存不加 marker;retrieve 拿回原文片段。
回归:现有 72 测试不变。

## 12. 与防熵规范体系衔接

- 新增 `sampling/result_store.py`、`tools/retrieve_result.py` **必须登记 `docs/ARCHITECTURE.md`
  manifest**,否则 `scripts/quality_gate.py` 的 drift fail。
- 新增 **ADR `docs/adr/0003-ccr-lite-result-retrieval.md`**(决策:内化 CCR 思想、持久化 store、
  pressure-adaptive 门控、不引入 headroom 本体)。
- 属大改(新模块 + 新公共 API + 跨模块)→ 本 spec 即其设计依据;实现走 `feat/*` 分支,过质量闸,
  规范化 commit。

## 13. 验收标准

1. 大 CSV/文本/JSON 结果被摘要,尾部带 `result_id`;`retrieve_result` 能按行分页拿回原文,query 可过滤。
2. 小结果不变;摘要收益不足且原文 ≤ max_chars 时 passthrough。
3. pressure 高时门控更松、低时更严(单测可验证)。
4. TTL 过期条目被回收;总量/单条上限生效;跨 resume 仍可回取(persist_path 在时)。
5. 现有 72 测试不回归;`scripts/quality_gate.py` 五步全绿(含 manifest 同步与依赖规则)。

## 14. 风险与对策

- **token 反弹**:retrieve 强制 limit≤500 + 页字节封顶 + 默认小 limit。
- **压缩即攻击面**(CompressionAttack):错误/异常行保留留待 P2 的 log/json 策略;本期靠"原文可回取 +
  精确聚合回 pandas"的警告兜底,不让摘要承担精确计算。
- **存储膨胀**:TTL + 总量 + 单条三重上限 + 旧条目淘汰。
- **敏感数据落盘**:store_dir 跟随 persist_path(用户已选持久化即知情);默认 TTL 清理;
  ADR 注明落盘位置与清理策略。
- **模型不调用 retrieve**:marker 文案明确、放摘要尾部;后续可加本地 metrics 观测 retrieve 调用率。
