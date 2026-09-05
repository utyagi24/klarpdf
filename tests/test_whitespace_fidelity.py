"""Whitespace fidelity (PLAN.md §M91, M91.1). Offscreen GUI.

One rule, three surfaces: **whitespace decides whether text exists, never how it looks.** A text box
paints its leading spaces where the saved file has them, the wrap keeps a paragraph's indent, and the
note / form-field paths stop rewriting content on its way into the model — while every *all-blank*
drop stays exactly as it was.

**No test here may assert an absolute text pixel offset.** The headless platform resolves no font at
all (``QFontInfo(qt_font("helv", 24)).family()`` is ``''``), so every glyph — the space included —
measures exactly one em against Helvetica's real 6.67 pt. An absolute offset would encode the tofu
fallback's metrics and pass for the wrong reason. The invariants below are relative: an indented line
starts further right than the same line unindented, by *that font's own* measured advance for the
indent.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSimpleTextItem

from app import PdfApp
from klarpdf.model.page_edits import Highlight, TextBox
from store.settings import Settings
from viewer.text_format_bar import qt_font


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


def _line_items(win) -> list:
    """The painted text items of the last text box, top line first.

    They are **children** of the box frame since M59.11 (so the text can never out-stack a mark
    covering the box), so they are reached through the frame rather than listed as top-level items.
    """
    boxes = [it for it in win.view.annotations._items if type(it) is QGraphicsRectItem]
    assert boxes, "no text-box frame was painted"
    children = [c for c in boxes[-1].childItems() if isinstance(c, QGraphicsSimpleTextItem)]
    return sorted(children, key=lambda it: it.pos().y())


def _paint(win, text: str, rect=(100.0, 100.0, 400.0, 160.0), **kw):
    win.vdoc.add_annotation(0, TextBox(rect, text, **kw))
    win.view.annotations.repaint()
    return _line_items(win)


# ---- the paint (M91.1) ----------------------------------------------------------


def test_a_leading_indent_moves_the_ink_right(win):
    """The reported defect. ``QGraphicsSimpleTextItem`` reserves leading whitespace in its bounding
    rect but paints the glyphs flush left, so the single-item overlay put the indent on the *right*
    as slack while the saved file baked ``(    hello) Tj``. One item per line, indent paid as an x
    advance, is what makes the overlay agree with the file."""
    plain = _paint(win, "hello")[0].pos().x()
    indented = _paint(win, "    hello")[0].pos().x()
    fm = QFontMetricsF(qt_font("helv", 11.0))
    assert indented - plain == pytest.approx(fm.horizontalAdvance("    "), abs=0.5)


def test_the_indent_is_not_slack_on_the_right(win):
    """The other half of the same defect: the ink used to stop short of where the box ends by
    exactly the indent it had reserved. Now the indented and the unindented box ink the *same*
    glyphs at the same width — only the start moves."""
    # Read each paint's values before the next repaint — a repaint destroys the items of the last.
    item = _paint(win, "hello")[0]
    plain_text, plain_x, plain_w = item.text(), item.pos().x(), item.boundingRect().width()
    item = _paint(win, "    hello")[0]
    assert plain_text == item.text() == "hello"                        # not in the string…
    assert item.pos().x() > plain_x                                    # …but in the position
    assert item.boundingRect().width() == pytest.approx(plain_w)       # and nothing reserved


def test_each_line_carries_its_own_indent(win):
    """One item per line is what lets two lines be indented differently at all — a single item can
    only be positioned once."""
    lines = _paint(win, "flush\n    indented\nflush again")
    assert [it.text() for it in lines] == ["flush", "indented", "flush again"]
    assert lines[1].pos().x() > lines[0].pos().x()
    assert lines[2].pos().x() == pytest.approx(lines[0].pos().x())


def test_the_lines_stack_by_the_font_line_spacing(win):
    """Vertical centring switched from the single item's bounding rect to ``len(lines) *
    lineSpacing()``, since no one item spans the box any more."""
    lines = _paint(win, "one\ntwo\nthree")
    fm = QFontMetricsF(qt_font("helv", 11.0))
    gaps = [b.pos().y() - a.pos().y() for a, b in zip(lines, lines[1:])]
    assert all(gap == pytest.approx(fm.lineSpacing(), abs=0.01) for gap in gaps)


def test_a_blank_line_spaces_without_painting(win):
    """A blank line is vertical space, not an item: it still pushes the next line down by one line
    of spacing, but there is nothing to paint for it."""
    lines = _paint(win, "before\n\nafter")
    fm = QFontMetricsF(qt_font("helv", 11.0))
    assert [it.text() for it in lines] == ["before", "after"]
    assert lines[1].pos().y() - lines[0].pos().y() == pytest.approx(2 * fm.lineSpacing(), abs=0.01)


# ---- the wrap (M91.1) ----------------------------------------------------------


def test_the_wrap_keeps_a_paragraph_indent(win):
    """``"    a b".split(" ")`` yields empty tokens that the ``if current and …`` guard discarded, so
    the fixed-width path destroyed the indent of *every* paragraph — reached after a width-handle
    drag (M78.3) and for any reopened box whose text does not fit one line."""
    ov = win.view.annotations
    lines, _fm = ov._wrap_textbox_lines("    indented first\nflush second", "helv", 11.0, 400.0)
    assert lines == ["    indented first", "flush second"]


def test_a_wrapped_indent_lands_on_the_first_line_only(win):
    """A continuation line is not separately indented — the indent belongs to the paragraph's first
    line, and it is charged against the available width there, so that line wraps earlier."""
    ov = win.view.annotations
    lines, fm = ov._wrap_textbox_lines("        alpha beta gamma delta epsilon", "helv", 11.0, 60.0)
    assert len(lines) > 1
    assert lines[0].startswith("        ")
    assert not any(line.startswith(" ") for line in lines[1:])
    assert " ".join(lines).split() == ["alpha", "beta", "gamma", "delta", "epsilon"]
    assert fm.horizontalAdvance(lines[0].split()[0]) > 0           # the font measured something


def test_a_width_dragged_box_keeps_its_indents(win):
    """The width handle reflows through the same wrap, so the indents survive the drag."""
    ov = win.view.annotations
    box = TextBox((100.0, 100.0, 400.0, 160.0), "    indented and long enough to fold once or twice",
                  auto_width=False)
    win.vdoc.add_annotation(0, box)
    lines, _fm = ov._wrap_textbox_lines(box.text, box.fontname, box.fontsize, 80.0)
    assert lines[0].startswith("    ")
    painted = _paint(win, "    indented and long enough to fold once or twice",
                     rect=(100.0, 200.0, 190.0, 280.0), auto_width=False)
    assert painted[0].pos().x() > painted[1].pos().x()              # first line indented, rest not


# ---- the all-blank drops, unchanged (M91.1) ------------------------------------


def test_an_all_whitespace_box_is_still_dropped(win):
    """The governing rule's other half: whitespace still decides *existence*. ``_commit_textbox``
    keeps ``raw`` and strips only to answer empty-vs-not, which is the shape the other sites copy."""
    ov = win.view.annotations
    ov.place_textbox(win.view.scene_rect_for_box(0, (60, 90, 80, 110)).center())
    ov._editor.setPlainText("     ")
    ov._commit_textbox()
    assert [a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox)] == []


def test_a_note_keeps_its_indentation(win):
    """``_commit_note`` used to ``strip()`` the note on its way into the model, so an indented
    remark was quietly rewritten."""
    win.vdoc.add_annotation(0, Highlight(((100.0, 100.0, 200.0, 112.0),)))
    mark = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)][-1]
    win._commit_note(0, tuple(mark.rects), mark, "    indented note  ")
    noted = [a for a in win.vdoc.page_annotations(0) if getattr(a, "note", "")]
    assert [a.note for a in noted] == ["    indented note  "]


def test_an_all_whitespace_note_still_drops_the_note_and_keeps_the_mark(win):
    """M90.1's rule, untouched by the fidelity fix: a note is a field of its mark, so clearing it
    removes the note and not the mark."""
    win.vdoc.add_annotation(0, Highlight(((100.0, 100.0, 200.0, 112.0),)))
    mark = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)][-1]
    win._commit_note(0, tuple(mark.rects), mark, "keep me")
    mark = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)][-1]
    win._commit_note(0, tuple(mark.rects), mark, "   ")
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)]
    assert len(marks) == 1 and marks[0].note == ""


def test_a_form_field_keeps_its_value_but_not_its_name(win):
    """A field *name* is an identifier — matched, warned about on a clash, required by OK — so it
    keeps its strip. The default *value* is content the user typed."""
    from ui.field_dialog import FieldDialog

    dialog = FieldDialog(win, "text")
    dialog.name.setText("  full_name  ")
    dialog.value.setText("  Ada  ")
    field = dialog.field()
    dialog.deleteLater()
    assert field.name == "full_name"
    assert field.value == "  Ada  "
