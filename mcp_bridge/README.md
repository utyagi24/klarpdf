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

## Reading a tool's full contract

Each tool's description carries what you need **before** calling it. The reference half — the
field-by-field catalogue for `redact_text`, the counting and scope rules, how to feed `search` hits
to `redact_regions` — is published as an MCP resource instead:

```
klarpdf://docs/redact_text
klarpdf://docs/search
```

This is not organisation for its own sake. Claude Code truncates a tool description at 2,048
characters and appends `… [truncated]` with no error, so 69% of `redact_text`'s documentation used
to be discarded in transit — and it was the half describing what the reply *means*. Resource reads
are capped at 100,000 characters, so that material now has somewhere to live. The resource serves
the tool's live registered description plus the appendix, so it can never drift from what the tool
actually advertises.

## The tools

Page numbers are **1-based** everywhere, matching what a reader sees. An out-of-range page is an
error, never a silent clamp.

| Read | |
|---|---|
| `get_info` | Pages, size, page sizes, encryption + permissions, **has-text-layer**, outline. Call it first. |
| `get_outline` | Bookmarks as `{level, title, page}`. |
| `search` | Hits with page, snippet, box, and whether the text is `invisible` on the page. `match_case`, `whole_words`. |
| `extract_text` | Text of named pages. |
| `render_page` | One page — or one `clip` region of it — as a PNG image block. |
| `get_form_fields` | Fillable fields, one entry per occurrence, with each one's checkbox on-state and read-only / required / multiline / max-length. |

| Transform — writes a **new** file | |
|---|---|
| `extract_pages` | Named pages out as one new document — the "give me pages 10-20" tool. |
| `delete_pages` · `reorder` · `rotate` | Page-set edits; bookmarks follow their pages. |
| `split` · `merge` | Cut into several files by print-dialog ranges (`"1-3"`, `"5-"`) / concatenate; merge renames colliding fields. |
| `fill_form` · `flatten` | Fill (still editable; checkboxes take `true` or their own export state, anything else is an error) / bake in (no longer editable). `fill_form` warns on an XFA form and on read-only fields. |
| `export_images` | Rasterise pages — or one `clip` region of each — to png/jpg files. |

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
- **A misspelled *parameter* is an error too.** Every tool rejects an argument name it does not
  declare, naming it, suggesting the near miss (case-insensitively, so `PAGES` is answered with
  `pages`), and listing what it does accept — before reading or writing anything. This is not decoration: the MCP SDK's default is to **drop** an unrecognised
  key, so `redact_text` called with `querys` instead of `queries` used to redact only the other
  query and report an unqualified success — `residual_matches: 0`, cross-engine verified — on a
  file that still held the value you asked it to remove. Nothing downstream can catch that, because
  the record of what you asked for is gone before the tool runs (PLAN.md §M106).

### Clipping to a region

`render_page` and `export_images` both take an optional `clip` — `[x0, y0, x1, y1]` in **page
points**, the same coordinate space `search` reports hits in and `redact_regions` consumes them
from. Boxes are this server's native currency, so region→image is the "I know *where*" workflow it
already serves, minus the destruction.

The composition worth knowing: **`search` → `render_page(clip=…)` shows a person the actual pixels
of what is about to be deleted.** Every other safety mechanism here is textual; this is the one that
makes the preview visual, on tools whose docs already say to search before you redact. It also
reads a stamp, signature or single table cell at 300 dpi without paying to render the page around
it.

A `search` hit carries **`boxes`**, one per line, so a match wrapping a line break has several. Pass
one, or their bounding box to see the whole match. `clip` takes a single rectangle where
`redact_regions` takes the list, and the asymmetry is deliberate: a union spanning two lines picks
up whatever sits between them, which is helpful when looking and is data loss when deleting.

**Rotation is handled for you.** `search` reports boxes in the page's *unrotated* space — the same
coordinates at `/Rotate 0` and `/Rotate 90` — and `clip` reads them there, so a hit feeds straight
back whatever the rotation. The image is the region as *displayed*, so a quarter turn swaps its
width and height. One consequence worth knowing: `get_info.page_sizes` reports displayed
dimensions, so on a turned page a perfectly valid box can extend past the width shown there.

**The pixel size rounds outward to whole device pixels**, so it is
`ceil(x1 x dpi/72) - floor(x0 x dpi/72)`, not `(x1-x0) x dpi/72`. A 100 pt square at 150 dpi comes
back 209 px, not 208.33 — and exactly 100 px at 72 dpi, where the scale is 1:1. Expanding outward is
deliberate: no partial pixel of the region you asked for is dropped, which is what you want from a
crop. Just don't assert on the naive formula.

**Every exported file carries its page number**, zero-padded to the document's page count —
`<stem>-03.png` from a 20-page file, `<stem>-003.png` from a 572-page one, whether you export one
page or twenty. The width comes from the document rather than from the pages you asked for, so two
exports from one file into one directory agree and sort correctly.
`name` chooses the stem. That matters for the use `clip` exists for: cutting two
regions out of *one* page needs `name: "card_front"` then `name: "card_back"`, or both calls want
the same filename and the second is refused. `name` is a plain filename stem, never a path: no
separators, no `..`, no extension.

**A clip that runs off the edge of the page is an error, not a smaller image.** PyMuPDF would
happily intersect it and hand back a cropped pixmap; `render_page` returns an image block, so its
reply has nowhere to say that it did. The error names the page's rect instead, so the fix is one
step rather than a guess. `export_images` checks **every** page in the set before writing anything —
page sizes vary within a document, and a clip that dies on page 7 must not leave six files behind.

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

**`residual_literal` counts occurrences, not spellings.** It is the number of times the query is
still in the file in a form `whole_words` did not match, with `residual_literal_forms` naming each
spelling, its own count and its pages. A single spelling can account for a dozen occurrences on a
long document, so read the integer as "how much is still there" and the list as "what it is".

**When a redaction lands inside a longer word, the reply says so.** That is the one way this tool
damages text you never asked about: redacting `Male` with `whole_words` off also takes the `male`
out of `Female`, leaving `Fe`. It is not filtered out, because matching inside longer words is
exactly what you want for an identifier buried in a machine tag — but it is reported.
`partial_word_matches` names the term, the word it came out of, and what that word now reads, and a
warning repeats it. None of the residual checks can find this on their own: they are all scoped to
the query, and the query *was* removed exactly as asked.

**A redacted file can be larger than its source, and the reply explains why.** Erasing pixels
inside an image means decoding it; re-compressing lossily would degrade exactly the area being
redacted, so the image is stored losslessly, and a photograph held losslessly is much bigger than
the same photograph as JPEG. `images_recoded` names each one by its PDF filter (`DCTDecode` →
`FlateDecode`) with the embedded stream size before and after, so the numbers reconcile against the
output file. Nothing is duplicated — only the placements a box actually overlaps are re-encoded.

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
