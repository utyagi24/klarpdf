"""Grab / Select viewer interaction mode (PLAN.md, M18). Offscreen GUI.

SELECT (default) keeps text selection + form fill; GRAB switches the view to a hand-pan tool and
suppresses selection/form routing.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QGraphicsView

from app import PdfApp
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.pdf_view import PdfView
from viewer.text_selection import TextSelection
from viewer.tools import InteractionMode


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def view(qapp, a_pdf):
    v = PdfView(VirtualDocument.from_path(a_pdf))
    v.selection = TextSelection(v)  # MainWindow normally wires this
    return v


def test_default_mode_is_select(view):
    assert view.mode == InteractionMode.SELECT
    assert view.dragMode() == QGraphicsView.DragMode.NoDrag


def test_grab_mode_enables_hand_drag(view):
    view.set_mode(InteractionMode.GRAB)
    assert view.mode == InteractionMode.GRAB
    assert view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
    view.set_mode(InteractionMode.SELECT)
    assert view.dragMode() == QGraphicsView.DragMode.NoDrag


def test_grab_mode_suppresses_text_selection(view):
    view.set_mode(InteractionMode.GRAB)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(40, 40), QPointF(40, 40),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    view.mousePressEvent(press)
    assert view.selection.active is False  # no selection started while grabbing


def test_armed_text_tool_paints_the_selection_in_its_target_colour(view):
    """An armed Highlight / Redact-text paints the live selection in the tool's final colour, so
    there's no blue→colour flip when it lands on release; a plain selection stays selection-blue.
    Highlight follows the sticky colour the window keeps on the view (M76.2), falling back to the
    default yellow when none is wired (this raw-view fixture)."""
    from PySide6.QtGui import QColor

    from viewer.text_selection import _HIGHLIGHT_ARMED, _REDACT_ARMED, _SELECTION
    from viewer.tools import ArmedTool

    sel = view.selection

    def colour_after(arm_action) -> object:
        arm_action()
        sel._anchor = sel._cursor = (0, 0)  # select the first word on page 0
        sel.repaint()
        assert sel._items, "expected a painted selection rect"
        return sel._items[0].brush().color()

    assert colour_after(lambda: view.arm(ArmedTool.HIGHLIGHT)) == _HIGHLIGHT_ARMED  # fallback
    assert colour_after(lambda: view.arm(ArmedTool.REDACT_TEXT)) == _REDACT_ARMED
    assert colour_after(lambda: view.disarm()) == _SELECTION

    # With a sticky colour wired, an armed Highlight previews *that* colour from the first drag —
    # the fix for the owner-reported yellow flash that only "converted" on release.
    view.highlight_preview_color = (0.55, 0.80, 1.00)  # blue
    expected = QColor.fromRgbF(0.55, 0.80, 1.00)
    expected.setAlpha(120)
    assert colour_after(lambda: view.arm(ArmedTool.HIGHLIGHT)) == expected


def test_mode_toggle_in_toolbar_is_exclusive(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(a_pdf)
    bar = win.markup_bar  # the mode trio lives on the markup bar since M71
    by_text = {a.text(): a for a in bar.actions() if a.text()}
    select, grab = by_text["Select"], by_text["Grab"]
    assert select.isCheckable() and grab.isCheckable()
    assert select.isChecked() and not grab.isChecked()  # Select is the default

    grab.trigger()
    assert win.view.mode == InteractionMode.GRAB
    assert grab.isChecked() and not select.isChecked()  # exclusive

    select.trigger()
    assert win.view.mode == InteractionMode.SELECT
    win.close()
