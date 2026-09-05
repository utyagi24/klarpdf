"""Search returns the page's printed content-stream text only (PLAN §Future enhancements →
Direction A). Offscreen GUI.

PyMuPDF's ``search_for`` pulls FreeText annotation text **and** AcroForm field values into its
text layer alongside real body text; Preview and Edge search neither. So search now drops any hit
landing on a FreeText annotation (our text boxes or foreign) or a form-field widget, keeping only
the printed content. This is what resolves the owner-reported trio: a typed text box is never a
hit (live or baked), so a saved-then-reopened box isn't found and a *moved* one no longer matches
its stale baked location.

Markup annotations (highlight / underline / strikeout) add no text and sit *over* real content, so
a hit beneath one stays findable. And a content-only edit (a highlight) no longer blanks the
results list — only a structural edit (delete / reorder), where the page-index-keyed hits go stale.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from klarpdf.model.edit_commands import AddAnnotationCommand, DeleteCommand
from klarpdf.model.page_edits import Highlight
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def opener(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    qapp.object_clipboard = []
    opened = []

    def _open(path):
        w = qapp.open_document(path)
        w.show()
        qapp.processEvents()
        opened.append(w)
        return w

    yield _open
    for w in opened:
        w.undo_stack.setClean()
        w.close()


def _pdf(tmp_path, name, build) -> str:
    doc = fitz.open()
    build(doc)
    path = str(tmp_path / name)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def overlay_pdf(tmp_path):
    """One page carrying a distinct word in each layer: body (content stream), a FreeText
    annotation (a typed text box), and a filled AcroForm text field."""
    def build(doc):
        p = doc.new_page()
        p.insert_text((72, 100), "alpha bravo charlie", fontsize=14)   # content stream
        a = p.add_freetext_annot(fitz.Rect(72, 300, 320, 340), "delta echo", fontsize=14)
        a.update()
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_name = "field1"
        w.field_value = "foxtrot"
        w.rect = fitz.Rect(72, 400, 320, 430)
        p.add_widget(w)
    return _pdf(tmp_path, "overlay.pdf", build)


@pytest.fixture
def highlighted_pdf(tmp_path):
    """Body text with a highlight annotation baked over one word."""
    def build(doc):
        p = doc.new_page()
        p.insert_text((72, 100), "golf hotel india", fontsize=14)
        a = p.add_highlight_annot(p.search_for("golf"))
        a.update()
    return _pdf(tmp_path, "highlighted.pdf", build)


@pytest.fixture
def multipage_pdf(tmp_path):
    """Three pages, the same body word on each."""
    def build(doc):
        for _ in range(3):
            doc.new_page().insert_text((72, 100), "alpha", fontsize=14)
    return _pdf(tmp_path, "multi.pdf", build)


# ---- what search excludes ----------------------------------------------------


def test_body_text_is_found(opener, overlay_pdf):
    win = opener(overlay_pdf)
    assert win.view.search.search("alpha") == 1
    assert win.view.search.search("charlie") == 1


def test_text_box_content_is_not_found(opener, overlay_pdf):
    """A FreeText annotation's text — our text boxes and foreign ones alike. This is the trio's
    root: a typed box never becomes a hit, so save+reopen and move-to-another-page both behave."""
    win = opener(overlay_pdf)
    assert win.view.search.search("delta") == 0
    assert win.view.search.search("echo") == 0


def test_form_field_value_is_not_found(opener, overlay_pdf):
    win = opener(overlay_pdf)
    assert win.view.search.search("foxtrot") == 0


def test_highlighted_body_text_stays_findable(opener, highlighted_pdf):
    """A highlight adds no text and sits over real content, so the word under it is still a hit."""
    win = opener(highlighted_pdf)
    assert win.view.search.search("golf") == 1
    assert win.view.search.search("india") == 1


# ---- results survive a content edit, drop on a structural one ----------------


def test_a_content_edit_keeps_the_results(opener, multipage_pdf):
    """A highlight (content-only) leaves every hit where it was — the results list must persist,
    not blank (the owner-reported #1)."""
    win = opener(multipage_pdf)
    assert win.view.search.search("alpha") == 3
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    rects = tuple(tuple(r) for r in page.search_for("alpha"))
    win.undo_stack.push(AddAnnotationCommand(win.vdoc, 0, Highlight(rects=rects)))
    assert win.view.search.position()[1] == 3   # kept, not cleared


def test_a_structural_edit_clears_the_results(opener, multipage_pdf):
    """Deleting a page remaps page indices, so the page-index-keyed hits are stale and dropped."""
    win = opener(multipage_pdf)
    assert win.view.search.search("alpha") == 3
    win.undo_stack.push(DeleteCommand(win.vdoc, [1]))
    assert win.view.search.position()[1] == 0
