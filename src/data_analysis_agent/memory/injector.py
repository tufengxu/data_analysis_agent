"""MemoryInjector: the read + write sides of domain memory, as agent-loop callbacks.

* render(query)  -> text prepended to the system prompt (read side). Touches the
  entries it surfaces, which drives the metric light-confirm loop.
* record_tool(...) -> in-line dataset_profile capture (write side): a successful
  read_file on a tabular path generates/refreshes a profile, deterministically.

Both are plain callables so AgentLoop depends on neither memory nor this class —
same decoupling as approval_handler / artifact_store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..context.compression import estimate_tokens
from .model import DatasetProfile, MemoryEntry
from .profiler import ProfileStore, is_tabular
from .store import MemoryStore

_KIND_LABEL = {
    "metric_definition": "口径定义",
    "analysis_pref": "分析偏好",
    "open_concern": "待复核",
}


def render_profile(profile: DatasetProfile) -> str:
    """One-line-per-column structural digest; flags stale stats."""
    name = Path(profile.path).name
    cols = ", ".join(
        f"{c['name']}({c.get('dtype', '?')})" for c in profile.structure.get("columns", [])
    )
    head = f"- 数据集 {name}(共 {profile.structure.get('n_cols', 0)} 列):{cols}"
    if profile.stale:
        head += "\n  注:统计信息可能已过期(数据文件更新过),如需精确分布请用 python_analysis 重算。"
    return head


class MemoryInjector:
    """Aggregates the profile + textual stores into the two agent-loop callbacks."""

    def __init__(
        self,
        profile_store: ProfileStore,
        memory_store: MemoryStore,
        *,
        budget_tokens: int = 1500,
    ) -> None:
        self.profiles = profile_store
        self.memory = memory_store
        self.budget_tokens = budget_tokens

    # --- read side (memory_injector callback) ---------------------------

    def render(self, query: str) -> str:
        """Build the memory preamble for this query, within the token budget."""
        sections: list[str] = []

        profile_lines = [
            render_profile(p) for p in self.profiles.all() if self._mentions(query, p.path)
        ]
        if profile_lines:
            sections.append("已知数据集画像:\n" + "\n".join(profile_lines))

        hits = self.memory.search(query, top_k=8)
        if hits:
            # Touch first: a surfacing counts as a use, which may cross the
            # confirm threshold — so the wording reflects the post-use state
            # (touch mutates the entry in place; hits hold the same objects).
            for e in hits:
                self.memory.touch(e.kind, e.key)
            unconfirmed = any(e.kind == "metric_definition" and not e.confirmed for e in hits)
            lines = [f"- [{_KIND_LABEL.get(e.kind, e.kind)}] {e.content}" for e in hits]
            header = "相关记忆"
            if unconfirmed:
                header += "(标注「口径定义」者基于历史推断,如不符请指正)"
            sections.append(header + ":\n" + "\n".join(lines))

        text = "\n\n".join(sections)
        if not text:
            return ""
        text = self._truncate(text)
        return "\n\n## 记忆(来自历史会话)\n" + text

    def _truncate(self, text: str) -> str:
        if estimate_tokens(text) <= self.budget_tokens:
            return text
        # Trim from the end, keeping whole lines, until under budget.
        lines = text.split("\n")
        while lines and estimate_tokens("\n".join(lines)) > self.budget_tokens:
            lines.pop()
        return "\n".join(lines) + "\n…(记忆超预算已截断)"

    @staticmethod
    def _mentions(query: str, path: str) -> bool:
        stem = Path(path).name
        return stem in query or Path(path).stem in query

    # --- write side (memory_recorder callback) --------------------------

    def record_tool(
        self, tool_name: str, tool_input: dict[str, Any], metadata: dict[str, Any]
    ) -> None:
        """Capture a dataset_profile when a tabular file is read."""
        if tool_name != "read_file":
            return
        path = tool_input.get("file_path")
        if isinstance(path, str) and is_tabular(path):
            self.profiles.record(path)

    # --- helpers for explicit metric capture ----------------------------

    def remember_metric(self, name: str, definition: str, *, session_id: str = "") -> None:
        """Store a metric definition as unconfirmed (light-confirm pending)."""
        self.memory.put(
            MemoryEntry(
                kind="metric_definition",
                key=name,
                content=definition,
                source_session=session_id,
                confirmed=False,
            )
        )
