"""M31 preview render path (offscreen GUI + headless model).

A round-tripped (baked) annotation must render from a per-source copy with **our** marks stripped,
so the rendered page pixmap is clean and the editable overlay is the single source of truth. Without
this, ``get_pixmap`` bakes the mark into the page *and* the overlay draws the model's copy on top:
the original shows twice, and moving / deleting a reopened annotation only shifts / hides the
overlay while the baked original stays pinned (the bug this guards against). Foreign annotations and
the shared source are left untouched.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication

from app import PdfApp
from model.edit_engine import PyMuPDFEngine
from model.page_edits import Highlight, TextBox, page_has_klarpdf_annotations
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.pdf_view import PdfView
from viewer.text_selection import TextSelection


@pytest.fixture(scope="session")
def qapp():
    # A PdfApp (a QApplication subclass) so the thumbnail test can use open_document; the PdfView
    # tests only need it to be *a* QApplication.
    return PdfApp.instance() or PdfApp([])


def _clean_pdf(tmp_path, name="clean.pdf") -> str:
    path = str(tmp_path / name)
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "HELLO WORLD sample text here", fontsize=14)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def baked_pdf(tmp_path) -> str:
    """A saved PDF with a baked KlarPDF highlight + filled text-box on page 0, plus a clean page 1.

    The text-box has a fill over a blank region (page text sits at y≈100) so its presence /
    absence in a render is measurable as ink.
    """
    src = str(tmp_path / "src.pdf")
    doc = fitz.open()
    for i in range(2):
        doc.new_page().insert_text((72, 100), f"Page {i} HELLO WORLD sample text", fontsize=14)
    doc.save(src)
    doc.close()

    v = VirtualDocument.from_path(src)
    page = v.sources[v.ordered[0].source_id][0]
    rects = tuple(tuple(w[:4]) for w in page.get_text("words")[:2])
    v.add_annotation(0, Highlight(rects))
    v.add_annotation(0, TextBox(BOX, "note", fill_color=(0.1, 0.2, 0.8)))
    out = str(tmp_path / "baked.pdf")
    PyMuPDFEngine().materialize(v, out)
    return out


BOX = (200.0, 300.0, 380.0, 360.0)  # a blank region of page 0


def _region_ink(pixmap, box) -> int:
    """Count non-near-white pixels inside ``box`` (page points == pixels at zoom 1.0)."""
    img = pixmap.toImage()
    if img.isNull():
        return 0
    x0, y0, x1, y1 = (int(v) for v in box)
    n = 0
    for y in range(max(0, y0), min(y1, img.height())):
        for x in range(max(0, x0), min(x1, img.width())):
            c = img.pixelColor(x, y)
            if c.red() < 240 or c.green() < 240 or c.blue() < 240:
                n += 1
    return n


# ---- the render copy strips our marks ---------------------------------------


def test_render_copy_strips_our_marks_but_not_the_source(qapp, baked_pdf):
    v = VirtualDocument.from_path(baked_pdf)
    view = PdfView(v)
    try:
        ref = v.ordered[0]
        page = view._render_source_page(ref)
        assert page is not None                          # a render copy is built for the marked source
        assert not page_has_klarpdf_annotations(page)    # our baked marks stripped from the copy
        # The shared source is untouched — another window may reference it.
        assert page_has_klarpdf_annotations(v.sources[ref.source_id][0])
    finally:
        view.deleteLater()


def test_reopened_baked_box_is_absent_from_the_page_pixmap(qapp, baked_pdf):
    v = VirtualDocument.from_path(baked_pdf)
    view = PdfView(v)
    try:
        rendered = _region_ink(view._render_pixmap(0), BOX)
        # Sanity: the raw source page (rendered with annotations, as get_pixmap defaults) DOES show
        # the filled box — proving the region would be inked if we rendered straight from source.
        raw_pm = v.sources[v.ordered[0].source_id][0].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        from PySide6.QtGui import QImage, QPixmap

        raw_img = QImage(raw_pm.samples, raw_pm.width, raw_pm.height, raw_pm.stride,
                         QImage.Format.Format_RGB888)
        raw = _region_ink(QPixmap.fromImage(raw_img.copy()), BOX)
        assert raw > 1000                                # the baked box is there in the raw source
        assert rendered < raw * 0.1                      # but the viewer render stripped it out
    finally:
        view.deleteLater()


def test_clean_document_renders_from_shared_source(qapp, tmp_path):
    v = VirtualDocument.from_path(_clean_pdf(tmp_path))
    view = PdfView(v)
    try:
        assert view._render_source_page(v.ordered[0]) is None  # fast path preserved for clean docs
    finally:
        view.deleteLater()


def test_render_copy_keeps_foreign_annotations(qapp, tmp_path):
    # A source carrying BOTH a foreign mark and a baked KlarPDF mark.
    src = str(tmp_path / "foreign.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "HELLO WORLD here", fontsize=14)
    foreign = page.add_highlight_annot(fitz.Rect(72, 88, 150, 104))
    foreign.set_info(title="other tool")
    foreign.update()
    doc.save(src)
    doc.close()
    baked = str(tmp_path / "foreign_baked.pdf")
    v0 = VirtualDocument.from_path(src)
    v0.add_annotation(0, TextBox(BOX, "mine"))
    PyMuPDFEngine().materialize(v0, baked)  # output now has the foreign highlight + our text box

    v = VirtualDocument.from_path(baked)
    view = PdfView(v)
    try:
        page = view._render_source_page(v.ordered[0])
        assert page is not None
        titles = {a.info.get("title") for a in page.annots()}
        assert titles == {"other tool"}  # foreign kept, ours stripped (the overlay draws ours)
    finally:
        view.deleteLater()


# ---- model-level flags ------------------------------------------------------


def test_source_and_doc_annotation_flags(baked_pdf):
    v = VirtualDocument.from_path(baked_pdf)
    sid = v.ordered[0].source_id
    assert v.source_has_klarpdf_annotations(sid) is True
    assert v.has_baked_klarpdf_annotations() is True
    # Removing the model annotations does NOT clear the source-bytes flag — the marks are still
    # baked into the on-disk source, so the render must stay on the strip path.
    v.clear_annotations(0)
    assert v.has_baked_klarpdf_annotations() is True


def test_clean_document_has_no_baked_flag(tmp_path):
    v = VirtualDocument.from_path(_clean_pdf(tmp_path))
    assert v.has_baked_klarpdf_annotations() is False


# ---- thumbnail stays on the baked path --------------------------------------


def test_thumbnail_stays_baked_after_removing_last_annotation(qapp, baked_pdf, tmp_path):
    """Deleting the last model annotation must not flip the sidebar back to the fast source render —
    that would show the just-deleted mark (still baked into the source) until the next save."""
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(baked_pdf)
    try:
        assert win.thumbs._edited_render() is not None     # reopened-with-marks → baked path
        win.vdoc.clear_annotations(0)                       # remove the round-tripped marks
        baked = win.thumbs._edited_render()
        assert baked is not None                            # still baked path (source bytes have them)
        baked.close()
    finally:
        win.undo_stack.setClean()
        win.close()


# ---- text selection excludes our annotation text ---------------------------


def _selectable_words(view, page_index=0) -> list:
    sel = TextSelection(view)
    return [w[4] for w in sel._words_for(page_index)]


def test_in_session_textbox_text_is_not_selectable(qapp, tmp_path):
    """An in-session text box is a model overlay, not body text — its words never appear in the
    selectable set (it isn't baked into the source the words are read from)."""
    v = VirtualDocument.from_path(_clean_pdf(tmp_path))
    v.add_annotation(0, TextBox(BOX, "INSESSIONNOTE"))
    view = PdfView(v)
    try:
        words = _selectable_words(view)
        assert "HELLO" in words                 # body text is selectable
        assert "INSESSIONNOTE" not in words      # the box's text is not
    finally:
        view.deleteLater()


def test_reopened_textbox_text_is_not_drag_selectable(qapp, tmp_path):
    """After reopen the box is baked into the source — and get_text would include its text — but the
    selection reads the stripped render page, so the box text is consistently not drag-selectable
    (matching the in-session box), rather than selectable from its baked position."""
    src = _clean_pdf(tmp_path, "body.pdf")
    v0 = VirtualDocument.from_path(src)
    v0.add_annotation(0, TextBox(BOX, "SECRETNOTE"))
    out = str(tmp_path / "baked_sel.pdf")
    PyMuPDFEngine().materialize(v0, out)

    v = VirtualDocument.from_path(out)
    assert any(isinstance(a, TextBox) for a in v.page_annotations(0))  # it did round-trip
    view = PdfView(v)
    try:
        words = _selectable_words(view)
        assert "HELLO" in words
        assert "SECRETNOTE" not in words
    finally:
        view.deleteLater()


def test_word_cache_invalidates_on_reorder(qapp, tmp_path):
    """reload() invalidates the selection's word cache, so after a reorder index 0 yields the new
    page's words — not the stale ones cached before the move."""
    src = str(tmp_path / "two.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "PAGEZERO alpha", fontsize=14)
    doc.new_page().insert_text((72, 100), "PAGEONE beta", fontsize=14)
    doc.save(src)
    doc.close()

    v = VirtualDocument.from_path(src)
    view = PdfView(v)
    view.selection = TextSelection(view)  # wire it as MainWindow does
    try:
        assert "PAGEZERO" in [w[4] for w in view.selection._words_for(0)]  # prime the cache
        v.move_pages([0], 2)  # page 0 → the end; index 0 is now the old page 1
        view.reload()
        page0_words = [w[4] for w in view.selection._words_for(0)]
        assert "PAGEONE" in page0_words and "PAGEZERO" not in page0_words
    finally:
        view.deleteLater()
