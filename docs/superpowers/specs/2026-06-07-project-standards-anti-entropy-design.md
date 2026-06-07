# 项目防熵规范体系 v1(方案 B)— 方案设计

- 日期: 2026-06-07
- 状态: 已确认方向,待写实现计划
- 目标: 让后续每次迭代在规范下持续正确、稳定地推进,**不会越迭代越乱**。

## 1. 目标与主要矛盾

**主要矛盾**:质量与架构的 enforcement 当前**全靠人记得** → 必然漂移(已实测:README 引用的
`OKComputer_AgentDesign_Chat/` 已失效;`ruff/mypy/pytest` 无强制;openspec 空置)。

**解法核心**:把"靠自觉"换成**确定性机器闸**。关键教训(实事求是):本项目 AGENTS.md 曾被
LLM cron 写坏 → "自动维护"必须是**确定性漂移检测(发散即 fail)**,而非 LLM 再生成叙述。

## 2. 已确认决策(本设计的硬约束)

1. 阶段一建**方案 B**;将来多人协作或 B 不够用时再迁移到 C(OpenSpec 全流程)。
2. **阻断式 Stop hook**:质量闸不可遗漏,不过则不让收尾。
3. **git 化 + 从迭代流程标准化**(分支模型 + Conventional Commits)。
4. **所有迭代都要有明确记录**:大改走 spec,小修也必须过质量闸 + 规范化 commit。
5. **CI 留到阶段二**,阶段一先把本地闸打磨稳。
6. 质量闸**全跑**,并**记录每次结果 + 耗时**,供后续耗时分析;影响显著再换更细的分档策略。
7. 文件体积红线 **600 LOC**(阶段一仅告警)。

## 3. 组件设计

### 组件 1 · Git 化 + 标准化迭代流程

- `git init`;先写好 `.gitignore`(`.venv/`、`.*cache/`、`__pycache__/`、`.DS_Store`、
  `.quality/`),**再** `git add`,避免把 venv/缓存提交进去。保留 `uv.lock`。初始提交落在 `main`。
- **分支模型(trunk-based)**:`main` 永远绿(过闸);改动走 `feat/* | fix/* | docs/* |
refactor/* | chore/*` 短分支,过闸才并回 `main`。
- **提交规范 = Conventional Commits**(`feat: / fix: / docs: / refactor: / test: / chore:`),
  机器可解析,为阶段二自动 CHANGELOG 铺路。
- **记录纪律**:小修 = 分支 + 规范化 commit(commit 即记录);大改 = `docs/superpowers/specs/`
  下 spec +(涉架构则)一条 ADR,commit message 引用 spec 路径。

### 组件 2 · 质量闸 runner —— DoD 的唯一事实源

`scripts/quality_gate.py`(纯 stdlib,跨平台、便于扩展),顺序执行、任一失败即非零退出:

1. `ruff check src tests`
2. `ruff format --check src tests`
3. `mypy src`
4. `pytest tests/ -q`
5. **漂移检测**(组件 3)

- 自动选用 `.venv/bin/python`(存在时)否则 `python3`;路径相对脚本自身定位,任意 cwd 可跑。
- 终端打印每步 PASS/FAIL + 耗时 + 总结。
- **唯一事实源**:Stop hook、(阶段二)pre-commit、CI 全部调用同一脚本,准出标准只定义一处。
- **运行日志(决策 6)**:每次运行向 `.quality/gate-runs.jsonl`(gitignored)追加一条 JSON:
  `{"ts","git_head","passed",​"total_sec","steps":[{"name","ok","sec"}]}`。供后续耗时分析。

### 组件 3 · 架构/文档漂移检测(防熵内核,确定性、不靠 LLM)

实现为 `scripts/checks/`(被 runner 调用,也可单独跑),规则数据化于 `scripts/drift_rules.toml`:

- **模块清单同步**:`docs/ARCHITECTURE.md` 用 `<!-- manifest:start -->`/`<!-- manifest:end -->`
  标记包裹一段 `path = "一行职责"` 清单(每个 `src/data_analysis_agent/**/*.py` 一条;`__init__.py`
  统一**豁免登记**,因其为再导出 shim)。脚本双向比对:**有非 `__init__` 模块未登记 / 登记项指向
  不存在文件 → fail**。文档与清单同处一文件,无法互相漂移。
- **死链检测**:扫 `README.md`/`AGENTS.md`/`docs/ARCHITECTURE.md` 中的反引号仓库路径与
  相对链接,断言存在(堵住 OKComputer 那类失效)。
- **依赖规则(ast 解析 import)**,初始集合(须对当前代码验证通过,违例则修代码或调规则):
  - `sampling/*` 不得 import `tools` / `agent_loop` / `protocol` / `skills` / `security` / `context`
    (sampling 是叶子工具)。
  - `sampling/sandbox_summary.py` 不得 import 任何 `data_analysis_agent.*`(PYTHONPATH="" 硬约束)。
  - `tools/*` 不得 import `agent_loop`;`protocol/*` 不得 import `agent_loop` / `tools` / `skills`。
- **文件体积红线**:任一模块 > 600 LOC → **告警(阶段一不阻断)**,提示 god-file。

### 组件 4 · 阻断式 Stop hook(硬标尺)

- **项目级** `.claude/settings.json` 注册 `Stop` hook,命令调用 `quality_gate.py --hook`。
- `--hook` 模式逻辑:
  1. 先 `git diff` 检查 `src/`、`tests/`、`docs/`(含未跟踪)是否有改动;**无改动 → 退出 0 放行**
     (纯问答/无码改动收尾不挡路)。
  2. 有改动 → 跑全闸;失败 → 按 Claude Code Stop hook 契约输出
     `{"decision":"block","reason": <失败摘要>}`,agent 必须修复才能收尾;成功 → 放行。
- 作用域锁定本项目目录,不污染工作区其它项目。
- 紧急情况可临时在 settings 关闭(文档注明),但默认常开。

### 组件 5 · 文档体系(承载架构 + 标准)

- `docs/ARCHITECTURE.md` — 模块职责(manifest 段)+ 各子系统关键不变量 + 依赖规则(与组件 3
  强制项同源描述)。
- `docs/QUALITY_BAR.md` — **写下来的 DoD 硬标尺**:闸全绿 / manifest 已更新 / 大改有 spec /
  规范化 commit;并定义大改 vs 小修边界(见组件 6)。
- `docs/DEVELOPMENT.md` — 标准化迭代流程(分支 → 改 → 过闸 → commit →(大改先 spec))。
- `docs/adr/NNNN-*.md`(MADR-lite)— 架构决策记录;**首条 ADR 追记**"采样:沙箱精确 > sketch 库"
  这次既成决策。
- 修掉 README 失效的 `OKComputer_AgentDesign_Chat/` 引用。

### 组件 6 · 大改 / 小修边界(写进 QUALITY_BAR)

- **大改(必走 spec)**:新增模块 / 新公共 API / 跨模块改动 / 改依赖规则。
- **小修(只需过闸 + 规范 commit)**:单模块内 bugfix、内部重构、文档微调。

## 4. 分期(持久战)

- **阶段一(本次)**:组件 1–6 全建;覆盖率仅**测量+报告**不设硬地板;体积红线仅告警。
- **阶段二**:覆盖率地板(按阶段一基线设)、体积红线转阻断、自动 CHANGELOG、`.quality` 耗时
  分析小工具、(可选)推 GitHub + CI 镜像同一质量闸。
- **阶段三**:多人时迁移 OpenSpec 全流程治理(方案 C)。

## 5. 验收标准(阶段一"完成"的定义)

1. `git init` 完成,`.gitignore` 生效(未提交 `.venv`/缓存),`main` 上有规范化初始提交。
2. `scripts/quality_gate.py` 跑通 5 步,**在当前仓库整体 PASS**(为此须:修 README 死链、补全
   manifest、验证依赖规则成立)。
3. 漂移检测对当前树通过;`.quality/gate-runs.jsonl` 有记录写入。
4. 文档齐备:`ARCHITECTURE.md`(+ manifest)、`QUALITY_BAR.md`、`DEVELOPMENT.md`、
   `docs/adr/0001-*.md`。
5. Stop hook 已配置并**双向验证**:制造一处失败 → 被 block;清理后 → 放行;docs-only/无改动 → 放行。
6. 现有 58 测试仍全绿;`ruff`/`mypy` 仍 PASS。

## 6. 非目标(YAGNI,留后续)

- CI / GitHub remote、覆盖率硬地板、体积红线阻断、自动 CHANGELOG、OpenSpec 激活——均阶段二/三。
- 不做 LLM 自动重写叙述文档(确定性漂移检测替代之)。

## 7. 风险与对策

- **阻断式 hook 误挡**:闸为确定性 + git-diff 守卫 + docs-only 放行;紧急可临时关。
- **pytest 变慢拖累收尾**:决策 6 的耗时日志即为此预埋数据,影响显著时按数据改分档。
- **git init 误提交 venv**:先写 `.gitignore` 再 `add`;首提交后核对 `git ls-files` 不含 `.venv`。
- **依赖规则与现状冲突**:实现时先用脚本扫一遍当前 import,违例则修代码或回调规则,确保落地即绿。
