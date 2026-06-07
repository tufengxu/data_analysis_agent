# 0002 — 确定性防熵规范(质量闸 + 漂移检测 + 阻断式 Stop hook)

- 状态: Accepted (2026-06-07)

## 背景

质量与架构 enforcement 全靠人自觉,已出现文档漂移。AGENTS.md 曾被 LLM cron 写坏。

## 决策

建立单一 `scripts/quality_gate.py` 准出源 + 确定性漂移检测;git 化 + Conventional Commits;
项目级阻断式 Stop hook。**自动维护用确定性漂移检测(发散即 fail),不用 LLM 再生成文档。**

## 理由

把 enforcement 从人转移到机器;确定性检测规避 LLM 重写文档的损坏风险。

## 影响

新增 `scripts/`、`docs/ARCHITECTURE.md`、`docs/QUALITY_BAR.md`、`docs/DEVELOPMENT.md`、
`.claude/settings.json` Stop hook。详见
`docs/superpowers/specs/2026-06-07-project-standards-anti-entropy-design.md`。
