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
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV_LOCK = ROOT / "requirements-dev.txt"

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


def test_the_dev_lock_carries_no_windows_only_package():
    """The failure this prevents is not a red test — it is `pip install` dying before pytest runs."""
    pinned = _pinned_names(DEV_LOCK.read_text(encoding="utf-8"))
    for package in WINDOWS_ONLY:
        assert package not in pinned, (
            f"{package} is pinned in requirements-dev.txt, which Linux CI and the WSL dev venv "
            f"install. It has no Linux wheel, so that install now fails outright. This lock was "
            f"compiled on Windows: recompile it in WSL with `invoke lock-dev` (see RELEASE.md §1)."
        )


def test_the_dev_lock_stays_unhashed_and_unmarkered():
    """Both properties are what let one file serve Linux CI, WSL and the Windows release run."""
    text = DEV_LOCK.read_text(encoding="utf-8")
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
