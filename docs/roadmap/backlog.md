# DataAnalysisAgent 未完成项 Backlog

> 统一索引：把散落在路线图、2026-07 审计报告、memory 里的未完成项归到一处。
> 创建：2026-07-20。状态随每次 PR 更新。
> 权威路线图：`docs/roadmap/2026-07-05-phase1-phase2-execution-plan.md`
> 审计基线：`../../../research/daa-audit-2026-07-14/REPORT.md` + `REPORT-SUPPLEMENT.md`

状态约定：✅ 完成 · ⚠️ 部分 · ❌ 未开始 · ⏸ 主动跳过（附理由）

## 推进主线（顺序执行，依赖链：Wave 1 → 2 → 3）

| Wave | 工作                                         | 状态                                                                                                                             |
| ---- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 0    | 清理陈旧分支 + 本 backlog                    | ✅ 2026-07-20                                                                                                                    |
| 1    | P1-2 工作区 + P1-1 安全基线 + 审计安全延后项 | ✅ PR #8/#9/#10（工作区/安全基线/doctor）                                                                                        |
| 2    | P1-3 Web Workbench                           | ⚠️ Slice 1（live-agent SSE run，PR #11）✅；**前端 UI 全部移交 `frontend-ui-todo.md`（Kimi-K3 做）**；剩余非 UI：unsafe 公开旗标 |
| 3    | G1 自进化真闭环（一次真实晋升）              | ❌ 未开始（下一步）                                                                                                              |

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

## Wave 1 Slice 2a 已知跟进项（独立审查 minor + 诚实范围）

- [ ] ResultStore / artifact 输出含敏感数据：sensitive-mode 只抑制"用户输入形"捕获（轨迹 input、manifest request、session 消息），不净化计算产物（python 输出可能回显输入）。需 output redaction。
- [ ] `local_safe` + Web 调用方必须接 approval handler，否则所有 mutator 被 fail-closed 拒绝（Wave 2 Web 实现时注意，spec 注记）。
- [ ] 主动 PII scrubbing（当前是"不捕获"，非"净化"）。

## 支线（穿插，不阻塞主线）

### P1-4 数据分析工具硬化（缺口）

- [x] P1-4.1 `data_quality` 工具（缺失值/重复/异常/口径）— ✅ PR #14（feat/data-quality）。
      8 flag + 全量读+1M cap+truncation + file-only（与 data_profile 结构发现互补）。三轮独立审查收敛（0/0）。
      跟进（minor，advisory）：① 0/1 编码整数列（CSV 里的语义布尔 is_vip 等）仍触发 `high_outliers`
      ——难与合法 0/1 数值区分，启发式两难；② 单行表退化情形每列标 `constant`（n_rows>1 门可收紧）。
- [x] P1-4.2 `join_planner` — ✅ PR #15（feat/join-planner）。多文件/多 sheet 跨表只读 join 顾问：
      候选键(同名列)/唯一性→关系(1:1/1:N/N:1/N:N)/值覆盖/估算连接行数/行乘积风险(high iff N:N)/
      null-key 风险/推荐顺序(大表为锚，优先入端 unique)。两轮独立审查收敛 0/0。
      跟进（minor）：跨名值重叠(cust_id↔id)与 case-insensitive 匹配（更模糊，留 follow-up）。
- [x] P1-4.3 `metric_contract` 工具 — ✅ PR #16（feat/metric-contract）。只读口径规整：name/
      numerator/denominator/aggregation/filters/exclusions/time_window/grain/timezone/unit →
      MetricSpec（新增 exclusions 字段，additive 向后兼容）+ 完整性校验（复用 QA 判据）+
      memory_definition 交叉核对（confirmed/unconfirmed/absent + 名字一致性）+ signature。
      无状态无路径（镜像 report_contract）。一轮独立审查收敛 0/0（MetricSpec.exclusions 跨
      reporting 链 additive 安全经探针验证）。**P1-4 工具硬化三部曲完成**（data_quality → join_planner → metric_contract）。
      跟进（minor）：live memory store 注入 tool（当前输入传入）；fuzzy 文本冲突判定；exclusions 进 html_report caveat。
- [x] P1-4.6 nl_query schema-aware 升级 — ✅ PR #20（feat/nl-query-schema-aware）。① schema-aware：可选 `schema`（data_profile 列表）→ 生成代码用真实列名（按 query 关键词匹配数值列，categorical deny-list dtype 分类含 double/real/numeric）；无 schema 保持旧行为。② secret 防护：SQL 连接串含 `@` → 生成代码改用 `$DB_URL`、display 脱敏、warning；detection 用原始 `@`（不依赖 urlparse 的脆弱 netloc 解析，免疫密码含 `#`/`?`/`/`/`@`/scheme 含 `_`/畸形 URL），redact fail-closed（urlparse 干净提取才重建 scheme+host:port，否则占位）。**四轮独立审查收敛**（每轮挖出 secret 泄露路径并修：regex→urlparse→raw-`@`+fail-closed；400 fuzz 0 泄露）。20 测试。
- [ ] P1-4.7 Excel 多表工作流（部分）

### 审计小项（触及相关代码时顺手）

- [x] §3.6 完整 evidence artifact 解析（ArtifactStore/ResultStore 接到渲染边界）— ✅ PR #17（feat/evidence-resolution）。QA 加 `evidence_resolver` 注入式三态检查（resolved/fabricated/descriptive）；`ResultStore.contains` 只读存在性；html_report 注入 result_store + 构建 resolver，**限定 artifact_dir 子树**（杀文件存在性 oracle + 杜绝系统文件冒充证据，symlink 安全）。fabricated ref → HIGH（NEEDS_REVIEW 徽章，不拒渲染，与 evidence.empty_ref 一致）。两轮独立审查收敛 0/0（r1 抓 spec↔impl 矛盾 + 路径遍历；r2 实测 symlink fail-closed）。**不做数值校验**（独立 slice）。
- [x] kernel stdout 捕获期上限 — ⏸ 已基本封顶（kernel_main 有 `_MAX_FIELD_CHARS=2M`/`_MAX_RESPONSE_BYTES=8M`/stdout clip 500k；残留仅短命子进程内 StringIO 执行期膨胀，价值低，不做）
- [ ] recovery-policy 扩面（streaming 重试已部分缓解）
- [x] rephrase 启发式升级（CJK/否定变体；现人审门+泄露守卫兜底）— ✅ PR #19（feat/rephrase-upgrade）。CJK 子串 marker 收紧到无歧义纠正词（否定/错误/重做 + 改一/再改/换个），删歧义 opener（等等/应该是/其实是/反过来/再算）；英文改词边界 regex（修裸 no⊂note、again⊂against 误报）。一轮独立审查收敛后修了 `等等` MAJOR（多义假阳）。
- [x] overlay 域化（templates 已接 report_contract；overlays 需 contract 加 domain 字段）— ✅ PR #18（feat/overlay-domain）。ReportContract 加 `domain` 字段（additive）；report_contract 工具接 `domain` 输入（大小写归一）→ `apply_overlay` 把域特化 required_caveats（saas→mrr_churn 等）叠到模板，**apply_overlay 从死代码变活路径**。未知域 no-op；AD_HOC(None 模板)不崩。一轮独立审查收敛 0/0。
- [x] ResultStore TTL 用 monotonic() 非墙上钟 — ⏸ 不做：`created_at` 持久化到 index.jsonl，`time.monotonic()` 契约不保证跨进程重启可比，naive 换会破坏跨重启 TTL；当前 wall-clock 对持久化时间戳正确（时钟跳对本地单用户 CLI 可忽略）

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
