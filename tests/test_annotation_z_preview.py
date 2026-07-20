"""Preview paint order follows the model's z-order (PLAN.md §GUI feature roadmap, M59.11 — R3).

M59.8 established that a page's annotation tuple **is** its z-order: later entries paint on top,
in the saved PDF (``apply_annotations`` writes in order) and in the hit-test (which walks the
tuple reversed). The *preview* did not follow: it gave each mark a fixed z by **type** — highlight
6, drawn marks and the text-box frame 7, the text-box's text 8 — so a filled shape over a text box
covered the box's fill (tie at 7) but its text showed straight through, and the z-order verbs were
visually inert between marks of different types.

These tests pin the preview to the file: same tuple, same stacking.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.page_edits import (
    Highlight,
    Shape,
    TextBox,
    apply_annotations,
)
from store.settings import Settings
from viewer.annotations import _ANNOT_Z_BASE, _REDACTION_Z, AnnotationOverlay

BOX = TextBox((100.0, 100.0, 300.0, 140.0), "HELLO THERE", fontsize=20,
              color=(0.0, 0.0, 0.0), fill_color=(1.0, 1.0, 0.0))
COVER = Shape("rect", (90.0, 90.0, 310.0, 150.0), color=(1.0, 0.0, 0.0), width=2.0,
              fill_color=(0.0, 0.0, 1.0), opacity=1.0)


# ---- what the saved file does (the reference the preview must match) ----------


def test_an_opaque_shape_over_a_text_box_covers_its_text_in_the_baked_pdf():
    """The behaviour the preview has to agree with: annotations paint in tuple order, so a filled
    Square written after a FreeText hides it completely — text included."""
    doc = fitz.open()
    try:
        page = doc.new_page()
        apply_annotations(page, (BOX, COVER))
        pix = page.get_pixmap(clip=fitz.Rect(95, 95, 305, 145))
        dark = sum(
            1
            for y in range(pix.height)
            for x in range(pix.width)
            if max(pix.pixel(x, y)) < 90
        )
        assert dark == 0                     # no text pixels survive under the fill
    finally:
        doc.close()


# ---- the z the overlay assigns ------------------------------------------------


def test_z_follows_tuple_position_not_type():
    z = [AnnotationOverlay._annot_z(i, 3) for i in range(3)]
    assert z == sorted(z) and len(set(z)) == 3        # strictly increasing, no ties


def test_the_annotation_band_never_reaches_the_overlay_chrome():
    """Every mark stays inside [6, 7), so search hits (9), text selection (10), the live gesture
    (11) and the selection chrome (12–14) keep sitting above the marks however many there are."""
    for count in (1, 2, 50, 500):
        for index in range(min(count, 5)):
            z = AnnotationOverlay._annot_z(index, count)
            assert _ANNOT_Z_BASE <= z < _ANNOT_Z_BASE + 1
    assert _REDACTION_Z < _ANNOT_Z_BASE               # redactions stay below every mark (M59.9)


# ---- the live scene (offscreen GUI) ------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _painted(win):
    """The overlay's items as ``(z, is_text)`` — a text-box's text rides its box as a child, so it
    reports the parent's z (Qt paints a child directly above its parent and nowhere else)."""
    out = []
    for item in win.view.annotations._items:
        parent = item.parentItem()
        out.append((parent.zValue() if parent is not None else item.zValue(), item))
    return out


def test_a_shape_added_after_a_text_box_paints_above_all_of_it(win):
    """The reported bug: the shape covered the box's fill but its text showed through, because the
    text sat on its own higher band. Now the box (frame + text) is one stack below the shape."""
    win.vdoc.add_annotation(0, BOX)
    win.vdoc.add_annotation(0, COVER)
    win.view.annotations.repaint()
    items = win.view.annotations._items
    frame = next(i for i in items if i.childItems())            # the text box: it carries the text
    shape = next(i for i in items if i is not frame and i.parentItem() is None)
    assert frame.zValue() == pytest.approx(AnnotationOverlay._annot_z(0, 2))
    assert shape.zValue() == pytest.approx(AnnotationOverlay._annot_z(1, 2))
    assert shape.zValue() > frame.zValue()                      # …and the text rides the frame


def test_the_text_box_text_is_a_child_of_its_frame(win):
    """The structural guarantee: the text has no z of its own, so no reordering can leave it
    floating above a mark that covers its box."""
    win.vdoc.add_annotation(0, BOX)
    win.view.annotations.repaint()
    from PySide6.QtWidgets import QGraphicsSimpleTextItem

    texts = [i for i in win.view.annotations._items if isinstance(i, QGraphicsSimpleTextItem)]
    assert texts == []                    # not tracked as a top-level item at all
    frames = [i for i in win.view.annotations._items if i.childItems()]
    assert len(frames) == 1 and isinstance(frames[0].childItems()[0], QGraphicsSimpleTextItem)


def test_send_to_back_restacks_the_preview(win):
    """M59.8's promise, now true across types: the verbs move paint order, not just hit order."""
    win.vdoc.add_annotation(0, BOX)
    win.vdoc.add_annotation(0, COVER)
    win.view.reload()
    win.view.annotations.repaint()
    top_before = max(z for z, _ in _painted(win))

    win.view.annotations.select_object(0, win.vdoc.page_annotations(0)[1])   # the shape
    assert win._reorder_objects("back") is True
    win.view.annotations.repaint()

    order = [type(a).__name__ for a in win.vdoc.page_annotations(0)]
    assert order == ["Shape", "TextBox"]                  # model moved
    frames = [i for i in win.view.annotations._items if i.childItems()]
    assert frames and frames[0].zValue() == pytest.approx(top_before)  # …and the preview with it


def test_a_highlight_added_over_a_shape_paints_above_it(win):
    """Type no longer decides: a highlight used to sit on band 6, permanently under every drawn
    mark, even when the model put it last."""
    win.vdoc.add_annotation(0, COVER)
    win.vdoc.add_annotation(0, Highlight(((100.0, 100.0, 300.0, 140.0),), color=(1.0, 0.86, 0.1)))
    win.view.annotations.repaint()
    zs = sorted(z for z, _ in _painted(win))
    assert zs[0] < zs[-1]
    assert zs[-1] == pytest.approx(AnnotationOverlay._annot_z(1, 2))
