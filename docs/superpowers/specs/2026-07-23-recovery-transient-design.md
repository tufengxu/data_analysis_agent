# 2026-07-23 recovery-policy 扩面：429/超时 transient 重试（审计小项）

> 审计：`recovery` 只处理 prompt-too-long，其他可恢复错误（429/超时）不处理。现状：streaming 层
> （PR#1）已对 RateLimitError/APITimeoutError/连接错误在「未 yield 前」重试 max_attempts 次（带
> `min(2**attempt,10)` 退避）；耗尽后以 `AnthropicClientError("API error: ...", is_recoverable=True)`
> 抛给 agent_loop。loop 调 `attempt_recovery`，但它只认 "too long" → 对 transient 返回 None →
> 会话以 MODEL_ERROR 终结。本 slice 让 recovery 对 transient 错误做**有界 loop 层退避重试**。

## Intent

transient API 错误（429/超时/overloaded/连接）耗尽 streaming 重试后，不再直接放弃，而是在 loop 层
做有限次指数退避重试（与 prompt-too-long 的 compact 路径并列、互斥）。

## Ground truth（已核实）

- **`recovery.py:attempt_recovery`**：`msg=str(error).lower(); if "prompt is too long" in msg or
"too long" in msg: <collapse-drain / reactive-compact ladder>; return None`。transient（msg 含
  "API error"）落到末尾 `return None`。
- **`protocol/client.py:stream_model`**（261-291）：prompt-too-long → `AnthropicClientError("Prompt
too long", is_recoverable=True)`；transient（RateLimitError/APITimeoutError/APIConnectionError/
  APIError retryable）在未 yield 前重试（`asyncio.sleep(min(2**attempt,10))`），耗尽 →
  `AnthropicClientError("API error: {e}", is_recoverable=True)`。AuthenticationError/BadRequest(非
  长度) 不设 is_recoverable（False）。
- **agent_loop.py:326-339**：`except AnthropicClientError as e: if e.is_recoverable:
recovery = attempt_recovery(state, e); if recovery: state=recovery; continue`。recovery 为 None →
  ErrorEvent + MODEL_ERROR 终结。
- **state_machine.py**：`ContinueReason` enum（无 match/case 穷尽匹配，加成员安全）；`AgentState` 是
  frozen dataclass，已有 `max_output_tokens_recovery_count`/`has_attempted_reactive_compact` +
  `with_*`。加带默认值的 `transient_recovery_count` 向后兼容。
- **`tests/test_recovery.py:117 test_attempt_recovery_ignores_non_length_errors`**：当前断言 transient
  ("rate limit exceeded") → None（忽略）。本 slice 改为重试 → 该测试期望需更新。

## 设计决策

1. **transient 与 prompt-too-long 互斥分流**：`attempt_recovery` 先判 "too long"（现有 compact ladder，
   行为不变）；否则（is_recoverable 的 transient API 错误）走新 `_recover_transient`。client 只对
   prompt-too-long + transient 设 is_recoverable，故「is_recoverable 且非 prompt-too-long」即 transient。
2. **有界 loop 层退避重试**：`_recover_transient` —— `transient_recovery_count < TRANSIENT_RECOVERY_LIMIT(3)`
   时 `await sleep(min(2**count, TRANSIENT_BACKOFF_CAP=10))` → 返回
   `state.with_transient_recovery_count(count+1).with_transition(TRANSIENT_RETRY)`（同 messages，重试调用）；
   耗尽 → None（→ MODEL_ERROR，现有终结路径）。
3. **`sleep` 可注入**（默认 `asyncio.sleep`）：RecoveryPolicy.**init** 加 `sleep=asyncio.sleep`，
   便于测试用 no-op。agent_loop 构造不变（用默认）。
4. **新 `ContinueReason.TRANSIENT_RETRY`** + 新 `AgentState.transient_recovery_count: int = 0` +
   `with_transient_recovery_count`（镜像 max_output_tokens_recovery_count）。
5. **不动 streaming 层重试**（已就绪）；只在 loop 层补「耗尽后再退避重试」这一级。Authentication/
   BadRequest(非长度) 仍 is_recoverable=False → 立即终结（退避修不了）。
6. **总退避上界**：streaming 层 max_attempts + loop 层 3 次，最坏额外 ~7s（1+2+4），对 transient 合理。
7. **count 复位**（审查后补）：每次成功的模型调用（loop 追加 assistant 消息时）将 `transient_recovery_count` 复位为 0——一次成功的调用打断 transient 连击，给后续不相关 transient 错误全新配额（避免单次长 run 内早期 429 风暴吃掉后面配额）。`has_attempted_reactive_compact` 不复位（一次性杠杆），transient 是 per-streak 计数器，语义不同。
8. **已知限制**：① 分流用 `"too long"` 子串——理论上一条 transient 错误消息含 "too long"（如 "request queue too long"）会误走 compact ladder；实际风险 ~0（client 只对 413 抛 "Prompt too long"、对 transient 抛 "API error: ..."，真实 SDK 错误消息不含 "too long"）；误路由后果也不致命（compact 后仍重试）。② reset-on-success 是 agent_loop 胶水（1 行，只写新计数器），未单独 e2e 测试（loop 内部构造 RecoveryPolicy 无法注入 no-op sleep，e2e 会真睡；policy 层 12 单测覆盖，reset trivially correct）。

## 文件范围

- `src/data_analysis_agent/state_machine.py`：`ContinueReason.TRANSIENT_RETRY`；`AgentState` 加
  `transient_recovery_count` + `with_transient_recovery_count`。
- `src/data_analysis_agent/recovery.py`：`__init__` 加 `sleep`；`TRANSIENT_RECOVERY_LIMIT`/
  `TRANSIENT_BACKOFF_CAP`；`attempt_recovery` 分流到 `_recover_prompt_too_long`（现有逻辑）+
  `_recover_transient`（新）。
- `tests/test_recovery.py`：`_policy` 加可选 `sleep`；把 `ignores_non_length_errors` 改为
  `retries_transient_errors`（断言 TRANSIENT_RETRY + 计数+1 + 不 compact）；加 transient 耗尽→None 测试。
- **不改** agent_loop（构造用默认 sleep，continue 逻辑不变）/ drift_rules / AGENTS.md/CLAUDE.md。

## 验收

- transient 错误（"API error: rate limit exceeded"，is_recoverable=True）首次 → 返回 TRANSIENT_RETRY +
  count=1（不 drain/force compact）；注入 no-op sleep，测试不真睡。
- count 达 TRANSIENT_RECOVERY_LIMIT → 返回 None（放弃）。
- prompt-too-long 路径行为不变（drain/reactive-compact ladder，既有 3 测试仍绿）。
- 非 is_recoverable 错误（auth/bad-request）—— 不到 attempt_recovery（loop 先 `if e.is_recoverable`
  守卫）；若被直接调，transient 分支对它们也仅退避重试（安全：失败会快速耗尽→None）。
- 质量门全绿；独立审查 blocking/major 清零。

## 验证命令

```
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
.venv/bin/python scripts/quality_gate.py
.venv/bin/python -m pytest tests/test_recovery.py -q
```

## 显式不在本 slice

- 审计 P0-3 数值校验（下一 slice）。
- streaming 层重试参数调优（max_attempts/退避基保持）。
- jitter（当前退避无随机抖动，简单指数；jitter 留 follow-up）。
