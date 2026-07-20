"""Copy / paste objects (PLAN.md §GUI feature roadmap, M59 — R3 "Markup Tools"). Offscreen GUI.

An in-process object clipboard over the frozen descriptor value objects: copy a free-placed mark
(text box + the R3 drawn types), paste with an offset on the same page, rect-clamped across page
sizes, cross-window for free (single process, the page-clipboard pattern). Ctrl+C/X/V are
focus-routed — inline text editor → Pages sidebar → text selection → selected object — and
copying a text box also sets ``text/plain``. The Done-when: copy/paste within + across windows;
keyboard routing unambiguous.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent

import main_window as mw
from app import PdfApp
from main_window import MainWindow
from model.page_edits import InkStroke, Line, Shape, TextBox
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    qapp.object_clipboard = None
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


_BOX = TextBox((100.0, 100.0, 220.0, 140.0), "COPY-ME")
_SHAPE = Shape("rect", (100.0, 300.0, 180.0, 360.0))


def _scene(win, x, y):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


# ---- copy / cut --------------------------------------------------------------


def test_copy_object_fills_both_clipboards_for_a_text_box(app, win):
    win.vdoc.add_annotation(0, _BOX)
    assert win._copy_object((0, _BOX)) is True
    assert app.object_clipboard == _BOX
    assert QGuiApplication.clipboard().text() == "COPY-ME"  # text/plain rides along


def test_copy_drawn_mark_does_not_touch_the_text_clipboard(app, win):
    QGuiApplication.clipboard().setText("keep-me")
    win.vdoc.add_annotation(0, _SHAPE)
    win._copy_object((0, _SHAPE))
    assert app.object_clipboard == _SHAPE
    assert QGuiApplication.clipboard().text() == "keep-me"


def test_cut_object_copies_then_removes_undoably(app, win):
    win.vdoc.add_annotation(0, _SHAPE)
    win._cut_object((0, _SHAPE))
    assert app.object_clipboard == _SHAPE
    assert win.vdoc.page_annotations(0) == ()
    win.undo_stack.undo()
    assert win.vdoc.page_annotations(0) == (_SHAPE,)


# ---- paste: offset, click spot, clamp ----------------------------------------


def test_paste_offsets_on_the_same_page_and_undoes(app, win):
    win.vdoc.add_annotation(0, _SHAPE)
    win._copy_object((0, _SHAPE))
    win._paste_object()
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape)]
    assert len(marks) == 2
    pasted = next(m for m in marks if m != _SHAPE)
    assert pasted.rect == pytest.approx((112.0, 312.0, 192.0, 372.0))  # +12, +12
    win.undo_stack.undo()
    assert win.vdoc.page_annotations(0) == (_SHAPE,)


def test_paste_at_a_context_point_centres_there(app, win):
    app.object_clipboard = _SHAPE  # 80 × 60
    win._paste_object(0, (300.0, 400.0))
    pasted = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape))
    assert pasted.rect == pytest.approx((260.0, 370.0, 340.0, 430.0))  # centred on the click


def test_paste_clamps_to_the_page(app, win):
    pw, ph = win.vdoc.page_visible_size(0)
    app.object_clipboard = _SHAPE
    win._paste_object(0, (pw - 1.0, ph - 1.0))  # click in the far corner
    pasted = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape))
    x0, y0, x1, y1 = pasted.rect
    assert x1 <= pw + 0.01 and y1 <= ph + 0.01              # rect-clamped onto the page
    assert (x1 - x0, y1 - y0) == (80.0, 60.0)               # size preserved


def test_paste_with_empty_clipboard_is_a_noop(app, win):
    win._paste_object()
    assert win.vdoc.page_annotations(0) == ()


def test_paste_translates_every_ink_point(app, win):
    ink = InkStroke((((100.0, 100.0), (140.0, 120.0)),))
    app.object_clipboard = ink
    win._paste_object()
    pasted = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, InkStroke))
    assert pasted.paths[0][0] == pytest.approx((112.0, 112.0))
    assert pasted.paths[0][1] == pytest.approx((152.0, 132.0))


# ---- cross-window ------------------------------------------------------------


def test_paste_lands_in_another_window(app, a_pdf, b_pdf):
    source = MainWindow(app, a_pdf, app.settings)
    target = MainWindow(app, b_pdf, app.settings)
    source.vdoc.add_annotation(0, _BOX)
    source._copy_object((0, _BOX))
    target._paste_object()
    pasted = [a for a in target.vdoc.page_annotations(0) if isinstance(a, TextBox)]
    assert len(pasted) == 1 and pasted[0].text == "COPY-ME"
    assert source.vdoc.page_annotations(0) == (_BOX,)       # copy, not move
    for w in (source, target):
        w.undo_stack.setClean()
        w.close()


# ---- focus routing -----------------------------------------------------------


def test_ctrl_c_prefers_the_text_selection(app, win):
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    word = page.get_text("words")[0]
    center = win.view.scene_rect_for_box(0, word[:4]).center()
    assert win.view.selection.select_word_at(center)
    win.vdoc.add_annotation(0, _SHAPE)
    win.view.annotations.select_object(0, _SHAPE)           # an object is selected too
    win._edit_copy()
    assert QGuiApplication.clipboard().text() == word[4]    # the text won
    assert app.object_clipboard is None                     # the object did not


def test_ctrl_c_copies_the_selected_object_without_text(app, win):
    win.vdoc.add_annotation(0, _SHAPE)
    win.view.annotations.select_object(0, _SHAPE)
    win._edit_copy()
    assert app.object_clipboard == _SHAPE


def test_sidebar_focus_routes_to_pages(app, win, monkeypatch):
    monkeypatch.setattr(win.thumbs, "hasFocus", lambda: True)
    win.thumbs.setCurrentRow(0)
    win._edit_copy()
    assert len(app.page_clipboard) == 1                     # a page, not an object
    win._edit_paste()
    assert win.vdoc.page_count == 4                         # pasted after the selection


def test_inline_editor_keeps_its_own_clipboard(app, win, monkeypatch):
    class FakeEditor(mw.QPlainTextEdit):
        pasted = False

        def paste(self):
            self.pasted = True

    editor = FakeEditor()
    monkeypatch.setattr(mw.QApplication, "focusWidget", staticmethod(lambda: editor))
    app.object_clipboard = _SHAPE
    win._edit_paste()
    assert editor.pasted is True                            # forwarded to the editor…
    assert win.vdoc.page_annotations(0) == ()               # …never pasted as an object


# ---- click-select + keyboard delete ------------------------------------------


def test_click_selects_and_delete_removes(app, win):
    win.vdoc.add_annotation(0, _SHAPE)
    win.view.reload()
    overlay = win.view.annotations
    assert overlay.begin_move(_scene(win, 140, 330)) is True
    overlay.finish_move()                                   # zero-drag release = click → select
    assert overlay.selected_object == (0, _SHAPE)
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    win.view.keyPressEvent(press)
    assert win.vdoc.page_annotations(0) == ()               # removed, undoably
    win.undo_stack.undo()
    assert win.vdoc.page_annotations(0) == (_SHAPE,)


def test_escape_clears_the_selection(app, win):
    win.vdoc.add_annotation(0, _SHAPE)
    win.view.annotations.select_object(0, _SHAPE)
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    win.view.keyPressEvent(press)
    assert win.view.annotations.selected_object is None
    assert win.vdoc.page_annotations(0) == (_SHAPE,)        # cleared, not deleted


# ---- context menus -----------------------------------------------------------


def test_annotation_menu_offers_copy_cut_for_free_placed_marks(app, win):
    win.vdoc.add_annotation(0, _SHAPE)
    win.view.reload()
    menu = win._view_context_menu(_scene(win, 140, 330))
    titles = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert titles == ["Copy Object", "Cut Object", "Remove shape"]


def test_highlight_menu_stays_remove_only(app, win):
    from model.page_edits import Highlight

    win.vdoc.add_annotation(0, Highlight(((100.0, 100.0, 200.0, 114.0),)))
    win.view.reload()
    menu = win._view_context_menu(_scene(win, 150, 107))
    assert [a.text() for a in menu.actions()] == ["Remove highlight"]


def test_bare_page_paste_object_enables_with_clipboard(app, win):
    app.object_clipboard = _SHAPE
    menu = win._view_context_menu(_scene(win, 300, 600))
    paste = next(a for a in menu.actions() if a.text() == "Paste Object")
    assert paste.isEnabled() is True
    paste.trigger()
    pasted = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape))
    assert pasted.rect == pytest.approx((260.0, 570.0, 340.0, 630.0), abs=0.05)  # centred on it
