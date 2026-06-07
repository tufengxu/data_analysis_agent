# Development Workflow

本目录是 git 仓库,采用 trunk-based 流程。`main` 永远绿(过质量闸)。

## 环境

```bash
uv pip install -e ".[data,dev]"   # 沙箱会拦 uv 缓存,需放行;装上 pandas 等
```

## 一次迭代

1. 开短分支:`git switch -c feat/<topic>`(或 `fix/ docs/ refactor/ chore/`)。
2. 大改先写 spec(见 `docs/QUALITY_BAR.md`);小修直接改。
3. 本地过闸:`.venv/bin/python scripts/quality_gate.py`(收尾时 Stop hook 也会强制跑)。
4. 规范化提交:`git commit -m "feat: ..."`(Conventional Commits)。
5. 并回 main:闸绿后 `git switch main && git merge --no-ff <branch>`。

## 命令

- 质量闸:`.venv/bin/python scripts/quality_gate.py`
- 单测:`.venv/bin/pytest tests/ -v`
- 耗时日志:`.quality/gate-runs.jsonl`(每次全跑追加;供后续耗时分析)。
