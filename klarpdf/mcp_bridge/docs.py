"""The reference half of each tool's contract, for `klarpdf://docs/{tool}` (M105).

**Why this is a separate file rather than more docstring.** The client truncates a tool description
at 2,048 characters and says nothing (ENV-001), so a description is a *budget*, not a place. What
must survive the cut is the part a caller needs **before** calling — that it destroys content, what
`whole_words` changes, the word-boundary trap. What belongs here is the part they need **while
reading the reply**: the field-by-field catalogue, the counting rules, the scope semantics. Those
run to thousands of characters and are useless if they push the safety contract past the cut, which
is exactly what had happened — 69% of `redact_text` never arrived, and it was the 69% below the
fold.

Resource reads are capped at 100,000 characters rather than 2,048, so this side has room.

**Nothing here restates a description.** The resource serves the live description *plus* the entry
below, so the two are concatenated, never duplicated — which is what makes drift impossible rather
than merely unlikely. Add to the description what a caller must know before calling; add here what
they need to interpret what came back. A test asserts the resource still contains the live
description verbatim, so a rewrite up there cannot silently strand the text down here.
"""

from __future__ import annotations

REFERENCE: dict[str, str] = {
    "redact_text": """\
## Why one pass beats chained calls

`queries` removes several values in a single verified pass and writes one file. Chaining separate
calls leaves an intermediate file per step, each a partially-redacted copy still holding the live
values you have not got to yet, and every one of them is yours to remember and delete. One pass
also removes an ordering trap: chained calls had to run the longest query first, or a shorter one
ate part of a longer match and left fragments behind. Each query is verified separately and to the
same standard.

## What the verification actually proves

The written file is checked twice: that the redacted regions really lost their text (PyMuPDF, plus
Poppler when installed — a different engine from the one that did the removing), and that
re-running this same search against the output finds zero remaining matches in the pages redacted.
If either check fails the output is deleted and the call fails, so a path coming back always points
at a file where the query no longer matches. `residual_matches` reports the count that was verified.

## The residual fields

* **`residual_literal`** counts **occurrences** — how many times the query still appears
  *literally* in a spelling the `whole_words` setting does not match. `residual_literal_forms`
  breaks that down, one entry per spelling with its own `count` and `pages`, and `warnings` names
  them. It is not automatically a leak — redacting whole-word "Smith" leaves "Smithsonian" and says
  so — but if a named survivor is the value you meant to remove, re-run with `whole_words: false`.
  This is the check that catches the matcher being wrong, so it is the one worth reading. Read the
  integer as "how much is still there" and the forms list as "what it is": on a long document a
  single spelling can account for a dozen occurrences.
* **`residual_normalized`** names spellings of the query still in the file that differ from it only
  in separators — `6073474692031` against a query of `607347469 203 1`, or `08/24/1970` against
  `08-24-1970`, or a number broken by a line wrap. Nothing was deleted for these: whether two
  spellings are one value is a fact about the document that only you have. If they are, redact those
  forms too. Its `count` is occurrences as well, not pages — several variants on one page count
  severally. **An empty list and `null` are different answers.** `[]` means the scan ran and found
  none; `null` means it did not run — a short unpunctuated query like `000000` cannot be checked
  this way, because matching it across separators finds coincidence rather than spellings. A `null`
  says so in `warnings`, and means "unchecked", never "clean".
* **`invisible_matches`** counts removals that were never visible on the page. They are gone, but
  their presence means this document hides data where a reader cannot see it.
* **`query_terms`** breaks the match count down per term when `whole_words` is off and the query has
  several words. If one term did most of the deleting and the phrase itself is rare, `warnings` says
  so — that is the over-redaction signal, and it is the only one you get, because destroyed content
  leaves no trace in the output to check afterwards.

## `matches` and `boxes_redacted` are different numbers and both are right

`matches` is the sum of each query's own hit count, so text that two queries both matched counts
twice; `boxes_redacted` counts the distinct rectangles actually applied to the page. With a single
`query` they are usually equal, which is why the difference only shows up with `queries`. Neither is
"how many identifiers did I remove" — a short query whose match sits inside a longer query's match
produces two real boxes over one piece of text.

## `residual_scope` names the pages the residual scans read

It is every page unless you passed `pages`, in which case the scans — like the redaction — cover
only those, and `residual_literal` / `residual_normalized` describe that slice rather than the
document. A warning says so. It is not the same as `pages_redacted`, which lists only where boxes
landed and is a smaller set.

## Per-query reporting

With `queries`, each field above is reported per query inside `queries`, alongside that query's own
`matches`, rather than at the top level — six queries' counts flattened into one set would report
the last one's results as the whole call's. A query that matches nothing does **not** fail the call
when another matched: it comes back as `matches: 0` with a warning, because failing would delete an
output that correctly removed the others.

## When the output is larger than the input

Redacting text that sits **on top of an image** means erasing pixels inside that image, which means
decoding it. Re-compressing lossily would degrade exactly the area being redacted, so the image is
stored losslessly instead — and a photograph held losslessly is far larger than the same photograph
as JPEG. A redaction touching a handful of images can therefore multiply the file size.

`images_recoded` lists each one (`page`, `from`, `to`, `bytes_before`, `bytes_after`) and a warning
states the total change. `from` and `to` are the **PDF filter names** the output actually carries —
typically `DCTDecode` (JPEG) to `FlateDecode` — and the byte counts are the embedded stream
lengths, so they reconcile against the file itself rather than a re-encoded copy of it.

Nothing is duplicated and no untouched page is altered; only placements a
redaction box actually overlaps are re-encoded, so a page that draws the same image twice keeps the
untouched copy in its original encoding.

## What the guarantee excludes

It covers the **text layer**. Text that is part of a scanned image has no text to verify;
`verified_text` will be empty and `cross_engine_verified` tells you whether the second engine ran at
all.
""",
    "annotate": """\
## The review workflow this is built for

Marking up a document is worth doing on its own, but the shape it was designed around is a handover:
an agent **proposes**, a person **disposes**, and the agent then acts only on what was approved.

1. Locate what matters (`search`, `extract_text`) and `annotate` it — perhaps in one colour for
   "certain" and another for "please check", with a `note` on each saying why.
2. The person opens the output in KlarPDF and reviews it there. Every mark this tool writes is an
   ordinary editable mark in the app: they can recolour it, extend it, delete it, or type a reply
   into its note. That review surface is better than anything a tool reply can be.
3. Read the reviewed file back with `get_annotations`, filter on what they left, and act.

Colour is the natural verdict channel, and it is **the reviewer's convention, never this tool's**.
Nothing here decides that orange means delete. If deletion is the outcome, filter `get_annotations`
and pass the boxes to `redact_regions`, which verifies the removal the way it always does — there is
no "redact what I highlighted" shortcut on purpose, because that step should be one you took
deliberately rather than one a colour triggered.

## What the reply's fields mean

* **`marks_requested`** is how many marks you passed; **`marks_added`** is the net change in
  annotation count. They differ whenever a mark merged into one already on the page, which is normal
  and not a failure — re-running the same call gives `marks_added: 0` and a file identical in
  content to the first run's, note included.
  **`marks_added` can also be negative**, and that is correct rather than a bug: a mark that bridges
  two existing same-colour marks absorbs both and leaves one where there were two, so a call
  requesting one mark reports `marks_added: -1`. Read it as "the net change in how many annotations
  the page carries", never as "how many marks were laid".
* **`annotations`** is `get_annotations`' own output narrowed to **the spans this call touched**,
  read back off the written file rather than echoed from the request. So it shows the marks as they
  *are* — post-merge geometry, the colour actually stored, any note inherited from an absorbed mark
  — which is what you want before showing a person what you did. Boxes here can be fed to
  `render_page(clip=...)` to show them the pixels. It is deliberately **not** every mark on the
  pages touched: adding one mark to a page already holding eighty would otherwise return
  eighty-one, a reply bounded by the page's history rather than by your request.

  **Three kinds of entry can appear, and `mine` tells them apart.** A mark this call *wrote*; a mark
  it *merged into* (same type and colour, absorbed); and a mark it *landed beside* — one at the same
  span that this call could not merge with because KlarPDF did not write it (`mine: false`). The
  third is included on purpose: if you have just laid a highlight over a reviewer's, their mark is
  exactly what you need to see, and it pairs with the `warnings` entry naming its author.
* **`pages_annotated`** lists the pages touched, 1-based.
* **`warnings`** appears when the document's permissions ask readers not to annotate it (advisory,
  enforced by nothing — the marks are written, and the user should be told before the file is
  shared), or when a requested mark overlaps one this app did not write. Relay both.

## Where the output appears, and when

The file at `out` materialises only when the call **finishes**: the write goes to a temporary file
in the output directory and is renamed into place at the end, so a crash cannot leave a
half-written PDF where you expect a good one. A caller polling `out` mid-call sees nothing and
should not read that as failure. Nothing is ever written over the input — `out` must be a new path
(or pass `overwrite: true` to replace an existing file that is not the input).

## Marking a phrase, not its words

Pass the boxes of **one** `search` hit as **one** mark. Marking a phrase word by word gives one mark
per word, and the words do not touch: `New` ends at x=184.49 while `York` begins at 187.54, a 3 pt
gap, so the merge leaves two marks where the reader sees one continuous phrase. A hit's own `boxes`
already covers the whole phrase, including across a line wrap.

## Which corner the coordinates start from

Boxes are measured from the **top-left** of the page, y increasing **downward**. The PDF format
itself measures from the bottom-left with y increasing upward, and so does almost every other PDF
library — the flip is PyMuPDF's, applied on the way in and out, and the file written is always
standard PDF.

Inside these tools it never matters: `search` → `annotate` → `get_annotations` → `redact_regions`
are one frame end to end, and boxes pass between them untouched. It matters at the seam with
anything that read the file directly. A box built against the PDF's own origin lands **mirrored
about the page's horizontal axis**: still valid, still on the page, no error raised — just the wrong
line. Convert with `y' = page_height - y` (`get_info` reports each page's height).

`get_annotations` reports `snippet` and `text_length` for every mark precisely so this is visible
rather than silent: if a mark's snippet is not the text you meant to mark, the box was wrong. On a
page with no text layer both come back empty, which is indistinguishable from a wrong box — the one
case this check cannot cover.

## Merging, in detail

`merge_markup` is the app's own function, so a mark written here and one drawn by hand **in
KlarPDF** resolve the same way. Against markup of the **same type** that the new boxes overlap:

* **Same colour** → absorbed. The old mark is dropped and its bars folded into the new one, so
  re-marking a marked span is a no-op and extending one grows it in place. A pass bridging two
  same-colour marks merges all three.
* **Different colour** → trimmed. The covered span is cut out of the old mark and the new colour
  takes it; a cut through the middle splits the old mark, leaving the parts you did not cover in
  their original colour.

Different types never interact: a highlight and an underline over the same words are two marks, as
they are in any PDF reader. Boxes must genuinely **overlap** to merge — touching exactly does not,
and neither does a 0.01 pt intersection, though 0.5 pt does.

**Merging never crosses authorship.** A mark made in Acrobat, Edge, Preview or any other tool is
never absorbed and never trimmed, whatever its type or colour: a new mark over one is simply added
beside it, and the reply's `warnings` names the author whose mark you landed on. This is deliberate
and will not change — merging *deletes* a mark, and silently deleting a reviewer's annotation to
reattribute their span would be far worse than leaving a duplicate.

The consequence for the review workflow below: step 2 assumes the person reviews **in KlarPDF**,
where every mark this tool wrote is ordinary and editable. A reviewer working in Acrobat or Edge
produces marks the next `annotate` pass stacks against rather than merges with. Read them with
`get_annotations` — they are all reported, with `mine: false` — and decide what to do with them
yourself.

**On a page with no text layer, merging works on plain geometry.** Two overlapping marks become the
single box *enclosing* both, which on a scan paints corners that were in neither. On a text page
this is invisible because the bars follow the lines; on a scan there is no such structure to follow.
The resulting box is in the reply, so nothing is hidden.

**Notes are never lost to a merge.** An absorbed mark's note is carried onto the survivor, joined
with a blank line when several arrive. A note you pass in this call joins them rather than replacing
them, and a note **already present on the mark is not added again** — so re-running a call leaves
the note reading once, not twice. (Matching is on the whole note as a blank-line-separated segment,
so a note that itself contains a blank line is ambiguous under that encoding and can still
duplicate.) The rule is that only deleting a mark deletes its note.

## Notes are a field of their mark, not an object

A note is the annotation's PDF `/Contents`, which is how Acrobat, Preview and Edge all store a
comment on a highlight — so a note written here shows up as a comment in those readers too, and a
comment written in them is readable by `get_annotations`. There is no standalone sticky note in this
tool: pass `note` with no `type` and it creates a highlight to carry it, which is what the app does
when a note is dropped on unmarked text.

Note text is **not** body text: `search` and `extract_text` will not find it, in this tool or in the
app's find bar.

## What is not here

Ink, lines, rectangles, ellipses and text boxes are drawing rather than markup, take different
geometry, and are not written by this tool. Neither is editing an annotation that already exists —
recolouring one, deleting one, or attaching a note to a mark someone else made. `annotate` adds
marks; the app edits them.
""",
    "get_annotations": """\
## Every mark, not just this app's

The listing walks the page's real annotations rather than only the ones the model can redraw, which
matters more than it sounds. A reviewer working in Acrobat or Edge may leave their comments as
**sticky notes** rather than as notes on highlights; those are a type this app displays but cannot
edit, and a listing built on what is editable would have skipped them silently. If someone wrote it
on the page, it is in this list.

Two flags separate the cases, and they differ in both directions:

* **`mine`** — KlarPDF wrote this mark (its PDF author tag says so).
* **`editable`** — the mark round-trips as an editable object in the app. A foreign highlight is
  `editable: true, mine: false`: the app adopts it when you double-click it. A sticky note is
  `false` on both — displayed, movable, deletable, but not editable in place.

Form-field widgets are excluded; `get_form_fields` reports those properly, with values and states.
Link rectangles are not annotations here either.

## Reading the whole document: `offset` and `more_available`

This is the one tool here that **paginates**, and it needs to be, because the usual advice does not
apply. Every other capped tool answers truncation with *narrow the request* — `search` says tighten
the query, `export_images` says list the directory. There is no query to tighten here: the marks are
simply on the page, so the only lever is `pages`, and it fails precisely in the case that overflows,
where one dense page carries four hundred marks and cannot be narrowed at all.

So the reply is bounded by a mark count **and** a character budget — a mark's JSON runs 213-613
characters depending on its note, so a count alone does not bound the size — and whole marks are
dropped rather than trimmed:

* **`total_annotations`** — how many marks are in scope in total, whatever came back. Read it first:
  it tells you how many rounds to expect before you start.
* **`count`** / **`offset`** — how many this reply holds, and where it started.
* **`more_available`** — `true` when marks were left behind. Call again with
  `offset = offset + count` and keep going until it is `false`.

**One exception to "whole marks, never trimmed": a note long enough to blow the whole budget on its
own.** A mark is never dropped for being too big — a batch that returned nothing while saying more
was available would page forever — so instead that mark's **note** is cut, and only its note.
Everything you filter on (`boxes`, `color`, `color_name`, `page`, `type`, `snippet`, the flags) is
small, bounded and comes through intact. Such a mark carries **`note_truncated: true`** and
**`note_length`**, the original character count, so the reply says plainly that there is more text
and how much. Nothing else in the listing is affected.

The order is stable across calls (page order, then each page's own annotation order), and no write
tool can change the file underneath you — every one of them writes to a *new* path — so a plain
integer offset is safe here in a way it usually is not. No cursor, no snapshot, no staleness.

**The risk this trades for, and it is on you:** a truncated reply looks exactly like a complete one
once you have filtered it. If you are collecting "everything highlighted in orange" across a
document and you stop at the first batch, you have an incomplete answer that reads as total. Never
report a filtered result from a reply whose `more_available` is `true`.

## `boxes` and the redaction hand-off

`boxes` is in **unrotated page points** — the same space `search` reports hits in and
`redact_regions` and `clip` consume — at every page rotation. So a filtered list goes straight on:

    get_annotations → keep the ones you want → redact_regions([{page, boxes}, …])

Text markup carries **one box per line** it covers, so a highlight over a phrase that wraps has two,
and both belong to that one mark. Pass all of a mark's boxes or you will redact half of it. For
types with no line geometry — a sticky note, a rectangle, a text box — `boxes` is the single
annotation rectangle.

The boxes come from the annotation's quad points, not its `/Rect`, which is padded a few points
wider on every side; using the rect would silently over-cover a redaction built from it.

Coordinates are measured from the **top-left**, y downward — see `klarpdf://docs/annotate` for the
conversion and for why a box from another library lands mirrored.

## `snippet` and `text_length`

The text each mark's own boxes actually cover. `snippet` is windowed for reading, the way a `search`
hit's is; `text_length` is the full character count, unwindowed, so a box that looks plausible but
covers three paragraphs still stands out.

They are here to make a wrong box **self-revealing**: if a mark's snippet is not the text you meant,
the coordinates were wrong, whatever produced them. On a page with no text layer both come back
empty — which is indistinguishable from a wrong box, and the one case this cannot catch.

## Colour

* **`color`** is the raw RGB as stored, `null` for an annotation that carries none.
* **`color_name`** is the nearest palette name and is **advisory** — near enough to be useful for
  filtering, never a claim about what was intended. It is `null` when nothing is close, rather than
  a misleading guess. Nearness is judged perceptually (luma-weighted), not by raw RGB distance, so
  another tool's default yellow is named `Yellow` rather than being pulled toward `Orange` by a
  difference the eye reads as paleness rather than hue.
* **`color_exact`** is `true` only when the colour *is* one of the app's **current** swatches. It is
  a fact about the stored value, not about who wrote the mark: a mark picked from KlarPDF's palette
  today is exact, one from Acrobat generally is not — but so is a KlarPDF mark written under an
  **older swatch set**, which reads `mine: true, color_exact: false`. Read it as "this is a palette
  colour I can match by name", never as "a human chose this from a menu".

**The two palettes are not one palette.** Highlights are Yellow, Green, Blue, Pink, Orange; lines
(underline and strikeout) are Red, Blue, Green, Black — and the shared names are **different
colours**: a highlight Blue is a pale wash, a line Blue is a saturated ink, and they sit further
apart than any two swatches within either set. So filtering `color_name == "Blue"` across mixed mark
types collects two visibly different colours. Filter on `type` as well when the distinction matters.

Filter on colour when the workflow assigned it a meaning, and show the user what matched before
acting on it — the meaning is theirs, and this tool has no opinion about it.

## `note`

The annotation's `/Contents`, empty string when there is none. This is the same field the app's note
editor writes and the same one Acrobat and Preview use for a comment on a highlight, so a review
done in any of them reads back here.
""",
    "get_links": """\
## The fields, one by one

* **`page`** — the 1-based page the link's rectangle sits *on*, never where it goes.
* **`rect`** — `[x0, y0, x1, y1]` in the same **unrotated page points** `search` reports,
  `render_page`'s `clip` takes and `redact_regions` consumes, so a link feeds any of them with no
  arithmetic. `rect[0]` is the indent, and the indent is often the outline level — see below.

  Coordinates run from the **top-left**, y increasing downward. The PDF format itself measures from
  the bottom-left, so a rectangle you built by reading the raw file — or took from another
  library — needs `y' = page_height - y` before it will line up with these. A box against the wrong
  origin lands **mirrored about the page's horizontal axis**: on the page, no error, wrong line.

  On a **rotated** page this is a conversion, not a pass-through, and it is the one place links and
  annotations genuinely differ. `get_annotations` needs no adjustment because PyMuPDF already
  stores and reports annotation geometry unrotated; a link's rectangle comes out of the library in
  *displayed* space, which turns with `/Rotate`, and is converted here. You see the unrotated
  quadruple at 0°, 90°, 180° and 270° alike.
* **`kind`** — `goto` (a page in this file), `named` (also a page in this file — see below),
  `uri` (a web, `tel:` or `mailto:` address), `gotor` (a page in *another* file), `launch` (opens
  another file). There is no `none` row: a rectangle that goes nowhere is counted in
  `links_without_action` instead, and `kinds: ["none"]` is rejected.

  **`goto` and `named` both mean "a page in this file", and the difference is real.** A `goto`
  link writes its destination down. A `named` link writes a *nickname* — `Chapter3` — that the
  document keeps a separate lookup table for, so the target can move without every link being
  rewritten. If a document's name table is incomplete, its `named` links are the ones that break;
  its `goto` links cannot.

  The label describes **the document**, not how it parsed. That is worth stating because the
  underlying library decides between the two by pattern-matching, and mislabels an ordinary
  `<< /S /GoTo /D [46 0 R /Fit] >>` as named whenever the destination carries a `/Fit`-style view
  instead of a zoom; those are corrected here. To list every internal jump regardless, ask for
  `["goto", "named"]`.
* **`target_page`** — the 1-based page an internal link jumps to, and `null` for every other kind.
  It is deliberately `null` for `gotor`, which does carry a page number: that number is a page in
  the *other* document, and reporting it here would say a link goes to your page 4 when it opens
  somebody else's.
* **`uri`** — the address, for `uri` links only, exactly as the file spells it.
* **`file`** — the other document, for `gotor` and `launch` only. Treat it as untrusted text: it is
  a path chosen by whoever made the PDF.
* **`text`** — the words the rectangle covers, or `null` when it covers none. It is read by word
  **centre**, so it is the anchor rather than whatever shares the rectangle's band. Two documents
  in the wild will surprise you: a page carrying **two overlapping text layers** (the same words
  typeset twice, differing only in straight vs. smart quotes) legitimately returns both, so a
  title reads `"8.1.8 “8.1.8`; and a link over a photograph or a logo returns `null`, which is a
  fact about the document and not a failure to read it. Building an outline from `text` means
  de-duplicating the string yourself.
* **`links_with_unresolved_target`** — on the reply, not the row: how many of the rows returned
  are internal links (`goto` or `named`) whose `target_page` came back `null`. The row is still
  returned, because its rectangle and its anchor text are true and only the destination is not —
  and a link that names a destination the document does not define is a defect in the document
  worth seeing rather than one worth hiding. Unlike `links_without_action` this counts the rows
  **returned**, so it moves with `kinds` and `offset`.
* **`links_without_action`** — on the reply, not the row: how many `/Link` annotations in scope
  name no destination at all. These are **not** returned as rows, because a dead hotspot a designer
  left behind is not a place the document points, and listing it in a privacy audit would be noise.
  The count is here so the arithmetic still closes: `total_links + links_without_action` is the raw
  `/Subtype/Link` count, which is what an auditor or a migration script reconciles against. It is
  almost always 0. There is no `kind: "none"` row and `kinds: ["none"]` is rejected, for the same
  reason — it could only ever match nothing.

## Rebuilding a contents page from links

When `get_outline` returns `count: 0` and the document has a printed contents page, that page is
usually the answer already: read its links, and each one carries the title (`text`), the target
(`target_page`) and the level (`rect[0]`, the indent). Four rules earn their keep, all of them
learned from real documents:

* **Merge continuations before reading indents.** A heading that ran onto a second line arrives as
  two rows. Merge them, then read the indent — the continuation often sits at the *outer* indent and
  would otherwise read as a phantom top-level section.

  Two rows sharing a `target_page` at adjacent `rect[1]` is the **first** test, and on a densely
  sectioned document it is not sufficient: a prospectus routinely puts several sections on one page,
  so `2.1 Responsibility Statement` and `2.2 General Disclaimers` share target page 50 and sit
  14 pt apart while being different sections. When the entries carry **dot leaders**, those settle
  it: a complete entry ends in a run of dots and a page number, so **the row *missing* the leader is
  the incomplete one — it is the *first* line, and it merges with the row below it.** Measured over
  a 400-page prospectus, 104 contents rows: 101 carry a leader, and the 3 that do not are exactly
  the three wrapped first lines. Note the direction — the leader runs to the page number at the
  *end* of the entry, which lands on its **last** line, so it is the continuation that carries the
  dots. Merging upward instead of downward corrupts two entries at once.
* **Dedupe by target — only when the layout is one entry per page.** A magazine links each entry
  twice, once on its photograph and once on its caption; same target, one entry. **This rule is
  destructive on an indexed document**, where many sections legitimately begin on the same page:
  measured on that prospectus, 101 entries span 60 distinct target pages (one page carries six
  sections), so deduping by target would have discarded **41 of 101 entries — 41% of the outline**.
* **Drop the rows with no `text`.** They are the picture half of that pair.
* **Distinct indents are not always levels.** They are in an indented list; in a magazine grid the
  anchors sit anywhere from x0 10 to 233 and mean nothing. When the indents do not fall into two or
  three tight clusters, emit a flat level 1 rather than inventing a hierarchy out of layout noise.

Watch also for a link that appears on many pages pointing *backwards* to one of them: that is a
running footer, not a contents entry. A 35-page brochure carried 13, all aimed at its contents page;
taken as entries they would have produced thirteen junk bookmarks.

### Which of these rules apply is a property of the layout — and the reply tells you

The four are not a checklist to run in order: two contents pages can need **opposite** readings, and
applying the wrong profile silently produces a plausible outline that is wrong. Classify the
document first, from the reply you already have:

| | **Indexed / dense** (prospectus, standard, manual) | **Magazine grid** (brochure) |
|---|---|---|
| rows per target | uneven — 6, 3, 1 … | exactly 2, every time |
| rows with `text: null` | ~none | ~half (the photograph) |
| dot leaders | present | absent |
| `rect[0]` | two or three tight clusters | scattered across the page |
| **dedupe by target** | **no** — it deletes real entries | **yes** |
| **continuations** | dot-leader test | rare; adjacency is enough |
| **levels** | from the indent clusters | flat level 1 |

The cheap discriminator is the first two rows of that table: compute rows-per-target, and if every
target has exactly two rows with half the anchors empty, it is a grid. Uneven counts mean an indexed
document, and dedupe must stay off.

What to do with the entries once you have them: **`set_outline`** takes exactly this shape —
`[{level, title, page}]`, where `page` is the link's `target_page` — and writes it into a copy of
the document as real bookmarks. Every viewer then has the contents page in its sidebar, and the
navigation stops depending on a reader finding page 3. `set_outline` refuses a page the document
does not have, so a rule you applied wrongly surfaces as an error rather than as a bookmark quietly
pointing at the nearest real page.

## What this does not see: URLs that are only printed

A link is an **annotation** — an object in the file with a rectangle and an action. A document can
show a reader an address without carrying one, and plenty do: bills and statements typeset
`www.example.com/autopay` as ordinary glyphs and never wrap it. Most viewers auto-linkify anything
URL-shaped, so those look and behave like links on screen while being invisible here — one real
6-page bill returns **0 links** and prints **five** distinct URLs.

That is the honest answer for a tool that reads what the document actually declares, and it is the
same fidelity that makes `target_page` trustworthy. But it means "where does this document point"
has two halves, and this tool answers one. For a privacy sweep, run `search` for `http`, `www.`,
`@` and `tel:` alongside this, or `extract_text` the pages and scan them yourself. Say which half
you checked when you report.

## Counting, filtering and paging

**`kinds`** is a count per kind over everything scanned, taken *before* the `kinds` filter and
before the caps — so a call filtered to `["uri"]` still tells you how many internal jumps the
document holds, and an unfiltered call tells you what a narrower one would cost. **`total_links`**
is the number in scope *after* `kinds`, and it is what `offset` walks.

The reply is bounded by a link count **and** a character budget, because a count alone does not
bound a reply: an entry runs 127-647 characters depending on its anchor text and URI, and 502 links
measured out at 79,518 characters. Whole links are dropped, never trimmed. When `more_available` is
`true`, call again with `offset = offset + count` until it is `false` — or narrow first, which is
cheaper: `kinds: ["uri"]` on a prospectus cut 502 links to 37.
""",
    "set_outline": """\
## The entry shape, and why it is the one `get_outline` returns

An entry is `{"level": 1, "title": "Introduction", "page": 12}` and nothing else — an unknown key
is an error, not an ignored extra, because an agent writing `text` instead of `title` from memory
would otherwise get an outline of empty rows and a success report counting them.

`level` is 1 for a top-level heading and 2 for a subsection under the entry above it. `page` is
1-based, and it is the page the bookmark *lands on*, not the page the heading is printed on if
those differ. Order matters: the outline is read top to bottom, and an entry's parent is the
nearest preceding entry with a lower level.

Because the shape is `get_outline`'s, a document's own outline round-trips through this tool
unchanged, and the two compose: read, splice, write.

## Pages are checked; levels are repaired

The two arguments are treated differently on purpose, and the difference is about what can be
recovered from a mistake.

**A page outside the document is an error and nothing is written.** The PDF layer will not refuse
one: measured against a 6-page document, `page: 99` writes a bookmark to page 6, `page: 0` writes
one to page 1, and `page: -1` writes a bookmark with no destination at all — one that appears in
the sidebar and navigates nowhere. All three report success. There is no way to tell afterwards
that a number was wrong, so it is checked before anything is written.

**A level sequence that a PDF cannot express is repaired.** An outline must begin at level 1 and
may not skip a level, which a contents page derived from indents does not satisfy on its own — one
that opens at the second indent is ordinary. So levels are normalised, relative nesting is kept,
and the reply carries `levels_normalised` listing every entry whose level moved, plus a `warnings`
line. Read it: it is also how a genuine mistake shows up, since a level that jumps from 1 to 4 is
repaired to 2 and named.

## What the write costs, and what it keeps

The page set does not change, so this is the preserving route — the accessibility structure tree,
`/Perms`, the `/Names` tree, encryption and permissions all come through. A restricted published
manual comes back restricted, which is the point: the reason to add navigation to one is to hand
back the same document with bookmarks.

On a document with **no outline**, the write is an incremental append: the original bytes are left
exactly where they are and the new objects go on the end. Measured, +772 bytes on a 40-page file
with the first 16,925 byte-identical.

On a document that **already has** an outline, the file is rewritten instead. That is deliberate.
An append cannot take anything away — the entries being replaced would survive in the revision
underneath, and measured, the old bookmark titles are still readable in the output's bytes. If the
titles you are replacing are the sensitive part, this is the case to know about, and the rewrite is
what makes replacing them mean it.

## Enriching an outline the document already has

If the document has bookmarks, this **refuses** unless you pass `replace_outline: true`. That is
not an obstacle to work around — it is the fork in the road, and the two ways past it are different
operations.

**To enrich** (the usual intent — the document has chapters, you want sections under them):

```
existing = get_outline(path)["entries"]     # [{level, title, page}], the same shape
merged   = ...                              # weave yours in; keeping one is one `+`
set_outline(path, merged, out, replace_outline=True)
```

You send the **whole tree**, not a delta. There is no "insert into" operation, because deciding
where a new entry belongs is a judgement about meaning: whether a derived *Revenue by quarter*
duplicates an existing *Q3 Revenue*, parents it, or sits beside it cannot be settled by rule. That
onus is yours, and this tool will not pretend to take it — it never merges.

**To discard** the existing bookmarks deliberately, pass `replace_outline: true` and send only your
own entries. The reply's `replaced` count tells you how many went.

Where the sub-entries come from is the open part. `get_links` is exact when the document has a
printed contents page, but a document that already ships bookmarks usually does not have one —
the bookmarks *are* its navigation. Failing that today: `extract_text` over the section's page
range and read it, or `render_page` and look. Note that `extract_text` returns plain strings with
no font or weight, and weight is usually the signal that separates an unnumbered subheading from
body text set at the same size.

## What it does not do

It **replaces**; it does not merge. An outline is a single tree and interleaving two of them has no
right answer — see above.

It does not **remove** an outline: `entries: []` is refused. An empty list is far more often a
filter that matched nothing than a request to strip a document's navigation, and the destructive
reading of an ambiguous argument is not the one to take. (`replace_outline: true` is the same
principle said the other way: the destructive reading is available, but only when you ask for it.)

It does not write **destination points within a page** (a bookmark to a heading halfway down),
colours, or open/collapsed state. Each entry lands at the top of its page, which is what a
contents-page link resolves to for a reader anyway.
""",
    "get_tables": """\
## The two readers, and why there is no third

A table is returned only when the document itself says where the cells are.

**A drawn grid.** Lines box every cell in, so nothing is inferred. Across a 14-document corpus this
returned 15 tables with no damage of any kind.

**Drawn rules for the rows, alignment for the columns.** The common filing shape: a rule above and
below the header and nothing else. Taking the rows from the drawn rules is what keeps the column
inference honest — it has to explain only the horizontal spacing within a row it already knows the
bounds of. 22 tables, 2 damaged.

**Inferring both axes was measured and rejected.** It adds about ten tables across the same corpus,
four of them damaged, and its failure mode is the one you cannot detect from the reply: it drops
leading characters. `Beginning balance` arrives as `eginning balance`, `Total current assets` as
`tal current assets`. Nothing in the row says a character is missing. Rather than return that, the
page is declined and named in `unread_regions`.

## What `unread_regions` means, and what to do about it

Each entry gives `page`, `bbox`, a `reason` and a `suggestion`. It means: there is something
table-shaped here and reading it as a grid would have required guessing.

`extract_text` on that page returns **every value the table holds**, in reading order, one per
line — the header cells first, then each row's cells in order. What you do not get is the grouping,
so `Name / 2024 Bonus Target / 2025 Bonus Target / Mr. Ricks / 150% / 175%` arrives flat and you
infer that it is a three-column table. That is usually easy. It degrades when a row has **blank
cells**, because a missing value simply is not in the sequence and nothing marks which column it
belonged to. For a sparse table, `render_page` and read the image.

## Fields

| field | meaning |
|---|---|
| `rows` | every row, as a list of lists of strings |
| `header` | the first row **when a drawn grid proves it is a header**, else null |
| `title` | the caption above the table, or null — best-effort, see below |
| `title_from_previous_page` | the title was stranded at the foot of the previous page |
| `continues_from` | page number this *may* continue from — a hint, never applied |
| `bbox` | `[x0, y0, x1, y1]`, unrotated, top-left origin — feeds `render_page`'s `clip` |
| `row_count` / `col_count` | the shape actually returned |

## `header` is null more often than you expect, and that is deliberate

A table read from **drawn rules** routinely starts its band one row inside the table, so `rows[0]`
is the first line of data and the real header is left outside it. Apple's 10-Q page 4 comes back
with `["Products $", "78,678 $", ...]` in that position — plausible column names, and wrong.
PyMuPDF's own header detection does not help: it reports `external: false` and hands back the same
first row, assuming exactly what it was asked to determine.

So `header` is filled in only where a drawn grid settles it, and null everywhere else. When it is
null, look at `title` — a header that fell outside the band is often picked up there — and at
`rows[0]`, which may or may not be one. This is the same rule as everything else here: state it
where the document states it, say nothing where it would be a guess.

## A long label can be split across the first two cells

Where a row label runs past the first column's right edge, it arrives as two cells —
`["Liabilities and Stockholders'", "Equity"]`, `["Property and equipment,", "net"]`. This is not
damage and nothing is lost: **the split is placed where no word is cut, so joining the two with a
space reproduces the printed line.** A row where the label fits keeps its own cell and the second
cell holds the second column's value as usual, so the test is whether the second cell looks like a
continuation of the first rather than a value.

The same rule holds for a label the page **outdents** left of the table's own column — a section
header, a total. Those are read from the page's margin rather than from the column, so the first
character is present; earlier builds cut it (`Total current assets` as `otal current ass`).

## Titles are best-effort

The caption is taken from the nearest block above the table that reads like one — short, not a
sentence, not mostly numbers — **stepping over any introductory paragraph in between**, which is
the common report shape (heading, paragraph, table). It can still be wrong: a column heading that
sits at the table's left margin can be picked up instead, and a table with no caption may borrow a
section heading. Treat `title` as a label to show a user, not as a key to match on.

A title *below* a table is not handled. Searching the corpus for caption-style titles
(`Table 3:`, `Figure 1.`) across six documents found none, so there is no evidence either way and
the rule stays where the evidence is. A below-lookup was tried and actively broke a real document:
it stole the stranded title that belongs to the *next* page's table.

## Continuation is flagged, never applied

Two tables on consecutive pages can share a header and column positions exactly and still be
different tables — a product manual in the corpus does this, one reporting 24 hours and the next 28.
Merging them by geometry produces plausible nonsense. So `continues_from` is a hint for you to
judge, and a table that received a stranded title from the previous page is never marked as a
continuation, because that title is evidence it starts something new.

## Accounting negatives

A negative prints as `(1,234)`. When a column edge falls inside it the closing bracket lands in the
next cell and the value reads positive — a sign error nothing downstream can see. Measured over 36
such cells in a prospectus, **35 keep the opening bracket and lose only the closer**, so a leading
`(` with nothing closing it is restored to `(1,234)`.

**The 36th lost its opening bracket instead** and still reads positive. That case is not repaired:
treating a stray closing bracket as a sign is not sound in the other direction. If you are reading a
cash-flow statement where sign matters, spot-check against `render_page`.

## Cost

Reading tables runs at about 6 pages/s against `extract_text`'s 158, which is why `pages` is
required. Narrow first with `get_outline`, `search`, or `extract_text`'s `table_pages` — that last
one names every page this tool can read a grid from, plus some it cannot, at no meaningful cost.
""",
    "search": """\
## Feeding hits straight to `redact_regions`

`boxes` is normally a single rectangle. A phrase that wraps a line break occupies one on **each**
line, the way a find bar highlights a wrapped match, and all of them come back under the one hit —
so `count` counts occurrences, not fragments. Redact every box of a hit: clearing only the first
leaves the tail of the phrase legible. Passing the whole hit to `redact_regions` as
`{"page": hit["page"], "boxes": hit["boxes"]}` does that for you.

## Why `whole_words: false` is usually right for an identifier

A one-word query makes the two modes differ only in the boundary rule, so `whole_words: false`
cannot over-match it — while `true` will miss the value entirely whenever it is embedded in a longer
unbroken run. Machine tags, filenames, URLs and `key:value` pairs are all that shape.
""",
}
"""Reference material by tool name. A tool with no entry serves its description alone."""
