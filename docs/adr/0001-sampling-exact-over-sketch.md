# 0001 — 采样摘要:沙箱精确计算优于流式 sketch 库

- 状态: Accepted (2026-06-06)

## 背景

大结果进上下文此前是盲截断。调研报告主推 t-digest/HLL/CMS 流式 sketch。

## 决策

内存有界场景(子进程把文件读入 DataFrame)用 pandas 精确统计;只在无结构文本兜底用纯 stdlib
近似。**不引入第三方 sketch 库**。

## 理由

内存态 pandas 精确计算又快又准、严格优于近似;零新依赖契合精简依赖与离线沙箱约束。

## 影响

新增 `sampling/` 模块;两个接缝(python_exec 沙箱、agent_loop 兜底)。详见
`docs/superpowers/specs/2026-06-06-data-sampling-compaction-design.md`。
