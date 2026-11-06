"""A drag survives what the page does around it (M165, #431).

A drag keeps an item in the scene, and a scene rebuild destroys it. Pressing a text box's handle
while its editor was open started a drag and then let the editor commit, one tick later, which
rebuilt the scene: every mouse event after that raised on a dead object, the drag was lost, and a
later click put a step that changed nothing on the Undo stack. A zoom or an undo mid-drag did the
same to every kind of drag. These tests send real mouse events, because the order of the editor's
focus-out, the press and the commit is the whole defect. Offscreen.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import PdfApp
from klarpdf.model.page_edits import Shape, TextBox
from main_window import MainWindow
from store.settings import Settings
from viewer.tools import ArmedTool, InteractionMode

_LEFT = Qt.MouseButton.LeftButton
_NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="module")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, tmp_path):
    path = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 300), "Some words to select on this page.", fontsize=12)
    page.add_rect_annot(fitz.Rect(400, 600, 500, 700)).set_info(title="Someone else")
    doc.save(path)
    doc.close()
    w = MainWindow(app, path, app.settings)
    w.show()
    QTest.qWaitForWindowExposed(w)
    w.activateWindow()
    QApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _point(win, x: float, y: float) -> QPoint:
    return win.view.mapFromScene(win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center())


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _press(win, pos: QPoint) -> None:
    """A real press: it goes through the application, so the editor loses focus first."""
    QTest.mousePress(win.view.viewport(), _LEFT, _NONE, pos)
    QApplication.processEvents()        # the editor's deferred commit runs here, as it would live


def _drag_to(win, pos: QPoint) -> None:
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(pos),
                        QPointF(win.view.viewport().mapToGlobal(pos)), Qt.MouseButton.NoButton,
                        _LEFT, _NONE)
    QApplication.sendEvent(win.view.viewport(), event)
    QApplication.processEvents()


def _release(win, pos: QPoint) -> None:
    QTest.mouseRelease(win.view.viewport(), _LEFT, _NONE, pos)
    QApplication.processEvents()


def _add(win, *marks) -> tuple:
    for mark in marks:
        win.vdoc.add_annotation(0, mark)
    win.view.reload()
    return win.vdoc.page_annotations(0)


def _edit(win, box, text: str) -> None:
    """Select ``box``, so its handle shows, and open its editor on ``text``."""
    overlay = win.view.annotations
    overlay.select_object(0, box)
    x0, y0, _x1, y1 = box.rect
    assert overlay.edit_textbox_at(_scene(win, x0 + 5, (y0 + y1) / 2))
    QApplication.processEvents()
    assert overlay._editor.hasFocus(), "the press has to take focus from the editor to test this"
    overlay._editor.setPlainText(text)


def _steps(win) -> list[str]:
    return [win.undo_stack.text(i) for i in range(win.undo_stack.count())]


# ---- a press with the editor open ------------------------------------------------------------


def test_dragging_the_handle_after_an_edit_resizes_the_box(win):
    """The owner's report: the text is kept, the drag resizes, and each is one step of Undo."""
    (box,) = _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "a much longer first version"))
    _edit(win, box, "short")
    start = _point(win, 300, 110)
    end = _point(win, 350, 110)
    _press(win, start)
    assert win.view.annotations.resizing
    _drag_to(win, end)
    _release(win, end)
    (after,) = win.vdoc.page_annotations(0)
    assert after.text == "short"
    assert after.rect[2] == pytest.approx(350, abs=1.5)
    assert _steps(win) == ["Edit text box", "Resize text box"]


def test_the_handle_still_works_when_the_edit_leaves_the_box_the_same_size(win):
    (box,) = _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "fixed width", auto_width=False))
    _edit(win, box, "still fixed")
    end = _point(win, 340, 110)
    _press(win, _point(win, 300, 110))
    _drag_to(win, end)
    _release(win, end)
    (after,) = win.vdoc.page_annotations(0)
    assert (after.text, after.rect[2]) == ("still fixed", pytest.approx(340, abs=1.5))


def test_a_press_on_another_box_commits_the_edit_and_moves_that_box(win):
    first, second = _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "first"),
                         TextBox((100.0, 200.0, 300.0, 220.0), "second"))
    _edit(win, first, "edited")
    start = _point(win, 150, 210)
    end = start + QPoint(0, 40)
    _press(win, start)
    _drag_to(win, end)
    _release(win, end)
    edited, moved = win.vdoc.page_annotations(0)
    assert edited.text == "edited"
    assert moved.text == "second" and moved.rect[1] > 200
    assert _steps(win) == ["Edit text box", "Move text box"]


def test_the_press_keeps_the_other_marks_selected(win):
    """A new box is being typed while a shape is selected: the shape's handle is still there."""
    (shape,) = _add(win, Shape("rect", (300.0, 300.0, 400.0, 400.0)))
    overlay = win.view.annotations
    overlay.select_object(0, shape)
    assert overlay.place_textbox(_scene(win, 100, 100))
    QApplication.processEvents()
    overlay._editor.setPlainText("new")
    start, end = _point(win, 400, 400), _point(win, 440, 440)
    _press(win, start)
    assert overlay.resizing
    _drag_to(win, end)
    _release(win, end)
    drawn = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape)]
    assert drawn[0].rect[2] == pytest.approx(440, abs=1.5)


def test_emptying_the_box_and_pressing_its_handle_removes_it(win):
    (box,) = _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "going"))
    _edit(win, box, "   ")
    pos = _point(win, 300, 110)
    _press(win, pos)
    _release(win, pos)
    assert win.vdoc.page_annotations(0) == ()
    assert not win.view.annotations.resizing


def test_a_press_with_nothing_changed_starts_the_drag_as_before(win):
    (box,) = _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "same"))
    _edit(win, box, "same")
    end = _point(win, 340, 110)
    _press(win, _point(win, 300, 110))
    _drag_to(win, end)
    _release(win, end)
    assert _steps(win) == ["Resize text box"]


# ---- a rebuild during a drag -----------------------------------------------------------------


def _zoom(win) -> None:
    win.view.set_zoom(win.view.zoom * 1.25)


def _reload(win) -> None:
    win.view.reload()


def _select_box(win) -> None:
    win.view.annotations.select_object(0, win.vdoc.page_annotations(0)[0])


def _objects_mode(win) -> None:
    win.view.set_mode(InteractionMode.OBJECT)


def _arm(tool):
    return lambda win: win.view.arm(tool)


# name: (what to do first, where the press lands)
_DRAGS = {
    "move": (None, (150, 110)),
    "foreign move": (None, (450, 650)),
    "resize": (_select_box, (300, 110)),
    "marquee": (_objects_mode, (400, 500)),
    "shape": (_arm(ArmedTool.RECT), (120, 400)),
    "pen": (_arm(ArmedTool.PEN), (120, 400)),
    "redaction": (_arm(ArmedTool.REDACT_REGION), (120, 400)),
    "crop": (_arm(ArmedTool.CROP), (120, 400)),
}


def _in_a_drag(win) -> bool:
    overlay = win.view.annotations
    return (overlay.moving or overlay.moving_foreign or overlay.resizing or overlay.marqueeing
            or overlay.drawing or overlay.redacting or win.view.cropping)


@pytest.mark.parametrize("rebuild", [_zoom, _reload], ids=["zoom", "reload"])
@pytest.mark.parametrize("drag", _DRAGS)
def test_a_rebuild_ends_the_drag_and_applies_nothing(win, drag, rebuild):
    before = _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "hello"))
    setup, start = _DRAGS[drag]
    if setup is not None:
        setup(win)
    pos = _point(win, *start)
    _press(win, pos)
    assert _in_a_drag(win), "the press did not start the drag this test is about"
    rebuild(win)
    assert not _in_a_drag(win)
    end = pos + QPoint(40, 30)
    _drag_to(win, end)
    _release(win, end)
    assert not _in_a_drag(win)
    assert win.vdoc.page_annotations(0) == before
    assert win.undo_stack.count() == 0


@pytest.mark.parametrize("rebuild", [_zoom, _reload], ids=["zoom", "reload"])
def test_selecting_text_through_a_rebuild_raises_nothing(win, rebuild):
    """A text selection holds no item across a drag, so it needs nothing ended. Pinned because it
    is the one drag that is not in ``cancel_drags``."""
    pos = _point(win, 80, 296)
    _press(win, pos)
    assert win.view.selection.active
    rebuild(win)
    end = pos + QPoint(60, 0)
    _drag_to(win, end)
    _release(win, end)


def test_the_next_drag_works_after_one_was_ended(win):
    (box,) = _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "hello"))
    pos = _point(win, 150, 110)
    _press(win, pos)
    _reload(win)
    _release(win, pos)
    end = pos + QPoint(0, 40)
    _press(win, pos)
    _drag_to(win, end)
    _release(win, end)
    (moved,) = win.vdoc.page_annotations(0)
    assert moved.rect[1] > box.rect[1]
    assert _steps(win) == ["Move text box"]


def test_a_finished_drag_is_not_ended_by_its_own_edit(win):
    """A drag's own commit rebuilds the scene too. It has let go of its items by then, or a move
    would cancel itself."""
    (box,) = _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "hello"))
    start = _point(win, 150, 110)
    end = start + QPoint(0, 40)
    _press(win, start)
    _drag_to(win, end)
    _release(win, end)
    assert win.vdoc.page_annotations(0)[0].rect[1] > box.rect[1]


@pytest.mark.parametrize("drag, held", [
    ("move", lambda overlay: overlay._move_items[0][2]),
    ("marquee", lambda overlay: overlay._marquee_item),
    ("redaction", lambda overlay: overlay._redact_band),
])
def test_cancelling_takes_the_item_out_of_the_scene(win, drag, held):
    """A rebuild would destroy these anyway, so only a direct call can show they are removed."""
    _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "hello"))
    setup, start = _DRAGS[drag]
    if setup is not None:
        setup(win)
    _press(win, _point(win, *start))
    item = held(win.view.annotations)
    assert item.scene() is win.view.scene()
    win.view.annotations.cancel_drags()
    assert item.scene() is None
