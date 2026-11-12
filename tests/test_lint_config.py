"""Ruff's and mypy's configuration agrees with what else states the same fact (M167, M172, M173).

`required-version` in pyproject.toml makes any other Ruff refuse to run. That is what keeps the
editor, `invoke lint` and the `lint` CI job giving one answer. It is also a second copy of the dev
lock's pin, and a fact stated twice is two facts that agree today (M132). These tests keep them one,
so a Ruff bump that changes only one of them fails here, with the reason, rather than in CI.

mypy has no such setting, so its version is stated once, in the dev lock. Its `python_version` is
the second copy of another fact: the oldest Python the wheel supports.
"""

from __future__ import annotations

import re
import tomllib

from tests.test_pypdf_is_dev_only import ROOT, _shipped_modules, needs_git

PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))


def _pin(tool: str, name: str) -> str:
    match = re.search(rf"^{tool}==(\S+)", (ROOT / name).read_text("utf-8"), re.M)
    assert match, f"{name} has no exact `{tool}==` pin"
    return match.group(1)


def test_required_version_is_the_dev_lock_pin():
    config = PYPROJECT["tool"]["ruff"]
    pin = _pin("ruff", "requirements-dev.txt")
    assert config["required-version"] == f"=={pin}", (
        f"pyproject.toml requires Ruff {config['required-version']}, but requirements-dev.txt pins "
        f"{pin}. A Ruff bump changes both in one commit (RELEASE.md section 1)."
    )


def test_the_dev_input_pins_ruff_exactly():
    """A floor would let `pip-compile --upgrade` move Ruff, and with it the rules (M167)."""
    assert _pin("ruff", "requirements-dev.in") == _pin("ruff", "requirements-dev.txt")


def test_the_dev_input_pins_mypy_exactly():
    """A floor would let `pip-compile --upgrade` move mypy, and with it what it reports (M172)."""
    assert _pin("mypy", "requirements-dev.in") == _pin("mypy", "requirements-dev.txt")


def test_mypy_checks_klarpdf_as_the_oldest_python_the_wheel_supports():
    """mypy refuses syntax and library calls newer than `python_version`, whichever Python runs it.

    Set higher than the wheel's floor, it would pass code that fails on the floor. The
    `bridge-pyver` CI job would still catch that, minutes later and only on the lines a test runs.
    """
    floor = re.match(r">=(\d+\.\d+)", PYPROJECT["project"]["requires-python"])
    assert floor, "requires-python no longer starts with a `>=` floor"
    assert PYPROJECT["tool"]["mypy"]["python_version"] == floor.group(1), (
        f"[tool.mypy] checks as Python {PYPROJECT['tool']['mypy']['python_version']}, but the wheel "
        f"supports {floor.group(1)} and up (`requires-python`)."
    )


@needs_git
def test_mypy_checks_every_shipped_file():
    """`[tool.mypy] files` lists the wheel and the app, which together are every shipped file (M173).

    A new top-level module or package of the app would otherwise go unchecked, and say nothing. The
    shipped files are the ones the dead-code check reads: what git tracks, less the suite, the
    tools and the build scripts (`tests/test_pypdf_is_dev_only.py`, `NOT_SHIPPED`).
    """
    listed = PYPROJECT["tool"]["mypy"]["files"]
    shipped = {path.relative_to(ROOT).as_posix() for path in _shipped_modules()}
    covered = {name for name in shipped if any(name == entry or name.startswith(entry + "/") for entry in listed)}
    assert shipped - covered == set(), (
        f"[tool.mypy] files in pyproject.toml leaves out {sorted(shipped - covered)}. Add the module, or its "
        "package, there."
    )
    unshipped = [entry for entry in listed if not any(n == entry or n.startswith(entry + "/") for n in shipped)]
    assert not unshipped, f"[tool.mypy] files names {unshipped}, which hold no shipped file"
