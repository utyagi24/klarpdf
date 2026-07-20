"""Adopt-on-edit (PLAN.md §R5, M68). Headless + offscreen GUI.

M66's delete and M67's move never look *inside* an annotation — which is exactly what makes them
safe for every type. Adoption is the opposite: it parses a foreign annotation into one of our
descriptors so it becomes fully editable. That is only possible for the types the model represents,
and only *faithful* when the annotation uses nothing the model cannot carry.

Hence the two claims under test:

* **adopt → edit → save round-trips** — an adopted mark becomes an ordinary KlarPDF mark, and the
  original is stripped rather than left underneath;
* **the degrade warning fires exactly when features would be lost** — not "whenever we're unsure",
  which would train people to click through it, and never silently.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.foreign_annots import (
    adopt_annotation,
    degradations,
    find_annotation,
    is_adoptable,
    read_foreign_annotations,
)
from model.page_edits import KLARPDF_AUTHOR, Shape, TextBox
from model.virtual_document import VirtualDocument
from store.settings import Settings


def _foreign(page, kind, rect, contents="", author="Alice"):
    if kind == "square":
        annot = page.add_rect_annot(fitz.Rect(rect))
    elif kind == "circle":
        annot = page.add_circle_annot(fitz.Rect(rect))
    elif kind == "highlight":
        annot = page.add_highlight_annot(fitz.Rect(rect))
    elif kind == "underline":
        annot = page.add_underline_annot(fitz.Rect(rect))
    elif kind == "ink":
        annot = page.add_ink_annot([[(rect[0], rect[1]), (rect[2], rect[3])]])
    elif kind == "line":
        annot = page.add_line_annot(fitz.Point(rect[0], rect[1]), fitz.Point(rect[2], rect[3]))
    elif kind == "stamp":
        annot = page.add_stamp_annot(fitz.Rect(rect), stamp=0)
    elif kind == "text":
        annot = page.add_text_annot(fitz.Point(rect[0], rect[1]), contents or "note")
    else:
        annot = page.add_freetext_annot(fitz.Rect(rect), contents or "callout")
    annot.set_info(title=author, content=contents)
    annot.update()
    return annot


@pytest.fixture
def adopt_pdf(tmp_path) -> str:
    path = str(tmp_path / "adopt.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 500), "BODYTEXT", fontsize=12)
    _foreign(page, "square", (100, 100, 200, 160), "a square")
    _foreign(page, "text", (300, 100, 320, 120), "a sticky note")   # not a modeled type
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def vdoc(adopt_pdf):
    v = VirtualDocument.from_path(adopt_pdf)
    yield v
    v.close()


def _source_page(vdoc, index=0):
    ref = vdoc.ordered[index]
    return vdoc.sources[ref.source_id][ref.source_page_index]


def _one(page, kind, rect, contents="", **info):
    """A single-annotation document, for the per-feature degradation checks."""
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((60, 300), "some text", fontsize=12)
    annot = _foreign(p, kind, rect, contents)
    return doc, p, annot


# ---- which types can be adopted -------------------------------------------------


@pytest.mark.parametrize("kind", ["square", "circle", "highlight", "underline", "ink",
                                  "line", "freetext"])
def test_modeled_types_are_adoptable(kind):
    doc, _page, annot = _one(None, kind, (60, 292, 160, 306))
    try:
        assert is_adoptable(annot) is True
        assert adopt_annotation(annot) is not None
    finally:
        doc.close()


@pytest.mark.parametrize("kind", ["text", "stamp"])
def test_unmodeled_types_are_not_adoptable(kind):
    """A sticky note and a stamp stay delete/move only — the model has no descriptor for them, and
    inventing one would mean re-drawing something we cannot reproduce."""
    doc, _page, annot = _one(None, kind, (60, 292, 160, 306), "x")
    try:
        assert is_adoptable(annot) is False
        assert adopt_annotation(annot) is None
    finally:
        doc.close()


def test_adoption_uses_the_same_parser_as_the_round_trip(vdoc):
    """One parser for both paths, so an adopted mark and one we drew cannot drift apart."""
    page = _source_page(vdoc)     # held: PyMuPDF weak-references an annot's page
    annot = next(a for a in page.annots() if a.type[0] == fitz.PDF_ANNOT_SQUARE)
    adopted = adopt_annotation(annot)
    assert isinstance(adopted, Shape)
    assert adopted.kind == "rect"
    assert adopted.rect == pytest.approx((100, 100, 200, 160), abs=2)


# ---- the degrade warning fires exactly when something is lost -------------------


def test_a_plain_annotation_degrades_nothing(vdoc):
    page = _source_page(vdoc)     # held: PyMuPDF weak-references an annot's page
    annot = next(a for a in page.annots() if a.type[0] == fitz.PDF_ANNOT_SQUARE)
    assert degradations(annot) == []


def test_rich_text_is_reported():
    doc, page, annot = _one(None, "freetext", (60, 200, 260, 260), "styled")
    try:
        doc.xref_set_key(annot.xref, "RC", fitz.get_pdf_str("<body>rich</body>"))
        assert "rich text formatting" in degradations(annot)
    finally:
        doc.close()


def test_a_callout_line_is_reported():
    """Detected by ``/IT /FreeTextCallout``, the spec's intent marker — **not** by ``/CL`` being
    present, which PyMuPDF writes on every FreeText it creates."""
    doc, page, annot = _one(None, "freetext", (60, 200, 260, 260), "callout")
    try:
        assert "its callout line" not in degradations(annot)      # a plain text box: no warning
        doc.xref_set_key(annot.xref, "CL", "[10 20 30 40]")
        doc.xref_set_key(annot.xref, "IT", "/FreeTextCallout")
        assert "its callout line" in degradations(annot)
    finally:
        doc.close()


@pytest.mark.parametrize("kind", ["square", "circle", "freetext", "highlight", "line", "ink"])
def test_an_ordinary_annotation_of_any_modeled_type_warns_about_nothing(kind):
    """The cry-wolf guard. ``/RD`` (a border inset) and ``/CL`` are written routinely by the very
    library that produced these fixtures, so a naive key-presence check warns on marks that lose
    nothing at all — which is how a warning stops being read."""
    doc, page, annot = _one(None, kind, (60, 292, 160, 306))
    try:
        assert degradations(annot) == []
    finally:
        doc.close()


def test_transparency_is_reported_for_text_markup():
    """A Highlight descriptor has no opacity field, so a translucent one would come back solid."""
    doc, page, annot = _one(None, "highlight", (60, 292, 160, 306))
    try:
        annot.set_opacity(0.4)
        annot.update()
        assert "its transparency" in degradations(annot)
    finally:
        doc.close()


def test_transparency_is_not_reported_for_drawn_marks():
    """A Shape *does* carry opacity (M59.9), so nothing is lost — the warning must not cry wolf."""
    doc, page, annot = _one(None, "square", (60, 200, 160, 260))
    try:
        annot.set_opacity(0.4)
        annot.update()
        assert "its transparency" not in degradations(annot)
    finally:
        doc.close()


def test_a_non_base14_font_is_reported():
    doc, page, annot = _one(None, "freetext", (60, 200, 260, 260), "text")
    try:
        doc.xref_set_key(annot.xref, "DA", fitz.get_pdf_str("0 g /CustomFont 12 Tf"))
        assert any("font" in item for item in degradations(annot))
    finally:
        doc.close()


def test_a_base14_font_is_not_reported():
    doc, page, annot = _one(None, "freetext", (60, 200, 260, 260), "text")
    try:
        doc.xref_set_key(annot.xref, "DA", fitz.get_pdf_str("0 g /Helv 12 Tf"))
        assert not any("font" in item for item in degradations(annot))
    finally:
        doc.close()


def test_a_reply_thread_is_reported():
    doc, page, annot = _one(None, "square", (60, 200, 160, 260))
    try:
        doc.xref_set_key(annot.xref, "IRT", f"{annot.xref} 0 R")
        assert "its reply thread" in degradations(annot)
    finally:
        doc.close()


# ---- adopt → edit → save round-trips --------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, adopt_pdf):
    w = MainWindow(app, adopt_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _square(win):
    return next(a for a in win.view.annotations.foreign_annotations(0) if a.kind_name == "Square")


def _shapes(win):
    return [a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape)]


def _materialize(win, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(win.vdoc, out)
    return out


def test_adopting_creates_an_editable_mark(win):
    assert win._adopt_foreign_annotation(0, _square(win)) is True
    assert len(_shapes(win)) == 1
    assert _shapes(win)[0].kind == "rect"


def test_the_original_stops_being_foreign(win):
    """It is replaced, not shadowed — otherwise the page would carry both and the user would see
    the mark twice after a save."""
    win._adopt_foreign_annotation(0, _square(win))
    assert [a.kind_name for a in win.view.annotations.foreign_annotations(0)] == ["Text"]


def test_the_saved_file_has_exactly_one_and_it_is_ours(win, tmp_path):
    win._adopt_foreign_annotation(0, _square(win))
    saved = fitz.open(_materialize(win, tmp_path))
    try:
        squares = [a for a in saved[0].annots() if a.type[0] == fitz.PDF_ANNOT_SQUARE]
        assert len(squares) == 1
        assert (squares[0].info or {}).get("title") == KLARPDF_AUTHOR
    finally:
        saved.close()


def test_an_adopted_mark_round_trips_on_reopen(win, tmp_path):
    """From adoption onwards it behaves exactly like a mark we drew: reopening finds it editable."""
    win._adopt_foreign_annotation(0, _square(win))
    out = _materialize(win, tmp_path)
    reopened = VirtualDocument.from_path(out)
    try:
        shapes = [a for a in reopened.page_annotations(0) if isinstance(a, Shape)]
        assert len(shapes) == 1
        assert read_foreign_annotations(
            reopened.sources[reopened.ordered[0].source_id][0]
        )[0].kind_name == "Text"                        # only the un-adopted sticky note is foreign
    finally:
        reopened.close()


def test_adoption_is_one_undo_step(win):
    win._adopt_foreign_annotation(0, _square(win))
    win.undo_stack.undo()
    assert _shapes(win) == []
    assert len(win.view.annotations.foreign_annotations(0)) == 2


def test_an_adopted_mark_is_then_editable(win):
    """The point of adopting: the mark now answers to the ordinary object tools."""
    win._adopt_foreign_annotation(0, _square(win))
    overlay = win.view.annotations
    win.view.reload()
    shape = _shapes(win)[0]
    overlay.select_object(0, shape)
    assert overlay.remove_selected_objects() is True
    assert _shapes(win) == []


def test_adopting_a_moved_mark_keeps_it_where_it_was_put(win):
    """Adopting after dragging must not snap the mark back to its original spot."""
    from model.foreign_annots import ForeignMove

    mark = _square(win)
    win._move_foreign_annotation(0, mark, 40.0, 25.0)
    win._adopt_foreign_annotation(0, win.view.annotations.foreign_annotations(0)[0])
    assert _shapes(win)[0].rect[0] == pytest.approx(100 + 40, abs=3)
    # …and the now-redundant move descriptor is gone, not left aimed at a deleted annotation.
    assert [a for a in win.vdoc.page_annotations(0) if isinstance(a, ForeignMove)] == []


def test_an_unmodeled_type_is_declined_with_an_explanation(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    seen = {}
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: seen.setdefault("text", a[2])))
    note = next(a for a in win.view.annotations.foreign_annotations(0) if a.kind_name == "Text")
    assert win._adopt_foreign_annotation(0, note) is False
    assert "can't edit" in seen["text"]


def test_the_degrade_warning_gates_the_adoption(win, monkeypatch, tmp_path):
    """Declining the warning must leave the annotation completely untouched."""
    ref = win.vdoc.ordered[0]
    source = win.vdoc.sources[ref.source_id][ref.source_page_index]
    annot = find_annotation(source, _square(win).fingerprint)
    source.parent.xref_set_key(annot.xref, "RC", fitz.get_pdf_str("<body>rich</body>"))

    monkeypatch.setattr(MainWindow, "_confirm_degrade", lambda self, mark, lost: False)
    assert win._adopt_foreign_annotation(0, _square(win)) is False
    assert _shapes(win) == []
    assert len(win.view.annotations.foreign_annotations(0)) == 2


def test_accepting_the_degrade_warning_adopts(win, monkeypatch):
    ref = win.vdoc.ordered[0]
    source = win.vdoc.sources[ref.source_id][ref.source_page_index]
    annot = find_annotation(source, _square(win).fingerprint)
    source.parent.xref_set_key(annot.xref, "RC", fitz.get_pdf_str("<body>rich</body>"))

    captured = {}
    monkeypatch.setattr(MainWindow, "_confirm_degrade",
                        lambda self, mark, lost: captured.setdefault("lost", lost) or True)
    assert win._adopt_foreign_annotation(0, _square(win)) is True
    assert "rich text formatting" in captured["lost"]


def test_no_warning_when_nothing_would_be_lost(win, monkeypatch):
    """Fires *exactly* when something goes — a warning on every adoption would train people to
    click through the one that matters."""
    monkeypatch.setattr(MainWindow, "_confirm_degrade",
                        lambda self, mark, lost: pytest.fail("warned with nothing to lose"))
    assert win._adopt_foreign_annotation(0, _square(win)) is True


def test_a_freetext_adopts_as_an_editable_text_box(tmp_path, app):
    path = str(tmp_path / "ft.pdf")
    doc = fitz.open()
    page = doc.new_page()
    _foreign(page, "freetext", (100, 100, 300, 160), "someone else's note")
    doc.save(path)
    doc.close()

    window = MainWindow(app, path, app.settings)
    try:
        mark = window.view.annotations.foreign_annotations(0)[0]
        assert window._adopt_foreign_annotation(0, mark) is True
        boxes = [a for a in window.vdoc.page_annotations(0) if isinstance(a, TextBox)]
        assert len(boxes) == 1
        assert boxes[0].text == "someone else's note"
    finally:
        window.undo_stack.setClean()
        window.close()
