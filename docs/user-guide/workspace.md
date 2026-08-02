# Project 工作区（Workspace）

Project 把一次真实分析项目的所有状态**收敛到同一根目录**：授权路径、会话、产物、结果、
run 清单、上传、日志。它让 agent 成为一个**可持久的本地工作台**——你能回看每次 run、
审查产物、跨会话续接。

---

## 为什么用 project

不用 project 时，每次 run 的产物散在临时目录，会话互不关联。用 project 后：

- 每次 run 落一份 **run manifest**（可审计、可回放）；
- 授权路径**跨会话持久**（不用每次 `--path`）；
- 产物 / 结果 / 会话归档在同一 project 根下，便于检视与清理；
- agent 只能读 project 授权的路径（fail-closed）。

---

## 快速上手

```bash
# 1. 初始化(指定数据目录为授权路径)
data-agent project init sales --path ./data --authorize ./data

# 2. 在 project 内跑分析
data-agent --project sales "分析 sales.csv 的月度趋势并出一份报告"

# 3. 查看
data-agent project status sales     # project 清单(授权路径/模型/预设/run 索引)
data-agent project history sales    # 历次 run(run_id/时间/终止原因/工具数/产物数)
data-agent project list             # 所有 project
data-agent project open sales       # 显示"如何在 project 内运行"的提示
```

`init` 参数：`--path`（project 根，默认 `~/.daa/projects/<id>`）、`--authorize`（授权
数据路径，**可重复**）、`--model`、`--preset`。

除 `init` 外，project 子命令全部**只读**。

---

## 目录布局

```
~/.daa/projects/<id>/
├── project.json      # project 清单:project_id/created_at/root/authorized_paths/model/preset/runs 索引
├── sessions/         # 会话态(消息历史)
├── artifacts/        # 产物:HTML 报告、图表
├── results/          # 结果存储(CCR-lite:大工具结果原文落盘 + 按行回取)
├── workspace/        # 工作文件
├── runs/             # 每次 run 的 manifest:runs/<run_id>.json
├── uploads/          # Web 端上传的数据文件
└── logs/             # 日志
```

每份 `runs/<run_id>.json` 记录：run_id、project_id、起止时间、请求、授权路径、session_id、
事件计数、工具调用计数、产物列表、终止原因、token 用量、警告。（反馈单独写
`feedback.jsonl` / session，不入 manifest。）

> **设计说明**：`trajectories/`、`memory/`、`skills/`、`eval_tasks/` 仍留在**全局**
> `~/.daa/` 根（跨 project 共享的自进化语料与记忆），不随单个 project 走。这是有意为之
> —— 领域记忆和技能是跨项目复用的。见 `workspace.py` docstring。

---

## 数据存哪（一览）

| 内容                         | 位置                           |
| ---------------------------- | ------------------------------ |
| project 清单 + run manifests | `~/.daa/projects/<id>/`        |
| 轨迹（自进化原料）           | `~/.daa/trajectories/`（全局） |
| L1 领域记忆                  | `~/.daa/memory/`（全局）       |
| 技能（candidate/active）     | `~/.daa/skills/`（全局）       |
| eval 任务 / fixture          | `~/.daa/eval_tasks/`（全局）   |

根目录可用环境变量 `DAA_HOME` 改（默认 `~/.daa`）。

---

## 相关

[local-safe.md](local-safe.md) · [web-workbench.md](web-workbench.md) ·
[evolution.md](evolution.md) · [troubleshooting.md](troubleshooting.md)
