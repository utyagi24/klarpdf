"""Arrow ends as line style (PLAN.md §GUI feature roadmap → R6, M74). Headless + offscreen GUI.

Preview treats arrowheads as *line style*, and M74 adopts it: the Arrow tool is gone, ``Line``'s
ends (none · start · end · both) are set from the M59.5 style picker — which is what makes a
**both-ended** arrow drawable for the first time — and a selected line's ends restyle in place
like colour/width. Pre-R6 arrows (baked ``Line(arrow_end=True)``) reopen editable and unchanged;
the round-trip rides ``set_line_ends`` / ``annot.line_ends`` exactly as before.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from model.edit_engine import PyMuPDFEngine
from model.page_edits import Line, restyle_mark
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.markup_style import MarkupStyle, MarkupStyleButton


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


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


# ---- the model round-trip ----------------------------------------------------


@pytest.mark.parametrize("ends", [(False, False), (True, False), (False, True), (True, True)])
def test_every_ends_variant_bakes_and_reopens(tmp_path, a_pdf, ends):
    """Draw a line with any ends combination → save → reopen: the same descriptor comes back.
    (True, True) is the new capability — a both-ended arrow."""
    v1 = VirtualDocument.from_path(a_pdf)
    v1.add_annotation(0, Line((100, 200), (220, 260), arrow_start=ends[0], arrow_end=ends[1]))
    out = _materialize(v1, tmp_path)
    reopened = VirtualDocument.from_path(out)
    lines = [a for a in reopened.page_annotations(0) if isinstance(a, Line)]
    assert len(lines) == 1
    assert (lines[0].arrow_start, lines[0].arrow_end) == ends


def test_both_ended_line_carries_two_arrowheads_in_the_file(tmp_path, a_pdf):
    """The saved annotation itself carries both open-arrow ends (``/LE``), not just our model."""
    v1 = VirtualDocument.from_path(a_pdf)
    v1.add_annotation(0, Line((100, 200), (220, 260), arrow_start=True, arrow_end=True))
    out = _materialize(v1, tmp_path)
    with fitz.open(out) as doc:
        page = doc[0]  # hold the page, or the annot detaches mid-iteration ("not bound")
        ends = [a.line_ends for a in page.annots() if a.type[0] == fitz.PDF_ANNOT_LINE]
        assert ends == [(fitz.PDF_ANNOT_LE_OPEN_ARROW, fitz.PDF_ANNOT_LE_OPEN_ARROW)]


def test_pre_r6_arrow_reopens_editable_and_unchanged(tmp_path, a_pdf):
    """What the retired Arrow tool wrote — ``Line(arrow_end=True)`` — reads back exactly, still
    author-tagged and therefore editable. Nothing about the file format changed at M74."""
    v1 = VirtualDocument.from_path(a_pdf)
    v1.add_annotation(0, Line((100, 200), (200, 200), arrow_end=True))  # a pre-R6 "arrow"
    out = _materialize(v1, tmp_path)
    reopened = VirtualDocument.from_path(out)
    lines = [a for a in reopened.page_annotations(0) if isinstance(a, Line)]
    assert len(lines) == 1
    assert lines[0].arrow_end is True and lines[0].arrow_start is False
    # Editable means restylable: give the reopened arrow a start head too, in place.
    restyled = restyle_mark(lines[0], lines[0].color, lines[0].width, None,
                            lines[0].opacity, (True, True))
    assert restyled.arrow_start is True and restyled.arrow_end is True


# ---- restyle-in-place --------------------------------------------------------


def test_restyle_mark_changes_ends_and_none_keeps_them():
    line = Line((0, 0), (10, 0), arrow_end=True)
    both = restyle_mark(line, line.color, line.width, None, 1.0, (True, True))
    assert (both.arrow_start, both.arrow_end) == (True, True)
    kept = restyle_mark(line, (0, 0, 0), 4.0, None, 1.0, None)  # ends omitted → unchanged
    assert (kept.arrow_start, kept.arrow_end) == (False, True)
    assert kept.color == (0, 0, 0) and kept.width == 4.0


def test_selected_lines_ends_restyle_in_place_one_undo_step(win):
    """The picker path: select a drawn line, pick Both → the mark is replaced in place, one undo
    step, still selected — exactly like recolouring it (M59.5's contract)."""
    line = Line((100.0, 200.0), (220.0, 260.0))
    win.vdoc.add_annotation(0, line)
    win.view.reload()
    win.view.annotations.repaint()
    win.view.annotations.select_object(0, line)
    before = win.undo_stack.count()
    win._on_markup_style_changed(MarkupStyle(line_ends=(True, True)))
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Line)]
    assert len(marks) == 1
    assert (marks[0].arrow_start, marks[0].arrow_end) == (True, True)
    assert win.undo_stack.count() == before + 1
    win.undo_stack.undo()
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Line)]
    assert (marks[0].arrow_start, marks[0].arrow_end) == (False, False)


def test_selecting_a_line_loads_its_ends_into_the_picker(win):
    """M59.5's load-on-select covers ends too, so a follow-up tweak edits *that* line."""
    line = Line((100.0, 200.0), (220.0, 260.0), arrow_start=True, arrow_end=True)
    win.vdoc.add_annotation(0, line)
    win.view.reload()
    win.view.annotations.repaint()
    win._on_object_selected(line)
    assert win._markup_style_button.style().line_ends == (True, True)


def test_from_mark_reads_line_ends():
    style = MarkupStyle.from_mark(Line((0, 0), (10, 10), arrow_start=True))
    assert style.line_ends == (True, False)


# ---- the picker UI -----------------------------------------------------------


def test_picker_offers_the_four_ends_and_emits(qapp):
    button = MarkupStyleButton()
    labels = [a.text() for a in button._ends_menu.actions()]
    assert labels == ["None", "Start", "End", "Both"]
    seen = []
    button.styleChanged.connect(seen.append)
    button._set_ends((True, True))
    assert seen and seen[-1].line_ends == (True, True)
    button.set_style(MarkupStyle(line_ends=(False, True)))  # load without emitting
    assert button._ends_actions[(False, True)].isChecked()
    assert len(seen) == 1


# ---- the Arrow tool is gone --------------------------------------------------


def test_arrow_left_the_menus_and_the_draw_button(win):
    from viewer.tools import ArmedTool

    assert not hasattr(ArmedTool, "ARROW")
    for bar_action in win.menuBar().actions():
        if bar_action.text() == "&Tools" and bar_action.menu() is not None:
            texts = [a.text() for a in bar_action.menu().actions() if a.text()]
            assert "Arrow" not in texts
    assert "Arrow" not in [a.text() for a in win._draw_button.menu().actions()]
