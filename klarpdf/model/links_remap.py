"""Internal link remap for materialize-on-save (PLAN.md, M33).

``insert_pdf`` doesn't carry internal links through our edit reliably: a **GoTo** link is dropped
when its target page isn't inside the contiguous run being copied (and our reorder/delete materialize
copies pages in many small runs), and a **named-destination** link is dropped outright (the
``/Dests`` name tree isn't reconstructed). So, exactly like the outline (:mod:`model.toc_remap`), we
**rebuild** them: read each source page's internal links, point every survivor at its target's
**new** output index, and drop links whose target page was deleted.

Both kinds resolve (via PyMuPDF ``get_links``) to a target **page**, so they are remapped the same
way and re-emitted as direct GoTo links — a named destination is *baked* to the page it currently
points at (the navigation is preserved; the destination *name* is not, which doesn't matter for an
internal jump). An internal link stays within its own source document, so the remap is keyed by
``(source_id, source_page_index) -> output index``.

**URI links** carry no page target and normally ride ``insert_pdf`` unchanged — but PyMuPDF's link
copy re-serialises the URI text without PDF-string escaping, so a URI containing an unbalanced
paren (seen in the wild: novaPDF writing ``http://www.adobe.com)``) is **silently dropped** with a
console "skipping bad link / annot item N". The same flaw sits in ``insert_link``, so the restore
pass here re-adds any dropped URI link with the text pre-escaped — which round-trips correctly
(the escape is undone by PDF string decoding, so readers see the original URI). Launch / GoToR /
other external kinds stay ``insert_pdf``'s as before (no observed loss).

Model-layer (uses PyMuPDF, no GUI) and headless-testable.
"""

from __future__ import annotations

import pymupdf as fitz

# Link kinds that name a page inside the document (so they follow the page through an edit).
_INTERNAL_KINDS = (fitz.LINK_GOTO, fitz.LINK_NAMED)


def internal_link_target(link: dict) -> int | None:
    """The 0-based **source** page a GoTo / named-destination link points at, or ``None`` if the
    link isn't an internal one or its destination doesn't resolve to a page."""
    if link.get("kind") not in _INTERNAL_KINDS:
        return None
    page = link.get("page")
    return page if isinstance(page, int) and page >= 0 else None


def link_target_map(ordered) -> dict[tuple[str, int], int]:
    """Map ``(source_id, source_page_index)`` -> its **first** output index, over ``ordered``.

    A page absent from the map was deleted (links pointing at it are dropped); a duplicated page
    maps to its first occurrence, since a GoTo link target is single-valued.
    """
    target_map: dict[tuple[str, int], int] = {}
    for out_index, ref in enumerate(ordered):
        target_map.setdefault((ref.source_id, ref.source_page_index), out_index)
    return target_map


def _pdf_string_escape(text: str) -> str:
    """Escape ``text`` for a PDF literal string ``(...)`` — backslash first, then the parens.
    PDF string decoding undoes it, so a reader sees the original text."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _uri_key(link: dict) -> tuple:
    """Identity of a URI link for the dropped-link check: its text + its (rounded) rect —
    rounding absorbs the float noise a copy introduces."""
    r = link["from"]
    return (link.get("uri"), round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2))


def remap_internal_links(out_doc: fitz.Document, vdoc) -> None:
    """Rebuild internal links on the materialised ``out_doc`` (output page ``i`` == ``ordered[i]``)
    and restore any URI link ``insert_pdf`` dropped.

    For each output page we strip the internal links ``insert_pdf`` left (a GoTo kept within a run,
    or — rarely — a named one), then re-add every internal source link (GoTo **or**
    named-destination) with its target remapped, emitted as a direct GoTo. So the result is correct
    and duplicate-free whatever the page order, a link to a deleted page is dropped, and named
    destinations (which ``insert_pdf`` drops entirely) survive as working page jumps.

    URI links are then compared source-vs-output: one missing from the output was dropped by
    ``insert_pdf``'s unescaped re-serialisation (see the module docstring) and is re-added with
    the text pre-escaped. A URI link that copied fine is left exactly as ``insert_pdf`` wrote it,
    so a well-formed document's output is unchanged.
    """
    target_map = link_target_map(vdoc.ordered)
    for out_index, ref in enumerate(vdoc.ordered):
        out_page = out_doc[out_index]
        out_links = out_page.get_links()  # read once, before the deletes below
        for link in out_links:
            if link.get("kind") in _INTERNAL_KINDS:
                out_page.delete_link(link)
        source_page = vdoc.sources[ref.source_id][ref.source_page_index]
        source_links = source_page.get_links()
        for link in source_links:
            target_src = internal_link_target(link)
            if target_src is None:
                continue
            new_index = target_map.get((ref.source_id, target_src))
            if new_index is None:
                continue  # target page was deleted — drop the link (no dangling)
            out_page.insert_link(
                {
                    "kind": fitz.LINK_GOTO,  # a named destination is baked to a direct page link
                    "from": link["from"],
                    "page": new_index,
                    "to": link.get("to", fitz.Point(0, 0)),
                }
            )
        copied_uris = {_uri_key(l) for l in out_links if l.get("kind") == fitz.LINK_URI}
        for link in source_links:
            if link.get("kind") != fitz.LINK_URI or not link.get("uri"):
                continue
            if _uri_key(link) in copied_uris:
                continue  # insert_pdf carried it — leave its bytes untouched
            out_page.insert_link(
                {
                    "kind": fitz.LINK_URI,
                    "from": link["from"],
                    "uri": _pdf_string_escape(link["uri"]),  # decoded back to the original by readers
                }
            )
