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

* **`residual_literal`** counts places the query still appears *literally* that the `whole_words`
  setting does not match, with each one named in `warnings`. It is not automatically a leak —
  redacting whole-word "Smith" leaves "Smithsonian" and says so — but if a named survivor is the
  value you meant to remove, re-run with `whole_words: false`. This is the check that catches the
  matcher being wrong, so it is the one worth reading.
* **`residual_normalized`** names spellings of the query still in the file that differ from it only
  in separators — `6073474692031` against a query of `607347469 203 1`, or `08/24/1970` against
  `08-24-1970`, or a number broken by a line wrap. Nothing was deleted for these: whether two
  spellings are one value is a fact about the document that only you have. If they are, redact those
  forms too. **An empty list and `null` are different answers.** `[]` means the scan ran and found
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
