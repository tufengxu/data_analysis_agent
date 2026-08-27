"""Deterministic drift checks for the quality gate.

Pure functions over the repo tree and docs — no side effects, no LLM. Each
``check_*`` returns a list of human-readable problem strings (empty = OK).
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

# Directories never scanned when resolving doc references.
_IGNORE_DIRS = {
    ".git",
    ".venv",
    ".uv-cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".quality",
    "node_modules",
    "*.egg-info",
}

_MANIFEST_RE = re.compile(r"<!--\s*manifest:start\s*-->(.*?)<!--\s*manifest:end\s*-->", re.DOTALL)
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


def extract_imports(source: str, module_dotted: str, *, is_init: bool = False) -> set[str]:
    """Absolute dotted names imported by source (relative imports resolved).

    For an ``__init__.py`` pass ``is_init=True``: the module dotted name IS the
    package, so relative imports resolve against it rather than its parent.
    Raises ``SyntaxError`` if source does not parse (callers decide how to report).
    """
    tree = ast.parse(source)
    parts = module_dotted.split(".")
    package_parts = parts if is_init else parts[:-1]  # the module's package
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # level 1 = current package; clamp at the package root (no wrap)
                idx = max(0, len(package_parts) - (node.level - 1))
                prefix = package_parts[:idx] + ([node.module] if node.module else [])
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
        try:
            imports = extract_imports(
                path.read_text(encoding="utf-8"), dotted, is_init=path.name == "__init__.py"
            )
        except SyntaxError as exc:
            errors.append(f"syntax-error: {rel}: {exc}")
            continue
        for rule in rules:
            who = str(rule.get("who", ""))
            if not who or not _matches(who, dotted):
                continue
            for target in (str(t) for t in rule.get("forbid", []) or []):
                for imp in imports:
                    if _forbidden(target, imp):
                        errors.append(f"import-rule: {rel} 不得 import {imp} (规则 who={who})")
    return errors


_PATH_TOKEN = re.compile(r"`([^`]+)`|\]\(([^)]+)\)")
# Known repo file extensions — avoids flagging dotted API names (os.path) or
# version strings (v1.2.3) as paths, which would be false dead-links.
_FILE_EXT = re.compile(r"\.(py|md|txt|toml|yaml|yml|json|jsonl|cfg|ini|sh|rst|lock|cff)$")


def find_repo_paths(markdown: str) -> list[str]:
    """Candidate repo paths referenced in markdown (backtick paths + md links)."""
    out: list[str] = []
    for m in _PATH_TOKEN.finditer(markdown):
        token = (m.group(1) or m.group(2) or "").strip()
        if not token or token.startswith(("http://", "https://", "#", "mailto:")):
            continue
        looks_like_path = "/" in token or bool(_FILE_EXT.search(token))
        if not looks_like_path:
            continue
        if any(ch in token for ch in "*?<>|"):  # glob / placeholder, not a real path
            continue
        if " " in token.strip("/"):
            continue
        out.append(token.rstrip("/"))
    return out


def _repo_files(repo_root: Path) -> set[str]:
    """All repo-relative posix file paths, skipping venv/caches/git."""
    files: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for name in filenames:
            files.add((Path(dirpath) / name).relative_to(repo_root).as_posix())
    return files


def check_dead_links(markdown: str, repo_root: Path) -> list[str]:
    """Flag referenced paths that exist nowhere in the repo.

    A token resolves if it exists at the repo root or is a path-suffix of any
    tracked file (so shorthand refs like ``tools/base.py`` resolve to
    ``src/data_analysis_agent/tools/base.py``).
    """
    files = _repo_files(repo_root)
    errors: list[str] = []
    for token in find_repo_paths(markdown):
        canonical = token.lstrip("/")  # treat absolute-looking tokens as repo-relative
        if (repo_root / canonical).exists():
            continue
        if canonical in files or any(f.endswith("/" + canonical) for f in files):
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


# --- v2: harness adapter glue-only checks -------------------------------------
#
# Adapters (harnesses/*) must be protocol glue, never a second capability
# implementation. Heuristics: (1) they may only reach the Python capability
# layer through the `data-agent-capabilities` entrypoint (MCP/CLI), never via
# python -m / inline imports; (2) per-file size caps.

_ADAPTER_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".cjs", ".sh"}
_ADAPTER_IGNORE_DIRS = {"node_modules", "dist", "build", ".pi"}
_ADAPTER_FILE_LOC_LIMIT = 500
_SPAWN_CALL_RE = re.compile(
    r"(?:spawn|spawnSync|execFile|execFileSync|exec|execSync|fork)\s*\(([^;]{0,240})",
    re.DOTALL,
)
_PYTHON_ENTRY_RE = re.compile(r"python[0-9.]*\s+-[cm]\s+data_analysis_agent")


def check_harness_adapters(repo_root: Path) -> list[str]:
    harnesses = repo_root / "harnesses"
    if not harnesses.is_dir():
        return []
    problems: list[str] = []
    for path in sorted(harnesses.rglob("*")):
        if not path.is_file() or path.suffix not in _ADAPTER_SOURCE_SUFFIXES:
            continue
        if any(part in _ADAPTER_IGNORE_DIRS for part in path.parts):
            continue
        if path.name == "check-ts.sh":  # gate tooling itself, not an adapter
            continue
        rel = path.relative_to(repo_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text.splitlines()) > _ADAPTER_FILE_LOC_LIMIT:
            problems.append(
                f"adapter-glue: {rel} 超过 {_ADAPTER_FILE_LOC_LIMIT} 行 — "
                "适配层应只有胶水,业务逻辑须下沉能力层"
            )
        if _PYTHON_ENTRY_RE.search(text):
            problems.append(
                f"adapter-entry: {rel} 直接调 python -m/-c data_analysis_agent — "
                "只准经 data-agent-capabilities 入口"
            )
        if "from data_analysis_agent" in text or "import data_analysis_agent" in text:
            problems.append(f"adapter-entry: {rel} 内联 Python 能力代码/导入 — 禁止第二实现")
        for match in _SPAWN_CALL_RE.finditer(text):
            snippet = match.group(1)
            mentions_python = "python" in snippet.lower() or "data-agent" in snippet
            if mentions_python and "data-agent-capabilities" not in snippet:
                problems.append(
                    f"adapter-entry: {rel} spawn 非 data-agent-capabilities 的 Python 入口: "
                    f"{snippet.strip()[:80]!r}"
                )
    return problems
