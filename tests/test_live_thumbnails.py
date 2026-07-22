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
    panel._ensure_rendered(index)  # thumbnails render lazily; force this one for the pixel check
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


def test_thumbnail_width_stays_full_when_saved_rotated_page_is_rotated_back(qapp, tmp_path):
    """Pre-existing rotation-sizing bug: a page saved with /Rotate=90 then rotated back to portrait
    must size to the same width as a normal portrait page. The native pixmap is rotated by the
    override, so the thumbnail must be sized by its *displayed* width, not the native page.rect width
    (else it renders narrower than its neighbours)."""
    import pymupdf as fitz

    from model.virtual_document import VirtualDocument
    from organize.thumbnail_panel import ThumbnailPanel

    path = str(tmp_path / "rot.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792).set_rotation(90)  # saved rotated → would display landscape
    doc.new_page(width=612, height=792)                   # a normal portrait page
    doc.save(path)
    doc.close()
    vdoc = VirtualDocument.from_path(path)
    vdoc.set_rotation(0, 0)            # rotate page 0 back to portrait (absolute override 0)
    panel = ThumbnailPanel(vdoc)
    try:
        panel._ensure_rendered(0)
        panel._ensure_rendered(1)
        back = panel.item(0).icon().pixmap(panel.iconSize())
        norm = panel.item(1).icon().pixmap(panel.iconSize())
        assert abs(back.width() - norm.width()) <= 1   # full width, not shrunk
        assert back.height() > back.width()            # and portrait again
    finally:
        panel.deleteLater()


def test_edit_then_undo_returns_thumbnail_to_clean(win):
    """The sidebar repopulates on every undo/redo, so removing the edit restores the clean render."""
    clean = _thumb(win.thumbs, 0)
    win.vdoc.add_annotation(0, Redaction(((50, 50, 500, 700),)))
    win.thumbs.populate()
    assert _diff_fraction(clean, _thumb(win.thumbs, 0)) > 0.1
    win.vdoc.clear_annotations(0)
    win.thumbs.populate()
    assert _diff_fraction(clean, _thumb(win.thumbs, 0)) < 0.02  # back to the clean render


# ---- an edit must not blank the sidebar (owner-reported, M69.2) ------------------
#
# Rendering is lazy, but `populate()` runs on *every* edit and used to reset every row to a blank
# grey placeholder — so one edit emptied the whole sidebar and only the rows on screen came back.
# Anything scrolled away stayed an empty rectangle until the user happened to scroll to it.


def _is_blank_placeholder(panel, row) -> bool:
    """Whether row ``row`` is the grey placeholder rather than a page render.

    Keyed on the placeholder's own fill colour, not on "the image is flat": a page that is mostly
    white *is* flat over most sampling grids, so flatness would call every clean page a placeholder.
    """
    image = panel.item(row).icon().pixmap(panel.iconSize()).toImage()
    sampled = {image.pixel(x, y)
               for x in range(0, image.width(), 7)
               for y in range(0, image.height(), 11)}
    return sampled == {0xFFECECEC}          # ThumbnailPanel._placeholder_icon's fill


def test_an_edit_does_not_blank_the_thumbnails_it_did_not_re_render(win):
    """The reported symptom: rows outside the viewport turned into empty grey rectangles."""
    panel = win.thumbs
    for row in range(panel.count()):
        panel._ensure_rendered(row)
    assert not any(_is_blank_placeholder(panel, row) for row in range(panel.count()))

    win.vdoc.add_annotation(0, Highlight(((50, 50, 200, 70),)))
    panel.populate()

    assert not any(_is_blank_placeholder(panel, row) for row in range(panel.count())),         "an edit blanked a thumbnail instead of carrying its previous render"


def test_a_carried_thumbnail_is_still_due_a_real_render(win):
    """Carrying supplies a *placeholder*, not an answer: the row is not marked rendered, so it
    refreshes when looked at. Otherwise the sidebar would go permanently stale, not momentarily."""
    panel = win.thumbs
    for row in range(panel.count()):
        panel._ensure_rendered(row)
    clean = _thumb(panel, 0)
    carried = panel._carryable_icons()
    assert set(carried) == set(range(panel.count()))     # every rendered row can be carried

    win.vdoc.add_annotation(0, Redaction(((50, 50, 500, 700),)))
    panel.populate()
    assert _diff_fraction(clean, _thumb(panel, 0)) > 0.1  # the re-render carries the edit


def test_a_structural_edit_carries_nothing(win):
    """A carried icon belongs to the page that was in that row. Reorder the pages and the old image
    is a *different page* — an honest placeholder beats a confident lie."""
    panel = win.thumbs
    for row in range(panel.count()):
        panel._ensure_rendered(row)
    assert panel._carryable_icons() != {}
    win.vdoc.ordered.reverse()
    assert panel._carryable_icons() == {}


def test_a_rotation_carries_nothing(win):
    """Rotation changes the row's shape as well as its pixels, so a carried icon would be the wrong
    aspect — the layout key covers it alongside reorder and crop."""
    panel = win.thumbs
    for row in range(panel.count()):
        panel._ensure_rendered(row)
    win.vdoc.set_rotation(0, 90)
    assert panel._carryable_icons() == {}
