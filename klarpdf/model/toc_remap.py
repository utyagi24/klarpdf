"""Outline (table-of-contents) remap for materialize-on-save.

``insert_pdf`` does **not** copy the source outline (PLAN.md, Open items), so materialize
rebuilds it explicitly: take the origin document's outline, point every surviving entry at its
**new** page index, and drop entries whose target page was deleted. Dropping an interior entry
can orphan its children and leave a level jump that ``Document.set_toc`` rejects, so we also
**repair the level sequence** (start at 1, never jump by more than +1) while preserving the
relative nesting of the entries that remain.

Pure functions over plain lists — no PyMuPDF dependency, so this is trivially unit-testable.
"""

from __future__ import annotations

TocEntry = list  # [level:int, title:str, page:int(1-based), dest:dict|None]

# ``fitz.LINK_GOTO``, written as its value so this module keeps the no-PyMuPDF property its
# docstring claims. A test pins it against the library, so a constant that ever stopped matching
# fails there rather than silently writing the wrong destination kind.
_LINK_GOTO = 1


def bake_dest(dest: dict | None, new0: int) -> dict | None:
    """``dest`` re-pointed at output page ``new0`` (0-based), in a form ``set_toc`` can write.

    **A destination that is not already a direct GoTo cannot survive a page move** (M138.4). The
    outline of a document produced by InDesign and friends is usually a stack of *named*
    destinations — ``{'kind': 4, 'xref': 6614, 'page': '4', 'view': 'Fit'}`` — where the page is a
    lookup PyMuPDF resolved for reading, and ``xref`` points into the **source** document. Handing
    that dict back to ``set_toc`` on the materialised output makes it chase an xref that is not
    there (``cannot find object in xref``), and the bookmark is written with **no destination at
    all**: it still appears in the outline and navigates nowhere.

    That is worse than being dropped, and it is why it went unseen — counting outline entries finds
    39 of 39 and looks correct. Measured on a 128-page annual report: **37 of its 39** bookmarks
    broke this way on every page move, in the app's Save as much as in the bridge's `merge`,
    `reorder`, `delete_pages` and `extract_pages`. The survivors were the two whose destinations
    happened to be direct.

    So a foreign destination is **baked to a direct GoTo at the remapped page**, exactly as
    :func:`~model.links_remap.remap_internal_links` bakes a named *link* — the navigation is
    preserved, the destination *name* is not, and for an internal jump that is all that matters.
    A destination that is already a GoTo keeps its own ``to`` point, so an outline that aimed at a
    precise spot on the page still does.
    """
    if dest is None:
        return None
    if dest.get("kind") == _LINK_GOTO:
        # Already direct: only the page moves. `xref` is the source document's and means nothing
        # in the output, so it goes rather than being carried along looking meaningful.
        kept = {k: v for k, v in dest.items() if k != "xref"}
        kept["page"] = new0
        return kept
    # `view: Fit` and its relatives carry no coordinates to preserve, so the baked destination is
    # the top of the target page — which is what "fit this page" resolves to for a reader anyway.
    return {"kind": _LINK_GOTO, "page": new0, "zoom": 0.0}


def repair_levels(levels: list[int]) -> list[int]:
    """Normalise a list of original outline levels to a ``set_toc``-valid sequence.

    The first level becomes 1 and no level exceeds the previous by more than 1, while the
    ancestor/descendant relationships among the surviving entries are preserved. Orphaned
    children (whose parent was dropped) are promoted toward the root.
    """
    out: list[int] = []
    stack: list[tuple[int, int]] = []  # (original level, assigned level)
    for orig in levels:
        while stack and stack[-1][0] >= orig:
            stack.pop()
        assigned = stack[-1][1] + 1 if stack else 1
        stack.append((orig, assigned))
        out.append(assigned)
    return out


def remap_toc(toc: list[TocEntry], index_map: dict[int, int]) -> list[TocEntry]:
    """Remap an outline to new page indices, dropping dangling entries.

    ``toc`` is the output of ``Document.get_toc(simple=False)``: each entry is
    ``[level, title, page, dest]`` with ``page`` **1-based**. ``index_map`` maps an origin
    document's **0-based** page index to its **0-based** index in the materialised output;
    pages absent from the map were deleted. Returns a new outline ready for
    ``Document.set_toc``.
    """
    kept: list[TocEntry] = []
    orig_levels: list[int] = []
    for entry in toc:
        level, title, page = entry[0], entry[1], entry[2]
        dest = entry[3] if len(entry) > 3 else None
        old0 = page - 1
        new0 = index_map.get(old0)
        if new0 is None:
            continue  # target page was deleted — drop this bookmark (no dangling/-1)
        baked = bake_dest(dest, new0)          # dest carries a 0-based page
        if baked is not None:
            kept.append([level, title, new0 + 1, baked])
        else:
            kept.append([level, title, new0 + 1])
        orig_levels.append(level)

    for entry, fixed in zip(kept, repair_levels(orig_levels)):
        entry[0] = fixed
    return kept
