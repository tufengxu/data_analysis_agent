# Local-Safe 模式（权限与隐私）

DataAnalysisAgent 在本机运行，但默认按**最小权限**工作：agent 只能读你显式授权的路径，
破坏性操作默认被拒绝，敏感数据可选择不被记录。本页解释三层控制：权限预设、路径授权、
sensitive 模式。

---

## 1. 权限预设（permission preset）

两个命名预设，用 `--preset` 或 config 的 `permission_preset` 选择：

| 预设         | 行为                                                                                                        | 适用                                           |
| ------------ | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `local_safe` | **deny-by-default**：只读操作放行；已知 mutator（写/删/执行类）降为 **ASK**（弹确认）；未知操作**直接拒绝** | **Web Workbench 默认**；不熟悉的数据、共享机器 |
| `local_dev`  | CLI 友好、不启用权限引擎，全部放行                                                                          | 本机单人 CLI、自己信任的环境                   |

> CLI 默认其实是 `permission_preset=""`（空），与 `local_dev` 效果相同（都不建引擎、全放行）。
> 想在本机 CLI 启用防护，显式 `--preset local_safe`。

```bash
data-agent --preset local_safe "分析 sales.csv"
```

`local_safe` 下任何 ASK 操作都会触发**交互式审批**；若没有接审批 handler（例如脚本环境），
则 **fail-closed 直接拒绝**——不会静默放行。

> 工具本身也是 fail-closed 设计：`is_destructive` 默认 True，`python_analysis` 走受限
> 子进程（`PYTHONPATH=""`）。权限引擎按 deny > ask > allow 优先级裁决。

---

## 2. 路径授权（path scoping）

`read_file` / `data_profile` / `python_analysis` / artifacts / uploads 全部**路径限定**：
agent 读不到你未授权的任何路径。授权方式：

**（a）一次性授权（CLI）** — `--path` 可重复：

```bash
data-agent --path ./data --path ./reports/sales.csv "对比两个目录的销售"
```

未加 `--path` 时默认当前工作目录。

**（b）project 授权（推荐，跨会话持久）** — 见 [workspace.md](workspace.md)：

```bash
# init 的位置参数是 project_id;授权路径用 --authorize(可重复),project 根用 --path
data-agent project init sales --path ./data --authorize ./data
data-agent --project sales "..."     # 之后运行即在该 project 授权路径内
```

> 注意：不带 `--authorize` 时 `authorized_paths` 为空，`--project` 运行的可读路径回退到
> 当前工作目录——必须显式 `--authorize` 才能锁定 project 授权路径。

授权路径记入 project manifest，可用 `data-agent project status <id>` 查看。

---

## 3. Sensitive 模式（隐私）

```bash
data-agent --sensitive "分析含身份证号的用户表"
```

本次 run **抑制**隐私相关的落盘：

- **不写记忆**（`enable_memory=False`）——本次内容不进入跨会话 L1 记忆；
- **不捕获轨迹输入**（`enable_trajectory_inputs=False`）——trajectory 仍记录工具名/时长
  （用于自进化统计），但**不记录用户输入原文**。

> 注意（诚实边界）：sensitive 模式是「**不捕获**」而非「**净化**」。它阻止输入形落盘，
> 但**不**对 `python_analysis` 计算输出做主动 PII 擦除（输出可能回显输入）。
> 主动 output redaction 是后续项（见 `docs/roadmap/backlog.md`）。处理高度敏感数据时，
> 请把这一点纳入你的威胁模型。

---

## 4. 三者如何组合

- Web Workbench：`local_safe`（默认）+ project 授权 + 可选 sensitive。审批在浏览器里点
  allow/deny，超时 = deny。
- 本机 CLI 自用：`local_dev`（默认）+ `--path` 授权。
- 处理敏感数据：任意预设 + `--sensitive`。

相关：[web-workbench.md](web-workbench.md) · [workspace.md](workspace.md) ·
[troubleshooting.md](troubleshooting.md)
