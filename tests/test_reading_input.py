"""The rest of the reading-input conventions — M89.1 / M89.2 / M89.3 (`PLAN.md` §M89), and the
M91.4 corrections to the paging keys (`PLAN.md` §M91). Offscreen GUI.

Three gestures every PDF reader has and this view did not:

* **M89.1** `Home` / `End` / `Ctrl+Home` / `Ctrl+End` → the **document's** start / end, all four one
  verb. Qt left every one of them dead here: `QAbstractScrollArea` binds Home/End only on macOS.
* **M89.2** `Space` / `Shift+Space` → page down / up — and, since **M91.4**, by a *page* rather than
  by the scrollbar's `SliderPageStep`. The step is a **reading stop**: stepping by the viewport
  height while the strip advances by the page pitch slipped one `_PAGE_GAP` per press and the error
  accumulated (owner report, 2026-07-30). `PgDn`/`PgUp` ride the same rule, as M89.2 promised.
* **M89.3** `Shift+wheel` → horizontal pan. An *override*, not a gap — Qt's own `Shift+wheel`
  scrolls this view vertically, so a page wider than the window had no wheel gesture to cross it.

The design decision under all of them is that they live in `PdfView.keyPressEvent` / `wheelEvent`,
**never** as window-level `QAction` shortcuts: a window shortcut fires wherever focus is, so `Home`
and `Space` bound that way would hijack those keys from the inline editors that are children of this
viewport. Two tests pin that (`test_the_navigation_keys_are_not_window_shortcuts`,
`test_space_still_types_a_space_in_a_form_field`). M91.4's sidebar fallback is the same decision
read the other way — `MainWindow.keyPressEvent` runs *after* every widget in the chain has declined
the key, so a focused editor still wins, which is what makes it not a shortcut.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtTest import QTest

from app import PdfApp
from store.settings import Settings
from viewer.pdf_view import _PAGE_GAP, _WHEEL_NOTCH

NONE = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(a_pdf)
    w.resize(1000, 700)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def view(win):
    return win.view


@pytest.fixture
def deck(qapp, tmp_path):
    """A 12-page window — enough pages for a per-press drift to show up as itself (M91.4)."""
    path = str(tmp_path / "deck.pdf")
    doc = fitz.open()
    for i in range(12):
        doc.new_page().insert_text((72, 100), f"page {i + 1}", fontsize=30)
    doc.save(path)
    doc.close()
    qapp.settings = Settings(tmp_path / "vs-deck.json")
    w = qapp.open_document(path)
    w.resize(1000, 700)
    w.show()
    qapp.processEvents()
    w.view.fit_page()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _top_of(view, index: int) -> int:
    """The scroll offset that puts page ``index``'s top edge under the viewport's — what `goto_page`
    lands on, and therefore what a paging key must agree with to the pixel."""
    return int(view._pages[index]["y"]) - _PAGE_GAP


def _key(view, key, mods=NONE):
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))


def _wheel(view, notches, mods=NONE, x_notches=0):
    """One mouse-wheel detent per notch (positive = towards the reader / up-and-left)."""
    pt = QPointF(view.viewport().rect().center())
    delta = QPoint(_WHEEL_NOTCH * x_notches, _WHEEL_NOTCH * notches)
    view.wheelEvent(QWheelEvent(pt, view.viewport().mapToGlobal(pt), delta, delta,
                                Qt.MouseButton.NoButton, mods,
                                Qt.ScrollPhase.NoScrollPhase, False))


def _widen(view, zoom=3.0):
    """Zoom past the viewport's width so there is a horizontal range to pan across."""
    view.set_zoom(zoom)
    PdfApp.instance().processEvents()
    hbar = view.horizontalScrollBar()
    assert hbar.maximum() > hbar.minimum(), "fixture failed to produce a horizontal range"
    return hbar


# ---- M89.1: Home / End go to the ends of the document ------------------------


@pytest.mark.parametrize("mods", [NONE, CTRL], ids=["bare", "ctrl"])
def test_end_jumps_to_the_document_end(view, mods):
    """`End` and `Ctrl+End` are the same verb (owner call): a continuous strip has no separate
    "line end" for the bare form to mean, and Preview/Edge/Chrome bind them alike in a PDF."""
    vbar = view.verticalScrollBar()
    assert vbar.value() == vbar.minimum()
    _key(view, Qt.Key.Key_End, mods)
    assert vbar.value() == vbar.maximum()


@pytest.mark.parametrize("mods", [NONE, CTRL], ids=["bare", "ctrl"])
def test_home_jumps_to_the_document_start(view, mods):
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    _key(view, Qt.Key.Key_Home, mods)
    assert vbar.value() == vbar.minimum()


def test_the_page_indicator_follows_the_jump(view):
    """Implemented as the scrollbar's minimum/maximum, which is the literal reading — and which
    gets the current-page update for free, since `_on_scroll` already does it."""
    seen: list[int] = []
    view.currentPageChanged.connect(seen.append)
    _key(view, Qt.Key.Key_End)
    assert view.current_page == len(view._pages) - 1
    assert seen and seen[-1] == view.current_page
    _key(view, Qt.Key.Key_Home)
    assert view.current_page == 0


def test_the_keypad_home_key_works_too(view):
    """The keypad's Home carries `KeypadModifier`, so an exact modifier match would accept the main
    keyboard's key and silently ignore the keypad's — a difference no reader means."""
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    _key(view, Qt.Key.Key_Home, Qt.KeyboardModifier.KeypadModifier)
    assert vbar.value() == vbar.minimum()


def test_the_slideshow_keeps_its_own_home_and_end(win, view):
    """M78 already binds Home/End to the first/last *slide*; that branch returns long before the
    new one, so whole-slide stepping is untouched."""
    win._a_slideshow.trigger()
    PdfApp.instance().processEvents()
    assert view.slideshow
    _key(view, Qt.Key.Key_End)
    assert view.current_page == len(view._pages) - 1
    _key(view, Qt.Key.Key_Home)
    assert view.current_page == 0
    _key(view, Qt.Key.Key_Escape)
    PdfApp.instance().processEvents()


# ---- M89.2 / M91.4: Space pages, by a page ------------------------------------


def test_space_lands_on_the_next_page(deck):
    """A document opens at Fit Page, where one screenful *is* one page — so one press must put the
    next page's top exactly under the viewport's, not a viewport height further down."""
    view = deck.view
    _key(view, Qt.Key.Key_Space)
    assert view.verticalScrollBar().value() == _top_of(view, 1)


def test_shift_space_lands_on_the_previous_page(deck):
    view = deck.view
    view.goto_page(5)
    _key(view, Qt.Key.Key_Space, SHIFT)
    assert view.verticalScrollBar().value() == _top_of(view, 4)


def test_paging_never_drifts(deck):
    """**The M91.4 regression.** `SliderPageStepAdd` advances by the *viewport height*; the strip
    advances by the *page pitch*, which at Fit Page is one `_PAGE_GAP` less — so every press
    overshot by 14 px and the error accumulated (measured 126 px by page 10, past half a screen by
    page ~27, at which point the counter reads one page ahead of what fills the window).

    Eleven presses, every one of them checked: not "roughly a page" but the same offset a click on
    that page's thumbnail produces.
    """
    view = deck.view
    vbar = view.verticalScrollBar()
    for index in range(1, len(view._pages)):
        _key(view, Qt.Key.Key_Space)
        assert vbar.value() == _top_of(view, index), f"drifted by page {index + 1}"
        assert view.current_page == index


def test_paging_back_never_drifts(deck):
    view = deck.view
    vbar = view.verticalScrollBar()
    view.goto_page(len(view._pages) - 1)
    for index in reversed(range(len(view._pages) - 1)):
        _key(view, Qt.Key.Key_Space, SHIFT)
        assert vbar.value() == _top_of(view, index), f"drifted by page {index + 1}"


def test_space_matches_page_down(deck):
    """Same verb, same distance — `Space` is `PgDn` on the key most readers actually reach for.
    M91.4 takes `PgDn`/`PgUp` off Qt to keep that true: left to the base class they kept the old
    screenful, so the two keys would have drifted apart from one another as well as from the page.
    """
    view = deck.view
    vbar = view.verticalScrollBar()
    _key(view, Qt.Key.Key_PageDown)
    by_pgdn = vbar.value()
    assert by_pgdn == _top_of(view, 1)
    vbar.setValue(vbar.minimum())
    _key(view, Qt.Key.Key_Space)
    assert vbar.value() == by_pgdn
    _key(view, Qt.Key.Key_PageUp)
    assert vbar.value() == vbar.minimum()


def test_a_tall_page_takes_equal_steps_that_end_on_the_next_page(deck):
    """A page taller than the viewport is cut into the fewest **equal** steps that each still fit.

    Equal, rather than "a screenful, then the remainder": the remainder can be a handful of pixels —
    a press that visibly does nothing — and since every page of a document is usually the same
    height, it would do nothing once per page for the whole document.
    """
    view = deck.view
    view.set_zoom(view.zoom * 2.5)                     # one page now spans several screenfuls
    view.goto_page(0)                                  # ... and start from a page top: zoom re-anchors
    PdfApp.instance().processEvents()
    vbar = view.verticalScrollBar()
    assert view._pages[0]["h"] > vbar.pageStep(), "fixture failed to make a page taller than the screen"
    steps, seen = [], vbar.value()
    while vbar.value() < _top_of(view, 1):
        _key(view, Qt.Key.Key_Space)
        steps.append(vbar.value() - seen)
        seen = vbar.value()
    assert len(steps) > 1
    assert vbar.value() == _top_of(view, 1)            # the last step lands exactly on page 2
    assert max(steps) - min(steps) <= 1                # equal but for integer rounding
    assert max(steps) <= vbar.pageStep()               # ... and no step outruns the screen


def test_a_free_scroll_is_put_back_on_the_page_grid(deck):
    """The stops are anchored to the page, not to wherever the reader happens to be — so a press
    after a wheel scroll re-aligns instead of carrying the arbitrary offset down the document."""
    view = deck.view
    view.set_zoom(view.zoom * 2.5)
    view.goto_page(0)
    PdfApp.instance().processEvents()
    vbar = view.verticalScrollBar()
    aligned = [vbar.value()]
    while vbar.value() < _top_of(view, 1):             # the stops inside page 1
        _key(view, Qt.Key.Key_Space)
        aligned.append(vbar.value())
    vbar.setValue(aligned[0] + 37)                     # ... now scroll off the grid by hand
    _key(view, Qt.Key.Key_Space)
    assert vbar.value() == aligned[1]


def test_zoomed_out_space_advances_every_page_that_fits(deck):
    """Furthest stop within reach, not nearest: with several pages on screen a press advances all
    of them — still landing on a page top."""
    view = deck.view
    view.set_zoom(view.zoom / 3.0)
    PdfApp.instance().processEvents()
    vbar = view.verticalScrollBar()
    _key(view, Qt.Key.Key_Space)
    assert view.current_page >= 2, "a screenful holding 3+ pages advanced by fewer"
    assert vbar.value() == _top_of(view, view.current_page)


def test_the_facing_layout_pages_by_a_spread(deck):
    """A facing row's two pages share a scene y (M78), so the stop that closes a row's segment has
    to be found by *value* — taking "the next page" literally closes the row against its own partner
    and leaves the spread unsteppable."""
    view = deck.view
    view.set_page_layout("facing")
    view.goto_page(0)
    PdfApp.instance().processEvents()
    _key(view, Qt.Key.Key_Space)
    assert view.verticalScrollBar().value() == _top_of(view, 2)   # the 3|4 spread, not 2|3
    assert view.current_page == 2
    view.set_page_layout("single")


def test_space_at_the_end_stays_at_the_end(deck):
    """No stop is left in reach past the last page, so the fallback screenful is clamped by the bar
    — the key still means "onwards", it just has nowhere left to go."""
    vbar = deck.view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    _key(deck.view, Qt.Key.Key_Space)
    assert vbar.value() == vbar.maximum()


def test_space_still_types_a_space_in_a_form_field(win, view):
    """The reason these are view keys and not window shortcuts. The inline field editor is a child
    of this viewport, so with focus in it a real `Space` must reach the editor and type — never
    page the document."""
    rect = fitz.Rect(72, 200, 272, 220)                    # the `name` field in the A.pdf fixture
    assert view.form.handle_press(view.scene_rect_for_box(0, rect).center())
    editor = view.form._editor
    editor.setText("ab")
    editor.setCursorPosition(1)
    before = view.verticalScrollBar().value()
    QTest.keyClick(editor, Qt.Key.Key_Space)
    assert editor.text() == "a b"
    assert view.verticalScrollBar().value() == before      # ... and the page did not move


def test_the_navigation_keys_are_not_window_shortcuts(win):
    """Pins the decision itself: no `QAction` anywhere in the window may claim these keys, or a
    focused editor would never see them. M91.4's fallback is a `keyPressEvent`, not a shortcut,
    precisely so this stays true — see `test_space_still_types_a_space_in_a_form_field`."""
    hijacked = {Qt.Key.Key_Space, Qt.Key.Key_Home, Qt.Key.Key_End}
    for action in win.findChildren(type(win.menuBar().actions()[0])):
        for sequence in action.shortcuts():
            for i in range(sequence.count()):
                assert Qt.Key(sequence[i].key()) not in hijacked, action.text()


# ---- M91.4: the sidebar hands the paging key to the document -----------------


def _sidebar(win, tab=None):
    """Show the sidebar and return the Pages panel — or mount an optional tab (M79.1), raise it and
    return that panel. Raising it matters: `setFocus` on a widget behind another tab does nothing."""
    if tab is None:
        win.pages_dock.setVisible(True)
        PdfApp.instance().processEvents()
        return win.thumbs
    win._toggle_sidebar_tab(tab, True)
    win.pages_dock.setVisible(True)
    panel = {"outline": win.outline, "annotations": win.annotations_panel}[tab]
    win.pages_dock.widget().setCurrentWidget(panel)
    PdfApp.instance().processEvents()
    return panel


def test_space_pages_the_document_from_the_thumbnail_sidebar(deck):
    """Owner report: "pressing spacebar when I am on sidebar thumbnails does nothing".

    It did nothing *for ever* — `QAbstractItemView` accepts `Space`, so Qt never propagated it and
    the only way back was to click the page. The reader's eyes are on the document whatever the
    focus ring says, which is how Preview, Acrobat and Edge read it too.
    """
    thumbs, view = _sidebar(deck), deck.view
    thumbs.setFocus()
    PdfApp.instance().processEvents()
    assert PdfApp.instance().focusWidget() is thumbs
    QTest.keyClick(thumbs, Qt.Key.Key_Space)
    PdfApp.instance().processEvents()
    assert view.verticalScrollBar().value() == _top_of(view, 1)
    QTest.keyClick(thumbs, Qt.Key.Key_Space, SHIFT)
    PdfApp.instance().processEvents()
    assert view.verticalScrollBar().value() == view.verticalScrollBar().minimum()


def test_space_in_the_sidebar_does_not_touch_the_page_selection(deck):
    """The key was not inert, which is what makes this a defect rather than a gap: Qt's default
    **adds the current row to the selection** — the selection Delete Pages and Rotate act on.

    Staged here as a *multi*-row selection, which M85 already protects from the scrolling highlight
    (deliberate staging for a page operation must survive reading). Before the fix, `Space` grew it
    to `[0, 2, 3]` — a page the reader never picked, one keypress from being deleted.
    """
    thumbs = _sidebar(deck)
    thumbs.clearSelection()          # the open page is selected on open (M85) — stage our own
    for row in (2, 3):
        thumbs.item(row).setSelected(True)
    thumbs.setFocus()
    PdfApp.instance().processEvents()
    QTest.keyClick(thumbs, Qt.Key.Key_Space)
    PdfApp.instance().processEvents()
    assert thumbs.selected_rows() == [2, 3]


@pytest.mark.parametrize("key", [Qt.Key.Key_Down, Qt.Key.Key_End])
def test_the_sidebar_keeps_its_own_keys(deck, key):
    """Only `Space` is handed over. The arrows, PgUp/PgDn and Home/End all mean something in a page
    list, and each of them jumps the view through `pageActivated` anyway."""
    thumbs, view = _sidebar(deck), deck.view
    thumbs.setCurrentRow(0)
    thumbs.setFocus()
    PdfApp.instance().processEvents()
    QTest.keyClick(thumbs, key)
    PdfApp.instance().processEvents()
    assert thumbs.currentRow() > 0
    assert view.current_page == thumbs.currentRow()   # ... and the view followed


def test_space_pages_the_document_from_the_outline_tab(win):
    """The same rule on every tab of the sidebar — a reader who opened the outline to navigate has
    not stopped reading."""
    outline, view = _sidebar(win, tab="outline"), win.view
    outline.setFocus()
    PdfApp.instance().processEvents()
    assert PdfApp.instance().focusWidget() is outline
    QTest.keyClick(outline, Qt.Key.Key_Space)
    PdfApp.instance().processEvents()
    assert view.verticalScrollBar().value() == _top_of(view, 1)


def test_the_window_fallback_is_inert_in_the_slideshow(deck):
    """The slideshow steps *slides*; a scroll would be the wrong verb, and free-scrolling the mode
    that shows one page per screen is exactly what M78 stopped."""
    deck.view.slideshow = True
    assert deck.view.reading_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, NONE)) is False
    deck.view.slideshow = False


# ---- M91.4: a thumbnail click always jumps -----------------------------------


def test_clicking_the_current_thumbnail_re_seats_the_view(deck):
    """`currentRowChanged` fires only when the row *changes*, and the view drags the highlight along
    as the reader scrolls — so scrolling away from page 1 and clicking page 1's thumbnail to get
    back did **nothing**. A click on a thumbnail is "show me this page", every time."""
    thumbs, view = _sidebar(deck), deck.view
    view.verticalScrollBar().setValue(300)             # part-way down page 1; the row is still 0
    PdfApp.instance().processEvents()
    assert thumbs.currentRow() == 0
    _click_thumb(thumbs, 0)
    assert view.verticalScrollBar().value() == _top_of(view, 0)


def test_clicking_another_thumbnail_jumps_once(deck):
    """... and the row-change path has not been doubled up by the click path."""
    thumbs, view = _sidebar(deck), deck.view
    seen: list[int] = []
    thumbs.pageActivated.connect(seen.append)
    _click_thumb(thumbs, 4)
    assert seen == [4]
    assert view.verticalScrollBar().value() == _top_of(view, 4)


def _click_thumb(thumbs, row: int) -> None:
    QTest.mouseClick(thumbs.viewport(), Qt.MouseButton.LeftButton, NONE,
                     thumbs.visualItemRect(thumbs.item(row)).center())
    PdfApp.instance().processEvents()


# ---- M89.3: Shift+wheel pans horizontally ------------------------------------


def test_shift_wheel_pans_horizontally(view):
    hbar = _widen(view)
    hbar.setValue(hbar.maximum() // 2)
    before = hbar.value()
    _wheel(view, -1, SHIFT)                      # wheel away from the reader → pan right
    assert hbar.value() > before
    _wheel(view, 1, SHIFT)                       # ... and back
    assert hbar.value() == before


def test_shift_wheel_does_not_scroll_vertically(view):
    """The override. Qt's own `Shift+wheel` scrolls *down* here — measured with the h-bar at full
    range — which is why a wide page had no wheel gesture that could cross it."""
    _widen(view)
    vbar = view.verticalScrollBar()
    before = vbar.value()
    _wheel(view, -1, SHIFT)
    assert vbar.value() == before


def test_shift_wheel_is_inert_when_the_page_fits(view):
    """With nothing to pan across the gesture does nothing — rather than silently scrolling down,
    so the shifted wheel means one thing everywhere, as it does in a browser."""
    view.fit_page()
    PdfApp.instance().processEvents()
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    assert hbar.maximum() == hbar.minimum()
    before = vbar.value()
    _wheel(view, -1, SHIFT)
    assert vbar.value() == before


def test_a_real_horizontal_wheel_is_left_to_qt(view):
    """A tilt wheel or touchpad already sends an x component, and Qt routes it correctly — we only
    reinterpret the vertical axis Shift is decorating."""
    hbar = _widen(view)
    hbar.setValue(hbar.minimum())
    _wheel(view, 0, SHIFT, x_notches=-1)
    assert hbar.value() > hbar.minimum()


def test_a_plain_wheel_still_scrolls_vertically(view):
    _widen(view)
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    hbar.setValue(hbar.maximum() // 2)
    before_h, before_v = hbar.value(), vbar.value()
    _wheel(view, -1)
    assert vbar.value() > before_v
    assert hbar.value() == before_h


def test_ctrl_wheel_still_zooms(view):
    """M80 is checked first, so a Ctrl+Shift+wheel zooms rather than panning."""
    before = view.zoom
    _wheel(view, 1, CTRL)
    PdfApp.instance().processEvents()
    assert view.zoom > before
