"""Live thumbnails (PLAN.md, M28). Offscreen GUI.

The Pages sidebar renders each thumbnail from the page's **current edited state** (annotations /
redactions / fills), via the shared ``render_output`` bake — so a redacted/annotated page's
thumbnail shows the edit, matching the page and the saved output. A clean document keeps the fast
source-render path.
"""

from __future__ import annotations

import pytest

from app import PdfApp
from model.page_edits import Highlight, Redaction, TextBox
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
    w.undo_stack.setClean()  # avoid the dirty-close prompt blocking teardown
    w.close()


def _thumb(panel, index):
    return panel.item(index).icon().pixmap(panel.iconSize())


def _dark_fraction(pixmap) -> float:
    """Fraction of (sampled) thumbnail pixels that are near-black — a redaction's opaque box."""
    img = pixmap.toImage()
    if img.isNull():
        return 0.0
    dark = total = 0
    for y in range(0, img.height(), 3):
        for x in range(0, img.width(), 3):
            c = img.pixelColor(x, y)
            total += 1
            if c.red() < 70 and c.green() < 70 and c.blue() < 70:
                dark += 1
    return dark / max(1, total)


def _diff_fraction(a, b) -> float:
    """Fraction of (sampled) pixels that differ between two same-size thumbnails."""
    ia, ib = a.toImage(), b.toImage()
    if ia.isNull() or ib.isNull() or ia.size() != ib.size():
        return 1.0
    diff = total = 0
    for y in range(0, ia.height(), 3):
        for x in range(0, ia.width(), 3):
            total += 1
            if ia.pixelColor(x, y) != ib.pixelColor(x, y):
                diff += 1
    return diff / max(1, total)


def test_clean_document_uses_the_source_fast_path(win):
    assert win.thumbs._edited_render() is None  # no edits → no bake, render straight from source


def test_edited_document_builds_a_baked_render(win):
    win.vdoc.add_annotation(0, Highlight(((72, 72, 200, 90),)))
    baked = win.thumbs._edited_render()
    try:
        assert baked is not None
        assert baked.page_count == win.vdoc.page_count  # page i == ordered[i]
    finally:
        if baked is not None:
            baked.close()


def test_redaction_shows_as_a_black_box_in_the_thumbnail(win):
    before = _dark_fraction(_thumb(win.thumbs, 0))
    win.vdoc.add_annotation(0, Redaction(((50, 50, 500, 700),)))  # large opaque box over page 0
    win.thumbs.populate()
    after = _dark_fraction(_thumb(win.thumbs, 0))
    assert after > before + 0.1            # the baked redaction darkens the thumbnail
    assert _dark_fraction(_thumb(win.thumbs, 1)) < 0.1   # an unedited page is untouched


def test_highlight_changes_the_thumbnail(win):
    before = _thumb(win.thumbs, 0)
    win.vdoc.add_annotation(0, Highlight(((72, 66, 400, 92),)))  # a band over page 0's text
    win.thumbs.populate()
    # Rendering is deterministic, so a clean-vs-clean diff is exactly 0 — any change is the highlight.
    assert _diff_fraction(before, _thumb(win.thumbs, 0)) > 0.005


def test_textbox_changes_the_thumbnail(win):
    before = _thumb(win.thumbs, 0)
    win.vdoc.add_annotation(0, TextBox((120, 300, 360, 340), "NOTE", fill_color=(0.9, 0.9, 0.2)))
    win.thumbs.populate()
    assert _diff_fraction(before, _thumb(win.thumbs, 0)) > 0.01


def test_form_fill_marks_the_document_edited(win):
    assert win.thumbs._edited_render() is None
    win.vdoc.set_field_value("name", "FILLED-IN-THUMBNAIL")
    baked = win.thumbs._edited_render()
    try:
        assert baked is not None  # a fill bakes via render_output too
    finally:
        if baked is not None:
            baked.close()


def test_edit_then_undo_returns_thumbnail_to_clean(win):
    """The sidebar repopulates on every undo/redo, so removing the edit restores the clean render."""
    clean = _thumb(win.thumbs, 0)
    win.vdoc.add_annotation(0, Redaction(((50, 50, 500, 700),)))
    win.thumbs.populate()
    assert _diff_fraction(clean, _thumb(win.thumbs, 0)) > 0.1
    win.vdoc.clear_annotations(0)
    win.thumbs.populate()
    assert _diff_fraction(clean, _thumb(win.thumbs, 0)) < 0.02  # back to the clean render
