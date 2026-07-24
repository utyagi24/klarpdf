"""Text-box width reflow (PLAN.md §GUI roadmap, M78.3).

A lone text box gains a single right-edge handle: dragging it sets the wrap width (left edge pinned)
and the text refolds, its height auto-fitting. ``TextBox.auto_width`` records which regime a box is
in — ``True`` hugs the longest line (a new box's default), ``False`` makes the rect width
authoritative and wraps. A group resize still leaves text boxes unstretched. ``auto_width`` is not
written to the PDF: on round-trip it is inferred from whether the text fits one line in the stored
rect, so a narrowed box reopens still folded.

The model + round-trip parts are headless; the handle/reflow parts drive the offscreen GUI.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.page_edits import TextBox, _textbox_fits_one_line
from model.virtual_document import VirtualDocument
from store.settings import Settings


# ---- the round-trip inference (headless) -------------------------------------


def test_fits_one_line_true_when_text_is_shorter_than_the_box():
    box = (0.0, 0.0, 400.0, 20.0)
    assert _textbox_fits_one_line("short note", "helv", 11.0, box) is True


def test_fits_one_line_false_when_the_box_is_narrower_than_the_text():
    box = (0.0, 0.0, 40.0, 20.0)                       # deliberately far too narrow
    assert _textbox_fits_one_line("a much longer line of note text", "helv", 11.0, box) is False


def test_fits_one_line_measures_the_longest_paragraph():
    """Explicit newlines don't force a wrap — each paragraph is measured on its own."""
    box = (0.0, 0.0, 120.0, 40.0)
    assert _textbox_fits_one_line("one\ntwo\nthree", "helv", 11.0, box) is True


@pytest.fixture
def text_pdf(tmp_path) -> str:
    path = str(tmp_path / "t.pdf")
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def test_a_narrowed_box_keeps_its_fold_across_save_and_reopen(text_pdf, tmp_path):
    # A wide-text box narrowed so the text must wrap: auto_width False, and the rect is too narrow
    # for the text on one line.
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, TextBox((72.0, 200.0, 150.0, 260.0),
                                 "a fairly long note that will not fit on a single narrow line",
                                 auto_width=False))
    out = _materialize(v1, tmp_path)
    got = next(a for a in VirtualDocument.from_path(out).page_annotations(0)
               if isinstance(a, TextBox))
    assert got.auto_width is False                     # the fold survived the round-trip


def test_an_auto_width_box_reopens_as_auto_width(text_pdf, tmp_path):
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, TextBox((72.0, 200.0, 320.0, 220.0), "Note", auto_width=True))
    out = _materialize(v1, tmp_path)
    got = next(a for a in VirtualDocument.from_path(out).page_annotations(0)
               if isinstance(a, TextBox))
    assert got.auto_width is True


# ---- the handle + reflow (offscreen GUI) -------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _add(win, *marks):
    for mark in marks:
        win.vdoc.add_annotation(0, mark)
    win.view.reload()
    return win.vdoc.page_annotations(0)


def _only_textbox(win):
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox)]
    assert len(marks) == 1
    return marks[0]


def test_lone_box_shows_only_the_right_edge_handle(win):
    _add(win, TextBox((100.0, 100.0, 300.0, 120.0), "hello"))
    win.view.annotations.select_object(0, _only_textbox(win))
    assert set(win.view.annotations._handles._items) == {"e"}


def test_dragging_the_right_edge_reflows_and_grows_height(win):
    text = "one two three four five six seven eight nine ten eleven twelve"
    _add(win, TextBox((100.0, 100.0, 300.0, 120.0), text))
    ov = win.view.annotations
    ov.select_object(0, _only_textbox(win))
    assert ov.begin_resize("e", _scene(win, 300, 110)) is True
    ov.update_resize(_scene(win, 180, 110))            # drag the right edge left to ~x=180
    ov.finish_resize()
    box = _only_textbox(win)
    assert box.auto_width is False
    assert box.rect[0] == pytest.approx(100.0)         # left edge pinned
    assert box.rect[2] == pytest.approx(180.0, abs=1.0)  # width followed the cursor
    assert box.rect[3] - box.rect[1] > 20.0            # height grew to fit the wrapped text
    assert win.undo_stack.undoText() == "Resize text box"
    win.undo_stack.undo()
    assert _only_textbox(win).auto_width is True        # back to the hug-the-text original


def test_group_resize_leaves_a_text_box_unstretched(win):
    from model.page_edits import Shape

    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)),
         TextBox((200.0, 200.0, 300.0, 220.0), "note"))
    ov = win.view.annotations
    ov.select_objects(0, list(win.vdoc.page_annotations(0)))
    assert ov.begin_resize("se", _scene(win, 300, 220)) is True
    ov.update_resize(_scene(win, 400, 320))            # grow the group's box
    ov.finish_resize()
    box = _only_textbox(win)
    assert box.rect[2] - box.rect[0] == pytest.approx(100.0)   # width unchanged…
    assert box.rect[3] - box.rect[1] == pytest.approx(20.0)    # …and height unchanged
    assert box.auto_width is True                              # regime untouched by a group scale


def test_wrap_helper_folds_a_long_line(win):
    ov = win.view.annotations
    lines, _fm = ov._wrap_textbox_lines("alpha beta gamma delta epsilon zeta", "helv", 11.0, 60.0)
    assert len(lines) > 1                              # a 60 pt width folds the phrase
    assert " ".join(lines).split() == "alpha beta gamma delta epsilon zeta".split()
