"""Search & redact (PLAN.md §R4, M64). Offscreen GUI + headless leak verification.

Redact every occurrence of a string. The milestone's done-when is the whole flow:
**mark-all → review → redact-checked → Save removes them**, verified cross-engine, with warnings on
image-only pages.

The review step is the point. A search for ``Smith`` also finds ``Smithsonian``, and redaction is
destructive, so the hits arrive **ticked but prunable** and the two toggles (match case, whole words)
let the common false positives be excluded wholesale rather than one at a time.

Nothing the dialog does is destructive: checked hits become ordinary ``Redaction`` descriptors that
the existing confirmed Save applies. These tests hold that line — a marked-but-unsaved redaction must
still be undoable, and only the save may destroy anything.
"""

from __future__ import annotations

import shutil
import subprocess

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.page_edits import Redaction
from store.settings import Settings
from ui.redact_matches_dialog import RedactMatchesDialog, image_only_pages

TARGET = "Smith"
DECOY = "Smithsonian"
KEEP = "PUBLICINFO"


@pytest.fixture
def names_pdf(tmp_path) -> str:
    """Two pages: the target twice on page 0 (one of them inside a longer word), once on page 1."""
    path = str(tmp_path / "names.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), f"{TARGET} and {DECOY} and {KEEP}", fontsize=12)
    page.insert_text((60, 140), f"SMITH shouted at {TARGET}", fontsize=12)
    page = doc.new_page()
    page.insert_text((60, 100), f"{TARGET} again, plus {KEEP}", fontsize=12)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, names_pdf):
    w = MainWindow(app, names_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def dialog(win):
    d = RedactMatchesDialog(win, win.view)
    yield d
    win.view.search.clear()
    d.deleteLater()


def _redactions(win, page: int):
    return [a for a in win.vdoc.page_annotations(page) if isinstance(a, Redaction)]


def _all_boxes(win) -> list[tuple]:
    return [box for page in range(win.vdoc.page_count)
            for mark in _redactions(win, page) for box in mark.rects]


# ---- searching: the toggles -----------------------------------------------------


def test_a_plain_search_finds_every_occurrence(dialog):
    """Case-insensitive, substring — MuPDF's default, and the widest possible net by design."""
    dialog.query.setText(TARGET)
    # page 0: Smith, Smithsonian, SMITH, Smith  ·  page 1: Smith
    assert dialog.results.count() == 5


def test_whole_words_only_excludes_the_decoy(dialog, win):
    """The "untick Smithsonian" case, handled wholesale instead of row by row.

    Asserted on the hit *boxes*, not the row text: a snippet shows its neighbouring words, so every
    row on that line mentions "Smithsonian" whether or not it is the one that matched inside it.
    """
    dialog.query.setText(TARGET)
    # Keyed by (page, box): both pages carry a "Smith" at the same coordinates, so boxes alone
    # would collapse two distinct hits into one.
    before = {(page, box) for page, box, _s in win.view.search.hits()}
    dialog.whole_word.setChecked(True)
    after = {(page, box) for page, box, _s in win.view.search.hits()}
    assert len(before) == 5 and len(after) == 4
    _page, dropped = (before - after).pop()
    # The excluded hit is the one sitting inside a longer word.
    ref = win.vdoc.ordered[0]
    words = win.vdoc.sources[ref.source_id][ref.source_page_index].get_text("words")
    assert any(w[4] == DECOY and w[0] <= dropped[0] and w[2] > dropped[2] for w in words)


def test_match_case_excludes_the_shouted_one(dialog):
    dialog.query.setText(TARGET)
    dialog.case_sensitive.setChecked(True)
    assert dialog.results.count() == 4          # SMITH drops out; Smithsonian's "Smith" stays


def test_both_toggles_narrow_to_the_exact_word(dialog):
    dialog.query.setText(TARGET)
    dialog.case_sensitive.setChecked(True)
    dialog.whole_word.setChecked(True)
    assert dialog.results.count() == 3          # Smith x2 on p0, Smith x1 on p1


def test_no_matches_reports_plainly(dialog):
    dialog.query.setText("NOTHINGHERE")
    assert dialog.results.count() == 0
    assert "No matches" in dialog.count_label.text()
    assert dialog.redact_button.isEnabled() is False


def test_an_empty_query_marks_nothing(dialog):
    dialog.query.setText("")
    assert dialog.checked_hits() == []
    assert dialog.redact_button.isEnabled() is False


# ---- reviewing: ticked by default, prunable -------------------------------------


def test_hits_arrive_ticked(dialog):
    """Opt-out, not opt-in: the user asked for all of them, then prunes. Opt-in would make the
    common case (redact every one) a click per hit."""
    dialog.query.setText(TARGET)
    assert len(dialog.checked_hits()) == dialog.results.count() == 5


def test_unticking_a_row_excludes_it(dialog):
    from PySide6.QtCore import Qt

    dialog.query.setText(TARGET)
    decoy_row = next(r for r in range(dialog.results.count())
                     if DECOY in dialog.results.item(r).text())
    dialog.results.item(decoy_row).setCheckState(Qt.CheckState.Unchecked)
    assert len(dialog.checked_hits()) == 4


def test_select_none_and_all(dialog):
    dialog.query.setText(TARGET)
    dialog.results.set_all_checked(False)
    assert dialog.checked_hits() == []
    dialog.results.set_all_checked(True)
    assert len(dialog.checked_hits()) == 5


def test_hits_are_highlighted_on_the_page_while_reviewing(dialog, win):
    """The dialog drives the *real* search controller, so a doubtful hit can be inspected on the
    page before it is redacted — the reason review happens on M47's panel."""
    dialog.query.setText(TARGET)
    assert win.view.search.position()[1] == 5


# ---- marking: batched, undoable, not yet destructive ----------------------------


# Hit order is reading order per page, so for `names_pdf` searching "Smith" the rows are:
#   0 Smith (p0 l1) · 1 Smith-inside-Smithsonian (p0 l1) · 2 SMITH (p0 l2) · 3 Smith (p0 l2)
#   4 Smith (p1)
# Row 1 is the decoy. Unticking by row index rather than by snippet text, because every row on that
# line carries "Smithsonian" in its context snippet.
DECOY_ROW = 1


def _mark(win, monkeypatch, query: str, whole_word: bool = False, drop_rows=()):
    """Run the dialog headlessly: type, optionally untick rows, accept."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    def fake_exec(self):
        self.query.setText(query)
        self.whole_word.setChecked(whole_word)
        for row in drop_rows:
            self.results.item(row).setCheckState(Qt.CheckState.Unchecked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RedactMatchesDialog, "exec", fake_exec)
    win._redact_matches()


def test_marking_creates_one_redaction_per_page(win, monkeypatch):
    _mark(win, monkeypatch, TARGET)
    assert len(_redactions(win, 0)) == 1        # batched, not one descriptor per hit
    assert len(_redactions(win, 1)) == 1
    assert len(_redactions(win, 0)[0].rects) == 4
    assert len(_redactions(win, 1)[0].rects) == 1


def test_marking_is_one_undo_step(win, monkeypatch):
    _mark(win, monkeypatch, TARGET)
    assert _all_boxes(win)
    win.undo_stack.undo()
    assert _all_boxes(win) == []


def test_marking_is_not_yet_destructive(win, monkeypatch):
    """Only the confirmed Save may destroy anything — the marked state must stay reversible."""
    _mark(win, monkeypatch, TARGET)
    ref = win.vdoc.ordered[0]
    source = win.vdoc.sources[ref.source_id][ref.source_page_index]
    assert TARGET in source.get_text()          # the shared source is untouched
    assert win.vdoc.has_redactions() is True    # …and the save will be a point of no return


def test_cancelling_marks_nothing(win, monkeypatch):
    from PySide6.QtWidgets import QDialog

    monkeypatch.setattr(RedactMatchesDialog, "exec",
                        lambda self: (self.query.setText(TARGET), QDialog.DialogCode.Rejected)[1])
    win._redact_matches()
    assert _all_boxes(win) == []


def test_unticked_matches_are_left_alone(win, monkeypatch):
    _mark(win, monkeypatch, TARGET, drop_rows=(DECOY_ROW,))
    assert len(_redactions(win, 0)[0].rects) == 3   # the Smithsonian hit was pruned


# ---- saving: the text is actually gone (cross-engine) ---------------------------


def _materialize(win, tmp_path) -> str:
    out = str(tmp_path / "redacted.pdf")
    PyMuPDFEngine().materialize(win.vdoc, out)
    return out


def test_save_removes_every_marked_match(win, monkeypatch, tmp_path):
    """The done-when, end to end."""
    _mark(win, monkeypatch, TARGET, whole_word=True)
    saved = fitz.open(_materialize(win, tmp_path))
    try:
        text = "\n".join(page.get_text() for page in saved)
        assert "SMITH" not in text              # the shouted standalone one is gone
        assert DECOY in text                    # …and the longer word was never a whole-word match
        # Every standalone occurrence is gone; the only "Smith" left is inside "Smithsonian".
        assert text.count(TARGET) == 1
        assert KEEP in text                     # everything unrelated survives
    finally:
        saved.close()


def test_an_unticked_match_survives_the_save(win, monkeypatch, tmp_path):
    """The review step has to actually mean something in the output."""
    _mark(win, monkeypatch, TARGET, drop_rows=(DECOY_ROW,))
    saved = fitz.open(_materialize(win, tmp_path))
    try:
        assert DECOY in saved[0].get_text()
    finally:
        saved.close()


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler pdftotext not installed")
def test_no_leak_cross_engine(win, monkeypatch, tmp_path):
    """Verified with a *different* engine than the one that wrote the file — a PyMuPDF-only check
    could miss text its own writer hides from itself (the M21 discipline)."""
    _mark(win, monkeypatch, TARGET, whole_word=True)
    out = _materialize(win, tmp_path)
    extracted = subprocess.run(["pdftotext", out, "-"], capture_output=True, text=True).stdout
    assert "SMITH" not in extracted
    assert extracted.count(TARGET) == 1     # only the one inside "Smithsonian"
    assert KEEP in extracted


# ---- honesty: what it cannot reach ----------------------------------------------


def test_image_only_pages_are_detected(tmp_path):
    """A scanned page has nothing to search, and "0 matches" would otherwise read as "the word is
    not there" rather than "I cannot see the words at all"."""
    from model.virtual_document import VirtualDocument

    path = str(tmp_path / "mixed.pdf")
    doc = fitz.open()
    text_page = doc.new_page()
    text_page.insert_text((60, 100), "readable text", fontsize=12)
    scan = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
    pix.clear_with(128)
    scan.insert_image(fitz.Rect(50, 50, 250, 150), pixmap=pix)
    doc.new_page()                              # blank: no text, no images — not a warning
    doc.save(path)
    doc.close()

    vdoc = VirtualDocument.from_path(path)
    try:
        assert image_only_pages(vdoc) == [1]
    finally:
        vdoc.close()


def test_the_dialog_warns_about_image_only_pages(app, tmp_path):
    from PySide6.QtWidgets import QApplication

    path = str(tmp_path / "scan.pdf")
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
    pix.clear_with(128)
    page.insert_image(fitz.Rect(50, 50, 250, 150), pixmap=pix)
    doc.save(path)
    doc.close()

    window = MainWindow(app, path, app.settings)
    try:
        dlg = RedactMatchesDialog(window, window.view)
        try:
            assert dlg.warning.isHidden() is False
            assert "no text layer" in dlg.warning.text()
        finally:
            dlg.deleteLater()
            QApplication.processEvents()
    finally:
        window.undo_stack.setClean()
        window.close()


def test_no_warning_on_an_ordinary_text_document(dialog):
    assert dialog.warning.isHidden() is True


def test_the_dialog_states_its_limits(dialog):
    """The honesty budget is a UI requirement, not a docs one — assert the wording is on screen."""
    from PySide6.QtWidgets import QLabel

    shown = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "text layer only" in shown
    assert "form-field" in shown
    assert "permanently when you save" in shown


# ---- the whole-word predicate (headless) ----------------------------------------


def test_is_whole_word_rejects_a_substring_hit():
    from viewer.search import is_whole_word

    words = [(100.0, 10.0, 180.0, 22.0, DECOY, 0, 0, 0)]
    assert is_whole_word(words, (100.0, 10.0, 130.0, 22.0)) is False   # "Smith" inside it
    assert is_whole_word(words, (100.0, 10.0, 180.0, 22.0)) is True    # the whole word
