# Quality Bar — Definition of Done

每次迭代「完成」的硬性标尺。**全部满足才算 done**;由 `scripts/quality_gate.py` 机器强制,
并由阻断式 Stop hook 在收尾时执行。

## 准出清单

- [ ] `python scripts/quality_gate.py` 全绿(ruff / format / mypy / pytest / drift / ts / eval)。
- [ ] 新增或删除模块时,`docs/ARCHITECTURE.md` 的 manifest 已同步(否则 drift fail)。
- [ ] 改动有明确记录:见下方「大改 vs 小修」。
- [ ] 提交信息符合 Conventional Commits(`feat/fix/docs/refactor/test/chore`)。
- [ ] v2 相关:触碰 `capabilities/*` 或 `harnesses/*` 时,依赖方向与「适配层仅胶水」
  规则由 drift/adapter 检查强制;TS 侧改动须在装好 node_modules 后跑
  `bash harnesses/check-ts.sh`(无 Node 环境的门内显式 SKIP,本机验收必须实际执行)。

## 大改 vs 小修

- **大改(必走 spec)**:新增模块 / 新公共 API / 跨模块改动 / 改依赖规则。
  先在 `docs/specs/YYYY-MM-DD-*.md` 写 spec 或落 OpenSpec 变更(`openspec/changes/`);
  涉架构决策再加 `docs/adr/NNNN-*.md`;commit message 引用 spec 路径。
- **小修(过闸即可)**:单模块内 bugfix、内部重构、文档微调。分支 + 规范化 commit。

## 闸由什么组成

- ruff(lint)· ruff format --check(风格)· mypy src(类型,strict)· pytest(全测试)
- drift:模块 manifest 同步、文档死链(README/AGENTS/ARCHITECTURE/V2_RUNBOOK/
  THIRD_HARNESS_GUIDE)、`scripts/drift_rules.py` 依赖规则(含 v2 `capabilities*`
  禁入 harness/装配层与采样域纯度)、600 LOC 体积告警、
  `check_harness_adapters`(适配器 spawn 白名单/规模上限/禁内联能力实现)。
- ts:`harnesses/check-ts.sh` 对每个装好依赖的适配器跑 `tsc --noEmit`。
- eval:examples/eval_tasks 结构化门(schema/≥20 任务/断言词表)。
