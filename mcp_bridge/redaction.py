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
4. **Never report success on an unverified file.** If verification fails the output is **deleted**
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
from mcp_bridge.queries import open_document, resolve_pages
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
    vdoc, target: str, expectations: dict, source: str, password: str | None, **extra
) -> dict:
    """Write, verify, and delete the output if verification fails."""
    _write(vdoc, target)
    try:
        report = _verify(target, expectations, password)
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
                if match_case and text.text_under(box).strip() != term:
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
            query=query,
            hits=hits,
            pages_redacted=sorted(expectations),
        )
