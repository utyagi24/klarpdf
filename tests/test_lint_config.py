"""Ruff's configuration agrees with the version it is pinned at (M167).

`required-version` in pyproject.toml makes any other Ruff refuse to run. That is what keeps the
editor, `invoke lint` and the `lint` CI job giving one answer. It is also a second copy of the dev
lock's pin, and a fact stated twice is two facts that agree today (M132). These tests keep them one,
so a Ruff bump that changes only one of them fails here, with the reason, rather than in CI.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ruff_pin(name: str) -> str:
    match = re.search(r"^ruff==(\S+)", (ROOT / name).read_text("utf-8"), re.M)
    assert match, f"{name} has no exact `ruff==` pin"
    return match.group(1)


def test_required_version_is_the_dev_lock_pin():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["ruff"]
    pin = _ruff_pin("requirements-dev.txt")
    assert config["required-version"] == f"=={pin}", (
        f"pyproject.toml requires Ruff {config['required-version']}, but requirements-dev.txt pins "
        f"{pin}. A Ruff bump changes both in one commit (RELEASE.md section 1)."
    )


def test_the_dev_input_pins_ruff_exactly():
    """A floor would let `pip-compile --upgrade` move Ruff, and with it the rules (M167)."""
    assert _ruff_pin("requirements-dev.in") == _ruff_pin("requirements-dev.txt")
