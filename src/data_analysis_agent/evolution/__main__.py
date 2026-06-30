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


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Pull a JSON array of objects out of an LLM reply (tolerates prose/fences).

    A bare object (model returned one item, not an array) is wrapped into a
    one-element list; anything unparsable yields an empty list.
    """
    for candidate in (text, *re.findall(r"\[.*\]", text, re.S)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    obj = _extract_json(text)
    return [obj] if obj is not None else []


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


def llm_extract(
    client: AnthropicApiClient,
) -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
    """Default extractor: distill one turn into domain-memory candidates.

    Records STRUCTURE, not values (ADR 0004): metric *definitions* and analysis
    *preferences*, never a numeric finding from the turn.
    """

    def extract(turn: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = (
            "下面是一轮已完成的数据分析对话。请抽取其中**可复用的领域知识**,"
            "只输出严格 JSON 数组(无任何额外文字);没有可抽取项时输出 []。"
            "每个元素字段:\n"
            '  kind: "metric_definition"(口径/指标如何计算)| "analysis_pref"'
            '(分析或展示偏好)| "open_concern"(数据质量待复核点)\n'
            "  key: 简短名称(如 指标名)\n"
            "  content: 一句话定义/偏好/隐患,**只记口径与结构,不记任何具体数值结论**\n\n"
            f"用户输入:{turn.get('user_input', '')}\n"
            f"回答摘要:{turn.get('final_text_digest', '')}"
        )
        try:
            response = asyncio.run(
                client.call_model(messages=[{"role": "user", "content": prompt}], max_tokens=1500)
            )
        except Exception as e:  # noqa: BLE001 — offline best-effort, report and skip
            print(f"  extract failed: {e}")
            return []
        return _extract_json_array(response.get_text())

    return extract


def cmd_mine_memory(args: argparse.Namespace) -> int:
    config = AgentConfig.from_env()
    if not config.api_key:
        print("ANTHROPIC_API_KEY not set; mining needs the model.")
        return 1
    from ..memory.store import MemoryStore
    from .memory_miner import MemoryMiner

    client = AnthropicApiClient(api_key=config.api_key, model=config.model)
    store = MemoryStore(config.memory_dir())
    miner = MemoryMiner(config.trajectories_dir(), store, llm_extract(client))
    written = miner.mine()
    print(f"挖掘到 {len(written)} 条记忆(metric 写为未确认,待轻确认)。")
    for e in written:
        print(f"  [{e.kind}] {e.key}: {e.content}")
    if not written:
        print("  没有产出记忆(轨迹不足或抽取为空)。")
    return 0


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
    sub.add_parser("mine-memory", help="轨迹 → 领域记忆(口径/偏好/隐患)").set_defaults(
        func=cmd_mine_memory
    )
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
