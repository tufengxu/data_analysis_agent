# 2026-07-20 Project Workspace Foundation — Design (Wave 1, Slice 1)

> 路线图 P1-2 的第一刀。引入 Project 概念，把一次 run 的会话态产物统一到
> 一个可检查的根目录下，并落 project / run 清单。安全预设 / 敏感模式 / 磁盘上限 /
> PII 脱敏 / doctor 推到 Slice 2。

## Intent

让"一次分析"成为可复现、可检查的单元：所有 session 态产物（artifact / kernel
workspace / result / message store）落在同一个 project 根下，并产生 per-run 清单。

## Ground truth（当前路径模型，已核实）

- **per-session**（跟 `persist_path`）：`artifacts_dir` / `kernel_work_dir` /
  `result_store` 都从 `Path(persist_path).resolve().parent` 派生兄弟目录；
  `persist_path=None` 时落 tempdir。
- **cross-session**（跟 `DAA_HOME`/`~/.daa`）：`trajectories_dir` / `memory_dir` /
  `skills_dir` / `eval_tasks_dir` 全局共享。
- `AgentRuntime.from_config(config, persist_path=..., analysis_paths=...)` 是唯一组合根，
  CLI 与 eval 都走它。
- **缺口**：无 project 单元；run 的 session 态与轨迹/记忆在不同根；没有任何地方记录
  一次 run 授权了什么、产出了什么。

## 设计决策

1. **Opt-in，不改默认行为**。project 通过 `data-agent project init` / `--project <id>`
   / `--project-path <dir>` 激活；不激活时行为与今天逐字节一致（现有测试不动，eval 不受影响）。
2. **Project 根**：默认 `~/.daa/projects/<project_id>/`；`--project-path` 用外部目录。
   子目录：`sessions/ artifacts/ results/ workspace/ runs/ uploads/ logs/`。
3. **Slice 1 只接管 session 态**：artifact / kernel workspace / result / message store
   路由到 project 根。**trajectories / memory / skills 暂留全局根**（project.json 记录该
   决策；按 project 划分它们是 P1-5 范围，后做）——避免破坏现有 evolution 管线。
4. **Per-run session**：每次 run 一个新 `run_id`（uuid4），session jsonl 落
   `sessions/<run_id>.jsonl`；project 内 resume 改为显式 open 历史 run（Slice 1 不做自动 resume）。
5. **Run 清单在 CLI 侧写**：CLI 已累积 artifact、逐事件可见；在 turn 结束后用
   `project.add_run(RunManifest(...))` 落 `runs/<run_id>.json` 并更新 project 清单索引。
   runtime 只负责暴露 `project` / `run_id` 和按 project 派生目录。
6. **原子写**：project.json 与 run 清单复用 `tmp + os.replace` 模式（与 JsonlStore 一致）。

## 文件范围

- 新 `src/data_analysis_agent/workspace.py`：`Project` / `ProjectManifest` / `RunManifest`
  - `init/open/list/history/add_run`，原子写。
- `runtime.py`：`from_config` 增 `project: Project | None`；project 激活时从 project 派生
  persist_path / artifacts / kernel workspace / results 目录；`AgentRuntime` 暴露
  `project` / `run_id`。
- `__main__.py`：首参 `project` 分流到 `project init|status|list|open|history` 子命令；
  顶层加 `--project` / `--project-path`；`run_turn` 累积 tool/event 统计，turn 后写 RunManifest。
- `docs/ARCHITECTURE.md` + `scripts/drift_rules.py`：登记新模块（若 drift 规则要求）。
- 新 `tests/test_workspace.py`。

## RunManifest 字段

run_id, project_id, started_at, finished_at, request, authorized_paths, session_id,
event_counts（按事件类型计）, tool_calls（name→count）, artifacts（路径列表）,
terminal_reason, token_usage（best-effort，可能 None）, warnings。

## 验收

- `project init <id>` 建 project.json + 全子目录；幂等。
- project 下的一次 run：artifact + session jsonl + result + run 清单同根。
- RunManifest 含上述全部字段。
- **不激活 project 时，现有行为逐字节不变**（现有测试全绿）。
- `project list/status/open/history` 只读（init 除外）。
- `scripts/quality_gate.py` 绿；独立审查零 must-fix。

## 验证命令

```
.venv/bin/python scripts/quality_gate.py
.venv/bin/pytest tests/test_workspace.py -v
.venv/bin/pytest tests/ -q   # 全量回归确认无破坏
```

## 显式不在本 slice 内（Slice 2）

local_safe/local_dev 权限预设、sensitive-mode + PII 脱敏、~/.daa 全目录磁盘上限 +
retention、doctor 命令、project-scoped trajectories/memory。
