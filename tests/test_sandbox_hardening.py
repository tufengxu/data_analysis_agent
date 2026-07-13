"""Sandbox hardening tests for PythonAnalysisTool.

Covers the escape routes closed in the AST deny-list expansion (ADR 0008) and
the stateless-subprocess resource caps. These assert the *verified* bypasses
(``getattr``/``open``-alias/``builtins``/``ctypes``/string-concat/``setattr``)
are now rejected at validation time, and that a runaway disk write in the
stateless path is bounded by RLIMIT_FSIZE.

Threat-model reminder (see ADR 0008): the sandbox is best-effort containment of
model-generated code for a single-user local CLI, NOT a security boundary. These
tests pin the cheap, known escape routes; they cannot prove completeness.
"""

from __future__ import annotations

import asyncio

import pytest

from data_analysis_agent.tools import python_exec as pe
from data_analysis_agent.tools.python_exec import PythonAnalysisTool

# --- AST rejections: the verified escape routes -----------------------------


@pytest.mark.parametrize(
    "code",
    [
        # Canonical string-concat bypass: getattr + builtins + split eval name.
        'getattr(__builtins__, "ev"+"al")("1+1")',
        # Direct getattr on any object.
        "getattr(obj, 'columns')",
        # Alias-then-call: defeats a call-only check.
        "g = getattr\ng(obj, 'x')",
        # setattr reaches the dunder layer without an attribute access the
        # dunder check can see (e.g. forge a __class__).
        'setattr(x, "__class__", y)',
        'delattr(x, "a")',
        # open ALIAS: ``f = open; f('/etc/passwd')`` would skip the path
        # whitelist that only fires on a direct open(...) call.
        "f = open\nf('x')",
        "funcs = [open]",
        # ATTRIBUTE-form alias of file APIs: ``f = io.open`` or
        # ``Path(x).read_bytes`` skip the Call-branch check on the alias.
        "import io\nf = io.open",
        "from pathlib import Path\nPath('x').read_bytes",
        # builtins module import (gives direct handle to eval/exec/import).
        "import builtins",
        "import importlib",
        "import ctypes",
        "import multiprocessing",
        "import pickle",
        "import marshal",
        # operator.methodcaller/attrgetter reach methods/attrs by STRING,
        # bypassing every ast.Attribute check (methodcaller('unlink')(p)).
        "import operator",
        "from operator import methodcaller",
        # inspect.currentframe().f_builtins is a full arbitrary-code escape
        # (live builtins dict → __import__ → os → system). inspect itself blocked.
        "import inspect",
        # Process-spawn / REPL-host modules (cheap defense-in-depth).
        "import pty",
        "import runpy",
        # Direct reference to the builtins dict object.
        "b = __builtins__",
        # Globals/locals reach arbitrary in-scope names.
        "globals()",
        "locals()",
        "vars()",
        # Preamble-leak ACE (BLOCKER): _wrap_code does `import sys`, leaking the
        # name; `sys.modules['os'].system(...)` once validated AND executed.
        'sys.modules["os"].system("echo BYPASS")',
        "x = sys",
        # Alias-then-call of the builtin eval/import family (no paren → slips the
        # Layer-1 substring; caught only at the AST Name check).
        "e = eval\ne('1+1')",
        "imp = __import__\nimp('os')",
    ],
)
def test_ast_rejects_known_escape_routes(code: str) -> None:
    tool = PythonAnalysisTool()
    result = tool.validate_input({"code": code})
    assert not result.valid, f"expected rejection for: {code!r}"


def test_ast_rejects_dunder_attribute_chain() -> None:
    """The classic subclass-chain escape must stay blocked."""
    tool = PythonAnalysisTool()
    code = "[c for c in ().__class__.__bases__[0].__subclasses__()]"
    assert not tool.validate_input({"code": code}).valid


def test_ast_rejects_frame_introspection_ace() -> None:
    """Round-4 regression: ``inspect.currentframe().f_builtins`` yields the live
    builtins dict → full arbitrary-code execution. The payload below once
    validated AND executed; it must now be rejected at the import."""
    tool = PythonAnalysisTool()
    payload = (
        'import inspect\ninspect.currentframe().f_builtins["__import__"]("os").system("echo PWNED")'
    )
    assert not tool.validate_input({"code": payload}).valid


def test_ast_rejects_frame_attribute_sink() -> None:
    """Round-5 regression: the frame-attribute sink (f_builtins / gi_frame / …)
    is blocked at the Attribute regardless of which module reaches the frame.
    ``gen.gi_frame.f_builtins[...]`` once validated AND executed; reject it."""
    tool = PythonAnalysisTool()
    payload = (
        "def g():\n    yield 1\ngen = g()\n"
        'gen.gi_frame.f_builtins["__import__"]("os").system("echo X")'
    )
    assert not tool.validate_input({"code": payload}).valid
    # The sink names themselves are blocked even in isolation.
    assert not tool.validate_input({"code": "x = obj.f_builtins"}).valid
    assert not tool.validate_input({"code": "x = gen.gi_frame"}).valid


def test_path_zero_arg_does_not_crash() -> None:
    """Regression: ``Path()`` and ``Path().resolve()`` (idiomatic cwd lookup)
    once crashed the validator with IndexError. They must validate cleanly."""
    tool = PythonAnalysisTool()
    # Path() is harmless (defaults to cwd); accept it. Either verdict is fine
    # — the property under test is "does not raise".
    assert tool.validate_input({"code": "from pathlib import Path\nPath()"}).valid
    assert tool.validate_input({"code": "from pathlib import Path\nPath().resolve()"}).valid


@pytest.mark.parametrize(
    "code",
    [
        "from pathlib import Path\nPath()",
        "from pathlib import Path\nPath().resolve()",
        "Path(*[])",
        "",
        "   ",
        "import pandas as pd\ndf = pd.read_csv()",
        "open()",
        "((((((((x))))))))",
        "a.b.c.d.e.f.g",
        "@dec\nclass C: pass",
        "lambda: lambda: lambda: 0",
        "f'{x for x in (1,)}'",
    ],
)
def test_validator_never_raises_on_parseable_input(code: str) -> None:
    """The validator must never raise — a validator crash propagates out of the
    agent loop. Any parseable (or empty) input yields a ValidationResult."""
    tool = PythonAnalysisTool()
    result = tool.validate_input({"code": code})
    # No exception is the assertion; .valid is whatever the policy says.
    assert isinstance(result.valid, bool)


# --- Legit code still passes (no over-blocking regression) ------------------


@pytest.mark.parametrize(
    "code",
    [
        "print(1)",
        "x = [i * 2 for i in range(10)]\nprint(sum(x))",
        # Attribute access (NOT a dunder, NOT a dangerous path method) is fine.
        "import pandas as pd\nprint(df.shape)",
        "import pandas as pd\nprint(df.describe())",
        # Relative literal path read is allowed (existing policy).
        'import pandas as pd\ndf = pd.read_csv("data.csv")',
        # Pre-installed scientific libs all import cleanly.
        "import numpy as np\nprint(np.array([1, 2, 3]).mean())",
        "import matplotlib.pyplot as plt",
        "import seaborn as sns",
        # Direct open() with a relative literal path is allowed (the alias form
        # ``f = open`` is what's blocked, above).
        "open('out.txt').read()",
    ],
)
def test_legit_analysis_code_still_allowed(code: str) -> None:
    tool = PythonAnalysisTool()
    assert tool.validate_input({"code": code}).valid, f"unexpected rejection: {code!r}"


def test_computed_path_read_is_known_residual() -> None:
    """Pins the DOCUMENTED residual (ADR 0008): a non-literal path to a pandas
    reader skips the allowed_paths whitelist because the AST cannot see the
    runtime value. Accepted under the local-CLI threat model. If this test
    starts FAILING (the route got closed), ADR 0008 and this test must move
    together — don't silently flip the assertion.
    """
    tool = PythonAnalysisTool()
    code = "import pandas as pd\npath = 'x.csv'\ndf = pd.read_csv(path)"
    assert tool.validate_input({"code": code}).valid, (
        "computed-path read was apparently closed — update ADR 0008's residual "
        "section and this test together"
    )


@pytest.mark.parametrize(
    "code",
    [
        # stdlib "read-file-by-path" open class (ADR 0008 residual).
        "import linecache",
        "import tarfile",
        "import configparser",
        # reflection / introspection open class (ADR 0008 residual); the frame
        # sinks are blocked, so these don't reach ACE today, but the modules
        # themselves still validate.
        "import types",
        "import gc",
        "import functools",
    ],
)
def test_known_residuals_validate_today(code: str) -> None:
    """Pin ADR 0008's documented OPEN-CLASS residuals: these still validate
    (accepted under the local-CLI threat model — blacklist cannot be
    exhaustive). If one starts being REJECTED, the documented residual class
    shrank and ADR 0008 + this test must move together — do not silently flip.
    """
    tool = PythonAnalysisTool()
    result = tool.validate_input({"code": code})
    assert result.valid, (
        f"{code!r} was apparently closed — ADR 0008's residual class shrank; "
        "update the ADR's residual section and this test together"
    )


def test_relative_traversal_is_known_residual() -> None:
    """ADR 0008 residual: a literal relative ``../`` path slips the
    allowed_paths whitelist (only absolute paths are checked). Accepted because
    the sandbox cwd is an isolated tmpdir deep under $TMPDIR. If this is ever
    closed, update ADR 0008's residual section together with this test.
    """
    tool = PythonAnalysisTool()
    code = 'import pandas as pd\npd.read_csv("../../etc/passwd")'
    assert tool.validate_input({"code": code}).valid, (
        "relative `../` traversal was apparently closed — update ADR 0008's "
        "residual section and this test together"
    )


# --- Stateless resource cap: RLIMIT_FSIZE bounds a runaway disk write --------


def test_stateless_disk_fill_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A write exceeding RLIMIT_FSIZE fails instead of filling the disk.

    The cap is lowered to 1 MB for the test so we don't actually write 4 GB+
    (the production default). The child reads the module global at fork time,
    so monkeypatching before the call is sufficient.
    """
    monkeypatch.setattr(pe, "_RLIMIT_FSIZE_BYTES", 1 * 1024 * 1024)
    tool = PythonAnalysisTool(kernel=None)  # force the stateless (fallback) path

    code = (
        "with open('big.bin', 'wb') as f:\n"
        "    f.write(b'x' * (5 * 1024 * 1024))\n"
        "print('wrote-all')"
    )
    result = asyncio.run(tool.call({"code": code, "timeout": 30}))

    # Strong assertion: the 5 MB write exceeds the 1 MB cap, so the success
    # marker must be ABSENT from stdout (the write raised EFBIG partway).
    assert "wrote-all" not in result.content, (
        f"RLIMIT_FSIZE did not bound the write — result: {result.content!r}"
    )


def test_stateless_normal_write_within_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A small, legitimate write succeeds under the same cap (no false positive)."""
    monkeypatch.setattr(pe, "_RLIMIT_FSIZE_BYTES", 1 * 1024 * 1024)
    tool = PythonAnalysisTool(kernel=None)

    code = "with open('small.txt', 'w') as f:\n    f.write('hello')\nprint('wrote-small')"
    result = asyncio.run(tool.call({"code": code, "timeout": 30}))
    assert not result.is_error
    assert "wrote-small" in result.content
