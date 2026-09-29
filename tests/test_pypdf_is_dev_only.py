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

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_mcp_packaging import _imports, _input

ROOT = Path(__file__).resolve().parent.parent

#: Top-level entries that are not shipped: the suite itself, dev scripts, build scripts, and the
#: vendored-wheel record. Everything else with Python in it ships, in the app or the bridge, so a
#: new package is covered the day it is added to git rather than when someone remembers to list it.
NOT_SHIPPED = {"tests", "tools", "packaging", "vendor", "tasks.py"}

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git to list tracked files")


def _shipped_modules() -> list[Path]:
    """The ``.py`` files git tracks, less the top-level entries in ``NOT_SHIPPED``.

    It asks git rather than walking the checkout, because a checkout also holds what builds leave
    behind, in folders git ignores. The owner's ``build/`` and ``dist/`` held copies of the old
    ``edit_engine.py``, and on Windows ``build/venv`` had pypdf itself installed. A walk read them
    all, so it would have failed on both of the owner's machines. CI passed, because it starts from
    a clean checkout.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    # A tracked file deleted from the working tree is still listed until the deletion is staged.
    return [
        ROOT / name
        for name in sorted(set(filter(None, listed.split("\0"))))
        if name.split("/")[0] not in NOT_SHIPPED
        and not name.startswith(".")
        and (ROOT / name).is_file()
    ]


@needs_git
def test_the_scan_sees_the_shipped_code():
    """A scan that finds nothing looks exactly like a clean one, so check it found the modules
    that matter: both surfaces, and the save path that used to import pypdf."""
    found = {p.relative_to(ROOT).as_posix() for p in _shipped_modules()}
    assert {"main_window.py", "klarpdf/model/edit_engine.py", "klarpdf/mcp_bridge/server.py"} <= found


@needs_git
def test_no_shipped_module_imports_pypdf():
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in _shipped_modules()
        for module, line in _imports(path)
        if module.split(".")[0] == "pypdf"
    ]
    assert offenders == [], (
        f"shipped code imports pypdf: {offenders}. pypdf is a dev-only test reader (M156); the "
        "installer and the bridge do not carry it, so this import would pass CI and fail for users."
    )


def test_pypdf_is_declared_for_the_tests_and_nowhere_that_ships():
    """Both shipped inputs take PyMuPDF from ``requirements-core.in`` through a ``-r`` line, so this
    follows those lines. A check that stopped at the top of each file would pass with pypdf in the
    file they share."""
    assert "pypdf" in _input("requirements-dev.in")
    assert "pypdf" not in _input("requirements.in")
    assert "pypdf" not in _input("requirements-mcp.in")


def test_the_ship_lock_does_not_pin_pypdf():
    """The inputs above decide what a recompile writes, but the build installs from the committed
    lock, and that is compiled by hand on Windows. It kept ``pypdf==6.17.0`` until M156's Windows
    half recompiled it, so while it did, the build venv still installed pypdf."""
    pinned = {
        line.split("==")[0].strip().lower()
        for line in (ROOT / "requirements-win.txt").read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.lstrip().startswith("#")
    }
    assert "pymupdf" in pinned  # the parse found the lock's pins at all
    assert "pypdf" not in pinned
