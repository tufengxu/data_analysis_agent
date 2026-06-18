"""Persistent analysis kernel: line-protocol REPL (sandbox side).

Self-contained — must not import data_analysis_agent (it runs as a standalone
script with PYTHONPATH=""). kernel.manager composes the boot script as
``sandbox_summary source + this file`` and spawns ``python <script> <cfg>``;
``summarize_dataframe`` therefore appears via globals() at runtime, and the
sampling parameters arrive as a JSON argv payload.

Protocol (one JSON object per line):
    stdin  -> {"id": str, "code": str}
    stdout <- {"id": str, "ok": bool, "stdout": str, "stderr": str,
               "error": str | null, "outputs": list}
"""

from __future__ import annotations

import builtins
import io
import json
import sys
import traceback
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

# The kernel's code executor, bound once. Static-analysis gates (the harness
# AST check) run on the *incoming* code before it ever reaches this point.
_EXECUTE = builtins.exec

# Defensive cap so one runaway print cannot wedge the response pipe.
_MAX_FIELD_CHARS = 2_000_000
# Whole-response byte ceiling, kept well under the manager's 16MB readline
# limit (CJK text can be ~3-4 bytes/char after encoding).
_MAX_RESPONSE_BYTES = 8_000_000


def _serialize_response(response: dict[str, Any]) -> str:
    """Serialize, shedding payload progressively to stay under the pipe limit."""
    serialized = json.dumps(response, ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) <= _MAX_RESPONSE_BYTES:
        return serialized
    response = dict(response)
    response["outputs"] = [
        {"type": "note", "note": "outputs dropped: response exceeded the pipe size limit"}
    ]
    serialized = json.dumps(response, ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) <= _MAX_RESPONSE_BYTES:
        return serialized
    response["stdout"] = _clip(str(response.get("stdout", "")))[:500_000]
    response["stderr"] = _clip(str(response.get("stderr", "")))[:500_000]
    if response.get("error") is not None:
        response["error"] = _clip(str(response["error"]))[:500_000]
    serialized = json.dumps(response, ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) <= _MAX_RESPONSE_BYTES:
        return serialized
    # Hard floor: whatever slipped through, the reply must fit the pipe.
    return json.dumps(
        {
            "id": response.get("id", ""),
            "ok": False,
            "stdout": "",
            "stderr": "",
            "error": "response exceeded the pipe size limit and was dropped",
            "outputs": [],
        },
        ensure_ascii=False,
    )


def _clip(text: str) -> str:
    if len(text) <= _MAX_FIELD_CHARS:
        return text
    return text[:_MAX_FIELD_CHARS] + f"\n... [truncated from {len(text)} chars]"


def _sampling_params() -> dict[str, Any]:
    if len(sys.argv) > 1:
        try:
            parsed = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _auto_summarize(
    namespace: dict[str, Any],
    params: dict[str, Any],
    agent_summarize: Callable[[Any], Any],
) -> None:
    """Summarize an oversized ``result`` DataFrame/Series after user code runs."""
    trigger_rows = params.get("trigger_rows")
    if not isinstance(trigger_rows, int):
        return
    result_obj = namespace.get("result")
    if result_obj is None:
        return
    try:
        import pandas as pd
    except Exception:
        return  # pandas optional: degrade to no-op
    try:
        if isinstance(result_obj, pd.DataFrame | pd.Series) and (
            int(result_obj.shape[0]) > trigger_rows
        ):
            agent_summarize(result_obj)
    except Exception as exc:
        # stderr is the kernel's only observability channel (manager redirects
        # it to a log + crash tail); don't fail silently like a no-op.
        print(f"[auto_summarize] skipped: {exc}", file=sys.stderr)


def _run_request(code: str, namespace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Execute one code block in the persistent namespace."""
    outputs: list[Any] = []

    def agent_result(outs: Any) -> None:
        if isinstance(outs, list):
            outputs.extend(outs)

    def agent_summarize(obj: Any) -> Any:
        summarizer = globals().get("summarize_dataframe")
        if summarizer is None:
            print("[agent_summarize] skipped: summarizer unavailable")
            return None
        kwargs = {k: v for k, v in params.items() if k != "trigger_rows"}
        if isinstance(kwargs.get("quantiles"), list):
            kwargs["quantiles"] = tuple(kwargs["quantiles"])
        try:
            summary = summarizer(obj, **kwargs)
            outputs.append({"type": "table_summary", "summary": summary})
            print(
                f"[agent_summarize] summarized {summary['n_rows']} rows x {summary['n_cols']} cols"
            )
            return summary
        except Exception as exc:
            print(f"[agent_summarize] skipped: {exc}")
            return None

    namespace["agent_result"] = agent_result
    namespace["agent_summarize"] = agent_summarize

    out_buf, err_buf = io.StringIO(), io.StringIO()
    error: str | None = None
    try:
        code_obj = compile(code, "<agent>", "exec")
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            _EXECUTE(code_obj, namespace)
            _auto_summarize(namespace, params, agent_summarize)
    except (Exception, SystemExit, KeyboardInterrupt):
        # The kernel must outlive user-code failures, including sys.exit().
        # Clipped: a giant exception message (e.g. str(big_df) in the repr)
        # must not blow the response pipe and masquerade as a kernel crash.
        error = _clip(traceback.format_exc())

    return {
        "ok": error is None,
        "stdout": _clip(out_buf.getvalue()),
        "stderr": _clip(err_buf.getvalue()),
        "error": error,
        "outputs": outputs,
    }


def main() -> None:
    params = _sampling_params()
    namespace: dict[str, Any] = {"__name__": "__main__"}
    real_stdout = sys.stdout
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = request.get("code")
        if isinstance(code, str):
            response = _run_request(code, namespace, params)
        else:
            response = {
                "ok": False,
                "stdout": "",
                "stderr": "",
                "error": "request missing 'code'",
                "outputs": [],
            }
        response["id"] = request.get("id", "")
        real_stdout.write(_serialize_response(response) + "\n")
        real_stdout.flush()


if __name__ == "__main__":
    main()
