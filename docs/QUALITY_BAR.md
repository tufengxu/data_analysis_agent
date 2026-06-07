# Quality Bar — Definition of Done

每次迭代「完成」的硬性标尺。**全部满足才算 done**;由 `scripts/quality_gate.py` 机器强制,
并由阻断式 Stop hook 在收尾时执行。

## 准出清单

- [ ] `python scripts/quality_gate.py` 全绿(ruff / format / mypy / pytest / drift)。
- [ ] 新增或删除模块时,`docs/ARCHITECTURE.md` 的 manifest 已同步(否则 drift fail)。
- [ ] 改动有明确记录:见下方「大改 vs 小修」。
- [ ] 提交信息符合 Conventional Commits(`feat/fix/docs/refactor/test/chore`)。

## 大改 vs 小修

- **大改(必走 spec)**:新增模块 / 新公共 API / 跨模块改动 / 改依赖规则。
  先在 `docs/superpowers/specs/YYYY-MM-DD-*.md` 写 spec;涉架构决策再加 `docs/adr/NNNN-*.md`;
  commit message 引用 spec 路径。
- **小修(过闸即可)**:单模块内 bugfix、内部重构、文档微调。分支 + 规范化 commit。

## 闸由什么组成

ruff(lint)· ruff format --check(风格)· mypy src(类型,strict)· pytest(全测试)·
drift(模块 manifest 同步、文档死链、依赖规则、600 LOC 体积告警)。
