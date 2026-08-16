"""Read-only PDF queries for the MCP bridge — the milestone's only genuinely new logic.

Everything here is plain Python over ``model/``: no Qt, no MCP SDK, JSON-ready return values. That
split is deliberate. ``server.py`` becomes a thin adapter whose bugs are schema bugs, and the PDF
behaviour is testable by calling functions rather than by driving a protocol.

Two conventions hold across every function and are part of the tool contract:

* **Pages are 1-based** at this boundary, as they are in the viewer's page counter, in a PDF
  outline (``get_toc`` already returns 1-based targets), and in how anyone asking for "page 4"
  means it. ``model/`` is 0-based throughout; the conversion happens here and nowhere else.
* **Documents are opened through** :class:`~model.virtual_document.VirtualDocument`, not through a
  bare ``fitz.open``. It reads the file into memory rather than holding a handle (so nothing blocks
  a concurrent save), it decrypts an encrypted source once, and it is the same object the transform
  tools will mutate — so the read and write halves of the bridge cannot drift apart on how a
  document is opened.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pymupdf as fitz

from model.page_text import PageText
from model.virtual_document import PasswordRequired, VirtualDocument

# How many pages `document_info` samples before reporting `has_text_layer: false`. A text document
# answers on page 1; the scan of an 800-page scanned file is what this bound exists to stop.
_TEXT_LAYER_SAMPLE_PAGES = 20

# The advisory permission bits, named — a bitfield is not an answer an agent can act on. The model
# normalises "restricts nothing" to -1, which has every bit set, so an unrestricted document falls
# out as all-true here with no special case.
_PERMISSION_BITS = {
    "print": fitz.PDF_PERM_PRINT,
    "modify": fitz.PDF_PERM_MODIFY,
    "copy": fitz.PDF_PERM_COPY,
    "annotate": fitz.PDF_PERM_ANNOTATE,
    "fill_forms": fitz.PDF_PERM_FORM,
    "accessibility": fitz.PDF_PERM_ACCESSIBILITY,
    "assemble": fitz.PDF_PERM_ASSEMBLE,
    "print_high_quality": fitz.PDF_PERM_PRINT_HQ,
}


def _password_provider(password: str | None):
    """Adapt a plain password string to the model's ``(path, retry) -> str | None`` provider.

    Returns ``None`` when no password was given, which is what makes an encrypted document raise
    :class:`PasswordRequired` instead of hanging on a prompt that has no user behind it. On a wrong
    password the provider declines the retry rather than looping forever — an agent gets one answer,
    not an infinite authentication loop.
    """
    if password is None:
        return None
    return lambda _path, retry: None if retry else password


@contextmanager
def open_document(path: str | os.PathLike[str], password: str | None = None):
    """Open ``path`` as a :class:`VirtualDocument` and close it on the way out.

    Every tool goes through here so no code path can leak the in-memory source copies.
    """
    vdoc = VirtualDocument.from_path(os.fspath(path), password_provider=_password_provider(password))
    try:
        yield vdoc
    finally:
        vdoc.close()


def _page_of(vdoc: VirtualDocument, index0: int) -> fitz.Page:
    """The live source page behind output index ``index0`` (0-based)."""
    ref = vdoc.ordered[index0]
    return vdoc.sources[ref.source_id][ref.source_page_index]


def resolve_pages(vdoc: VirtualDocument, pages: list[int] | None) -> list[int]:
    """Turn a caller's 1-based page list into validated 0-based indices, in document order.

    ``None`` means the whole document. An out-of-range number is an error rather than a silent
    clamp: an agent that asked for page 900 of a 300-page file has a wrong belief, and quietly
    returning page 300 would confirm it.
    """
    count = vdoc.page_count
    if pages is None:
        return list(range(count))
    out: list[int] = []
    for page in pages:
        if not isinstance(page, int) or isinstance(page, bool):
            raise ValueError(f"page numbers must be integers, got {page!r}")
        if not 1 <= page <= count:
            raise ValueError(f"page {page} is out of range — the document has {count} pages")
        if page - 1 not in out:
            out.append(page - 1)
    return sorted(out)


# ---- query/route: the cheap calls an agent makes before committing to content ----


def document_info(path: str, password: str | None = None) -> dict:
    """Size, page count, encryption, text layer, outline — the routing call.

    Answers *before* any content is loaded, which is the point: it is how an agent decides whether
    to search, extract, or rasterise, instead of pulling an 800-page file into its context to find
    out. An encrypted document with no password reports what it is rather than failing, so the
    caller learns to supply one.

    **``encrypted`` is a fact about the file, not about this call.** It used to be
    ``password is not None`` — which answers "did the caller hand me a password?" — so an
    owner-password document, the kind that opens freely and still forbids copying, reported
    ``false`` from the one tool documented as the call that answers what changes everything else
    (TC-002 ISSUE 5). Two distinct protections have to come back true here: a *user* password,
    without which the file will not open, and an *owner* password, which restricts what may be
    done with it. ``needs_password`` is what separates them.
    """
    size = os.path.getsize(path)
    try:
        with open_document(path, password) as vdoc:
            first = _first_page_with_text(vdoc)
            info = vdoc.effective_metadata()
            encryption = vdoc.origin_encryption()
            return {
                "path": os.path.abspath(path),
                "pages": vdoc.page_count,
                "size_bytes": size,
                "encrypted": encryption is not None,
                "encryption": encryption,
                "needs_password": False,
                "permissions": _permissions(vdoc.permissions),
                "has_text_layer": first is not None,
                "first_page_with_text": first,
                "has_outline": vdoc.has_outline(),
                "title": info.get("title") or None,
                "author": info.get("author") or None,
                "page_sizes": _page_sizes(vdoc),
            }
    except PasswordRequired:
        return {
            "path": os.path.abspath(path),
            "size_bytes": size,
            "encrypted": True,
            "needs_password": True,
        }


def _permissions(flags: int) -> dict[str, bool]:
    """The document's advisory permissions, named. Advisory: honoured by most viewers, enforced by
    nothing but the password, so this reports what the document *asks* for."""
    return {name: bool(flags & bit) for name, bit in _PERMISSION_BITS.items()}


def _first_page_with_text(vdoc: VirtualDocument) -> int | None:
    """1-based number of the first sampled page carrying extractable text, else ``None``.

    Sampled, not exhaustive — see :data:`_TEXT_LAYER_SAMPLE_PAGES`. A ``None`` from a long document
    therefore means "no text in the first 20 pages", which is the honest claim and the one that
    matters: it is the difference between a searchable PDF and a scan that would need OCR.
    """
    for index0 in range(min(vdoc.page_count, _TEXT_LAYER_SAMPLE_PAGES)):
        if _page_of(vdoc, index0).get_text("text").strip():
            return index0 + 1
    return None


def _page_sizes(vdoc: VirtualDocument) -> list[dict]:
    """Distinct page geometries with the pages holding each — a mixed-size document is worth
    knowing about before a split, and listing all 800 pages of a uniform one is noise."""
    seen: dict[tuple, list[int]] = {}
    for index0 in range(vdoc.page_count):
        width, height = vdoc.page_visible_size(index0)
        seen.setdefault((round(width, 1), round(height, 1)), []).append(index0 + 1)
    return [
        {"width_pt": w, "height_pt": h, "pages": pages}
        for (w, h), pages in sorted(seen.items(), key=lambda kv: -len(kv[1]))
    ]


def outline(path: str, password: str | None = None) -> list[dict]:
    """The document outline (bookmarks) as ``{level, title, page}``, nesting preserved.

    Read through ``remapped_toc`` rather than raw ``get_toc`` so the transform tools get the correct
    outline for free once they start reordering pages — the remap is what keeps a bookmark pointing
    at its own page after a move, and dropping it here would mean two different answers to
    "what is the outline" depending on which tool asked.
    """
    with open_document(path, password) as vdoc:
        return [
            {"level": entry[0], "title": entry[1], "page": entry[2]}
            for entry in vdoc.remapped_toc()
        ]


def search(
    path: str,
    query: str,
    *,
    match_case: bool = False,
    whole_words: bool = False,
    password: str | None = None,
) -> list[dict]:
    """Locate ``query`` and return one hit per **occurrence**: page, snippet, and boxes.

    The same semantics as the app's find bar (M75.1), because they are the same primitives — MuPDF's
    ``search_for`` is always case-insensitive and always matches inside words, so both filters are
    applied afterwards against the text actually under each hit box:

    * ``whole_words`` off, the query is a **list of words**, any of which matches on its own, each
      still matching inside longer words. On, the query is **one phrase** and neither end may sit
      inside a longer word.
    * ``match_case`` compares the text under the box against the term that found it.

    A phrase that wraps a line break occupies a rectangle on **each** line — MuPDF returns the
    match that way, and every rectangle is real because ``redact_text`` has to clear all of them.
    They are grouped back into one hit rather than reported as several, so a count is a count of
    occurrences: ``boxes`` is normally one box, and two when the match wraps. The ``snippet`` joins
    the lines, so a wrapped match reads as the whole phrase.

    Per-hit text comes from :class:`~model.page_text.PageText`, which indexes a page once and serves
    every hit on it. This is the reuse the milestone was shrunk for: the naive ``get_textbox`` call
    re-extracts the whole page per hit (~31 ms), which took ~37 minutes on a one-letter query over a
    320-page file, and it answers by clipping so it returns the neighbouring line as often as not.

    Unlike the viewer, hits inside the app's own overlay text boxes and form widgets are **not**
    excluded — a freshly opened document has no pending overlays, and an agent searching a file
    wants what the file says.

    Each hit carries ``invisible`` (M95): the text is in the file but is not drawn on the page —
    white on white, transparent, or painted over. It is reported because a caller has no other way
    to find out. ``search`` looks identical for visible and invisible text, ``render_page`` shows
    nothing there, and a human comparing renders before and after a redaction sees a clean result
    either way — which is how TC-003's bill kept its account number through a redaction that
    everything reported as successful. See :meth:`~model.page_text.PageText.is_invisible` for what
    the flag can and cannot see.
    """
    terms = [query] if whole_words else query.split()
    hits: list[dict] = []
    if not terms:
        return hits
    with open_document(path, password) as vdoc:
        for index0 in range(vdoc.page_count):
            page = _page_of(vdoc, index0)
            per_term = [(term, [(r.x0, r.y0, r.x1, r.y1) for r in page.search_for(term)])
                        for term in terms]
            if not any(boxes for _term, boxes in per_term):
                continue
            text = PageText(page)
            found = [(boxes, term) for term, term_boxes in per_term
                     for boxes in text.group_matches(term_boxes, term)]
            if len(terms) > 1:  # one term already arrives in reading order
                found.sort(key=lambda f: (round(f[0][0][1], 1), f[0][0][0]))
            seen: set = set()
            for boxes, term in found:
                key = tuple(tuple(round(v, 2) for v in box) for box in boxes)
                if key in seen:
                    continue  # two terms landing on the same text is still one hit
                seen.add(key)
                if whole_words and not all(text.is_whole_word(box) for box in boxes):
                    continue
                if match_case and not all(text.matches_case(box, term) for box in boxes):
                    continue
                hits.append(
                    {
                        "page": index0 + 1,
                        "snippet": text.snippet_for(boxes),
                        "boxes": [[round(v, 2) for v in box] for box in boxes],
                        "invisible": any(text.is_invisible(box) for box in boxes),
                    }
                )
    return hits


# ---- query: content ----------------------------------------------------------


def extract_text(path: str, pages: list[int] | None = None, password: str | None = None) -> dict:
    """Text of ``pages`` (1-based; ``None`` = all), one entry per page, in document order."""
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        return {
            "page_count": vdoc.page_count,
            "pages": [
                {"page": i + 1, "text": _page_of(vdoc, i).get_text("text")} for i in indices
            ],
        }


def render_page(path: str, page: int, dpi: int = 150, password: str | None = None) -> dict:
    """Rasterise one page to PNG bytes — for anything the text layer cannot answer.

    Rendered from :meth:`PyMuPDFEngine.render_output`, the same in-memory build a Save would write,
    so the image shows the document as it *would be* produced rather than as it was stored: page
    order, rotation and any pending edits are already applied. On a freshly opened file those are
    identity, but the transform tools share this path and must not need a second one.
    """
    from model.edit_engine import PyMuPDFEngine

    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi}")
    with open_document(path, password) as vdoc:
        (index0,) = resolve_pages(vdoc, [page])
        out = PyMuPDFEngine().render_output(vdoc)
        try:
            pixmap = out[index0].get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
            return {
                "page": page,
                "dpi": dpi,
                "width_px": pixmap.width,
                "height_px": pixmap.height,
                "png": pixmap.tobytes("png"),
            }
        finally:
            out.close()


def form_fields(path: str, password: str | None = None) -> list[dict]:
    """Every fillable widget, in page order — the input ``fill_form`` (M40) takes.

    One entry per *occurrence*: a field appearing on three pages is three entries sharing a ``name``
    and therefore a value, which is what a caller has to know before filling it.

    Beyond locating each field this reports what it takes to **fill** it, which is a different
    question and was unanswerable before M94 (TC-002 ISSUE 6):

    * ``on_state`` / ``states`` — a checkbox's ticked value is per-widget, not a convention. The
      SSA-3 uses ``"1"`` on one box and ``"2"`` on another, and ``choices`` cannot carry it because
      PyMuPDF populates ``choice_values`` for combo/list only. A caller with neither has to guess
      ``"Yes"``. (``fill_form`` also takes a plain ``true``, which is the easy path — but a tool
      that only works if you know an undocumented convenience is a tool that does not work.)
    * ``read_only`` / ``required`` / ``multiline`` / ``max_len`` — the SSA-3 carries three 3-pt
      plumbing fields (``P2_PAReadOnly_FLD`` and friends) that were indistinguishable from the
      fields a person is meant to fill.
    """
    from model.page_edits import read_form_fields

    with open_document(path, password) as vdoc:
        return [
            {
                "name": field.name,
                "type": field.type_string,
                "page": field.page_index + 1,
                "rect": [round(v, 2) for v in field.rect],
                "choices": list(field.choices) if field.choices else None,
                "value": field.current_value,
                "on_state": field.on_state,
                "states": list(field.states) or None,
                "read_only": field.read_only,
                "required": field.required,
                "multiline": field.multiline,
                "max_len": field.max_len,
            }
            for field in read_form_fields(vdoc)
        ]
