"""CLI for the offline evolution pipeline.

python -m data_analysis_agent.evolution synthesize   # trajectories -> candidates
python -m data_analysis_agent.evolution list          # show candidate skills
python -m data_analysis_agent.evolution evaluate       # fixture rerun -> promote
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from ..config import AgentConfig
from ..protocol.client import AnthropicApiClient
from ..skills.loader import load_skills
from .synthesizer import SkillSynthesizer

_ALLOWED_TOOLS = "read_file / python_analysis / visualization / html_report / retrieve_result"


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of an LLM reply (tolerates prose/fences)."""
    for candidate in (text, *re.findall(r"\{.*\}", text, re.S)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def llm_reflect(
    client: AnthropicApiClient,
) -> Callable[[list[dict[str, Any]]], dict[str, Any] | None]:
    """Default reflection: ask the model to distill a cluster into a skill record."""

    def reflect(cluster_turns: list[dict[str, Any]]) -> dict[str, Any] | None:
        samples = "\n".join(f"- {t.get('user_input', '')}" for t in cluster_turns[:8])
        tools_seen = sorted(
            {
                str(tc.get("name", ""))
                for t in cluster_turns
                for tc in t.get("tool_calls", [])
                if isinstance(tc, dict)
            }
        )
        prompt = (
            "以下是一组重复出现、当前没有专门技能覆盖的数据分析任务。"
            "请把它们提炼成一个可复用的分析技能,只输出严格 JSON(不要任何额外文字),字段:\n"
            "  name: snake_case 英文标识\n"
            "  description: 中文一句话\n"
            "  keywords: 中英文触发词数组\n"
            f"  allowed_tools: 从 {_ALLOWED_TOOLS} 中选\n"
            "  instructions: 分步骤的中文分析套路(可复用,不含具体数值结论)\n\n"
            f"任务样本:\n{samples}\n\n这些任务用到的工具:{tools_seen}"
        )
        try:
            response = asyncio.run(
                client.call_model(messages=[{"role": "user", "content": prompt}], max_tokens=2000)
            )
        except Exception as e:  # noqa: BLE001 — offline best-effort, report and skip
            print(f"  reflection failed: {e}")
            return None
        return _extract_json(response.get_text())

    return reflect


def cmd_synthesize(args: argparse.Namespace) -> int:
    config = AgentConfig.from_env()
    if not config.api_key:
        print("ANTHROPIC_API_KEY not set; synthesis needs the model.")
        return 1
    client = AnthropicApiClient(api_key=config.api_key, model=config.model)
    synth = SkillSynthesizer(config.trajectories_dir(), config.skills_dir(), llm_reflect(client))
    clusters = synth.find_clusters()
    print(f"找到 {len(clusters)} 个候选任务簇(重复且未被现有技能覆盖)。")
    paths = synth.synthesize()
    for p in paths:
        print(f"  生成候选技能: {p}")
    if not paths:
        print("  没有产出候选技能(轨迹不足或反思未通过)。")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    config = AgentConfig.from_env()
    candidates = load_skills(config.skills_dir(), statuses=("candidate",))
    active = load_skills(config.skills_dir(), statuses=("active",))
    print(f"active 技能: {[s.name for s in active]}")
    print(f"candidate 技能(待评估/人审): {[s.name for s in candidates]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DataAnalysisAgent evolution pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("synthesize", help="轨迹 → 候选技能").set_defaults(func=cmd_synthesize)
    sub.add_parser("list", help="列出 active / candidate 技能").set_defaults(func=cmd_list)

    # 'evaluate' is registered by Stage E (evaluator) if available.
    try:
        from .evaluator import register_evaluate_cli

        register_evaluate_cli(sub)
    except ImportError:
        pass

    args = parser.parse_args()
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
