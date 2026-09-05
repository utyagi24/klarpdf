"""Highlights multiply instead of alpha-blending (M84). Offscreen GUI, measured in pixels.

Owner-reported: *"our highlight color appear very dull compared to the color used by Edge. can we
revisit our palette?"* **The palette is not the problem and is unchanged.** The overlay painted a
highlight with ``setAlpha(110)`` — plain source-over at 43% — which moves every pixel under the
mark 43% of the way toward the mark's colour, washing it toward the white page.

Two defects, not one:

* **the dullness itself** — yellow rendered ``(255, 240, 156)`` where the mark's own colour is
  ``(255, 219, 26)``;
* **the text went with it** — black under a highlight came out ``(110, 95, 11)``, olive. A
  highlighter that *reduces* legibility is doing the opposite of its job.

And the saved file was never wrong: PyMuPDF bakes highlight annotations with ``/BM /Multiply``, so
the same passage looked **more vivid reopened in Edge** than in the app that drew it. This is a
preview-fidelity bug, not a taste question — which is why the tests below compare our render
against the saved PDF's own rendering rather than against a number someone liked.

M84.2 rides along because they cannot ship apart: the M73 sticky-markup flow shows the drag-over-
text preview constantly, so fixing only the committed mark would leave arming pale and make the
mark jump vivid on release.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsRectItem

from app import PdfApp
from main_window import MainWindow
from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.page_edits import Highlight
from store.settings import Settings
from viewer.blend import MultiplyRectItem
from viewer.markup_style import HIGHLIGHT_COLORS
from viewer.tools import ArmedTool

YELLOW = HIGHLIGHT_COLORS[0][1]
BLACK = (0, 0, 0)


@pytest.fixture
def text_pdf(tmp_path) -> str:
    path = str(tmp_path / "hl.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((60, 100), "BLACK TEXT UNDER THE MARK", fontsize=14)
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
def win(app, text_pdf):
    w = MainWindow(app, text_pdf, app.settings)
    w.show()
    app.processEvents()
    w.view.set_zoom(1.0)
    w.view._render_visible()          # the page pixmap must be in the scene to composite against
    app.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _word_box(win, first=0, last=3):
    """One text line's box, spanning several words — the geometry a highlight actually covers."""
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    words = page.get_text("words")
    return (words[first][0], words[first][1], words[last][2], words[first][3])


# The rendered rect is a pixel or two larger than the painted one (float→int), so the outermost
# ring catches unpainted paper. Sampling inset keeps every probe inside the mark.
_INSET = 3


def _sample(win, box):
    """``(over_paper, over_text)`` inside ``box`` of the composited scene, as ``(r, g, b)``.

    The lightest pixel is the mark over white paper; the darkest is the mark over a black glyph.
    Rendering the *scene* rather than the page pixmap is the point — the blend mode only exists
    during composition, so anything that inspects the item's brush would not see this bug at all.

    ``IgnoreAspectRatio`` because ``render()`` otherwise defaults to ``KeepAspectRatio`` and
    letterboxes: the source rect is fractional and the target integer, so it fits the source inside
    the image and leaves **white bars** down both sides. Those bars are paper, and the probe below
    looks for the *lightest* pixel, so they read as "the highlight did not paint". The bars were
    ~1 px wide before M88.1 and ``_INSET`` hid them by luck; at the 1.333x scene scale they grew to
    ~3 px and started leaking in. Not scaling the source at all removes the artefact outright,
    rather than re-tuning a constant that would drift again at the next scale change.
    """
    rect = win.view.scene_rect_for_box(0, box)
    image = QImage(int(rect.width()), int(rect.height()), QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    win.view.scene().render(painter, QRectF(image.rect()), rect,
                            Qt.AspectRatioMode.IgnoreAspectRatio)
    painter.end()
    pixels = [image.pixelColor(x, y)
              for y in range(_INSET, image.height() - _INSET)
              for x in range(_INSET, image.width() - _INSET)]
    lightest = max(pixels, key=lambda c: c.lightness())
    darkest = min(pixels, key=lambda c: c.lightness())
    return lightest.getRgb()[:3], darkest.getRgb()[:3]


def _expected(rgb):
    """A colour multiplied over white paper is the colour itself."""
    return tuple(round(channel * 255) for channel in rgb)


# ---- M84.1 the committed mark ---------------------------------------------------


@pytest.mark.parametrize("name,rgb", HIGHLIGHT_COLORS, ids=[n for n, _ in HIGHLIGHT_COLORS])
def test_every_palette_colour_renders_at_full_strength(win, name, rgb):
    """Over white paper, multiply leaves the mark's own colour — no wash toward the page."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=rgb))
    win.view.annotations.repaint()
    over_paper, _over_text = _sample(win, box)
    assert over_paper == pytest.approx(_expected(rgb), abs=2)


@pytest.mark.parametrize("name,rgb", HIGHLIGHT_COLORS, ids=[n for n, _ in HIGHLIGHT_COLORS])
def test_black_text_under_a_highlight_stays_black(win, name, rgb):
    """The legibility half. Alpha-110 turned black glyphs olive — ``(110, 95, 11)`` under yellow —
    so the mark actively reduced legibility. Multiply cannot lighten anything."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=rgb))
    win.view.annotations.repaint()
    _over_paper, over_text = _sample(win, box)
    assert over_text == pytest.approx(BLACK, abs=2)


def test_yellow_is_no_longer_the_washed_out_measurement(win):
    """The reported symptom, pinned to the exact numbers from the investigation so a regression
    reads as itself rather than as 'some colour changed'."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.annotations.repaint()
    over_paper, over_text = _sample(win, box)
    assert over_paper != pytest.approx((255, 240, 156), abs=2)   # the old alpha-110 render
    assert over_text != pytest.approx((110, 95, 11), abs=2)      # the old olive text
    assert over_paper == pytest.approx((255, 219, 26), abs=2)
    assert over_text == pytest.approx(BLACK, abs=2)


def test_the_preview_matches_the_saved_pdf(win, tmp_path):
    """The claim that makes this a fidelity bug rather than a taste question: PyMuPDF bakes
    highlights with ``/BM /Multiply``, so before M84 a passage highlighted in KlarPDF looked more
    vivid reopened in Edge than it had in KlarPDF. Rendered here by PyMuPDF itself, at the same
    scale, and compared pixel to pixel."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.annotations.repaint()
    ours_paper, ours_text = _sample(win, box)

    out = str(tmp_path / "saved.pdf")
    PyMuPDFEngine().materialize(win.vdoc, out)
    doc = fitz.open(out)
    try:
        pixmap = doc[0].get_pixmap(clip=fitz.Rect(box))
        samples = [pixmap.pixel(x, y)
                   for y in range(_INSET, pixmap.height - _INSET)
                   for x in range(_INSET, pixmap.width - _INSET)]
    finally:
        doc.close()
    theirs_paper = max(samples, key=sum)
    theirs_text = min(samples, key=sum)

    assert ours_paper == pytest.approx(theirs_paper, abs=3)
    assert ours_text == pytest.approx(theirs_text, abs=3)


def test_the_committed_highlight_uses_a_multiply_item(win):
    """Structural companion to the pixel tests: a plain rect item with the same brush would show
    the mark's colour on paper *and* wipe the text out to solid, which the pixel tests above would
    not distinguish from correct on a page with no glyphs in the probe."""
    win.vdoc.add_annotation(0, Highlight((_word_box(win),), color=YELLOW))
    win.view.annotations.repaint()
    painted = [i for i in win.view.annotations._items if isinstance(i, QGraphicsRectItem)]
    assert painted
    assert all(isinstance(i, MultiplyRectItem) for i in painted)


def test_the_brush_carries_no_second_translucency(win):
    """Multiply supplies the translucency. An alpha on top of it would wash the colour a second
    time and bring the dullness straight back — the failure mode of a half-applied fix."""
    win.vdoc.add_annotation(0, Highlight((_word_box(win),), color=YELLOW))
    win.view.annotations.repaint()
    item = next(i for i in win.view.annotations._items if isinstance(i, MultiplyRectItem))
    assert item.brush().color().alpha() == 255


# ---- M84.2 the live preview -----------------------------------------------------


def _arm_and_select(win, box):
    win.view.arm(ArmedTool.HIGHLIGHT)
    win.view.selection.select_word_at(win.view.scene_rect_for_box(0, box).center())
    win.view.selection.repaint()
    PdfApp.instance().processEvents()


def test_the_armed_preview_multiplies_too(win):
    _arm_and_select(win, _word_box(win, 0, 0))
    try:
        items = win.view.selection._items
        assert items
        assert all(isinstance(i, MultiplyRectItem) for i in items)
        assert items[0].brush().color().alpha() == 255
    finally:
        win.view.disarm()


def test_preview_and_committed_mark_are_the_same_colour(win):
    """No flip on release. The M73 sticky flow shows this preview constantly, so a preview that
    disagreed with its own result would be visible on every single markup gesture."""
    box = _word_box(win, 0, 0)
    _arm_and_select(win, box)
    try:
        previewed = _sample(win, box)
    finally:
        win.view.disarm()

    win.view.selection.clear()
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.annotations.repaint()
    committed = _sample(win, box)
    assert previewed == committed


def test_the_unwired_fallback_preview_multiplies_as_well(win):
    """There is one highlight-preview path, not two. The fallback (nothing wired for the sticky
    colour) used to live in the armed-colour table as a fourth QColor at alpha 120 — so it would
    have stayed pale while the wired case went vivid. Caught by the existing M76.2 test when the
    rest of M84.2 landed, which is why the table no longer holds a highlight entry at all."""
    box = _word_box(win, 0, 0)
    win.view.highlight_preview_color = None
    win.view.arm(ArmedTool.HIGHLIGHT)
    try:
        win.view.selection.select_word_at(win.view.scene_rect_for_box(0, box).center())
        win.view.selection.repaint()
        items = win.view.selection._items
        assert items and all(isinstance(i, MultiplyRectItem) for i in items)
        assert items[0].brush().color() == QColor.fromRgbF(*YELLOW)
    finally:
        win.view.disarm()


def test_a_plain_text_selection_still_blends_normally(win):
    """The guard against over-applying the fix. An unarmed selection is a *selection indicator*,
    not a mark: multiplying the selection blue would darken the text it is meant to reveal."""
    box = _word_box(win, 0, 0)
    win.view.selection.select_word_at(win.view.scene_rect_for_box(0, box).center())
    win.view.selection.repaint()
    items = win.view.selection._items
    assert items
    assert not any(isinstance(i, MultiplyRectItem) for i in items)


@pytest.mark.parametrize("tool", [ArmedTool.UNDERLINE, ArmedTool.STRIKEOUT, ArmedTool.REDACT_TEXT])
def test_the_other_armed_previews_are_unchanged(win, tool):
    """Their committed mark is a thin line (or an opaque bar), not a fill — washing the whole band
    in multiply would preview something the release does not produce."""
    box = _word_box(win, 0, 0)
    win.view.arm(tool)
    try:
        win.view.selection.select_word_at(win.view.scene_rect_for_box(0, box).center())
        win.view.selection.repaint()
        assert not any(isinstance(i, MultiplyRectItem) for i in win.view.selection._items)
    finally:
        win.view.disarm()
