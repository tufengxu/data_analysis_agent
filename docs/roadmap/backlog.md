# DataAnalysisAgent 未完成项 Backlog

> 统一索引：把散落在路线图、2026-07 审计报告、memory 里的未完成项归到一处。
> 创建：2026-07-20。状态随每次 PR 更新。
> 权威路线图：`docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md`
> 审计基线：`../../../research/daa-audit-2026-07-14/REPORT.md` + `REPORT-SUPPLEMENT.md`

状态约定：✅ 完成 · ⚠️ 部分 · ❌ 未开始 · ⏸ 主动跳过（附理由）

## 推进主线（顺序执行，依赖链：Wave 1 → 2 → 3）

| Wave | 工作                                         | 状态                            |
| ---- | -------------------------------------------- | ------------------------------- |
| 0    | 清理陈旧分支 + 本 backlog                    | ✅ 2026-07-20                   |
| 1    | P1-2 工作区 + P1-1 安全基线 + 审计安全延后项 | ❌ 未开始                       |
| 2    | P1-3 Web Workbench MVP                       | ❌ 未开始（建在 Wave 1 契约上） |
| 3    | G1 自进化真闭环（一次真实晋升）              | ❌ 未开始                       |

## Wave 1 范围明细

### P1-2 本地项目工作区（整体未开始）

- [ ] `~/.daa/projects/<project_id>/` 布局（uploads/artifacts/sessions/results/trajectories/memory/eval_tasks/logs/manifests）
- [ ] `project.json` 项目清单（project_id/authorized_paths/artifact_dir/persist_path/各子目录/配置预设/model/retention）
- [ ] per-run manifest（`runs/<run_id>.json`：请求/授权路径/session/tool calls/artifacts/feedback/memory writes/eval eligibility/终止原因/token/warnings）
- [ ] workspace 路径接入 `AgentRuntime.from_config()`
- [ ] CLI：`data-agent project init/status/list/open/history`（只读除非显式建项目）

### P1-1 安全基线（除 read_file 白名单 + ADR0008 外未做）

- [ ] P1-1.2/1.3 `local_safe` / `local_dev` 权限预设（local_safe 为 Web 默认）
- [ ] P1-1.6 sensitive-mode 开关（禁轨迹/记忆写入）→ **同时闭合审计 PII 脱敏**
- [ ] P1-1.7 `data-agent doctor`（API key/data extras/DAA_HOME 可写/artifact 目录/ECharts 模式/权限预设/授权路径/kernel 健康/本地端口）

### 审计延后项（随 Wave 1 闭合）

- [ ] ~/.daa 全目录磁盘上限 + retention（trajectories 已有 cap `7e12fb5`，扩到 memory/profiles/skills）
- [ ] 轨迹 PII 脱敏（`user_input` 全文 + `final_text_digest` 原样落盘）
- [ ] ⏸ 默认 fail-closed（P3-1）— 不推荐：python_analysis 是主工具，CLI 单用户每次 ASK 不可用

## Wave 1 Slice 1 已知跟进项（独立审查 minor，不阻塞）

- [ ] 崩溃 run 也落 RunManifest（terminal_reason="error"）：当前 run_turn 抛异常则不记录任何清单
- [ ] 时间戳后缀统一（workspace 用 `+00:00`，session 用 `Z`）：纯 cosmetic
- [ ] project add_run 的 runs 索引并发安全（当前 read-modify-write，两并发 CLI 可能丢索引项；原子写防损坏但不防丢失）
- [ ] interactive 模式一次调用共享一个 run_id（一 manifest/调用，非一 manifest/turn）：Slice 1 设计取舍

## 支线（穿插，不阻塞主线）

### P1-4 数据分析工具硬化（缺口）

- [ ] P1-4.1 `data_quality` 工具（缺失值/重复/异常/口径）
- [ ] P1-4.2 `join_planner`
- [ ] P1-4.3 `metric_contract` 工具
- [ ] P1-4.6 nl_query schema-aware 升级（部分）
- [ ] P1-4.7 Excel 多表工作流（部分）

### 审计小项（触及相关代码时顺手）

- [ ] §3.6 完整 evidence artifact 解析（ArtifactStore/ResultStore 接到渲染边界）
- [ ] kernel stdout 捕获期上限（响应已 cap；proper fix 需改自包含 kernel_main）
- [ ] recovery-policy 扩面（streaming 重试已部分缓解）
- [ ] rephrase 启发式升级（CJK/否定变体；现人审门+泄露守卫兜底）
- [ ] overlay 域化（templates 已接 report_contract；overlays 需 contract 加 domain 字段）
- [ ] ResultStore TTL 用 monotonic() 非墙上钟（low）

## CI / 基础设施待办

- [ ] Node.js 20 deprecation：等 checkout@v4 / setup-uv@v6 发 Node-24-native 版本再 bump
- [ ] `enforce_admins` → true（CI 跑稳数周后）

## 收尾

- [ ] P1-9 文档 + Phase 1 Release Candidate（路线图 §8 完成清单逐步勾选）

## Phase 2（later，依赖 Phase 1 完成）

整体未开始。P2-1~P2-12（分布式平台）+ P2-12 因果平台化。见路线图 §9–§14。

---

## 已完成（参考，避免重复立项）

- ✅ PR#1 审计 P0/P1/P0-sec 修复（C3 压缩、ResultStore 原子化、read_file 白名单、v2 ReportDocument 接活交付 + QA 闸、chart_render select_family、causal_report、streaming 重试、echarts_src 安全、prompt-injection 净化）
- ✅ PR#2 eval 数值锚点 + correctness taxonomy
- ✅ PR#3–5,7 CI（quality_gate / step summary / dep-drift / mypy fix）
- ✅ 报告交付 wave1–8 + reporting/causal/chart_render 领域层接活模型
- ✅ 自进化五子系统骨架（telemetry/memory/skills/evolution）+ 人审门接 CI + 轨迹磁盘 cap + Memory/Profile 并发锁
