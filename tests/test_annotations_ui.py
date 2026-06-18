"""Viewer annotation interaction (PLAN.md, M20 — PR-B). Offscreen GUI.

Select-then-highlight, the text-box placement tool, and the overlay preview — driven through a real
MainWindow on the page-edit-layer model from PR-A.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from app import PdfApp
from model.page_edits import Highlight, TextBox
from store.settings import Settings
from viewer.tools import InteractionMode


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(a_pdf)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()  # avoid the dirty-close prompt blocking teardown
    w.close()


def _first_word_center(win):
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    box = page.get_text("words")[0][:4]
    return win.view.scene_rect_for_box(0, box).center()


def test_highlight_selection_creates_highlight(win):
    win.view.selection.select_word_at(_first_word_center(win))  # select a word
    assert win.view.selection.selected_words()
    win._highlight_selection()
    annots = win.vdoc.page_annotations(0)
    assert any(isinstance(a, Highlight) for a in annots)


def test_highlight_with_no_selection_does_nothing(win):
    win.view.selection.clear()
    win._highlight_selection()
    assert win.vdoc.page_annotations(0) == ()


def test_textbox_tool_places_annotation(win):
    win.view.set_mode(InteractionMode.TEXTBOX)
    center = win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center()
    assert win.view.annotations.place_textbox(center) is True
    win.view.annotations._editor.setPlainText("Hello note")
    win.view.annotations._commit_textbox()
    annots = win.vdoc.page_annotations(0)
    assert any(isinstance(a, TextBox) and a.text == "Hello note" for a in annots)


def test_textbox_editor_follows_zoom(win):
    win.view.set_mode(InteractionMode.TEXTBOX)
    win.view.annotations.place_textbox(win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center())
    before = win.view.annotations._editor.geometry()
    win.view.set_zoom(win.view.zoom * 1.5)
    assert win.view.annotations._editor.geometry() != before  # editor tracked the zoom


def test_empty_textbox_adds_nothing(win):
    win.view.set_mode(InteractionMode.TEXTBOX)
    win.view.annotations.place_textbox(win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center())
    win.view.annotations._editor.setPlainText("   ")  # whitespace only
    win.view.annotations._commit_textbox()
    assert win.vdoc.page_annotations(0) == ()


def test_textbox_mode_click_routes_to_tool(win):
    win.view.set_mode(InteractionMode.TEXTBOX)
    center = win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center()
    vp = QPointF(win.view.mapFromScene(center))
    press = QMouseEvent(QEvent.Type.MouseButtonPress, vp, vp, Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.view.mousePressEvent(press)
    assert win.view.annotations._editor is not None  # the click opened the inline editor


def test_overlay_paints_existing_annotations(win):
    win.vdoc.add_annotation(0, Highlight(((72, 72, 160, 92),)))
    win.vdoc.add_annotation(0, TextBox((72, 150, 300, 180), "note"))
    win.view.annotations.repaint()
    assert len(win.view.annotations._items) >= 2  # highlight rect + text-box box/text


def test_annotation_at_hit_test_and_remove(win):
    tb = TextBox((100, 120, 300, 160), "removable")
    win.vdoc.add_annotation(0, tb)
    win.view.annotations.repaint()
    center = win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center()
    hit = win.view.annotations.annotation_at(center)
    assert hit is not None and hit[1] is tb           # right-click finds the annotation
    win.view.annotations.remove(hit[0], hit[1])       # what the context menu calls
    assert tb not in win.vdoc.page_annotations(0)     # removed (undoable)


def test_annotation_at_returns_none_off_annotation(win):
    win.vdoc.add_annotation(0, TextBox((100, 120, 300, 160), "x"))
    win.view.annotations.repaint()
    off = win.view.scene_rect_for_box(0, (100, 400, 200, 430)).center()  # empty area
    assert win.view.annotations.annotation_at(off) is None
