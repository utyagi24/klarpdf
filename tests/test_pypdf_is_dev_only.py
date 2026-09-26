"""pypdf is a test dependency, not a shipped one (PLAN.md §M156).

From M1 to M156 the core carried ``PyPdfEngine``, a pypdf fallback planned as the escape from
PyMuPDF's AGPL. Nothing outside the tests ever constructed it, yet its lazy ``import pypdf`` put the
library in the Windows installer, and four security bumps treated it as a live attack surface.
M156 removed the engine and moved pypdf to ``requirements-dev.in``, where it stays as an independent
second reader that checks what PyMuPDF wrote.

The trap this guards: CI installs ``requirements-dev.txt``, which *has* pypdf, so a new
``import pypdf`` in shipped code passes the whole suite and fails only in the installed app or
bridge. So the check reads the source rather than running it. Every import in a module counts,
including one inside a function body, which is exactly where the old engine hid its import.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Top-level entries that are not shipped: the suite itself, dev scripts, build scripts, and the
#: vendored-wheel record. Everything else with Python in it ships, in the app or the bridge, so a
#: new package is covered the day it is added rather than when someone remembers to list it.
NOT_SHIPPED = {"tests", "tools", "packaging", "vendor", "tasks.py"}


def _requirements(name: str) -> set[str]:
    """The package names a ``*.in`` file requires — code, not comments."""
    names = set()
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        spec = line.split("#")[0].strip()
        if spec and not spec.startswith("-"):
            names.add(re.match(r"[A-Za-z0-9_.-]+", spec).group(0).lower())
    return names


def _shipped_modules() -> list[Path]:
    files = []
    for entry in sorted(ROOT.iterdir()):
        if entry.name in NOT_SHIPPED or entry.name.startswith("."):
            continue
        if entry.is_file() and entry.suffix == ".py":
            files.append(entry)
        elif entry.is_dir() and not (entry / "pyvenv.cfg").exists():
            files.extend(p for p in sorted(entry.rglob("*.py")) if "__pycache__" not in p.parts)
    return files


def _imports_pypdf(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        if any(n == "pypdf" or n.startswith("pypdf.") for n in names):
            lines.append(node.lineno)
    return lines


def test_the_scan_sees_the_shipped_code():
    """A scan that finds nothing looks exactly like a clean one, so check it found the modules
    that matter: both surfaces, and the save path that used to import pypdf."""
    found = {p.relative_to(ROOT).as_posix() for p in _shipped_modules()}
    assert {"main_window.py", "klarpdf/model/edit_engine.py", "klarpdf/mcp_bridge/server.py"} <= found


def test_no_shipped_module_imports_pypdf():
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in _shipped_modules()
        for line in _imports_pypdf(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    ]
    assert offenders == [], (
        f"shipped code imports pypdf: {offenders}. pypdf is a dev-only test reader (M156); the "
        "installer and the bridge do not carry it, so this import would pass CI and fail for users."
    )


def test_pypdf_is_declared_for_the_tests_and_nowhere_that_ships():
    assert "pypdf" in _requirements("requirements-dev.in")
    assert "pypdf" not in _requirements("requirements.in")
    assert "pypdf" not in _requirements("requirements-mcp.in")
