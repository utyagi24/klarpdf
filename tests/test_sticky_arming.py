"""Sticky markup arming (PLAN.md §GUI feature roadmap → R6, M73). Offscreen GUI.

Highlight / Underline / Strike Out / Pen stay **armed across gestures** (Preview's behaviour):
mark passage after passage on one arm. Three exits — click the lit button again · Esc · arm any
other tool. Placement and destructive tools (Text Box, shapes/lines, Stamp/Signature, Redact,
Crop) stay **one-shot**: repeat use is rare there, and a stuck destructive mode is a trap.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

from app import PdfApp
from model.page_edits import Highlight, InkStroke, Line, Redaction, Strikeout, Underline
from store.settings import Settings
from viewer.tools import ArmedTool


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
    w.undo_stack.setClean()
    w.close()


def _mouse_drag(view, p0: QPointF, p1: QPointF) -> None:
    view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, p0, p0,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    view.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, p1, p1,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    view.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, p1, p1,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _drag_over_word(win, page_index: int) -> None:
    """Press-drag across ``page_index``'s first word through the real mouse routing."""
    ref = win.vdoc.ordered[page_index]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    rect = win.view.scene_rect_for_box(page_index, page.get_text("words")[0][:4])
    p0 = QPointF(win.view.mapFromScene(QPointF(rect.left() + 1, rect.center().y())))
    p1 = QPointF(win.view.mapFromScene(QPointF(rect.right() - 1, rect.center().y())))
    _mouse_drag(win.view, p0, p1)


def _drag_on_margin(win, box: tuple) -> None:
    """Press-drag over an empty page-0 region (content coords) through the mouse routing."""
    r = win.view.scene_rect_for_box(0, box)
    _mouse_drag(win.view,
                QPointF(win.view.mapFromScene(r.topLeft())),
                QPointF(win.view.mapFromScene(r.bottomRight())))


def _marks(win, cls, page_index: int = 0) -> list:
    return [a for a in win.vdoc.page_annotations(page_index) if isinstance(a, cls)]


# ---- the sticky quartet ------------------------------------------------------


def test_three_highlights_on_one_arm(win):
    """The M73 acceptance gesture: mark passage after passage without re-arming."""
    win.view.arm(ArmedTool.HIGHLIGHT)
    for page_index in (0, 1, 2):
        _drag_over_word(win, page_index)
        assert win.view.armed is ArmedTool.HIGHLIGHT  # still armed after each passage
    for page_index in (0, 1, 2):
        assert len(_marks(win, Highlight, page_index)) == 1
    win.view.disarm()


@pytest.mark.parametrize("tool,cls", [(ArmedTool.UNDERLINE, Underline),
                                      (ArmedTool.STRIKEOUT, Strikeout)])
def test_underline_and_strikeout_are_sticky_too(win, tool, cls):
    win.view.arm(tool)
    _drag_over_word(win, 0)
    assert win.view.armed is tool
    _drag_over_word(win, 1)
    assert len(_marks(win, cls, 0)) == 1 and len(_marks(win, cls, 1)) == 1
    win.view.disarm()


def test_pen_draws_stroke_after_stroke_on_one_arm(win):
    win.view.arm(ArmedTool.PEN)
    _drag_on_margin(win, (300, 400, 360, 460))
    assert win.view.armed is ArmedTool.PEN            # still armed after the stroke
    _drag_on_margin(win, (300, 500, 360, 560))
    assert win.view.armed is ArmedTool.PEN
    assert len(_marks(win, InkStroke)) == 2           # two strokes, one arm
    win.view.disarm()


def test_armed_state_stays_visible_on_the_button(win):
    """The M73 'always visible' clause: the lit button survives each gesture."""
    win._arm_tool(ArmedTool.HIGHLIGHT)
    _drag_over_word(win, 0)
    assert win._armed_actions[ArmedTool.HIGHLIGHT].isChecked()
    win.view.disarm()
    assert not win._armed_actions[ArmedTool.HIGHLIGHT].isChecked()


# ---- the three exits ---------------------------------------------------------


def test_exit_one_clicking_the_lit_button_again(win):
    win._arm_tool(ArmedTool.HIGHLIGHT)
    _drag_over_word(win, 0)
    win._arm_tool(ArmedTool.HIGHLIGHT)  # the lit button, clicked again
    assert win.view.armed is None


def test_exit_two_escape(win):
    win.view.arm(ArmedTool.PEN)
    _drag_on_margin(win, (300, 400, 360, 460))
    win.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                     Qt.KeyboardModifier.NoModifier))
    assert win.view.armed is None


def test_exit_three_arming_any_other_tool(win):
    win._arm_tool(ArmedTool.HIGHLIGHT)
    _drag_over_word(win, 0)
    win._arm_tool(ArmedTool.UNDERLINE)  # arming another tool from the markup bar
    assert win.view.armed is ArmedTool.UNDERLINE
    win.view.disarm()


def test_switching_mode_also_exits(win):
    from viewer.tools import InteractionMode

    win.view.arm(ArmedTool.PEN)
    win.view.set_mode(InteractionMode.GRAB)
    assert win.view.armed is None
    win.view.set_mode(InteractionMode.SELECT)


# ---- one-shot tools unchanged ------------------------------------------------


def test_shape_draw_stays_one_shot(win):
    win.view.arm(ArmedTool.LINE)
    _drag_on_margin(win, (300, 400, 380, 460))
    assert win.view.armed is None                     # reverted to Select after one gesture
    assert len(_marks(win, Line)) == 1


def test_block_redact_stays_one_shot(win):
    win.view.arm(ArmedTool.REDACT_REGION)
    _drag_on_margin(win, (300, 400, 380, 460))
    assert win.view.armed is None
    assert len(_marks(win, Redaction)) == 1


def test_text_redact_stays_one_shot(win):
    """Destructive stays one-shot even though it shares the drag-over-text path with the
    sticky trio — a stuck redact mode is exactly the trap M73 refuses."""
    win.view.arm(ArmedTool.REDACT_TEXT)
    _drag_over_word(win, 0)
    assert win.view.armed is None
    assert len(_marks(win, Redaction)) == 1


def test_combined_redact_text_gesture_stays_one_shot(win):
    """The M72 combined slot resolved onto text: applies and disarms like the explicit verb."""
    win.view.arm(ArmedTool.REDACT)
    _drag_over_word(win, 0)
    assert win.view.armed is None
    assert len(_marks(win, Redaction)) == 1
