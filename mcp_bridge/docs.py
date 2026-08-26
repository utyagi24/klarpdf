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
  content to the first run's.
* **`annotations`** is `get_annotations`' own output for the pages this call touched, read back off
  the written file rather than echoed from the request. So it shows the marks as they *are* —
  post-merge geometry, the colour actually stored, any note inherited from an absorbed mark — which
  is what you want before showing a person what you did. Boxes here can be fed to
  `render_page(clip=...)` to show them the pixels.
* **`pages_annotated`** lists the pages touched, 1-based.

## Merging, in detail

`merge_markup` is the app's own function, so a mark written here and one drawn by hand resolve the
same way. Against markup of the **same type** that the new boxes overlap:

* **Same colour** → absorbed. The old mark is dropped and its bars folded into the new one, so
  re-marking a marked span is a no-op and extending one grows it in place. A pass bridging two
  same-colour marks merges all three.
* **Different colour** → trimmed. The covered span is cut out of the old mark and the new colour
  takes it; a cut through the middle splits the old mark, leaving the parts you did not cover in
  their original colour.

Different types never interact: a highlight and an underline over the same words are two marks, as
they are in any PDF reader.

**Notes are never lost to a merge.** An absorbed mark's note is carried onto the survivor, joined
with a blank line when several arrive. A note you pass in this call joins them rather than replacing
them. The rule is that only deleting a mark deletes its note.

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

## Colour

* **`color`** is the raw RGB as stored, `null` for an annotation that carries none.
* **`color_name`** is the nearest palette name (Yellow, Green, Blue, Pink, Orange, Red, Black) and
  is **advisory** — near enough to be useful for filtering, never a claim about what was intended.
  It is `null` when nothing is close, rather than a misleading guess.
* **`color_exact`** is `true` only when the colour *is* one of the app's swatches. A mark made in
  KlarPDF carries an exact value; one made in Acrobat generally does not. Use it to tell "the
  reviewer picked Orange from the menu" from "something orange-ish arrived from elsewhere".

Filter on colour when the workflow assigned it a meaning, and show the user what matched before
acting on it — the meaning is theirs, and this tool has no opinion about it.

## `note`

The annotation's `/Contents`, empty string when there is none. This is the same field the app's note
editor writes and the same one Acrobat and Preview use for a comment on a highlight, so a review
done in any of them reads back here.
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
