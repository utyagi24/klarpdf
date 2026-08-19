"""Destructive, leak-verified redaction — the reason this bridge is worth building.

`redact_mcp`, the one PDF-redaction MCP server found in the 2026-08 sweep, paints a **visual overlay
and calls it redacted**: the text is still in the file, one `pdftotext` away from whoever receives
it. That is not a smaller version of this feature, it is the failure mode this feature exists to
prevent, and it is why PLAN.md §Why calls the destructive-redaction half the load-bearing argument
for the whole roadmap.

So the contract here is deliberately stronger than "we called `apply_redactions`":

1. **Capture what is there first.** Before anything is removed, the text under each region is read
   out of the source. That string is the thing that must not survive.
2. **Remove it destructively.** `model/page_edits.py:apply_redactions` runs PyMuPDF's
   `apply_redactions` on the materialised copy — the glyphs, images and vector graphics under each
   box are physically deleted, and the redaction annotation is *consumed*, so the output carries
   nothing that could be deleted to reveal what was under it.
3. **Verify the written file, with a second engine.** Re-open the output and confirm the captured
   text is gone — with PyMuPDF always, and with Poppler's `pdftotext` whenever it is on the machine.
   Two engines matter because the claim "the text is gone" should not rest on the same library that
   performed the removal.
4. **Verify the *query*, not just the boxes.** Step 3 asks "did the regions I redacted lose their
   text?", and on its own that is a check which cannot fail on a matching bug: it derives its budget
   from the boxes it chose, so an occurrence the matcher never found simply widens the allowance by
   exactly the amount it then leaks. `redact_text` therefore re-runs its own search against the
   output and requires **zero** surviving matches — the document-level property a caller reads a
   success as promising. M44's verification pass found this the expensive way (TC-001): a phrase
   redaction left two thirds of its occurrences legible and every box-level check passed.
5. **Never report success on an unverified file.** If either check fails the output is **deleted**
   and the tool raises. A caller must not be handed a path to a file that looks redacted and is not.

**Where the guarantee stops, stated rather than implied** (PLAN.md §Honesty principle): this covers
the *text layer*. Text baked into a scanned image is pixels — the box is painted over it and the
pixels under it are removed with the image, but there is no text to verify, so `verified_text` comes
back empty and the caller is told so. When Poppler is absent the result says which engines actually
ran; it never claims a cross-engine check it did not perform.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pymupdf as fitz

from model.page_edits import Redaction
from model.page_text import PageText
from mcp_bridge.queries import open_document, resolve_pages, search
from mcp_bridge.transforms import _resolve_out, _write

# Poppler's extractor. A *different* implementation from the PyMuPDF writer, which is the whole
# point of running it — a bug that hid text from fitz would have to be present in both to slip past.
_PDFTOTEXT = "pdftotext"

# Verification failure is a security event, not a bad argument, so it gets its own exception type
# rather than sharing ValueError with "you passed a bad page number".
class RedactionLeak(RuntimeError):
    """Redacted content was still recoverable from the written file. The output has been deleted."""


def _poppler_text(path: str, page1: int, password: str | None = None) -> str | None:
    """Page ``page1`` (1-based) as Poppler sees it, or ``None`` if Poppler is not installed.

    Scoped to the single page with ``-f``/``-l``: the same string surviving on a *different*,
    deliberately unredacted page must not mask a real leak on this one. ``-upw`` is passed for an
    encrypted document — without it Poppler cannot read the file, and a check that silently cannot
    run is worse than no check.
    """
    if shutil.which(_PDFTOTEXT) is None:
        return None
    command = [_PDFTOTEXT, "-f", str(page1), "-l", str(page1)]
    if password:
        command += ["-upw", password]
    result = subprocess.run(
        [*command, path, "-"], capture_output=True, text=True, check=True
    )
    return result.stdout


def _open_verified(path: str, password: str | None):
    """Open a written output for reading back, authenticating if we re-encrypted it.

    A redacted copy of an encrypted document is **still encrypted** (M54 carries the password
    through), so verification has to unlock it — otherwise the check cannot read the file at all,
    which is the one outcome that must never be mistaken for "nothing found".
    """
    doc = fitz.open(path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise RedactionLeak(
                f"cannot verify {path!r}: it is encrypted and could not be unlocked, so the "
                "removal is UNVERIFIED. The output has been deleted."
            )
    return doc


def _count(text: str, token: str) -> int:
    return text.count(token)


def _shortfall(
    page1: int, token: str, out: str, found: int, allowed: int,
    before: int, covered: int, engine: str,
) -> str:
    """Explain a failed budget — and say which of the two ways it failed.

    ``allowed`` is negative when more boxes claimed to cover a token than the source ever contained
    it, which is not a leak at all: it means the token was **derived wrongly**, so no output could
    ever satisfy the check. That case used to print ``max(allowed, 0)``, rendering an impossible
    budget of -1 as ``at most 0 expected`` beside ``still appears 0 time(s)`` — a message that reads
    as a contradiction and sent TC-005's reporter looking for a comparison bug that was not there.
    The arithmetic was right; only the number shown was wrong, and it hid the real fault one layer
    down (M97).
    """
    if allowed < 0:
        return (
            f"page {page1}: cannot verify {token!r} in {out!r} — {covered} redaction box(es) claim "
            f"to cover it but the source contained it {before} time(s), so no output could satisfy "
            f"the check. That is a bug in how the token was derived, not a leak in the file "
            f"({engine} found it {found} time(s) after redaction)."
        )
    return (
        f"page {page1}: {token!r} still appears {found} time(s) in {out!r} "
        f"(at most {allowed} expected after redaction) — {engine}"
    )


def _verify(out: str, expectations: dict[int, dict], password: str | None) -> dict:
    """Confirm each redacted token lost at least as many occurrences as boxes covered it.

    **Counts, not presence** — and the difference is not pedantry. Redacting the standalone "Smith"
    out of "Smith and Smithsonian" leaves "Smithsonian", which still *contains* the substring
    "Smith"; a presence check would call that a leak and destroy a perfectly good output. So for
    each page and token the rule is::

        occurrences_after <= occurrences_before - boxes_that_covered_it

    which is exact in both directions: it cannot be tripped by surviving unrelated text, and it
    still catches the case where two boxes covered the same word and only one was removed (a
    presence check would pass that, since one occurrence legitimately remains).

    ``expectations`` maps 1-based page → ``{"boxes": {token: n}, "fitz_before": {token: n},
    "poppler_before": {token: n}}``. Raises :class:`RedactionLeak` on the first shortfall.

    **What this cannot catch**, and why :func:`_no_residual_match` exists: the budget above is
    derived from the boxes this call chose, so a match the *matcher* never found is not a shortfall
    here — it is an occurrence that was never counted as covered, and its surviving text is spent
    against the allowance it was never charged for. The arithmetic stays self-consistent while the
    output leaks. Proving the boxes were emptied is a different claim from proving the query is
    gone, and only the second one catches a matching bug.
    """
    engines = ["pymupdf"]
    poppler_available = shutil.which(_PDFTOTEXT) is not None
    if poppler_available:
        engines.append("poppler")

    doc = _open_verified(out, password)
    try:
        for page1, expected in expectations.items():
            after = doc[page1 - 1].get_text("text")
            for token, covered in expected["boxes"].items():
                allowed = expected["fitz_before"].get(token, 0) - covered
                found = _count(after, token)
                if found > allowed:
                    raise RedactionLeak(
                        _shortfall(page1, token, out, found, allowed,
                                   expected["fitz_before"].get(token, 0), covered, "PyMuPDF")
                    )
    finally:
        doc.close()

    if poppler_available:
        for page1, expected in expectations.items():
            after = _poppler_text(out, page1, password) or ""
            for token, covered in expected["boxes"].items():
                allowed = expected["poppler_before"].get(token, 0) - covered
                found = _count(after, token)
                if found > allowed:
                    raise RedactionLeak(
                        _shortfall(page1, token, out, found, allowed,
                                   expected["poppler_before"].get(token, 0), covered, "Poppler")
                        + ". PyMuPDF reported it removed, so this is exactly the cross-engine "
                        "disagreement the second check exists to catch"
                    )
    return {
        "verified_with": engines,
        "cross_engine_verified": poppler_available,
        "verification_note": (
            "Text-layer removal confirmed by PyMuPDF and by Poppler, a different engine from the "
            "one that performed the removal."
            if poppler_available
            else "Text-layer removal confirmed by PyMuPDF only — Poppler (pdftotext) is not "
            "installed on this machine, so the cross-engine check did NOT run. Install "
            "poppler-utils for the stronger guarantee."
        ),
    }


def _word_bounded(haystack: str, needle: str, start: int) -> bool:
    """Do whole-word boundaries hold around ``haystack[start:start + len(needle)]``?

    The textual twin of :meth:`PageText.is_whole_word`, and deliberately the *same rule*: expand to
    the whitespace-delimited token on each side and require that whatever sits between the token's
    edge and the match carries no word content. ``expression.`` is a match for ``expression``;
    ``ALPHA-zero-A0`` is not one for ``ALPHA``. Two checks that disagreed about what "whole word"
    means would be worse than one.

    Assumes ``haystack`` has already been flattened to single spaces, so a token boundary is a
    space or an end of string.
    """
    end = start + len(needle)
    left = haystack.rfind(" ", 0, start) + 1
    right = haystack.find(" ", end)
    right = len(haystack) if right == -1 else right
    outside = haystack[left:start] + haystack[end:right]
    return not any(ch.isalnum() or ch == "_" for ch in outside)


# An identifier written two ways is still one identifier. `607347469 203 1` and `6073474692031`
# are the same policy number, `08-24-1970` and `08/24/1970` the same date, and a literal scan sees
# neither in the other — so a caller who redacts one form is told the file is clean while the other
# form is still in it (TC-007). Dropping every non-alphanumeric character collapses the whole family
# in one pass: separator *substitution* and separator *removal* normalise identically.
#
# **This reports; it never matches and never deletes.** Whitespace-insensitive *matching* in a
# destructive tool would be dangerous — `12345` would start matching across table columns — and the
# decision that two spellings denote one value is document semantics only the caller has. So the
# scan runs after the write, on the output, and its entire output is a sentence.
#
# Two guards, both set by measurement over 49 documents and 270 identifier-shaped queries rather
# than by taste (probe recorded in PLAN.md §M98):
#
# * **A floor on the query.** Without it `000000` matched across `708.000 0.00` — digits from two
#   unrelated numbers, welded by dropping the separators. Every false positive in the corpus came
#   from a query like that, and none survived the floor.
# * **A boundary test on the *original* text, not the normalised stream.** TC-007 proposed
#   "the match must not sit inside a longer alphanumeric run", which is vacuous once applied to the
#   normalised form: stripping separators makes the whole stream alphanumeric, so every interior
#   match is inside a longer run. It has to be read against the source, which needs the offset map
#   below. Measured effect: 9 of 53 candidate hits suppressed.
#
# Measured precision after both guards: **41 extra hits across the corpus, every one a real
# variant** — including `526-\n5999`, an identifier broken by a line wrap, which no literal scan
# can see at all.
# **The floor applies to a query the caller wrote as a bare run, and not to one they punctuated.**
# The first version applied it to every query and was wrong in a way the corpus could not show:
# `999 99 9999`, `4444 5555` and `AB 12 CD` are obviously structured identifiers, and all three were
# silently skipped — the first two for repeating a character, the third for being six characters
# long (TC-007 retest). The probe missed it because it generated candidates from documents with a
# digit-run regex, so it never asked what happens to a short or repetitive query that a *person*
# typed with separators in it.
#
# Separators are the caller telling you this is a structured value. `000000` is a bare run and could
# be anything, so it must earn the scan by being long and varied; `999 99 9999` has already said
# what it is. Re-measured over the same 49 documents: scanning 36 more queries produced **exactly
# the same 41 hits**, so the relaxation costs no precision at all.
_MIN_NORMALISED_CHARS = 7   # a bare run this short is where coincidence lives…
_MIN_DISTINCT_CHARS = 3     # …as is one that repeats a single character
_MIN_STRUCTURED_CHARS = 4   # a punctuated query has declared itself; it only has to be non-trivial


def _normalise(text: str, *, match_case: bool) -> tuple[str, list[int]]:
    """``text`` reduced to its alphanumerics, plus each kept character's index in the original.

    The index map is what makes the boundary test possible: a hit is found in the normalised
    stream and judged in the source, which is the only place the separators still exist.
    """
    kept, index = [], []
    for i, ch in enumerate(text):
        if ch.isalnum():
            kept.append(ch if match_case else ch.casefold())
            index.append(i)
    return "".join(kept), index


def _worth_scanning(query: str, *, match_case: bool) -> bool:
    """Is ``query`` specific enough that a separator-insensitive match means something?

    Two floors, because the two kinds of query carry different amounts of information. A query with
    a separator in it — `999 99 9999`, `AB 12 CD` — has been *declared* a structured value by the
    person who typed it, and only has to be non-trivial. A bare run like `000000` has declared
    nothing, so it must be long and varied enough that finding it across a separator is unlikely to
    be two unrelated numbers touching.
    """
    normalised, _ = _normalise(query, match_case=match_case)
    if any(not char.isalnum() for char in query.strip()):
        return len(normalised) >= _MIN_STRUCTURED_CHARS
    return (len(normalised) >= _MIN_NORMALISED_CHARS
            and len(set(normalised)) >= _MIN_DISTINCT_CHARS)


def _variant_residuals(text: str, query: str, *, match_case: bool) -> list[str]:
    """Spellings of ``query`` still in ``text`` that differ from it only in separators.

    Returns each survivor **as the document writes it**, which is the part a caller can act on:
    ``'6073474692031'`` next to a query of ``'607347469 203 1'`` is self-evidently the same policy
    number, and that judgement is theirs to make rather than the tool's to guess.
    """
    if not _worth_scanning(query, match_case=match_case):
        return []
    needle, _ = _normalise(query, match_case=match_case)
    flat, index = _normalise(text, match_case=match_case)
    found: list[str] = []
    start = flat.find(needle)
    while start != -1:
        first, last = index[start], index[start + len(needle) - 1]
        before = text[first - 1] if first else ""
        after = text[last + 1] if last + 1 < len(text) else ""
        if not before.isalnum() and not after.isalnum():
            written = " ".join(text[first:last + 1].split())
            if written != " ".join(query.split()) and written not in found:
                found.append(written)   # only the spellings that are NOT the query itself
        start = flat.find(needle, start + 1)
    return found


def _literal_residuals(text: str, query: str, *, match_case: bool) -> list[str]:
    """The whitespace-delimited tokens of ``text`` that still contain ``query`` **literally**.

    The check that owes the matcher nothing — no term splitting, no boundary rule, no
    ``whole_words``. It exists because :func:`_residual_in_text`, the pass written to be
    independent of the matcher, is not: it shares :func:`_word_bounded`, and a shared predicate
    means a shared blind spot. TC-003 is the proof. Redacting ``220885-1063303`` with
    ``whole_words: true`` cleared the two plain occurrences and left the two inside
    ``<AccountNumber:220885-1063303>``, which neither the matcher nor its "independent" verifier
    could see — so the tool reported ``residual_matches: 0``, ``cross_engine_verified: true``, and
    handed back a file with the account number still in it.

    **Reported, never fatal**, and that is not timidity — a literal scan cannot be a leak test.
    Redacting whole-word ``Smith`` out of ``Smith and Smithsonian`` correctly leaves
    ``Smithsonian``, which literally contains the query;
    ``test_a_legitimate_survivor_is_not_mistaken_for_a_leak`` pins that, and failing here would
    delete a perfectly good output. So the tokens come back instead of a count, and the token is
    what tells the two apart at a glance: ``Smithsonian`` reads as obviously fine and
    ``<AccountNumber:220885-1063303>`` reads as obviously not.
    """
    needle = " ".join(query.split())
    if not needle:
        return []
    tokens = " ".join(text.split()).split(" ")
    if not match_case:
        needle = needle.casefold()
    found: list[str] = []
    # A query with spaces spans tokens, so the window has to be as wide as the query is.
    width = len(needle.split())
    for start in range(len(tokens)):
        window = " ".join(tokens[start:start + width])
        if needle in (window if match_case else window.casefold()) and window not in found:
            found.append(window)
    return found


def _residual_in_text(text: str, query: str, *, match_case: bool, whole_words: bool) -> int:
    """How many times ``query`` still matches in ``text``, by the tool's own search semantics.

    Whitespace is flattened first, which is what lets this see a phrase the page broke across a
    line — the exact shape that survived redaction in TC-001, and one a naive substring scan of
    extracted text misses entirely.

    **This shares the matcher's whole-word rule on purpose and is therefore not the independent
    check it was once described as** (the docstring below used to claim it "owes the matcher
    nothing"; it owes it :func:`_word_bounded`). Keeping the two in step is right for deciding
    *what to redact* — two matchers disagreeing about what a word is would be worse than one — and
    wrong for a safety net, which is only worth having if it can fail when the matcher does. That
    is :func:`_literal_residuals`.
    """
    flat = " ".join(text.split())
    terms = [query] if whole_words else query.split()
    if not match_case:
        flat, terms = flat.lower(), [term.lower() for term in terms]
    total = 0
    for term in terms:
        start = flat.find(term)
        while start != -1:
            if not whole_words or _word_bounded(flat, term, start):
                total += 1
            start = flat.find(term, start + 1)
    return total


def _no_residual_match(
    out: str,
    query: str,
    *,
    match_case: bool,
    whole_words: bool,
    scope: set[int],
    password: str | None,
) -> dict:
    """Confirm ``query`` no longer matches the written output anywhere in scope.

    Three passes, because they fail for different reasons and only together are they worth much:

    1. **Re-run the tool's own search.** This makes the contract checkable by the caller in the
       obvious way — `search` on the returned path comes back empty. It catches a *coverage* gap:
       the matcher saw an occurrence and the redaction did not clear it (a wrapped phrase where
       only one fragment was boxed is exactly this).
    2. **Scan each engine's extracted text**, with :func:`_residual_in_text`. It catches what pass 1
       cannot see for having consumed the same search machinery, and with Poppler installed it does
       not even share the extractor. TC-001 was this shape: a phrase invisible to the matcher at
       both ends.
    3. **Scan the same text literally**, with :func:`_literal_residuals` — no boundary rule, nothing
       borrowed from the matcher. Passes 1 and 2 both honour ``whole_words``, so a query the
       *matcher* cannot see is a query neither of them can see either, and the file is pronounced
       clean twice over. That is TC-003, and it is why this pass exists.

    Passes 1 and 2 **fail** the call and delete the output. Pass 3 **warns**: a literal hit is not
    proof of a leak (redacting whole-word ``Smith`` legitimately leaves ``Smithsonian``), so it
    reports the surrounding token and lets the caller judge. The distinction is the whole design —
    a check that cannot be trusted to fail must not be wired to a delete, and a check that cannot
    fail at all is decoration.

    Scoped to the pages the call was asked to redact — an occurrence on page 7 of a ``pages=[2]``
    request is out of scope, not a leak, and failing on it would make the page filter unusable.

    **All four scans stay inside that scope, and the reply must therefore say what the scope was**
    (M103, TC-007 Finding D). Everything here describes the operation the caller asked for, which is
    the right rule and the one the owner settled on: a reply that mixed page-scoped and
    document-wide results would be worse than one that is consistently narrow. But the two advisory
    fields were spelled as if they were absolute — ``residual_literal: 0`` and
    ``residual_normalized: []`` are documented as "the scan ran and found nothing" — so a call with
    ``pages=[1, 3]`` reported them about a document it had read two pages of. Nothing here can be
    misread as *success*, since success is signalled by returning at all rather than by any field;
    what it could be misread as is *"no homework"*. So :func:`redact_text` reports
    ``residual_scope`` beside them and warns when ``pages`` narrowed it. ``pages_redacted`` is not a
    substitute: it lists where boxes landed, which is a strictly smaller set — ``[1]`` for a call
    that scanned ``[1, 2, 3]``.
    """
    leaks = [hit for hit in search(out, query, match_case=match_case,
                                   whole_words=whole_words, password=password)
             if hit["page"] in scope]
    if leaks:
        # `boxes`, not `box`: a hit occupies one rectangle per line since #250, and reading the old
        # singular key here raised `KeyError` instead of `RedactionLeak` — which `_finish` does not
        # catch, so the unverified output was never deleted (M102).
        where = "; ".join(f"page {hit['page']} at {hit['boxes']}" for hit in leaks[:5])
        more = f" (+{len(leaks) - 5} more)" if len(leaks) > 5 else ""
        raise RedactionLeak(
            f"{query!r} still matches {len(leaks)} time(s) in {out!r} — {where}{more}. The "
            "redacted boxes came back clean, so this is a coverage gap rather than a removal "
            "failure: some occurrences were never boxed. The output has been deleted."
        )

    doc = _open_verified(out, password)
    try:
        extracted = [("PyMuPDF", page1, doc[page1 - 1].get_text("text")) for page1 in sorted(scope)]
    finally:
        doc.close()
    if shutil.which(_PDFTOTEXT) is not None:
        extracted += [("Poppler", page1, _poppler_text(out, page1, password) or "")
                      for page1 in sorted(scope)]

    for engine, page1, text in extracted:
        found = _residual_in_text(text, query, match_case=match_case, whole_words=whole_words)
        if found:
            raise RedactionLeak(
                f"page {page1}: {query!r} still reads back {found} time(s) from {out!r} "
                f"({engine}), even though the tool's own search reported the file clean. A matcher "
                "cannot see an occurrence it failed to redact, which is why this check does not "
                "use it. The output has been deleted."
            )

    survivors: list[str] = []
    for _engine, _page1, text in extracted:
        for token in _literal_residuals(text, query, match_case=match_case):
            if token not in survivors:
                survivors.append(token)
    report: dict = {"residual_matches": 0, "residual_literal": len(survivors)}
    if survivors:
        shown = ", ".join(repr(token) for token in survivors[:5])
        more = f" (+{len(survivors) - 5} more)" if len(survivors) > 5 else ""
        report["warnings"] = [
            f"{query!r} still appears literally in {len(survivors)} place(s) that "
            f"`whole_words: {str(whole_words).lower()}` does not match: {shown}{more}. "
            + (
                "Each is inside a longer unbroken run of characters — a machine-readable tag, an "
                "identifier, a filename — which whole-word matching treats as a different word. If "
                "any of those is the value you meant to remove, re-run with `whole_words: false`."
                if whole_words
                else "This is expected when the query is a substring of a longer word that was "
                "meant to survive. Check each one before sending the file on."
            )
        ]

    # The variant scan reads one engine's extraction, not both: it is advisory, and reporting the
    # same spelling twice because two extractors saw it would be noise dressed as thoroughness.
    #
    # **`[]` and absence are different answers, and the key is always present to keep them apart.**
    # A scan that ran and found nothing is reassurance; a scan that never ran is not, and if both
    # are spelled by omitting the field then the caller reads the second as the first — which is
    # precisely the invisible failure this feature exists to close (TC-007 retest). So: a list means
    # it looked, `null` means it did not, and the `null` says why.
    variants: dict[str, list[int]] = {}
    if not _worth_scanning(query, match_case=match_case):
        report["residual_normalized"] = None
        report.setdefault("warnings", []).append(
            f"{query!r} was NOT scanned for separator variants — it is a short unpunctuated run, "
            "where matching across separators finds coincidence rather than spellings (digits from "
            "two neighbouring numbers, say). Nothing here says the file is free of variants; it "
            "says this query cannot be checked for them. Check by hand if it is an identifier."
        )
        return report
    for engine, page1, text in extracted:
        if engine != "PyMuPDF":
            continue
        for written in _variant_residuals(text, query, match_case=match_case):
            pages = variants.setdefault(written, [])
            if page1 not in pages:
                pages.append(page1)
    report["residual_normalized"] = [
        {"as_written": written, "pages": sorted(pages), "count": len(pages)}
        for written, pages in variants.items()
    ]
    if variants:
        listed = "; ".join(f"{w!r} on page(s) {sorted(p)}" for w, p in list(variants.items())[:4])
        report.setdefault("warnings", []).append(
            f"the characters of {query!r} also appear written differently and are still in the "
            f"file: {listed}. They differ only in spacing or punctuation, so if that is the same "
            "value, redact those spellings too — nothing here was deleted. This is a report, not a "
            "match: whether two spellings mean one thing is a fact about the document, and only "
            "you have it."
        )
    return report


def _covered_tokens(page: fitz.Page, boxes: list[tuple]) -> dict[str, int]:
    """How many times each checkable token is covered by ``boxes``, counting each one **once**.

    ``PageText`` indexes the page once and answers by character *centre* rather than by clipping,
    so a box gets what it actually covers instead of whatever shared its horizontal band — the
    difference matters here, because a verification string that was never under the box would make
    the check pass for the wrong reason.

    **Counted over the union of the boxes, not box by box** (M100). Two boxes covering the same
    characters — which is what a multi-query redaction produces the moment one term is a substring
    of another's match — would otherwise each contribute the token, and the check would claim to
    have removed ``607347469`` three times from a page that contained it twice. ``_verify`` reads
    that as ``allowed = 2 - 3 = -1``, an expectation no output can satisfy, and deletes a correct
    redaction. The union is taken by :meth:`PageText.text_under_all`, at the character rather than
    the rectangle, because overlapping rectangles and shared characters are not the same question.

    This also pairs the two sides of the budget correctly for the first time. ``fitz_before`` has
    always counted *occurrences* (``str.count``), while this counted *distinct tokens per box* — so
    one box over ``203 1 203`` claimed a single removal of ``203`` against a before of two, and the
    surviving copy was permitted. Occurrences on both sides is the arithmetic ``_verify`` describes.

    **Single-character tokens count too** (M103, TC-008 Finding B). They were dropped from M41
    onward by a ``len(part) >= 2`` filter whose only stated rationale was the phrase "tokens worth
    checking for individually" — no design note, no test. The cost was not cosmetic: this dict *is*
    what :func:`_verify` checks, so redacting ``1`` produced ``verified_text: {}`` beside
    ``boxes_redacted: 2`` in the same reply, and the box-level cross-engine check ran **zero**
    assertions. On the over-redaction path 216 of 240 boxes came from the term ``1``, and the field
    that exists to say "here is what I deleted" never mentioned it.

    The filter's plausible motive — that a box's edge catches a stray character and litters the
    report — does not survive measurement: :class:`PageText` answers by character *centre*, tight
    enough that a box over ``Smith`` yields exactly ``Smith``, and the only things the filter ever
    dropped were genuine one-character tokens. Nor is the check it suppressed vacuous, which was the
    other worry: on ``Item 1 and item 1 again, ref 2031`` the budget is ``before 3 − covered 2 = 1``,
    requiring the ``1`` inside ``2031`` to survive and the two standalone ones to go.
    """
    counts: dict[str, int] = {}
    for token in PageText(page).text_under_all(boxes).split():
        counts[token] = counts.get(token, 0) + 1
    return counts


def _apply(vdoc, per_page: dict[int, list[tuple]], source: str, password: str | None) -> dict:
    """Attach a :class:`Redaction` per page and record what verification will expect afterwards.

    The "before" counts are read here, from the still-intact source, because after materialise
    there is nothing left to count. Both engines are measured, since each has its own idea of how a
    page's text runs together and comparing one engine's before to another's after would be noise.
    """
    expectations: dict[int, dict] = {}
    for index0, boxes in per_page.items():
        page1 = index0 + 1
        ref = vdoc.ordered[index0]
        page = vdoc.sources[ref.source_id][ref.source_page_index]

        covered = _covered_tokens(page, boxes)

        fitz_before = page.get_text("text")
        poppler_before = _poppler_text(source, page1, password) or ""
        expectations[page1] = {
            "boxes": covered,
            "fitz_before": {t: _count(fitz_before, t) for t in covered},
            "poppler_before": {t: _count(poppler_before, t) for t in covered},
        }
        vdoc.add_annotation(index0, Redaction(tuple(boxes)))
    return expectations


def _finish(
    vdoc,
    target: str,
    expectations: dict,
    source: str,
    password: str | None,
    residual=None,
    **extra,
) -> dict:
    """Write, verify, and delete the output if any check fails.

    ``residual`` is the optional document-level check (:func:`_no_residual_match`), passed by
    ``redact_text`` because only a query-driven redaction has a query to re-run. It returns the
    fields it verified (and any ``warnings``), and runs inside the same ``try`` as :func:`_verify`
    so both failures take the one delete-and-raise path — the "never leave a false-secure file
    behind" invariant is not a thing to reimplement per caller.
    """
    _write(vdoc, target)
    try:
        report = _verify(target, expectations, password)
        if residual is not None:
            report = _merged(report, residual(target))
    except RedactionLeak:
        if os.path.exists(target):
            os.remove(target)  # never leave a false-secure file behind
        raise
    return _merged(
        {
            "out": target,
            "pages": vdoc.page_count,
            "bytes": os.path.getsize(target),
            "source": os.path.abspath(source),
            "source_unchanged": True,
            "verified_text": {
                str(page1): sorted(expected["boxes"])
                for page1, expected in expectations.items()
                if expected["boxes"]
            },
        },
        report,
        extra,
    )


def _merged(*parts: dict) -> dict:
    """Combine result fragments, **concatenating** ``warnings`` instead of overwriting it.

    Two independent checks now emit warnings — the literal residual scan and the invisible-text
    report — and a plain ``{**a, **b}`` would keep whichever ran last and drop the other without a
    word. On a tool whose job is to tell the caller what they cannot see for themselves, a silently
    discarded warning is the same class of defect as the one this milestone exists to fix.
    """
    out: dict = {}
    for part in parts:
        for key, value in part.items():
            if key == "warnings" and key in out:
                out[key] = out[key] + value
            else:
                out[key] = value
    return out


def redact_regions(
    path: str,
    regions: list[dict],
    out: str,
    *,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Destructively remove rectangular regions and verify the removal.

    ``regions`` is a list of ``{"page": 1-based, "box": [x0, y0, x1, y1]}`` in page points — the
    coordinate space ``search`` and ``get_form_fields`` already report boxes in.

    ``boxes`` (a list of rectangles) is accepted in place of ``box``, which is what a ``search`` hit
    carries: a match wrapping a line break occupies a rectangle on each line, so a hit goes straight
    back in as one region without the caller having to take it apart — and without the trap of
    passing ``boxes[0]`` and redacting half a phrase.
    """
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        per_page: dict[int, list[tuple]] = {}
        for region in regions:
            if (not isinstance(region, dict) or "page" not in region
                    or not ("box" in region or "boxes" in region)):
                raise ValueError(f"each region needs 'page' and 'box' (or 'boxes'); got {region!r}")
            if "box" in region and "boxes" in region:
                raise ValueError(f"each region needs 'box' or 'boxes', not both; got {region!r}")
            (index0,) = resolve_pages(vdoc, [region["page"]])
            raw = [region["box"]] if "box" in region else list(region["boxes"])
            if not raw:
                raise ValueError(f"region {region!r} carries no boxes")
            for entry in raw:
                box = tuple(float(v) for v in entry)
                if len(box) != 4:
                    raise ValueError(f"box must be [x0, y0, x1, y1]; got {entry!r}")
                if box[0] >= box[2] or box[1] >= box[3]:
                    raise ValueError(f"box {list(box)} is empty or inverted")
                per_page.setdefault(index0, []).append(box)
        if not per_page:
            raise ValueError("no regions given — redaction must remove something")
        expectations = _apply(vdoc, per_page, path, password)
        return _finish(
            vdoc,
            target,
            expectations,
            path,
            password,
            regions=len(regions),
            pages_redacted=sorted(expectations),
        )


def _resolve_queries(query: str | None, queries: list[str] | None) -> list[str]:
    """The list of queries to remove, from the one-or-many pair (M100).

    Exactly one of the two, because a call carrying both has two readings and neither is safe to
    guess at in a tool that deletes. Duplicates collapse — asking twice for the same string is not
    an error, but reporting it twice would suggest the tool did something twice.
    """
    if (query is None) == (queries is None):
        raise ValueError("pass either `query` (one string) or `queries` (a list), not both/neither")
    given = [query] if queries is None else list(queries)
    if not given:
        raise ValueError("`queries` is empty — a redaction must remove something")
    resolved: list[str] = []
    for entry in given:
        if not isinstance(entry, str):
            raise ValueError(f"each query must be a string; got {entry!r}")
        if not entry.split():
            raise ValueError(f"query {entry!r} is empty — nothing to redact")
        if entry not in resolved:
            resolved.append(entry)
    return resolved


def redact_text(
    path: str,
    query: str | None,
    out: str,
    *,
    queries: list[str] | None = None,
    match_case: bool = False,
    whole_words: bool = False,
    pages: list[int] | None = None,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Find every occurrence of ``query`` — or of each of ``queries`` — remove it, then verify.

    Search semantics are the app's find bar exactly (see :func:`mcp_bridge.queries.search`), which
    matters more here than anywhere else: with ``whole_words`` off, a search for "Smith" also
    matches inside "Smithsonian", and this tool *deletes* what it finds. Preview with ``search``
    first — its snippets are what let a caller see that before it is irreversible.

    Verified twice over, because the two checks answer different questions. :func:`_verify` proves
    the removal was *destructive* — the text under each box is gone from the file, confirmed by a
    second engine. :func:`_no_residual_match` proves it was *complete* — re-running this same
    search against the output finds nothing left in scope. A box-level check alone cannot fail on a
    matching bug, which is the failure mode with teeth: it is how M44's verification pass got a
    file with a legible `regular expression.` in it and a success report to go with it (TC-001).
    With several queries **every one of them** is verified, to the same standard, against the one
    output; a query whose residual check fails deletes that output exactly as it would alone.

    **``queries`` exists for data hygiene, not for typing less** (M100). Chaining six identifiers
    through six calls leaves five intermediate files on disk, each a partially-redacted copy still
    holding live PII, and every one of them has to be remembered and deleted — sprawl caused by our
    own rule that a write always needs a fresh ``out``, which makes it ours to remove. One pass also
    retires an ordering hazard: chained calls had to run longest-query-first or a shorter one left
    fragments of a longer match behind, whereas here every box is computed against the *intact*
    source before anything is applied, so order cannot matter.

    Raises rather than writing an untouched copy when nothing matches: a redaction tool reporting
    success over a file it did not change is how a secret ships. With several queries the rule is
    that **none** of them matched — one that finds nothing while another succeeds is reported in
    its ``matches: 0`` and a warning, because failing the whole call would leave the caller with
    nothing for the five that were found.
    """
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    all_queries = _resolve_queries(query, queries)
    with open_document(path, password) as vdoc:
        scope = resolve_pages(vdoc, pages)
        per_page: dict[int, list[tuple]] = {}
        invisible: dict[int, list[str]] = {}
        stats: dict[str, dict] = {
            q: {"matches": 0, "terms": {}, "phrase": 0} for q in all_queries
        }
        for index0 in scope:
            ref = vdoc.ordered[index0]
            page = vdoc.sources[ref.source_id][ref.source_page_index]
            text: PageText | None = None
            page_invisible: list[str] | None = None
            # Box-groups already added on this page, across **all** queries. Two queries matching
            # the same run of text is the normal case for overlapping identifiers, and each should
            # report its own `matches` — but the text is one piece and gets boxed once.
            added: set = set()
            for one in all_queries:
                terms = [one] if whole_words else one.split()
                per_term = [(term, [(r.x0, r.y0, r.x1, r.y1) for r in page.search_for(term)])
                            for term in terms]
                if not any(boxes for _term, boxes in per_term):
                    continue
                if text is None:
                    text = PageText(page)
                    page_invisible = invisible.setdefault(index0 + 1, [])
                stat = stats[one]
                if len(terms) > 1 and not whole_words:
                    # What the caller would have got had they meant the phrase. Costs one extra
                    # `search_for` on pages that already matched, and it is the number that makes an
                    # over-redaction legible — see `_term_report`.
                    phrase_boxes = [(r.x0, r.y0, r.x1, r.y1) for r in page.search_for(one)]
                    stat["phrase"] += len(text.group_matches(phrase_boxes, one))
                seen: set = set()
                for term, term_boxes in per_term:
                    # Grouped, so a match wrapping a line break is one occurrence — and **every** box
                    # it occupies is redacted. Clearing only the first is what left the tail of the
                    # phrase legible next to a black box in TC-001.
                    for boxes in text.group_matches(term_boxes, term):
                        key = tuple(tuple(round(v, 2) for v in box) for box in boxes)
                        if key in seen:
                            continue
                        seen.add(key)
                        if whole_words and not all(text.is_whole_word(box) for box in boxes):
                            continue
                        if match_case and not all(text.matches_case(box, term) for box in boxes):
                            continue
                        stat["matches"] += 1
                        stat["terms"][term] = stat["terms"].get(term, 0) + 1
                        if key in added:
                            continue
                        added.add(key)
                        per_page.setdefault(index0, []).extend(boxes)
                        if any(text.is_invisible(box) for box in boxes):
                            page_invisible.append(text.snippet_for(boxes))
        if not per_page:
            listed = all_queries[0] if len(all_queries) == 1 else all_queries
            raise ValueError(
                f"{listed!r} was not found — nothing was redacted and no file was written "
                "(use `search` to check what matches before redacting)"
            )
        matches = sum(stat["matches"] for stat in stats.values())
        expectations = _apply(vdoc, per_page, path, password)
        page_scope = {index0 + 1 for index0 in scope}
        return _finish(
            vdoc,
            target,
            expectations,
            path,
            password,
            residual=lambda written: _query_reports(
                written,
                all_queries,
                stats,
                match_case=match_case,
                whole_words=whole_words,
                scope=page_scope,
                password=password,
                flat=queries is None,
                page_filtered=pages is not None,
            ),
            matches=matches,
            boxes_redacted=sum(len(boxes) for boxes in per_page.values()),
            pages_redacted=sorted(expectations),
            # What the residual scans actually read — *not* `pages_redacted`, which lists only where
            # boxes landed and is a strictly smaller set (`[1]` for a call that scanned `[1, 2, 3]`).
            # Without this the advisory zeros are bare, and "the scan found nothing" cannot be told
            # apart from "the scan found nothing on the two pages it read" (M103, TC-007 Finding D).
            residual_scope=sorted(page_scope),
            **({"query": query} if queries is None else {}),
            **_invisible_report(invisible),
        )


# Above this many misses the warning is summarised rather than repeated per query. Three is the
# point where a list stops reading as "these ones" and starts reading as wallpaper — and the cost of
# wallpaper is not its size but what it buries: a 60-query call produced 59 near-identical ~330
# character warnings, so a genuine over-redaction warning among them would have been line 37 of 59
# (TC-007 Finding A). `residual_literal`'s own warning already truncates with `(+N more)`.
_WARNING_DETAIL_LIMIT = 3


def _zero_match_warning(
    missed: list[str], total: int, *, whole_words: bool, scope: set[int], page_filtered: bool
) -> str:
    """One warning for every query that matched nothing, however many there are.

    **The advice leads with the page filter when there is one** (M103, TC-007 Finding E). The old
    text sent the caller to audit their spelling — "it may be spelled in a way this mode cannot
    see" — when the cause may simply be that they restricted the call to pages the value does not
    appear on. The response already knows ``pages`` was supplied, so blaming the query first is
    advice the tool has the information to know might be wrong.
    """
    shown = ", ".join(repr(q) for q in missed[:_WARNING_DETAIL_LIMIT])
    if len(missed) > _WARNING_DETAIL_LIMIT:
        shown += f" (+{len(missed) - _WARNING_DETAIL_LIMIT} more — see `queries[]` for each)"
    head = (
        f"{len(missed)} of {total} queries matched nothing and removed nothing: {shown}."
        if len(missed) > 1
        else f"{missed[0]!r} matched nothing and removed nothing."
    )
    if page_filtered:
        why = (
            f" This call was restricted to pages {sorted(scope)}, so any occurrence elsewhere was "
            "never looked at — check that before treating this as absence."
        )
    else:
        why = (
            " If you expected it in the document, `search` before trusting its absence: with "
            f"`whole_words: {str(whole_words).lower()}` it may be spelled in a way this mode "
            "cannot see."
        )
    return (
        head
        + " The other quer(ies) in this call were still redacted and verified, and this output is "
        "theirs."
        + why
    )


def _query_reports(
    written: str,
    all_queries: list[str],
    stats: dict[str, dict],
    *,
    match_case: bool,
    whole_words: bool,
    scope: set[int],
    password: str | None,
    flat: bool,
    page_filtered: bool,
) -> dict:
    """Verify every query against the written output and shape the per-query half of the reply.

    ``flat`` is **which parameter the caller used, not how many queries survived** — and the
    distinction is the whole of the contract. A ``query`` returns exactly what it always did: the
    residual fields and any ``query_terms`` at the top level, no ``queries`` list, so no existing
    caller has to learn a new shape to keep doing what it was doing. A ``queries`` list always
    returns the list form, *including* when it holds one entry or when duplicates collapsed it to
    one. Branching on the count instead would hand a caller iterating a variable-length list a
    different reply shape on the days its list happened to have one element in it, which is a
    footgun of exactly the kind this module spends its time removing.

    The list carries one entry per query, each with that query's ``matches`` beside its own residual
    counts: those numbers only mean anything per query, and flattening six queries' fields into one
    set would silently report the last one's results as the whole call's.

    Warnings are concatenated across queries rather than merged, and stay legible because every
    warning this module writes already names its own query. A dropped warning is the failure this
    tool exists to prevent, which is why :func:`_merged` special-cases the key.

    A query that matched **nothing** while others matched is reported here rather than raised.
    Failing the whole call would delete a verified output that correctly removed the other five,
    leaving the caller worse off than the warning does. Those misses are gathered and reported
    **once** rather than per query — see :func:`_zero_match_warning`.
    """
    entries: list[dict] = []
    warnings: list[str] = []
    missed: list[str] = []
    for one in all_queries:
        stat = stats[one]
        report = _no_residual_match(
            written, one, match_case=match_case, whole_words=whole_words,
            scope=scope, password=password,
        )
        terms = [one] if whole_words else one.split()
        report = _merged(
            report, _term_report(terms, stat["terms"], stat["phrase"], whole_words=whole_words)
        )
        warnings += report.pop("warnings", [])
        if stat["matches"] == 0:
            missed.append(one)
        entries.append({"query": one, "matches": stat["matches"], **report})

    if missed:
        warnings.append(_zero_match_warning(missed, len(all_queries), whole_words=whole_words,
                                            scope=scope, page_filtered=page_filtered))

    if flat:
        merged = dict(entries[0])
        merged.pop("query", None)
        merged.pop("matches", None)   # already reported at the top level
    else:
        merged = {"queries": entries}

    if page_filtered:
        # Once per call, not once per query: the scope is a property of the call. Emitted even when
        # every advisory field is clean — *especially* then, since a bare `0`/`[]` is exactly what
        # reads as a document-wide all-clear (M103, TC-007 Finding D).
        warnings.append(
            f"the residual scans covered pages {sorted(scope)} only, because this call set "
            "`pages`. `residual_literal` and `residual_normalized` describe those pages and say "
            "nothing about the rest of the document — re-run without `pages` to check it whole."
        )
    if warnings:
        merged["warnings"] = warnings
    return merged


# How many times over the word-list reading has to out-delete the phrase before it is worth saying
# so. The comparison is the signal, not any one term's share: **share alone is a bad test**, because
# an ordinary two-word query whose second word is simply commoner ("John Smith", if Smith appears
# three times as often) reaches any share threshold without anything being wrong. Asking instead
# "how much more did this remove than the phrase you appear to have typed?" answers the actual
# question, and it stays quiet in the two cases that must not warn: a query whose phrase never
# occurs is a deliberate word list, and a query whose phrase accounts for most of the hits is
# behaving as the caller expects. TC-007 removed 240 where its phrase occurs 9 times — 26x.
_OVER_REDACTION_FACTOR = 3


def _term_report(
    terms: list[str], counts: dict[str, int], phrase_matches: int, *, whole_words: bool
) -> dict:
    """Per-term match counts, and a warning when the word-list split did most of the deleting.

    **The counterweight to over-redaction**, which had none. Everything else here guards the
    opposite failure: `residual_matches` and `residual_literal` prove the query is *gone*, and both
    are silent when a query removed far more than the caller meant. TC-007 hit exactly that — the
    default word-list mode split `607347469 203 1` into three terms, of which `1` matched every
    standalone digit in a 22-page document, and the call reported 240 boxes redacted with zero
    residuals, cross-engine verified, and nothing else to say.

    The asymmetry is worth naming because it is structural, not an oversight: a missed occurrence
    survives in the output and can be looked for, so it is checkable after the fact. Destroyed
    content leaves no trace in the output at all — the only record that it was ever there is the
    source, which this tool never touches. So the moment of the write is the only moment the
    warning can be given, and the numbers to give it with are already in hand.
    """
    if len(terms) < 2 or not counts:
        return {}
    total = sum(counts.values())
    report = {
        "query_terms": [{"term": term, "matches": counts.get(term, 0)} for term in terms],
    }
    if not whole_words and phrase_matches and total >= phrase_matches * _OVER_REDACTION_FACTOR:
        top, top_count = max(counts.items(), key=lambda kv: kv[1])
        report["warnings"] = [
            f"`whole_words` was not set, so this query was read as {len(terms)} separate words and "
            f"each was redacted wherever it appeared: {total} occurrences in all, {top_count} of "
            f"them from the single term {top!r}. The phrase {' '.join(terms)!r} itself occurs only "
            f"{phrase_matches} time(s), so this removed roughly {total // max(phrase_matches, 1)}x "
            "more than the phrase you appear to have meant. Re-run with `whole_words: true` if so "
            "— the input is untouched, so this output can simply be discarded."
        ]
    return report


def _invisible_report(invisible: dict[int, list[str]]) -> dict:
    """Report the occurrences that were removed but were never *visible* on the page.

    Good news that has to be said out loud, because it is unverifiable by the caller any other way.
    These were redacted — they are gone — but nobody looking at the document before or after could
    have known they were there: they do not appear in a render, and a human approving the change by
    comparing renders sees no difference. Saying so is what turns "the render looks right" from the
    caller's evidence into what it actually is (TC-003 ISSUE 2).

    It is also the tell for a document that hides data elsewhere. A bill that stamps its account
    number into white-on-white tags very likely stamps other things there too, and the caller has
    just learned that eyeballing the page is not enough for this file.
    """
    found = {page1: snippets for page1, snippets in invisible.items() if snippets}
    if not found:
        return {}
    total = sum(len(snippets) for snippets in found.values())
    shown = "; ".join(
        f"page {page1}: {snippets[0][:70]!r}" for page1, snippets in sorted(found.items())
    )
    return {
        "invisible_matches": total,
        "warnings": [
            f"{total} of the redacted occurrence(s) were **invisible** on the page — white-on-"
            f"white, transparent, or painted over — and are gone now ({shown}). They never showed "
            "in a render, so comparing the page before and after would not have revealed them, and "
            "nothing outside this tool would have told the reader they existed. Machine-readable "
            "tags like these often carry more than one identifier: consider `extract_text` on the "
            "output to see what else is in the text layer but not on the page."
        ],
    }
