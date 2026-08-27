#!/usr/bin/env python3
"""v2 端到端演示(无 LLM):经与 Pi/dsh 两基座相同的 MCP stdio 通道跑完整任务链。

fixture CSV → 读取 → 画像 → 图表 → 自包含 HTML 报告 → 因果分析/推断 →
超大结果压缩 + 分页召回 → 轨迹记录 → 契约校验 → 离线管线可消费(TurnRecord)。

用法:.venv/bin/python examples/v2/demo_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
BIN = REPO / ".venv" / "bin" / "data-agent-capabilities"
FIXTURE = Path(__file__).resolve().parent / "sales.csv"


def big_table(rows: int = 4000) -> str:
    lines = ["region,product,units,price"]
    for i in range(rows):
        lines.append(f"r{i % 7},p{i % 13},{i},{(i % 11) * 1.5:.2f}")
    return "\n".join(lines)


async def main() -> int:
    from mcp import StdioServerParameters
    from mcp.client.session import ClientSession
    from mcp.client.stdio import stdio_client

    out_dir = Path(tempfile.mkdtemp(prefix="daa-v2-demo-"))
    env = {
        **os.environ,
        "DAA_CAPABILITIES_HOME": str(out_dir / "store"),
        "DAA_CAPABILITIES_ARTIFACTS": str(out_dir / "artifacts"),
        "DAA_CAPABILITIES_ALLOWED_ROOTS": str(REPO),
        "DAA_CAPABILITIES_EVOLUTION_ROOT": str(out_dir / "traj"),
    }
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, note: str = "") -> None:
        checks.append((name, ok, note))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {note}" if note else ""))

    params = StdioServerParameters(command=str(BIN), args=["mcp"], env=env)
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            async def call(name: str, args: dict[str, object]) -> dict[str, object]:
                result = await session.call_tool(name, args)
                text = next(
                    (c.text for c in result.content if getattr(c, "type", "") == "text"), ""
                )
                return json.loads(text)

            env1 = await call(
                "tabular_read_file", {"file_path": str(FIXTURE)}
            )
            check("读表 tabular_read_file", env1["ok"] is True)

            env2 = await call("tabular_data_profile", {"path": str(FIXTURE)})
            check("画像 tabular_data_profile", env2["ok"] is True)

            env3 = await call(
                "reporting_render_chart",
                {
                    "block_id": "demo_region_units",
                    "family": "bar",
                    "title": "各区域销量",
                    "data": {
                        "labels": ["east", "west", "north", "south"],
                        "series": [{"name": "units", "values": [1619, 1328, 1608, 1297]}],
                    },
                    "artifact_dir": str(out_dir / "artifacts"),
                },
            )
            chart_ok = env3["ok"] is True and bool(env3.get("artifacts"))
            check("图表 reporting_render_chart", chart_ok, str(env3.get("artifacts") or env3.get("error")))

            env4 = await call(
                "reporting_render_html",
                {
                    "title": "区域销量分析报告(v2 演示)",
                    "sections": [
                        {
                            "heading": "概览",
                            "body": "基于 examples/v2/sales.csv(30 天数据)的全链路演示。",
                        }
                    ],
                    "artifact_dir": str(out_dir / "artifacts"),
                },
            )
            html_ok = env4["ok"] is True and bool(env4.get("artifacts"))
            html_path = (env4.get("artifacts") or [""])[0]
            if html_ok:
                html_ok = Path(str(html_path)).exists() and Path(str(html_path)).stat().st_size > 1000
            check("HTML 报告 reporting_render_html", html_ok, str(html_path))

            env5 = await call(
                "causal_analyze",
                {
                    "question": "提高折扣是否导致销量上升?",
                    "context": {"columns": ["discount", "units", "region"], "n_rows": 30},
                },
            )
            check("因果分析 causal_analyze(分析子能力)", env5["ok"] is True)

            env6 = await call(
                "causal_estimate",
                {
                    "records": [
                        {"arm": "control", "value": 120.0},
                        {"arm": "control", "value": 125.0},
                        {"arm": "control", "value": 118.0},
                        {"arm": "control", "value": 122.0},
                        {"arm": "treatment", "value": 135.0},
                        {"arm": "treatment", "value": 140.0},
                        {"arm": "treatment", "value": 132.0},
                        {"arm": "treatment", "value": 138.0},
                    ],
                    "group_column": "arm",
                    "outcome_column": "value",
                    "control_group": "control",
                    "treatment_groups": ["treatment"],
                },
            )
            check("因果推断 causal_estimate(推断子能力)", env6["ok"] is True)

            env7 = await call(
                "sampling_compact_result",
                {
                    "content": big_table(),
                    "context_pressure": 0.9,
                    "result_id": "demo-big-1",
                    "tool_name": "demo",
                },
            )
            data7 = env7.get("data") or {}
            check(
                "超大结果压缩 sampling_compact_result",
                env7["ok"] is True and data7.get("was_compacted") is True,
                f"method={data7.get('sampling_method')} fidelity={data7.get('fidelity_level')}",
            )

            env8 = await call(
                "retrieve_result", {"result_id": "demo-big-1", "offset": 0, "limit": 5}
            )
            first_line = str(env8.get("content", "")).splitlines()
            check(
                "分页召回 retrieve_result",
                env8["ok"] is True and len(first_line) > 1 and first_line[1] == "region,product,units,price",
            )

            trajectory_events = [
                {
                    "event": "turn_start",
                    "ts": "2026-08-26T12:00:00Z",
                    "session_id": "demo-v2-e2e",
                    "turn": 1,
                    "harness": "v1",
                    "data": {"user_input_digest": "3b5d2f07a1c9:4096"},
                },
                {
                    "event": "tool_call",
                    "ts": "2026-08-26T12:00:05Z",
                    "session_id": "demo-v2-e2e",
                    "turn": 1,
                    "harness": "v1",
                    "data": {"tool": "tabular_read_file", "input_digest": "aabbccdd0011:128"},
                },
                {
                    "event": "tool_result",
                    "ts": "2026-08-26T12:00:06Z",
                    "session_id": "demo-v2-e2e",
                    "turn": 1,
                    "harness": "v1",
                    "data": {
                        "tool": "tabular_read_file",
                        "ok": True,
                        "output_digest": "111122223333:64",
                        "chars": 64,
                    },
                },
                {
                    "event": "turn_end",
                    "ts": "2026-08-26T12:00:10Z",
                    "session_id": "demo-v2-e2e",
                    "turn": 1,
                    "harness": "v1",
                    "data": {"outcome": "complete"},
                },
            ]
            recorded = [
                (
                    await call("evolution_record_event", event)
                )["ok"]
                is True
                for event in trajectory_events
            ]
            check("轨迹记录 evolution_record_event", all(recorded))

    # 离线侧(进程内):契约校验 + 离线管线消费
    from data_analysis_agent.capabilities.evolution.convert import load_v2_turns
    from data_analysis_agent.capabilities.evolution.store import verify_file

    traj_file = out_dir / "traj" / "demo-v2-e2e.jsonl"
    report = verify_file(traj_file)
    check("轨迹校验 verify_file", report["ok"] is True, f"events={report['events']}")

    turns = load_v2_turns(traj_file)
    check(
        "离线管线消费 load_v2_turns",
        len(turns) == 1 and turns[0].terminal_reason == "COMPLETED",
        f"turns={len(turns)}",
    )

    passed = all(ok for _, ok, _ in checks)
    print(f"\n产物目录: {out_dir}")
    print("V2 E2E DEMO:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
