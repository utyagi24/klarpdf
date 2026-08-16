# KlarPDF MCP bridge

KlarPDF's PDF engine as [MCP](https://modelcontextprotocol.io) tools, for Claude Code, Claude
Desktop, and any other client that speaks stdio. Seventeen tools: read a document without pulling it
whole into context, transform it to a new file without losing its content, and redact it
destructively with cross-engine verification.

**It is a separate, optional component.** The Windows app (`klarpdf-setup-x64.exe`) does not contain
it, is not made bigger by it, and does not depend on it. Nothing here imports PySide6 — a test
asserts that in a fresh interpreter after every tool has run.

**It makes no network connections.** stdio is the only transport; there is no listening port. (Note
the one exception, which is an *install*-time thing, not a runtime one: the one-click `.mcpb` below
fetches its dependencies from PyPI when Claude Desktop installs it.)

## Install

**Install it — do not try to run it in place.** From a clone:

```bash
pip install -r requirements-mcp.txt   # the audited, cross-platform pins
pip install -e .                      # puts `klarpdf-mcp` on PATH
klarpdf-mcp                           # serves on stdio; Ctrl-C to stop
```

Or isolated, if you never want to touch it again:

```bash
pipx install .
```

`klarpdf-mcp` is the command to give any client. **`python -m mcp_bridge` is not** — it only works
when the current directory happens to be the repo, because `-m` puts the *working directory* on
`sys.path`, never the interpreter's location. A client launches its servers from its own working
directory, which is essentially never your checkout, so `python -m` fails there with
`No module named mcp_bridge` even when you point it at the right virtualenv's Python. (If you must,
`PYTHONPATH=/path/to/klarpdf` fixes it — but installing is the answer.)

Install the two lines together: `requirements-mcp.txt` carries the exact versions we audit weekly,
and `pip install -e .` on its own would resolve fresh ones. That lock is `==`-pinned, unhashed and
**cross-platform** on purpose — the app is Windows-first, the bridge is not, and a hashed
`win_amd64` lock would silently make it Windows-only, because `pip install --require-hashes` fails
on other platforms by design.

## Claude Code

Install first (above), then:

```bash
claude mcp add klarpdf -- klarpdf-mcp
```

A `.mcp.json` is also checked in at the repo root, so inside this project Claude Code offers the
server and you approve it — but it too needs the install, since it invokes `klarpdf-mcp`.

Confirm with `/mcp`: it should say **klarpdf — 17 tools**. If it says *failed*, run the command by
hand in the same shell you launch Claude from; the error is almost always `command not found`
(nothing installed, or a different virtualenv active) or `No module named mcp` (installed the
package but not its dependencies).

## Claude Desktop

**Option A — one-click.** Build the bundle and open it; Desktop installs it as an extension.

```bash
python packaging/mcpb/build_mcpb.py     # -> dist/klarpdf-<version>.mcpb
```

Requires [`uv`](https://docs.astral.sh/uv/) on your PATH — the bundle uses it to resolve Python and
its dependencies for your machine. Two consequences worth knowing before you choose this path:

- **It installs online.** `uv` fetches wheels from PyPI at install time. This is the one deliberate
  break from everything else this project ships, and it is what buys a single bundle that works on
  macOS, Windows and Linux: MCPB cannot portably vendor compiled dependencies, and this bundle has
  two (PyMuPDF is C, pydantic is Rust).
- **The audited lock is not what it installs.** The repo's weekly `pip-audit` covers
  `requirements-mcp.txt`, i.e. the pip/pipx path above. The bundle's `pyproject.toml` is *generated
  from* that lock, so the two agree when the bundle is built — but a Desktop install resolves
  fresh. If that trade is not acceptable, use Option B.

**Option B — edit the config.** Add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "klarpdf": {
      "command": "klarpdf-mcp"
    }
  }
}
```

Use the absolute path to the script if it is not on the PATH Desktop sees (`which klarpdf-mcp` /
`where klarpdf-mcp`).

## The tools

Page numbers are **1-based** everywhere, matching what a reader sees. An out-of-range page is an
error, never a silent clamp.

| Read | |
|---|---|
| `get_info` | Pages, size, page sizes, encryption + permissions, **has-text-layer**, outline. Call it first. |
| `get_outline` | Bookmarks as `{level, title, page}`. |
| `search` | Hits with page, snippet and box. `match_case`, `whole_words`. |
| `extract_text` | Text of named pages. |
| `render_page` | One page as a PNG image block. |
| `get_form_fields` | Fillable fields, one entry per occurrence, with each one's checkbox on-state and read-only / required / multiline / max-length. |

| Transform — writes a **new** file | |
|---|---|
| `extract_pages` | Named pages out as one new document — the "give me pages 10-20" tool. |
| `delete_pages` · `reorder` · `rotate` | Page-set edits; bookmarks follow their pages. |
| `split` · `merge` | Cut into several files by print-dialog ranges (`"1-3"`, `"5-"`) / concatenate; merge renames colliding fields. |
| `fill_form` · `flatten` | Fill (still editable; checkboxes take `true` or their own export state, anything else is an error) / bake in (no longer editable). `fill_form` warns on an XFA form and on read-only fields. |
| `export_images` | Rasterise pages to png/jpg files. |

| Redact — **destructive**, verified | |
|---|---|
| `redact_text` | Remove every match of a query. |
| `redact_regions` | Remove rectangles; a `search` hit's `box` feeds straight back in. |

### What the write tools guarantee

- **Your input is never written to.** Every write tool requires an explicit `out`, in-place save is
  not exposed, and the refusal resolves through the same path-identity check the app uses — so a
  symlink, a `..` segment, or a case-different spelling on Windows cannot slip the input back in as
  the destination. "The source is left byte-identical" is a test, not a claim.
- **Nothing else is clobbered either.** An existing output is refused unless you pass
  `overwrite: true`.
- **Lossless for content; for document structure, only if the page set is unchanged.** Text layer,
  form fields, annotations and bookmarks always survive, and bookmarks are re-pointed at pages' new
  positions rather than left dangling. But a tool that *moves* pages (`reorder`, `delete_pages`,
  `extract_pages`, `split`, `merge`) builds a new document, and the accessibility structure tree,
  `/Perms`, the `/Names` tree and encryption do not survive that — they are document-level, and
  copying pages does not copy them. A tool that leaves every page in place (`fill_form`, `flatten`,
  the redactions) edits a copy of the original instead and keeps all of it. See PLAN.md §Key design
  idea for the table.
- **A mistake is an error, not a quiet partial success.** An unknown form-field name, a `reorder`
  that is not a full permutation, a `delete_pages` that would empty the document, a `redact_text`
  that matches nothing — all fail loudly rather than writing something plausible.

### What redaction guarantees, and where it stops

The text is **physically removed**, not covered with a black box, and the redaction annotation is
consumed so there is nothing left in the output to delete and reveal what was under it. Then the
written file is re-read and checked: with PyMuPDF always, and with Poppler's `pdftotext` when it is
installed — a different engine from the one that did the removing. **If anything is still
recoverable, the output is deleted and the call fails.** A path coming back always names a file that
was verified.

The guarantee covers the **text layer**. Text that is part of a scanned image is pixels: the region
is removed along with the image, but there is no text to verify, so `verified_text` comes back empty
and the result says so. `cross_engine_verified` tells you whether the second engine ran at all —
install `poppler-utils` for the stronger check.

Preview with `search` before `redact_text`. Matching is the app's find-bar behaviour, so with
`whole_words` off a search for "Smith" also matches inside "Smithsonian" — and this tool deletes
what it finds.
