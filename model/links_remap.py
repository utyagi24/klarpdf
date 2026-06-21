"""Internal GoTo-link remap for materialize-on-save (PLAN.md, M33).

``insert_pdf`` drops an internal GoTo link whose target page isn't inside the contiguous run being
copied — and our reorder/delete materialize copies pages in many small runs, so internal links are
lost (or, within a single run, can survive pointing at a stale target). So, exactly like the outline
(:mod:`model.toc_remap`), we **rebuild** them: read each source page's GoTo links, point every
surviving one at its target's **new** output index, and drop links whose target page was deleted.

An internal GoTo link stays within its own source document, so the remap is keyed by
``(source_id, source_page_index) -> output index``. URI / launch / other non-GoTo links carry no
page target, so ``insert_pdf`` copies them fine and we leave them untouched.

Model-layer (uses PyMuPDF, no GUI) and headless-testable.
"""

from __future__ import annotations

import pymupdf as fitz


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
    """Rebuild internal GoTo links on the materialised ``out_doc`` (output page ``i`` == ``ordered[i]``).

    For each output page we strip the GoTo links ``insert_pdf`` left (correct or stale) and re-add
    them from the source with the target remapped — so the result is correct and duplicate-free
    whatever the page order, and a link to a deleted page is dropped. Non-GoTo links (URIs, …) are
    left as ``insert_pdf`` copied them. A page with no GoTo links on either side is untouched, so a
    link-free document's output is unchanged.
    """
    target_map = link_target_map(vdoc.ordered)
    for out_index, ref in enumerate(vdoc.ordered):
        source_page = vdoc.sources[ref.source_id][ref.source_page_index]
        source_goto = [link for link in source_page.get_links() if link.get("kind") == fitz.LINK_GOTO]
        out_page = out_doc[out_index]
        for link in out_page.get_links():
            if link.get("kind") == fitz.LINK_GOTO:
                out_page.delete_link(link)
        for link in source_goto:
            new_index = target_map.get((ref.source_id, link["page"]))
            if new_index is None:
                continue  # target page was deleted — drop the link (no dangling)
            out_page.insert_link(
                {
                    "kind": fitz.LINK_GOTO,
                    "from": link["from"],
                    "page": new_index,
                    "to": link.get("to", fitz.Point(0, 0)),
                }
            )
