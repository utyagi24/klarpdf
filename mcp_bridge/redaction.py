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
                        f"page {page1}: {token!r} still appears {found} time(s) in {out!r} "
                        f"(at most {max(allowed, 0)} expected after redaction) — PyMuPDF"
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
                        f"page {page1}: {token!r} still appears {found} time(s) in {out!r} "
                        f"(at most {max(allowed, 0)} expected) — Poppler. PyMuPDF reported it "
                        "removed, so this is exactly the cross-engine disagreement the second "
                        "check exists to catch"
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


def _residual_in_text(text: str, query: str, *, match_case: bool, whole_words: bool) -> int:
    """How many times ``query`` still matches in ``text``, by the tool's own search semantics.

    Whitespace is flattened first, which is what lets this see a phrase the page broke across a
    line — the exact shape that survived redaction in TC-001, and one a naive substring scan of
    extracted text misses entirely.
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
) -> int:
    """Confirm ``query`` no longer matches the written output anywhere in scope.

    Two passes, because they fail for different reasons and only both together are worth much:

    1. **Re-run the tool's own search.** This makes the contract checkable by the caller in the
       obvious way — `search` on the returned path comes back empty. It catches a *coverage* gap:
       the matcher saw an occurrence and the redaction did not clear it (a wrapped phrase where
       only one fragment was boxed is exactly this).
    2. **Scan each engine's extracted text directly**, with :func:`_residual_in_text`. This is the
       one that matters, because pass 1 shares a matcher with the redaction and therefore shares
       its blind spots — a matcher that cannot see an occurrence cannot see it on the way out
       either, and would report a clean file twice. TC-001 was precisely that: the phrase was
       invisible to the matcher at both ends. A textual scan owes the matcher nothing, and when
       Poppler is installed it does not even share the extractor.

    Scoped to the pages the call was asked to redact — an occurrence on page 7 of a ``pages=[2]``
    request is out of scope, not a leak, and failing on it would make the page filter unusable.

    Returns the verified residual count (always 0) so the caller can report the number it checked.
    """
    leaks = [hit for hit in search(out, query, match_case=match_case,
                                   whole_words=whole_words, password=password)
             if hit["page"] in scope]
    if leaks:
        where = "; ".join(f"page {hit['page']} at {hit['box']}" for hit in leaks[:5])
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
    return 0


def _tokens_under(page: fitz.Page, boxes: list[tuple]) -> list[str]:
    """The text under ``boxes``, split into tokens worth checking for individually.

    ``PageText`` indexes the page once and answers by character *centre* rather than by clipping,
    so a box gets what it actually covers instead of whatever shared its horizontal band — the
    difference matters here, because a verification string that was never under the box would make
    the check pass for the wrong reason.
    """
    text = PageText(page)
    tokens: list[str] = []
    for box in boxes:
        under = text.text_under(box).strip()
        tokens.extend(part for part in under.split() if len(part) >= 2)
    return sorted(set(tokens))


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

        covered: dict[str, int] = {}
        for box in boxes:
            for token in _tokens_under(page, [box]):
                covered[token] = covered.get(token, 0) + 1

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
    ``redact_text`` because only a query-driven redaction has a query to re-run. It runs inside the
    same ``try`` as :func:`_verify` so both failures take the one delete-and-raise path — the
    "never leave a false-secure file behind" invariant is not a thing to reimplement per caller.
    """
    _write(vdoc, target)
    try:
        report = _verify(target, expectations, password)
        if residual is not None:
            report["residual_matches"] = residual(target)
    except RedactionLeak:
        if os.path.exists(target):
            os.remove(target)  # never leave a false-secure file behind
        raise
    return {
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
        **report,
        **extra,
    }


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
    coordinate space ``search`` and ``get_form_fields`` already report boxes in, so a hit can be fed
    straight back in as a region.
    """
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        per_page: dict[int, list[tuple]] = {}
        for region in regions:
            if not isinstance(region, dict) or "page" not in region or "box" not in region:
                raise ValueError(f"each region needs 'page' and 'box'; got {region!r}")
            (index0,) = resolve_pages(vdoc, [region["page"]])
            box = tuple(float(v) for v in region["box"])
            if len(box) != 4:
                raise ValueError(f"box must be [x0, y0, x1, y1]; got {region['box']!r}")
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


def redact_text(
    path: str,
    query: str,
    out: str,
    *,
    match_case: bool = False,
    whole_words: bool = False,
    pages: list[int] | None = None,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Find every occurrence of ``query`` and destructively remove it, then verify.

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

    Raises rather than writing an untouched copy when nothing matches: a redaction tool reporting
    success over a file it did not change is how a secret ships.
    """
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    terms = [query] if whole_words else query.split()
    if not terms:
        raise ValueError("query is empty — nothing to redact")
    with open_document(path, password) as vdoc:
        scope = resolve_pages(vdoc, pages)
        per_page: dict[int, list[tuple]] = {}
        for index0 in scope:
            ref = vdoc.ordered[index0]
            page = vdoc.sources[ref.source_id][ref.source_page_index]
            found = [(r, term) for term in terms for r in page.search_for(term)]
            if not found:
                continue
            text = PageText(page)
            seen: set = set()
            for rect, term in found:
                box = (rect.x0, rect.y0, rect.x1, rect.y1)
                key = tuple(round(v, 2) for v in box)
                if key in seen:
                    continue
                seen.add(key)
                if whole_words and not text.is_whole_word(box):
                    continue
                if match_case and not text.matches_case(box, term):
                    continue
                per_page.setdefault(index0, []).append(box)
        if not per_page:
            raise ValueError(
                f"{query!r} was not found — nothing was redacted and no file was written "
                "(use `search` to check what matches before redacting)"
            )
        expectations = _apply(vdoc, per_page, path, password)
        hits = sum(len(boxes) for boxes in per_page.values())
        return _finish(
            vdoc,
            target,
            expectations,
            path,
            password,
            residual=lambda written: _no_residual_match(
                written,
                query,
                match_case=match_case,
                whole_words=whole_words,
                scope={index0 + 1 for index0 in scope},
                password=password,
            ),
            query=query,
            hits=hits,
            pages_redacted=sorted(expectations),
        )
