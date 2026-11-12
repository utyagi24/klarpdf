"""Shipped code vulture calls unused, kept on purpose, each with its reason (M169, #409).

``tests/test_no_dead_code.py`` fails on a finding that is not listed here, and on an entry here that
vulture no longer reports, so this list can neither hide new dead code nor outlive the code it
names. Keys are ``(path, name)``: a name is kept in the one file that holds it, not everywhere.

What earns an entry (owner, #409 and M169's session):

* **Something outside the code calls it.** Qt calls an override by name, and the MCP SDK reads an
  extension's members. Vulture cannot see either caller.
* **A read-only accessor the tests observe state through.** Deleting one would only make the tests
  read the private attribute behind it, which ties them more tightly to the internals, not less.

Code that only tests *run*, rather than read, is not kept: M169 deleted it and pointed its tests at
the code the app runs. MCP tools need no entry: they are registered with ``@server.tool``, which
``test_no_dead_code.py`` tells vulture to ignore, so a new tool is covered the day it is added.
"""

from __future__ import annotations

KEEP: dict[tuple[str, str], str] = {
    # ---- called by Qt, by name ------------------------------------------------------------------
    ("edit_commands.py", "mergeWith"):
        "QUndoCommand override: QUndoStack calls it to merge an auto-repeated nudge into one step",
    ("organize/thumbnail_panel.py", "startDrag"):
        "QAbstractItemView override: Qt calls it when a drag starts in the Pages sidebar",
    ("organize/thumbnail_panel.py", "supported_actions"):
        "the argument Qt passes to startDrag; the override builds its own drag and ignores it",
    ("ui/popup_closer.py", "eventFilter"):
        "QObject override: Qt calls it for every event once the filter is installed on the app",
    # ---- read by the MCP SDK --------------------------------------------------------------------
    ("klarpdf/mcp_bridge/server.py", "identifier"):
        "the SDK reads an extension's identifier to advertise it in capabilities.extensions",
    ("klarpdf/mcp_bridge/server.py", "intercept_tool_call"):
        "the SDK calls this extension hook around every tool call (strict-argument checking)",
    ("klarpdf/mcp_bridge/server.py", "__signature__"):
        "inspect.signature reads it, so the SDK builds each tool's schema from the wrapped function",
    # ---- read-only accessors the tests observe state through ------------------------------------
    ("klarpdf/model/virtual_document.py", "metadata_is_removed"):
        "the tests read through it whether the metadata is set to be removed (an empty override)",
    ("main_window.py", "_a_objects"):
        "the handle the tests trigger the Objects-mode action through, as _a_select is used live",
    ("ui/icons.py", "DOC_ICON"):
        "names the .pdf document icon's SVG; the app never draws it, but tests/test_icons.py checks "
        "through it the file packaging/app/make_icon.py builds klarpdf-doc.ico from",
    ("viewer/text_format_bar.py", "text_style"):
        "the tests read the style the format bar holds through it. It was `style()` until M173, a name"
        " Qt widgets also have, which hid from this check that only tests call it",
    ("viewer/annotations.py", "current_style"):
        "the tests read the text-box style the next new box will carry through it",
    ("viewer/annotations.py", "selected_object"):
        "the tests read the single selected object through it",
    ("viewer/note_editor.py", "is_read_only"):
        "the tests read whether the open note is read-only (a foreign author's) through it",
    ("viewer/pdf_view.py", "night_mode"):
        "the tests read whether night mode is on through it",
    ("viewer/pdf_view.py", "page_layout"):
        "the tests read the single or facing page layout through it",
    ("viewer/pixmap_cache.py", "total_bytes"):
        "the tests read the cache's size against its budget through it",
}
