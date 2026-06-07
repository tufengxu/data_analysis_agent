# 项目防熵规范体系 v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一套确定性、机器强制的项目规范(质量闸 + 架构/文档漂移检测 + git 标准流程 + 阻断式 Stop hook),让后续迭代不会越改越乱。

**Architecture:** 单一 `scripts/quality_gate.py` 作为准出唯一事实源,顺序跑 ruff/format/mypy/pytest + 漂移检测(`scripts/checks.py` 纯函数 + `scripts/drift_rules.py` 规则),把结果与耗时写入 `.quality/gate-runs.jsonl`;项目级 `.claude/settings.json` 的阻断式 Stop hook 在「源码/测试/文档有改动」时调用同一脚本,不过则阻止收尾。文档侧 `docs/ARCHITECTURE.md` 内嵌机器可校验 manifest,与代码树双向比对。

**Tech Stack:** Python 3.13(运行)/≥3.10(目标),stdlib only(ast/subprocess/json/re/pathlib),pytest、ruff、mypy,Claude Code hooks。

**实现偏离 spec 的一处**:规则文件用 `scripts/drift_rules.py`(Python dict)替代 `drift_rules.toml`,避免 `tomllib` 在 3.10 缺失。其余与 `docs/superpowers/specs/2026-06-07-project-standards-anti-entropy-design.md` 一致。

---

## File Structure

| 文件                                                    | 职责                                           | 动作   |
| ------------------------------------------------------- | ---------------------------------------------- | ------ |
| `.gitignore`                                            | 排除 venv/缓存/`.quality`                      | Create |
| `scripts/checks.py`                                     | 漂移检测纯函数(manifest/死链/import 规则/体积) | Create |
| `scripts/drift_rules.py`                                | 依赖规则 + 体积阈值 + 受检文档清单             | Create |
| `scripts/quality_gate.py`                               | 准出 runner + `.quality` 日志 + `--hook` 模式  | Create |
| `tests/conftest.py`                                     | 把 `scripts/` 加入 import 路径                 | Create |
| `tests/test_tooling.py`                                 | checks.py 纯函数的 TDD 测试                    | Create |
| `docs/ARCHITECTURE.md`                                  | 模块职责(manifest 段)+ 不变量 + 依赖规则       | Create |
| `docs/QUALITY_BAR.md`                                   | DoD 硬标尺 + 大改/小修边界                     | Create |
| `docs/DEVELOPMENT.md`                                   | 标准化迭代流程                                 | Create |
| `docs/adr/0001-sampling-exact-over-sketch.md`           | 首条 ADR(追记采样决策)                         | Create |
| `docs/adr/0002-deterministic-anti-entropy-standards.md` | 本规范体系决策                                 | Create |
| `.claude/settings.json`                                 | 阻断式 Stop hook                               | Create |
| `README.md`                                             | 修死链 + 指向新文档                            | Modify |
| `AGENTS.md`                                             | 指向 QUALITY_BAR/DEVELOPMENT                   | Modify |

---

## Task 1: Git 化与基线提交

**Files:**

- Create: `.gitignore`
- Modify: (formatting) `src/**`, `tests/**`

- [ ] **Step 1: 写 `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/

# Virtualenv / tooling caches
.venv/
.uv-cache/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Quality-gate local telemetry (not source)
.quality/

# OS
.DS_Store
```

- [ ] **Step 2: 初始化 git 并确认不会吞掉 venv**

Run:

```bash
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
git init -q
git add .gitignore
git status --porcelain | grep -E "\.venv/|\.uv-cache/" && echo "LEAK" || echo "CLEAN"
```

Expected: `CLEAN`(.gitignore 生效,venv 不在待提交列表)。

- [ ] **Step 3: 归一化格式(让后续 `ruff format --check` 能过)**

Run:

```bash
.venv/bin/ruff format src tests
.venv/bin/ruff check src tests --fix
.venv/bin/ruff check src tests && .venv/bin/mypy src && .venv/bin/python -m pytest tests/ -q
```

Expected: ruff/mypy PASS,`58 passed`(若 format 改动了文件属正常,下一步一起提交)。

- [ ] **Step 4: 首次提交(基线)**

```bash
git add -A
git commit -q -m "chore: initialize git repo with baseline and .gitignore"
git ls-files | grep -E "\.venv/|\.uv-cache/" && echo "LEAK" || echo "CLEAN"
```

Expected: `CLEAN`;`git log --oneline` 有一条提交。

---

## Task 2: 漂移检测纯函数 `scripts/checks.py`(TDD)

**Files:**

- Create: `scripts/checks.py`
- Create: `tests/conftest.py`
- Create: `tests/test_tooling.py`

- [ ] **Step 1: 让测试能 import `scripts/`**

Create `tests/conftest.py`:

```python
"""Make the repo's scripts/ importable from tests."""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
```

- [ ] **Step 2: 写失败测试 `tests/test_tooling.py`**

````python
"""Tests for the quality-gate drift-check helpers in scripts/checks.py."""

from __future__ import annotations

import checks


def test_parse_manifest_extracts_entries():
    md = (
        "intro\n"
        "<!-- manifest:start -->\n"
        '```\n'
        'src/pkg/a.py = "does A"\n'
        'src/pkg/b.py = "does B"\n'
        "```\n"
        "<!-- manifest:end -->\n"
        "outro\n"
    )
    assert checks.parse_manifest(md) == {
        "src/pkg/a.py": "does A",
        "src/pkg/b.py": "does B",
    }


def test_list_source_modules_skips_init(tmp_path):
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("x = 1")
    (pkg / "sub").mkdir()
    (pkg / "sub" / "__init__.py").write_text("")
    (pkg / "sub" / "b.py").write_text("y = 2")
    mods = checks.list_source_modules(tmp_path / "src", tmp_path)
    assert set(mods) == {"src/pkg/a.py", "src/pkg/sub/b.py"}


def test_check_manifest_flags_undocumented_and_dangling(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "a.py").write_text("x = 1")
    (tmp_path / "src" / "pkg" / "b.py").write_text("y = 2")
    arch = tmp_path / "ARCH.md"
    arch.write_text(
        "<!-- manifest:start -->\n```\n"
        'src/pkg/a.py = "A"\n'
        'src/pkg/ghost.py = "missing"\n'
        "```\n<!-- manifest:end -->\n"
    )
    errors = checks.check_manifest(arch, tmp_path / "src", tmp_path)
    joined = "\n".join(errors)
    assert "src/pkg/b.py" in joined  # undocumented
    assert "src/pkg/ghost.py" in joined  # dangling


def test_module_dotted_name_and_imports():
    src = "from . import render\nfrom ..tools import x\nimport os\nfrom data_analysis_agent.config import AgentConfig\n"
    dotted = checks.module_dotted_name("src/data_analysis_agent/sampling/text_summary.py")
    assert dotted == "data_analysis_agent.sampling.text_summary"
    imports = checks.extract_imports(src, dotted)
    assert "data_analysis_agent.sampling.render" in imports
    assert "data_analysis_agent.tools.x" in imports
    assert "data_analysis_agent.config.AgentConfig" in imports
    assert "os" in imports


def test_check_import_rules_flags_forbidden(tmp_path):
    base = tmp_path / "src" / "data_analysis_agent" / "sampling"
    base.mkdir(parents=True)
    (base / "bad.py").write_text("from ..tools import registry\n")
    (base / "ok.py").write_text("from . import model\n")
    rules = [{"who": "data_analysis_agent.sampling", "forbid": ["data_analysis_agent.tools"]}]
    errors = checks.check_import_rules(tmp_path / "src", tmp_path, rules)
    joined = "\n".join(errors)
    assert "bad.py" in joined and "data_analysis_agent.tools" in joined
    assert "ok.py" not in joined


def test_find_repo_paths_and_dead_links(tmp_path):
    (tmp_path / "real.py").write_text("x = 1")
    md = "see `real.py` and `ghost/missing.md` and [x](also_missing.txt) and `not a path`"
    candidates = checks.find_repo_paths(md)
    assert "real.py" in candidates
    assert "ghost/missing.md" in candidates
    assert "also_missing.txt" in candidates
    assert "not a path" not in candidates
    dead = checks.check_dead_links(md, tmp_path)
    assert "real.py" not in "\n".join(dead)
    assert "ghost/missing.md" in "\n".join(dead)


def test_check_file_sizes_warns_over_limit(tmp_path):
    (tmp_path / "src").mkdir()
    big = tmp_path / "src" / "big.py"
    big.write_text("\n".join(f"x{i} = {i}" for i in range(20)))
    warns = checks.check_file_sizes(tmp_path / "src", tmp_path, limit=10)
    assert any("big.py" in w for w in warns)
````

- [ ] **Step 3: 运行,确认全部失败(模块不存在)**

Run: `.venv/bin/python -m pytest tests/test_tooling.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'checks'`。

- [ ] **Step 4: 实现 `scripts/checks.py`**

```python
"""Deterministic drift checks for the quality gate.

Pure functions over the repo tree and docs — no side effects, no LLM. Each
``check_*`` returns a list of human-readable problem strings (empty = OK).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_MANIFEST_RE = re.compile(
    r"<!--\s*manifest:start\s*-->(.*?)<!--\s*manifest:end\s*-->", re.DOTALL
)
_ENTRY_RE = re.compile(r'^\s*(\S+?)\s*=\s*"(.*)"\s*$')


def parse_manifest(markdown: str) -> dict[str, str]:
    """Extract ``path = "desc"`` entries between manifest markers."""
    block = _MANIFEST_RE.search(markdown)
    if not block:
        return {}
    entries: dict[str, str] = {}
    for line in block.group(1).splitlines():
        match = _ENTRY_RE.match(line)
        if match:
            entries[match.group(1)] = match.group(2)
    return entries


def list_source_modules(src_root: Path, repo_root: Path) -> list[str]:
    """All non-``__init__`` .py files under src_root, as repo-relative posix paths."""
    return sorted(
        p.relative_to(repo_root).as_posix()
        for p in src_root.rglob("*.py")
        if p.name != "__init__.py"
    )


def check_manifest(arch_path: Path, src_root: Path, repo_root: Path) -> list[str]:
    declared = parse_manifest(arch_path.read_text(encoding="utf-8"))
    actual = set(list_source_modules(src_root, repo_root))
    errors: list[str] = []
    for module in sorted(actual - set(declared)):
        errors.append(f"manifest: 模块未登记于 ARCHITECTURE.md: {module}")
    for entry in sorted(set(declared) - actual):
        errors.append(f"manifest: 登记项指向不存在的文件: {entry}")
    return errors


def module_dotted_name(repo_rel_path: str) -> str:
    """src/data_analysis_agent/sampling/x.py -> data_analysis_agent.sampling.x"""
    parts = repo_rel_path.split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][: -len(".py")]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def extract_imports(source: str, module_dotted: str) -> set[str]:
    """Absolute dotted names imported by source (relative imports resolved)."""
    tree = ast.parse(source)
    package_parts = module_dotted.split(".")[:-1]  # the module's package
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                prefix = base + ([node.module] if node.module else [])
            else:
                prefix = [node.module] if node.module else []
            base_dotted = ".".join(prefix)
            for alias in node.names:
                found.add(f"{base_dotted}.{alias.name}" if base_dotted else alias.name)
    return found


def _matches(who: str, module_dotted: str) -> bool:
    return module_dotted == who or module_dotted.startswith(who + ".")


def _forbidden(target: str, imported: str) -> bool:
    return imported == target or imported.startswith(target + ".")


def check_import_rules(
    src_root: Path, repo_root: Path, rules: list[dict[str, object]]
) -> list[str]:
    errors: list[str] = []
    for path in src_root.rglob("*.py"):
        rel = path.relative_to(repo_root).as_posix()
        dotted = module_dotted_name(rel)
        imports = extract_imports(path.read_text(encoding="utf-8"), dotted)
        for rule in rules:
            who = str(rule["who"])
            if not _matches(who, dotted):
                continue
            for target in (str(t) for t in rule["forbid"]):  # type: ignore[union-attr]
                for imp in imports:
                    if _forbidden(target, imp):
                        errors.append(
                            f"import-rule: {rel} 不得 import {imp} (规则 who={who})"
                        )
    return errors


_PATH_TOKEN = re.compile(r"`([^`]+)`|\]\(([^)]+)\)")


def find_repo_paths(markdown: str) -> list[str]:
    """Candidate repo paths referenced in markdown (backtick paths + md links)."""
    out: list[str] = []
    for m in _PATH_TOKEN.finditer(markdown):
        token = (m.group(1) or m.group(2) or "").strip()
        if not token or token.startswith(("http://", "https://", "#", "mailto:")):
            continue
        looks_like_path = "/" in token or re.search(r"\.\w{1,5}$", token)
        if looks_like_path and " " not in token.strip("/"):
            out.append(token.rstrip("/"))
    return out


def check_dead_links(markdown: str, repo_root: Path) -> list[str]:
    errors: list[str] = []
    for token in find_repo_paths(markdown):
        if (repo_root / token).exists():
            continue
        errors.append(f"dead-link: 引用的路径不存在: {token}")
    return errors


def check_file_sizes(src_root: Path, repo_root: Path, limit: int) -> list[str]:
    warnings: list[str] = []
    for path in src_root.rglob("*.py"):
        loc = len(path.read_text(encoding="utf-8").splitlines())
        if loc > limit:
            rel = path.relative_to(repo_root).as_posix()
            warnings.append(f"file-size: {rel} = {loc} LOC > {limit} (god-file 风险)")
    return warnings
```

- [ ] **Step 5: 运行测试,确认通过**

Run: `.venv/bin/python -m pytest tests/test_tooling.py -q`
Expected: PASS(8 个测试)。

- [ ] **Step 6: 提交**

```bash
git add scripts/checks.py tests/conftest.py tests/test_tooling.py
git commit -q -m "feat: add deterministic drift-check helpers for quality gate"
```

---

## Task 3: 架构文档 + manifest,修 README 死链

**Files:**

- Create: `docs/ARCHITECTURE.md`
- Modify: `README.md`(删除 `OKComputer_AgentDesign_Chat/` 死链)

- [ ] **Step 1: 写 `docs/ARCHITECTURE.md`(含机器可校验 manifest)**

```markdown
# Architecture

DataAnalysisAgent 是 ReAct(Reasoning+Acting)模式的数据分析 agent:模型决定「做什么」,
harness 决定「做多少」。本文件是架构的单一事实源;下方 manifest 段被 `scripts/checks.py`
机器校验,**新增/删除模块必须同步更新这里,否则质量闸 fail**。

## 子系统不变量

- **tools**:fail-closed(`is_destructive` 默认 True);`python_exec` 走受限子进程,
  `PYTHONPATH=""`。工具不得反向依赖 `agent_loop`。
- **sampling**:叶子工具,只在包内自依赖;`sandbox_summary.py` 不得 import 本包(被内联进沙箱)。
- **protocol**:底层 LLM 适配,不得依赖 `agent_loop`/`tools`/`skills`。
- **state**:不可变,经 `with_*()` 更新。

## 依赖规则(与 `scripts/drift_rules.py` 强制项同源)

- `sampling/*` ✗→ `tools`/`agent_loop`/`protocol`/`skills`/`security`/`context`
- `sampling/sandbox_summary.py` ✗→ 任何 `data_analysis_agent.*`
- `tools/*` ✗→ `agent_loop`
- `protocol/*` ✗→ `agent_loop`/`tools`/`skills`

## 模块 manifest

<!-- manifest:start -->
```

src/data_analysis_agent/**main**.py = "CLI 入口:rich UI、交互模式、registry/agent 装配"
src/data_analysis_agent/agent_loop.py = "ReAct while-loop 引擎 + 9 步流水线 + 错误恢复"
src/data_analysis_agent/state_machine.py = "不可变状态容器、ContinueReason、TerminalReason"
src/data_analysis_agent/events.py = "异步事件流类型(流式文本/工具/状态变更)"
src/data_analysis_agent/config.py = "AgentConfig 加载合并 + sampling_config() 构造"
src/data_analysis_agent/persistence.py = "append-only JSONL 消息存储 + session fork"
src/data_analysis_agent/context/compression.py = "5 级消息压缩流水线"
src/data_analysis_agent/protocol/client.py = "Anthropic 流式/非流式客户端 + 重试 + 懒导入"
src/data_analysis_agent/protocol/messages.py = "ContentBlock 类型层级"
src/data_analysis_agent/tools/base.py = "Tool 抽象基类 + ToolResult/Validation/Permission"
src/data_analysis_agent/tools/registry.py = "工具注册/过滤/装配(3 阶段)"
src/data_analysis_agent/tools/file_read.py = "按 offset/limit 读文件"
src/data_analysis_agent/tools/python_exec.py = "受限子进程执行 + 采样摘要注入"
src/data_analysis_agent/tools/nl_query.py = "自然语言 → pandas/SQL 代码生成"
src/data_analysis_agent/tools/visualization.py = "matplotlib/seaborn/plotly 图表生成"
src/data_analysis_agent/skills/base.py = "Skill 抽象基类"
src/data_analysis_agent/skills/registry.py = "技能注册 + 关键词匹配 + 优先级路由"
src/data_analysis_agent/skills/builtin.py = "描述性/相关性/趋势 三个内置分析技能"
src/data_analysis_agent/security/permissions.py = "deny-first 权限引擎(4 层防御)"
src/data_analysis_agent/sampling/config.py = "SamplingConfig + fidelity 档位预设"
src/data_analysis_agent/sampling/model.py = "ColumnSummary / TableSummary 数据类"
src/data_analysis_agent/sampling/render.py = "L3 Markdown 渲染器(共享,带采样警告)"
src/data_analysis_agent/sampling/text_summary.py = "harness 纯 stdlib 兜底摘要器"
src/data_analysis_agent/sampling/sandbox_summary.py = "精确 DataFrame 摘要,内联进 python_exec 沙箱"

```
<!-- manifest:end -->
```

- [ ] **Step 2: 修掉 README 死链**

先 `Read README.md` 定位 `OKComputer_AgentDesign_Chat/` 段落(约 147–156 行的 "Architecture Reference" 小节),把指向不存在目录的引用替换为指向 `docs/ARCHITECTURE.md`:

替换该小节为:

```markdown
## Architecture Reference

See `docs/ARCHITECTURE.md` for the module map (machine-checked manifest), subsystem
invariants, and dependency rules. Design specs live under `docs/superpowers/specs/`.
```

- [ ] **Step 3: 校验当前仓库的 manifest 与死链(临时手跑)**

Run:

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "scripts")
from pathlib import Path
import checks
root = Path(".")
print("manifest:", checks.check_manifest(root/"docs/ARCHITECTURE.md", root/"src", root))
for f in ["README.md", "AGENTS.md", "docs/ARCHITECTURE.md"]:
    print(f, "deadlinks:", checks.check_dead_links(Path(f).read_text(), root))
PY
```

Expected: `manifest: []`(全模块已登记且无悬挂);三个文档 `deadlinks: []`。若有残留死链,逐个修到列表为空。

- [ ] **Step 4: 提交**

```bash
git add docs/ARCHITECTURE.md README.md
git commit -q -m "docs: add machine-checked ARCHITECTURE manifest, fix README dead link"
```

---

## Task 4: 依赖规则 `scripts/drift_rules.py` + 对当前代码验证

**Files:**

- Create: `scripts/drift_rules.py`

- [ ] **Step 1: 写规则**

```python
"""Data-driven rules for the deterministic drift checks.

Edit here to evolve architecture guarantees. ``who`` matches a module whose
dotted name equals it or starts with ``who + "."``; ``forbid`` lists dotted
prefixes that such modules must not import.
"""

from __future__ import annotations

IMPORT_RULES: list[dict[str, object]] = [
    {
        "who": "data_analysis_agent.sampling",
        "forbid": [
            "data_analysis_agent.tools",
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.protocol",
            "data_analysis_agent.skills",
            "data_analysis_agent.security",
            "data_analysis_agent.context",
        ],
    },
    {
        "who": "data_analysis_agent.sampling.sandbox_summary",
        "forbid": ["data_analysis_agent"],
    },
    {
        "who": "data_analysis_agent.tools",
        "forbid": ["data_analysis_agent.agent_loop"],
    },
    {
        "who": "data_analysis_agent.protocol",
        "forbid": [
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.tools",
            "data_analysis_agent.skills",
        ],
    },
]

# Documents scanned for dead repo-path references.
DOC_FILES: list[str] = ["README.md", "AGENTS.md", "docs/ARCHITECTURE.md"]

# god-file warning threshold (lines of code). Phase 1: warn only.
FILE_SIZE_LIMIT = 600
```

- [ ] **Step 2: 跑依赖规则,确认当前代码 0 违例(否则修代码或调规则)**

Run:

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "scripts")
from pathlib import Path
import checks, drift_rules
root = Path(".")
errs = checks.check_import_rules(root/"src", root, drift_rules.IMPORT_RULES)
print("import-rule violations:", errs or "NONE")
print("size warnings:", checks.check_file_sizes(root/"src", root, drift_rules.FILE_SIZE_LIMIT) or "NONE")
PY
```

Expected: `import-rule violations: NONE`。若有违例:优先修代码使其符合架构;确属规则过严则在 `drift_rules.py` 收敛,并在 commit message 说明。

- [ ] **Step 3: 提交**

```bash
git add scripts/drift_rules.py
git commit -q -m "feat: add data-driven dependency + size drift rules"
```

---

## Task 5: 质量闸 runner `scripts/quality_gate.py` + 日志

**Files:**

- Create: `scripts/quality_gate.py`

- [ ] **Step 1: 实现 runner**

```python
"""Single source of truth for the project's quality bar (Definition of Done).

Runs ruff + format-check + mypy + pytest + deterministic drift checks, appends a
timing record to .quality/gate-runs.jsonl, and exits non-zero on any failure.

Modes:
    (default)  run the full gate, human-readable output.
    --hook     Claude Code Stop-hook mode: skip when no src/tests/docs changes;
               on failure emit a block decision so the agent must fix before stop.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks  # noqa: E402
import drift_rules  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
BIN = Path(sys.executable).parent


def _run(cmd: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    ok = proc.returncode == 0
    out = (proc.stdout + proc.stderr).strip()
    return ok, out


def _drift() -> tuple[bool, str]:
    problems: list[str] = []
    problems += checks.check_manifest(REPO / "docs/ARCHITECTURE.md", SRC, REPO)
    problems += checks.check_import_rules(SRC, REPO, drift_rules.IMPORT_RULES)
    for doc in drift_rules.DOC_FILES:
        text = (REPO / doc).read_text(encoding="utf-8")
        problems += checks.check_dead_links(text, REPO)
    warnings = checks.check_file_sizes(SRC, REPO, drift_rules.FILE_SIZE_LIMIT)
    msg_parts = []
    if warnings:
        msg_parts.append("warnings:\n  " + "\n  ".join(warnings))
    if problems:
        msg_parts.append("errors:\n  " + "\n  ".join(problems))
    return (not problems), "\n".join(msg_parts).strip()


def run_gate() -> tuple[bool, list[dict[str, object]]]:
    steps: list[tuple[str, object]] = [
        ("ruff", lambda: _run([str(BIN / "ruff"), "check", "src", "tests", "scripts"])),
        ("format", lambda: _run([str(BIN / "ruff"), "format", "--check", "src", "tests", "scripts"])),
        ("mypy", lambda: _run([str(BIN / "mypy"), "src"])),
        ("pytest", lambda: _run([str(BIN / "pytest"), "tests/", "-q"])),
        ("drift", _drift),
    ]
    results: list[dict[str, object]] = []
    for name, fn in steps:
        start = time.perf_counter()
        ok, out = fn()  # type: ignore[operator]
        elapsed = round(time.perf_counter() - start, 3)
        results.append({"name": name, "ok": ok, "sec": elapsed, "out": out})
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {name} ({elapsed}s)")
        if not ok and out:
            print("\n".join("    " + line for line in out.splitlines()[-20:]))
    return all(r["ok"] for r in results), results


def _log(passed: bool, results: list[dict[str, object]], total: float) -> None:
    qdir = REPO / ".quality"
    qdir.mkdir(exist_ok=True)
    head, _ = _run(["git", "rev-parse", "--short", "HEAD"])
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "git_head": head if isinstance(head, str) else "",
        "passed": passed,
        "total_sec": round(total, 3),
        "steps": [{k: r[k] for k in ("name", "ok", "sec")} for r in results],
    }
    with (qdir / "gate-runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _changed() -> bool:
    """True if src/tests/docs have tracked diffs or untracked files."""
    diff = subprocess.run(
        ["git", "diff", "--quiet", "--", "src", "tests", "docs"], cwd=REPO
    )
    if diff.returncode != 0:
        return True
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "src", "tests", "docs"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return bool(untracked.stdout.strip())


def main() -> int:
    hook = "--hook" in sys.argv
    if hook and not _changed():
        return 0  # nothing relevant changed -> allow stop

    start = time.perf_counter()
    passed, results = run_gate()
    total = time.perf_counter() - start
    _log(passed, results, total)
    print(f"\n{'PASS' if passed else 'FAIL'} — quality gate ({round(total, 2)}s)")

    if hook and not passed:
        failed = [r["name"] for r in results if not r["ok"]]
        reason = (
            "质量闸未通过(" + ", ".join(map(str, failed)) + ")。"
            "运行 `.venv/bin/python scripts/quality_gate.py` 查看详情并修复后再收尾。"
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0  # block decision delivered via JSON; exit 0
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 整仓跑一次,必须 PASS**

Run: `.venv/bin/python scripts/quality_gate.py`
Expected: 五步全 `[PASS]`,末行 `PASS — quality gate (…s)`;`.quality/gate-runs.jsonl` 新增一行。

- [ ] **Step 3: 确认日志写入**

Run: `cat .quality/gate-runs.jsonl | tail -1`
Expected: 一条 JSON,含 `"passed": true` 与各步 `sec`。

- [ ] **Step 4: 提交**

```bash
git add scripts/quality_gate.py
git commit -q -m "feat: add quality-gate runner with drift checks and timing log"
```

---

## Task 6: 规范文档(QUALITY_BAR / DEVELOPMENT / ADR)

**Files:**

- Create: `docs/QUALITY_BAR.md`, `docs/DEVELOPMENT.md`
- Create: `docs/adr/0001-sampling-exact-over-sketch.md`, `docs/adr/0002-deterministic-anti-entropy-standards.md`
- Modify: `AGENTS.md`(指向新文档)

- [ ] **Step 1: 写 `docs/QUALITY_BAR.md`**

```markdown
# Quality Bar — Definition of Done

每次迭代「完成」的硬性标尺。**全部满足才算 done**;由 `scripts/quality_gate.py` 机器强制,
并由阻断式 Stop hook 在收尾时执行。

## 准出清单

- [ ] `python scripts/quality_gate.py` 全绿(ruff / format / mypy / pytest / drift)。
- [ ] 新增或删除模块时,`docs/ARCHITECTURE.md` 的 manifest 已同步(否则 drift fail)。
- [ ] 改动有明确记录:见下方「大改 vs 小修」。
- [ ] 提交信息符合 Conventional Commits(`feat/fix/docs/refactor/test/chore`)。

## 大改 vs 小修

- **大改(必走 spec)**:新增模块 / 新公共 API / 跨模块改动 / 改依赖规则。
  先在 `docs/superpowers/specs/YYYY-MM-DD-*.md` 写 spec;涉架构决策再加 `docs/adr/NNNN-*.md`;
  commit message 引用 spec 路径。
- **小修(过闸即可)**:单模块内 bugfix、内部重构、文档微调。分支 + 规范化 commit。

## 闸由什么组成

ruff(lint)· ruff format --check(风格)· mypy src(类型,strict)· pytest(全测试)·
drift(模块 manifest 同步、文档死链、依赖规则、600 LOC 体积告警)。
```

- [ ] **Step 2: 写 `docs/DEVELOPMENT.md`**

````markdown
# Development Workflow

本目录是 git 仓库,采用 trunk-based 流程。`main` 永远绿(过质量闸)。

## 环境

```bash
uv pip install -e ".[data,dev]"   # 沙箱会拦 uv 缓存,需放行;装上 pandas 等
```
````

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

````

- [ ] **Step 3: 写两条 ADR**

`docs/adr/0001-sampling-exact-over-sketch.md`:
```markdown
# 0001 — 采样摘要:沙箱精确计算优于流式 sketch 库

- 状态: Accepted (2026-06-06)

## 背景
大结果进上下文此前是盲截断。调研报告主推 t-digest/HLL/CMS 流式 sketch。

## 决策
内存有界场景(子进程把文件读入 DataFrame)用 pandas 精确统计;只在无结构文本兜底用纯 stdlib
近似。**不引入第三方 sketch 库**。

## 理由
内存态 pandas 精确计算又快又准、严格优于近似;零新依赖契合精简依赖与离线沙箱约束。

## 影响
新增 `sampling/` 模块;两个接缝(python_exec 沙箱、agent_loop 兜底)。详见
`docs/superpowers/specs/2026-06-06-data-sampling-compaction-design.md`。
````

`docs/adr/0002-deterministic-anti-entropy-standards.md`:

```markdown
# 0002 — 确定性防熵规范(质量闸 + 漂移检测 + 阻断式 Stop hook)

- 状态: Accepted (2026-06-07)

## 背景

质量与架构 enforcement 全靠人自觉,已出现文档漂移。AGENTS.md 曾被 LLM cron 写坏。

## 决策

建立单一 `scripts/quality_gate.py` 准出源 + 确定性漂移检测;git 化 + Conventional Commits;
项目级阻断式 Stop hook。**自动维护用确定性漂移检测(发散即 fail),不用 LLM 再生成文档。**

## 理由

把 enforcement 从人转移到机器;确定性检测规避 LLM 重写文档的损坏风险。

## 影响

新增 `scripts/`、`docs/ARCHITECTURE.md|QUALITY_BAR.md|DEVELOPMENT.md`、`.claude/settings.json`
Stop hook。详见 `docs/superpowers/specs/2026-06-07-project-standards-anti-entropy-design.md`。
```

- [ ] **Step 4: AGENTS.md 增「质量准出」指引**

`Read AGENTS.md`,在「已知约束 / 关键决策」小节末尾(`完整架构说明见 README.md` 之前)插入:

```markdown
- **质量准出硬标尺**:每次迭代须过 `scripts/quality_gate.py`(ruff/format/mypy/pytest/drift),
  由阻断式 Stop hook 强制。规范见 `docs/QUALITY_BAR.md` 与 `docs/DEVELOPMENT.md`;架构与 manifest
  见 `docs/ARCHITECTURE.md`。新增/删模块必须同步 manifest。
```

- [ ] **Step 5: 过闸 + 提交**

Run: `.venv/bin/python scripts/quality_gate.py`
Expected: PASS(新增文档不得引入死链;若 ADR/QUALITY_BAR 引用的路径不存在会被 drift 抓到 → 修正)。

```bash
git add docs/QUALITY_BAR.md docs/DEVELOPMENT.md docs/adr/ AGENTS.md
git commit -q -m "docs: add quality bar, dev workflow, and ADRs 0001-0002"
```

---

## Task 7: 阻断式 Stop hook

**Files:**

- Create: `.claude/settings.json`

- [ ] **Step 1: 写项目级 Stop hook**

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR/.venv/bin/python\" \"$CLAUDE_PROJECT_DIR/scripts/quality_gate.py\" --hook"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: 验证「无关改动放行」(快路径)**

Run:

```bash
git stash -u 2>/dev/null; .venv/bin/python scripts/quality_gate.py --hook; echo "exit=$?"
```

Expected: 无输出(或无 block JSON),`exit=0`(工作区干净 → 放行)。随后 `git stash pop` 若有 stash。

- [ ] **Step 3: 验证「制造失败被 block」**

Run:

```bash
printf '\nbad_syntax(' >> src/data_analysis_agent/config.py
.venv/bin/python scripts/quality_gate.py --hook | tail -1
git checkout -- src/data_analysis_agent/config.py
```

Expected: 末行是 `{"decision": "block", "reason": "质量闸未通过(...)..."}`。恢复后再跑一次应放行。

- [ ] **Step 4: 验证 docs-only 改动会触发闸但仍能过**

Run:

```bash
printf '\n<!-- touch -->\n' >> docs/ARCHITECTURE.md
.venv/bin/python scripts/quality_gate.py --hook | tail -2
git checkout -- docs/ARCHITECTURE.md
```

Expected: 闸运行且 `PASS`(无 block JSON);说明 docs 改动纳入强制但不误伤。

- [ ] **Step 5: 提交**

```bash
git add .claude/settings.json
git commit -q -m "feat: enforce quality gate via blocking Stop hook"
```

> 注:Stop hook 现已生效。本会话若由 Claude Code 驱动,后续每次收尾都会在「有码/文档改动」时强制跑闸。

---

## Task 8: 收口 —— 整仓绿 + 记忆/README 指针

**Files:**

- Modify: `README.md`(Key Features 增「Quality Gate」一行)

- [ ] **Step 1: README 增准出说明**

在 `README.md` 的 Development 小节后追加:

```markdown
## Quality Gate (Definition of Done)

每次迭代须通过 `python scripts/quality_gate.py`(ruff / format / mypy / pytest / 架构漂移检测),
由阻断式 Stop hook 强制。详见 `docs/QUALITY_BAR.md`、`docs/DEVELOPMENT.md`、`docs/ARCHITECTURE.md`。
```

- [ ] **Step 2: 最终整仓过闸**

Run: `.venv/bin/python scripts/quality_gate.py`
Expected: 五步全 PASS。

- [ ] **Step 3: 确认无 venv 泄漏、提交历史规范**

Run:

```bash
git ls-files | grep -E "\.venv/|\.uv-cache/" && echo "LEAK" || echo "CLEAN"
git log --oneline
```

Expected: `CLEAN`;7–8 条 Conventional Commits。

- [ ] **Step 4: 提交收口**

```bash
git add README.md
git commit -q -m "docs: document quality gate in README"
```

---

## Self-Review(写计划者已自查)

- **Spec coverage**:组件 1(Task 1)、组件 2(Task 5)、组件 3(Task 2+4)、组件 4(Task 7)、
  组件 5(Task 3+6)、组件 6(Task 6 QUALITY_BAR)、决策 6 耗时日志(Task 5 `_log`)、
  验收 1–6(分散于各 Task 的 verify 步)——均有任务承载。
- **Placeholder scan**:无 TBD/TODO;所有代码、命令、预期输出均给全。
- **Type/名称一致性**:`checks.py` 暴露的 `parse_manifest/list_source_modules/check_manifest/
module_dotted_name/extract_imports/check_import_rules/find_repo_paths/check_dead_links/
check_file_sizes` 与测试、`quality_gate.py`、`drift_rules.py`(`IMPORT_RULES/DOC_FILES/
FILE_SIZE_LIMIT`)调用处一致。

```

```
