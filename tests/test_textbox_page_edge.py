"""A box the editor wraps at the page edge shows the lines it saves (M166, #434).

A line too long for the space left on the page wraps at the page's right edge while it is typed.
The box was committed as an auto-width box, which hugs one line, so the view painted one line while
the file held it wrapped, until the file was reopened. It is now committed as a fixed-width box at
the edge. Offscreen, through the real editor.
"""

from __future__ import annotations

import re

import pymupdf as fitz
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import PdfApp
from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.page_edits import TextBox, textbox_height, textbox_lines
from main_window import MainWindow
from store.settings import Settings

_LONG = "A long line of text that cannot fit in what is left of the page here"


@pytest.fixture(scope="module")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    path = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    doc.new_page()                                   # A4: 595 pt wide
    doc.save(path)
    doc.close()
    w = MainWindow(qapp, path, qapp.settings)
    w.show()
    QTest.qWaitForWindowExposed(w)
    w.activateWindow()          # or the editor loses focus when activation arrives, and commits
    QApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _commit(win) -> None:
    """Commit the way a reader does: focus leaves the editor, and its own focus-out commits. Calling
    ``_commit_textbox`` directly leaves the hidden editor holding focus, which no reader can do."""
    win.view.setFocus()
    QApplication.processEvents()


def _type_at(win, x: float, y: float, text: str) -> TextBox:
    overlay = win.view.annotations
    assert overlay.place_textbox(win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center())
    QApplication.processEvents()
    overlay._editor.setPlainText(text)
    _commit(win)
    (box,) = win.vdoc.page_annotations(0)
    return box


def _saved_lines(win, tmp_path) -> int:
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(win.vdoc, out)
    with fitz.open(out) as doc:
        page = doc[0]
        return len(re.findall(rb"Tj", page.first_annot._getAP()))


def test_a_line_too_long_for_the_page_shows_the_lines_it_saves(win, tmp_path):
    box = _type_at(win, 450, 100, _LONG)
    assert not box.auto_width
    assert box.rect[2] == pytest.approx(595)
    lines = textbox_lines(box)
    assert len(lines) > 1
    assert _saved_lines(win, tmp_path) == len(lines)


def test_it_is_as_tall_as_its_lines(win, tmp_path):
    box = _type_at(win, 450, 100, _LONG)
    need = textbox_height(len(textbox_lines(box)), box.fontsize, box.border_width)
    assert box.rect[3] - box.rect[1] == pytest.approx(need)


def test_reopening_it_changes_nothing(win, tmp_path):
    box = _type_at(win, 450, 100, _LONG)
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(win.vdoc, out)
    with fitz.open(out) as doc:
        from klarpdf.model.page_edits import read_klarpdf_annotations
        (back,) = read_klarpdf_annotations(doc[0])
    assert textbox_lines(back) == textbox_lines(box)


def test_a_short_line_near_the_edge_still_hugs_its_text(win):
    box = _type_at(win, 450, 100, "Short")
    assert box.auto_width
    assert box.rect[2] < 595


def test_a_long_line_in_the_middle_of_the_page_is_left_alone(win):
    box = _type_at(win, 60, 100, "A line that fits easily across the page")
    assert box.auto_width
    assert textbox_lines(box) == ["A line that fits easily across the page"]


def test_editing_a_box_into_a_long_line_wraps_it_at_the_edge(win, tmp_path):
    first = _type_at(win, 450, 100, "Short")
    overlay = win.view.annotations
    x0, y0, x1, y1 = first.rect
    assert overlay.edit_textbox_at(win.view.scene_rect_for_box(0, (x0 + 2, (y0 + y1) / 2,
                                                                   x0 + 2.01, (y0 + y1) / 2 + 0.01)).center())
    QApplication.processEvents()
    overlay._editor.setPlainText(_LONG)
    _commit(win)
    (box,) = win.vdoc.page_annotations(0)
    assert not box.auto_width
    assert _saved_lines(win, tmp_path) == len(textbox_lines(box))


def test_a_box_a_hair_too_narrow_for_its_text_is_widened(win):
    """The editor sizes a box by Qt's widths, and the file wraps by MuPDF's. A box that came out
    narrower than MuPDF needs is widened at the commit, as one read from a file is."""
    overlay = win.view.annotations
    width = fitz.get_text_length("JANE DOE", fontname="helv", fontsize=11)
    narrow = TextBox((100.0, 100.0, 100.0 + width, 120.0), "JANE DOE")
    fitted = overlay._fitted(0, narrow)
    assert fitted.auto_width and fitted.rect[2] > narrow.rect[2]
    assert fitted.rect[:2] == narrow.rect[:2]


def test_a_box_that_cannot_widen_enough_wraps_at_the_edge_not_before_it(win):
    """A box whose right side is short of the edge, with a line longer than the room left, uses all
    of that room before it wraps."""
    overlay = win.view.annotations
    box = TextBox((500.0, 100.0, 560.0, 120.0), "A line much longer than the room to the edge")
    fitted = overlay._fitted(0, box)
    assert not fitted.auto_width
    assert fitted.rect[2] == pytest.approx(595)
