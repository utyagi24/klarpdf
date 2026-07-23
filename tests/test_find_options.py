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
