# Development Workflow

本目录是 git 仓库,采用 trunk-based 流程。`main` 永远绿(过质量闸)。

## 环境

```bash
uv pip install -e ".[data,dev,web]"   # 沙箱会拦 uv 缓存,需放行;装上 pandas 等;web 供 mypy 检查 web/
uv pip install -e ".[data,dev,web,serving]"  # v2:再加 mcp SDK(MCP stdio server 需要)
```

TS 侧(双基座适配器;Node ≥22.19,本机 24.x 已验证):

```bash
(cd harnesses/shared && npm install)     # 共享 MCP 客户端(官方 @modelcontextprotocol/sdk)
(cd harnesses/pi && npm install)         # Pi 适配器(@earendil-works/pi-coding-agent 0.84.3,devDep 仅类型)
(cd harnesses/deepseek && npm install)   # dsh 适配器(@deepseek-ai/dsh 0.1.1-rc.2)
```

## 一次迭代

1. 开短分支:`git switch -c feat/<topic>`(或 `fix/ docs/ refactor/ chore/`)。
2. 大改先写 spec(见 `docs/QUALITY_BAR.md`)或落 OpenSpec 变更;小修直接改。
3. 本地过闸:`.venv/bin/python scripts/quality_gate.py`(收尾时 Stop hook 也会强制跑;
   含 ts 步——适配器装了 node_modules 就会跑 `tsc --noEmit`)。
4. 规范化提交:`git commit -m "feat: ..."`(Conventional Commits)。
5. 并回 main:闸绿后 `git switch main && git merge --no-ff <branch>`。

## v2 开发(能力核心层 / 双基座)

- 改能力逻辑 → `src/data_analysis_agent/capabilities/<域>/`;v1 `sampling/` 是纯
  re-export shim(实现物理迁移在 `capabilities/sampling/`),v1 公共 API 不变。
- 能力自测(不依赖 LLM/基座):`.venv/bin/pytest tests/test_capability_*.py -v`;
  全链路:`.venv/bin/python examples/v2/demo_e2e.py`。
- 适配器冒烟(无 key):`(cd harnesses/pi && npm run smoke)` / deepseek 同理。
- 三传输一致性:改 serving/契约后必跑 `tests/test_capability_serving.py`。
- 新增能力:域内 `registry.py` 注册 → `capabilities/serving/registry.py` 装配 →
  `tests/test_capability_serving.py` 的 EXPECTED_CAPABILITIES 同步 → manifest 同步。
- 运行/验证手册:`docs/V2_RUNBOOK.md`;接入新基座:`docs/THIRD_HARNESS_GUIDE.md`。

## 命令

- 质量闸:`.venv/bin/python scripts/quality_gate.py`
- 单测:`.venv/bin/pytest tests/ -v`
- v2 能力 CLI:`.venv/bin/data-agent-capabilities list|call|mcp|compact|retrieve`
- TS 类型检查:`bash harnesses/check-ts.sh`
- 耗时日志:`.quality/gate-runs.jsonl`(每次全跑追加;供后续耗时分析)。
