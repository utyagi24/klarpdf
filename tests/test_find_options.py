"""Find bar match options (PLAN.md §GUI feature roadmap → R6, M75). Offscreen GUI.

Match case + Whole words on the interactive FindBar — M64's existing ``SearchController.search``
filters (built for Find and Redact), finally surfaced on the bar. Next/prev, the count label and
the List All panel all operate on the filtered hit set; both toggles off is exactly the pre-M75
behaviour. A toggle re-runs the live query in place — no retyping.

Fixture text (conftest A.pdf): one word per page — "ALPHA-zero-A0" / "ALPHA-one-A1" /
"ALPHA-two-A2" — so "ALPHA" matches inside a longer word on every page (the whole-words case)
and lowercase queries exercise the case filter.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from store.settings import Settings


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


@pytest.fixture
def phrase_pdf(tmp_path) -> str:
    """Two pages carrying the owner's example: an "electric heater" phrase, plus an "electric"
    and a "heater" that never sit together."""
    path = str(tmp_path / "phrases.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "electric heater in the hall", fontsize=11)
    doc.new_page().insert_text((72, 100), "an electric fan and a gas heater", fontsize=11)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def phrase_win(qapp, phrase_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs2.json")
    w = qapp.open_document(phrase_pdf)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _hits(win) -> int:
    return win.view.search.position()[1]


def test_both_off_is_the_pre_m75_behaviour(win):
    """Case-insensitive, matches inside words — exactly what the bar always did."""
    win.find_bar.show_bar()
    win.find_bar._edit.setText("alpha")          # wrong case, partial word
    assert _hits(win) == 3                       # one hit per page, all found anyway


def test_match_case_filters_interactively(win):
    win.find_bar.show_bar()
    win.find_bar._edit.setText("alpha")
    win.find_bar._case_box.setChecked(True)      # toggle re-runs the live query (no retyping)
    assert _hits(win) == 0
    win.find_bar._edit.setText("ALPHA")
    assert _hits(win) == 3                       # exact case matches (still partial-word)


def test_whole_words_filters_interactively(win):
    win.find_bar.show_bar()
    win.find_bar._edit.setText("ALPHA")
    assert _hits(win) == 3                       # inside "ALPHA-zero-A0" etc.
    win.find_bar._word_box.setChecked(True)
    assert _hits(win) == 0                       # partial-word hits are gone
    win.find_bar._edit.setText("ALPHA-zero-A0")
    assert _hits(win) == 1                       # the whole word still matches


def test_label_and_navigation_follow_the_filtered_set(win):
    win.find_bar.show_bar()
    win.find_bar._word_box.setChecked(True)
    win.find_bar._edit.setText("ALPHA-one-A1")
    assert win.find_bar._label.text() == "1 of 1"
    win.find_bar.find_next()                     # wraps within the filtered set
    assert win.view.search.position() == (0, 1)


def test_list_all_panel_lists_only_filtered_hits(win):
    win.find_bar.show_bar()
    win.find_bar._edit.setText("ALPHA")
    win.find_bar._list_btn.setChecked(True)      # opens the results panel
    assert win.search_results.count() == 3
    win.find_bar._word_box.setChecked(True)      # live panel follows the toggle
    assert win.search_results.count() == 0
    win.find_bar._edit.setText("ALPHA-two-A2")
    assert win.search_results.count() == 1


def test_toggles_survive_hide_and_revive_with_the_query(win):
    """hide_bar keeps the query text; reopening re-runs it under the kept toggles."""
    win.find_bar.show_bar()
    win.find_bar._case_box.setChecked(True)
    win.find_bar._edit.setText("alpha")
    assert _hits(win) == 0
    win.find_bar.hide_bar()
    win.find_bar.show_bar()                      # revives the kept query…
    assert win.find_bar._case_box.isChecked()    # …under the kept option
    assert _hits(win) == 0


def test_whole_words_is_words_versus_phrase(phrase_win):
    """Off, a multi-word query is a **list of words** and any of them matches on its own; on, it is
    one unit — the phrase, as whole words (M75.1, owner's expectation)."""
    bar = phrase_win.find_bar
    bar.show_bar()
    bar._edit.setText("electric heater")
    assert _hits(phrase_win) == 4                # both "electric"s and both "heater"s
    bar._word_box.setChecked(True)
    assert _hits(phrase_win) == 1                # the phrase alone, on page 1
    assert phrase_win.view.search.hits()[0][0] == 0


def test_single_word_queries_are_unchanged(phrase_win):
    """The word/phrase split only bites on multi-word queries — one word still means what it did:
    substring off, whole word on."""
    bar = phrase_win.find_bar
    bar.show_bar()
    bar._edit.setText("heat")
    assert _hits(phrase_win) == 2                # inside "heater", both pages
    bar._word_box.setChecked(True)
    assert _hits(phrase_win) == 0                # …and not as a whole word anywhere


def test_multi_word_hits_come_in_reading_order(phrase_win):
    """Each word is searched separately, so the hits are re-ordered per page — next/prev must walk
    the page the way it is read, not all the "heater"s after all the "electric"s."""
    bar = phrase_win.find_bar
    bar.show_bar()
    bar._edit.setText("heater electric")         # query order is irrelevant
    hits = phrase_win.view.search.hits()
    assert [page for page, _box, _snip in hits] == [0, 0, 1, 1]
    assert hits[0][1][0] < hits[1][1][0]         # "electric" before "heater" on page 1
    assert hits[2][1][0] < hits[3][1][0]         # …and on page 2


def test_hit_verbs_are_dead_without_results(win):
    """Previous / Next / List All act on hits, so with none there is nothing to click."""
    bar = win.find_bar
    bar.show_bar()
    assert not bar._prev_btn.isEnabled()         # nothing searched yet
    assert not bar._next_btn.isEnabled()
    assert not bar._list_btn.isEnabled()
    bar._edit.setText("ALPHA")
    assert bar._prev_btn.isEnabled() and bar._next_btn.isEnabled() and bar._list_btn.isEnabled()
    bar._list_btn.setChecked(True)
    assert win.search_results.isVisible()
    bar._edit.setText("NOTHING-IN-HERE")
    assert bar._label.text() == "No results"
    assert not bar._prev_btn.isEnabled()
    assert not bar._next_btn.isEnabled()
    assert not bar._list_btn.isEnabled()
    assert not win.search_results.isVisible()    # the empty band goes with them
    bar._edit.setText("ALPHA")                   # …and comes back, listing, with the hits
    assert bar._list_btn.isEnabled()
    assert win.search_results.isVisible() and win.search_results.count() == 3


def test_find_and_redact_dialog_is_unaffected(win, monkeypatch):
    """M64's dialog drives the same controller with its own checkboxes — the bar's toggles must
    not leak into it. (The dialog constructs its own state; this pins the seam stays separate.)"""
    from ui.redact_matches_dialog import RedactMatchesDialog

    win.find_bar.show_bar()
    win.find_bar._case_box.setChecked(True)      # bar option on…
    dialog = RedactMatchesDialog(win, win.view)
    assert dialog.case_sensitive.isChecked() is False   # …dialog default untouched
    assert dialog.whole_word.isChecked() is False
    dialog.deleteLater()
