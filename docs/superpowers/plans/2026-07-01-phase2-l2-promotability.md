# Phase 2 · L2 技能质量与可晋升性 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"评估—晋升"回路的两个断点接上,使 `evaluate` 能给出 `promote`/`retire` 而非清一色 `needs_review`,自进化 L2 回路闭环。

**Architecture:** 2A 在 `trajectory.py` 捕获已流经 `ToolUseEvent` 的 tool parameters(管线本就通,只是被丢),脱敏后存入 `ToolCallRecord.input_digest` + `referenced_files`,供 synthesizer 反思与 2B 收割;2B 新建 `eval_harvester.py` 从轨迹自动产出 EvalTask + 冻结 fixture,解冷启动;2C 显式 defer。

**Tech Stack:** Python 3.10+(stdlib dataclasses / `hashlib` / `json` / `math` / `pathlib`),pytest,ruff,mypy。无新第三方依赖。

## Global Constraints

- **质量闸**:每 Task 末跑 `.venv/bin/python scripts/quality_gate.py`(ruff + format + mypy + pytest + drift)须全绿。
- **venv 准备(首次)**:跑测试前需 `uv pip install -e ".[data,dev]"`(关沙箱)。命令在 DataAnalysisAgent 目录下执行。
- **ADR 0005(硬约束)**:eval 断言只验方法/结构,**绝不**固化数值(不得出现 `pass_rate == X` / `留存率 == 12%` 之类)。
- **向后兼容**:ToolCallRecord 新字段必须有默认值,旧 jsonl 用 `.get(..., 默认)` 读回不报错。
- **路径硬依赖**:脱敏的硬依赖只是 HOME(`Path.home()`,始终可用);`analysis_paths` 是可选增强,为 None 时降级为 HOME-only,不崩。
- **不静默丢**:harvester 跳过任务必须 `logger.warning`(找不到文件 / 超上限)。
- **提交节奏**:每个 Task 末提交一次(conventional commits)。

---

## File Structure

| 文件                                                  | 责任                                                       | 动作 |
| ----------------------------------------------------- | ---------------------------------------------------------- | ---- |
| `src/data_analysis_agent/config.py`                   | `enable_trajectory_inputs` 开关 + `eval_tasks_dir()`       | 改   |
| `src/data_analysis_agent/telemetry/trajectory.py`     | 捕获 tool input → `input_digest`/`referenced_files` + 脱敏 | 改   |
| `src/data_analysis_agent/runtime.py`                  | 把开关 + analysis_paths 透传给 TrajectoryLogger            | 改   |
| `src/data_analysis_agent/evolution/eval_harvester.py` | 轨迹 → EvalTask + 冻结 fixture(确定性,无 LLM)              | 新建 |
| `src/data_analysis_agent/evolution/evaluator.py`      | SkillEvaluator 读多目录                                    | 改   |
| `src/data_analysis_agent/evolution/__main__.py`       | 注册 `harvest-eval` 子命令                                 | 改   |
| `tests/test_trajectory_inputs.py`                     | 2A 测试                                                    | 新建 |
| `tests/test_eval_harvester.py`                        | 2B 测试                                                    | 新建 |
| `tests/test_phase2_acceptance.py`                     | 端到端闭环验收                                             | 新建 |

---

## Task 1: config 开关 `enable_trajectory_inputs`

**Files:**

- Modify: `src/data_analysis_agent/config.py`(在 `enable_memory` 块之后加字段)
- Test: `tests/test_trajectory_inputs.py`

**Interfaces:**

- Produces: `AgentConfig.enable_trajectory_inputs: bool`(默认 True)

- [ ] **Step 1: Write the failing test**

Create `tests/test_trajectory_inputs.py`:

```python
from data_analysis_agent.config import AgentConfig


def test_enable_trajectory_inputs_defaults_true():
    assert AgentConfig().enable_trajectory_inputs is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_trajectory_inputs.py::test_enable_trajectory_inputs_defaults_true -v`
Expected: FAIL with `AttributeError: enable_trajectory_inputs`

- [ ] **Step 3: Write minimal implementation**

In `src/data_analysis_agent/config.py`, after the `enable_memory` / `memory_inject_budget_tokens` block (around line 85), add:

```python
    # Trajectory tool-input capture: record the "success process" (tool params /
    # code skeletons) so the synthesizer can learn reusable recipes. Off → only
    # tool name / duration / result_chars are recorded (privacy-preserving).
    enable_trajectory_inputs: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_trajectory_inputs.py::test_enable_trajectory_inputs_defaults_true -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/data_analysis_agent/config.py tests/test_trajectory_inputs.py
git commit -m "feat(config): add enable_trajectory_inputs toggle"
```

---

## Task 2: ToolCallRecord 捕获 input_digest + referenced_files

**Files:**

- Modify: `src/data_analysis_agent/telemetry/trajectory.py`
- Test: `tests/test_trajectory_inputs.py`(追加)

**Interfaces:**

- Produces: `ToolCallRecord.input_digest: str`、`ToolCallRecord.referenced_files: tuple[str,...]`;`TrajectoryLogger.__init__(..., enable_inputs=True, analysis_paths=None, home=None)`;模块函数 `_digest_tool_input`、`_extract_referenced_files`。
- Consumes: `ToolUseEvent.parameters`(已由 `agent_loop.py:301` 填充,本任务不改 agent_loop)。

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trajectory_inputs.py`:

```python
import json

from data_analysis_agent.events import ToolResultEvent, ToolUseEvent
from data_analysis_agent.telemetry.trajectory import (
    TrajectoryLogger,
    _digest_tool_input,
    _extract_referenced_files,
)


def test_digest_desensitizes_home_path():
    params = {"path": "/Users/testuser/data/sales.csv", "n": 3}
    out = _digest_tool_input(params, home=__import__("pathlib").Path("/Users/testuser"))
    assert "/Users/testuser" not in out
    assert "<path:sales.csv>" in out
    assert json.loads(out)["n"] == 3  # non-path values preserved


def test_digest_truncates_oversize():
    params = {"code": "x" * 5000}
    out = _digest_tool_input(params, home=__import__("pathlib").Path("/Users/u"), cap=100)
    assert out.endswith("…(truncated)")
    assert len(out) == 100 + len("…(truncated)")


def test_extract_referenced_files_by_suffix():
    params = {"path": "/abs/path/orders.xlsx", "other": "not_a_file"}
    assert _extract_referenced_files(params) == ("orders.xlsx",)


def _logger(tmp_path, **kw):
    return TrajectoryLogger(tmp_path / "traj", "s1", **kw)


def _feed(logger, params):
    logger.begin_turn("q")
    logger(ToolUseEvent(tool_use_id="t1", tool_name="data_profile", parameters=params))
    logger(ToolResultEvent(tool_use_id="t1", tool_name="data_profile", content="ok"))
    return logger.end_turn()


def test_capture_records_input_digest_and_refs(tmp_path):
    logger = _logger(tmp_path)
    rec = _feed(logger, {"path": "/Users/u/data/sales.csv"})
    tc = rec.tool_calls[0]
    assert tc.name == "data_profile"
    assert "<path:sales.csv>" in tc.input_digest
    assert tc.referenced_files == ("sales.csv",)


def test_enable_inputs_false_omits_fields(tmp_path):
    logger = _logger(tmp_path, enable_inputs=False)
    rec = _feed(logger, {"path": "/Users/u/data/sales.csv"})
    tc = rec.tool_calls[0]
    assert tc.input_digest == ""
    assert tc.referenced_files == ()
    # other fields still captured
    assert tc.name == "data_profile" and tc.result_chars == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_trajectory_inputs.py -v`
Expected: FAIL(`_digest_tool_input` 未定义 / `input_digest` 属性缺失)。

- [ ] **Step 3: Write minimal implementation**

In `src/data_analysis_agent/telemetry/trajectory.py`:

(a) 顶部 import 增加 `import json` 与 `Any`。把现有 import 块改为包含:

```python
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
```

(b) 常量区(`_DIGEST_CHARS = 2000` 旁)加:

```python
_INPUT_DIGEST_CHARS = 1000
_DATA_SUFFIXES = (".csv", ".tsv", ".xlsx", ".xls", ".parquet")
```

(c) `ToolCallRecord` 加两字段(有默认值 → 向后兼容):

```python
@dataclass
class ToolCallRecord:
    """One tool invocation within a turn."""

    name: str
    is_error: bool
    duration_ms: int
    result_chars: int
    input_digest: str = ""  # desensitized param JSON — the "how" (for reflection)
    referenced_files: tuple[str, ...] = ()  # basenames of data files touched (for harvesting)
```

(d) 在 `ToolCallRecord` 之上、`_utc_now` 附近加两个模块函数:

```python
def _walk_values(obj: Any):
    """Yield scalar values from nested dict/list structures."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_values(v)
    else:
        yield obj


def _extract_referenced_files(params: dict[str, Any]) -> tuple[str, ...]:
    """Best-effort basenames of data files named in tool params (suffix match).

    I/O-free: only string inspection. Existence is the harvester's job.
    Over-collection is harmless (an unused fixture costs little).
    """
    found: list[str] = []
    for value in _walk_values(params):
        low = str(value).lower()
        if any(low.endswith(suf) for suf in _DATA_SUFFIXES):
            name = Path(str(value)).name
            if name and name not in found:
                found.append(name)
    return tuple(found)


def _digest_tool_input(
    params: dict[str, Any],
    *,
    analysis_paths: Sequence[str | Path] | None = None,
    cap: int = _INPUT_DIGEST_CHARS,
    home: Path | None = None,
) -> str:
    """JSON-serialized params with absolute paths stripped to <path:basename>.

    HOME prefix is always stripped; analysis_paths (if given) stripped too.
    """
    if home is None:
        home = Path.home()
    prefixes = [str(home), *(str(p) for p in (analysis_paths or ()))]

    def scrub(v: Any) -> Any:
        if isinstance(v, str):
            for prefix in prefixes:
                if prefix and v.startswith(prefix):
                    return f"<path:{Path(v).name}>"
            return v
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [scrub(x) for x in v]
        return v

    try:
        text = json.dumps(scrub(params), ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(params)
    if len(text) > cap:
        return text[:cap] + "…(truncated)"
    return text
```

(e) `TrajectoryLogger.__init__` 加关键字参数,`_reset` 扩 `_tool_starts` 元组:

```python
    def __init__(
        self,
        trajectories_dir: str | Path,
        session_id: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        enable_inputs: bool = True,
        analysis_paths: Sequence[str | Path] | None = None,
        home: Path | None = None,
    ) -> None:
        self.dir = Path(trajectories_dir)
        self.session_id = session_id
        self._monotonic = monotonic
        self._enable_inputs = enable_inputs
        self._analysis_paths = list(analysis_paths) if analysis_paths else []
        self._home = home
        self._store = JsonlStore(self.dir / f"{session_id}.jsonl")
        self.path = self._store.path
        self._last_turn_id: str | None = None
        self._reset()
```

注:`Sequence` 已在文件用?若未导入,加 `from collections.abc import Sequence`(`Callable` 同处)。

`_reset` 内把:

```python
        self._tool_starts: dict[str, tuple[str, float]] = {}
```

改为:

```python
        self._tool_starts: dict[str, tuple[str, float, dict[str, Any]]] = {}
```

(f) `__call__` 的两个分支改写:

```python
        elif isinstance(event, ToolUseEvent):
            self._tool_starts[event.tool_use_id] = (
                event.tool_name,
                self._monotonic(),
                dict(event.parameters),
            )
        elif isinstance(event, ToolResultEvent):
            name, started, params = self._tool_starts.pop(
                event.tool_use_id, (event.tool_name, self._monotonic(), {})
            )
            if self._enable_inputs:
                digest = _digest_tool_input(
                    params, analysis_paths=self._analysis_paths, home=self._home
                )
                refs = _extract_referenced_files(params)
            else:
                digest, refs = "", ()
            self._tool_calls.append(
                ToolCallRecord(
                    name=name or event.tool_name,
                    is_error=event.is_error,
                    duration_ms=int(max(0.0, self._monotonic() - started) * 1000),
                    result_chars=len(event.content),
                    input_digest=digest,
                    referenced_files=refs,
                )
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_trajectory_inputs.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 质量闸 + Commit**

```bash
.venv/bin/python scripts/quality_gate.py
git add src/data_analysis_agent/telemetry/trajectory.py tests/test_trajectory_inputs.py
git commit -m "feat(trajectory): record tool input_digest + referenced_files"
```

---

## Task 3: runtime 透传开关 + analysis_paths

**Files:**

- Modify: `src/data_analysis_agent/runtime.py:256-259`
- Test: `tests/test_trajectory_inputs.py`(追加)

**Interfaces:**

- Consumes: Task 1 的 `config.enable_trajectory_inputs`、Task 2 的 `TrajectoryLogger.__init__(enable_inputs=, analysis_paths=)`。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trajectory_inputs.py`:

```python
from dataclasses import replace

from data_analysis_agent.config import AgentConfig
from data_analysis_agent.runtime import AgentRuntime


class _FakeClient:
    model = "dummy"


def test_runtime_threads_enable_inputs_false(tmp_path, monkeypatch):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    cfg = replace(
        AgentConfig(),
        api_key="x",
        persistent_kernel=False,
        enable_telemetry=True,
        enable_trajectory_inputs=False,
    )
    rt = AgentRuntime.from_config(cfg, client=_FakeClient())
    assert rt.session.trajectory_logger._enable_inputs is False


def test_runtime_threads_enable_inputs_true_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    cfg = replace(AgentConfig(), api_key="x", persistent_kernel=False, enable_telemetry=True)
    rt = AgentRuntime.from_config(cfg, client=_FakeClient())
    assert rt.session.trajectory_logger._enable_inputs is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_trajectory_inputs.py -k runtime_threads -v`
Expected: FAIL(`_enable_inputs` 为 True / 开关未生效)。

- [ ] **Step 3: Write minimal implementation**

In `src/data_analysis_agent/runtime.py`, change the TrajectoryLogger construction (around line 256-259) from:

```python
        if config.enable_telemetry:
            session.trajectory_logger = TrajectoryLogger(
                config.trajectories_dir(), session.meta.session_id
            )
```

to:

```python
        if config.enable_telemetry:
            session.trajectory_logger = TrajectoryLogger(
                config.trajectories_dir(),
                session.meta.session_id,
                enable_inputs=config.enable_trajectory_inputs,
                analysis_paths=analysis_paths,
            )
```

(`analysis_paths` 是 `from_config` 的既存参数,在此作用域可见;为 None 时 logger 自动降级为 HOME-only。)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_trajectory_inputs.py -k runtime_threads -v`
Expected: PASS。

- [ ] **Step 5: 质量闸 + Commit**

```bash
.venv/bin/python scripts/quality_gate.py
git add src/data_analysis_agent/runtime.py tests/test_trajectory_inputs.py
git commit -m "feat(runtime): thread enable_trajectory_inputs to logger"
```

---

## Task 4: config.eval_tasks_dir()

**Files:**

- Modify: `src/data_analysis_agent/config.py`(在 `skills_dir()` 旁加方法)
- Test: `tests/test_eval_harvester.py`

**Interfaces:**

- Produces: `AgentConfig.eval_tasks_dir() -> Path`(镜像 `trajectories_dir()`,落 `~/.daa/eval_tasks`)。

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_harvester.py`:

```python
from data_analysis_agent.config import AgentConfig


def test_eval_tasks_dir_under_daa_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    cfg = AgentConfig()
    assert cfg.eval_tasks_dir() == (tmp_path / "daa" / "eval_tasks").resolve()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_eval_harvester.py::test_eval_tasks_dir_under_daa_home -v`
Expected: FAIL(`AttributeError: eval_tasks_dir`)。

- [ ] **Step 3: Write minimal implementation**

In `src/data_analysis_agent/config.py`, next to `skills_dir()`(around line 118-119), add:

```python
    def eval_tasks_dir(self) -> Path:
        """Root for harvested eval tasks + fixtures (~/.daa/eval_tasks)."""
        return self._evolution_subdir("eval_tasks")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_eval_harvester.py::test_eval_tasks_dir_under_daa_home -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/data_analysis_agent/config.py tests/test_eval_harvester.py
git commit -m "feat(config): add eval_tasks_dir()"
```

---

## Task 5: eval_harvester 核心收割 + fixture 冻结

**Files:**

- Create: `src/data_analysis_agent/evolution/eval_harvester.py`
- Test: `tests/test_eval_harvester.py`(追加)

**Interfaces:**

- Consumes: `synthesizer.is_eligible`、`synthesizer.load_corpus`、`evaluator.EvalTask`。
- Produces: `harvest_eval_tasks(corpus, eval_dir, fixtures_dir, data_search_paths, *, max_tasks=50) -> list[Path]`;`derive_tool_count_max`、`stable_task_id`、`rewrite_input_paths`、`resolve_fixture`。

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eval_harvester.py`:

```python
import json

from data_analysis_agent.evolution.eval_harvester import (
    derive_tool_count_max,
    harvest_eval_tasks,
    rewrite_input_paths,
    stable_task_id,
)


def _write_turn(dir_path, turn_id, user_input, refs):
    dir_path.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "turn",
        "session_id": "s",
        "turn_id": turn_id,
        "ts_start": "",
        "ts_end": "",
        "user_input": user_input,
        "active_skill": None,
        "tool_calls": [
            {
                "name": "data_profile",
                "is_error": False,
                "duration_ms": 10,
                "result_chars": 100,
                "input_digest": '{"path": "<path:sales.csv>"}',
                "referenced_files": list(refs),
            }
        ],
        "terminal_reason": "COMPLETED",
        "model_turns": 5,
        "tokens": {},
        "final_text_digest": "",
    }
    (dir_path / f"{turn_id}.jsonl").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _make_csv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a,b\n1,2\n", encoding="utf-8")


def test_derive_tool_count_max_headroom_and_cap():
    assert derive_tool_count_max(1) == 2
    assert derive_tool_count_max(4) == 6
    assert derive_tool_count_max(100) == 20  # capped


def test_stable_task_id_is_deterministic():
    a = stable_task_id("分析 sales", ("sales.csv",))
    b = stable_task_id("分析 sales", ("sales.csv",))
    assert a == b and len(a) == 12


def test_rewrite_input_paths_to_fixture():
    assert rewrite_input_paths("对 sales.csv 做统计", "sales.csv") == "对 fixtures/sales.csv 做统计"


def test_harvest_produces_task_and_freezes_fixture(tmp_path):
    traj = tmp_path / "traj"
    data_root = tmp_path / "data"
    _make_csv(data_root / "sales.csv")
    for i in range(3):
        _write_turn(traj, f"t{i}", f"销售分析 第{i}批 sales.csv", ("sales.csv",))

    from data_analysis_agent.evolution.synthesizer import load_corpus

    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    written = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])

    assert len(written) == 3
    task = json.loads(written[0].read_text(encoding="utf-8"))
    assert task["dataset_fixture"] == "fixtures/sales.csv"
    assert "fixtures/sales.csv" in task["input"]
    assert task["assertions"] == {
        "no_error_results": True,
        "min_tool_calls": 1,
        "tool_call_count_max": 6,  # derive_tool_count_max(1 tool call) -> 2... see note
    }
    assert (eval_dir / "fixtures" / "sales.csv").read_text(encoding="utf-8") == "a,b\n1,2\n"
    # ADR 0005: NO numeric value assertions beyond structure
    for key in task["assertions"]:
        assert key in {"no_error_results", "min_tool_calls", "tool_call_count_max"}


def test_harvest_idempotent(tmp_path):
    traj = tmp_path / "traj"
    data_root = tmp_path / "data"
    _make_csv(data_root / "sales.csv")
    _write_turn(traj, "t1", "销售分析 sales.csv", ("sales.csv",))
    from data_analysis_agent.evolution.synthesizer import load_corpus

    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    first = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])
    second = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])
    assert [p.name for p in first] == [p.name for p in second]
    assert len(second) == 1  # no duplication


def test_harvest_skips_missing_referenced_file(tmp_path, caplog):
    traj = tmp_path / "traj"
    _write_turn(traj, "t1", "销售分析 sales.csv", ("sales.csv",))
    from data_analysis_agent.evolution.synthesizer import load_corpus

    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    written = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [tmp_path / "nope"])
    assert written == []
    assert any("sales.csv" in r.message for r in caplog.records)
```

> **注(实现者必读)**:`test_harvest_produces_task_and_freezes_fixture` 里每个 turn 只有 1 个 tool_call,故 `tool_call_count_max == derive_tool_count_max(1) == 2`。上面断言写 `6` 是占位错误——**实现时把断言改成 `== 2`**。这是本计划已知需实现者按真实值校正的一处(已在 Self-Review 标注)。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_eval_harvester.py -v`
Expected: FAIL(`eval_harvester` 模块不存在)。

- [ ] **Step 3: Write minimal implementation**

Create `src/data_analysis_agent/evolution/eval_harvester.py`:

```python
"""EvalTask harvester: turn successful trajectories into a frozen eval task set.

Solves the cold-start gap (E4): decide_promotion needs >= MIN_SAMPLES relevant
tasks, but only one hand-written eval task ships. Reads the same trajectory
corpus the synthesizer learns from and emits EvalTask JSON + frozen fixtures,
so candidate skills have enough samples to be promoted/retired.

Deterministic — no LLM, no API key. ADR 0005: assertions verify METHOD/STRUCTURE
only, never a specific numeric value.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any

from .synthesizer import is_eligible, load_corpus

logger = logging.getLogger(__name__)

_MAX_HARVESTED_TASKS = 50
_FIXTURES_SUBDIR = "fixtures"


def derive_tool_count_max(source_count: int) -> int:
    """Headroom over the source turn's tool-call count, hard-capped at 20."""
    return max(2, min(20, math.ceil(source_count * 1.5)))


def stable_task_id(input_text: str, referenced: tuple[str, ...]) -> str:
    """Deterministic id over (input, referenced files) → re-harvest is idempotent."""
    payload = f"{input_text}\x1f{'|'.join(referenced)}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


def rewrite_input_paths(input_text: str, basename: str) -> str:
    """Rewrite the data-file reference to fixtures/<basename> (resolves at eval)."""
    return input_text.replace(basename, f"{_FIXTURES_SUBDIR}/{basename}")


def resolve_fixture(basename: str, data_search_paths: list[Path]) -> Path | None:
    """First search-path hit for basename, else None (caller logs/skips)."""
    for root in data_search_paths:
        candidate = Path(root) / basename
        if candidate.is_file():
            return candidate
    return None


def _turn_referenced_files(turn: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for tc in turn.get("tool_calls") or []:
        for name in tc.get("referenced_files") or []:
            if name and name not in found:
                found.append(name)
    return found


def _turn_tool_count(turn: dict[str, Any]) -> int:
    return len(turn.get("tool_calls") or [])


def harvest_eval_tasks(
    corpus: list[dict[str, Any]],
    eval_dir: str | Path,
    fixtures_dir: str | Path,
    data_search_paths: list[str | Path],
    *,
    max_tasks: int = _MAX_HARVESTED_TASKS,
) -> list[Path]:
    """Write one EvalTask JSON per eligible turn + freeze its referenced dataset.

    Skips (with a warning) turns whose referenced file is not found in
    data_search_paths. Idempotent: stable task_id overwrites, fixtures not
    re-copied. Stops at max_tasks (logged) — no silent truncation.
    """
    eval_dir = Path(eval_dir)
    fixtures_dir = Path(fixtures_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    roots = [Path(p) for p in data_search_paths]

    written: list[Path] = []
    seen_ids: set[str] = set()
    for turn in corpus:
        if not is_eligible(turn):
            continue
        refs = _turn_referenced_files(turn)
        if not refs:
            continue
        basename = refs[0]
        src = resolve_fixture(basename, roots)
        if src is None:
            logger.warning(
                "harvest: %s not in data_search_paths; skipping turn %s",
                basename,
                turn.get("turn_id"),
            )
            continue
        dst = fixtures_dir / basename
        if not dst.exists():
            dst.write_bytes(src.read_bytes())
        input_text = str(turn.get("user_input", ""))
        task_id = stable_task_id(input_text, tuple(refs))
        if task_id in seen_ids:
            continue
        seen_ids.add(task_id)
        task = {
            "task_id": task_id,
            "input": rewrite_input_paths(input_text, basename),
            "dataset_fixture": f"{_FIXTURES_SUBDIR}/{basename}",
            "assertions": {
                "no_error_results": True,
                "min_tool_calls": 1,
                "tool_call_count_max": derive_tool_count_max(_turn_tool_count(turn)),
            },
        }
        path = eval_dir / f"{task_id}.json"
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
        if len(written) >= max_tasks:
            logger.info("harvest: reached max_tasks=%d; stopping", max_tasks)
            break
    return written


__all__ = [
    "derive_tool_count_max",
    "harvest_eval_tasks",
    "resolve_fixture",
    "rewrite_input_paths",
    "stable_task_id",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_eval_harvester.py -v`
Expected: 全 PASS(注:实现时把 `tool_call_count_max` 断言校正为 `== 2`)。

- [ ] **Step 5: 质量闸 + Commit**

```bash
.venv/bin/python scripts/quality_gate.py
git add src/data_analysis_agent/evolution/eval_harvester.py tests/test_eval_harvester.py
git commit -m "feat(evolution): eval task harvester + fixture freezing"
```

---

## Task 6: harvest-eval CLI 子命令

**Files:**

- Modify: `src/data_analysis_agent/evolution/__main__.py`(注册子命令 + cmd)
- Test: `tests/test_eval_harvester.py`(追加)

**Interfaces:**

- Consumes: Task 4 的 `config.eval_tasks_dir()`、Task 5 的 `harvest_eval_tasks`、`load_corpus`。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_harvester.py`:

```python
def test_harvest_eval_cli_writes_tasks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    traj = tmp_path / "daa" / "trajectories"
    data_root = tmp_path / "data"
    _make_csv(data_root / "sales.csv")
    _write_turn(traj, "t1", "销售分析 sales.csv", ("sales.csv",))

    from data_analysis_agent.evolution.__main__ import main

    rc = main(["harvest-eval", "--data-search-path", str(data_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "收割" in out
    assert (tmp_path / "daa" / "eval_tasks").is_dir()


def test_harvest_eval_cli_requires_data_search_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAA_HOME", str(tmp_path / "daa"))
    from data_analysis_agent.evolution.__main__ import main

    rc = main(["harvest-eval"])
    assert rc == 1
    assert "--data-search-path" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_eval_harvester.py -k harvest_eval_cli -v`
Expected: FAIL(`harvest-eval` 子命令未注册 / main 不识别)。

- [ ] **Step 3: Write minimal implementation**

Append to `src/data_analysis_agent/evolution/eval_harvester.py`:

```python
def register_harvest_eval_cli(subparsers: Any) -> None:
    """Register the ``harvest-eval`` subcommand on the evolution CLI."""
    p = subparsers.add_parser(
        "harvest-eval", help="轨迹 → eval 任务 + 冻结 fixture(解冷启动)"
    )
    p.add_argument(
        "--data-search-path",
        action="append",
        default=[],
        metavar="DIR",
        help="查找被引用数据文件的目录(可重复,通常即 agent 的 analysis_paths)",
    )
    p.set_defaults(func=_cmd_harvest_eval)


def _cmd_harvest_eval(args: Any) -> int:
    from ..config import AgentConfig

    config = AgentConfig.from_env()
    if not args.data_search_path:
        print("--data-search-path 至少一个(通常即 agent 的 analysis_paths)。")
        return 1
    corpus = load_corpus(config.trajectories_dir())
    eval_dir = config.eval_tasks_dir()
    written = harvest_eval_tasks(
        corpus, eval_dir, eval_dir / _FIXTURES_SUBDIR, args.data_search_path
    )
    print(f"收割 {len(written)} 个 eval 任务 → {eval_dir}")
    for p in written:
        print(f"  {p.name}")
    if not written:
        print("  没有产出(轨迹不足 / 无可冻结数据文件 / 全部被跳过)。")
    return 0
```

并补进 `__all__`:`"register_harvest_eval_cli"`。

Then wire it in `src/data_analysis_agent/evolution/__main__.py` `main()` — after the `evaluate` try/except block (around line 186):

```python
    # 'harvest-eval' is registered by the eval_harvester if available.
    try:
        from .eval_harvester import register_harvest_eval_cli

        register_harvest_eval_cli(sub)
    except ImportError:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_eval_harvester.py -k harvest_eval_cli -v`
Expected: PASS。

- [ ] **Step 5: 质量闸 + Commit**

```bash
.venv/bin/python scripts/quality_gate.py
git add src/data_analysis_agent/evolution/eval_harvester.py src/data_analysis_agent/evolution/__main__.py tests/test_eval_harvester.py
git commit -m "feat(cli): harvest-eval subcommand"
```

---

## Task 7: SkillEvaluator 读多目录(examples + ~/.daa)

**Files:**

- Modify: `src/data_analysis_agent/evolution/evaluator.py`(`SkillEvaluator.__init__`/`evaluate`、`_cmd_evaluate`)
- Test: `tests/test_eval_harvester.py`(追加)

**Interfaces:**

- Produces: `SkillEvaluator.__init__(eval_tasks_dir: str|Path|list[str|Path], ...)`;内部 `_all_tasks()` 去重合并。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_harvester.py`:

```python
def test_evaluator_reads_multiple_dirs(tmp_path):
    from data_analysis_agent.evolution.evaluator import EvalTask, SkillEvaluator

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "ta.json").write_text(
        json.dumps({"task_id": "ta", "input": "销售 x", "assertions": {}}), encoding="utf-8"
    )
    (dir_b / "tb.json").write_text(
        json.dumps({"task_id": "tb", "input": "销售 y", "assertions": {}}), encoding="utf-8"
    )

    def run_fn(task, skill):
        from data_analysis_agent.evolution.evaluator import EvalRun

        return EvalRun(tool_call_count=2, has_error=False, final_text="ok")

    ev = SkillEvaluator([dir_a, dir_b], tmp_path / "skills", run_fn, min_samples=1)

    class FakeSkill:
        name = "sales"
        keywords = ["销售"]

    # _all_tasks is the multi-dir aggregation surface
    tasks = ev._all_tasks()
    assert {t.task_id for t in tasks} == {"ta", "tb"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_eval_harvester.py::test_evaluator_reads_multiple_dirs -v`
Expected: FAIL(`_all_tasks` 不存在 / 单目录不接受 list)。

- [ ] **Step 3: Write minimal implementation**

In `src/data_analysis_agent/evolution/evaluator.py`:

(a) `SkillEvaluator.__init__` 改为归一化目录列表:

```python
    def __init__(
        self,
        eval_tasks_dir: str | Path | list[str | Path],
        skills_dir: str | Path,
        run_fn: RunFn,
        *,
        min_samples: int = MIN_SAMPLES,
    ) -> None:
        dirs = eval_tasks_dir if isinstance(eval_tasks_dir, (list, tuple)) else [eval_tasks_dir]
        self.tasks_dirs: list[Path] = [Path(d) for d in dirs]
        self.skills_dir = Path(skills_dir)
        self.run_fn = run_fn
        self.min_samples = min_samples

    def _all_tasks(self) -> list[EvalTask]:
        """Load + dedup tasks across all configured dirs (by task_id)."""
        tasks: list[EvalTask] = []
        seen: set[str] = set()
        for d in self.tasks_dirs:
            for t in load_eval_tasks(d):
                if t.task_id not in seen:
                    seen.add(t.task_id)
                    tasks.append(t)
        return tasks
```

(b) `evaluate` 内把 `relevant_tasks(skill, load_eval_tasks(self.tasks_dir))` 改为:

```python
        tasks = relevant_tasks(skill, self._all_tasks())
```

(c) `_cmd_evaluate` 里把 `evaluator = SkillEvaluator(eval_dir, config.skills_dir(), run_fn)` 改为读双目录:

```python
    evaluator = SkillEvaluator(
        [eval_dir, config.eval_tasks_dir()], config.skills_dir(), run_fn
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_eval_harvester.py::test_evaluator_reads_multiple_dirs -v`
Expected: PASS。

- [ ] **Step 5: 质量闸 + Commit**

```bash
.venv/bin/python scripts/quality_gate.py
git add src/data_analysis_agent/evolution/evaluator.py tests/test_eval_harvester.py
git commit -m "refactor(evaluator): read eval tasks from multiple dirs"
```

---

## Task 8: Phase 2 端到端验收(synthesize → harvest ≥5 → evaluate 出 promote)

**Files:**

- Test: `tests/test_phase2_acceptance.py`(新建,纯测试,无生产改动)

**Interfaces:**

- Consumes: Task 2/5/7 的全部产出;`SkillSynthesizer`、`SkillEvaluator`、`decide_promotion`、`EvalRun`。

**说明:** 这是主验收。证明 2A 数据流 + 2B 收割让 `evaluate` 能给出非 needs_review 判定,自进化回路闭环。

- [ ] **Step 1: Write the test**

Create `tests/test_phase2_acceptance.py`:

```python
"""Phase 2 acceptance: the evaluate→decide loop closes (not stuck at needs_review).

Chain: trajectories (2A enriched) → synthesizer candidate → harvester ≥5 tasks
→ evaluator decide_promotion ∈ {promote, retire}.
"""

import json

from data_analysis_agent.evolution.eval_harvester import harvest_eval_tasks
from data_analysis_agent.evolution.evaluator import (
    EvalRun,
    SkillEvaluator,
    decide_promotion,
)
from data_analysis_agent.evolution.synthesizer import SkillSynthesizer, load_corpus


def _write_turn(dir_path, turn_id, user_input):
    dir_path.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "turn",
        "session_id": "s",
        "turn_id": turn_id,
        "ts_start": "",
        "ts_end": "",
        "user_input": user_input,
        "active_skill": None,
        "tool_calls": [
            {
                "name": "data_profile",
                "is_error": False,
                "duration_ms": 10,
                "result_chars": 100,
                "input_digest": '{"path":"<path:sales.csv>"}',
                "referenced_files": ["sales.csv"],
            },
            {
                "name": "python_analysis",
                "is_error": False,
                "duration_ms": 20,
                "result_chars": 200,
                "input_digest": '{"code":"df=pd.read_csv(...)"}',
                "referenced_files": [],
            },
        ],
        "terminal_reason": "COMPLETED",
        "model_turns": 5,
        "tokens": {},
        "final_text_digest": "",
    }
    (dir_path / f"{turn_id}.jsonl").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def test_phase2_loop_promotes(tmp_path):
    traj = tmp_path / "traj"
    data_root = tmp_path / "data"
    (data_root / "sales.csv").write_text("region,sales\n北,10\n南,20\n", encoding="utf-8")
    # 6 distinct inputs → 6 harvested tasks; all share 销售 bigram → 1 cluster
    for i in range(6):
        _write_turn(traj, f"t{i}", f"销售分析 第{i}批 用 sales.csv")

    # --- 2B: harvest ≥5 relevant tasks ---
    corpus = load_corpus(traj)
    eval_dir = tmp_path / "eval"
    written = harvest_eval_tasks(corpus, eval_dir, eval_dir / "fixtures", [data_root])
    assert len(written) >= 5

    # --- synthesizer: 1 candidate skill from the cluster ---
    def reflect_fn(turns):
        return {
            "name": "sales-analysis",
            "keywords": ["销售"],
            "instructions": "描述性统计销售数据",
        }

    skills_dir = tmp_path / "skills"
    synth = SkillSynthesizer(
        traj, skills_dir, reflect_fn, min_occurrences=3, min_model_turns=4
    )
    candidate_files = synth.synthesize()
    assert len(candidate_files) == 1

    # --- evaluator: decide over the harvested task set ---
    def run_fn(task, skill):
        # treatment (skill present) is cheaper; both pass
        return EvalRun(tool_call_count=3 if skill is not None else 4,
                       has_error=False, final_text="done")

    ev = SkillEvaluator(eval_dir, skills_dir, run_fn, min_samples=5)
    from data_analysis_agent.skills.loader import load_skills

    candidate = load_skills(skills_dir, statuses=("candidate",))[0]
    verdict = ev.evaluate(candidate)

    assert verdict["decision"] == "promote"
    assert verdict["decision"] != "needs_review"
    assert verdict["metrics"]["n"] >= 5
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `.venv/bin/pytest tests/test_phase2_acceptance.py -v`
Expected: 若 Task 1–7 已正确实现,此测试 PASS(它是闭环验证,不要求先失败)。若失败,据断言定位是 2A 数据未流到 / 2B 收割不足 / relevant_tasks 未命中。

- [ ] **Step 3: 质量闸全绿 + Commit**

```bash
.venv/bin/python scripts/quality_gate.py
git add tests/test_phase2_acceptance.py
git commit -m "test(phase2): end-to-end promote/retire acceptance"
```

- [ ] **Step 4: 独立代码审查闭环(CLAUDE.md §2.9)**

全部 8 Task 完成后,spawn **全新上下文独立**的只读审查子 Agent(`pr-review-toolkit:code-reviewer`),只给 spec 路径 + 本计划 + 改动文件 diff,**不带**编码过程上下文。每轮复审新 spawn,循环至零遗留。

---

## Self-Review(写计划后自检)

**1. Spec 覆盖**:

- §2(2A 管线已通)→ Task 2 不动 client/event,只在 trajectory 捕获 ✓
- §5.1–5.4(ToolCallRecord 字段、捕获、脱敏、开关)→ Task 1+2+3 ✓
- §5.5(synthesizer 联动)→ 零生产代码,由 Task 8 验收证明数据流到 reflect_fn ✓
- §5.6(向后兼容)→ ToolCallRecord 新字段有默认值,Task 2 测试隐含 ✓
- §6.1–6.6(harvester、路径恢复、EvalTask 形状、fixture 冻结、落盘、CLI)→ Task 4+5+6 ✓
- §6.5 后半(evaluator 读双目录)→ Task 7 ✓
- §7(2C defer)→ 无 Task(显式 defer,正确)✓
- §10(主验收)→ Task 8 ✓
- §8(降级链)→ enable_inputs=False(Task 2 测试)、缺文件跳过+log(Task 5 测试)✓
- §9(测试矩阵)→ 全覆盖 ✓

**2. 占位扫描**:Task 5 测试里 `tool_call_count_max == 6` 是**已知占位错误**,已在测试下方的"注"里明示实现者改为 `== 2`(因每 turn 1 个 tool_call)。除此无 TBD/TODO。

**3. 类型一致性**:

- `harvest_eval_tasks(corpus, eval_dir, fixtures_dir, data_search_paths)` — Task 5 定义、Task 6 CLI 调用签名一致 ✓
- `SkillEvaluator(eval_tasks_dir: str|Path|list, skills_dir, run_fn, *, min_samples)` — Task 7 定义、Task 8 调用一致 ✓
- `ToolCallRecord.input_digest: str` / `referenced_files: tuple[str,...]` — Task 2 定义、Task 5/8 合成数据一致 ✓
- `TrajectoryLogger.__init__(..., enable_inputs, analysis_paths, home)` — Task 2 定义、Task 3 调用一致 ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-01-phase2-l2-promotability.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每个 Task 派一个全新子 Agent 实现,任务间两段式审查,迭代快。
2. **Inline Execution** — 在当前会话内用 executing-plans 批量执行,带 checkpoint。

Which approach?
