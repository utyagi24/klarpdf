"""M136 — `install.py`'s logic, without installing anything.

The end-to-end behaviour is exercised by a CI leg that really runs it; this file covers the parts
that are cheap to get wrong and expensive to notice: the version window, the platform paths, the
environment split, and the checks that stand between "pip exited 0" and "something runnable exists".

`install.py` is loaded by path rather than imported, because it is not part of the package — it is a
release artifact that ships alone, and giving it an import name here would be the first step towards
it acquiring dependencies it cannot have.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "packaging" / "mcp" / "installer" / "install.py"
SYNC = ROOT / "packaging" / "mcp" / "installer" / "sync_installer.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def installer():
    return _load(INSTALLER, "_klarpdf_install")


# --- the window it accepts -----------------------------------------------------------------------

def test_the_accepted_window_is_the_one_the_package_declares(installer):
    """`install.py` carries the bounds as literals — it runs where there is no repo to read. This is
    the check that keeps that fourth copy honest (M132's lesson, one file further on)."""
    result = subprocess.run([sys.executable, str(SYNC), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, f"install.py's constants are stale:\n{result.stderr}"


def test_it_refuses_a_python_outside_the_window(installer, monkeypatch):
    for below in ((3, 10), (3, 9), (2, 7)):
        monkeypatch.setattr(installer.sys, "version_info", below + (0, "final", 0))
        with pytest.raises(installer.InstallError, match="needs Python"):
            installer.check_python()
    monkeypatch.setattr(installer.sys, "version_info", (3, 15, 0, "final", 0))
    with pytest.raises(installer.InstallError, match="needs Python"):
        installer.check_python()


def test_it_accepts_every_python_in_the_window(installer, monkeypatch):
    lo, hi = installer.PYTHON_MIN, installer.PYTHON_MAX
    for minor in range(lo[1], hi[1] + 1):
        monkeypatch.setattr(installer.sys, "version_info", (lo[0], minor, 0, "final", 0))
        monkeypatch.setattr(installer.sys, "platform", "linux")
        installer.check_python()  # must not raise


def test_the_store_stub_is_named_rather_than_left_to_fail_later(installer, monkeypatch):
    """Typing `python` with no Python installed opens the Microsoft Store and nothing runs."""
    monkeypatch.setattr(installer.sys, "version_info", (3, 12, 0, "final", 0))
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(
        installer.sys, "executable",
        r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\python.exe",
    )
    with pytest.raises(installer.InstallError, match="Microsoft Store"):
        installer.check_python()


# --- platform paths ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "platform, expected_tail",
    [("win32", ("Scripts", "python.exe")), ("linux", ("bin", "python")), ("darwin", ("bin", "python"))],
)
def test_the_venv_interpreter_path_follows_the_platform(installer, monkeypatch, platform, expected_tail):
    monkeypatch.setattr(installer.sys, "platform", platform)
    assert installer.venv_python(Path("/x/.venv")).parts[-2:] == expected_tail


def test_the_install_directory_is_flat_and_identically_named_everywhere(installer, monkeypatch):
    """One name on all three platforms, and never nested under the app's own data directory — the
    bridge is a separate component and many people never install the app at all."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert installer.default_install_dir() == Path.home() / ".local/share/klarpdf-mcp"

    monkeypatch.setenv("XDG_DATA_HOME", "/custom/data")
    assert installer.default_install_dir() == Path("/custom/data/klarpdf-mcp")

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    assert installer.default_install_dir() == Path.home() / "Library/Application Support/klarpdf-mcp"

    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    assert installer.default_install_dir().name == "klarpdf-mcp"


# --- the environment handed to pip ----------------------------------------------------------------

def test_location_variables_are_stripped_and_network_variables_survive(installer, monkeypatch):
    """Measured in M136: `PIP_TARGET` sends the install elsewhere and pip still exits 0, so the
    entry point is simply absent. `PIP_USER` fails loudly. Meanwhile a corporate mirror or proxy
    must keep working, which is why `--isolated` is not used — it would also ignore pip.conf."""
    for name in installer.STRIP_ENV:
        monkeypatch.setenv(name, "/somewhere/else")
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.internal/simple")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:8080")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/ssl/corp.pem")

    env = installer.pip_env()
    for name in installer.STRIP_ENV:
        assert name not in env, f"{name} reached pip; it redirects where packages land"
    assert env["PIP_INDEX_URL"] == "https://mirror.internal/simple"
    assert env["HTTPS_PROXY"] == "http://proxy.internal:8080"
    assert env["REQUESTS_CA_BUNDLE"] == "/etc/ssl/corp.pem"


def test_the_two_environment_lists_do_not_overlap(installer):
    assert not (set(installer.KEEP_ENV) & set(installer.STRIP_ENV))


# --- the checks between "pip exited 0" and "something runs" ---------------------------------------

def test_a_missing_entry_point_is_reported_with_the_cause_named(installer, tmp_path, monkeypatch):
    """The failure mode a redirected pip actually produces: success, and nothing to run."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    (tmp_path / "bin").mkdir()
    with pytest.raises(installer.InstallError, match="PIP_TARGET"):
        installer.validate(tmp_path, verbose=False)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in server is a /bin/sh script")
def test_output_that_is_not_json_rpc_on_stdout_fails_validation(installer, tmp_path, monkeypatch):
    """A stray print to stdout corrupts every client session, and is otherwise invisible until one
    dies mid-conversation. The handshake is what makes it visible at install time.

    Skipped on Windows because the stand-in server here is a shell script, and `CreateProcess`
    cannot run one (`WinError 193`). What is under test — how `validate` reads a subprocess's
    stdout — has no platform-specific branch, and the Windows path is covered for real by the
    `installer (windows-latest)` CI job, which installs the actual package and completes a real
    handshake. This is where a fake is cheap and a real install is not.
    """
    monkeypatch.setattr(installer.sys, "platform", "linux")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "klarpdf-mcp"
    script.write_text("#!/bin/sh\necho 'loading plugins...'\n")
    script.chmod(0o755)
    with pytest.raises(installer.InstallError, match="not JSON-RPC"):
        installer.validate(tmp_path, verbose=False)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in server is a /bin/sh script")
def test_a_json_rpc_error_reply_still_counts_as_alive(installer, tmp_path, monkeypatch):
    """Deliberate: pinning a protocol version into a shipped installer would break it the next time
    the MCP SDK moves. What matters is that the process started, read stdin and answered in
    JSON-RPC. Skipped on Windows for the same reason as the test above."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "klarpdf-mcp"
    reply = json.dumps({"jsonrpc": "2.0", "id": 1,
                        "error": {"code": -32602, "message": "unsupported protocol version"}})
    script.write_text(f"#!/bin/sh\nif [ \"$1\" = \"--help\" ]; then exit 0; fi\ncat >/dev/null\necho '{reply}'\n")
    script.chmod(0o755)
    assert installer.validate(tmp_path, verbose=False)


# --- what the user is left with -------------------------------------------------------------------

def test_the_uninstaller_refuses_a_directory_that_is_not_ours(installer, tmp_path):
    """It deletes a whole tree, so pointing it at the wrong path must be refused rather than obeyed."""
    installer.write_uninstaller(tmp_path)
    result = subprocess.run([sys.executable, str(tmp_path / "uninstall.py")],
                            capture_output=True, text=True, input="y\n")
    assert result.returncode != 0
    assert "does not look like a KlarPDF MCP install" in (result.stdout + result.stderr)


def test_the_uninstaller_changes_directory_before_deleting(installer, tmp_path):
    """Windows cannot remove a directory that is the process's current working directory, and a
    reader who `cd`s in before running it is the obvious case."""
    installer.write_uninstaller(tmp_path)
    text = (tmp_path / "uninstall.py").read_text()
    assert "os.chdir" in text
    assert text.index("os.chdir") < text.index("rmtree")


def test_it_needs_no_stdin(installer):
    """Prompts became flags so the file is scriptable, works in a Dockerfile, and survives a pipe —
    where stdin holds the program itself and `input()` raises EOFError.

    The embedded uninstaller is exempt and excluded here: it always runs from disk, where asking
    before deleting a whole directory tree is the right thing to do.
    """
    source = INSTALLER.read_text(encoding="utf-8").replace(installer.UNINSTALL, "")
    assert "input(" not in source, "a prompt here would break `curl … | python -`, CI and Docker"
    assert "input(" in installer.UNINSTALL, "the uninstaller should still confirm before deleting"


def test_the_client_commands_are_argument_lists_not_shell_strings(installer):
    for client, argv in installer.CLIENTS.items():
        assert isinstance(argv, list) and argv, client
        assert not any(" " in part for part in argv), f"{client}: a shell string would need quoting"
