# 0003 — CCR-lite:大工具结果可回取 + pressure-adaptive 收益门控

- 状态: Accepted (2026-06-08)

## 背景

sampling 对大结果有损摘要后原文不可回取(采样不可逆风险);compact_result 无收益门控。
调研 headroom(`research/headroom/`)的 CCR(Compress-Cache-Retrieve)与 context-pressure 门控。

## 决策

内化 CCR 思想为本项目自有层:持久化 `sampling/result_store.py`(纯 stdlib 叶子)存原文 +
`tools/retrieve_result.py` 按行分页回取(offset/limit/query,比 headroom 仅 hash+query 多了分页,
防 token 反弹);`compact_result` 增 context_pressure 自适应门控(空闲严 0.65 ↔ 接近满松 0.90)。
**不引入 headroom 本体、proxy、ML 压缩、第三方 sketch 库**(与 ADR 0001 一致)。

## 理由

原文可回取消除采样不可逆风险;持久化跟随 persist_path 跨 resume/fork 可回取;门控避免
"越压越长信息更少"。确定性、纯 stdlib、可测,契合项目精简依赖与防熵规范。

## 影响

新增 `sampling/result_store.py`、`tools/retrieve_result.py`;agent_loop 接线(压缩即存 + 回取 marker);
TTL + 总量 + 单条上限防存储膨胀。详见
`docs/superpowers/specs/2026-06-08-ccr-lite-result-retrieval-design.md`。
