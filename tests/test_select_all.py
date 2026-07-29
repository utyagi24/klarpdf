"""`Ctrl+A` select-all, and the repaint rework it depends on — M89.6 (`PLAN.md` §M89). Offscreen GUI.

Two things in one milestone, because the first is unshippable without the second.

**Select all** means the *whole document*, not the current page (owner call: "both Edge and Brave
select text from all the document — why should we be different?"). It is nearly free in the model,
which has always carried the selection as a `(page_index, word_index)` anchor/cursor pair spanning
pages.

**The repaint rework** is not optional. `TextSelection.repaint` put one `QGraphicsRectItem` in the
scene per selected *word*, and `_build_scene` calls `scene.clear()` on every zoom step — measured at
**20.6 s** for a 247k-word selection. So Ctrl+A on a 500-page document followed by one zoom step
would have frozen the app for twenty seconds. A **pre-existing latent bug**: a drag carried across
several hundred pages reaches the same state today; Ctrl+A merely makes it one keystroke. Two
changes bound it — painting clipped to the visible band, and each line's run coalesced into one
rect.

The coalescing also closes a gap that was there all along: the mark this app *commits* has always
been one bar per line (`MainWindow._selection_line_bars`), so the per-word preview was the odd one
out and a highlight visibly re-flowed on release. Both now build from the same `line_bars`.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtTest import QTest

from app import PdfApp
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.pdf_view import PdfView, _PREFETCH
from viewer.text_selection import TextSelection
from viewer.tools import ArmedTool

LINES = ["The quick brown fox jumps over the lazy dog",
         "and then turns around to do it all again",
         "in the opposite direction, at some length"]


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


def _make_pdf(path, pages, *, blank_first=False, blank_last=False, text=True):
    doc = fitz.open()
    for p in range(pages):
        page = doc.new_page(width=430, height=300)
        blank = (blank_first and p == 0) or (blank_last and p == pages - 1) or not text
        if not blank:
            for i, line in enumerate(LINES):
                page.insert_text((40, 60 + 24 * i), f"p{p} {line}", fontsize=11)
    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def view(qapp, tmp_path):
    """A shown 12-page view whose viewport holds about one page — so the band is a small slice of
    a selection that spans the document."""
    path = _make_pdf(tmp_path / "many.pdf", 12)
    v = PdfView(VirtualDocument.from_path(path))
    v.selection = TextSelection(v)
    v.resize(500, 380)
    v.show()
    qapp.processEvents()
    v.open_at({})
    qapp.processEvents()
    yield v
    v.deleteLater()


def _ctrl_a(view):
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                                 Qt.KeyboardModifier.ControlModifier))


# ---- select all --------------------------------------------------------------


def test_ctrl_a_selects_the_whole_document(view, qapp):
    """The owner call. Not the current page — a viewer that scrolls continuously has no page
    boundary a reader would recognise as the limit of "all"."""
    _ctrl_a(view)
    qapp.processEvents()
    pages = {p for p, _i, _w in view.selection.selected_words()}
    assert pages == set(range(12))


def test_ctrl_a_copies_the_documents_text(view, qapp):
    _ctrl_a(view)
    assert view.selection.copy() is True
    text = QGuiApplication.clipboard().text()
    assert text.startswith("p0 The quick")
    assert "p11 in the opposite" in text


def test_select_all_pins_the_two_ends(view):
    view.selection.select_all()
    assert view.selection._anchor == (0, 0)
    last = view.selection._words_for(11)
    assert view.selection._cursor == (11, len(last) - 1)


def test_an_image_only_document_selects_nothing(qapp, tmp_path):
    """`Ctrl+A` on a document with no text layer selects nothing **and does not error** — Edge
    behaves identically, and it is the right answer for a scanned PDF."""
    path = _make_pdf(tmp_path / "blank.pdf", 3, text=False)
    v = PdfView(VirtualDocument.from_path(path))
    v.selection = TextSelection(v)
    v.resize(400, 300)
    v.show()
    qapp.processEvents()
    v.open_at({})
    qapp.processEvents()
    try:
        assert v.selection.select_all() is False
        assert v.selection.selected_words() == []
        assert v.selection.selected_text() == ""
    finally:
        v.deleteLater()


def test_the_ends_skip_pages_with_no_words(qapp, tmp_path):
    """The ends are the first and last pages that *have* words, not simply page 0 and page n−1: a
    leading or trailing scanned page has none, and an anchor on a non-existent word index would
    select nothing at all."""
    path = _make_pdf(tmp_path / "edges.pdf", 4, blank_first=True, blank_last=True)
    v = PdfView(VirtualDocument.from_path(path))
    v.selection = TextSelection(v)
    v.resize(400, 300)
    v.show()
    qapp.processEvents()
    v.open_at({})
    qapp.processEvents()
    try:
        assert v.selection.select_all() is True
        assert v.selection._anchor[0] == 1 and v.selection._cursor[0] == 2
        assert "p1 The quick" in v.selection.selected_text()
    finally:
        v.deleteLater()


def test_select_all_is_inert_in_a_rotated_view(view):
    """Selection is rotation-0 only throughout this class; Ctrl+A does not become the exception."""
    view.rotate_view(90)
    assert view.selection.select_all() is False
    assert view.selection.selected_words() == []


def test_ctrl_a_does_not_hijack_a_focused_editor(qapp, a_pdf, tmp_path):
    """Why this is a view key and not a window `QAction`: with focus in the inline field editor — a
    child of this viewport — Ctrl+A must mean select-all-*in-this-field*."""
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(a_pdf)
    win.resize(800, 600)
    win.show()
    qapp.processEvents()
    rect = fitz.Rect(72, 200, 272, 220)                 # the `name` field in the A.pdf fixture
    assert win.view.form.handle_press(win.view.scene_rect_for_box(0, rect).center())
    editor = win.view.form._editor
    editor.setText("abc")
    editor.setCursorPosition(3)
    QTest.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert editor.selectedText() == "abc"               # the field selected its own text...
    assert win.view.selection.selected_words() == []    # ...and the document did not
    win.undo_stack.setClean()
    win.close()


# ---- the repaint rework ------------------------------------------------------


def test_painting_is_clipped_to_the_visible_band(view, qapp):
    """The model holds the whole selection; the scene holds only what is on screen. This is what
    bounds the item count by viewport size instead of document length."""
    _ctrl_a(view)
    qapp.processEvents()
    lo, hi = view.overlay_band()
    assert hi - lo + 1 < 12                                  # the band really is a slice
    painted = {p for p in view.selection.line_bars(view.overlay_band())}
    assert painted == set(range(lo, hi + 1))
    assert len(view.selection._items) == sum(
        len(b) for b in view.selection.line_bars(view.overlay_band()).values())


def test_the_scene_holds_far_less_than_the_selection(view, qapp):
    """The measurement that forces the rework, expressed as an invariant: a document-wide selection
    must not put a document-wide number of items in the scene."""
    _ctrl_a(view)
    qapp.processEvents()
    words = len(view.selection.selected_words())
    assert words > 300                                       # ~36 words × 12 pages
    assert len(view.selection._items) < words / 10           # two orders of magnitude in practice


def test_the_band_follows_the_scroll(view, qapp):
    """Necessary *because* painting clips: selected text scrolling in from off screen would
    otherwise have nothing painted over it."""
    _ctrl_a(view)
    qapp.processEvents()
    before = view.overlay_band()
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    qapp.processEvents()
    after = view.overlay_band()
    assert after != before
    assert set(view.selection.line_bars(after)) == set(range(after[0], after[1] + 1))
    assert len(view.selection._items) == sum(
        len(b) for b in view.selection.line_bars(after).values())


def test_a_scroll_inside_the_band_does_not_repaint(view, qapp):
    """The guard that keeps this cheap: `repaint_for_scroll` returns immediately unless the band
    actually moved, which is the common case for a scroll event."""
    _ctrl_a(view)
    qapp.processEvents()
    items = list(view.selection._items)
    view.selection.repaint_for_scroll()          # band unchanged → same item objects, not rebuilt
    assert view.selection._items == items


def test_the_band_includes_the_prefetch_margin(view, qapp):
    """The margin is `_PREFETCH`, matching what the renderer keeps warm, so a scroll of a page or
    two finds the marks already there."""
    vbar = view.verticalScrollBar()
    vbar.setValue((vbar.minimum() + vbar.maximum()) // 2)
    qapp.processEvents()
    first, last = view._visible_range()
    assert view.overlay_band() == (max(0, first - _PREFETCH),
                                   min(11, last + _PREFETCH))


# ---- coalescing --------------------------------------------------------------


def test_a_lines_run_is_one_rect(view, qapp):
    """Words are sorted `(block, line, word_no)`, so a selected run on one line is a contiguous
    index range whose boxes union cleanly."""
    view.selection.select_all()
    qapp.processEvents()
    bars = view.selection.line_bars((0, 0))
    assert len(bars[0]) == len(LINES)                 # one bar per line, not per word
    words_on_page = [w for p, _i, w in view.selection.selected_words((0, 0))]
    assert len(words_on_page) > len(LINES) * 4        # ...and there were many words per line


def test_a_bar_spans_its_whole_line(view, qapp):
    """Unioned, not just the first word: the bar reaches from the line's leftmost word box to its
    rightmost, which is what removes the inter-word gaps."""
    view.selection.select_all()
    qapp.processEvents()
    words = [w for _p, _i, w in view.selection.selected_words((0, 0))]
    line0 = [w for w in words if (w[5], w[6]) == (words[0][5], words[0][6])]
    bar = next(b for b in view.selection.line_bars((0, 0))[0]
               if b[1] == pytest.approx(min(w[1] for w in line0)))
    assert bar[0] == pytest.approx(min(w[0] for w in line0))
    assert bar[2] == pytest.approx(max(w[2] for w in line0))


def test_the_preview_and_the_committed_mark_are_the_same_bars(qapp, tmp_path):
    """The gap coalescing closes. The mark this app commits has always been one bar per line, so
    the per-word preview visibly re-flowed on release. Both now come from `line_bars`, so they
    cannot drift."""
    qapp.settings = Settings(tmp_path / "vs.json")
    path = _make_pdf(tmp_path / "one.pdf", 1)
    win = qapp.open_document(path)
    win.resize(700, 560)
    win.show()
    qapp.processEvents()
    win.view.arm(ArmedTool.HIGHLIGHT)
    win.view.selection.select_all()
    qapp.processEvents()
    preview = win.view.selection.line_bars()[0]
    win._highlight_selection()
    qapp.processEvents()
    from model.page_edits import Highlight

    mark = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight))
    assert sorted(mark.rects) == sorted(preview)
    win.undo_stack.setClean()
    win.close()


def test_line_bars_without_a_band_still_covers_everything(view, qapp):
    """Everything that asks *what is selected* — copy, the markup verbs — still gets all of it;
    only painting clips."""
    _ctrl_a(view)
    qapp.processEvents()
    assert set(view.selection.line_bars()) == set(range(12))
