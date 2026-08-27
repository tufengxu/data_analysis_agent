# V2 运行手册(双基座数据分析 Agent)

v2 = Harness 无关能力核心层(`src/data_analysis_agent/capabilities/`)+ 统一暴露层(MCP stdio + CLI)+ 两个基座适配器(`harnesses/pi/`、`harnesses/deepseek/`)。本手册命令均在本机(darwin/arm64,Node 24.15,Python 3.13)实际执行过,证据见文末。

## 0. 环境(已验证 2026-08-26)

- Pi Agent Core:npm 包 earendil-works/pi-coding-agent **0.84.3**(engines node>=22.19)
- DeepSeek Harness:npm 包 deepseek-ai/dsh **0.1.1-rc.2**(dev preview,有破坏性变更风险;Cordis 插件运行时)
- 能力层依赖:`mcp` Python SDK 2.1.1(extra `serving`)

## 1. 安装

```bash
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
uv pip install -e ".[data,dev,web,serving]"
export PATH="$PWD/.venv/bin:$PATH"          # 适配器经 PATH 找 data-agent-capabilities
```

TS 侧(每个适配器,shared 已含):

```bash
(cd harnesses/shared && npm install)
(cd harnesses/pi && npm install)
(cd harnesses/deepseek && npm install)
```

## 2. 无 key 冒烟(全部已验证)

```bash
# 能力层:真实 stdio 框架层 + 全链路任务
.venv/bin/python examples/v2/smoke_stdio_mcp.py      # STDIO SMOKE: PASS
.venv/bin/python examples/v2/demo_e2e.py             # V2 E2E DEMO: PASS(11 检查)

# 适配器(不起 LLM)
(cd harnesses/pi && npm run smoke)                   # PI SMOKE: PASS(26 检查)
(cd harnesses/deepseek && npm run smoke)             # DSH SMOKE: PASS(19 检查)

# TS 类型检查(= 质量门 ts 步)
bash harnesses/check-ts.sh                           # checked=3 failed=0
```

## 3. dsh 真实 E2E(已验证 2026-08-26,凭据为本机 dsh profile)

```bash
cd harnesses/deepseek
ln -sfn "$(pwd)" ~/.dsh/profiles/<profile>/daa       # 插件装载(README 有 file:// 备选)
dsh --profile <profile> --patch cordis.example.yml
# 提示词示例:读取 examples/v2/sales.csv,分析区域销量并生成报告,给因果结论
```

已观测行为(2026-08-26 实跑记录):模型调用 `mcp__daa__tabular_read_file`;32KB 表格工具结果被 `tools/post-execute` 接缝压缩为 1.3KB 采样摘要(模型原文复述了「数据采样摘要」与中文回取提示);轨迹事件(turn_start/context_injection/model_input/tool_call/tool_result/turn_end)以 `daa.trajectory.v1` 落 `~/.daa/trajectories/v2/`。

## 4. Pi 真实 E2E(未验证 —— 本机无 ANTHROPIC_API_KEY)

```bash
export ANTHROPIC_API_KEY=sk-...
cd harnesses/pi
pi -e ./src/extension.ts "读取 examples/v2/sales.csv,分析区域销量并生成自包含 HTML 报告,再给因果结论"
```

期望:模型经 `daa_*` 工具(同一 MCP 通道)完成任务;超 8000 字符结果被 `tool_result` 钩子压缩并给回取句柄;轨迹同上落盘。接缝逻辑已由 `npm run smoke` 在能力层验证,仅缺真实模型编排一环。

## 5. CLI 通道(调试/兜底)

```bash
data-agent-capabilities list
data-agent-capabilities call tabular_data_profile --input '{"path": "examples/v2/sales.csv"}'
data-agent-capabilities compact --pressure 0.9 --result-id demo < big.txt
data-agent-capabilities retrieve demo --limit 50
data-agent-capabilities mcp    # stdio server 本体
```

环境变量:`DAA_CAPABILITIES_HOME`(ResultStore)、`DAA_CAPABILITIES_ARTIFACTS`(产物根)、`DAA_CAPABILITIES_ALLOWED_ROOTS`(tabular 路径白名单,冒号分隔)、`DAA_CAPABILITIES_EVOLUTION_ROOT`(轨迹 v2)。

## 6. 质量门

```bash
.venv/bin/python scripts/quality_gate.py   # ruff/format/mypy/pytest/drift/ts/eval
```

## 执行证据(2026-08-26)

| 命令 | 结果 |
|---|---|
| `pytest tests/ -q` | 1278+ passed(v1 全量不回归) |
| `examples/v2/smoke_stdio_mcp.py` | PASS(19 tools) |
| `examples/v2/demo_e2e.py` | PASS(11/11) |
| `harnesses/pi npm run smoke` | PASS(26/26) |
| `harnesses/deepseek npm run smoke` | PASS(19/19) |
| `harnesses/check-ts.sh` | checked=3 failed=0 |
| dsh live run | 已验证(§3) |
| Pi live run | 未验证(§4,缺 key) |
| `harnesses/deepseek/python/agent.py` | 未验证(deepseek-harness-sdk 网络不可达;API 签名经 wheel 源码核实) |
