"""M39 keystone — the MCP server path never imports Qt, and never opens a socket.

This is the invariant the whole "reuses the GUI-free core" claim rests on (PLAN.md §Verification).
If it fails, the bridge silently depends on a 60 MB GUI toolkit it has no use for, and the
"separate optional component" packaging story stops being true.

**Why a subprocess.** ``PySide6`` is in ``sys.modules`` by the time any of this runs — pytest
imports every test module at collection and most of them import Qt, and ``conftest.py``'s teardown
hook imports it too. An in-process ``assert "PySide6" not in sys.modules`` would therefore be
asserting something about the *test suite*, not about the server. The only honest check is a fresh
interpreter that imports nothing but the bridge. PLAN.md's wording is deliberate here: *assert it in
a test, don't just observe it.*

The child exercises every tool before checking, because an import that only happens inside a tool
body — ``model.edit_engine`` in ``render_page``, ``model.page_edits`` in ``get_form_fields`` — is
exactly the one a load-time check would miss.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

# Modules that must never appear. `shiboken6` is PySide6's binding layer and would be the tell if
# something imported a Qt submodule by a path that dodged the PySide6 name.
FORBIDDEN = ("PySide6", "shiboken6")

# The GUI-only corner of model/: it imports QUndoCommand, and excluding it is what lets the rest of
# model/ be shared (PLAN.md §Architecture).
FORBIDDEN_MODEL = "model.edit_commands"

_CHILD = textwrap.dedent(
    '''
    import asyncio, json, sys

    PDF = sys.argv[1]

    # Refuse the network before anything is imported: the server is stdio-only, so it must make no
    # outbound connection and bind no port (PLAN.md §Verification).
    #
    # Poison `connect`/`bind`, NOT the socket constructor. asyncio's event loop builds an internal
    # `socket.socketpair()` self-pipe to wake itself, which is neither a connection nor a port; a
    # blanket ban on constructing sockets breaks the event loop before any tool runs, and would be
    # testing asyncio rather than us.
    import socket
    def _refuse(what):
        def guard(self, *a, **k):
            raise AssertionError(f"the MCP server path called socket.{what}{a!r}")
        return guard
    socket.socket.connect = _refuse("connect")
    socket.socket.connect_ex = _refuse("connect_ex")
    socket.socket.bind = _refuse("bind")

    from mcp_bridge.server import server

    async def exercise():
        await server.list_tools()
        await server.call_tool("get_info", {"path": PDF})
        await server.call_tool("get_outline", {"path": PDF})
        await server.call_tool("search", {"path": PDF, "query": "ALPHA"})
        await server.call_tool("extract_text", {"path": PDF, "pages": [1]})
        await server.call_tool("render_page", {"path": PDF, "page": 1, "dpi": 36})
        await server.call_tool("get_form_fields", {"path": PDF})

    asyncio.run(exercise())

    leaked = sorted(
        name for name in sys.modules
        if name == "PySide6" or name.startswith("PySide6.")
        or name == "shiboken6" or name.startswith("shiboken6.")
        or name == "model.edit_commands"
    )
    print(json.dumps({"leaked": leaked, "modules": len(sys.modules)}))
    '''
)


@pytest.fixture
def child_result(a_pdf):
    """Run the exerciser in a clean interpreter and hand back its JSON verdict."""
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, a_pdf],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=".",
    )
    assert proc.returncode == 0, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    import json

    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_no_qt_reaches_the_server_path(child_result):
    """Every tool has run in a fresh interpreter; Qt must be nowhere in it."""
    assert child_result["leaked"] == []


def test_the_qt_bound_corner_of_model_stays_excluded(child_result):
    """``model/edit_commands.py`` imports ``QUndoCommand``. The server calls ``VirtualDocument``
    operations directly to avoid it — this is what proves the avoidance is real."""
    assert FORBIDDEN_MODEL not in child_result["leaked"]


def test_the_server_path_opens_no_socket(child_result):
    """Reaching here at all means the child's socket guard never fired: no outbound connection, no
    listening port. stdio is the transport, and HTTP is a non-goal, not a deferral.

    (The fixture asserts the child exited 0, which is the whole assertion — a `connect` or `bind`
    would have raised inside it. ``test_the_socket_guard_would_notice_a_connection`` proves the
    guard is not asleep.)
    """
    assert child_result["modules"] > 0


def test_the_socket_guard_would_notice_a_connection(a_pdf):
    """Negative control for the network half: make the child connect on purpose, and watch it die."""
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD + "\nsocket.socket().connect(('127.0.0.1', 9))\n", a_pdf],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=".",
    )
    assert proc.returncode != 0
    assert "the MCP server path called socket.connect" in proc.stderr


def test_the_guard_would_notice_qt(a_pdf):
    """A negative control. A check that cannot fail is not a check — this proves the child's
    detection works by importing Qt on purpose and watching it get caught."""
    proc = subprocess.run(
        [sys.executable, "-c", "import PySide6.QtCore\n" + _CHILD, a_pdf],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=".",
    )
    assert proc.returncode == 0, proc.stderr
    import json

    assert "PySide6" in json.loads(proc.stdout.strip().splitlines()[-1])["leaked"]
