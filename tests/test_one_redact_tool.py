"""One Redact tool (PLAN.md §GUI feature roadmap → R6, M72). Offscreen GUI.

The markup bar's two Redact slots become **one armed tool** with Preview-style gesture detect:
the press point's text hit decides — a drag starting on a word runs the text-flow redaction, a
drag starting in margin/image space rubber-bands a block. The combined slot resolves *at press*
to the concrete tool (REDACT_TEXT / REDACT_REGION), so the armed-selection tint, the release
path and the one-shot disarm are exactly the explicit tools'. The Tools menu keeps both explicit
verbs (menus are the complete catalog) and Ctrl+Shift+R still arms Redact Text.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent

from app import PdfApp
from model.page_edits import Redaction
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


def _press(view, view_pt):
    view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, view_pt, view_pt,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def _move(view, view_pt):
    view.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, view_pt, view_pt,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def _release(view, view_pt):
    view.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, view_pt, view_pt,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _first_word(win):
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    return page.get_text("words")[0]


def _word_span_in_view(win):
    """(start, end) view points across page 0's first word, mid-height."""
    rect = win.view.scene_rect_for_box(0, _first_word(win)[:4])
    p0 = QPointF(win.view.mapFromScene(QPointF(rect.left() + 1, rect.center().y())))
    p1 = QPointF(win.view.mapFromScene(QPointF(rect.right() - 1, rect.center().y())))
    return p0, p1


def _margin_span_in_view(win):
    """(start, end) view points over an empty region of page 0 (right of the text column)."""
    r = win.view.scene_rect_for_box(0, (300, 400, 400, 500))
    return (QPointF(win.view.mapFromScene(r.topLeft())),
            QPointF(win.view.mapFromScene(r.bottomRight())))


def _redactions(win):
    return [a for a in win.vdoc.page_annotations(0) if isinstance(a, Redaction)]


# ---- gesture detection -------------------------------------------------------


def test_press_on_text_runs_the_text_flow_redaction(win):
    win.view.arm(ArmedTool.REDACT)
    p0, p1 = _word_span_in_view(win)
    _press(win.view, p0)
    assert win.view.armed is ArmedTool.REDACT_TEXT  # resolved by the press point's word hit
    _move(win.view, p1)
    _release(win.view, p1)
    assert win.view.armed is None                   # one-shot: applied + disarmed
    reds = _redactions(win)
    assert reds and len(reds[0].rects) >= 1         # per-line text bars


def test_press_on_margin_rubber_bands_a_block(win):
    win.view.arm(ArmedTool.REDACT)
    p0, p1 = _margin_span_in_view(win)
    _press(win.view, p0)
    assert win.view.armed is ArmedTool.REDACT_REGION  # no word under the press → block
    assert win.view.annotations.redacting is True     # the band began at the press
    _move(win.view, p1)
    _release(win.view, p1)
    assert win.view.armed is None
    reds = _redactions(win)
    assert reds and len(reds[0].rects) == 1           # a region drag → one rect


def test_press_off_page_stays_armed_and_unresolved(win):
    win.view.arm(ArmedTool.REDACT)
    off = QPointF(win.view.mapFromScene(QPointF(5, 2)))  # the gap above page 0
    _press(win.view, off)
    assert win.view.armed is ArmedTool.REDACT  # a mis-click neither wastes nor locks the arm
    _release(win.view, off)
    assert win.view.armed is ArmedTool.REDACT


def test_no_commit_click_on_text_restores_the_combined_arm(win):
    """A click on a word (no drag) selects nothing — the arm must return to REDACT, or the next
    press on a margin would drag-select text instead of rubber-banding."""
    win.view.arm(ArmedTool.REDACT)
    p0, _p1 = _word_span_in_view(win)
    _press(win.view, p0)
    assert win.view.armed is ArmedTool.REDACT_TEXT
    _release(win.view, p0)  # zero-length drag → nothing selected → nothing committed
    assert win.view.armed is ArmedTool.REDACT
    assert win.vdoc.page_annotations(0) == ()


def test_rotated_view_resolves_to_block(win):
    """Text selection is disabled in a rotated view, so the text gesture can't run there — a
    press over what was text must fall back to the block gesture, not dead-end."""
    win.view.rotate_view(90)
    win.view.arm(ArmedTool.REDACT)
    rect = win.view.scene_rect_for_box(0, _first_word(win)[:4])
    _press(win.view, QPointF(win.view.mapFromScene(rect.center())))
    assert win.view.armed is ArmedTool.REDACT_REGION
    win.view.disarm()
    win.view.rotate_view(-90)


# ---- the toolbar slot --------------------------------------------------------


def test_markup_bar_has_one_redact_slot(win):
    texts = [a.text() for a in win.markup_bar.actions() if a.text()]
    assert texts.count("Redact") == 1
    assert "Redact Text" not in texts and "Redact Block" not in texts


def test_slot_arms_the_combined_tool_and_lights(win):
    win._arm_redact()
    assert win.view.armed is ArmedTool.REDACT
    assert win._a_redact.isChecked()


def test_slot_lights_for_a_menu_armed_explicit_verb(win):
    """Tools ▸ Redact Text arms the explicit tool — the bar's one Redact slot is where that
    armed state must be visible."""
    win._arm_tool(ArmedTool.REDACT_TEXT)
    assert win._a_redact.isChecked()
    win.view.disarm()
    assert not win._a_redact.isChecked()


def test_clicking_the_lit_slot_disarms_whichever_redact_is_armed(win):
    win._arm_redact()
    win._arm_redact()
    assert win.view.armed is None       # toggle off the combined tool
    win._arm_tool(ArmedTool.REDACT_REGION)
    win._arm_redact()
    assert win.view.armed is None       # and a menu-armed explicit one


def test_slot_applies_to_a_live_selection_immediately(win):
    """Select-then-click applies at once (the M46 owner call), exactly as Redact Text does."""
    word = _first_word(win)
    win.view.selection.select_word_at(win.view.scene_rect_for_box(0, word[:4]).center())
    assert win.view.selection.selected_words()
    win._arm_redact()
    assert win.view.armed is None                    # applied, not armed-and-waiting
    assert _redactions(win)


# ---- the menu catalog is unchanged -------------------------------------------


def test_menu_keeps_both_explicit_verbs_and_the_shortcut(win):
    for bar_action in win.menuBar().actions():
        if bar_action.text() == "&Tools" and bar_action.menu() is not None:
            texts = [a.text() for a in bar_action.menu().actions() if a.text()]
            assert "Redact Text" in texts and "Redact Block" in texts
            assert "Redact" not in texts  # the combined slot is toolbar sugar, not a third verb
            by_text = {a.text(): a for a in bar_action.menu().actions()}
            assert by_text["Redact Text"].shortcut() == QKeySequence("Ctrl+Shift+R")
            return
    raise AssertionError("no Tools menu found")


def test_explicit_menu_verbs_still_arm_their_own_tools(win):
    win._arm_tool(ArmedTool.REDACT_TEXT)
    assert win.view.armed is ArmedTool.REDACT_TEXT
    win._arm_tool(ArmedTool.REDACT_REGION)
    assert win.view.armed is ArmedTool.REDACT_REGION
    win.view.disarm()


def test_explicit_text_arm_is_not_restored_to_combined_on_a_no_commit_click(win):
    """The explicit Redact Text keeps its pre-M72 contract: a stray click leaves *it* armed —
    only the combined slot's resolution is rolled back."""
    win._arm_tool(ArmedTool.REDACT_TEXT)
    p0, _p1 = _word_span_in_view(win)
    _press(win.view, p0)
    _release(win.view, p0)
    assert win.view.armed is ArmedTool.REDACT_TEXT
    win.view.disarm()
