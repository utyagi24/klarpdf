"""M176 (#416): the bridge says which version it is, to a person at the terminal and to an agent.

A person runs ``klarpdf-mcp --version``, or reads it at the top of ``--help``. An agent reads
``klarpdf://env``, which also lists the libraries the bridge runs on and the switches it was started
with. The server's instructions name that page, in both modes, because a model would not think to
look for it otherwise. `tests/test_mcp_docs.py` checks the instructions still fit their budget.
"""

from __future__ import annotations

import asyncio
import json
import platform
import socket
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from klarpdf.mcp_bridge.config import Config, PathNotAllowed, PathPolicy
from klarpdf.mcp_bridge.replies import EnvironmentReply
from klarpdf.mcp_bridge.server import ENV_URI, create_server, parse_args
from klarpdf.version import __version__


def read_env(built) -> dict:
    contents = asyncio.run(built.read_resource(ENV_URI))
    assert len(contents) == 1
    assert contents[0].mime_type == "application/json"
    return json.loads(contents[0].content)


# ---- klarpdf://env ----------------------------------------------------------------


def test_the_page_reports_the_versions_that_are_running():
    """Each value is checked against a second source where one exists, so a value copied into the
    code would fail here the day the library moves on."""
    got = read_env(create_server())
    assert got["klarpdf"] == __version__
    assert got["pymupdf"] == version("pymupdf")
    assert got["pymupdf"].startswith(got["mupdf"]), "PyMuPDF's version leads with MuPDF's"
    assert got["mcp"] == version("mcp")
    assert got["python"] == ".".join(str(part) for part in sys.version_info[:3])
    assert got["platform"]


def test_the_page_holds_exactly_the_fields_its_type_names():
    assert set(read_env(create_server())) == EnvironmentReply.__required_keys__


def test_an_unrestricted_server_says_so():
    got = read_env(create_server())
    assert got["read_only"] is False
    assert got["paths_restricted"] is False
    assert got["allowed_roots"] == []


def test_the_page_lists_the_folders_the_way_a_refusal_names_them(tmp_path):
    """The owner chose full paths (2026-11-12): a refused call already shows them to the agent, and
    with them on the page the agent can pick a path that will be accepted before it is refused."""
    inside, other = tmp_path / "inside", tmp_path / "other"
    inside.mkdir()
    other.mkdir()
    policy = PathPolicy.from_args([str(inside), str(other)])
    got = read_env(create_server(Config(policy=policy, read_only=True)))

    assert got["read_only"] is True
    assert got["paths_restricted"] is True
    assert got["allowed_roots"] == list(policy.roots)
    with pytest.raises(PathNotAllowed) as refused:
        policy.check(tmp_path / "elsewhere.pdf")
    for root in got["allowed_roots"]:
        assert root in str(refused.value)


def test_the_page_names_no_other_path_and_not_the_machine():
    """The allowed folders are the only paths on the page. A field such as the Python executable's
    path would put the user's home folder into every bug report the page is pasted into."""
    text = json.dumps(read_env(create_server()))
    assert str(Path.home()) not in text
    assert sys.executable not in text
    for name in {socket.gethostname(), platform.node()} - {""}:
        assert name not in text


@pytest.mark.parametrize("read_only", [False, True], ids=["default", "read-only"])
def test_both_modes_list_the_page_and_name_it_in_the_instructions(read_only):
    built = create_server(Config(read_only=read_only))
    assert ENV_URI in {str(r.uri) for r in asyncio.run(built.list_resources())}
    assert f"`{ENV_URI}`" in (built.instructions or "")


# ---- --version and --help ---------------------------------------------------------


def test_version_prints_the_version_to_stdout_and_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exited:
        parse_args(["--version"])
    assert exited.value.code == 0
    out, err = capsys.readouterr()
    assert out == f"klarpdf-mcp {__version__}\n"
    assert err == ""


def test_help_shows_the_version(capsys):
    with pytest.raises(SystemExit) as exited:
        parse_args(["--help"])
    assert exited.value.code == 0
    out, _ = capsys.readouterr()
    assert f"klarpdf-mcp {__version__}" in out
    assert "--version" in out


@pytest.mark.parametrize("flag", ["--version", "--help"])
def test_the_flag_exits_before_the_server_starts(flag):
    """Run as a client runs it, with stdin left open. A server that had started would wait on stdin
    for a request and never exit, so the time limit is what fails.

    ``wait``, not ``communicate``: ``communicate`` closes stdin, and a server would read that as the
    end of the session and exit by itself. The output is far smaller than a pipe's buffer, so
    waiting before reading it cannot block.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "klarpdf.mcp_bridge", flag],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=".",
    )
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail(f"klarpdf-mcp {flag} did not exit: the server started and is waiting on stdin")
    out, err = proc.communicate()
    assert proc.returncode == 0, err.decode(errors="replace")
    assert f"klarpdf-mcp {__version__}" in out.decode()
