# Web Workbench

Web Workbench 是 DataAnalysisAgent 的浏览器界面：在浏览器里跑真实分析、看 live 进度、
审批工具调用、预览报告与图表、给反馈。**默认只监听 localhost（127.0.0.1）**，权限预设
默认 `local_safe`。

---

## 启动

统一 workbench（推荐）——live agent run + 报告/QA/artifact/反馈面板一体：

```bash
python -m data_analysis_agent.server
# 浏览器打开 http://127.0.0.1:8000
```

它内部把报告 workbench 挂在 `/workbench` 路径下，所以同一个端口既有 live run 也有报告面板。

仅报告 workbench（不需要 live run 时）：

```bash
data-agent-web            # = python -m data_analysis_agent.web
# 或指定端口
data-agent-web 8001
```

两个入口都支持 `--host` 与 `--unsafe`（见下文「绑定与安全」）。端口方式不同：
`server` 用 `--port` flag（`python -m data_analysis_agent.server --port 8001`）；
`web`（`data-agent-web`）用**位置参数**（`data-agent-web 8001`），无 `--port` flag。

---

## 能做什么

| 能力                | 说明                                                            |
| ------------------- | --------------------------------------------------------------- |
| **上传 / 选择数据** | 上传 CSV、Excel；或选择已授权 project / 路径下的文件            |
| **live 进度**       | SSE 实时流式显示模型输出、工具调用、事件时间线、终止态          |
| **审批**            | ASK 类工具在浏览器弹 allow/deny；**超时 = deny**（fail-closed） |
| **artifact 预览**   | 列出本次 run 产物（HTML 报告 / 图表），点击在浏览器内打开       |
| **报告 QA**         | need → context → contract → QA 的报告生成管线面板               |
| **反馈**            | Good / Bad / Rephrase + 可选短评，写入反馈信号用于自进化        |

每次 run 都会落一份 **run manifest**（见 [workspace.md](workspace.md)），记录请求、授权
路径、工具调用、产物、终止原因、token、警告（反馈单独写 `feedback.jsonl`，不入 manifest）。

---

## 绑定与安全（localhost-only）

Web Workbench **默认只绑 127.0.0.1**。这是刻意的安全设计：它提供 agent 输出和变更性端点
（run / approval / upload / feedback），暴露到网络等于让任何能到达该端口的人驱动你的
agent。

绑**非 loopback** 地址（LAN 暴露）必须显式加 `--unsafe`，否则启动即拒绝（fail-closed）：

```bash
# 拒绝:绑 0.0.0.0 但没加 --unsafe → 打印报错并退出(码 1)
python -m data_analysis_agent.server --host 0.0.0.0

# 允许(危险):显式 --unsafe,启动时打印醒目警告
python -m data_analysis_agent.server --host 0.0.0.0 --unsafe
```

> 仅在明确知道后果时使用 `--unsafe`（例如临时在受信局域网内演示）。公网暴露**不要**用
> `--unsafe`——Phase 1 没有认证层，任何能到达端口的人都能驱动它。多用户/认证是 Phase 2
> 的范围。

其它安全细节：

- **权限预设默认 `local_safe`**：只读放行、mutator 走 ASK、未知拒绝（见
  [local-safe.md](local-safe.md)）。
- **artifact 预览路径守卫**：预览被限定在 workspace 的 artifacts 子树，防止路径穿越
  读到任意文件。
- **CSP sandbox**：artifact 页在独立 opaque origin 渲染，无法反向驱动 agent/审批端点。
- **CSRF token**：所有变更性端点 `/api/run/stream`、`/api/approval`、`/api/upload`、
  `/api/feedback` 均校验 per-session `X-DAA-Token`（served UI 内嵌 token，请求须回显；
  自定义头让跨源 form-POST 无法伪造），防止同源 artifact 页或跨站表单静默驱动 agent/
  审批、种植数据或假反馈。

---

## 相关

[local-safe.md](local-safe.md) · [workspace.md](workspace.md) ·
[evolution.md](evolution.md) · [troubleshooting.md](troubleshooting.md)
