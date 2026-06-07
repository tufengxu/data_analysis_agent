"""Deterministic drift checks for the quality gate.

Pure functions over the repo tree and docs — no side effects, no LLM. Each
``check_*`` returns a list of human-readable problem strings (empty = OK).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

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
                        errors.append(f"import-rule: {rel} 不得 import {imp} (规则 who={who})")
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
