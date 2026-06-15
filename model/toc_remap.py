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
        if dest is not None:
            dest = dict(dest)
            if "page" in dest:
                dest["page"] = new0  # dest carries a 0-based page
            kept.append([level, title, new0 + 1, dest])
        else:
            kept.append([level, title, new0 + 1])
        orig_levels.append(level)

    for entry, fixed in zip(kept, repair_levels(orig_levels)):
        entry[0] = fixed
    return kept
