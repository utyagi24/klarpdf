# KlarPDF MCP bridge

KlarPDF's PDF engine as [MCP](https://modelcontextprotocol.io) tools — for Claude Code, Claude
Desktop, Codex CLI, Gemini CLI, and any other client that speaks MCP over stdio. Nineteen tools:
read a document without pulling it whole into context; transform it by splitting, merging,
reordering, rotating, deleting pages, filling forms and annotating; and redact it destructively
with cross-engine verification.

**It is independent of the KlarPDF app** and needs no GUI components. It runs on macOS, Linux and
Windows.

**Your PDFs stay where they are.** An agent works on the file on your disk — nothing is uploaded and
no third-party service sees it, which is the point of running the engine locally rather than sending
documents somewhere to be processed. The server itself makes no network connections: stdio is the
only transport and there is no listening port. What a tool *returns* — a page's text, a rendered
image — goes back to your model like any other tool result, so the usual care about what you hand a
hosted model still applies.

**It does not understand your documents.** Every tool here is mechanical: `search` matches literal
text rather than meaning, `extract_text` returns what is on the page rather than a summary, and
`annotate` and the redactions take boxes and strings rather than questions. Working out what a
clause means, which name matters, or what ought to be removed is the model's job — this server's job
is to hand it accurate material and then do exactly what it is told.

**Just want it running?** [Quick setup](QUICKSTART.md) gets Claude Code, Codex CLI or Gemini CLI
talking to it in six commands. This page is the full reference.

## What you need

| | |
|---|---|
| **Python 3.11 – 3.14** | Any version in that range; each is tested in CI on Linux and Windows. |
| **The source** | **Only if you install with `pip`.** The bridge is not published to PyPI, so the source comes from GitHub: clone the repo and check out a release tag, or download the source archive from the [releases page](https://github.com/utyagi24/klarpdf/releases). |
| **[`uv`](https://docs.astral.sh/uv/)** | **Only if you install the `.mcpb` bundle in Claude Desktop.** Desktop launches it with `uv run`, so `uv` must be on the PATH Desktop sees. Nothing warns you if it is missing; the symptom is the server failing to start. |
| **An MCP client** | The app your AI assistant runs in — Claude Code, Claude Desktop, Codex CLI, Gemini CLI and others. It starts `klarpdf-mcp` as a local subprocess and relays the model's tool calls to it. |
| **`poppler-utils`** | **Optional.** Adds a second, independent engine to redaction's verification step. Without it, redaction still verifies — with PyMuPDF alone. See [What redaction guarantees](#what-redaction-guarantees-and-where-it-stops). |

No GUI toolkit is installed. The bridge's only dependencies are PyMuPDF and the MCP SDK.

## Install

```bash
git clone https://github.com/utyagi24/klarpdf.git
cd klarpdf
git checkout v<version>               # a release tag; `git tag` lists them
python3 -m venv .venv                 # Windows: py -3 -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements-mcp.txt   # the exact versions we test against
pip install -e .                      # puts `klarpdf-mcp` inside the virtualenv
```

A dedicated virtualenv keeps the bridge's 29 packages out of your regular Python environment, and
lets you choose the interpreter yourself. Prefer a release tag over `main`: tags are what the
released `.mcpb` and the published locks correspond to. If you would rather not use git at all, the
[releases page](https://github.com/utyagi24/klarpdf/releases) carries a source archive for each one.

Run both `pip` lines, in that order. `requirements-mcp.txt` names all 29 packages at exact versions
— the same ones our CI tests against, and the ones we scan regularly for newly published
vulnerabilities. The second line then installs `klarpdf-mcp` itself, with its requirements already
satisfied at those pinned versions.

Check that it worked, and note the path it reports — you will need it in a moment:

```bash
klarpdf-mcp --help
which klarpdf-mcp        # Windows: where klarpdf-mcp
# -> /path/to/klarpdf/.venv/bin/klarpdf-mcp
```

**You will not normally run `klarpdf-mcp` yourself** — your client starts it and talks to it through
the subprocess's stdin and stdout.

## Connect it to your client

Every client has its own way of configuring MCP servers, but they all ask for **a command to run**.
For our MCP server that command is `klarpdf-mcp`.

That command is the whole of KlarPDF's entry in that list. There is nothing else to fill in: no
arguments, no environment variables, and no URL, port or token — those belong to servers that run
over a network, and this one does not. Your client starts `klarpdf-mcp` as a subprocess and the two
talk over its stdin and stdout. (Two optional switches can restrict what it is allowed to do — see
[Limiting what the server can do](#limiting-what-the-server-can-do).)

**Give the full path, not the bare name.** `klarpdf-mcp` is on your PATH only while the virtualenv
is active, and a client started from an icon or a launcher does not activate it. Every example below
uses `/path/to/klarpdf/.venv/bin/klarpdf-mcp` — substitute the path `which klarpdf-mcp` printed
during install. On Windows it is `...\.venv\Scripts\klarpdf-mcp.exe`.

### Claude Code

Install first (above), then:

```bash
claude mcp add klarpdf -- /path/to/klarpdf/.venv/bin/klarpdf-mcp
```

**If you run Claude Code from inside this repo, skip that command.** A `.mcp.json` at the repo root
already describes the server, and Claude Code offers it when you start here — approve the prompt and
you are configured. (It is offered rather than switched on silently, which is why checking it in is
safe.) That file calls the bare `klarpdf-mcp`, so activate the virtualenv before you start Claude
Code: `source .venv/bin/activate`.

Confirm with `/mcp`: it should say **klarpdf — 19 tools**. If it says *failed*, run the command by
hand in the same shell you launch Claude from; the error is almost always `command not found`
(nothing installed, or a different virtualenv active) or `No module named mcp` (installed the
package but not its dependencies).

### Claude Desktop

**Option A — the `.mcpb` bundle.** Install it in Claude Desktop as an extension. You can get the
bundle in two ways:

- **Download it** — `klarpdf-<version>.mcpb` from the
  [latest release](https://github.com/utyagi24/klarpdf/releases/latest), with `SHA256SUMS` alongside
  it. No clone and no Node required.
- **Build from a checkout** — run `python packaging/mcp/mcpb/build_mcpb.py`. This needs
  [Node](https://nodejs.org), since the packer itself is a Node tool. The script's header documents
  exactly what goes into the bundle and what's deliberately left out.

Either way, you also need [`uv`](https://docs.astral.sh/uv/). The bundle contains only source — no
interpreter, no dependencies of its own — so Claude Desktop relies on `uv run` to actually launch
the server. Downloading spares you Node, but not `uv`; make sure it's on the PATH that Desktop sees
before you open the file.

This also sets Option A apart from every other path here: **it installs online**. At install time,
`uv` fetches wheels from PyPI for your machine, resolved against a hashed `uv.lock` shipped inside
the bundle — so you get the exact package set we reviewed, not whatever PyPI happens to resolve that
day. That lock does pin a handful of platform-specific packages that our weekly `pip-audit` never
sees. If that residual risk isn't acceptable, use Option B.

**Option B — edit the config.** Add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "klarpdf": {
      "command": "/path/to/klarpdf/.venv/bin/klarpdf-mcp"
    }
  }
}
```

### Codex CLI

```bash
codex mcp add klarpdf -- /path/to/klarpdf/.venv/bin/klarpdf-mcp
```

Or write it into `~/.codex/config.toml` yourself:

```toml
[mcp_servers.klarpdf]
command = "/path/to/klarpdf/.venv/bin/klarpdf-mcp"
```

### Gemini CLI

```bash
gemini mcp add klarpdf /path/to/klarpdf/.venv/bin/klarpdf-mcp
```

Or edit `~/.gemini/settings.json` (or `.gemini/settings.json` for a single project), which uses the
same `mcpServers` block Claude Desktop does:

```json
{
  "mcpServers": {
    "klarpdf": {
      "command": "/path/to/klarpdf/.venv/bin/klarpdf-mcp"
    }
  }
}
```

### Any other client

Clients differ in *where* the configuration lives, not in what it has to say. Claude Desktop and
Gemini CLI share the `mcpServers` JSON block above; Codex CLI uses the `[mcp_servers.<name>]` TOML
table; others vary again. All of them want one thing from you — a command to run. Point yours at
your `/path/to/klarpdf/.venv/bin/klarpdf-mcp`, add `args` if you want the switches below, and ignore
every field about URLs, ports and tokens, because this server has none.

## Limiting what the server can do

By default the server can read and write any file you can. That is the honest default rather than a
lax one: it is a subprocess *you* launched, running as you, and a client that can start it can
already read your disk. Two switches narrow that when you want a smaller blast radius — a shared
machine, an agent you are still learning to trust, a directory of client documents.

| Switch | Environment variable | What it does |
|---|---|---|
| `--read-only` | `KLARPDF_MCP_READ_ONLY=1` | Registers only the seven read tools. The twelve transform and redaction tools are never advertised, so the model does not see them and cannot ask for them. |
| `--allow-root DIR` | `KLARPDF_MCP_ALLOW_ROOTS` | Confines every path — inputs and outputs alike — to that directory tree. Repeatable on the command line; the variable takes a list separated by your platform's path separator (`:` on macOS and Linux, `;` on Windows). |

Pass them wherever your client names the command:

```json
{
  "mcpServers": {
    "klarpdf": {
      "command": "/path/to/klarpdf/.venv/bin/klarpdf-mcp",
      "args": ["--read-only", "--allow-root", "/Users/me/Documents/contracts"]
    }
  }
}
```

Three details that matter in practice:

- **Containment is checked on the resolved path**, through the same file-identity code the app uses,
  so a symlink pointing out of an allowed root, a `..` escape, and a case-different spelling on
  Windows are all refused. A path that does not exist yet — an output you are about to write — is
  judged by its parent directory.
- **`--allow-root` wins over the variable** when both are set. `--read-only` is on if *either* asks
  for it, and neither turns it back off.
- **A restriction is announced on stderr** when the server starts, naming what is in force. Never on
  stdout, which carries the JSON-RPC stream — one stray line there ends the session.

Writes are on by default because no write tool can destroy data by construction: each needs an
explicit new output path, and in-place save is never exposed. `--read-only` is the cautious opt-out,
not the recommended setting.

## Password-protected PDFs

Every tool takes an optional `password`. Call `get_info` first — it reports `needs_password: true`
for a document that will not open without one, and reports the encryption and permissions of a
document that opens but is restricted.

A missing or wrong password fails the call with a message naming the file and telling you to call
again with `password`. The server never prompts: there is nobody behind an MCP call to answer a
prompt, so prompting would hang the client instead of asking anyone anything.

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
error, never a silent clamp. Every tool takes an optional `password` — see
[Password-protected PDFs](#password-protected-pdfs).

| Read | |
|---|---|
| `get_info` | Pages, size, page sizes, encryption + permissions, **has-text-layer**, outline. Call it first. |
| `get_outline` | Bookmarks as `{level, title, page}`. |
| `search` | Hits with page, snippet, box, and whether the text is `invisible` on the page. `match_case`, `whole_words`. |
| `extract_text` | Text of named pages. |
| `render_page` | One page — or one `clip` region of it — as a PNG image block. |
| `get_form_fields` | Fillable fields, one entry per occurrence, with each one's checkbox on-state and read-only / required / multiline / max-length. |
| `get_annotations` | Every mark on the page — anyone's — with its `note`, colour and boxes; `mine` / `editable` say who wrote it and whether the app can edit it. |

| Transform — writes a **new** file | |
|---|---|
| `extract_pages` | Named pages out as one new document — the "give me pages 10-20" tool. |
| `delete_pages` · `reorder` · `rotate` | Page-set edits; bookmarks follow their pages. |
| `split` · `merge` | Cut into several files by print-dialog ranges (`"1-3"`, `"5-"`) / concatenate; merge renames colliding fields. |
| `fill_form` · `flatten` | Fill (still editable; checkboxes take `true` or their own export state, anything else is an error) / bake in (no longer editable). `fill_form` warns on an XFA form and on read-only fields. |
| `export_images` | Rasterise pages — or one `clip` region of each — to png/jpg files. |
| `annotate` | Write highlights / underlines / strike-throughs, each able to carry a note. Takes boxes, not queries; merges with markup already there rather than stacking. |

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

### Marking up, and the review hand-off

`annotate` writes highlights, underlines and strike-throughs; `get_annotations` reads back every
mark a document carries, including ones made in Acrobat, Preview or Edge. Both are worth having on
their own — "underline every termination clause", "summarise the review comments in this contract" —
and together they make a hand-off an agent cannot do alone:

1. **Propose.** Locate what matters with `search`, then `annotate` it, perhaps one colour for
   *certain* and another for *needs a look*, with a `note` on each saying why. Nothing is deleted.
2. **Dispose.** A person opens the output in KlarPDF. Every mark written here is an ordinary
   editable mark: recolour it, extend it, delete it, reply in its note. That review surface already
   exists and is better than any tool reply.
3. **Act.** Read the reviewed file with `get_annotations`, filter on what they left, and go.

**`annotate` takes boxes, not queries.** It does not search and it has no idea what a name or a
termination clause looks like — that judgement is the caller's, and a PDF engine that pretended
otherwise would be guessing. This is the same line redaction's variant scan draws when it reports
rather than matches.

**Colour is the reviewer's convention, never the tool's.** Nothing here decides that orange means
delete. If deletion is the outcome, filter `get_annotations` and pass the boxes to `redact_regions`,
which verifies the removal as it always does. There is deliberately no "redact what I highlighted"
shortcut: that step should be one you took, not one a colour triggered. The boxes need no adjusting
on the way — both tools work in unrotated page points, at any page rotation.

**A repeat call merges rather than stacking.** Re-marking a span in the same colour is a no-op, a
different colour takes the span over, and notes are carried onto the survivor — the app's own rules,
because it is the app's own function. So retrying a call is safe.

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
