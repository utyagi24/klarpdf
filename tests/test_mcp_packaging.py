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
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-mcp.txt"
MANIFEST = ROOT / "packaging" / "mcpb" / "manifest.json"
BUNDLE_PYPROJECT = ROOT / "packaging" / "mcpb" / "pyproject.toml"
BUNDLE_UV_LOCK = ROOT / "packaging" / "mcpb" / "uv.lock"
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


def test_the_built_metadata_declares_dependencies():
    """The bug this exists for: `pyproject.toml` had no `dependencies`, so the built metadata
    carried **zero `Requires-Dist`** and the documented `pipx install .` produced a `klarpdf-mcp`
    that died on `import mcp`. Found by hand; nothing in CI noticed, because every other test ran
    against a working tree where the imports resolve anyway.

    Builds the real metadata through the declared backend rather than re-parsing the TOML, so it
    checks what a *user's installer* would see. No network: the backend is called directly, which
    skips build isolation.
    """
    import tempfile

    pytest.importorskip("setuptools", reason="build backend; not in the bridge's runtime lock")

    from setuptools.build_meta import prepare_metadata_for_build_wheel

    cwd = os.getcwd()
    out = tempfile.mkdtemp()
    try:
        os.chdir(ROOT)
        dist_info = prepare_metadata_for_build_wheel(out)
    finally:
        os.chdir(cwd)

    metadata = (Path(out) / dist_info / "METADATA").read_text(encoding="utf-8")
    requires = [
        line.split(":", 1)[1].strip()
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist")
    ]
    assert requires, "no Requires-Dist — `pipx install .` would install a script with no deps"
    joined = " ".join(requires).lower()
    assert "mcp" in joined and "pymupdf" in joined

    entry_points = (Path(out) / dist_info / "entry_points.txt").read_text(encoding="utf-8")
    assert "klarpdf-mcp" in entry_points


def _requirements(text: str, base: Path | None = None) -> dict[str, tuple[str, str]]:
    """`{name: (operator, version)}` from a requirements-style block, ignoring comments.

    Follows `-r other.in` includes when `base` is given, because that is how the inputs are now
    written: PyMuPDF lives in `requirements-core.in` and both `requirements.in` and
    `requirements-mcp.in` pull it in from there (M115), so a parser that stopped at the top level
    would report the shared engine as declared by nobody.
    """
    out = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip().strip('",')
        if not line:
            continue
        include = re.match(r"^-r\s+(\S+)$", line)
        if include and base is not None:
            nested = base.parent / include.group(1)
            if nested.exists():
                out.update(_requirements(nested.read_text("utf-8"), nested))
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(==|>=)\s*([0-9][^,\s]*)", line)
        if m:
            out[m.group(1).lower()] = (m.group(2), m.group(3))
    return out


def _input(name: str) -> dict[str, tuple[str, str]]:
    """Parse one `.in` file, following its `-r` includes."""
    path = ROOT / name
    return _requirements(path.read_text("utf-8"), path)


def _version(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


def test_the_declared_dependencies_match_the_locks_input():
    """`pyproject.toml` and `requirements-mcp.in` must name the **same packages**, and where the
    input pins one exactly that pin must satisfy the declared floor.

    This used to assert the two blocks were string-equal, which only worked while both were floors.
    They are legitimately different *kinds* — `pyproject.toml` says what the code needs to work,
    `requirements-mcp.in` says the exact set we audit and compile the lock from (M115) — so equality
    was the wrong comparison, and it would have failed the moment either side was tightened.
    """
    declared_block = re.search(
        r"dependencies = \[(.*?)\n\]", PROJECT_PYPROJECT.read_text("utf-8"), re.S
    )
    declared = _requirements(declared_block.group(1))
    wanted = _input("requirements-mcp.in")

    assert set(declared) == set(wanted), (
        f"pyproject names {sorted(declared)}, requirements-mcp.in names {sorted(wanted)}"
    )
    for name, (op, version) in wanted.items():
        if op == "==":
            floor_op, floor = declared[name]
            assert _version(version) >= _version(floor), (
                f"{name}: the compiled pin {version} is below pyproject's floor {floor_op}{floor}"
            )


def _app_lock() -> dict[str, tuple[str, str]]:
    """The shipped app's pins, with the hashed lock's `\\` continuations stripped."""
    return _requirements(
        "\n".join(
            line.split("\\")[0]
            for line in (ROOT / "requirements-win.txt").read_text("utf-8").splitlines()
        )
    )


def test_the_bridge_and_the_app_never_ship_different_versions_of_a_shared_library():
    """**Every** package both locks carry must be at the same version — not just PyMuPDF (M115).

    PyMuPDF is the one that bit us, because it *is* the PDF engine: `model/` hands it every read and
    write both surfaces make, so two versions can write different bytes for the same edit. The app
    shipped 1.27.2.3 while the bridge's lock said 1.28.2 for three months. But the defect is the
    *shape*, not the package, so this asserts the general invariant — a library shared by both
    surfaces cannot be at two versions, whichever one somebody adds next.

    Deliberately compares the **locks**, not the inputs: an input can say anything, the lock is what
    is installed. `requirements-dev.txt` is not a third party to this — CI installs it and it tracks
    the app, which is precisely why the bridge's real lock went three months without being run.
    """
    app, bridge = _app_lock(), _requirements(LOCK.read_text("utf-8"))
    shared = sorted(set(app) & set(bridge))
    assert shared, "expected at least PyMuPDF in common; did a lock stop parsing?"
    mismatched = {
        name: (app[name][1], bridge[name][1]) for name in shared if app[name][1] != bridge[name][1]
    }
    assert not mismatched, (
        "the app and the bridge would install different versions of a shared library "
        f"(app, bridge): {mismatched} — bump both together, never one"
    )


def test_a_library_the_app_also_ships_is_pinned_in_the_bridge_input_not_floored():
    """The root cause, asserted directly: a **floor** cannot hold two locks together.

    Both inputs asked for `PyMuPDF>=1.25.5` and `pip-compile` resolves `>=` to whatever was newest
    on the day it ran — so the same line produced 1.27.2.3 for the app and 1.28.2 for the bridge
    (M115). The version test above catches the drift once it has happened; this one catches the
    construction that allows it, which is the part that silently re-arms after any recompile.

    Only packages the *app also ships* are constrained. Everything else in the bridge's input is
    free to float — `mcp>=2,<3` is not shared with anything and stays a range.
    """
    app = _app_lock()
    declared = _input("requirements-mcp.in")
    floored = {
        name: f"{op}{version}" for name, (op, version) in declared.items()
        if name in app and op != "=="
    }
    assert not floored, (
        f"shared with the shipped app but not pinned in requirements-mcp.in: {floored} — "
        "a floor lets pip-compile resolve the two locks to different versions"
    )


def test_nothing_tells_a_user_to_run_the_module_form():
    """`python -m mcp_bridge` only works when the CWD *is* the repo — `-m` puts the working
    directory on `sys.path`, never the interpreter's location — and a client launches its servers
    from its own directory. Both the checked-in `.mcp.json` and the README said to use it, and it
    failed for exactly that reason the first time anyone ran it from a real folder of PDFs.

    `mcp_bridge/__main__.py` stays (it is a genuine convenience *inside* a checkout); what must not
    come back is documenting it as the way to wire up a client.
    """
    config = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    assert config["mcpServers"]["klarpdf"]["command"] == "klarpdf-mcp"
    assert "args" not in config["mcpServers"]["klarpdf"]

    for doc in (ROOT / "mcp_bridge" / "README.md", ROOT / "README.md"):
        for line in doc.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if "python -m mcp_bridge" not in stripped:
                continue
            # Allowed only where the text is warning against it, not prescribing it.
            assert any(
                word in stripped for word in ("not", "fails", "only works", "must")
            ), f"{doc.name} prescribes `python -m mcp_bridge`: {stripped!r}"


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


def _lock_versions(text: str) -> dict[str, str]:
    """`{name: version}` for every `[[package]]` block in a `uv.lock`."""
    found = {}
    for block in text.split("[[package]]")[1:]:
        name = re.search(r'^\s*name = "([^"]+)"', block, re.M)
        version = re.search(r'^version = "([^"]+)"', block, re.M)
        if name and version:
            found[name.group(1).lower().replace("_", "-")] = version.group(1)
    return found


def test_the_bundle_ships_its_lock(build_mcpb, tmp_path):
    """M129 — the `.mcpb` must carry `uv.lock`, because that is what makes the install hash-verified.

    `uv run --directory` **honours a committed lock** — measured by shipping one with `colorama`
    pinned a version back and watching that older version install where a fresh resolve picks the
    newer one. Without this file the bundle carries pins only, `uv` writes its own lock on the user's
    machine, and the hashes attest to whatever PyPI served then rather than to anything we audited.

    Before M129 `stage()` copied the packages, `version.py` and `pyproject.toml` and stopped, while
    `RELEASE.md` told a reader to run `uv lock` and rebuild "so the lock travels inside the bundle".
    It did not travel, and the person checking whether locks are honoured would have installed a
    bundle with no lock in it and recorded the wrong answer.
    """
    staged = build_mcpb.stage(tmp_path / "stage", "0.0.0", ["mcp==1.0.0"])
    shipped = staged / "server" / "uv.lock"

    assert shipped.exists(), "the bundle must ship uv.lock — see M129"
    assert shipped.read_bytes() == BUNDLE_UV_LOCK.read_bytes(), "shipped lock differs from committed"
    assert "sha256" in shipped.read_text(encoding="utf-8"), "a lock without hashes buys nothing"


def test_the_committed_lock_is_in_step_with_the_generated_pyproject():
    """A stale lock ships silently, and it is the lock — not the pins — that now decides the install.

    `pyproject.toml` is regenerated from `requirements-mcp.txt` on every build, but `uv.lock` is
    refreshed only when someone runs `uv lock`. So a dependency bump that skips that step leaves a
    lock pinning the previous version, and since M129 that lock is what a Desktop install obeys.
    Regenerate with: `cd packaging/mcpb && uv lock`.
    """
    locked = _lock_versions(BUNDLE_UV_LOCK.read_text(encoding="utf-8"))
    declared = re.findall(
        r'"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?==([^"]+)"',
        BUNDLE_PYPROJECT.read_text(encoding="utf-8"),
    )
    assert declared, "no pins found in the generated pyproject"

    drift = [
        (name, want, locked.get(name.lower().replace("_", "-")))
        for name, want in declared
        if locked.get(name.lower().replace("_", "-")) != want
    ]
    assert not drift, f"uv.lock is stale — run `uv lock` in packaging/mcpb. Drift: {drift}"


def test_the_manifest_runtime_range_is_semver_not_pep_440(manifest):
    """M128 — the manifest is read by a Node host, so this field is a **node-semver** range.

    `>=3.12,<3.13` is the PEP 440 spelling and it is correct in `pyproject.toml`, where pip and uv
    read it. In `compatibility.runtimes` it is parsed by node-semver, where a comma is not the AND
    separator — a space is — so the range is unsatisfiable and Claude Desktop reported the runtime
    as unmet on a machine running exactly 3.12.10. Measured with semver@7:
    `>=3.12,<3.13` rejects 3.12.10; `>=3.12.0 <3.13.0` accepts it.

    `mcpb validate` passes either way (it checks the field is a string), so nothing upstream catches
    this. The comma is the whole bug and is what this asserts.
    """
    declared = manifest["compatibility"]["runtimes"]["python"]

    assert "," not in declared, (
        f"{declared!r} is PEP 440 syntax in a node-semver field — no Python can satisfy it. "
        "Separate comparators with a space: '>=3.12.0 <3.13.0'."
    )
    # node-semver compares part by part, so a bare `3.12` bound is a trap of its own: spell the
    # patch component out rather than relying on the parser's zero-fill.
    for bound in declared.split():
        assert re.match(r"^[<>=~^]*\d+\.\d+\.\d+$", bound), f"{bound!r} is not a full-version bound"


def test_the_two_python_requirements_describe_the_same_window(manifest, build_mcpb):
    """The bundle states its Python floor twice, in two ecosystems' syntaxes, and they must agree.

    `pyproject.toml` is what actually installs (PEP 440, comma-separated); the manifest is what the
    host pre-flights (node-semver, space-separated). They are written in different files by different
    rules, so nothing but this keeps them from drifting into a bundle that installs fine and warns,
    or pre-flights fine and fails to install.
    """
    rendered = build_mcpb.render_pyproject("0.0.0", ["mcp==1.0.0"])
    pep440 = re.search(r'requires-python = "([^"]+)"', rendered).group(1)
    semver = manifest["compatibility"]["runtimes"]["python"]

    def bounds(text: str) -> set[tuple[str, str, str]]:
        found = set()
        for part in re.split(r"[,\s]+", text.strip()):
            match = re.match(r"^([<>]=?)(\d+)\.(\d+)", part)
            if match:
                found.add((match.group(1), match.group(2), match.group(3)))
        return found

    assert bounds(pep440) == bounds(semver), (
        f"pyproject says {pep440!r}, manifest says {semver!r} — same window, two syntaxes"
    )


def test_npx_is_started_by_resolved_path_never_by_the_bare_name(build_mcpb, monkeypatch):
    """M127 — a bare "npx" does not start a process on the one platform this project ships.

    npm installs the launcher as `npx.CMD` on Windows, and `subprocess` with a list argv goes to
    `CreateProcess`, which searches PATH appending only `.exe` — it never reads `PATHEXT`. So the
    spelling that works on Linux and macOS raised `FileNotFoundError: [WinError 2]` here, and the
    `.mcpb` could not be built on Windows at all. It went unseen because the bundle had only ever
    been built in WSL, where `npx` is a real executable.

    Asserting on argv[0] rather than on a successful build keeps this a unit test: it pins the
    regression without needing Node installed on the runner.
    """
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(build_mcpb.shutil, "which", lambda name: rf"C:\node\{name}.CMD")
    monkeypatch.setattr(build_mcpb.subprocess, "run", fake_run)

    build_mcpb.mcpb("validate", "manifest.json")

    launcher = calls[0][0]
    assert launcher != "npx", "a bare 'npx' is unstartable on Windows; resolve it through PATHEXT"
    assert launcher.endswith("npx.CMD")


def test_a_missing_node_is_reported_rather_than_raising_winerror_2(build_mcpb, monkeypatch):
    """The failure mode without Node should name Node. Before M127 it was a raw traceback ending in
    `FileNotFoundError: [WinError 2] The system cannot find the file specified`, which names neither
    the missing tool nor the fact that it is a build-time dependency only."""
    monkeypatch.setattr(build_mcpb.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit) as excinfo:
        build_mcpb.resolve_npx()

    message = str(excinfo.value)
    assert "npx is not on PATH" in message
    assert "nodejs.org" in message


# ---- the Claude Code config -----------------------------------------------------------


def test_the_checked_in_mcp_json_names_a_real_entry_point():
    """It must name the console script `pyproject.toml` actually declares — the two drifting apart
    is a config that looks right and fails at launch with `command not found`."""
    config = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    command = config["mcpServers"]["klarpdf"]["command"]
    assert f"{command} =" in PROJECT_PYPROJECT.read_text(encoding="utf-8")

    from mcp_bridge.server import main

    assert callable(main)


# ---- the Python version window (M132) -------------------------------------------------


def _declared_window(text: str) -> tuple[int, int]:
    """`(floor_minor, ceiling_minor)` from a `>=3.x,<3.y` / `>=3.x.0 <3.y.0` range, either syntax.

    It raises rather than returning a default when either bound is missing, and the message names
    the string, because the most likely way to get here is the one real failure this guards: a
    `uv.lock` still holding uv's own `==3.12.*` spelling after the pyproject was widened. A silent
    default would turn that into a passing test.
    """
    floor = re.search(r">=\s*3\.(\d+)", text)
    ceiling = re.search(r"<\s*3\.(\d+)", text)
    if not floor or not ceiling:
        raise AssertionError(
            f"{text!r} is not a two-bounded range. If this came from uv.lock, uv writes `==3.12.*` "
            "when it resolves for a single version — re-run `uv lock` in packaging/mcpb so the lock "
            "covers the whole window the pyproject declares."
        )
    return int(floor.group(1)), int(ceiling.group(1))


def test_the_three_python_declarations_are_one_window(manifest):
    """M132 — the window is now written in THREE files, not two, and `uv.lock` is the new one.

    `test_the_two_python_requirements_describe_the_same_window` above pairs the root pyproject with
    the manifest. The lock is the third, and it is the one that silently matters most: uv resolves
    wheels **for the window the lock declares**, so a lock still saying `==3.12.*` while the
    pyproject says `>=3.11,<3.15` records cp312 wheels only — an install that pre-flights fine,
    declares support for four Pythons and can only actually install on one.

    That is not hypothetical. It is exactly the state this repo was in before `uv lock` was re-run:
    the committed lock held 16 `rpds-py` wheels, all of them cp312.
    """
    pyproject = _declared_window(
        re.search(r'requires-python = "([^"]+)"', PROJECT_PYPROJECT.read_text(encoding="utf-8")).group(1)
    )
    semver = _declared_window(manifest["compatibility"]["runtimes"]["python"])
    lock = _declared_window(
        re.search(r'requires-python = "([^"]+)"', BUNDLE_UV_LOCK.read_text(encoding="utf-8")).group(1)
    )

    assert pyproject == semver == lock, (
        f"pyproject {pyproject}, manifest {semver}, uv.lock {lock} — one window, three spellings. "
        "If the pyproject moved, re-run `uv lock` in packaging/mcpb."
    )


def _wheel_covers(filename: str, minor: int) -> bool:
    """Does this wheel install on CPython 3.`minor`?

    `abi3` is the stable ABI: `cp310-abi3` is ONE wheel that serves 3.10 and every later version,
    which is why PyMuPDF needs no per-version build and why nothing here imposes a ceiling. A `t`
    suffix (`cp314t`) is the free-threaded build — a separate ABI that a normal interpreter cannot
    load, so it must not be counted as coverage.
    """
    match = re.search(r"-cp3(\d+)-(abi3|cp3\d+t?)-", filename)
    if not match:
        return False
    built_for, abi = int(match.group(1)), match.group(2)
    if abi == "abi3":
        return minor >= built_for
    if abi.endswith("t"):
        return False
    return built_for == minor


def test_every_compiled_pin_has_a_wheel_for_every_python_in_the_window():
    """The window is only real if a wheel exists at each end of it, on each platform we claim.

    A missing wheel does not fail the install — pip and uv fall back to building from source, and
    two of these are C (PyMuPDF, cffi) and two are Rust (pydantic-core, rpds-py). On a user's
    machine that means a compiler toolchain they do not have, at install time, from a one-click
    bundle. So this walks the committed lock rather than trusting the range.

    Pure-Python packages are skipped: a `py3-none-any` wheel is every version and every platform.
    `pywin32` is skipped for non-Windows because the lock marks it `sys_platform == 'win32'` and it
    is therefore never resolved there — the one package whose absence off-Windows is correct.
    """
    text = BUNDLE_UV_LOCK.read_text(encoding="utf-8")
    floor, ceiling = _declared_window(re.search(r'requires-python = "([^"]+)"', text).group(1))

    platforms = {
        "win_amd64": lambda name: "win_amd64" in name,
        "macos-arm64": lambda name: "macosx" in name and "arm64" in name,
        "linux-x86_64": lambda name: ("manylinux" in name or "musllinux" in name)
        and "x86_64" in name
        and "i686" not in name,
    }
    windows_only = {"pywin32"}

    gaps = []
    for block in text.split("[[package]]")[1:]:
        name_match = re.search(r'name = "([^"]+)"', block)
        wheels = re.findall(r'/([^/"]+\.whl)', block)
        if not name_match or not wheels:
            continue
        name = name_match.group(1)
        if any(w.endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl")) for w in wheels):
            continue
        for minor in range(floor, ceiling):
            for platform, matches in platforms.items():
                if name in windows_only and platform != "win_amd64":
                    continue
                if not any(matches(w) and _wheel_covers(w, minor) for w in wheels):
                    gaps.append(f"{name} has no wheel for 3.{minor} on {platform}")

    assert not gaps, (
        "the declared Python window promises more than the lock can deliver; these would fall back "
        "to a source build on a user's machine:\n  " + "\n  ".join(gaps)
    )


def test_every_python_in_the_window_is_run_by_some_ci_job():
    """A range nobody runs is a guess. This ties the declaration to the runners that test it.

    The pyproject's comment says widening the window means adding a runner first; this is what makes
    that true rather than advisory.

    It reads every job that runs the bridge suite, not just one, because coverage is deliberately
    split: `bridge` and `bridge-windows` pin 3.12, and `bridge-pyver` carries the rest — 3.12 is
    left out of that matrix rather than run a third time. Asserting against the union is what lets
    that stay a free choice instead of something this test forces.

    Regex rather than `yaml.safe_load`, and not by preference: this file runs in the `bridge` job
    under `requirements-mcp.txt` + pytest, and PyYAML is in neither.
    """
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    floor, ceiling = _declared_window(
        re.search(r'requires-python = "([^"]+)"', PROJECT_PYPROJECT.read_text(encoding="utf-8")).group(1)
    )

    # Split into top-level jobs, then keep the ones that actually run the bridge suite. A job that
    # sets up a Python but never runs these tests proves nothing about the window.
    jobs = re.split(r"\n  (?=[a-z][a-z0-9-]*:\n)", workflow)
    tested = set()
    for job in jobs:
        if "tests/test_mcp_*.py" not in job:
            continue
        tested |= set(re.findall(r'python-version: "(3\.\d+)"', job))
        matrix = re.search(r"python-version: \[([^\]]+)\]", job)
        if matrix:
            tested |= set(re.findall(r'"(3\.\d+)"', matrix.group(1)))

    missing = {f"3.{minor}" for minor in range(floor, ceiling)} - tested
    assert not missing, (
        f"requires-python claims {sorted(missing)} but no CI job runs the bridge suite on it. "
        "Add it to the bridge-pyver matrix in .github/workflows/test.yml, or narrow the window."
    )
