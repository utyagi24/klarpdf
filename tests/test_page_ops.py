"""Page ops: extract + blank / duplicate (PLAN.md §GUI feature roadmap, M51).

Export ▸ Selected Pages as PDF… extracts a page subset **object-level** through the ordinary
materialise path — the extracted PDF carries the text layer, form fields, and our round-trippable
annotations, and the origin bookmarks / internal links are remapped to the new page numbers.
Insert ▸ Blank Page and Duplicate Pages are plain ``PageRef`` inserts on the undo stack, so they
undo cleanly like every other page edit.
"""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest

import main_window as mw
from app import PdfApp
from main_window import MainWindow
from klarpdf.model.edit_commands import RotatePagesCommand
from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.export import export_selected_pages
from klarpdf.model.page_edits import Redaction, TextBox
from klarpdf.model.virtual_document import VirtualDocument
from store.settings import Settings
from tests.conftest import A_TEXT


def _text(path, page_index=0) -> str:
    with fitz.open(path) as doc:
        return doc[page_index].get_text("text")


def _page_count(path) -> int:
    with fitz.open(path) as doc:
        return doc.page_count


# ---- extract: the object-level page-subset export ---------------------------


def test_extract_writes_selected_pages_in_document_order(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    out = str(tmp_path / "x.pdf")
    export_selected_pages(v, [2, 0, 2], out)  # click order + a duplicate index
    assert _page_count(out) == 2  # deduped, document order
    assert A_TEXT[0] in _text(out, 0) and A_TEXT[2] in _text(out, 1)
    assert A_TEXT[1] not in _text(out, 0) + _text(out, 1)


def test_extract_no_indices_writes_nothing(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    out = str(tmp_path / "none.pdf")
    export_selected_pages(v, [], out)
    assert not os.path.exists(out)


def test_extract_carries_form_field_and_fill(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.set_field_value("name", "EXTRACTED-FILL")
    out = str(tmp_path / "x.pdf")
    export_selected_pages(v, [0], out)  # the field lives on page 0
    with fitz.open(out) as doc:
        widgets = list(doc[0].widgets() or [])
        assert [w.field_name for w in widgets] == ["name"]   # still an interactive field
        assert widgets[0].field_value == "EXTRACTED-FILL"    # with the pending fill applied


def test_extract_remaps_bookmarks_and_drops_the_rest(a_pdf, tmp_path):
    # A.pdf outline: Chapter 1 → p1, Section 1.1 → p2, Chapter 2 → p3.
    v = VirtualDocument.from_path(a_pdf)
    out = str(tmp_path / "x.pdf")
    export_selected_pages(v, [0, 2], out)
    with fitz.open(out) as doc:
        toc = doc.get_toc()
    # Section 1.1 (page 2, not extracted) is dropped; the survivors point at the new pages.
    assert toc == [[1, "Chapter 1", 1], [1, "Chapter 2", 2]]


def test_extract_remaps_internal_links(tmp_path):
    src = str(tmp_path / "linked.pdf")
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"L{i}", fontsize=12)
    # Page 0 links to page 2 (extracted with it) and to page 1 (which won't be).
    doc[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(72, 100, 200, 120),
                        "page": 2, "to": fitz.Point(0, 0)})
    doc[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(72, 140, 200, 160),
                        "page": 1, "to": fitz.Point(0, 0)})
    doc.save(src)
    doc.close()

    v = VirtualDocument.from_path(src)
    out = str(tmp_path / "x.pdf")
    export_selected_pages(v, [0, 2], out)
    with fitz.open(out) as doc:
        links = doc[0].get_links()
    # The surviving link is remapped to the extracted output index; the dangling one is dropped.
    assert [l["page"] for l in links if l["kind"] == fitz.LINK_GOTO] == [1]


def test_extract_applies_per_page_edits(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.set_rotation(0, 90)
    v.add_annotation(0, TextBox((72, 200, 320, 240), "RIDES-ALONG"))
    out = str(tmp_path / "x.pdf")
    export_selected_pages(v, [0], out)
    with fitz.open(out) as doc:
        assert doc[0].rotation == 90
        assert len(list(doc[0].annots())) == 1  # our annotation, still editable (Save-like)
    assert "RIDES-ALONG" in _text(out)


def test_extract_applies_pending_redaction_without_committing_it(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Redaction(((60, 60, 540, 90),)))  # covers page 0's text band
    out = str(tmp_path / "x.pdf")
    export_selected_pages(v, [0], out)
    assert A_TEXT[0] not in _text(out)      # destructive in the extracted copy
    assert v.has_redactions() is True       # still pending (undoable) in the working document


def test_extract_leaves_the_working_document_untouched(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    export_selected_pages(v, [1], str(tmp_path / "x.pdf"))
    assert v.dirty is False and v.page_count == 3 and v.path == a_pdf


def test_extracted_annotations_round_trip(a_pdf, tmp_path):
    """The extract is a Save-like artifact: reopening it reads our marks back as editable."""
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(1, TextBox((72, 200, 320, 240), "ROUND-TRIP"))
    out = str(tmp_path / "x.pdf")
    export_selected_pages(v, [1], out)
    reopened = VirtualDocument.from_path(out)
    assert [type(a).__name__ for a in reopened.page_annotations(0)] == ["TextBox"]


# ---- blank page source + sizing ---------------------------------------------


def test_open_blank_source_is_one_empty_page_and_idempotent_per_size(a_pdf):
    v = VirtualDocument.from_path(a_pdf)
    sid = v.open_blank_source(612.0, 792.0)
    assert v.open_blank_source(612.0, 792.0) == sid       # same size → same shared source
    assert v.open_blank_source(792.0, 612.0) != sid       # different size → its own source
    doc = v.sources[sid]
    assert doc.page_count == 1
    assert doc[0].rect.width == 612 and doc[0].rect.height == 792
    assert doc[0].get_text("text").strip() == ""


def test_page_visible_size_follows_rotation_and_crop(a_pdf):
    v = VirtualDocument.from_path(a_pdf)
    w, h = v.page_visible_size(0)
    assert (w, h) == (595.0, 842.0)                       # fitz's default page is A4
    v.set_rotation(0, 90)
    assert v.page_visible_size(0) == (842.0, 595.0)       # rotated frame swaps the sides
    v.set_rotation(0, None)
    v.set_crop([0], (10.0, 20.0, 110.0, 70.0))
    assert v.page_visible_size(0) == (100.0, 50.0)        # a crop shows only its own rect


def test_blank_page_materialises_empty_between_its_neighbours(a_pdf, tmp_path):
    from klarpdf.model.virtual_document import PageRef

    v = VirtualDocument.from_path(a_pdf)
    sid = v.open_blank_source(612.0, 792.0)
    v.insert_pages(1, [PageRef(sid, 0)])
    out = str(tmp_path / "b.pdf")
    PyMuPDFEngine().materialize(v, out)
    assert _page_count(out) == 4
    assert A_TEXT[0] in _text(out, 0) and _text(out, 1).strip() == ""
    assert A_TEXT[1] in _text(out, 2)


def test_duplicated_ref_materialises_as_two_pages(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.insert_pages(1, [v.ordered[0]])  # duplicate page 0 right after itself
    out = str(tmp_path / "d.pdf")
    PyMuPDFEngine().materialize(v, out)
    assert _page_count(out) == 4
    assert A_TEXT[0] in _text(out, 0) and A_TEXT[0] in _text(out, 1)


# ---- the menu wiring (offscreen GUI) ----------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


def test_insert_blank_page_action_inserts_after_current_and_undoes(app, a_pdf):
    win = MainWindow(app, a_pdf, app.settings)
    win._insert_blank_page()  # no selection → after the current page (0)
    assert win.vdoc.page_count == 4
    ref = win.vdoc.ordered[1]
    assert ref.source_id.startswith("blank:")
    assert win.vdoc.sources[ref.source_id][0].get_text("text").strip() == ""
    win.undo_stack.undo()
    assert win.vdoc.page_count == 3 and not any(
        r.source_id.startswith("blank:") for r in win.vdoc.ordered
    )


def test_insert_blank_page_lands_the_reader_on_the_new_page(app, a_pdf):
    """#288. The inserted page is the thing the reader just asked for, so it gets the marker.

    Before this, the current row was a bare integer that survived the rebuild — so after an insert
    it pointed at a *different* page than it had before, and the new one was off-screen below.
    """
    win = MainWindow(app, a_pdf, app.settings)
    win.view.set_current_page(0)

    win._insert_blank_page()                       # no selection → inserts at index 1

    assert win.view.current_page == 1
    assert win.thumbs.currentRow() == 1
    assert win.vdoc.ordered[1].source_id.startswith("blank:")


def test_inserting_deeper_in_the_document_still_lands_on_the_new_page(app, a_pdf):
    """The reported gesture was on a thumbnail partway down a scrolled strip, not on page 1."""
    win = MainWindow(app, a_pdf, app.settings)
    win.view.set_current_page(2)

    win._insert_blank_page()

    assert win.view.current_page == 3
    assert win.thumbs.currentRow() == 3


def test_the_new_page_is_actually_on_screen_in_the_sidebar(app, tmp_path):
    """#288 as reported, end to end: the reader is partway down a scrolled strip, inserts, and the
    page they just made must be *visible* — not below the fold with the clicked thumbnail pinned to
    the bottom edge.

    Needs a document long enough for the sidebar to scroll; `a_pdf` has three pages and cannot show
    the defect at all.
    """
    path = str(tmp_path / "many.pdf")
    doc = fitz.open()
    for i in range(40):
        doc.new_page(width=612, height=792).insert_text((72, 100), f"page {i}", fontsize=24)
    doc.save(path)
    doc.close()

    win = MainWindow(app, path, app.settings)
    win.thumbs.resize(210, 700)
    win.view.set_current_page(20)          # partway down, strip scrolled
    app.processEvents()

    win._insert_blank_page()
    app.processEvents()

    new_row = win.view.current_page
    assert win.vdoc.ordered[new_row].source_id.startswith("blank:")

    rect = win.thumbs.visualItemRect(win.thumbs.item(new_row))
    port = win.thumbs.viewport().rect()
    assert rect.top() >= port.top() and rect.bottom() <= port.bottom(), (
        f"the new page's row {(rect.top(), rect.bottom())} is outside the sidebar viewport "
        f"{(port.top(), port.bottom())}"
    )


def _page_filling_the_viewport(win) -> int:
    """Which page actually occupies most of the main view — what the reader is *looking at*.

    Deliberately not `win.view.current_page`: that is a stored property, and the whole defect this
    guards is the two disagreeing. `set_current_page` moves the property without scrolling, so
    asserting on it would have passed against the very bug it is here to catch.
    """
    from PySide6.QtCore import QRectF

    view_rect = win.view.mapToScene(win.view.viewport().rect()).boundingRect()
    best, most = -1, -1.0
    for i, page in enumerate(win.view._pages):
        shown = view_rect.intersected(QRectF(page["x"], page["y"], page["w"], page["h"]))
        area = shown.width() * shown.height()
        if area > most:
            best, most = i, area
    return best


def test_the_view_and_the_sidebar_agree_after_an_insert(app, tmp_path):
    """#288 follow-up (owner, 2026-08-26). The first fix moved the sidebar marker onto the new page
    but left the main view on the page it was inserted from, so the two disagreed.

    An insert is a "take me there" gesture: both go.
    """
    path = str(tmp_path / "many.pdf")
    doc = fitz.open()
    for i in range(40):
        doc.new_page(width=612, height=792).insert_text((72, 100), f"page {i}", fontsize=24)
    doc.save(path)
    doc.close()

    win = MainWindow(app, path, app.settings)
    win.thumbs.resize(210, 700)
    win.view.set_current_page(20)
    app.processEvents()

    win._insert_blank_page()
    app.processEvents()

    new_page = 21
    assert win.vdoc.ordered[new_page].source_id.startswith("blank:")
    assert win.thumbs.currentRow() == new_page, "the sidebar marker is not on the new page"
    assert _page_filling_the_viewport(win) == new_page, (
        "the main view is still showing the page the blank was inserted from"
    )


def test_insert_blank_page_matches_the_preceding_pages_size(app, a_pdf):
    win = MainWindow(app, a_pdf, app.settings)
    win.undo_stack.push(RotatePagesCommand(win.vdoc, [0], 90))  # make page 0 landscape first
    win._insert_blank_page()
    ref = win.vdoc.ordered[1]
    page = win.vdoc.sources[ref.source_id][0]
    assert (page.rect.width, page.rect.height) == (842.0, 595.0)  # landscape, like its neighbour


def test_duplicate_pages_action_carries_edits_and_undoes(app, a_pdf):
    win = MainWindow(app, a_pdf, app.settings)
    win.vdoc.set_rotation(1, 180)
    win._duplicate_pages([1])
    assert win.vdoc.page_count == 4
    assert win.vdoc.ordered[2] == win.vdoc.ordered[1]      # the copy sits right after
    assert win.vdoc.ordered[2].rotation_override == 180    # and carries the page's edits
    assert win.undo_stack.undoText() == "Duplicate page"
    win.undo_stack.undo()
    assert win.vdoc.page_count == 3


def test_duplicate_multiple_pages_is_one_undo_step(app, a_pdf):
    win = MainWindow(app, a_pdf, app.settings)
    win._duplicate_pages([0, 2])
    assert win.vdoc.page_count == 5
    # Copies land together after the last selected page, keeping their relative order.
    assert [r.source_page_index for r in win.vdoc.ordered] == [0, 1, 2, 0, 2]
    win.undo_stack.undo()
    assert [r.source_page_index for r in win.vdoc.ordered] == [0, 1, 2]


def test_export_selected_pages_menu_writes_file_and_leaves_doc_untouched(
    app, a_pdf, tmp_path, monkeypatch
):
    win = MainWindow(app, a_pdf, app.settings)
    target = str(tmp_path / "picked.pdf")
    monkeypatch.setattr(
        mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, ""))
    )
    win._export_selected_pages()  # no selection → the current page (0)
    assert _page_count(target) == 1 and A_TEXT[0] in _text(target)
    assert win.vdoc.path == a_pdf and win.vdoc.page_count == 3
    assert win.undo_stack.isClean()  # a derived artifact — the document stays clean


def test_export_selected_pages_menu_cancelled_writes_nothing(app, a_pdf, tmp_path, monkeypatch):
    win = MainWindow(app, a_pdf, app.settings)
    monkeypatch.setattr(
        mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    win._export_selected_pages()
    assert {p.name for p in tmp_path.glob("*.pdf")} == {"A.pdf"}  # only the fixture file
