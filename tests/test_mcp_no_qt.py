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

The child exercises every tool before checking — reads *and* writes — because an import that only
happens inside a tool body (``model.edit_engine`` in ``render_page``, ``model.page_edits`` in
``get_form_fields``, ``model.export`` in ``flatten``, ``util.page_range`` in ``split``) is exactly
the one a load-time check would miss.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

# Modules that must never appear. `shiboken6` is PySide6's binding layer and would be the tell if
# something imported a Qt submodule by a path that dodged the PySide6 name.
FORBIDDEN = ("PySide6", "shiboken6")

# The GUI-only corner of model/: it imports QUndoCommand, and excluding it is what lets the rest of
# model/ be shared (PLAN.md §Architecture).
FORBIDDEN_MODEL = "klarpdf.model.edit_commands"

# The *other* library the bridge's lock leaves out (M115). `requirements-mcp.in` says "the bridge
# never uses PyPdfEngine" — true, and until now nothing checked it. The import sits inside
# `PyPdfEngine.materialize` rather than at module level, so `model.edit_engine` loads fine without
# it and a load-time check proves nothing; only reaching that method fails, with
# `ModuleNotFoundError: pypdf`, on a user's machine and never in CI — because CI installs
# `requirements-dev.txt`, which *has* pypdf. Same shape as the version drift M115 fixes: what CI
# runs is not what the bridge ships. Cheap to close here, since this exerciser already runs every
# tool in a clean interpreter.
FORBIDDEN_LIB = "pypdf"

# The core both surfaces share is klarpdf/model/ and klarpdf/util/ less these three files, which
# only the app loads (CLAUDE.md §Two consumers share one core names the same three, M146):
# edit_commands imports Qt, reveal is the scroll-into-view policy of the page view and the Pages
# sidebar, and resources locates the files bundled with the app (the About box's license texts).
APP_ONLY_CORE = frozenset(
    {"klarpdf.model.edit_commands", "klarpdf.util.reveal", "klarpdf.util.resources"}
)

_CHILD = textwrap.dedent(
    '''
    import asyncio, json, sys

    PDF = sys.argv[1]

    # Build the event loop BEFORE the guard is armed, because its self-pipe is asyncio's socket and
    # not ours (M122). The loop wakes itself through an internal `socket.socketpair()`, and on
    # Windows that is not a syscall: CPython emulates it in `socket._fallback_socketpair` with a
    # real loopback `bind()` + `connect()`. Arm the guard first and it fires on *that*, inside
    # `asyncio.run`, before a single tool has run — the invariant never gets tested at all.
    # Constructing the loop up here moves the whole self-pipe out of the guarded window, so what the
    # guard sees afterwards is only ever the server's own doing, on either platform.
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    # Refuse the network before anything is imported: the server is stdio-only, so it must make no
    # outbound connection and bind no port (PLAN.md §Verification).
    #
    # Poison `connect`/`bind`, NOT the socket constructor. A blanket ban on *constructing* sockets
    # would catch the self-pipe on every platform and be testing asyncio rather than us — the loop
    # above sidesteps the same problem from the other end.
    import socket
    def _refuse(what):
        def guard(self, *a, **k):
            raise AssertionError(f"the MCP server path called socket.{what}{a!r}")
        return guard
    socket.socket.connect = _refuse("connect")
    socket.socket.connect_ex = _refuse("connect_ex")
    socket.socket.bind = _refuse("bind")

    from klarpdf.mcp_bridge.server import server

    import os, tempfile
    WORK = tempfile.mkdtemp()
    def out(name):
        return os.path.join(WORK, name)

    async def exercise():
        await server.list_tools()
        # reads
        await server.call_tool("get_info", {"path": PDF})
        await server.call_tool("get_outline", {"path": PDF})
        await server.call_tool("get_links", {"path": PDF})
        await server.call_tool("get_tables", {"path": PDF, "pages": [1]})
        await server.call_tool("search", {"path": PDF, "query": "ALPHA"})
        await server.call_tool("extract_text", {"path": PDF, "pages": [1]})
        await server.call_tool("render_page", {"path": PDF, "page": 1, "dpi": 36})
        await server.call_tool("get_form_fields", {"path": PDF})
        await server.call_tool("get_annotations", {"path": PDF})
        # writes — each pulls its own import chain (model.export, model.page_edits,
        # util.page_range), which is exactly what a load-time-only check would miss
        await server.call_tool("delete_pages", {"path": PDF, "pages": [2], "out": out("d.pdf")})
        await server.call_tool("reorder", {"path": PDF, "order": [3, 2, 1], "out": out("r.pdf")})
        await server.call_tool("rotate", {"path": PDF, "degrees": 90, "out": out("t.pdf")})
        await server.call_tool("split", {"path": PDF, "out_dir": WORK, "ranges": ["1-2"]})
        await server.call_tool("merge", {"paths": [PDF, PDF], "out": out("m.pdf")})
        await server.call_tool(
            "extract_pages", {"path": PDF, "pages": [1, 2], "out": out("x.pdf")}
        )
        await server.call_tool(
            "fill_form", {"path": PDF, "values": {"name": "x"}, "out": out("f.pdf")}
        )
        await server.call_tool("flatten", {"path": PDF, "out": out("fl.pdf")})
        await server.call_tool(
            "set_outline",
            {
                "path": PDF,
                "entries": [{"level": 1, "title": "Top", "page": 1}],
                "out": out("so.pdf"),
                # The fixture ships an outline, so this is the replace path — which is the one
                # worth exercising here anyway: it is the full-rewrite route, where the append
                # route's narrower code path would not reach `set_toc` at all.
                "replace_outline": True,
            },
        )
        # `annotate` reaches model.page_edits' merge/markup path and model.markup_palette — the
        # latter lifted out of viewer/ precisely so this assertion can keep holding (M101)
        await server.call_tool(
            "annotate",
            {
                "path": PDF,
                "marks": [{"type": "highlight", "page": 1, "box": [70, 60, 300, 90]}],
                "out": out("a.pdf"),
            },
        )
        await server.call_tool(
            "export_images", {"path": PDF, "out_dir": WORK, "pages": [1], "dpi": 36}
        )
        # redaction — pulls model.page_edits' destructive path and the Poppler subprocess
        await server.call_tool(
            "redact_text", {"path": PDF, "query": "ALPHA", "out": out("rt.pdf")}
        )
        await server.call_tool(
            "redact_regions",
            {
                "path": PDF,
                "regions": [{"page": 1, "box": [70, 60, 300, 90]}],
                "out": out("rr.pdf"),
            },
        )

    # Not `asyncio.run`: that builds its own loop, which is exactly the self-pipe the guard is now
    # armed against. Run on the one made above, while the guard stays live for every tool call.
    _loop.run_until_complete(exercise())

    leaked = sorted(
        name for name in sys.modules
        if name == "PySide6" or name.startswith("PySide6.")
        or name == "shiboken6" or name.startswith("shiboken6.")
        or name == "klarpdf.model.edit_commands"
        or name == "pypdf" or name.startswith("pypdf.")
    )
    # The desktop app's own code, none of which is in the wheel (M146). Kept out of `leaked`, which
    # is about Qt and pypdf: most of it would show up there as Qt anyway, but three viewer/ modules
    # would not.
    app_code = sorted(
        name for name in sys.modules
        if name.split(".")[0] in (
            "viewer", "organize", "ui", "store",
            "app", "main_window", "launcher", "platform_integration",
        )
    )
    core = sorted(n for n in sys.modules if n.startswith(("klarpdf.model.", "klarpdf.util.")))
    print(json.dumps(
        {"leaked": leaked, "app": app_code, "core": core, "modules": len(sys.modules)}
    ))
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
    """``klarpdf/model/edit_commands.py`` imports ``QUndoCommand``. The server calls ``VirtualDocument``
    operations directly to avoid it — this is what proves the avoidance is real."""
    assert FORBIDDEN_MODEL not in child_result["leaked"]


def test_the_bridge_never_reaches_the_pypdf_engine(child_result):
    """``requirements-mcp.in`` leaves pypdf out because "the bridge never uses PyPdfEngine" — and
    until M115 nothing checked that the claim stayed true.

    It cannot be checked at load time: ``model/edit_engine.py`` imports pypdf *inside*
    ``PyPdfEngine.materialize``, so the module loads without it and only reaching that method fails.
    On a bridge user's machine that is ``ModuleNotFoundError: pypdf``; in CI it never fails at all,
    because CI installs ``requirements-dev.txt``, which carries pypdf for the app. Running every
    tool in a clean interpreter — which this exerciser already does — is the only honest check.
    """
    assert FORBIDDEN_LIB not in child_result["leaked"]


def test_nothing_of_the_apps_own_code_reaches_the_server_path(child_result):
    """``viewer/``, ``organize/``, ``ui/``, ``store/`` and the app's top-level modules are not in
    the wheel (``tests/test_mcp_packaging.py``), so an installed bridge cannot import them (M146).

    Most of them need Qt and would fail the Qt check first, but not all: ``viewer/links.py``,
    ``pixmap_cache.py`` and ``tools.py`` import none. The repo root is on ``sys.path`` here, so an
    import of one inside a tool body would pass every other test in the suite and fail only on a
    user's machine, as ``ModuleNotFoundError`` when that tool is called. CI's installer job starts
    the installed server but calls no tool, so it would not see it either.
    """
    assert child_result["app"] == [], (
        f"the bridge loaded the desktop app's own code: {child_result['app']}. Move what it needs "
        "into klarpdf/model/, as M101 did with the markup palette (CLAUDE.md §Two consumers share "
        "one core)."
    )


def test_the_bridge_loads_the_whole_core_but_three_app_only_files(child_result):
    """CLAUDE.md's "core" (what a ``core`` issue is, and what owes a test on both surfaces) is
    ``klarpdf/model/`` and ``klarpdf/util/`` less ``APP_ONLY_CORE``. This keeps that true (M146).

    It can go wrong silently in either direction. If the bridge starts using one of the three, a
    change to that file reaches the bridge while the docs still call it app-only. If a new file
    there is used by the app alone, it reads as core and asks for bridge tests that cannot reach
    it, the same mistake as the rule naming ``viewer/`` and ``organize/`` as shared. So equality,
    not containment.
    """
    klarpdf_dir = pathlib.Path(__file__).resolve().parent.parent / "klarpdf"
    in_core_dirs = {
        f"klarpdf.{folder}.{path.stem}"
        for folder in ("model", "util")
        for path in (klarpdf_dir / folder).glob("*.py")
        if path.stem != "__init__"
    }
    unloaded = in_core_dirs - set(child_result["core"])
    assert unloaded == APP_ONLY_CORE, (
        f"listed as app-only but now loaded by the bridge: {sorted(APP_ONLY_CORE - unloaded)}; "
        f"not loaded by the bridge and not listed: {sorted(unloaded - APP_ONLY_CORE)} (if a tool "
        "does use it, the exerciser is not reaching it). Update APP_ONLY_CORE and the list in "
        "CLAUDE.md §Two consumers share one core together."
    )


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


def test_the_exerciser_covers_every_registered_tool():
    """The child's tool list is written by hand, and nothing made it stay complete.

    Everything above is only as strong as the exerciser: an import that happens *inside* a tool body
    is invisible until that tool runs, which is the whole reason the child calls them all. So a tool
    added without a line in ``_CHILD`` is not covered by the no-Qt, no-pypdf or no-socket assertions
    — silently, because every one of them still passes on the twenty-two that are there.

    ``tests/test_mcp_server.py`` pins the *registry* against ``EXPECTED_TOOLS``, so tool number
    twenty-three cannot register unnoticed; it says nothing about this file. This is the other half: the
    registry is the source of truth, and the exerciser must match it exactly. Equality rather than
    containment, so a line left behind for a removed tool fails here too.
    """
    import asyncio
    import re

    from klarpdf.mcp_bridge.server import server as live_server

    exercised = set(re.findall(r'call_tool\(\s*"(\w+)"', _CHILD))
    registered = {tool.name for tool in asyncio.run(live_server.list_tools())}
    assert exercised == registered, (
        "the no-Qt/no-socket exerciser has drifted from the registry — "
        f"never exercised: {sorted(registered - exercised)}; "
        f"exercised but not registered: {sorted(exercised - registered)}"
    )


def test_the_guard_would_notice_qt(a_pdf):
    """A negative control. A check that cannot fail is not a check — this proves the child's
    detection works by importing Qt on purpose and watching it get caught."""
    # Needs the toolkit it is proving we can detect. Absent under the `bridge` job's lock, where
    # the *positive* checks above still run and are the ones that matter there (M115.1).
    pytest.importorskip("PySide6", reason="the bridge lock has no Qt; this control needs it")
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


def test_the_guard_would_notice_app_code_that_needs_no_qt(a_pdf):
    """The negative control for the app-code check, using the case it exists for: ``viewer.links``
    loads no Qt, so the Qt check passes and only the app-code check can catch it. Runs under the
    bridge's lock too, which has no PySide6 but has everything ``viewer/links.py`` imports."""
    proc = subprocess.run(
        [sys.executable, "-c", "import viewer.links\n" + _CHILD, a_pdf],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=".",
    )
    assert proc.returncode == 0, proc.stderr
    import json

    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "viewer.links" in result["app"]
    assert result["leaked"] == [], "viewer.links was meant to be the case the Qt check cannot see"
