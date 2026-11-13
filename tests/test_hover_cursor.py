"""The pointer changes before a click (PLAN.md §M175, #449). Offscreen GUI.

Over a link the pointer is a pointing hand, over a resize handle a resize arrow, and over a text box
the four-way move cross. `PdfView._update_hover_cursor` decided all of this from M33 on, but it ran
only from `mouseMoveEvent`, and Qt sends that without a button held only to a widget with **mouse
tracking** on. The page area never had it, so a reader saw an arrow everywhere. The tests that
existed called `_update_hover_cursor` directly and passed.

So every test here moves the mouse the way Qt delivers it. `_hover` sends a move with no button
held through `QApplication.sendEvent`, which drops it, as the window system path does, when the
viewport has tracking off. It is not `QTest.mouseMove`: QTest remembers buttons across tests, and a
test that presses without releasing would turn every later move here into a drag.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import PdfApp
from klarpdf.model.page_edits import Shape, TextBox
from store.settings import Settings
from viewer.pdf_view import _HOVER_SETTLE_MS
from viewer.resize_handles import cursor_for


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


_GOTO_BOX = (72, 100, 200, 120)     # page 0 → page 3
_WEB_BOX = (72, 180, 200, 200)      # page 0 → https://example.org/docs
_JS_BOX = (72, 260, 200, 280)       # page 0 → javascript:…, which a click never opens
_BLANK = (400, 600)                 # a point on page 0 with nothing on it
_WORD = (90, 66)                    # inside "PAGE 0"


@pytest.fixture
def hover_pdf(tmp_path) -> str:
    path = str(tmp_path / "hover.pdf")
    doc = fitz.open()
    for i in range(5):
        doc.new_page().insert_text((72, 72), f"PAGE {i}", fontsize=20)
    doc[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(*_GOTO_BOX), "page": 3,
                        "to": fitz.Point(0, 0)})
    doc[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(*_WEB_BOX),
                        "uri": "https://example.org/docs"})
    doc[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(*_JS_BOX),
                        "uri": "javascript:alert(1)"})
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def win(app, hover_pdf):
    w = app.open_document(hover_pdf)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _scene(win, x: float, y: float) -> QPointF:
    return win.view.scene_rect_for_box(0, (x, y, x, y)).center()


def _centre(win, box) -> QPointF:
    return win.view.scene_rect_for_box(0, box).center()


def _move(win, scene_pt: QPointF, buttons=Qt.MouseButton.NoButton) -> None:
    viewport = win.view.viewport()
    pos = QPointF(win.view.mapFromScene(scene_pt))
    event = QMouseEvent(QEvent.Type.MouseMove, pos, QPointF(viewport.mapToGlobal(pos)),
                        Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, event)
    QApplication.processEvents()


def _hover(win, scene_pt: QPointF) -> Qt.CursorShape:
    """Rest the pointer on ``scene_pt`` with no button held, and return the pointer's shape. It
    moves to a blank spot first, so the shape it returns is what this point set."""
    _move(win, _scene(win, *_BLANK))
    _move(win, scene_pt)
    return win.view.viewport().cursor().shape()


def _shape(win) -> Qt.CursorShape:
    return win.view.viewport().cursor().shape()


def _settle() -> None:
    QTest.qWait(_HOVER_SETTLE_MS * 3)


# ---- links ----------------------------------------------------------------------


def test_the_pointer_is_a_hand_over_a_link_to_a_page(win):
    assert _hover(win, _centre(win, _GOTO_BOX)) == Qt.CursorShape.PointingHandCursor


def test_the_pointer_is_a_hand_over_a_web_link(win):
    assert _hover(win, _centre(win, _WEB_BOX)) == Qt.CursorShape.PointingHandCursor


def test_the_pointer_is_an_arrow_again_off_a_link(win):
    _hover(win, _centre(win, _GOTO_BOX))
    _move(win, _scene(win, *_BLANK))
    assert _shape(win) == Qt.CursorShape.ArrowCursor


def test_a_link_a_click_will_not_open_does_not_look_clickable(win):
    assert _hover(win, _centre(win, _JS_BOX)) == Qt.CursorShape.ArrowCursor


@pytest.mark.parametrize("turn", [90, 180, 270])
def test_a_link_on_a_turned_and_cropped_page_is_found_where_it_shows(win, turn):
    """The link test maps the pointer into the page once, instead of each link onto the screen
    (M175). The two must agree wherever a turn or a crop moves the page's origin."""
    win.vdoc.rotate_pages([0], turn)
    win.vdoc.set_crop([0], (36.0, 48.0, 500.0, 700.0))
    win.view.reload()
    QApplication.processEvents()
    assert _hover(win, _centre(win, _GOTO_BOX)) == Qt.CursorShape.PointingHandCursor
    assert _hover(win, _centre(win, (72, 140, 200, 160))) == Qt.CursorShape.ArrowCursor


# ---- marks ----------------------------------------------------------------------


def test_the_pointer_is_a_resize_arrow_over_a_handle(win):
    win.vdoc.add_annotation(0, Shape("rect", (100.0, 300.0, 160.0, 340.0)))
    win.view.reload()
    win.view.annotations.select_object(0, win.vdoc.page_annotations(0)[-1])
    assert _hover(win, _scene(win, 160, 340)) == cursor_for("se")
    assert cursor_for("se") != Qt.CursorShape.ArrowCursor


def test_the_pointer_is_a_move_cross_over_a_text_box(win):
    win.vdoc.add_annotation(0, TextBox((100.0, 300.0, 200.0, 330.0), "note"))
    win.view.reload()
    assert _hover(win, _scene(win, 150, 315)) == Qt.CursorShape.SizeAllCursor


# ---- a bare pointer is not a drag ------------------------------------------------


def test_a_move_with_no_button_held_does_not_carry_on_a_drag(win):
    """Until M175 no move reached the drag code without a button held. A drag whose release went
    elsewhere, such as to another window, must stay where it was rather than follow the pointer."""
    view = win.view
    pos = QPointF(view.mapFromScene(_scene(win, *_WORD)))
    view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton,
                                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert view.selection.active                     # the press started a text selection
    _move(win, _scene(win, 300, 400))                # ...and its release never arrived
    assert view.selection.selected_words() == []
    _move(win, _scene(win, 300, 400), buttons=Qt.MouseButton.LeftButton)
    assert view.selection.selected_words() != []     # the same move with the button held does


# ---- the page moves under a still pointer ------------------------------------------


def test_a_scroll_under_a_still_pointer_changes_it_once_the_page_stops(win):
    view = win.view
    view.set_zoom(2.0)
    view.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    assert _hover(win, _centre(win, _GOTO_BOX)) == Qt.CursorShape.PointingHandCursor
    view.verticalScrollBar().setValue(400)
    _settle()
    assert _shape(win) == Qt.CursorShape.ArrowCursor
    view.verticalScrollBar().setValue(0)
    _settle()
    assert _shape(win) == Qt.CursorShape.PointingHandCursor


def test_an_edit_under_a_still_pointer_changes_it(win):
    """A rebuild with no scroll, such as an undo that removes the box under the pointer."""
    box = TextBox((100.0, 300.0, 200.0, 330.0), "note")
    win.vdoc.add_annotation(0, box)
    win.view.reload()
    assert _hover(win, _scene(win, 150, 315)) == Qt.CursorShape.SizeAllCursor
    win.vdoc.remove_annotation(0, win.vdoc.page_annotations(0)[-1])
    win.view.reload()
    _settle()
    assert _shape(win) == Qt.CursorShape.ArrowCursor


@pytest.mark.parametrize("gone", ["a button is held", "the pointer left"])
def test_a_scroll_leaves_the_pointer_alone_when_it_is_not_resting_on_the_page(win, gone):
    view = win.view
    view.set_zoom(2.0)
    view.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    assert _hover(win, _centre(win, _GOTO_BOX)) == Qt.CursorShape.PointingHandCursor
    if gone == "a button is held":
        pos = QPointF(view.mapFromScene(_scene(win, *_BLANK)))
        view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton,
                                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    else:
        QApplication.sendEvent(view.viewport(), QEvent(QEvent.Type.Leave))
    view.verticalScrollBar().setValue(400)
    _settle()
    assert _shape(win) == Qt.CursorShape.PointingHandCursor   # not checked again
