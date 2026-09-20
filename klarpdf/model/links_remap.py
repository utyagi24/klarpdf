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

import re

import pymupdf as fitz

# Link kinds that name a page inside the document (so they follow the page through an edit).
_INTERNAL_KINDS = (fitz.LINK_GOTO, fitz.LINK_NAMED)


def internal_link_target(link: dict) -> int | None:
    """The 0-based **source** page a GoTo / named-destination link points at, or ``None`` if the
    link isn't an internal one or its destination doesn't resolve to a page.

    **PyMuPDF spells that page two ways, and they are numbered differently** (M138.2). Both come
    out of ``get_links()`` on ordinary documents, so both have to be read:

    * an **int**, and **0-based** — the destination resolved through the document's name tree
      (``Document.resolve_names()``). Measured on a real magazine: ``page=5`` is the sixth page,
      the one whose printed folio reads ``06``.
    * a **str of digits**, and **1-based** — a destination MuPDF turned into its own
      ``#page=N&view=Fit`` URI which PyMuPDF then failed to pattern-match. Its ``getLinkDict``
      only converts ``#page=N`` and ``#page=N&zoom=…`` into a 0-based int; anything else
      (``&view=Fit`` is the common one) falls through to a generic URI-to-dict split that leaves
      every value a **string, still 1-based**, and relabels the link ``LINK_NAMED``. Measured on a
      128-page annual report: ``page='4'`` is the fourth page, whose printed folio reads ``2``.

    Reading the string as 0-based, or the int as 1-based, is an **off-by-one that looks entirely
    plausible** — it lands on a real neighbouring page — which is why the two spellings are named
    here rather than normalised at a call site.

    The `isinstance` guard is load-bearing beyond correctness: `'4' >= 0` is a ``TypeError``, and
    the bridge's own copy of this logic raised exactly that on the first document to carry the
    string spelling, failing a whole-document call over 18 of its 119 links (TC-019).
    """
    if link.get("kind") not in _INTERNAL_KINDS:
        return None
    page = link.get("page")
    if isinstance(page, bool):          # bool is an int subclass; a True target is not a page 1
        return None
    if isinstance(page, int):
        return page if page >= 0 else None
    if isinstance(page, str) and page.isascii() and page.isdigit():
        index = int(page) - 1           # this spelling counts from 1
        return index if index >= 0 else None
    return None


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


def _annot_xrefs(doc: fitz.Document, page_xref: int) -> list[int]:
    """The page's ``/Annots`` entries, in file order — which ``insert_link`` appends to at once,
    where the ``Page`` object it was called on does not notice its own new annotation."""
    annots = doc.xref_get_key(page_xref, "Annots")
    if annots[0] != "array":
        return []
    return [int(x) for x in re.findall(r"(\d+)\s+\d+\s+R", annots[1])]


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
    from klarpdf.model.destinations import page_index_map, read_destination, write_destination

    target_map = link_target_map(vdoc.ordered)
    source_dest_maps: dict[str, dict] = {}
    source_name_memos: dict[str, dict] = {}   # per source: resolved destination names
    for out_index, ref in enumerate(vdoc.ordered):
        out_page = out_doc[out_index]
        out_links = out_page.get_links()  # read once, before the deletes below
        for link in out_links:
            if link.get("kind") in _INTERNAL_KINDS:
                out_page.delete_link(link)
        source = vdoc.sources[ref.source_id]
        source_page = source[ref.source_page_index]
        source_links = source_page.get_links()
        if ref.source_id not in source_dest_maps:
            source_dest_maps[ref.source_id] = page_index_map(source)
            source_name_memos[ref.source_id] = {}
        page_of_xref = source_dest_maps[ref.source_id]
        names = source_name_memos[ref.source_id]
        # The tails are collected while inserting and written afterwards (M150), because
        # `insert_link` gives no way to say "leave this destination alone": it re-derives the point
        # from `to`, in a space that does not match the one `get_links` reported it in, and so
        # shifts every point on a rotated or offset-cropped page. What it *is* still needed for is
        # building the annotation — its rect, its border, its place in `/Annots`.
        seen_annots = set(_annot_xrefs(out_doc, out_page.xref))
        for link in source_links:
            # Only an internal link has a destination to carry; a URI or Launch link would cost
            # two object reads per link to be told so.
            dest = (
                read_destination(source, link["xref"], page_of_xref, names)
                if link.get("kind") in _INTERNAL_KINDS and link.get("xref") else None
            )
            target_src = internal_link_target(link)
            if target_src is None:
                # PyMuPDF could not resolve the destination to a page, and until M150 that meant
                # the link was dropped. Reading the file directly can still get there: measured on
                # the corpus, one Nature paper spells all **81** of its internal destinations as
                # UTF-16 hex-string names, and every one of them vanished on every page move —
                # silently, since the pages and the outline came through fine. The library stays
                # the authority wherever it has an answer (it resolves 7 the reader cannot); this
                # only fills in where it has none.
                if dest is None:
                    continue
                target_src = dest.page
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
            # A link whose destination names a page other than the one that was resolved, or a
            # page that was deleted, keeps the plain page jump above rather than a tail that would
            # disagree with it.
            keep = dest is not None and target_map.get((ref.source_id, dest.page)) == new_index
            if not keep:
                continue

            # **The annotation this call just made is found through `/Annots`, not through the
            # page.** Measured on 1.27.2.3, `get_links()` on the `Page` object that took the
            # `insert_link` still reports zero — the annotation is in the file but not in the
            # object, and asking `out_doc[i]` again hands back the same cached page. Reloading the
            # page does show it, and **breaks**: on one corpus document it raised PyMuPDF's own
            # ``AssertionError: refs_old=3`` from inside the reload, because live references to
            # that page still existed. The `/Annots` array is written immediately, needs no page
            # object, and names the new annotation last.
            fresh = _annot_xrefs(out_doc, out_page.xref)
            if not fresh or fresh[-1] in seen_annots:
                continue  # not where we expected it — leave the plain page jump alone
            seen_annots.add(fresh[-1])
            write_destination(out_doc, fresh[-1], out_doc.page_xref(new_index), dest.tail)
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
