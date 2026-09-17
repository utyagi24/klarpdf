"""M137 — `requirements-dev.txt` must install on Linux, because that is where CI runs it.

The dev lock is the one requirements file with **two** platforms as customers: the Ubuntu `pytest`
job (`pip install -r requirements-dev.txt`) and the WSL dev venv, plus Windows for the manual
release run. It carries no hashes and no markers precisely so it can be shared — and that sharing
is exactly what a Windows `pip-compile` breaks, silently and without touching a single `.in`.

pip-tools evaluates environment markers against the **compiling** interpreter and writes the
surviving requirements **unmarkered**. Compile on Windows and `mcp`'s `pywin32>=311; sys_platform ==
"win32"` resolves true and lands as a bare `pywin32==312`. pywin32 publishes win32/win_amd64/
win_arm64 wheels and **no sdist at all**, so the lock stops being installable on Linux entirely —
CI cannot even reach the tests to report it. Nothing else in the suite would notice, because every
other check reads the file rather than installing it.

This is the mirror image of the `colorama` note in `requirements-dev.in`: there a marker that was
*false* on the compiling platform silently dropped a line; here one that is *true* silently adds an
uninstallable one. The fix for colorama — declare it unmarkered in the `.in` — cannot work here,
since the package genuinely does not exist off Windows. So the rule is the compile platform itself,
enforced by `invoke lock-dev` refusing to run on Windows and pinned by this test.

**The bridge lock has the same two customers and a worse failure (M144).** `requirements-mcp.txt`
is installed by CI's Linux `bridge` job, and `sync_pins.py` copies every pin in it into the root
`pyproject.toml` — so a bare `pywin32` there would become an unconditional `Requires-Dist` of the
wheel published to PyPI, and `pipx install klarpdf` would fail on Linux and macOS. The platform
checks below therefore run over both locks; the setuptools one is the dev lock's alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEV_LOCK = ROOT / "requirements-dev.txt"
MCP_LOCK = ROOT / "requirements-mcp.txt"

# How to recompile each cross-platform lock correctly — both in WSL, never on Windows.
RECOMPILE = {
    DEV_LOCK: "`invoke lock-dev` (see RELEASE.md §1)",
    MCP_LOCK: "`pip-compile -o requirements-mcp.txt requirements-mcp.in` (see RELEASE.md §2)",
}
CROSS_PLATFORM_LOCKS = pytest.mark.parametrize("lock", list(RECOMPILE), ids=lambda p: p.name)

# Distributions that ship Windows-only wheels and no sdist, so a bare pin makes the lock
# uninstallable off Windows. `pywin32` is the one that actually reached a lock (M137); the rest are
# its neighbours in this dependency graph, listed so the test keeps holding as the graph moves.
WINDOWS_ONLY = ("pywin32", "pypiwin32", "pywin32-ctypes", "winshell", "wmi")


def _pinned_names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        stripped = line.split("#")[0].strip()
        if not stripped or "==" not in stripped:
            continue
        names.add(re.split(r"[=<>\[; ]", stripped, maxsplit=1)[0].strip().lower())
    return names


@CROSS_PLATFORM_LOCKS
def test_the_lock_carries_no_windows_only_package(lock):
    """The failure this prevents is not a red test — it is `pip install` dying before pytest runs."""
    pinned = _pinned_names(lock.read_text(encoding="utf-8"))
    for package in WINDOWS_ONLY:
        assert package not in pinned, (
            f"{package} is pinned in {lock.name}, which Linux installs. It has no Linux wheel, so "
            f"that install now fails outright. This lock was compiled on Windows: recompile it in "
            f"WSL with {RECOMPILE[lock]}."
        )


@CROSS_PLATFORM_LOCKS
def test_the_lock_stays_unhashed_and_unmarkered(lock):
    """Both properties are what let one file serve Linux, macOS and Windows installs alike."""
    text = lock.read_text(encoding="utf-8")
    assert "--hash" not in text, "hashes are per-platform; --require-hashes cannot be shared"
    assert "sys_platform" not in text and "platform_system" not in text


def test_the_dev_lock_keeps_the_setuptools_pin():
    """`--allow-unsafe` is required, not optional: without it pip-tools drops `setuptools` to a bare
    `# setuptools` comment, and `tests/test_mcp_packaging.py` imports it to build metadata offline.
    A plain `pip-compile` here is the other half of the M137 mistake."""
    text = DEV_LOCK.read_text(encoding="utf-8")
    assert "setuptools" in _pinned_names(text), (
        "requirements-dev.txt lost its setuptools pin — recompile with `--allow-unsafe` "
        "(`invoke lock-dev`); see requirements-dev.in and RELEASE.md §1."
    )
