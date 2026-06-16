"""Offscreen tests for M3 text selection + search (no real display)."""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QGuiApplication

from app import PdfApp
from model.virtual_document import VirtualDocument
from viewer.pdf_view import PdfView
from viewer.search import FindBar, SearchController
from viewer.text_selection import TextSelection


@pytest.fixture(scope="session")
def qapp():
    app = PdfApp.instance() or PdfApp([])
    yield app


@pytest.fixture
def text_pdf(tmp_path) -> str:
    """A 2-page doc with known words; 'world' appears on both pages (2 search hits)."""
    path = str(tmp_path / "text.pdf")
    doc = fitz.open()
    p0 = doc.new_page()
    p0.insert_text((72, 100), "Hello world foo", fontsize=12)
    p0.insert_text((72, 140), "bar baz qux", fontsize=12)
    p1 = doc.new_page()
    p1.insert_text((72, 100), "world again here", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def _view(qapp, path) -> PdfView:
    view = PdfView(VirtualDocument.from_path(path))
    view.selection = TextSelection(view)
    view.search = SearchController(view)
    return view


def _word_box(view: PdfView, page_index: int, word: str) -> tuple:
    ref = view._vdoc.ordered[page_index]
    page = view._vdoc.sources[ref.source_id][ref.source_page_index]
    for w in page.get_text("words"):
        if w[4] == word:
            return (w[0], w[1], w[2], w[3])
    raise AssertionError(f"word {word!r} not found on page {page_index}")


def _scene_center(view: PdfView, page_index: int, word: str) -> QPointF:
    return view.scene_rect_for_box(page_index, _word_box(view, page_index, word)).center()


# ---- selection --------------------------------------------------------------


def test_select_single_word(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    assert view.selection.begin(_scene_center(view, 0, "world")) is True
    assert view.selection.selected_text() == "world"


def test_select_word_range_same_line(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    view.selection.begin(_scene_center(view, 0, "Hello"))
    view.selection.update_to(_scene_center(view, 0, "foo"))
    assert view.selection.selected_text() == "Hello world foo"


def test_select_across_lines_inserts_newline(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    view.selection.begin(_scene_center(view, 0, "Hello"))
    view.selection.update_to(_scene_center(view, 0, "bar"))
    text = view.selection.selected_text()
    assert "\n" in text
    assert text.startswith("Hello") and text.endswith("bar")


def test_copy_puts_selection_on_clipboard(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    view.selection.begin(_scene_center(view, 0, "Hello"))
    view.selection.update_to(_scene_center(view, 0, "foo"))
    assert view.selection.copy() is True
    assert QGuiApplication.clipboard().text() == "Hello world foo"


def test_copy_with_no_selection_is_noop(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    assert view.selection.copy() is False


def test_selection_disabled_when_rotated(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    center = _scene_center(view, 0, "world")  # compute before rotating (geometry is rot-0)
    view.rotate_view(90)
    assert view.selection.begin(center) is False


def test_selection_highlights_rescale_on_zoom(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    view.selection.begin(_scene_center(view, 0, "Hello"))
    view.selection.update_to(_scene_center(view, 0, "foo"))
    n = len(view.selection._items)
    assert n == 3  # Hello world foo
    view.set_zoom(2.0)  # triggers _build_scene -> overlay repaint
    assert len(view.selection._items) == n  # survived rebuild


# ---- search -----------------------------------------------------------------


def test_search_finds_all_and_navigates(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    count = view.search.search("world")
    assert count == 2
    assert view.search.position() == (0, 2)
    view.search.next()
    assert view.search.position() == (1, 2)
    view.search.next()  # wraps
    assert view.search.position() == (0, 2)
    view.search.prev()  # wraps backward
    assert view.search.position() == (1, 2)


def test_search_no_results(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    assert view.search.search("zzzznotpresent") == 0
    assert view.search.position() == (-1, 0)


def test_search_clear_removes_highlights(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    view.search.search("world")
    assert len(view.search._items) == 2
    view.search.clear()
    assert view.search._items == []


def test_findbar_label_reports_matches(qapp, text_pdf):
    view = _view(qapp, text_pdf)
    bar = FindBar(view)
    bar._edit.setText("world")  # textChanged → search + label
    assert bar._label.text() == "1 of 2"
    bar.find_next()
    assert bar._label.text() == "2 of 2"
    bar._edit.setText("nope")
    assert bar._label.text() == "No results"
