"""M42 — the packaging invariants, which are exactly the things that rot silently.

None of this is exercised by using the app or the bridge, so nothing notices when it drifts: a tool
added without a manifest entry, a lock recompiled without regenerating the bundle's pyproject, an
audit step that quietly stops covering a lock, a hashed line creeping into a lock that has to stay
cross-platform. Each of those is one commit away and none of them fails a normal test run — so they
are asserted here instead.

Deliberately no network and no `npx`: these read committed files. Actually building a `.mcpb`
needs the Node CLI and is a release step, not a test.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-mcp.txt"
MANIFEST = ROOT / "packaging" / "mcpb" / "manifest.json"
BUNDLE_PYPROJECT = ROOT / "packaging" / "mcpb" / "pyproject.toml"
PROJECT_PYPROJECT = ROOT / "pyproject.toml"
AUDIT_WORKFLOW = ROOT / ".github" / "workflows" / "audit.yml"
AUDIT_SCRIPT = ROOT / "tools" / "audit-deps.ps1"
MCP_JSON = ROOT / ".mcp.json"


def _build_module():
    """Load `packaging/mcpb/build_mcpb.py` **by path**, not by import.

    `import packaging.mcpb.build_mcpb` does not work, and the reason is worth knowing because it is
    the same trap that renamed `mcp/` to `mcp_bridge/`: `packaging` is also a real PyPI package
    (pytest depends on it), and a regular installed package beats this repo's `__init__.py`-less
    directory of the same name. Here it is harmless — nothing imports `packaging/` at runtime, it
    holds build inputs — so the fix is to load the file directly rather than to rename anything.
    """
    import importlib.util

    path = ROOT / "packaging" / "mcpb" / "build_mcpb.py"
    spec = importlib.util.spec_from_file_location("_klarpdf_build_mcpb", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_mcpb():
    return _build_module()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tool_names() -> set[str]:
    from mcp_bridge.server import server

    return {tool.name for tool in asyncio.run(server.list_tools())}


# ---- the lock -------------------------------------------------------------------


def test_the_mcp_lock_is_cross_platform_and_unhashed():
    """The constraint PLAN.md flags as expensive to discover late: a hashed or platform-marked lock
    makes the bridge accidentally Windows-only, which defeats its entire audience."""
    text = LOCK.read_text(encoding="utf-8")
    assert "--hash" not in text, "a hashed lock is per-platform; --require-hashes fails elsewhere"
    assert "sys_platform" not in text and "platform_system" not in text
    assert "win32" not in text


def test_every_line_in_the_mcp_lock_is_an_exact_pin():
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "==" in stripped, f"not an exact pin: {stripped!r}"


def test_the_gui_ship_lock_does_not_carry_the_mcp_sdk():
    """The headline promise of "separate optional component": the installer's audit surface, size
    and offline lock are untouched by any of this."""
    ship = (ROOT / "requirements-win.txt").read_text(encoding="utf-8").lower()
    for package in ("mcp==", "mcp-types", "starlette", "uvicorn", "pydantic", "cryptography"):
        assert package not in ship, f"{package} leaked into the shipped Windows lock"


def test_requirements_in_does_not_carry_the_mcp_sdk():
    assert "mcp" not in (ROOT / "requirements.in").read_text(encoding="utf-8").lower().split()


# ---- the audit steps ------------------------------------------------------------


def test_ci_audits_the_mcp_lock():
    assert "requirements-mcp.txt" in AUDIT_WORKFLOW.read_text(encoding="utf-8")


def test_the_local_audit_twin_covers_the_same_locks():
    """`tools/audit-deps.ps1` is documented as the offline twin of the CI job. A twin that audits a
    different set of locks is worse than no twin, because it reports clean over a gap."""
    ci = AUDIT_WORKFLOW.read_text(encoding="utf-8")
    script = AUDIT_SCRIPT.read_text(encoding="utf-8")
    locks = set(re.findall(r"requirements[\w-]*\.txt", ci))
    for lock in locks:
        assert lock in script, f"{lock} is audited in CI but not by tools/audit-deps.ps1"


# ---- the entry point --------------------------------------------------------------


def test_the_console_script_points_at_something_that_exists():
    text = PROJECT_PYPROJECT.read_text(encoding="utf-8")
    assert 'klarpdf-mcp = "mcp_bridge.server:main"' in text

    from mcp_bridge.server import main

    assert callable(main)


def test_an_install_carries_the_core_but_not_the_gui():
    """The quarantined seam as a package boundary — `pip install` for the bridge pulls no PySide6."""
    text = PROJECT_PYPROJECT.read_text(encoding="utf-8")
    packages = re.search(r"packages\s*=\s*\[([^\]]+)\]", text).group(1)
    assert "mcp_bridge" in packages and "model" in packages and "util" in packages
    for gui in ("viewer", "organize", "ui", "store"):
        assert f'"{gui}"' not in packages, f"{gui} would drag Qt into a bridge install"


# ---- the .mcpb bundle ---------------------------------------------------------------


def test_the_manifest_validates_against_the_schema_we_can_check_offline(manifest):
    """`mcpb validate` needs Node, so the parts that can be checked here are checked here."""
    assert manifest["manifest_version"] in {"0.1", "0.2", "0.3"}
    assert manifest["name"] == "klarpdf"
    assert manifest["server"]["type"] in {"python", "node", "binary"}


def test_the_bundle_launches_through_uv(manifest):
    """PLAN.md specified `server.type = "uv"`; that type does not exist in the MCPB schema (see
    packaging/mcpb/build_mcpb.py). The behaviour survives because the *command* is uv — if that
    ever changes back to a bare interpreter, the bundle silently starts needing vendored deps it is
    forbidden from shipping."""
    config = manifest["server"]["mcp_config"]
    assert config["command"] == "uv"
    assert "--directory" in config["args"]


def test_the_manifest_lists_every_tool_the_server_registers(manifest, tool_names):
    """The listing Desktop shows a user before they install. A tool added without a manifest entry
    is invisible there — and this is the only thing that would notice."""
    listed = {tool["name"] for tool in manifest["tools"]}
    assert listed == tool_names, f"manifest/server drift: {listed ^ tool_names}"


def test_the_manifest_version_tracks_the_app_version(manifest):
    from version import __version__

    assert manifest["version"] == __version__


def test_the_bundle_pyproject_is_in_step_with_the_lock(build_mcpb):
    """It is generated from requirements-mcp.txt. Recompiling the lock without re-running the build
    script would ship a bundle that installs different versions from the ones we audited."""
    expected = build_mcpb.render_pyproject(build_mcpb.read_version(), build_mcpb.read_pins())
    assert BUNDLE_PYPROJECT.read_text(encoding="utf-8") == expected, (
        "packaging/mcpb/pyproject.toml is stale — run: python packaging/mcpb/build_mcpb.py --validate"
    )


def test_the_bundle_pins_the_whole_transitive_set():
    """Floors would let two users installing a month apart get different transitive sets — the
    reason PLAN.md requires `==` here even though everything else installs from a lock."""
    text = BUNDLE_PYPROJECT.read_text(encoding="utf-8")
    # Stop at the closing bracket on its own line: an entry can contain one (`pyjwt[crypto]==…`),
    # so a lazy `(.*?)\]` truncates the list at the first extra and silently under-counts.
    deps = re.search(r"dependencies = \[\n(.*?)\n\]", text, re.S).group(1)
    pins = re.findall(r'"([^"]+)"', deps)
    assert len(pins) >= 25, f"only {len(pins)} pins — the transitive set is ~29"
    for pin in pins:
        assert "==" in pin, f"{pin} is not an exact pin"


def test_the_bundle_never_ships_a_vendored_environment(build_mcpb):
    """`server/lib/` and `server/venv/` are forbidden by the format for this bundle, because MCPB
    cannot portably vendor the compiled dependencies we have (PyMuPDF is C, pydantic is Rust)."""
    assert "lib" not in build_mcpb.PAYLOAD_PACKAGES
    assert "venv" not in build_mcpb.PAYLOAD_PACKAGES
    # the one Qt-importing file in model/
    assert "model/edit_commands.py" in build_mcpb.EXCLUDE_FILES


# ---- the Claude Code config -----------------------------------------------------------


def test_the_checked_in_mcp_json_names_the_server_and_a_real_entry_point():
    config = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    server = config["mcpServers"]["klarpdf"]
    assert server["args"] == ["-m", "mcp_bridge"]

    import importlib.util

    assert importlib.util.find_spec("mcp_bridge.__main__") is not None
