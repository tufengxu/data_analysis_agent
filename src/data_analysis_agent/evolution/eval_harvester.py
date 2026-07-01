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
    payload = f"{input_text}\x1f{'|'.join(referenced)}".encode()
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


def register_harvest_eval_cli(subparsers: Any) -> None:
    """Register the ``harvest-eval`` subcommand on the evolution CLI."""
    p = subparsers.add_parser("harvest-eval", help="轨迹 → eval 任务 + 冻结 fixture(解冷启动)")
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


__all__ = [
    "derive_tool_count_max",
    "harvest_eval_tasks",
    "register_harvest_eval_cli",
    "resolve_fixture",
    "rewrite_input_paths",
    "stable_task_id",
]
