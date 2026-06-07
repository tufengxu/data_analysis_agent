"""Tests for the quality-gate drift-check helpers in scripts/checks.py."""

from __future__ import annotations

import checks


def test_parse_manifest_extracts_entries():
    md = (
        "intro\n"
        "<!-- manifest:start -->\n"
        "```\n"
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
