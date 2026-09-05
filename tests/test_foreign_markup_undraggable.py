"""Foreign text markup is not a drag target — and it stops stealing the press (M82).

Owner-reported on a PDF annotated in Edge: *"our app lets me grab the text highlight added by Edge
and drag it around like normal drawing objects and place it arbitrarily. We should **not** be able
to drag the highlights."*

It is an asymmetry, not a general drag problem. Our **own** Highlight / Underline / Strikeout are
deliberately undraggable — a text markup's quads *describe text*, so moving one marks nothing — but
the foreign path hit-tested every annotation by its rect with no type filter, so M67's move applied
to text markup too. Edge's highlights got a capability our identical marks are denied, and because
a ``ForeignMove`` is applied at materialise, the displacement became **permanent in the file**.

The more serious half is what it did to reading. ``begin_foreign_move`` runs in the SELECT-mode
press path, ahead of text selection:

    selected object → form field → our own marks → foreign annotation → text selection

so on any reviewed document, dragging across a highlighted passage **dragged the highlight instead
of selecting the text** — the reader could not select or copy the very words a reviewer marked for
their attention. ``covers_page`` exists because a grabbable full-page watermark caused exactly this
symptom for our own marks; the lesson was fixed locally and never generalised.

Delete (M66) and adopt (M68) are untouched: nothing about *removing* or *editing* a mark depends on
it being movable, and adoption of a commented highlight is the M81.3 path.

Headless + offscreen GUI.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from app import PdfApp
from main_window import MainWindow
from klarpdf.model.foreign_annots import (
    ForeignMove,
    is_free_placed,
    read_foreign_annotations,
)
from store.settings import Settings

# The passage lives on one text line at y≈292–306; the sticky note sits well clear of it.
TEXT_BOX = (60.0, 292.0, 200.0, 306.0)
NOTE_BOX = (300.0, 100.0, 320.0, 120.0)


def _add(page, kind, rect, contents="", author="Alice"):
    add = {
        "highlight": lambda: page.add_highlight_annot(fitz.Rect(rect)),
        "underline": lambda: page.add_underline_annot(fitz.Rect(rect)),
        "strikeout": lambda: page.add_strikeout_annot(fitz.Rect(rect)),
        "squiggly": lambda: page.add_squiggly_annot(fitz.Rect(rect)),
        "text": lambda: page.add_text_annot(fitz.Point(rect[0], rect[1]), contents or "note"),
        "square": lambda: page.add_rect_annot(fitz.Rect(rect)),
        "freetext": lambda: page.add_freetext_annot(fitz.Rect(rect), contents or "callout"),
    }[kind]
    annot = add()
    annot.set_info(title=author, content=contents)
    annot.update()
    return annot


@pytest.fixture
def edge_pdf(tmp_path) -> str:
    """What a document reviewed in Edge or Acrobat looks like: a highlight over a sentence, plus a
    free-placed sticky note that must keep on dragging."""
    path = str(tmp_path / "edge.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 302), "REVIEWERS MARKED THIS SENTENCE", fontsize=12)
    _add(page, "highlight", TEXT_BOX, "look at this")
    _add(page, "text", NOTE_BOX, "a sticky note")
    doc.save(path)
    doc.close()
    return path


# ---- the model-side rule --------------------------------------------------------


@pytest.mark.parametrize("kind", ["highlight", "underline", "strikeout", "squiggly"])
def test_text_markup_is_not_free_placed(tmp_path, kind):
    """Squiggly is included even though it is not in ``MODELED_KINDS`` — it cannot be *adopted*,
    but it is text markup by the same definition, so it must not be draggable either."""
    doc = fitz.open()
    page = doc.new_page()
    try:
        page.insert_text((60, 302), "mark me", fontsize=12)
        _add(page, kind, TEXT_BOX)
        (mark,) = read_foreign_annotations(page)
        assert is_free_placed(mark) is False
    finally:
        doc.close()


@pytest.mark.parametrize("kind", ["text", "square", "freetext"])
def test_free_placed_marks_stay_free_placed(kind):
    """Sticky notes, stamps and drawings are exactly what M67 was for — the gate must not catch
    them, or the milestone is reverted rather than corrected."""
    doc = fitz.open()
    page = doc.new_page()
    try:
        _add(page, kind, (100.0, 100.0, 200.0, 160.0), "x")
        (mark,) = read_foreign_annotations(page)
        assert is_free_placed(mark) is True
    finally:
        doc.close()


# ---- the viewer (offscreen GUI) -------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, edge_pdf):
    w = MainWindow(app, edge_pdf, app.settings)
    w.show()
    app.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _on_the_highlight(win):
    """A scene point inside the highlighted passage."""
    return _scene(win, (TEXT_BOX[0] + TEXT_BOX[2]) / 2, (TEXT_BOX[1] + TEXT_BOX[3]) / 2)


def _on_the_note(win):
    return _scene(win, (NOTE_BOX[0] + NOTE_BOX[2]) / 2, (NOTE_BOX[1] + NOTE_BOX[3]) / 2)


def _moves(win):
    return [a for a in win.vdoc.page_annotations(0) if isinstance(a, ForeignMove)]


def _words(win):
    """The selected words' text. ``selected_words`` yields ``(page_index, order, word_tuple)``."""
    return [w[2][4] for w in win.view.selection.selected_words()]


# ---- M82.1 the drag gate --------------------------------------------------------


def test_a_foreign_highlight_cannot_be_grabbed(win):
    """The press must fall straight through it, leaving the chain free to reach text selection."""
    assert win.view.annotations.begin_foreign_move(_on_the_highlight(win)) is False
    assert win.view.annotations.moving_foreign is False


def test_a_foreign_sticky_note_still_drags(win):
    """M67's actual subject, unchanged."""
    overlay = win.view.annotations
    assert overlay.begin_foreign_move(_on_the_note(win)) is True
    overlay.update_move(_scene(win, NOTE_BOX[0] + 50, NOTE_BOX[1] + 40))
    moved = overlay.finish_foreign_move()
    assert moved is not None
    win._move_foreign_annotation(*moved)
    assert len(_moves(win)) == 1


def test_a_highlight_over_a_sticky_note_does_not_shield_it(win, tmp_path):
    """The filter runs *inside* the hit-test loop, not on its result: a note lying under a
    highlight is still the mark the press meant. Filtering afterwards would have made the topmost
    (undraggable) mark block the one below it."""
    path = str(tmp_path / "stacked.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 302), "OVERLAPPING MARKS HERE", fontsize=12)
    _add(page, "text", (100.0, 290.0, 120.0, 310.0), "underneath")
    _add(page, "highlight", (60.0, 285.0, 200.0, 315.0), "on top")   # added later → topmost
    doc.save(path)
    doc.close()

    w = MainWindow(win.app if hasattr(win, "app") else PdfApp.instance(), path,
                   PdfApp.instance().settings)
    try:
        hit = w.view.annotations.foreign_annotation_at(
            w.view.scene_rect_for_box(0, (108.0, 298.0, 112.0, 302.0)).center(),
            free_placed_only=True,
        )
        assert hit is not None
        assert hit[1].kind_name == "Text"          # the note, not the highlight above it
    finally:
        w.undo_stack.setClean()
        w.close()


def test_the_plain_hit_test_still_finds_text_markup(win):
    """Unfiltered by default — delete (M66) and adopt (M68) must keep seeing every type, which is
    what the context menu and double-click paths rely on."""
    hit = win.view.annotations.foreign_annotation_at(_on_the_highlight(win))
    assert hit is not None
    assert hit[1].kind_name == "Highlight"


def test_deleting_a_foreign_highlight_still_works(win):
    """The gate is about dragging only. Removing a reviewer's mark is M66 and stays available for
    every type."""
    from klarpdf.model.foreign_annots import ForeignDeletion

    _page_index, mark = win.view.annotations.foreign_annotation_at(_on_the_highlight(win))
    win._delete_foreign_annotation(0, mark)
    assert any(isinstance(a, ForeignDeletion) for a in win.vdoc.page_annotations(0))


# ---- M82.2 the regression: the press reaches text selection ---------------------


def _press_drag(win, p_start, p_end):
    """A real press-drag-release through ``PdfView``'s own event handlers — the ordering *is* the
    bug, so driving the overlay directly would not reproduce it."""
    v0 = QPointF(win.view.mapFromScene(p_start))
    v1 = QPointF(win.view.mapFromScene(p_end))
    win.view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, v0, v0,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    win.view.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, v1, v1,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    win.view.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, v1, v1,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _passage_ends(win):
    rect = win.view.scene_rect_for_box(0, TEXT_BOX)
    return (QPointF(rect.left() + 2, rect.center().y()),
            QPointF(rect.right() - 2, rect.center().y()))


def test_dragging_across_a_foreign_highlight_selects_the_text(win):
    """The owner-reported symptom, at the level it was reported: the worst possible passage to lose
    selection on is the one a reviewer marked for your attention."""
    start, end = _passage_ends(win)
    _press_drag(win, start, end)
    assert win.view.selection.selected_words()
    assert _moves(win) == []                    # …and nothing was dragged in the process


def test_the_selection_matches_the_same_drag_on_unmarked_text(win, tmp_path):
    """Not merely 'something got selected' — the same words, so the highlight is genuinely
    transparent to the gesture rather than merely non-blocking."""
    start, end = _passage_ends(win)
    _press_drag(win, start, end)
    marked = _words(win)

    plain = str(tmp_path / "plain.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 302), "REVIEWERS MARKED THIS SENTENCE", fontsize=12)
    doc.save(plain)
    doc.close()

    w2 = MainWindow(PdfApp.instance(), plain, PdfApp.instance().settings)
    try:
        w2.show()
        PdfApp.instance().processEvents()
        rect = w2.view.scene_rect_for_box(0, TEXT_BOX)
        _press_drag(w2, QPointF(rect.left() + 2, rect.center().y()),
                    QPointF(rect.right() - 2, rect.center().y()))
        assert marked == _words(w2)
        assert marked                                    # and it selected something at all
    finally:
        w2.undo_stack.setClean()
        w2.close()


def test_the_selected_text_copies(win):
    """Ctrl+C on it — the verb the reader actually wanted when the highlight ate their drag."""
    from PySide6.QtGui import QGuiApplication

    start, end = _passage_ends(win)
    _press_drag(win, start, end)
    win._edit_copy()
    assert "MARKED" in QGuiApplication.clipboard().text()


def test_a_click_on_a_foreign_highlight_no_longer_selects_the_mark(win):
    """Before M82 a zero-drag press *outlined* the foreign mark — ``finish_foreign_move``'s
    click-not-a-drag path calls ``outline_foreign``. Asserting only "no move was recorded" would
    pass either way (a zero-drag records none regardless), so this checks the selection chrome:
    the press now belongs to the text layer, as on any unmarked word."""
    start, _end = _passage_ends(win)
    _press_drag(win, start, start)
    assert _moves(win) == []
    assert win.view.annotations._selection_items == []      # the reviewer's mark is not picked up
