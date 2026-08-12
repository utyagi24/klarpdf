"""KlarPDF's MCP (Model Context Protocol) server — the agent-facing surface.

A **quarantined seam**, the same pattern as ``packaging/`` and ``platform_integration.py``: the GUI
never imports this package, and this package never imports the GUI. It reuses the Qt-free half of
``model/`` — ``virtual_document``, ``edit_engine``, ``export``, ``page_edits``, ``page_text``,
``links_remap``, ``toc_remap`` — and deliberately **not** ``model/edit_commands.py``, which imports
``QUndoCommand``. That exclusion is the whole architecture: ``tests/test_mcp_no_qt.py`` asserts in a
subprocess that PySide6 never reaches ``sys.modules`` after every tool has run, so "reuses the
GUI-free core" is verified rather than believed (PLAN.md §MCP / Agent Bridge roadmap → Architecture).

**Why the directory is not called ``mcp/``.** PLAN.md named it that, and it cannot be: the official
SDK this server is built on *is* the top-level module ``mcp``. The app runs with the repo root on
``sys.path`` (``pyproject.toml`` sets ``pythonpath = ["."]`` for pytest, and a script's directory
goes on the path automatically), so a local ``mcp/`` package shadows the installed one and
``from mcp.server import MCPServer`` fails with ``ModuleNotFoundError: No module named 'mcp.server'``
— measured, not theorised. ``mcp_bridge`` is the name; the roadmap's ``mcp/`` was written before the
SDK was installed.

Layout:

* ``queries.py`` — the read-only helpers, plain Python over ``model/``, returning JSON-ready dicts.
  No SDK import, so the PDF logic is testable without a server.
* ``server.py`` — the thin adapter: an ``MCPServer`` with one ``@server.tool()`` per entry in
  PLAN.md's tool table, plus ``main()`` for the ``klarpdf-mcp`` console script.
"""
