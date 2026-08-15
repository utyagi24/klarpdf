"""The stdio MCP server: one tool per row of PLAN.md's tool table, over ``queries.py``.

Deliberately thin. Every function here converts arguments, calls one helper, and shapes the result
— the PDF behaviour lives in :mod:`mcp_bridge.queries`, which has no SDK import and is tested by
calling it. What remains in this file is the part only a protocol can get wrong: tool names,
schemas, docstrings (the model reads these to choose a tool), and the transport.

**stdio only.** Streamable HTTP is a non-goal, not a deferral: stdio is what Claude Code, Claude
Desktop, Codex CLI, Cursor and VS Code all accept as ``command`` + ``args``, while a listening port
would contradict the product's no-network guarantee and attach AGPL §13 remote-interaction
obligations that a subprocess never triggers (PLAN.md §Architecture).

The docstrings below are the tool descriptions the client shows the model, so they are written for
that reader: what the tool answers, when to reach for it, and what the numbers mean.

**Why the list-returning tools return a dict.** The SDK serialises a bare ``list`` into one content
block *per element* — a 500-hit search would arrive as 500 separate blocks. Wrapping the list in
``{"count": …, "<items>": [...]}`` makes it one block, and hands the caller the total before it
reads the items, which is the number that decides whether to narrow the query. It is also where
M43's return-size caps will report a truncation.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.mcpserver import Image

from mcp_bridge import queries
from version import __version__

INSTRUCTIONS = """\
KlarPDF exposes a local, offline PDF engine — reading, and (from M40) lossless page transforms and
verified redaction. Nothing here touches the network.

Route with the cheap tools first. `get_info` tells you the page count, whether the file has a text
layer at all, and whether it is encrypted; `get_outline` and `search` locate the part you want.
Reach for `extract_text` on specific pages once you know which, and `render_page` only when the
text layer cannot answer the question (scans, figures, layout, signatures).

Page numbers are 1-based everywhere, matching what a reader sees.
"""

server = MCPServer(
    name="klarpdf",
    title="KlarPDF",
    version=__version__,
    instructions=INSTRUCTIONS,
)


@server.tool()
def get_info(path: str, password: str | None = None) -> dict:
    """Summarise a PDF without loading its content: page count, file size, page sizes, whether it
    is encrypted, whether it has an outline, and whether it has a text layer.

    Call this first. It is the cheapest way to decide what to do next, and it answers the question
    that changes everything else — a document with no text layer is a scan, so `search` and
    `extract_text` will come back empty and `render_page` is the only way in.

    `has_text_layer` is sampled over the first 20 pages, so `false` means "no text that far in".
    An encrypted file with no password returns `needs_password: true` rather than failing.
    """
    return queries.document_info(path, password)


@server.tool()
def get_outline(path: str, password: str | None = None) -> dict:
    """The document's outline (bookmarks) as `entries`, a flat list of `{level, title, page}` in
    document order, with `level` giving the nesting depth (1 = top), plus a `count`.

    The fastest way to find the section you want in a long structured document — cheaper and more
    reliable than searching for a heading. `count` is 0 if the PDF has no outline.
    """
    entries = queries.outline(path, password)
    return {"count": len(entries), "entries": entries}


@server.tool()
def search(
    path: str,
    query: str,
    match_case: bool = False,
    whole_words: bool = False,
    password: str | None = None,
) -> dict:
    """Find text across the whole document. Returns a `count` and `hits` — one entry per match with
    `page`, a `snippet` of the surrounding line, and the `box` it occupies in page points.

    Use this to locate content before extracting it, and to check what a phrase actually matches
    before acting on it — the snippet is what tells you a search for "Smith" also caught
    "Smithsonian".

    With `whole_words` off (the default) the query is a list of words and any one of them matches,
    including inside longer words. With it on, the query is a single phrase and neither end may sit
    inside a longer word. `match_case` filters against the text under each hit.
    """
    hits = queries.search(
        path, query, match_case=match_case, whole_words=whole_words, password=password
    )
    return {"count": len(hits), "hits": hits}


@server.tool()
def extract_text(
    path: str, pages: list[int] | None = None, password: str | None = None
) -> dict:
    """Extract the text of specific pages (1-based). Omit `pages` for the whole document.

    Prefer naming the pages you need — that is the point of this server over reading the file
    directly. Use `search` or `get_outline` to find them first.
    """
    return queries.extract_text(path, pages, password)


@server.tool()
def render_page(path: str, page: int, dpi: int = 150, password: str | None = None) -> Image:
    """Render one page (1-based) to a PNG image at `dpi` (default 150).

    For what the text layer cannot tell you: scanned pages, figures, tables whose structure matters,
    signatures, stamps, and any question about layout or appearance. Higher `dpi` costs
    proportionally more — 150 reads body text comfortably, 300 is for fine print.
    """
    result = queries.render_page(path, page, dpi, password)
    return Image(data=result["png"], format="png")


@server.tool()
def get_form_fields(path: str, password: str | None = None) -> dict:
    """List the fillable form fields as `fields`: `name`, `type`, the 1-based `page`, the widget
    `rect`, any `choices`, and the current `value`. Plus a `count`.

    One entry per occurrence — a field that appears on several pages is listed several times under
    the same `name` and shares one value.
    """
    fields = queries.form_fields(path, password)
    return {"count": len(fields), "fields": fields}


def main() -> None:
    """Console-script entry point (`klarpdf-mcp`). Serves MCP over stdio until the client closes."""
    server.run("stdio")


if __name__ == "__main__":
    main()
