"""Search-all results panel (PLAN.md §GUI feature roadmap, M47). Offscreen GUI.

Every hit gets a context snippet; the FindBar's List All toggle shows a doc-wide hit list
("p. N   …snippet…"), a click jumps to that hit, and the panel follows the query, next/prev,
and edits. Hidden until asked for (no dead chrome); M64 later extends it with checkboxes.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from store.settings import Settings
from viewer.search import _snippet_for


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    for w in list(qapp._windows.values()):
        w.close()
    qapp._windows.clear()
    yield qapp
    for w in list(qapp._windows.values()):
        w.undo_stack.setClean()
        w.close()
    qapp._windows.clear()


@pytest.fixture
def prose_pdf(tmp_path) -> str:
    """3 pages; 'needle' appears on pages 1 and 3 (page 3 twice, one inside a long line)."""
    path = str(tmp_path / "prose.pdf")
    doc = fitz.open()
    p0 = doc.new_page()
    p0.insert_text((72, 100), "a needle in the first haystack", fontsize=12)
    doc.new_page().insert_text((72, 100), "nothing to see on this page", fontsize=12)
    p2 = doc.new_page()
    p2.insert_text((72, 100), "one two three four five six needle seven eight nine ten", fontsize=9)
    p2.insert_text((72, 140), "needle again", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def _win(app, path):
    w = app.open_document(path)
    app.processEvents()
    return w


def _rows(win) -> list[str]:
    return [win.search_results.item(i).text() for i in range(win.search_results.count())]


# ---- snippets -------------------------------------------------------------------


def test_hits_carry_line_snippets(app, prose_pdf):
    win = _win(app, prose_pdf)
    assert win.view.search.search("needle") == 3
    snippets = [s for _p, _b, s in win.view.search.hits()]
    assert snippets[0] == "a needle in the first haystack"  # whole (short) line, no ellipses
    assert "needle" in snippets[1]
    assert snippets[2] == "needle again"


def test_long_line_snippet_is_windowed_with_ellipses(app, prose_pdf):
    win = _win(app, prose_pdf)
    win.view.search.search("needle")
    long_snippet = win.view.search.hits()[1][2]
    # ±4 words around the match, both sides trimmed: "… three four five six needle seven eight nine ten"
    assert long_snippet.startswith("… ")
    assert "three four five six needle seven eight nine ten" in long_snippet
    assert "one two" not in long_snippet


def test_snippet_for_empty_when_box_misses_all_words():
    assert _snippet_for([(0, 0, 10, 10, "word", 0, 0, 0)], (500, 500, 520, 520)) == ""


# ---- the results panel ----------------------------------------------------------


def test_list_all_populates_and_click_jumps(app, prose_pdf):
    win = _win(app, prose_pdf)
    win.find_bar.show_bar()
    win.find_bar._edit.setText("needle")     # types the query → live search
    win.find_bar._list_btn.setChecked(True)  # List All → the panel appears, populated
    assert win.search_results.isVisible()
    rows = _rows(win)
    assert len(rows) == 3
    assert rows[0].startswith("p. 1") and rows[1].startswith("p. 3") and rows[2].startswith("p. 3")
    win.search_results._on_item_clicked(win.search_results.item(1))  # a row click
    assert win.view.search.position()[0] == 1  # that hit is now current…
    page_index, box, _ = win.view.search.hits()[1]
    viewport_scene = win.view.mapToScene(win.view.viewport().rect()).boundingRect()
    # …and revealed: the same ensureVisible contract as next/prev (scrolls enough to show the
    # hit — the viewport *centre*, which defines current_page, may lag a page behind).
    assert viewport_scene.intersects(win.view.scene_rect_for_box(page_index, box))


def test_panel_follows_the_query_as_typed(app, prose_pdf):
    win = _win(app, prose_pdf)
    win.find_bar.show_bar()
    win.find_bar._list_btn.setChecked(True)
    win.find_bar._edit.setText("needle")
    assert len(_rows(win)) == 3
    win.find_bar._edit.setText("haystack")
    assert len(_rows(win)) == 1
    win.find_bar._edit.setText("zzz-no-match")
    assert _rows(win) == []


def test_next_prev_track_the_current_row(app, prose_pdf):
    win = _win(app, prose_pdf)
    win.find_bar.show_bar()
    win.find_bar._edit.setText("needle")
    win.find_bar._list_btn.setChecked(True)
    assert win.search_results.currentRow() == 0
    win.find_bar.find_next()
    assert win.search_results.currentRow() == 1
    win.find_bar.find_prev()
    assert win.search_results.currentRow() == 0


def test_closing_the_bar_hides_the_panel(app, prose_pdf):
    win = _win(app, prose_pdf)
    win.find_bar.show_bar()
    win.find_bar._edit.setText("needle")
    win.find_bar._list_btn.setChecked(True)
    assert win.search_results.isVisible()
    win.find_bar.hide_bar()
    assert not win.search_results.isVisible()
    assert not win.find_bar._list_btn.isChecked()  # reopening starts without the panel


def test_panel_hidden_until_list_all(app, prose_pdf):
    win = _win(app, prose_pdf)
    win.find_bar.show_bar()
    win.find_bar._edit.setText("needle")  # plain next/prev flow — no panel
    assert not win.search_results.isVisible()


def test_edit_clears_a_visible_panel(app, prose_pdf):
    win = _win(app, prose_pdf)
    win.find_bar.show_bar()
    win.find_bar._edit.setText("needle")
    win.find_bar._list_btn.setChecked(True)
    assert len(_rows(win)) == 3
    win._delete_rows([1])  # any structural edit invalidates the hits
    assert _rows(win) == []  # no stale rows pointing at remapped pages
