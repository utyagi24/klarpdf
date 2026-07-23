"""Sidebar chrome + on-demand tabs (PLAN.md §GUI feature roadmap → R6, M79.1). Offscreen GUI.

Three owner calls from the R6 test pass, all about the sidebar being *quieter*:

* **no title bar** — a strip reading "Sidebar" over the sidebar, with a ✕ duplicating the toolbar
  button, is chrome about chrome;
* **Pages alone by default** — Outline and Annotations no longer appear by themselves, so the
  panel is the same shape on every document;
* **a ▾ on the sidebar button** to ask for them, remembered app-wide — and offering only the tabs
  the open document could actually show, so a tick never produces nothing.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QStyleOptionToolButton, QTabWidget

from app import PdfApp
from model.page_edits import Highlight
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")   # a fresh store: no tabs asked for yet
    qapp.page_clipboard = []
    yield qapp
    for w in list(qapp._windows.values()):
        w.undo_stack.setClean()
        w.close()
    qapp._windows.clear()


def _tab_labels(win) -> list[str]:
    widget = win.pages_dock.widget()
    if not isinstance(widget, QTabWidget):
        return []
    return [widget.tabText(i) for i in range(widget.count())]


def _word_box(win, page_index=0) -> tuple:
    ref = win.vdoc.ordered[page_index]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    return tuple(page.get_text("words")[0][:4])


# ---- the panel's own chrome ---------------------------------------------------


def test_the_sidebar_has_no_title_bar(app, a_pdf):
    """No "Sidebar" strip and no ✕ (M79.1): a label for the obvious, over a third way to do what
    the toolbar button and View ▸ Sidebar already do — and the only one that leaves no lit button
    behind to say how to get the panel back."""
    win = app.open_document(a_pdf)
    bar = win.pages_dock.titleBarWidget()
    assert bar is not None                       # an empty widget *is* the "no title bar" idiom
    assert bar.maximumHeight() == 0              # …and this one cannot claim a strip
    assert win.pages_dock.windowTitle() == "Sidebar"   # kept for screen readers, never drawn


# ---- Pages by default, the rest on demand -------------------------------------


def test_pages_only_by_default(app, a_pdf):
    """a_pdf has an outline; before M79.1 that alone mounted a tab bar."""
    win = app.open_document(a_pdf)
    assert _tab_labels(win) == []                # the bare Pages panel, no tab bar at all
    assert win.pages_dock.widget() is win.thumbs
    assert win.outline is None


def test_asking_for_the_outline_tab_mounts_it(app, a_pdf):
    win = app.open_document(a_pdf)
    win._sidebar_tab_actions["outline"].setChecked(True)
    assert _tab_labels(win) == ["Pages", "Outline"]
    assert win.outline is not None
    win._sidebar_tab_actions["outline"].setChecked(False)
    assert _tab_labels(win) == []                # …and back to the bare panel


def test_the_choice_is_remembered_across_documents(app, a_pdf, b_pdf):
    win = app.open_document(a_pdf)
    win._sidebar_tab_actions["outline"].setChecked(True)
    assert app.settings.get_pref("sidebar_tabs") == ["outline"]
    other = app.open_document(b_pdf)             # a second window reads the same store
    other._add_annotation(0, Highlight((_word_box(other),)))
    assert _tab_labels(other) == []              # B.pdf has no outline, and no Annotations asked
    assert other.annotations_panel is None       # …even though it now has a markup
    assert "outline" in other._sidebar_tabs_wanted()   # the ask itself carried over


def test_a_remembered_ask_mounts_the_tab_when_the_document_is_opened(app, a_pdf):
    """The preference decides what the sidebar holds at *open* — that is the whole point of
    remembering it. It is only mid-session that it stops mounting things (M79.3)."""
    app.settings.set_pref("sidebar_tabs", ["outline"])
    win = app.open_document(a_pdf)
    assert _tab_labels(win) == ["Pages", "Outline"]
    assert win._sidebar_tab_actions["outline"].isChecked()


def test_asking_for_a_tab_opens_a_hidden_sidebar(app, a_pdf):
    """Asking for a tab is asking to see it — the alternative is a menu item that appears to do
    nothing whenever the panel happens to be closed."""
    win = app.open_document(a_pdf)
    win.pages_dock.setVisible(False)
    win._sidebar_tab_actions["outline"].setChecked(True)
    assert win.pages_dock.isVisible()
    assert app.settings.get_pref("sidebar_visible") is True


def test_annotations_tab_needs_both_the_ask_and_the_marks(app, a_pdf):
    """At open (and at any other mount): asked for *and* something to list."""
    app.settings.set_pref("sidebar_tabs", ["annotations"])
    clean = app.open_document(a_pdf)
    assert _tab_labels(clean) == []              # asked for, but the document is clean
    marked = app.open_document(a_pdf)
    marked._add_annotation(0, Highlight((_word_box(marked),)))
    marked._mount_sidebar()                      # …as a reload or a ▾ tick would
    assert _tab_labels(marked) == ["Pages", "Annotations"]


def test_a_new_mark_offers_the_tab_but_never_mounts_it(app, a_pdf):
    """The owner's call (M79.3): marking up a page must not push a panel into the sidebar under
    your hands. The first mark makes the ▾ entry offerable — taking it up is the reader's move."""
    app.settings.set_pref("sidebar_tabs", ["annotations"])   # …even with the ask already stored
    win = app.open_document(a_pdf)
    win._add_annotation(0, Highlight((_word_box(win),)))
    assert _tab_labels(win) == []                            # no tab arrived
    assert win.annotations_panel is None
    entry = win._sidebar_tab_actions["annotations"]
    assert entry.isVisible()                                 # …but the ▾ now offers one
    assert not entry.isChecked()          # and reads as what the sidebar shows, not what is stored
    entry.setChecked(True)                                   # the reader takes it up
    assert _tab_labels(win) == ["Pages", "Annotations"]


def test_an_emptied_tab_folds_away_and_the_undo_brings_it_back(app, a_pdf):
    """Deleting the last mark *through* the tab must not leave the empty panel sitting there — and
    undoing that deletion has to bring the panel back with the mark, or the reader is left holding
    a restored annotation and no list (owner, both halves)."""
    win = app.open_document(a_pdf)
    win._add_annotation(0, Highlight((_word_box(win),)))
    win._sidebar_tab_actions["annotations"].setChecked(True)
    assert _tab_labels(win) == ["Pages", "Annotations"]
    win.undo_stack.undo()                                    # the last mark goes…
    assert _tab_labels(win) == []                            # …and takes the empty tab with it
    assert win.annotations_panel is None
    win.undo_stack.redo()
    assert _tab_labels(win) == ["Pages", "Annotations"]       # the window was carrying it
    assert win.annotations_panel.count() == 1


def test_a_dismissed_tab_stays_dismissed_when_marks_return(app, a_pdf):
    """Folding away on empty is not the same as being put away by hand: once the reader unticks it,
    a later mark only offers it again."""
    win = app.open_document(a_pdf)
    win._add_annotation(0, Highlight((_word_box(win),)))
    entry = win._sidebar_tab_actions["annotations"]
    entry.setChecked(True)
    entry.setChecked(False)                                  # put away by hand
    win._add_annotation(1, Highlight((_word_box(win, 1),)))
    assert _tab_labels(win) == []
    assert entry.isVisible() and not entry.isChecked()        # offered, not imposed


# ---- the ▾ offers only what this document could show ---------------------------


def test_the_menu_offers_only_applicable_tabs(app, a_pdf):
    """A tick that produces nothing is worse than an absent entry — the reader can't tell whether
    the tab is off or the document simply has no outline."""
    win = app.open_document(a_pdf)               # outline yes, marks no
    assert win._sidebar_tab_actions["outline"].isVisible()
    assert not win._sidebar_tab_actions["annotations"].isVisible()
    win._add_annotation(0, Highlight((_word_box(win),)))
    assert win._sidebar_tab_actions["annotations"].isVisible()   # the first mark offers it


def _draws_arrow(win) -> bool:
    """Whether the sidebar button paints its split ▾ section — the *drawn* state, not the menu.

    The distinction is the whole of M79.2: a QToolButton draws the section from its popup mode, so
    ``setMenu(None)`` alone left an arrow that opened nothing."""
    option = QStyleOptionToolButton()
    win._sidebar_button.initStyleOption(option)
    return bool(option.features & QStyleOptionToolButton.ToolButtonFeature.MenuButtonPopup)


def test_no_arrow_at_all_when_nothing_applies(app, b_pdf):
    """B.pdf has no outline and no marks: the ▾ itself is the signal that there is a choice."""
    win = app.open_document(b_pdf)
    assert win._sidebar_button.menu() is None
    assert not _draws_arrow(win)                 # …and no dead arrow over the missing menu
    win._add_annotation(0, Highlight((_word_box(win),)))
    assert win._sidebar_button.menu() is not None
    assert _draws_arrow(win)


def test_the_arrow_takes_its_width_with_it(app, b_pdf):
    """The button re-measures on both flips (M79.2). Qt clears neither the stylesheet's
    ``::menu-button`` rule nor the cached sizeHint on a popup-mode change, so without the re-polish
    the returning arrow is drawn squeezed over the icon at the plain button's width."""
    win = app.open_document(b_pdf)
    win.show()
    app.processEvents()
    plain = win._sidebar_button.width()
    win._add_annotation(0, Highlight((_word_box(win),)))
    app.processEvents()
    split = win._sidebar_button.width()
    assert split > plain                         # room for the ▾, not a crammed overlay
    win.undo_stack.undo()
    app.processEvents()
    assert win._sidebar_button.width() == plain  # …and the room goes back


def test_the_entries_are_named_for_the_tabs_they_produce(app, a_pdf):
    """"Outline", not "Outline Tab" — the entry sits under the sidebar button with a tick beside
    it, and it now reads as the tab it puts there (owner call)."""
    win = app.open_document(a_pdf)
    assert [a.text() for a in win._sidebar_tab_menu.actions()] == ["Outline", "Annotations"]


def test_the_sidebar_button_still_shows_and_hides(app, a_pdf):
    """The face of the split button is the old toggle — the ▾ only picks what the panel holds."""
    win = app.open_document(a_pdf)
    toggle = win._sidebar_button.defaultAction()
    assert toggle.isCheckable()
    toggle.trigger()
    assert win.pages_dock.isVisible() is toggle.isChecked()
