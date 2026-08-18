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
| `search` | Hits with page, snippet, box, and whether the text is `invisible` on the page. `match_case`, `whole_words`. |
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
| `redact_text` | Remove every match of a `query` — or of several `queries`, in one verified pass. |
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

**The two tools prove different things, and `redact_regions` proves the narrower one.** `redact_text`
removes what you *named* and re-checks the whole document to prove it caught every occurrence.
`redact_regions` removes what is *there* — you said where, so the boxes are the whole of the request,
and the check confirms those boxes are empty, nothing wider. Read `verified_text` on a region
redaction: it lists what actually came out, which is often more than you aimed at, since a rectangle
over a name may take the address under it too. If you are removing **PII rather than blanking an
area**, search those strings before sending the file on, or use `redact_text`.

Preview with `search` before `redact_text`. Matching is the app's find-bar behaviour, so with
`whole_words` off a search for "Smith" also matches inside "Smithsonian" — and this tool deletes
what it finds.

**Removing several things? Use `queries`, not several calls.** The argument is data hygiene rather
than convenience: every write needs a fresh `out`, so a chain of six redactions strews five
intermediate files, each a partially-redacted copy still holding the values you have not reached
yet, and each one yours to remember and delete. One pass writes one file. It also retires an
ordering trap — chained calls had to run the **longest query first**, or a shorter one consumed part
of a longer match and left fragments behind. In one pass every box is computed against the intact
source before anything is applied, so the order of the list cannot matter. Overlapping terms
(`607347469 203 1` and `607347469`) are the case this is *for*, not an edge case to avoid.

**`whole_words: true` means whole *token*, and for a single-token query that is usually the wrong
choice.** A "word" is a run of characters between spaces, so `220885-1063303` with `whole_words` on
does not match `<AccountNumber:220885-1063303>` — the entire tag is one word. Machine-readable tags,
filenames, URLs and `key:value` pairs all have that shape, and a bill that stamps its account number
into such a tag will keep it through a redaction that looks like it worked. For a query with no
spaces, `whole_words: false` is both safe (there is no second word to over-match) and the mode that
finds the value wherever it is embedded.

That case is why the reply carries two fields worth reading rather than just a success:

- **`residual_literal`** — places the query still appears *literally* that the `whole_words` setting
  does not match, each named in `warnings`. Deliberately a warning and not a failure: redacting
  whole-word "Smith" legitimately leaves "Smithsonian". The named token is what separates the two —
  `'Smithsonian'` reads as fine, `'<AccountNumber:220885-1063303>'` does not. This check shares
  nothing with the matcher, which is the point: the older verification did, so it could confirm the
  matcher's own blind spots as clean.
- **`invisible_matches`** — removals that were never *visible* on the page (white-on-white,
  transparent, painted over). They are gone, but they tell you this document stores data where no
  reader, and no before/after render comparison, would ever find it. `search` flags the same thing
  per hit as `invisible`.
- **`residual_normalized`** — spellings still in the file that differ from the query **only in
  separators**: `6073474692031` against `607347469 203 1`, `08/24/1970` against `08-24-1970`, or an
  identifier broken by a line wrap. A literal scan sees none of these, so redacting one form used to
  report the file clean while the other stayed in it. Nothing is deleted for a variant — whether two
  spellings are one value is a fact about the document that only you have.
  **`[]` and `null` mean different things.** `[]` is "the scan ran and found nothing"; `null` is
  "the scan did not run", which happens for a short unpunctuated query like `000000`, where matching
  across separators finds coincidence rather than spellings. `null` comes with a warning saying so,
  because a feature that exists to close an invisible failure must not go quiet in a way that reads
  as reassurance. A query you punctuated — `999 99 9999`, `AB 12 CD` — is always scanned: the
  separators are you saying it is a structured value.
- **`queries`** — the per-query breakdown when you passed a list. Every field above is reported
  **inside** it, per query, beside that query's own `matches`, because flattening six queries'
  counts into one set would report the last one's numbers as the whole call's. The shape follows
  which parameter you used, not how many queries you happened to put in it: a one-element `queries`
  list still comes back as a list, so a caller iterating a variable-length list gets one shape to
  parse. A query that matches **nothing** does not fail the call when another matched — it comes
  back `matches: 0` with a warning, because failing would delete an output that correctly removed
  the others.
- **`residual_scope`** — the pages the two scans above actually read. Every page, unless you passed
  `pages`; then the scans cover that slice, exactly as the redaction did, and a warning says so.
  Everything in a reply describes the operation you asked for — the tool never mixes page-scoped and
  document-wide answers in one response. Don't read `pages_redacted` as a substitute: it lists only
  where boxes landed, which is a smaller set (`[1]` for a call that scanned `[1, 2, 3]`).
- **`matches` vs `boxes_redacted`** — different numbers, both correct. `matches` sums each query's
  own hits, so text two queries both matched counts twice; `boxes_redacted` counts distinct
  rectangles applied. Neither is "how many identifiers came out": a short query matching inside a
  longer query's match makes two real boxes over one piece of text.
- **`query_terms`** — the per-term match breakdown when `whole_words` is off and the query has
  several words, with a warning when one term did most of the deleting and the phrase itself is
  rare. This is the **over-redaction** counterweight: everything else here proves the query is
  *gone*, and all of it is silent when a query removed far more than you meant. It is also the only
  warning you get, because destroyed content leaves no trace in the output — the only record it was
  ever there is the input, which is never modified.
