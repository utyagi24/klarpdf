"""A text box's text is where the saved file puts it (M164, #427). Offscreen GUI.

PyMuPDF draws a FreeText's text by fixed ratios: 1.5 border widths in from the box's edges, the
first baseline 0.8 font sizes below that, and lines 1.2 font sizes apart. The view centred its text
in the box and spaced it by Qt's own metrics, so 12 pt text in a 20 pt box saved 4.5 pt higher and
2 pt further left than it showed, and a long note lost the bottom of its last line in the file.

The headless platform resolves no real font (see ``tests/test_whitespace_fidelity.py``), so the view
is compared with the file through the baseline each line is placed at, its top plus its font's
ascent, never through pixels. That a ``QGraphicsSimpleTextItem`` draws its baseline exactly that
far below its top was measured on Windows' own font engine (``PLAN.md`` §M164).
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSimpleTextItem

from app import PdfApp
from klarpdf.model.page_edits import TextBox, apply_annotations
from store.settings import Settings
from viewer.annotations import _FREETEXT_BASELINE, _FREETEXT_INSET, _FREETEXT_PITCH
from viewer.text_format_bar import TextBoxStyle
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


def _saved_lines(box: TextBox) -> list[tuple[float, float, float]]:
    """``(x, baseline, bottom)`` of each line the saved drawing shows, top first, in page points.

    The box is drawn the way a save draws it and baked into the page, so this is the text every
    other viewer shows."""
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (box,))
    doc.bake()
    lines = {}
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            x, y = line["spans"][0]["chars"][0]["origin"]
            lines[round(y, 3)] = (x, y, line["bbox"][3])
    doc.close()
    return [lines[y] for y in sorted(lines)]


def _painted_lines(win, box: TextBox) -> list[tuple[float, float]]:
    """``(x, baseline)`` of each line the view paints for ``box``, top first, in page points."""
    win.vdoc.add_annotation(0, box)
    win.view.annotations.repaint()
    frames = [it for it in win.view.annotations._items if type(it) is QGraphicsRectItem]
    items = sorted((c for c in frames[-1].childItems() if isinstance(c, QGraphicsSimpleTextItem)),
                   key=lambda it: it.pos().y())
    return [(it.pos().x(), it.pos().y() + QFontMetricsF(it.font()).ascent()) for it in items]


# ---- what the file does ------------------------------------------------------------------------


@pytest.mark.parametrize("fontname", ["helv", "tiro", "cour"])
@pytest.mark.parametrize("fontsize", [8.0, 12.0, 24.0])
@pytest.mark.parametrize("border", [0.0, 2.0])
def test_pymupdf_lays_a_text_box_out_by_fixed_ratios(fontname, fontsize, border):
    """The numbers the view copies. A PyMuPDF upgrade that moves one fails here, with the reason,
    rather than quietly putting the view back out of step with the file."""
    rect = (100.0, 100.0, 400.0, 300.0)
    lines = _saved_lines(TextBox(rect, "Ag one\nAg two\nAg three", fontsize=fontsize,
                                 fontname=fontname, border_width=border))
    inset = _FREETEXT_INSET * border
    assert [(x, y) for x, y, _ in lines] == [
        pytest.approx((rect[0] + inset, rect[1] + inset + _FREETEXT_BASELINE * fontsize
                       + row * _FREETEXT_PITCH * fontsize), abs=0.01)
        for row in range(3)
    ]


# ---- the view puts it in the same place ---------------------------------------------------------


@pytest.mark.parametrize("box", [
    TextBox((150, 82, 230, 102), "JANE DOE", fontsize=12.0),                 # the reported box
    TextBox((100, 100, 400, 300), "one\ntwo\nthree", fontsize=24.0, fontname="tiro"),
    TextBox((100, 100, 400, 300), "bordered\nnote", fontsize=8.0, fontname="cour",
            border_width=2.0),
], ids=["reported", "three-lines", "bordered"])
def test_the_view_puts_each_line_where_the_file_does(win, box):
    painted = _painted_lines(win, box)
    saved = [(x, y) for x, y, _ in _saved_lines(box)]
    assert painted == [pytest.approx(line, abs=0.01) for line in saved]


def test_a_fixed_width_box_wraps_inside_the_file_inset(win, monkeypatch):
    """PyMuPDF wraps at the box's width less the inset on each side, which is no inset at all
    without a border. The view wrapped 2 pt in from each side, so a line that fits the file could
    break in the view."""
    ov = win.view.annotations
    widths = []
    wrap = ov._wrap_textbox_lines
    monkeypatch.setattr(ov, "_wrap_textbox_lines",
                        lambda text, fontname, fontsize, avail: widths.append(avail)
                        or wrap(text, fontname, fontsize, avail))
    wrapped = []
    for border in (0.0, 2.0):
        _painted_lines(win, TextBox((100, 100, 300, 160), "some words to wrap", border_width=border,
                                    auto_width=False))
        wrapped.append(widths[-1])            # a repaint wraps every box again; the newest is last
    assert wrapped == [pytest.approx(200.0), pytest.approx(200.0 - 3 * 2.0)]


# ---- a box holds the lines the file draws -------------------------------------------------------


@pytest.mark.parametrize("border", [0.0, 2.0])
def test_a_long_note_sized_by_the_width_handle_keeps_its_last_line(win, border):
    """The file clips its text to the box, and the width handle sized the box from Qt's line
    spacing, which is tighter than the file's. On Windows a 12 pt box lost the bottom of its last
    line from five lines on; the headless font's spacing is looser, so 30 lines are needed here.

    Every line has to be counted, not only the last one seen: a line wholly outside the clip is not
    extracted at all, and with a border the 30th line was, so the 29th looked like the last."""
    box = TextBox((100, 100, 300, 120), "\n".join(["gjpqy Jay"] * 30), fontsize=12.0,
                  border_width=border, auto_width=False)
    rect = win.view.annotations._reflow_textbox_rect(box, 200.0, 0)
    saved = _saved_lines(TextBox(rect, box.text, fontsize=12.0, border_width=border))
    assert len(saved) == 30
    assert saved[-1][2] <= rect[3] - border / 2


def test_a_long_note_typed_in_the_editor_keeps_its_last_line(win):
    """The editor's own padding, divided by the zoom, adds to the height, so the gap shows at a
    high zoom; at a low one the padding alone covered it."""
    win.view.set_zoom(2.0)
    win.view.arm(ArmedTool.TEXTBOX)
    ov = win.view.annotations
    ov.set_current_style(TextBoxStyle(fontsize=12.0))
    ov.place_textbox(win.view.scene_rect_for_box(0, (60, 60, 80, 80)).center())
    ov._editor.setPlainText("\n".join(["gjpqy Jay"] * 40))
    rect = ov._editor_rect
    saved = _saved_lines(TextBox(rect, ov._editor.toPlainText(), fontsize=12.0))
    assert len(saved) == 40
    assert saved[-1][2] <= rect[3]


# ---- the editor ---------------------------------------------------------------------------------


@pytest.mark.parametrize("zoom", [1.0, 1.3, 1.75, 2.4])
@pytest.mark.parametrize("border", [0.0, 2.0])
def test_the_editor_shows_the_text_where_it_will_stay(win, zoom, border):
    """While typing, the first line sits where the committed box, and the file, will put it, so
    committing the edit does not move the text. It used to sit 6 pt lower and 5 pt further right
    at 12 pt and 100%.

    A widget sits on whole pixels, so half a pixel is the most it can miss by. The first version
    allowed a pixel and still failed at 175% on Linux, by 0.016 px. It rounded the box's corner and
    its offset separately, and the test's own target a third time."""
    view = win.view
    view.set_zoom(zoom)
    view.arm(ArmedTool.TEXTBOX)
    ov = view.annotations
    ov.set_current_style(TextBoxStyle(fontsize=12.0, border_width=border))
    ov.place_textbox(view.scene_rect_for_box(0, (100, 120, 120, 140)).center())
    editor = ov._editor
    editor.setPlainText("JANE DOE")
    win.repaint()
    PdfApp.instance().processEvents()
    line = editor.cursorRect(editor.textCursor())
    origin = editor.viewport().mapTo(view.viewport(), line.topLeft())
    baseline = origin.y() + QFontMetricsF(editor.font()).ascent()
    x0, y0 = ov._editor_rect[0], ov._editor_rect[1]
    inset = _FREETEXT_INSET * border
    want = view.viewportTransform().map(view.scene_rect_for_box(
        0, (x0 + inset, y0 + inset + _FREETEXT_BASELINE * 12.0, x0 + inset + 1, y0 + 30)).topLeft())
    assert abs(origin.x() - want.x()) <= 0.5 + 1e-6
    assert abs(baseline - want.y()) <= 0.5 + 1e-6
