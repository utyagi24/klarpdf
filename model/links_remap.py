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
``(source_id, source_page_index) -> output index``. URI / launch / other links carry no page target,
so ``insert_pdf`` copies them fine and we leave them untouched.

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


def remap_internal_links(out_doc: fitz.Document, vdoc) -> None:
    """Rebuild internal links on the materialised ``out_doc`` (output page ``i`` == ``ordered[i]``).

    For each output page we strip the internal links ``insert_pdf`` left (a GoTo kept within a run,
    or — rarely — a named one), then re-add every internal source link (GoTo **or**
    named-destination) with its target remapped, emitted as a direct GoTo. So the result is correct
    and duplicate-free whatever the page order, a link to a deleted page is dropped, and named
    destinations (which ``insert_pdf`` drops entirely) survive as working page jumps. Non-internal
    links (URIs, …) are left as ``insert_pdf`` copied them, and a page with no internal links on
    either side is untouched — so a link-free document's output is unchanged.
    """
    target_map = link_target_map(vdoc.ordered)
    for out_index, ref in enumerate(vdoc.ordered):
        out_page = out_doc[out_index]
        for link in out_page.get_links():
            if link.get("kind") in _INTERNAL_KINDS:
                out_page.delete_link(link)
        source_page = vdoc.sources[ref.source_id][ref.source_page_index]
        for link in source_page.get_links():
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
