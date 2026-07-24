"""Live search stays responsive on a large document (owner-reported hang). Offscreen GUI.

Two independent faults made find-as-you-type unusable on a 320-page file, and the tests here pin
both plus the correctness bug the first one was hiding:

* **Per-hit page re-extraction.** Every hit re-scanned its page — the snippet three times over the
  word list, and Match case through ``page.get_textbox``, which re-extracts the page's text on each
  call (~31 ms). A one-letter query has ~72 000 hits on that document, so a *single keystroke* with
  Match case on cost ~37 minutes. :class:`~viewer.search._PageText` indexes the page once.
* **A full-document scan per keystroke.** Typing a five-letter word ran five scans, the most
  expensive of them (the one-letter prefix) first. The bar now debounces.

And the correctness half: ``get_textbox`` answers "what text is under this box?" by *clipping*, so
it sweeps in whatever else shares the box's band — on ordinary single-spaced text the line above
comes too. Match case compared that against the term and threw the hit away. Measured on
``spaceX_prospectus.pdf``, that lost 4 of 72 "SpaceX" hits and 84 of 2598 "the" hits.
"""

from __future__ import annotations

import time

import pymupdf as fitz
import pytest

from app import PdfApp
from model.page_text import PageText, boxes_touch
from store.settings import Settings
from viewer import search as search_mod


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def stacked_pdf(tmp_path) -> str:
    """Two ordinarily single-spaced lines (11 pt text, 11 pt leading). The upper line's glyph boxes
    reach into the lower line's band, which is all it takes for a clip-based read of the lower hit
    to come back as "Cla\\nSPX"."""
    path = str(tmp_path / "stacked.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Cla", fontsize=11)
    page.insert_text((72, 111), "SPX", fontsize=11)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def dense_pdf(tmp_path) -> str:
    """One page carrying many hits for a one-letter query — the shape that made the old per-hit
    re-extraction bite."""
    path = str(tmp_path / "dense.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for row in range(40):
        page.insert_text((40, 60 + row * 15), " ".join(["banana"] * 8), fontsize=9)
    doc.save(path)
    doc.close()
    return path


def _open(qapp, path, tmp_path, name="vs.json"):
    qapp.settings = Settings(tmp_path / name)
    win = qapp.open_document(path)
    win.show()
    qapp.processEvents()
    return win


@pytest.fixture
def stacked_win(qapp, stacked_pdf, tmp_path):
    win = _open(qapp, stacked_pdf, tmp_path)
    yield win
    win.undo_stack.setClean()
    win.close()


@pytest.fixture
def dense_win(qapp, dense_pdf, tmp_path):
    win = _open(qapp, dense_pdf, tmp_path, "vs2.json")
    yield win
    win.undo_stack.setClean()
    win.close()


def _spin(qapp, ms: int) -> None:
    """Pump the event loop for ``ms`` so real QTimers can fire."""
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)


# --- the hang: no per-hit page re-extraction -------------------------------------------------

def test_match_case_never_calls_get_textbox(dense_win, monkeypatch):
    """The hang, pinned at its source. ``get_textbox`` re-extracts the whole page per call, so one
    call per hit is what turned a keystroke into minutes. Not "fewer calls" — none."""
    calls = []
    monkeypatch.setattr(fitz.Page, "get_textbox",
                        lambda self, rect, **kw: calls.append(rect) or "")

    dense_win.find_bar.show_bar()
    dense_win.find_bar._case_box.setChecked(True)
    dense_win.find_bar._edit.setText("banana")

    assert dense_win.view.search.position()[1] == 320   # 8 per line × 40 lines, all found
    assert calls == []


def test_page_text_is_extracted_once_per_page_not_once_per_hit(dense_win, monkeypatch):
    """The page is indexed a fixed number of times however many hits it has — the property that
    makes the cost O(page) instead of O(page × hits)."""
    real = fitz.Page.get_text
    calls = []

    def counting(self, option="text", **kw):
        calls.append(option)
        return real(self, option, **kw)

    monkeypatch.setattr(fitz.Page, "get_text", counting)

    dense_win.find_bar.show_bar()
    dense_win.find_bar._case_box.setChecked(True)
    dense_win.find_bar._edit.setText("banana")

    assert dense_win.view.search.position()[1] == 320
    # One "words" pass + one "rawdict" pass for the single page that has hits.
    assert calls.count("words") == 1
    assert calls.count("rawdict") == 1


# --- the correctness half: a clip read a hit's neighbours as part of the hit ------------------

def test_match_case_keeps_a_hit_whose_neighbour_shares_its_band(stacked_win):
    """"SPX" is on the page in exactly that case, so Match case must keep it. Reading the box by
    clipping returned "Cla\\nSPX" — the line above — and the hit was dropped."""
    stacked_win.find_bar.show_bar()
    stacked_win.find_bar._case_box.setChecked(True)
    stacked_win.find_bar._edit.setText("SPX")

    assert stacked_win.view.search.position()[1] == 1


def test_match_case_still_rejects_a_genuine_case_mismatch(stacked_win):
    """The filter is tightened, not loosened: the page has no lowercase "spx"."""
    stacked_win.find_bar.show_bar()
    stacked_win.find_bar._case_box.setChecked(True)
    stacked_win.find_bar._edit.setText("spx")

    assert stacked_win.view.search.position()[1] == 0
    stacked_win.find_bar._case_box.setChecked(False)   # …and without the filter it is found
    assert stacked_win.view.search.position()[1] == 1


def test_page_index_agrees_with_a_full_scan(dense_pdf):
    """The per-line index is an optimisation, so it must return what scanning every word did."""
    doc = fitz.open(dense_pdf)
    page = doc[0]
    words = page.get_text("words")
    text = PageText(page)
    for r in page.search_for("nan"):                   # sub-word hits, one per "banana"
        box = (r.x0, r.y0, r.x1, r.y1)
        naive = [w for w in words if boxes_touch(w[:4], box)]
        assert [w for _i, w in text.struck(box)] == naive
        assert text.snippet(box) == search_mod._snippet_for(words, box)
        assert text.is_whole_word(box) == search_mod.is_whole_word(words, box)
    doc.close()


# --- the debounce ----------------------------------------------------------------------------

def test_typing_a_word_runs_one_search_not_one_per_keystroke(dense_win, qapp, monkeypatch):
    """The pathological search is the one-letter prefix nobody meant to run."""
    monkeypatch.setattr(search_mod, "SEARCH_DEBOUNCE_MS", 150)
    queries = []
    real = dense_win.view.search.search

    def recording(query, **kw):
        queries.append(query)
        return real(query, **kw)

    monkeypatch.setattr(dense_win.view.search, "search", recording)

    dense_win.find_bar.show_bar()
    for i in range(1, len("banana") + 1):
        dense_win.find_bar._edit.setText("banana"[:i])
        _spin(qapp, 20)                     # keystrokes land well inside the debounce gap
    assert queries == []                    # nothing has run yet
    _spin(qapp, 300)

    assert queries == ["banana"]            # …then exactly the query that was meant
    assert dense_win.view.search.position()[1] == 320


def test_enter_searches_what_was_typed_rather_than_waiting(dense_win, qapp, monkeypatch):
    """Enter mid-debounce must not navigate a stale (here: empty) hit set."""
    monkeypatch.setattr(search_mod, "SEARCH_DEBOUNCE_MS", 5000)   # long enough to never fire here
    dense_win.find_bar.show_bar()
    dense_win.find_bar._edit.setText("banana")
    assert dense_win.view.search.position()[1] == 0               # still pending

    dense_win.find_bar.find_next()                                # what returnPressed calls
    assert dense_win.view.search.position()[1] == 320


def test_closing_the_bar_drops_a_pending_search(dense_win, qapp, monkeypatch):
    """A queued keystroke must not repopulate highlights behind a closed bar."""
    monkeypatch.setattr(search_mod, "SEARCH_DEBOUNCE_MS", 100)
    dense_win.find_bar.show_bar()
    dense_win.find_bar._edit.setText("banana")
    dense_win.find_bar.hide_bar()
    _spin(qapp, 250)

    assert dense_win.view.search.position()[1] == 0
