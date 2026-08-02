# Troubleshooting

本指南覆盖本地使用 DataAnalysisAgent 最常见的故障。每条给出**症状 → 原因 → 处理**。

先用一行命令做整体自检：

```bash
data-agent doctor
```

`doctor` 打印一份 pass/warn/fail 只读报告，覆盖：API key、data extras（pandas 可导入性）、
`DAA_HOME` 可写性、`~/.daa` 各子目录磁盘用量、ECharts 模式、权限预设、kernel python、
Web 端口占用。任一检查 **FAIL** 时退出码为 1（可用于脚本）。它只做只读检查 + 创建并删除
一个探针文件测可写性，不改动任何状态。

---

## 1. 缺少 API key

**症状**：启动即报错，或 `doctor` 的 API key 项 FAIL。

**原因**：未设置 `ANTHROPIC_API_KEY`，或 config 文件里 `api_key` 为空。

**处理**：

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

或在 config JSON 里设置 `"api_key": "sk-ant-..."`。设置后重跑 `data-agent doctor` 应转为 pass。

> 安全：永远不要把 key 写进会被提交的文件。用环境变量或本地（不进版本库）的 config。

---

## 2. 缺少 pandas / openpyxl

**症状**：读 CSV/Excel 报 `ImportError`；`doctor` 的 data extras 项 FAIL 或 WARN。

**原因**：未安装 data extra。

**处理**：用带 extras 的方式重装：

```bash
pip install -e ".[data,dev,web]"
```

- CSV/Parquet 需要 **pandas**。
- Excel（.xlsx）还需要 **openpyxl**。

---

## 3. 文件路径被拒绝（denied file path）

**症状**：工具返回权限拒绝，提示路径未授权 / 不在白名单。

**原因**：`read_file` / `data_profile` / `python_analysis` / artifacts / uploads 都是
**路径限定（path-scoped）** 的。在 `local_safe` 预设下，只有显式授权的目录可读。

**处理**：

- 一次性授权：加 `--path`（可重复）——`data-agent --path ./data "..."`。
- 或用 project 持久授权（注意：`init` 的位置参数是 **project_id**，授权路径用
  `--authorize`）：

  ```bash
  data-agent project init sales --path ./data --authorize ./data
  data-agent --project sales "..."     # 在该 project 授权路径内工作
  ```

  不带 `--authorize` 时 `authorized_paths` 为空，可读路径回退到当前工作目录。

- 授权路径会记入 project manifest（`~/.daa/projects/<id>/project.json`），可用
  `data-agent project status <id>` 查看。

> 这是设计使然（fail-closed），不是 bug。agent 默认读不到你未授权的任何路径。

---

## 4. 没有产生 artifact（图表 / 报告）

**症状**：run 结束但 artifact 列表为空，或报告没生成。

**可能原因与处理**：

- 模型没有选择生成图表/报告 —— 在请求里明确要求（"…并生成一份 HTML 报告"）。
- artifact 目录不可写 —— 跑 `data-agent doctor` 查 `DAA_HOME` 可写性。
- Web 端 artifact 预览 404 —— artifact 预览被**路径守卫限定在 workspace 的 artifacts
  子树**；只有本次 run 真实落盘的产物可预览，这是安全设计。

artifact 默认落在 `~/.daa/projects/<id>/artifacts/`（project 模式）或 run 专属目录。

---

## 5. kernel 重启 / 状态丢失

**症状**：提示 kernel 已重启、之前的变量/DataFrame 不见了。

**原因**：持久 kernel 崩溃或超时后会**自动重启**，并**显式告知状态已丢失**；
此时变量不保留。若 kernel 启动失败，会**永久回落到无状态沙箱**（每次调用独立、不共享状态）。

**处理**：这是预期行为。重启后重新载人数据（重新读文件 / 重建 DataFrame）即可。
若频繁重启，跑 `data-agent doctor` 查 kernel python 健康。

---

## 6. ECharts 离线 / 报告图表不显示

**症状**：HTML 报告打开后图表区域空白。

**原因**：报告默认从 **CDN** 加载 ECharts。离线或 CDN 不可达时图表渲染失败。

**处理**：配置本地 `echarts_src` 文件，把 ECharts **内联嵌入**报告，得到完全离线可用的
自包含 HTML。在 config 里指向本地 echarts.min.js 路径即可。`data-agent doctor` 的
ECharts 模式项会显示当前是 CDN 还是内联。

---

## 7. 端口被占用（Web Workbench 起不来）

**症状**：`data-agent-web` 或 `python -m data_analysis_agent.server` 启动报错，
提示地址/端口已被使用；`doctor` 的 Web 端口项 WARN/FAIL。

**原因**：默认 8000 端口被别的进程占用。

**处理**：换端口启动：

```bash
data-agent-web 8001
# 或
python -m data_analysis_agent.server --port 8001
```

> Web Workbench **默认只绑 127.0.0.1（localhost）**。要绑非 loopback 地址（LAN 暴露）
> 必须显式加 `--unsafe`——否则启动即拒绝。这是刻意的 fail-closed 安全设计，见
> [web-workbench.md](web-workbench.md)。

---

## 仍未解决？

- 跑 `data-agent doctor` 拿到完整 pass/warn/fail 报告，逐项对照。
- 质量与架构约束见 `docs/QUALITY_BAR.md`、`docs/ARCHITECTURE.md`。
- 开发/测试环境（venv、extras、跑测试）见 `docs/DEVELOPMENT.md`。
