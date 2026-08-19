"""The stdio MCP server: one tool per row of PLAN.md's tool table, over the helper modules.

Deliberately thin. Every tool converts arguments, applies the server's policy, calls one helper, and
shapes the result — the PDF behaviour lives in :mod:`mcp_bridge.queries`,
:mod:`mcp_bridge.transforms` and :mod:`mcp_bridge.redaction`, none of which import the SDK. What
remains here is the part only a protocol can get wrong: tool names, schemas, docstrings (the model
reads these to choose a tool), the transport, and the three server-wide policies from
:mod:`mcp_bridge.config`.

**stdio only.** Streamable HTTP is a non-goal, not a deferral: stdio is what Claude Code, Claude
Desktop, Codex CLI, Cursor and VS Code all accept as ``command`` + ``args``, while a listening port
would contradict the product's no-network guarantee and attach AGPL §13 remote-interaction
obligations that a subprocess never triggers (PLAN.md §Architecture).

**Nothing may ever print to stdout.** stdout *is* the protocol — a stray ``print`` corrupts the JSON
-RPC stream and the client drops the connection with no useful error. Diagnostics go to stderr.

**Why the tools are built by a factory.** ``--read-only`` has to *withhold* the write tools, not
refuse them when called: a tool the model can see is a tool it will try, and a server that lists
sixteen tools and errors on ten is worse than one that lists six. So registration is conditional,
which means it happens inside :func:`create_server` rather than at import time.

**Why the list-returning tools return a dict.** The SDK serialises a bare ``list`` into one content
block *per element* — a 500-hit search would arrive as 500 blocks. Wrapping the list in
``{"count": …, "<items>": [...]}`` makes it one block, and hands the caller the total before it
reads the items, which is the number that decides whether to narrow the query. It is also where the
size caps report a truncation.
"""

from __future__ import annotations

import argparse
import functools
import inspect
import os
import sys
from typing import Any

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.extension import Extension
from mcp.server.mcpserver import Image
from mcp_types import CallToolRequestParams, CallToolResult, TextContent

from mcp_bridge import queries, redaction, transforms
from mcp_bridge.config import Config, PathPolicy
from mcp_bridge.strict_args import rejection_message, unknown_parameters
from model.virtual_document import PasswordRequired
from version import __version__

INSTRUCTIONS = """\
KlarPDF exposes a local, offline PDF engine: reading, page transforms, and verified redaction.
Nothing here touches the network.

Route with the cheap tools first. `get_info` tells you the page count, whether the file has a text
layer at all, and whether it is encrypted; `get_outline` and `search` locate the part you want.
Reach for `extract_text` on specific pages once you know which, and `render_page` only when the
text layer cannot answer the question (scans, figures, layout, signatures).

The write tools never touch the document you give them. Each one takes an explicit `out` path,
writes a new file, and refuses if `out` is the input or an existing file (pass `overwrite: true` if
replacing one is intended).

What survives a write depends on whether the **page set** changed. A tool that leaves every page in
place — `fill_form`, `flatten`, the redactions — edits a copy of the original, so everything the
document holds comes through, including its accessibility structure tree and its encryption. A tool
that moves pages — `reorder`, `delete_pages`, `extract_pages`, `split`, `merge` — builds a new
document: the text layer, annotations and form fields come with the pages and the bookmarks and
internal links are re-pointed, but the structure tree, `/Perms`, the `/Names` tree and encryption do
not survive the move. Say so if that matters for the file in hand — a tagged form reordered is no
longer tagged.

`redact_text` and `redact_regions` DELETE content and verify the result before reporting success.
Preview with `search` first and show the user what matched.

Page numbers are 1-based everywhere, matching what a reader sees. Large results are capped rather
than truncated silently: if a reply carries `truncated: true`, narrow the request.
"""


def _explain(exc: Exception) -> Exception:
    """Turn an internal failure into something an agent can act on.

    The SDK wraps whatever a tool raises into a `ToolError` carrying its `str()`, so the message is
    the entire interface at that point. A bare `PasswordRequired('/path/x.pdf')` tells the model
    nothing about what to do next; the rewrite does.
    """
    if isinstance(exc, PasswordRequired):
        return ValueError(
            f"{exc.args[0] if exc.args else 'the document'} is encrypted and the password was "
            "missing or wrong. Call again with `password`; `get_info` reports "
            "`needs_password: true` without one."
        )
    if isinstance(exc, FileNotFoundError):
        return ValueError(f"no such file: {exc.filename or exc}")
    if isinstance(exc, IsADirectoryError):
        return ValueError(f"{exc.filename or exc} is a directory, not a PDF")
    return exc


class StrictArguments(Extension):
    """Reject a `tools/call` carrying an argument name the tool does not declare (M106).

    **Why this is an extension and not a `guarded`-style wrapper.** The drop happens inside the
    SDK: `func_metadata.create_model(..., __base__=ArgModelBase)` builds each tool's argument model
    with no `extra=`, so pydantic's default `extra="ignore"` applies and `model_validate` deletes
    unknown keys *before the tool function is ever called*. Nothing wrapping the function can see
    them. The check has to sit above that validation, and there is no knob to turn it off — the
    base class is hardcoded.

    **Why the extension seam and not `Server.middleware`.** PLAN.md §M106 scoped this to middleware
    and flagged the cost: that API carries a `TODO(L54): provisional — signature and semantics
    change with the Context/middleware rework before v2 final`. It turns out not to be needed.
    `intercept_tool_call` wraps the *handler*, which is below the runner's `CallToolRequestParams`
    validation but still above the per-tool argument model — and `CallToolRequestParams.arguments`
    is a plain `dict[str, Any]`, so the unknown keys are all still there (measured). That buys the
    same visibility from a documented, non-provisional API, and a short-circuit return is "sieved
    and stamped exactly like the wrapped handler's", so the rejection reaches the agent in the same
    shape as every other tool error rather than as a JSON-RPC protocol error.

    The cost is one line in `capabilities.extensions`, which is an honest description of a server
    that does check its arguments strictly.
    """

    identifier = "io.klarpdf/strict-arguments"

    def __init__(self) -> None:
        self._server: MCPServer | None = None
        self._accepted: dict[str, list[str]] | None = None

    def bind(self, server: MCPServer) -> None:
        """Point the extension at the server whose tools it guards.

        A setter rather than a constructor argument because `MCPServer` takes its extensions at
        construction, so the extension has to exist first. It may be called immediately afterwards
        and before any tool is registered: nothing is read here.
        """
        self._server = server

    async def accepted(self, tool: str) -> list[str] | None:
        """The parameter names `tool` declares, in signature order, or `None` if it has no schema.

        Read from the tool's own published input schema rather than a list kept here, so a tool
        that gains an argument cannot fall out of step with its guard. Cached on first use: the
        tool set is fixed once `create_server` returns, and rebuilding seventeen pydantic models
        per call to read their keys would be wasted work.
        """
        if self._accepted is None:
            if self._server is None:  # pragma: no cover — a wiring bug, not a reachable state
                raise RuntimeError("StrictArguments.bind() was never called; the guard is unarmed")
            self._accepted = {
                tool_info.name: list((tool_info.input_schema or {}).get("properties", {}))
                for tool_info in await self._server.list_tools()
                # A schema that admits extras means the tool wants them; do not second-guess it.
                if not (tool_info.input_schema or {}).get("additionalProperties")
            }
        return self._accepted.get(tool)

    async def intercept_tool_call(
        self,
        params: CallToolRequestParams,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        """Fail closed on an unrecognised name; otherwise leave the call untouched.

        Annotated to match the base signature exactly, so an SDK bump that changes the contract is
        a diff here rather than a silently unbound override.
        """
        given = list(params.arguments or {})
        accepted = await self.accepted(params.name)
        # `accepted is None` means the tool is not registered (or opted out above): let the SDK
        # answer for it, so an unknown *tool* keeps reporting as an unknown tool.
        if accepted is None or not given:
            return await call_next(ctx)
        unknown = unknown_parameters(accepted, given)
        if not unknown:
            return await call_next(ctx)
        message = rejection_message(params.name, accepted, unknown)
        return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def create_server(config: Config | None = None) -> MCPServer:
    """Build a server with ``config``'s policies applied. See the module docstring for why."""
    cfg = config or Config()
    check = cfg.policy.check
    limits = cfg.limits

    strict = StrictArguments()
    server = MCPServer(
        name="klarpdf",
        title="KlarPDF",
        version=__version__,
        extensions=[strict],
        instructions=INSTRUCTIONS
        + (
            "\nThis server is running READ-ONLY: the transform and redaction tools are not "
            "available.\n"
            if cfg.read_only
            else ""
        ),
    )
    # Safe before a single tool is registered: `bind` stores the reference and nothing else, and
    # the schemas are not read until the first call arrives. Binding here rather than on the way
    # out is what keeps it correct — `create_server` has two exits, and the `--read-only` one had
    # already been missed once.
    strict.bind(server)

    def guarded(function):
        """Translate internal exceptions at the tool boundary.

        ``__signature__`` is copied explicitly, not just via ``functools.wraps``: the SDK builds
        each tool's JSON schema by introspecting the callable, so a bare ``*args, **kwargs``
        wrapper silently produces a schema with two required parameters called `args` and `kwargs`
        and every real argument gone. It fails at *call* time with a pydantic validation error,
        which is a long way from the cause.
        """

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — re-raised, possibly reworded
                raise _explain(exc) from exc

        wrapper.__signature__ = inspect.signature(function)
        return wrapper

    # ---- query / route -------------------------------------------------------

    @server.tool()
    @guarded
    def get_info(path: str, password: str | None = None) -> dict:
        """Summarise a PDF without loading its content: page count, file size, page sizes, whether
        it is encrypted and what it permits, whether it has an outline, and whether it has a text
        layer.

        Call this first. It is the cheapest way to decide what to do next, and it answers the
        question that changes everything else — a document with no text layer is a scan, so `search`
        and `extract_text` will come back empty and `render_page` is the only way in.

        `has_text_layer` is sampled over the first 20 pages, so `false` means "no text that far in".

        `encrypted` describes the **file**, not this call: it is `true` both for a file that needs a
        password to open (`needs_password: true` — call again with `password`) and for one that
        opens freely but restricts what may be done with it, which is the usual shape for a
        published form. `permissions` names those restrictions (`copy`, `modify`, `assemble`, …);
        they are advisory, honoured by most viewers and enforced by nothing but the password, so
        treat a `false` as what the document asks for rather than as a wall. Tell the user when
        they are about to act against one.
        """
        return queries.document_info(check(path), password)

    @server.tool()
    @guarded
    def get_outline(path: str, password: str | None = None) -> dict:
        """The document's outline (bookmarks) as `entries`, a flat list of `{level, title, page}` in
        document order, with `level` giving the nesting depth (1 = top), plus a `count`.

        The fastest way to find the section you want in a long structured document — cheaper and
        more reliable than searching for a heading. `count` is 0 if the PDF has no outline.
        """
        entries = queries.outline(check(path), password)
        return {"count": len(entries), "entries": entries}

    @server.tool()
    @guarded
    def search(
        path: str,
        query: str,
        match_case: bool = False,
        whole_words: bool = False,
        password: str | None = None,
    ) -> dict:
        """Find text across the whole document. Returns a `count` and `hits` — one entry per
        occurrence with `page`, a `snippet` of the surrounding text, and `boxes`, the rectangles it
        occupies in page points.

        Use this to locate content before extracting it, and to check what a phrase actually matches
        before acting on it — the snippet is what tells you a search for "Smith" also caught
        "Smithsonian". Always run it before `redact_text`.

        With `whole_words` off (the default) the query is a list of words and any one of them
        matches, including inside longer words. With it on, the query is a single phrase and neither
        end may sit inside a longer word. `match_case` filters against the text under each hit.

        **"Word" means a run of characters between spaces**, so `whole_words: true` will not find a
        query buried inside a longer unbroken run: searching `220885-1063303` with it on misses
        `<AccountNumber:220885-1063303>` entirely, because that whole tag is one word. Machine tags,
        filenames, URLs and `key:value` pairs are all this shape. When a query is a single token
        with no spaces — an account number, a reference, an ID — `whole_words: false` is usually the
        right choice and cannot over-match, since a one-word query makes the two modes differ only
        in the boundary rule.

        Each hit also carries `invisible`. When true, the text is in the file but is not drawn on
        the page — white-on-white, transparent, or painted over — so it will not appear in
        `render_page` and the reader cannot see it. Tell the user: it is where sensitive values
        hide, and it is invisible to every check they could run themselves.

        `boxes` is normally a single rectangle. A phrase that wraps a line break occupies one on
        **each** line, the way a find bar highlights a wrapped match, and all of them come back
        under the one hit — so `count` counts occurrences, not fragments. Redact every box of a
        hit: clearing only the first leaves the tail of the phrase legible. Passing the whole hit
        to `redact_regions` as `{"page": hit["page"], "boxes": hit["boxes"]}` does that for you.
        """
        hits = queries.search(
            check(path),
            query,
            match_case=match_case,
            whole_words=whole_words,
            password=password,
        )
        total = len(hits)
        capped = hits[: limits.max_search_hits]
        result = {"count": len(capped), "hits": capped}
        if total > len(capped):
            result["truncated"] = True
            result["total_matches"] = total
            result["note"] = (
                f"{total} matches found, first {len(capped)} returned. Narrow the query, or set "
                "`whole_words` to stop matching inside longer words."
            )
        return result

    @server.tool()
    @guarded
    def extract_text(
        path: str, pages: list[int] | None = None, password: str | None = None
    ) -> dict:
        """Extract the text of specific pages (1-based). Omit `pages` for the whole document.

        Prefer naming the pages you need — that is the point of this server over reading the file
        directly. Use `search` or `get_outline` to find them first. A very large request is cut off
        at a character budget and reports `truncated: true` with the pages that made it.
        """
        result = queries.extract_text(check(path), pages, password)
        budget = limits.max_text_chars
        kept: list[dict] = []
        used = 0
        for page in result["pages"]:
            if used + len(page["text"]) > budget:
                break
            kept.append(page)
            used += len(page["text"])
        if len(kept) < len(result["pages"]):
            dropped = [p["page"] for p in result["pages"][len(kept) :]]
            result["truncated"] = True
            result["pages_omitted"] = dropped
            result["note"] = (
                f"Stopped at {used} characters ({limits.max_text_chars} budget). "
                f"Pages {dropped[0]}-{dropped[-1]} were not returned — ask for them directly."
            )
            result["pages"] = kept
        return result

    # `structured_output=False` because the return is an image block, not JSON. Without it the SDK
    # tries to build a pydantic output schema from the `-> Image` annotation and fails at import;
    # it only worked before `guarded` existed because an undecorated function's annotations stay
    # unresolved strings under `from __future__ import annotations`, and the wrapper resolves them.
    @server.tool(structured_output=False)
    @guarded
    def render_page(
        path: str,
        page: int,
        dpi: int = 150,
        password: str | None = None,
        clip: list[float] | None = None,
    ) -> Image:
        """Render one page (1-based) to a PNG image at `dpi` (default 150).

        For what the text layer cannot tell you: scanned pages, figures, tables whose structure
        matters, signatures, stamps, and any question about layout or appearance. Higher `dpi` costs
        proportionally more — 150 reads body text comfortably, 300 is for fine print.

        `clip` renders just one region: `[x0, y0, x1, y1]` in page points, the same coordinates
        `search` reports hits in — so you can pass a hit's box straight back to see the actual
        pixels of a match. Use it before `redact_text` to show someone what is about to be deleted,
        and to read a stamp, signature or table cell at high `dpi` without paying for the whole
        page. A `search` hit carries `boxes` (one per line, so a match that wraps has several):
        pass one, or their bounding box to see the whole match at once. A clip running off the edge
        of the page is an error naming the page's rect, not a quietly smaller image.

        On a **rotated** page, `clip` is in the same unrotated coordinates `search` reports and
        `redact_regions` consumes, so a hit still feeds straight back; the image you get is the
        region as displayed, with its width and height swapped for a quarter turn. Note that
        `get_info.page_sizes` reports *displayed* dimensions, so a box may legitimately extend past
        the width shown there.
        """
        result = queries.render_page(check(path), page, dpi, password, clip=clip)
        if len(result["png"]) > limits.max_image_bytes:
            raise ValueError(
                f"the rendered page is {len(result['png']) // 1024} KiB, over the "
                f"{limits.max_image_bytes // 1024} KiB limit — render it at a lower dpi "
                f"(you asked for {dpi})."
            )
        return Image(data=result["png"], format="png")

    @server.tool()
    @guarded
    def get_form_fields(path: str, password: str | None = None) -> dict:
        """List the fillable form fields as `fields`: `name`, `type`, the 1-based `page`, the widget
        `rect`, any `choices`, and the current `value`. Plus a `count`.

        One entry per occurrence — a field that appears on several pages is listed several times
        under the same `name` and shares one value.

        Each entry also carries what it takes to *fill* it. For a checkbox or radio button,
        `on_state` is the value that ticks **that** widget and `states` lists every value it
        accepts: these are per-widget, not a convention — one form here uses `"1"` on one box and
        `"2"` on another, and `"Yes"` on neither. (`fill_form` also takes plain `true`/`false` and
        resolves the on-state for you.) `read_only`, `required`, `multiline` and `max_len` describe
        the field: a `read_only` field is form plumbing, not something to offer the user — real
        forms carry 3-pt slivers that are indistinguishable from real fields without it.
        """
        fields = queries.form_fields(check(path), password)
        return {"count": len(fields), "fields": fields}

    if cfg.read_only:
        return server

    # ---- transforms: every one writes a NEW file and leaves the input untouched ----

    @server.tool()
    @guarded
    def delete_pages(
        path: str,
        pages: list[int],
        out: str,
        password: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Write a copy of the document with `pages` (1-based) removed.

        Bookmarks pointing at a deleted page are dropped and the survivors re-point at their new
        positions, so the outline stays correct rather than dangling. Refuses to delete every page.
        """
        return transforms.delete_pages(
            check(path), pages, check(out), password=password, overwrite=overwrite
        )

    @server.tool()
    @guarded
    def reorder(
        path: str,
        order: list[int],
        out: str,
        password: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Write a copy with the pages in the sequence `order` — a full permutation of 1..N.

        `order: [3, 1, 2]` puts page 3 first. Every page must appear exactly once; to drop pages use
        `delete_pages` instead. Text, form fields and remapped bookmarks all survive the move.
        """
        return transforms.reorder(
            check(path), order, check(out), password=password, overwrite=overwrite
        )

    @server.tool()
    @guarded
    def rotate(
        path: str,
        degrees: int,
        out: str,
        pages: list[int] | None = None,
        password: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Turn `pages` (1-based; omit for all) by `degrees` — a multiple of 90.

        `degrees` is a **delta** applied to each page's current rotation, so 90 is a quarter turn
        clockwise from wherever the page already sits and calling it twice gives 180.
        """
        return transforms.rotate(
            check(path),
            degrees,
            check(out),
            pages=pages,
            password=password,
            overwrite=overwrite,
        )

    @server.tool()
    @guarded
    def split(
        path: str,
        out_dir: str,
        ranges: list[str] | None = None,
        password: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Split the document into several PDFs written into `out_dir`.

        `ranges` are print-dialog page-range strings — `["1-3", "4", "5-"]` gives three files; `"5-"`
        means page 5 to the end. Omit `ranges` to write one file per page. Each part keeps its text
        layer, its form fields, and the bookmarks whose targets landed in it.
        """
        return transforms.split(
            check(path), check(out_dir), ranges=ranges, password=password, overwrite=overwrite
        )

    @server.tool()
    @guarded
    def extract_pages(
        path: str,
        pages: list[int],
        out: str,
        password: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Write `pages` (1-based) to a single new PDF — "give me pages 10-20 as a file".

        Use this whenever someone asks to **extract**, **pull out**, or **save a few pages as their
        own document**. `split` is for cutting a document into several files at once; this is for
        taking one piece out of it, and it lets you name the output.

        The text layer, form fields and annotations come with the pages, and bookmarks and internal
        links are re-pointed at the extracted page numbers instead of dangling. What does not come
        is what a PDF keeps at the document level: an extract of a tagged document is not tagged,
        and an extract of an encrypted one is not encrypted.
        """
        return transforms.extract_pages(
            check(path), pages, check(out), password=password, overwrite=overwrite
        )

    @server.tool()
    @guarded
    def merge(paths: list[str], out: str, overwrite: bool = False) -> dict:
        """Concatenate two or more PDFs into one, in the order given.

        Form fields with colliding names are renamed rather than dropped, so merging two documents
        that both have a field called `name` leaves you with two working fields.
        """
        return transforms.merge([check(p) for p in paths], check(out), overwrite=overwrite)

    @server.tool()
    @guarded
    def fill_form(
        path: str,
        values: dict,
        out: str,
        password: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Fill AcroForm fields and write the filled copy. The fields stay editable afterwards.

        `values` maps field name to value. Call `get_form_fields` first: an unknown field name is an
        error, not a silent no-op, so a typo cannot report success while writing nothing. A field
        that appears on several pages is filled on all of them.

        For a checkbox or radio button, send `true` / `false` and the widget's own on-state is
        looked up for you — or send that export value (`get_form_fields` reports it as `on_state`)
        if you would rather be explicit. Anything else is an **error** naming the states the widget
        accepts, and nothing is written: a state the button does not have used to be resolved as
        "off", so asking to tick a box with `"3"` on a form whose states are `"1"` and `"2"` cleared
        it and reported success.

        Filling a field the document marks **read-only** is allowed — you may be stamping a value
        into a signature line deliberately — but it comes back in `warnings`, because a reader
        cannot edit or clear it afterwards.

        The page set does not change, so the output keeps everything the original held: its tags,
        its encryption and permissions, its links. The exception is an **XFA** (LiveCycle) form,
        which stores a second copy of its values as XML: only the AcroForm side is filled, and the
        result then carries `warnings` and an `xfa` block saying so. Pass that on to the user —
        the page looks right, but a system that reads the XFA data will see an empty form.
        """
        return transforms.fill_form(
            check(path), values, check(out), password=password, overwrite=overwrite
        )

    @server.tool()
    @guarded
    def flatten(
        path: str, out: str, password: str | None = None, overwrite: bool = False
    ) -> dict:
        """Write a copy with annotations and form fields baked into the page content.

        The text layer survives; what goes away is the ability to edit or un-fill any of it. Use it
        for a final copy — a filled form nobody can change back.
        """
        return transforms.flatten(
            check(path), check(out), password=password, overwrite=overwrite
        )

    @server.tool()
    @guarded
    def export_images(
        path: str,
        out_dir: str,
        pages: list[int] | None = None,
        dpi: int = 150,
        fmt: str = "png",
        password: str | None = None,
        overwrite: bool = False,
        clip: list[float] | None = None,
        name: str | None = None,
    ) -> dict:
        """Rasterise `pages` (1-based; omit for all) to image files in `out_dir`, as png or jpg.

        Use `render_page` instead when you want to *look* at a page — that hands the image back
        inline. This one is for when the image files are the deliverable.

        `clip` crops every exported page to the same `[x0, y0, x1, y1]` region in page points — for
        cutting one card, figure or signature out as a file. It is checked against each page
        separately, so a region that overhangs any page in the set is an error rather than one file
        that came out short. On a rotated page `clip` is in the unrotated coordinates `search`
        reports, same as `render_page`.

        **Filenames always carry the page number** — `<stem>-3.png`, whether you export one page or
        twenty. Pass `name` to choose the stem: cutting two regions out of page 3 needs
        `name: "card_front"` then `name: "card_back"`, or the two calls want the same filename and
        the second is refused. `name` is a plain filename stem, not a path — no separators, no
        `..`, no extension (the format comes from `fmt`).

        The rendered pixel size rounds **outward** to whole device pixels:
        `ceil(x1 x dpi/72) - floor(x0 x dpi/72)`, so a 100 pt square at 150 dpi is 209 px, not the
        208.33 the naive formula gives. That is deliberate — no partial pixel of the region you
        asked for is dropped — but do not assert on `(x1-x0) x dpi/72`.
        """
        return transforms.export_images(
            check(path),
            check(out_dir),
            pages=pages,
            dpi=dpi,
            fmt=fmt,
            password=password,
            overwrite=overwrite,
            clip=clip,
            name=name,
        )

    # ---- redaction: destructive, and verified before success is reported ----

    @server.tool()
    @guarded
    def redact_text(
        path: str,
        out: str,
        query: str | None = None,
        queries: list[str] | None = None,
        match_case: bool = False,
        whole_words: bool = False,
        pages: list[int] | None = None,
        password: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """**Destructively** remove every occurrence of `query` and write a verified copy.

        Pass **either** `query` (one string) **or** `queries` (a list) — not both. Use `queries`
        whenever you have more than one thing to remove from the same document: it removes them all
        in one verified pass and writes **one** file. Chaining separate calls instead leaves an
        intermediate file per step, each a partially-redacted copy still holding the live values you
        have not got to yet, and every one of them is yours to remember and delete. One pass also
        removes an ordering trap — chained calls had to run the longest query first, or a shorter
        one ate part of a longer match and left fragments behind. Each query is verified separately
        and to the same standard; the reply carries a `queries` list with each one's own counts.

        This deletes content. The text is physically removed from the output — not covered by a
        black box — and the written file is then checked twice: that the redacted regions really
        lost their text (PyMuPDF, plus Poppler when installed — a different engine from the one
        that did the removing), and that re-running this same search against the output finds
        **zero** remaining matches in the pages redacted. If either check fails the output is
        **deleted** and this call fails, so a path coming back always points at a file where the
        query no longer matches. `residual_matches` reports the count that was verified.

        **Run `search` with the same `query`, `match_case` and `whole_words` first and show the
        caller the snippets** — this tool deletes what it finds. Matching is the app's find-bar
        behaviour, and `whole_words` chooses **what the query is**, not just how strictly it
        matches:

        * `whole_words: true` — the query is **one phrase**, matched whole, and neither end may sit
          inside a longer word. This is the mode to use for a phrase like "regular expression".
        * `whole_words: false` (the default) — the query is a **list of words**, any of which
          matches on its own and each of which still matches inside longer words. "Smith" also
          matches inside "Smithsonian"; "regular expression" removes every "regular" and every
          "expression" separately, wherever they appear.

        **A "word" ends at a space, so `whole_words: true` cannot see a value embedded in a longer
        unbroken run** — `220885-1063303` inside `<AccountNumber:220885-1063303>` is not a match,
        because the whole tag is one word. For a single token with no spaces (an account number, a
        reference, an ID) prefer `whole_words: false`: it cannot over-match a one-word query, and it
        is the mode that finds the value wherever it is embedded.

        Fails rather than writing an untouched copy when nothing matches.

        Read the reply, do not just check that it succeeded:

        * `residual_literal` counts places the query still appears **literally** that the
          `whole_words` setting does not match, with each one named in `warnings`. It is not
          automatically a leak — redacting whole-word "Smith" leaves "Smithsonian" and says so — but
          if a named survivor is the value you meant to remove, re-run with `whole_words: false`.
          This is the check that catches the matcher being wrong, so it is the one worth reading.
        * `residual_normalized` names spellings of the query still in the file that differ from it
          **only in separators** — `6073474692031` against a query of `607347469 203 1`, or
          `08/24/1970` against `08-24-1970`, or a number broken by a line wrap. Nothing was deleted
          for these: whether two spellings are one value is a fact about the document that only you
          have. If they are, redact those forms too. **An empty list and `null` are different
          answers**: `[]` means the scan ran and found none, `null` means it did not run — a short
          unpunctuated query like `000000` cannot be checked this way, because matching it across
          separators finds coincidence rather than spellings. A `null` says so in `warnings`, and
          means "unchecked", never "clean".
        * `invisible_matches` counts removals that were never visible on the page. They are gone,
          but their presence means this document hides data where a reader cannot see it.
        * `query_terms` breaks the match count down per term when `whole_words` is off and the
          query has several words. If one term did most of the deleting and the phrase itself is
          rare, `warnings` says so — that is the **over-redaction** signal, and it is the only one
          you get, because destroyed content leaves no trace in the output to check afterwards.

        **`matches` and `boxes_redacted` are different numbers and both are right.** `matches` is
        the sum of each query's own hit count, so text that two queries both matched counts twice;
        `boxes_redacted` counts the distinct rectangles actually applied to the page. With a single
        `query` they are usually equal, which is why the difference only shows up with `queries`.
        Neither is "how many identifiers did I remove" — a short query whose match sits inside a
        longer query's match produces two real boxes over one piece of text.

        **`residual_scope` names the pages the residual scans read.** It is every page unless you
        passed `pages`, in which case the scans — like the redaction — cover only those, and
        `residual_literal`/`residual_normalized` describe that slice rather than the document. A
        warning says so. It is not the same as `pages_redacted`, which lists only where boxes
        landed and is a smaller set.

        With `queries`, each of those fields is reported **per query** inside `queries`, alongside
        that query's own `matches`, rather than at the top level — six queries' counts flattened
        into one set would report the last one's results as the whole call's. A query that matches
        nothing does **not** fail the call when another matched: it comes back as `matches: 0` with
        a warning, because failing would delete an output that correctly removed the others.

        The guarantee covers the **text layer**. Text that is part of a scanned image has no text to
        verify; `verified_text` will be empty and `cross_engine_verified` tells you whether the
        second engine ran at all.
        """
        return redaction.redact_text(
            check(path),
            query,
            check(out),
            queries=queries,
            match_case=match_case,
            whole_words=whole_words,
            pages=pages,
            password=password,
            overwrite=overwrite,
        )

    @server.tool()
    @guarded
    def redact_regions(
        path: str,
        regions: list[dict],
        out: str,
        password: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """**Destructively** remove rectangular regions and write a verified copy.

        `regions` is a list of `{"page": 1, "box": [x0, y0, x1, y1]}` in page points — the same
        coordinate space `search` and `get_form_fields` report boxes in. A region may carry
        `boxes` (a list) instead of `box`, which is the shape a `search` hit already has, so a hit
        can be handed straight back as one region without being taken apart. Use this when you know *where* rather than *what*: a signature
        block, a letterhead, a photo, a table cell.

        Content is physically deleted, the written file is re-read and verified (PyMuPDF, plus
        Poppler when installed), and the output is deleted if anything survives. Images and vector
        graphics under a box are removed too — but only text can be verified, so a region over a
        scanned image comes back with an empty `verified_text`.

        There is no `residual_matches` here, and the difference is worth knowing: `redact_text` can
        re-run its own query against the output to prove it covered every occurrence, while a
        region redaction has no query to re-run. You said *where*, so the boxes are the whole of
        the request — and the check confirms those boxes are empty, nothing wider.

        So read `verified_text`: it lists what actually came out of the boxes, which is often more
        than you aimed at — a rectangle over a name may take the address under it too. If you are
        removing **PII rather than blanking an area**, search those strings before you send the
        file on, or use `redact_text`, which proves it removed every occurrence rather than every
        occurrence *here*.
        """
        return redaction.redact_regions(
            check(path), regions, check(out), password=password, overwrite=overwrite
        )

    return server


# The default server: unrestricted paths, writes enabled, standard caps. This is what `.mcp.json`
# and `python -m mcp_bridge` reach through `main()`, and what the tests exercise.
server = create_server()


def parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(
        prog="klarpdf-mcp",
        description="KlarPDF's PDF engine as MCP tools, over stdio. Makes no network connections.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help=(
            "expose only the six query tools; the transform and redaction tools are not registered "
            "at all, so the model never sees them. Writes are ON by default: no write tool can "
            "destroy data by construction (each needs an explicit new output path, in-place save "
            "is never exposed), so this is the cautious opt-out rather than the default."
        ),
    )
    parser.add_argument(
        "--allow-root",
        action="append",
        metavar="DIR",
        help=(
            "restrict the server to this directory tree; repeatable. Unrestricted by default, "
            "because a stdio server is a subprocess running as you with the file access you "
            "already have. Use this when you want a smaller blast radius than your own account."
        ),
    )
    args = parser.parse_args(argv)
    return Config(
        policy=PathPolicy.from_args(args.allow_root),
        read_only=args.read_only or Config.from_env().read_only,
    )


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (`klarpdf-mcp`). Serves MCP over stdio until the client closes."""
    config = parse_args(argv)
    # stderr, never stdout: stdout is the JSON-RPC stream and a stray line breaks the session.
    if config.read_only or config.policy.restricted:
        print(
            f"klarpdf-mcp {__version__}: "
            f"{'read-only, ' if config.read_only else ''}"
            f"{'roots=' + os.pathsep.join(config.policy.roots) if config.policy.restricted else 'unrestricted paths'}",
            file=sys.stderr,
        )
    create_server(config).run("stdio")


if __name__ == "__main__":
    main()
