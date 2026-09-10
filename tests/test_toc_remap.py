"""Pure-function tests for outline remap + level repair (no PyMuPDF needed)."""

from __future__ import annotations

from klarpdf.model.toc_remap import remap_toc, repair_levels


def test_repair_levels_starts_at_one_and_no_jumps():
    assert repair_levels([1, 2, 1]) == [1, 2, 1]
    assert repair_levels([2, 3]) == [1, 2]  # never starts above 1
    assert repair_levels([1, 3]) == [1, 2]  # a +2 jump is clamped to +1


def test_repair_levels_promotes_orphaned_child():
    # Parent (level 1) dropped, its child (level 2) and a following level-1 survive.
    assert repair_levels([2, 1]) == [1, 1]


def test_remap_drops_dangling_and_renumbers():
    toc = [[1, "Chapter 1", 1], [2, "Section 1.1", 2], [1, "Chapter 2", 3]]
    # A1 (page 2 -> old0=1) deleted; A0->0, A2->1.
    index_map = {0: 0, 2: 1}
    assert remap_toc(toc, index_map) == [[1, "Chapter 1", 1], [1, "Chapter 2", 2]]


def test_remap_updates_explicit_destination_page():
    toc = [[1, "Chapter 1", 3, {"kind": 1, "page": 2, "to": (0, 700)}]]
    index_map = {2: 0}  # origin page index 2 lands at output index 0
    out = remap_toc(toc, index_map)
    assert out[0][0] == 1
    assert out[0][2] == 1  # 1-based page
    assert out[0][3]["page"] == 0  # dest carries the new 0-based page
    assert out[0][3]["to"] == (0, 700)  # other dest keys untouched


# ---- destinations a page move cannot carry (M138.4 / TC-022) -------------------
#
# A bookmark whose destination is *named* rather than direct cannot be handed back to `set_toc` on
# a different document: its `xref` points into the source, so the written bookmark ends up with no
# destination at all — present in the outline, navigating nowhere. Counting entries finds 39 of 39
# and looks correct, which is why this shipped; the count that matters is how many still point
# somewhere.

import pymupdf as fitz
import pytest

from klarpdf.model.toc_remap import _LINK_GOTO, bake_dest


def test_the_goto_constant_matches_the_library():
    """`toc_remap` is deliberately PyMuPDF-free, so the kind is written as its value. This is the
    seam that keeps that honest — if the library ever renumbers, this fails here rather than
    silently writing the wrong destination kind into every outline we produce."""
    assert _LINK_GOTO == fitz.LINK_GOTO


def test_a_named_destination_is_baked_to_a_direct_goto():
    """The fix. `set_toc` writes a bookmark with no destination when handed this dict, because the
    `xref` belongs to the document it was read from."""
    dest = {"kind": fitz.LINK_NAMED, "xref": 6614, "page": "4", "view": "Fit", "zoom": 0.0}
    baked = bake_dest(dest, 7)
    assert baked == {"kind": fitz.LINK_GOTO, "page": 7, "zoom": 0.0}


def test_a_direct_destination_keeps_its_own_scroll_position():
    """The narrowness. A GoTo destination already works, and its `to` is the exact spot on the page
    the publisher aimed at — rewriting it to the page top would be a silent fidelity loss on the
    entries that were never broken."""
    point = fitz.Point(-276.976, 0.023986817)
    dest = {"kind": fitz.LINK_GOTO, "xref": 6652, "page": 16, "to": point, "zoom": 0.0}
    baked = bake_dest(dest, 3)
    assert baked["kind"] == fitz.LINK_GOTO
    assert baked["page"] == 3
    assert baked["to"] == point


def test_the_source_xref_is_never_carried_into_the_output():
    """It names an object in another document. Leaving it is what made the dict look reusable."""
    for dest in ({"kind": fitz.LINK_GOTO, "xref": 99, "page": 1},
                 {"kind": fitz.LINK_NAMED, "xref": 99, "page": "1", "view": "Fit"}):
        assert "xref" not in bake_dest(dest, 0)


def test_an_entry_with_no_dest_stays_without_one():
    assert bake_dest(None, 4) is None


@pytest.fixture
def fit_outline_pdf(tmp_path) -> str:
    """6 pages with a 3-entry outline whose items use `/Dest [<page> 0 R /Fit]`.

    The shape InDesign and most publishing pipelines emit, and the one no fixture had: `set_toc`
    writes direct GoTo destinations, so an outline built through the API can never reach this path.
    Hand-built raw objects are the only way to get there — which is exactly why 37 of a real annual
    report's 39 bookmarks could break with the suite green.
    """
    path = str(tmp_path / "fitoutline.pdf")
    doc = fitz.open()
    for i in range(6):
        doc.new_page().insert_text((72, 72), f"PAGE {i + 1}", fontsize=20)
    # `target` is a 0-based page index, so "Chapter One" lands on 1-based page 3.
    items = [(doc.get_new_xref(), title, target)
             for title, target in (("Chapter One", 2), ("Chapter Two", 4), ("Chapter Three", 5))]
    root = doc.get_new_xref()
    for i, (xref, title, target) in enumerate(items):
        nxt = f"/Next {items[i + 1][0]} 0 R" if i + 1 < len(items) else ""
        prv = f"/Prev {items[i - 1][0]} 0 R" if i else ""
        doc.update_object(xref, f"<< /Title ({title}) /Parent {root} 0 R {prv} {nxt} "
                                f"/Dest [ {doc.page_xref(target)} 0 R /Fit ] >>")
    doc.update_object(root, f"<< /Type /Outlines /First {items[0][0]} 0 R "
                            f"/Last {items[-1][0]} 0 R /Count {len(items)} >>")
    doc.xref_set_key(doc.pdf_catalog(), "Outlines", f"{root} 0 R")
    doc.save(path)
    doc.close()
    return path


def test_the_fixture_really_carries_named_destinations(fit_outline_pdf):
    """The control. If PyMuPDF ever reports these as direct GoTo the tests below stop exercising
    the fix, and this says so rather than letting them pass for a new reason."""
    doc = fitz.open(fit_outline_pdf)
    toc = doc.get_toc(simple=False)
    doc.close()
    assert [e[3]["kind"] for e in toc] == [fitz.LINK_NAMED] * 3
    assert [e[3]["page"] for e in toc] == ["3", "5", "6"]     # strings, not ints


@pytest.mark.parametrize("mutate,expected", [
    (lambda v: v.move_pages([0], 6), {"Chapter One": 2, "Chapter Two": 4, "Chapter Three": 5}),
    (lambda v: v.delete_pages([0]),  {"Chapter One": 2, "Chapter Two": 4, "Chapter Three": 5}),
])
def test_named_outline_destinations_survive_a_page_move(fit_outline_pdf, mutate, expected):
    """The bug as a user meets it: reorder or delete a page, save, and the bookmarks are still
    listed but navigate nowhere (`page -1`). Both the app's Save and the bridge's page-moving
    tools go through this path.

    Targets are asserted, not just counted — the whole reason this was invisible is that the count
    stayed right while every destination went dead.
    """
    from klarpdf.model.edit_engine import PyMuPDFEngine
    from klarpdf.model.virtual_document import VirtualDocument

    vdoc = VirtualDocument.from_path(fit_outline_pdf)
    mutate(vdoc)
    out = PyMuPDFEngine().render_output(vdoc)
    try:
        toc = out.get_toc()
        assert not [e for e in toc if e[2] < 1], "bookmarks written with no destination"
        assert {title: page for _lvl, title, page in toc} == expected
    finally:
        out.close()
        vdoc.close()


def test_a_bookmark_whose_page_is_deleted_is_still_dropped(fit_outline_pdf):
    """The pre-existing behaviour the fix must not loosen: a target that is gone leaves no
    bookmark, rather than one pointing at an arbitrary survivor."""
    from klarpdf.model.edit_engine import PyMuPDFEngine
    from klarpdf.model.virtual_document import VirtualDocument

    vdoc = VirtualDocument.from_path(fit_outline_pdf)
    # 0-based: index 2 is 1-based page 3, which is "Chapter One"'s target (the fixture builds its
    # destinations from `page_xref(2)`, so the printed page number is one higher than the index).
    vdoc.delete_pages([2])
    out = PyMuPDFEngine().render_output(vdoc)
    try:
        titles = [t for _l, t, _p in out.get_toc()]
        assert "Chapter One" not in titles
        assert titles == ["Chapter Two", "Chapter Three"]
    finally:
        out.close()
        vdoc.close()


# ---- outlines from every source, not just the origin (M138.5 / TC-023) ---------


@pytest.fixture
def two_outlined_pdfs(tmp_path):
    """Two documents that each carry their own outline, at different depths.

    The shape TC-022 could not test — its second document had no outline to lose, so `merge`
    keeping only the first one looked like correct behaviour for four rounds.
    """
    def build(name, pages, entries):
        path = str(tmp_path / name)
        doc = fitz.open()
        for i in range(pages):
            doc.new_page().insert_text((72, 72), f"{name} {i + 1}", fontsize=18)
        doc.set_toc(entries)
        doc.save(path)
        doc.close()
        return path

    first = build("first.pdf", 4, [[1, "F-One", 1], [2, "F-One-a", 2], [1, "F-Two", 3]])
    second = build("second.pdf", 3, [[1, "S-One", 1], [1, "S-Two", 2], [2, "S-Two-a", 3]])
    return first, second


def test_a_second_source_contributes_its_outline_with_its_own_offset(two_outlined_pdfs):
    """`remapped_toc` used to remap the origin's outline alone, on the documented assumption that
    other sources "carry no outline". True while the only way to gain one was the app splicing a
    page or two in; false the moment `merge` made documents 2..n first-class."""
    from klarpdf.model.virtual_document import PageRef, VirtualDocument

    first, second = two_outlined_pdfs
    vdoc = VirtualDocument.from_path(first)
    try:
        sid = vdoc.open_source(second)
        vdoc.append_pages([PageRef(sid, i) for i in range(vdoc.sources[sid].page_count)])
        got = [(lvl, title, page) for lvl, title, page, *_ in vdoc.remapped_toc()]
    finally:
        vdoc.close()

    assert got == [
        (1, "F-One", 1), (2, "F-One-a", 2), (1, "F-Two", 3),      # origin, unmoved
        (1, "S-One", 5), (1, "S-Two", 6), (2, "S-Two-a", 7),      # appended, +4
    ]


def test_each_source_keeps_its_own_nesting(two_outlined_pdfs):
    """Two trees that each start at level 1 concatenate into something `set_toc` accepts, and the
    child entries stay children rather than being flattened or re-parented."""
    from klarpdf.model.virtual_document import PageRef, VirtualDocument

    first, second = two_outlined_pdfs
    vdoc = VirtualDocument.from_path(first)
    try:
        sid = vdoc.open_source(second)
        vdoc.append_pages([PageRef(sid, i) for i in range(vdoc.sources[sid].page_count)])
        levels = [entry[0] for entry in vdoc.remapped_toc()]
    finally:
        vdoc.close()
    assert levels == [1, 2, 1, 1, 1, 2]


def test_bookmarks_read_in_the_order_a_reader_meets_them(two_outlined_pdfs):
    """The appended document's pages come first here, so its bookmarks must too — the order is the
    output's, not the order the sources happened to be opened in."""
    from klarpdf.model.virtual_document import PageRef, VirtualDocument

    first, second = two_outlined_pdfs
    vdoc = VirtualDocument.from_path(first)
    try:
        sid = vdoc.open_source(second)
        count = vdoc.sources[sid].page_count
        first_appended = vdoc.page_count                  # 4 origin pages, so the append starts at 4
        vdoc.append_pages([PageRef(sid, i) for i in range(count)])
        vdoc.move_pages(list(range(first_appended, first_appended + count)), 0)
        titles = [entry[1] for entry in vdoc.remapped_toc()]
    finally:
        vdoc.close()
    assert titles == ["S-One", "S-Two", "S-Two-a", "F-One", "F-One-a", "F-Two"]


def test_a_second_source_bookmark_whose_page_is_absent_is_still_dropped(two_outlined_pdfs):
    """The rule that applied to the origin has to apply to every source: only pages that made it
    into the output keep their bookmarks."""
    from klarpdf.model.virtual_document import PageRef, VirtualDocument

    first, second = two_outlined_pdfs
    vdoc = VirtualDocument.from_path(first)
    try:
        sid = vdoc.open_source(second)
        vdoc.append_pages([PageRef(sid, 0)])              # only the second document's first page
        titles = [entry[1] for entry in vdoc.remapped_toc()]
    finally:
        vdoc.close()
    assert titles == ["F-One", "F-One-a", "F-Two", "S-One"]
