"""A word wider than its box breaks across lines, and a box has room for the lines it shows (M166,
#435).

A word wider than a fixed-width box was left whole, so it ran past the box: KlarPDF showed it, and
the file's drawing, which is clipped to the box, cut it off in every viewer. It now breaks between
its characters, a box is never narrower than its widest character, and a box grows taller when its
lines would be clipped, which breaking a word can make of a box read back from a file. The owner
chose this over keeping words whole (2026-11-07). The bridge's side is in
``tests/test_mcp_textbox_narrow.py``. Mostly headless; the editor tests run offscreen.
"""

from __future__ import annotations

import dataclasses
import random

import pymupdf as fitz
import pytest

from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.page_edits import (
    KLARPDF_AUTHOR,
    TextBox,
    _DESCENT,
    apply_annotations,
    fitted_to_its_lines,
    read_klarpdf_annotations,
    textbox_height,
    textbox_lines,
    textbox_min_width,
    textbox_pad,
    wrap_text,
)
from klarpdf.model.virtual_document import VirtualDocument


def _width(text: str, size: float = 12.0, font: str = "helv") -> float:
    return fitz.get_text_length(text, fontname=font, fontsize=size)


def _letters(text: str) -> str:
    return "".join(text.split())


def _old_box(path: str, rect: tuple, text: str, fontsize: float = 12.0, border: float = 0.0) -> str:
    """One of our boxes as an earlier version saved it: drawn by PyMuPDF alone, which leaves a word
    wider than the box whole, running past its clipped edge."""
    doc = fitz.open()
    page = doc.new_page()
    annot = page.add_freetext_annot(fitz.Rect(rect), text, fontsize=fontsize, fontname="helv",
                                    border_width=border)
    annot.set_info(title=KLARPDF_AUTHOR)
    annot.update()
    doc.save(path)
    doc.close()
    return path


def _read(path: str) -> TextBox:
    with fitz.open(path) as doc:
        (box,) = read_klarpdf_annotations(doc[0])
    return box


def _nudge(path: str, out: str, dy: float = 1.0) -> None:
    vdoc = VirtualDocument.from_path(path)
    (old,) = vdoc.page_annotations(0)
    x0, y0, x1, y1 = old.rect
    vdoc.replace_annotation(0, old, dataclasses.replace(old, rect=(x0, y0 + dy, x1, y1 + dy)))
    PyMuPDFEngine().materialize(vdoc, out)


def _shown(path: str) -> str:
    """What a viewer can see of the box, as the drawing's clip leaves it."""
    with fitz.open(path) as doc:
        page = doc[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        return annot.get_text("text")


def _ink_inside(path: str) -> bool:
    """Whether every letter's ink lies inside the box's clip, to the hundredth of a point. Text
    extraction alone cannot say: MuPDF still reports a letter whose tail the clip cuts off."""
    with fitz.open(path) as doc:
        page = doc[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        half = (annot.border or {}).get("width", 0.0) / 2
        right, bottom = annot.rect.x1 - half, annot.rect.y1 - half
        chars = [c for block in annot.get_text("rawdict", flags=fitz.TEXT_ACCURATE_BBOXES)["blocks"]
                 for line in block.get("lines", []) for span in line["spans"] for c in span["chars"]
                 if not c["c"].isspace()]
        return bool(chars) and all(c["bbox"][2] <= right + 0.01 and c["bbox"][3] <= bottom + 0.01
                                   for c in chars)


def _saved(box: TextBox, path: str) -> str:
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (box,))
    doc.save(path)
    doc.close()
    return path


# ---- breaking a word -------------------------------------------------------------------------


def test_a_word_wider_than_the_room_breaks_into_pieces_that_fit():
    lines = wrap_text("11/06/2026", "helv", 12, 30.0)
    assert len(lines) > 1
    assert "".join(lines) == "11/06/2026"
    assert all(_width(line) <= 30.0 for line in lines)


def test_the_next_word_joins_the_last_piece_when_it_fits():
    room = 45.0
    pieces = wrap_text("11/06/2026", "helv", 12, room)
    assert len(pieces) > 1 and _width(pieces[-1] + " ok") <= room, "the case does not test this"
    assert wrap_text("11/06/2026 ok", "helv", 12, room) == pieces[:-1] + [pieces[-1] + " ok"]


def test_an_indent_stays_on_the_first_piece():
    lines = wrap_text("  Extraordinary", "helv", 12, 40.0)
    assert lines[0].startswith("  ") and not lines[1].startswith(" ")
    assert "".join(line.strip() for line in lines) == "Extraordinary"


def test_a_piece_is_never_empty_even_when_a_character_is_too_wide():
    lines = wrap_text("WWW", "helv", 12, 3.0)
    assert lines == ["W", "W", "W"]


def test_words_that_fit_still_wrap_between_words():
    assert wrap_text("one two three", "helv", 12, _width("one two") + 1) == ["one two", "three"]


# ---- saving it -------------------------------------------------------------------------------


def test_a_long_word_in_a_fixed_width_box_saves_whole(tmp_path):
    text = "Extraordinary word"
    box = fitted_to_its_lines(TextBox((100.0, 100.0, 140.0, 180.0), text, fontsize=12,
                                      auto_width=False))
    path = _saved(box, str(tmp_path / "long.pdf"))
    assert _letters(_shown(path)) == _letters(text)
    assert _ink_inside(path)
    with fitz.open(path) as doc:
        page = doc[0]
        assert page.first_annot._getAP().count(b"Tj") == len(textbox_lines(box))


def test_a_box_an_earlier_version_cut_off_gets_its_letters_back(tmp_path):
    """The owner's form: 27 pt wide, three words each wider than its room. Each breaks in two, so the
    box grows to hold six lines instead of losing the bottom three."""
    text = "ABCDEF ABCDEF ABCDEF"
    assert _width("ABCDEF") > 27 - 2 * textbox_pad(1.0)
    src = _old_box(str(tmp_path / "old.pdf"), (150, 82, 177, 123), text, border=1.0)
    assert _letters(_shown(src)) != _letters(text), "fixture lost nothing"
    box = _read(src)
    assert not box.auto_width
    assert box.rect[3] > 123
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out)
    assert _letters(_shown(out)) == _letters(text)
    assert _ink_inside(out)


def test_a_grown_box_holds_the_tails_of_its_last_line(tmp_path):
    """Letters that reach below the line (g j p q y) on the last line are the first a short box
    cuts, and the last a growth that stops short would show."""
    text = "gyppqj gyppqj gyppqj"
    assert _width("gyppqj") > 27 - 2 * textbox_pad(1.0)
    src = _old_box(str(tmp_path / "old.pdf"), (150, 82, 177, 123), text, border=1.0)
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out)
    assert _letters(_shown(out)) == _letters(text)
    assert _ink_inside(out)


def test_a_wide_box_with_long_words_from_a_test_file(tmp_path):
    """The owner's Testing_PR436 shape: 36 pt words in a 105.7 pt box."""
    text = "Some quite extraordinarily lengthy words in here"
    src = _old_box(str(tmp_path / "old.pdf"), (41.7, 96.1, 147.4, 400), text, fontsize=36, border=1.0)
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out)
    assert _letters(_shown(out)) == _letters(text)
    assert _ink_inside(out)


# ---- the minimum width -----------------------------------------------------------------------


def test_a_box_narrower_than_its_widest_character_is_widened():
    box = TextBox((100.0, 100.0, 110.0, 200.0), "WAW", fontsize=36, auto_width=False)
    fitted = fitted_to_its_lines(box)
    assert fitted.rect[2] - fitted.rect[0] >= _width("W", 36) + 2 * textbox_pad(0.0)
    assert fitted.rect[:2] == box.rect[:2]


def test_the_minimum_is_the_widest_character_of_the_box_s_own_text():
    narrow = TextBox((0.0, 0.0, 50.0, 50.0), "iii", fontsize=36, auto_width=False)
    wide = TextBox((0.0, 0.0, 50.0, 50.0), "iWi", fontsize=36, auto_width=False)
    assert textbox_min_width(narrow) < textbox_min_width(wide)
    assert textbox_min_width(wide) == pytest.approx(_width("W", 36) + 2 * textbox_pad(0.0), abs=0.02)


# ---- the height ------------------------------------------------------------------------------


def test_a_line_cut_off_but_still_partly_visible_comes_back(tmp_path):
    """The issue's own output (#430): two lines drawn in a 20 pt box, the second half visible. It
    reads as wrapped on purpose, and now grows to show both."""
    width = _width("JANE DOE")
    src = _old_box(str(tmp_path / "half.pdf"), (150, 82, 150 + width - 0.5, 102), "JANE DOE")
    box = _read(src)
    assert not box.auto_width
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out)
    assert _letters(_shown(out)) == "JANEDOE"
    assert _ink_inside(out)


def test_a_box_with_room_for_its_lines_keeps_its_height(tmp_path):
    """The owner's 13.5 pt box holds its one line, so it does not grow."""
    width = _width("JANE DOE")
    path = _old_box(str(tmp_path / "fits.pdf"), (150, 82, 150 + width + 5, 95.5), "JANE DOE")
    box = _read(path)
    assert box.rect[3] - box.rect[1] == pytest.approx(13.5, abs=0.01)


def test_growing_keeps_the_text_where_it_was(tmp_path):
    src = _old_box(str(tmp_path / "old.pdf"), (150, 82, 177, 123), "ABCDEF ABCDEF ABCDEF", border=1.0)
    with fitz.open(src) as doc:
        page = doc[0]
        first = next(line for block in page.first_annot.get_text("rawdict")["blocks"]
                     for line in block.get("lines", []))["spans"][0]["chars"][0]["origin"]
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out, dy=0.0001)
    with fitz.open(out) as doc:
        page = doc[0]
        again = next(line for block in page.first_annot.get_text("rawdict")["blocks"]
                     for line in block.get("lines", []))["spans"][0]["chars"][0]["origin"]
    assert again == pytest.approx(first, abs=0.01)


def test_a_blank_last_line_asks_for_no_room():
    """Only lines with ink count: a trailing newline draws nothing to clip."""
    box = TextBox((100.0, 100.0, 200.0, 100.0 + textbox_height(1, 12, 0.0)), "JANE\n", fontsize=12,
                  auto_width=True)
    assert fitted_to_its_lines(box).rect[3] == pytest.approx(box.rect[3])


def test_repeated_saves_do_not_grow_it(tmp_path):
    path = _old_box(str(tmp_path / "old.pdf"), (150, 82, 177, 123), "ABCDEF ABCDEF ABCDEF", border=1.0)
    heights = []
    for save in range(4):
        out = str(tmp_path / f"save{save}.pdf")
        _nudge(path, out)
        box = _read(out)
        heights.append(box.rect[3] - box.rect[1])
        path = out
    assert max(heights) - min(heights) < 0.001


def test_a_box_laid_out_afresh_always_holds_its_lines():
    """textbox_height always holds centred lines, so a box KlarPDF sized is never grown."""
    rng = random.Random(435)
    for _case in range(300):
        font = rng.choice(["helv", "tiro", "cour"])
        size = rng.uniform(6, 96)
        border = rng.choice([0.0, 0.5, 1.0, 2.5, 6.0])
        lines = rng.randint(1, 8)
        text = "\n".join(["Agjpqy|"] * lines)
        box = TextBox((50.0, 50.0, 50.0 + 2000, 50.0 + textbox_height(lines, size, border)), text,
                      fontsize=size, fontname=font, border_width=border, auto_width=True)
        assert fitted_to_its_lines(box) == box, (font, size, border, lines)


@pytest.mark.parametrize("fontname", ["helv", "tiro", "cour"])
def test_the_descents_are_what_the_fonts_draw(fontname):
    """Measured once and kept as constants; a PyMuPDF that draws deeper fails here with the reason."""
    chars = "".join(chr(c) for c in range(33, 256) if chr(c).isprintable() and not chr(c).isspace())
    doc = fitz.open()
    page = doc.new_page(width=14000, height=400)
    page.insert_text((10, 200), chars, fontname=fontname, fontsize=100)
    deepest = max((c["bbox"][3] - c["origin"][1]) / 100
                  for block in page.get_text("rawdict", flags=fitz.TEXT_ACCURATE_BBOXES)["blocks"]
                  for line in block.get("lines", []) for span in line["spans"] for c in span["chars"])
    assert deepest <= _DESCENT[fontname] + 0.001
    assert deepest >= _DESCENT[fontname] - 0.005


# ---- in the app ------------------------------------------------------------------------------


@pytest.fixture
def win(tmp_path):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from app import PdfApp
    from main_window import MainWindow
    from store.settings import Settings

    app = PdfApp.instance() or PdfApp([])
    app.settings = Settings(tmp_path / "vs.json")
    app.page_clipboard = []
    path = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    w = MainWindow(app, path, app.settings)
    w.show()
    QTest.qWaitForWindowExposed(w)
    w.activateWindow()
    QApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def test_the_width_handle_stops_at_the_widest_character(win):
    box = TextBox((100.0, 100.0, 300.0, 200.0), "WAW", fontsize=72, auto_width=False)
    assert textbox_min_width(box) > 40, "the app's own 40 pt floor would hide the check"
    rect = win.view.annotations._reflow_textbox_rect(box, 1.0, 0)
    assert rect[2] - rect[0] >= textbox_min_width(box) - 0.001


def test_raising_the_font_widens_a_box_too_narrow_for_a_character(win):
    from dataclasses import replace

    from PySide6.QtWidgets import QApplication

    from viewer.text_format_bar import TextBoxStyle

    win.vdoc.add_annotation(0, TextBox((100.0, 100.0, 140.0, 140.0), "WAW", fontsize=12,
                                       auto_width=False))
    win.view.reload()
    (box,) = win.vdoc.page_annotations(0)
    overlay = win.view.annotations
    assert overlay.edit_textbox_at(win.view.scene_rect_for_box(0, (103, 110, 103.01, 110.01)).center())
    QApplication.processEvents()
    overlay._on_style_changed(replace(TextBoxStyle.from_textbox(box), fontsize=72.0))
    QApplication.processEvents()
    win.view.setFocus()
    QApplication.processEvents()
    (after,) = win.vdoc.page_annotations(0)
    assert after.fontsize == 72.0
    assert after.rect[2] - after.rect[0] >= textbox_min_width(after) - 0.001
    lines = textbox_lines(after)
    assert after.rect[3] - after.rect[1] >= textbox_height(len(lines), 72.0, 0.0) - 0.01


def test_growth_stops_at_the_bottom_of_the_page(tmp_path):
    """The owner's Testing_PR436 box needed nearly three pages at 36 pt and 43.7 pt wide. A box
    grows no lower than the page, as the editor and the width handle already stop there."""
    text = " ".join(["Extraordinarily"] * 12)
    src = _old_box(str(tmp_path / "tall.pdf"), (41.7, 600, 85.4, 700), text, fontsize=36, border=1.0)
    with fitz.open(src) as doc:
        page_height = doc[0].rect.height
    box = _read(src)
    assert 700 < box.rect[3] <= page_height + 0.01


def test_the_view_cuts_text_off_where_the_file_does(win):
    """Lines that cannot fit once the box reaches the page bottom are cut off at the box less half
    its outline, as the saved drawing's clip cuts them, not shown in KlarPDF and lost in the file."""
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QGraphicsItem

    win.vdoc.add_annotation(0, TextBox((100.0, 100.0, 200.0, 140.0), "x", border_width=2.0))
    win.view.reload()
    (frame,) = [i for i in win.view.annotations._items if i.childItems()]
    assert frame.flags() & QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape
    assert frame.shape().boundingRect() == QRectF(101.0, 101.0, 98.0, 38.0)
