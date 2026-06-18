"""Redaction viewer interaction (PLAN.md, M21). Offscreen GUI.

The rubber-band redaction tool, its WYSIWYG overlay, and the mouse routing — driven through a real
MainWindow on the page-edit-layer model.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

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
    w.undo_stack.setClean()  # avoid the dirty-close prompt blocking teardown
    w.close()


_BOX = (72, 66, 160, 86)  # a region over page-0 text, in unrotated page points


def test_redact_drag_creates_redaction(win):
    win.view.arm(ArmedTool.REDACT)
    rect = win.view.scene_rect_for_box(0, _BOX)
    ov = win.view.annotations
    assert ov.begin_redaction(rect.topLeft()) is True
    assert ov.redacting is True
    ov.update_redaction(rect.bottomRight())
    ov.finish_redaction()
    assert ov.redacting is False
    reds = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Redaction)]
    assert reds and len(reds[0].rects) == 1  # a region drag → one rect


def test_redact_begin_off_page_returns_false(win):
    win.view.arm(ArmedTool.REDACT)
    assert win.view.annotations.begin_redaction(QPointF(5, 2)) is False  # the top gap


def test_tiny_drag_adds_nothing(win):
    win.view.arm(ArmedTool.REDACT)
    pt = win.view.scene_rect_for_box(0, _BOX).topLeft()
    ov = win.view.annotations
    ov.begin_redaction(pt)
    ov.update_redaction(pt)        # never moved → sub-threshold
    ov.finish_redaction()
    assert win.vdoc.page_annotations(0) == ()


def test_armed_redact_press_routes_and_disarms_on_release(win):
    win.view.arm(ArmedTool.REDACT)
    rect = win.view.scene_rect_for_box(0, _BOX)
    tl = QPointF(win.view.mapFromScene(rect.topLeft()))
    br = QPointF(win.view.mapFromScene(rect.bottomRight()))
    press = QMouseEvent(QEvent.Type.MouseButtonPress, tl, tl, Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.view.mousePressEvent(press)
    assert win.view.annotations.redacting is True  # the press started a rubber-band
    move = QMouseEvent(QEvent.Type.MouseMove, br, br, Qt.MouseButton.NoButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.view.mouseMoveEvent(move)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, br, br, Qt.MouseButton.LeftButton,
                          Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    win.view.mouseReleaseEvent(release)
    assert win.view.armed is None  # one-shot: reverted to Select after the drag committed
    assert any(isinstance(a, Redaction) for a in win.vdoc.page_annotations(0))


def test_overlay_paints_redaction(win):
    win.vdoc.add_annotation(0, Redaction((_BOX,)))
    win.view.annotations.repaint()
    assert len(win.view.annotations._items) >= 1


def test_remove_redaction_via_hit_test(win):
    r = Redaction((_BOX,))
    win.vdoc.add_annotation(0, r)
    win.view.annotations.repaint()
    hit = win.view.annotations.annotation_at(win.view.scene_rect_for_box(0, _BOX).center())
    assert hit is not None and hit[1] is r
    win.view.annotations.remove(hit[0], hit[1])      # what the context menu calls
    assert r not in win.vdoc.page_annotations(0)     # removed (undoable)


# ---- text-flow redaction (select then Redact Selection) -------------------------


def _first_word(win):
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    return page.get_text("words")[0]


def test_redact_selection_creates_a_redaction(win):
    word = _first_word(win)
    win.view.selection.select_word_at(win.view.scene_rect_for_box(0, word[:4]).center())
    assert win.view.selection.selected_words()
    win._redact_selection()
    reds = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Redaction)]
    assert reds and len(reds[0].rects) >= 1            # at least one per-line bar
    assert win.view.selection.selected_words() == []   # selection consumed


def test_redact_selection_with_no_selection_does_nothing(win):
    win.view.selection.clear()
    win._redact_selection()
    assert win.vdoc.page_annotations(0) == ()


def test_redacted_save_is_point_of_no_return(win, monkeypatch):
    """Saving with a redaction confirms, removes the content, then clears undo + reloads from the
    clean file — so the secret is gone from disk and can't be resurrected by undo."""
    import pymupdf as fitz
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Save
    )
    word = _first_word(win)
    token = word[4]
    win.vdoc.add_annotation(0, Redaction((tuple(word[:4]),)))
    assert win.save() is True
    assert win.undo_stack.count() == 0          # undo history cleared (nothing to revert)
    assert win.vdoc.has_redactions() is False   # descriptor gone from the model
    with fitz.open(win.path) as doc:
        assert token not in doc[0].get_text()   # provably removed from the saved file


def test_redacted_save_aborts_on_cancel(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    word = _first_word(win)
    win.vdoc.add_annotation(0, Redaction((tuple(word[:4]),)))
    assert win.save() is False                  # cancelled → not written
    assert win.vdoc.has_redactions() is True    # redaction still pending (nothing committed)
