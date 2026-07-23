"""Underline & strikeout (PLAN.md §GUI feature roadmap, M56 — R3 "Markup Tools").

The same text-quad path as Highlight: one continuous bar per selected line, author-tagged at
bake, read back on reopen as editable model descriptors. Flatten/print inherit automatically via
``apply_annotations``. The Done-when: underline/strikeout bake, reopen editable, print/flatten
correctly.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.export import export_flattened_pdf
from model.page_edits import (
    KLARPDF_AUTHOR,
    Strikeout,
    Underline,
    apply_annotations,
    read_klarpdf_annotations,
    strip_klarpdf_annotations,
)
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.tools import ArmedTool

_BARS = ((70.0, 66.0, 220.0, 80.0), (70.0, 86.0, 180.0, 100.0))  # two line bars


def _annots(path, page_index=0):
    doc = fitz.open(path)
    try:
        return [(a.type[0], a.info.get("title")) for a in doc[page_index].annots()]
    finally:
        doc.close()


# ---- bake + read-back (model) ------------------------------------------------


def test_underline_and_strikeout_bake_author_tagged(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Underline(_BARS))
    v.add_annotation(0, Strikeout((_BARS[0],)))
    out = str(tmp_path / "m.pdf")
    PyMuPDFEngine().materialize(v, out)
    kinds = _annots(out)
    assert (fitz.PDF_ANNOT_UNDERLINE, KLARPDF_AUTHOR) in kinds
    assert (fitz.PDF_ANNOT_STRIKE_OUT, KLARPDF_AUTHOR) in kinds


def test_round_trip_reopen_editable(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Underline(_BARS, color=(0.1, 0.5, 0.1)))
    v.add_annotation(1, Strikeout((_BARS[0],)))
    out = str(tmp_path / "m.pdf")
    PyMuPDFEngine().materialize(v, out)

    reopened = VirtualDocument.from_path(out)
    under = [a for a in reopened.page_annotations(0) if isinstance(a, Underline)]
    strike = [a for a in reopened.page_annotations(1) if isinstance(a, Strikeout)]
    assert len(under) == 1 and len(strike) == 1
    assert len(under[0].rects) == 2                       # both line bars came back
    assert under[0].color == pytest.approx((0.1, 0.5, 0.1))


def test_save_reopen_save_does_not_drift(a_pdf, tmp_path):
    """Two bake→read cycles keep the bar geometry stable (the M57 Done-when phrasing, applied
    here): the quad→rect→quad round trip must not creep."""
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Underline(_BARS))
    first = str(tmp_path / "one.pdf")
    PyMuPDFEngine().materialize(v, first)

    second_doc = VirtualDocument.from_path(first)
    second = str(tmp_path / "two.pdf")
    PyMuPDFEngine().materialize(second_doc, second)
    reread = VirtualDocument.from_path(second)
    (mark,) = [a for a in reread.page_annotations(0) if isinstance(a, Underline)]
    for got, expected in zip(mark.rects, _BARS):
        assert got == pytest.approx(expected, abs=1.0)    # stable within a point


def test_strip_removes_ours(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Strikeout(_BARS))
    out = str(tmp_path / "m.pdf")
    PyMuPDFEngine().materialize(v, out)
    doc = fitz.open(out)
    try:
        strip_klarpdf_annotations(doc[0])
        assert list(doc[0].annots()) == []
        assert read_klarpdf_annotations(doc[0]) == ()
    finally:
        doc.close()


def test_flatten_bakes_marks_into_content(a_pdf, tmp_path):
    """Export → Flattened PDF inherits the marks via apply_annotations + bake(): no annotation
    survives, but the page carries the drawn bars as content (the ink is in the pixels)."""
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Underline(_BARS))
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(v, out)
    doc = fitz.open(out)
    try:
        assert list(doc[0].annots()) == []                # locked: no annotations remain
        pix_marked = doc[0].get_pixmap()
    finally:
        doc.close()
    clean = VirtualDocument.from_path(a_pdf)
    clean_out = str(tmp_path / "clean.pdf")
    export_flattened_pdf(clean, clean_out)
    with fitz.open(clean_out) as doc:
        pix_clean = doc[0].get_pixmap()
    assert pix_marked.samples != pix_clean.samples        # the bars drew something


def test_apply_annotations_is_direct(a_pdf):
    """The page-level primitive: apply then read back symmetrically on a live page."""
    doc = fitz.open(a_pdf)
    try:
        marks = (Underline(_BARS), Strikeout((_BARS[1],)))
        apply_annotations(doc[0], marks)
        back = read_klarpdf_annotations(doc[0])
        assert [type(a).__name__ for a in back] == ["Underline", "Strikeout"]
    finally:
        doc.close()


# ---- the selection path + menus (offscreen GUI) ------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _select_first_word(win):
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    word = page.get_text("words")[0]
    center = win.view.scene_rect_for_box(0, word[:4]).center()
    assert win.view.selection.select_word_at(center) is True


def test_underline_selection_creates_one_undoable_mark(win):
    _select_first_word(win)
    win._underline_selection()
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Underline)]
    assert len(marks) == 1
    assert win.undo_stack.undoText() == "Underline"
    win.undo_stack.undo()
    assert win.vdoc.page_annotations(0) == ()


def test_strikeout_selection_via_armed_tool_signal(win):
    _select_first_word(win)
    win._apply_text_tool(ArmedTool.STRIKEOUT)             # what a drag-release emits
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Strikeout)]
    assert len(marks) == 1
    assert win.view.selection.selected_words() == []      # selection consumed


def test_armed_underline_applies_to_live_selection_immediately(win):
    """Owner rule from the M46 review, inherited by the new tools: clicking a drag-over-text
    tool while a selection is live applies at once instead of arming."""
    _select_first_word(win)
    win._arm_tool(ArmedTool.UNDERLINE)
    assert win.view.armed is None                         # applied, not armed
    assert any(isinstance(a, Underline) for a in win.vdoc.page_annotations(0))


def test_markup_split_button_faces_last_used_tool(win):
    button = win._markup_button
    actions = {a.text(): a for a in button.menu().actions() if not a.isSeparator()}
    # The three verbs, plus the M59.9 colour palettes that live with them.
    assert {"Highlight", "Underline", "Strike Out"} <= set(actions)
    assert "Highlight Colour" in actions and "Underline / Strike Colour" in actions
    assert button.defaultAction().text() == "Highlight"   # the initial face
    button.menu().triggered.emit(actions["Underline"])    # pick from the drop-down
    assert button.defaultAction().text() == "Underline"   # sticky last-used face


def test_picking_a_markup_colour_does_not_hijack_the_button_face(win):
    """QMenu.triggered fires for sub-menu entries too, so the sticky-face wiring must ignore
    anything that isn't one of the three tools — else the button would read "Yellow"."""
    button = win._markup_button
    swatch = next(iter(win._highlight_color_actions.values()))
    button.menu().triggered.emit(swatch)
    assert button.defaultAction().text() == "Highlight"   # unchanged


def test_remove_labels_in_context_menu(win):
    from viewer.markup_style import SwatchRowAction

    win.vdoc.add_annotation(0, Underline(_BARS))
    win.view.reload()
    win.view.annotations.repaint()
    center = win.view.scene_rect_for_box(0, _BARS[0]).center()
    menu = win._view_context_menu(center)
    # Since M76.1 removal is the Underline row's slashed dot; the undo label keeps the words.
    row = next(a for a in menu.actions()
               if isinstance(a, SwatchRowAction) and a.title == "Underline")
    assert row.remove_button.toolTip() == "Remove underline"
    row.remove_button.click()
    assert win.undo_stack.undoText() == "Remove underline"
