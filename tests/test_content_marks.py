"""Content-draw engine — stamps, signatures, watermarks (PLAN.md §R4, M61). Headless.

The R4 keystone: one engine draws a text stamp, a placed image and a watermark **into the page's
content stream** at materialise, rather than adding an annotation. These tests pin the three things
that distinguishes that path from the M20/M57 overlay path:

* it is **content, not an annotation** — nothing to select, drag off, or delete in another viewer;
* it is a **point of no return at Save** — nothing author-tagged survives to read back, so the
  document must reload from the written file or the next save would bake a second copy;
* it composes with the destructive redaction pass in the right order.

Plus the shared placement contract (the descriptors ride the PageRef, move and scale with the
existing primitives) and the "presets are prefilled custom stamps" rule.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from model.content_marks import (
    CONTENT_MARK_TYPES,
    ImageStamp,
    Stamp,
    apply_content_marks,
    is_content_mark,
    natural_size,
    placement_size,
    preset_mark,
    render_mark_document,
    size_for_page,
)
from model.edit_engine import PyMuPDFEngine
from model.page_edits import Highlight, Redaction, mark_bounds, scale_mark, translate_mark
from model.virtual_document import VirtualDocument

BODY = "BODYTEXT"
SECRET = "SECRETDATA"


@pytest.fixture
def body_pdf(tmp_path) -> str:
    """2 pages, each carrying one known word near the top-left."""
    path = str(tmp_path / "body.pdf")
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), BODY, fontsize=14)
        page.insert_text((72, 200), SECRET, fontsize=14)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def vdoc(body_pdf) -> VirtualDocument:
    return VirtualDocument.from_path(body_pdf)


@pytest.fixture
def png(tmp_path) -> str:
    """A small solid-red PNG on disk (the stand-in for a scanned signature)."""
    path = str(tmp_path / "sig.png")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
    pix.clear_with(20)  # near-black, so it is unmistakable against a white page
    pix.save(path)
    return path


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


STAMP_RECT = (300.0, 400.0, 480.0, 460.0)


# ---- it is page content, not an annotation --------------------------------------


def test_stamp_bakes_into_page_content(vdoc, tmp_path):
    """The stamp's text lands in the saved page's text layer — it is drawn content, so it is
    extractable and searchable, not a sticker floating above the page."""
    vdoc.add_annotation(0, Stamp(STAMP_RECT, "APPROVED"))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        text = saved[0].get_text()
        assert "APPROVED" in text
        assert BODY in text          # the page's own content is untouched
    finally:
        saved.close()


def test_stamp_adds_no_annotation(vdoc, tmp_path):
    """The whole point of the content path: nothing in the output can be selected and dragged off.
    A Highlight on the same page proves the assertion can see annotations when there are any."""
    vdoc.add_annotation(0, Stamp(STAMP_RECT, "APPROVED"))
    vdoc.add_annotation(1, Highlight(((72, 90, 140, 105),)))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert list(saved[0].annots()) == []
        assert len(list(saved[1].annots())) == 1
    finally:
        saved.close()


def test_stamped_page_keeps_its_text_and_gains_the_stamp(vdoc, tmp_path):
    """A stamp is additive: it draws over the page without rewriting what is already there."""
    before = fitz.open(_materialize(vdoc, tmp_path, "before.pdf"))
    baseline = before[0].get_text().split()
    before.close()

    vdoc.add_annotation(0, Stamp(STAMP_RECT, "APPROVED"))
    after = fitz.open(_materialize(vdoc, tmp_path, "after.pdf"))
    try:
        words = after[0].get_text().split()
        assert set(baseline) <= set(words)
        assert "APPROVED" in words
    finally:
        after.close()


# ---- over vs under the content (the watermark mode) -----------------------------


def _xobject_before_text(page) -> bool:
    """Does the page invoke its stamp XObject (``Do``) *before* it draws text (``Tj``/``TJ``)?

    The structural test for "under the content": ``show_pdf_page(overlay=False)`` prepends the
    artwork to the content stream, so it paints first and the body text lands on top of it.
    """
    content = page.read_contents()
    do = content.find(b" Do")
    show = min((i for i in (content.find(b"Tj"), content.find(b"TJ")) if i >= 0), default=-1)
    assert do >= 0 and show >= 0, "expected both an XObject invocation and drawn text"
    return do < show


def test_watermark_paints_under_the_page_content(vdoc, tmp_path):
    """``under=True`` prepends the mark, so the page's own text stays legible on top of it."""
    vdoc.add_annotation(0, preset_mark("Draft", (0, 0, 595, 842), whole_page=True))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert _xobject_before_text(saved[0]) is True
    finally:
        saved.close()


def test_stamp_paints_over_the_page_content(vdoc, tmp_path):
    """The default (``under=False``) appends, so the stamp covers what it lands on."""
    vdoc.add_annotation(0, Stamp(STAMP_RECT, "APPROVED"))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert _xobject_before_text(saved[0]) is False
    finally:
        saved.close()


# ---- the generator: auto-fit, rotation, opacity, framing ------------------------


def _stamp_span_size(path: str, text: str) -> float:
    doc = fitz.open(path)
    try:
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if text in span["text"]:
                        return span["size"]
        raise AssertionError(f"{text!r} not found in the rendered page")
    finally:
        doc.close()


def test_fontsize_auto_fits_the_placed_rect(vdoc, tmp_path):
    """``fontsize=0`` sizes the text to the box that was dragged — a wider stamp gets bigger text."""
    vdoc.add_annotation(0, Stamp((100, 400, 200, 440), "FIT"))
    small = _stamp_span_size(_materialize(vdoc, tmp_path, "small.pdf"), "FIT")

    vdoc.clear_annotations(0)
    vdoc.add_annotation(0, Stamp((100, 400, 500, 560), "FIT"))
    large = _stamp_span_size(_materialize(vdoc, tmp_path, "large.pdf"), "FIT")

    assert large > small * 1.5


def test_explicit_fontsize_is_not_refitted(vdoc, tmp_path):
    """A pinned size stays pinned, so one stamp applied across differently sized pages matches."""
    vdoc.add_annotation(0, Stamp((100, 400, 500, 560), "PINNED", fontsize=18.0))
    assert _stamp_span_size(_materialize(vdoc, tmp_path), "PINNED") == pytest.approx(18.0, abs=0.5)


# ---- "hug the text" sizing (the explicit-font-size placement) -------------------
#
# Auto-fit answers "how big can the text be in this box"; `natural_size` answers the inverse, and
# the inverse is what lets a stamp with a typed point size be *dropped* rather than dragged. The
# contract that matters is that the two agree: a box from `natural_size` must not re-fit the text
# to some other size when it is drawn.


def test_natural_size_grows_with_the_font_size():
    small = natural_size(Stamp((0, 0, 1, 1), "APPROVED", fontsize=18.0))
    large = natural_size(Stamp((0, 0, 1, 1), "APPROVED", fontsize=36.0))
    assert large[0] > small[0] and large[1] > small[1]


def test_natural_size_grows_with_the_text():
    short = natural_size(Stamp((0, 0, 1, 1), "OK", fontsize=24.0))
    long = natural_size(Stamp((0, 0, 1, 1), "CONFIDENTIAL", fontsize=24.0))
    assert long[0] > short[0]
    assert long[1] == pytest.approx(short[1], abs=0.5)   # one line either way


def test_natural_size_accounts_for_a_second_line():
    one = natural_size(Stamp((0, 0, 1, 1), "TWO", fontsize=24.0))
    two = natural_size(Stamp((0, 0, 1, 1), "TWO\nLINES", fontsize=24.0))
    assert two[1] > one[1] * 1.5


def test_a_naturally_sized_box_draws_the_font_size_it_was_built_for(vdoc, tmp_path):
    """The round-trip that makes the feature honest: size the box from the font, and the font that
    comes back out of the bake is the one asked for — not an auto-fit approximation of it."""
    mark = Stamp((0, 0, 1, 1), "APPROVED", fontsize=30.0)
    width, height = natural_size(mark)
    from dataclasses import replace

    vdoc.add_annotation(0, replace(mark, rect=(80, 300, 80 + width, 300 + height)))
    assert _stamp_span_size(_materialize(vdoc, tmp_path), "APPROVED") == pytest.approx(30.0, abs=0.5)


def test_a_frame_widens_the_natural_box():
    """The frame is drawn *inside* the rect, so a framed stamp needs a bigger one or the border
    would crowd the letters it was sized around."""
    bare = natural_size(Stamp((0, 0, 1, 1), "APPROVED", fontsize=24.0, border_width=0.0))
    framed = natural_size(Stamp((0, 0, 1, 1), "APPROVED", fontsize=24.0, border_width=3.0))
    assert framed[0] > bare[0] and framed[1] > bare[1]


def test_resizing_a_pinned_stamp_carries_its_font_size(vdoc, tmp_path):
    """A pinned stamp was sized to hug its text, so a resize must move the lettering too — leaving
    the size behind would only inflate the padding, a drag that visibly does nothing."""
    mark = Stamp((100, 100, 200, 140), "PINNED", fontsize=20.0)
    bigger = scale_mark(mark, 2.0, 2.0, 100, 100)
    assert bigger.fontsize == pytest.approx(40.0)
    # The smaller axis governs, so a squash can never push the text out of its own box.
    assert scale_mark(mark, 3.0, 1.0, 100, 100).fontsize == pytest.approx(20.0)


def test_resizing_an_auto_fit_stamp_leaves_it_auto_fit():
    """``fontsize=0`` is the sentinel for "fit the box" — scaling must not turn it into a real size."""
    assert scale_mark(Stamp((100, 100, 200, 140), "AUTO"), 2.0, 2.0, 0, 0).fontsize == 0.0


def test_resizing_a_pinned_stamp_keeps_its_box_hugging_the_text():
    """The owner-reported distortion: a pinned stamp's box is shaped *by* its text, so a lopsided
    corner-drag must re-derive the box rather than stretch it. Otherwise the artwork ends up in a
    box the wrong shape for it, which is what "resizing distorts the stamp" was."""
    mark = Stamp((100, 100, 200, 140), "APPROVED", fontsize=20.0)
    for sx, sy in ((2.0, 2.0), (3.0, 1.2), (0.5, 0.5), (1.0, 4.0)):
        out = scale_mark(mark, sx, sy, 100, 100)
        assert (out.rect[2] - out.rect[0], out.rect[3] - out.rect[1]) == pytest.approx(
            placement_size(out), abs=0.5
        )


def test_resizing_a_pinned_stamp_anchors_the_corner_being_pulled_against():
    """Dragging the bottom-right handle keeps the top-left still, and vice versa — the stamp grows
    away from the handle rather than sliding out from under it."""
    mark = Stamp((100, 100, 200, 140), "OK", fontsize=20.0)
    grown = scale_mark(mark, 2.0, 2.0, 100, 100)          # origin at the top-left corner
    assert (grown.rect[0], grown.rect[1]) == pytest.approx((100, 100), abs=0.5)
    grown = scale_mark(mark, 2.0, 2.0, 200, 140)          # origin at the bottom-right corner
    assert (grown.rect[2], grown.rect[3]) == pytest.approx((200, 140), abs=0.5)


# ---- a pinned size survives rotation and oversized pages ------------------------
#
# `show_pdf_page` *fits* the rotated artwork to its rect, up as readily as down. Both directions
# broke a pinned size: rotation shrank it (a 120pt stamp at -45 deg baked at 40pt) and a roomier
# rect blew it up. These pin both ends.


@pytest.mark.parametrize("angle", [0.0, -45.0, 30.0, 90.0])
def test_a_pinned_size_survives_rotation(vdoc, tmp_path, angle):
    """The owner-reported "120pt came out small": with the rect sized to the *unrotated* text, the
    rotated artwork was fitted down inside it. The rect must be the artwork's **rotated extent**."""
    from dataclasses import replace

    mark = Stamp((0, 0, 1, 1), "TILTED", fontsize=40.0, angle=angle)
    width, height = placement_size(mark)
    vdoc.add_annotation(0, replace(mark, rect=(40, 200, 40 + width, 200 + height)))
    baked = _stamp_span_size(_materialize(vdoc, tmp_path, f"r{angle}.pdf"), "TILTED")
    assert baked == pytest.approx(40.0, abs=1.0)


def test_a_pinned_stamp_is_not_enlarged_to_fill_a_roomier_box(vdoc, tmp_path):
    """"Pinned" has to mean pinned in both directions. `show_pdf_page` scales up as happily as down,
    so a pinned mark is capped at its own size and simply sits centred in a larger rect."""
    vdoc.add_annotation(0, Stamp((100, 400, 500, 560), "PINNED", fontsize=18.0))
    assert _stamp_span_size(_materialize(vdoc, tmp_path), "PINNED") == pytest.approx(18.0, abs=0.5)


def test_a_pinned_stamp_still_shrinks_rather_than_spilling(vdoc, tmp_path):
    """Shrinking stays uncapped: a mark that would overflow its rect is scaled down, never allowed
    to spill outside the box that is its placement promise."""
    vdoc.add_annotation(0, Stamp((100, 400, 180, 430), "PINNED", fontsize=60.0))
    assert _stamp_span_size(_materialize(vdoc, tmp_path), "PINNED") < 60.0


def test_size_for_page_reduces_a_size_too_big_for_the_paper():
    """The owner-reported spill: at 120pt and -45 deg, "APPROVED" spans a 634pt diagonal — wider
    than A4's 595pt — so the box hung off the page and could not be centred."""
    mark = Stamp((0, 0, 1, 1), "APPROVED", fontsize=120.0, angle=-45.0)
    fitted = size_for_page(mark, 595.0, 842.0)
    assert fitted < 120.0
    from dataclasses import replace

    width, height = placement_size(replace(mark, fontsize=fitted))
    assert width <= 595.0 + 0.5 and height <= 842.0 + 0.5


def test_size_for_page_leaves_a_size_that_already_fits_alone():
    """It reduces, never raises — a stamp that fits is placed at exactly the size that was typed."""
    mark = Stamp((0, 0, 1, 1), "OK", fontsize=24.0)
    assert size_for_page(mark, 595.0, 842.0) == 24.0


def test_size_for_page_ignores_an_auto_fit_stamp():
    assert size_for_page(Stamp((0, 0, 1, 1), "AUTO"), 595.0, 842.0) == 0.0


def test_rotated_stamp_stays_within_its_rect(vdoc, tmp_path):
    """An angled stamp is fitted into the rect it was placed at — the rect is the promise."""
    rect = fitz.Rect(200, 300, 400, 380)
    vdoc.add_annotation(0, Stamp(tuple(rect), "TILTED", angle=45.0))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        hits = saved[0].search_for("TILTED")
        assert hits, "the rotated stamp did not render"
        for quad_rect in hits:
            assert quad_rect in rect + (-2, -2, 2, 2)   # a hair of slack for glyph bounds
    finally:
        saved.close()


def _baked_text_direction(path: str, text: str) -> tuple[float, float]:
    """The writing direction of ``text`` as it was **baked into the page**, in MuPDF's y-down
    frame: ``(1, 0)`` is horizontal, a negative second component climbs to the right."""
    doc = fitz.open(path)
    try:
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                if any(text in span["text"] for span in line["spans"]):
                    return line["dir"]
        raise AssertionError(f"{text!r} not found in the rendered page")
    finally:
        doc.close()


@pytest.mark.parametrize("angle,climbs", [(-45.0, True), (45.0, False)])
def test_baked_angle_is_counter_clockwise(vdoc, tmp_path, angle, climbs):
    """``angle`` is counter-clockwise, and the **bake** has to agree with the descriptor.

    The regression: ``show_pdf_page``'s ``rotate`` is clockwise-positive, so passing ``angle``
    through unconverted baked every rotated mark as its own mirror image. On screen the preview
    (Qt, counter-clockwise) tilted one way while the thumbnail — which renders the bake — tilted
    the other, and the saved file agreed with the thumbnail. Nothing caught it, because the only
    rotation test asserted the mark stayed inside its rect, which a mirrored mark also does.
    """
    vdoc.add_annotation(0, Stamp((150, 300, 450, 400), "TILTED", angle=angle))
    dx, dy = _baked_text_direction(_materialize(vdoc, tmp_path, f"a{angle}.pdf"), "TILTED")
    assert dx > 0, "the text should still read left-to-right"
    # y is *down* in MuPDF's extraction frame, so climbing to the right means a negative dy.
    assert (dy < 0) is climbs


def test_watermark_default_diagonal_bakes_bottom_left_to_top_right(vdoc, tmp_path):
    """The near-universal watermark diagonal, end to end: the ``-45°`` default that
    :func:`preset_mark` promises must be what actually lands in the file."""
    vdoc.add_annotation(0, preset_mark("Confidential", (0, 0, 595, 842), whole_page=True))
    _dx, dy = _baked_text_direction(_materialize(vdoc, tmp_path, "wm.pdf"), "CONFIDENTIAL")
    assert dy < 0


def _ink_at(path: str, point: tuple[float, float]) -> int:
    """Darkness (0 = black, 255 = white) of the rendered page at a point, as a single channel."""
    doc = fitz.open(path)
    try:
        pix = doc[0].get_pixmap()
        return pix.pixel(int(point[0]), int(point[1]))[0]
    finally:
        doc.close()


def test_opacity_lightens_the_mark(vdoc, tmp_path):
    """A translucent stamp lets the page show through — the lever that makes a watermark readable
    over text rather than a blot on it."""
    solid_rect = (100.0, 400.0, 500.0, 560.0)
    centre = (300, 480)
    vdoc.add_annotation(0, Stamp(solid_rect, "BLOCK", fill_color=(0, 0, 0), opacity=1.0))
    opaque = _ink_at(_materialize(vdoc, tmp_path, "opaque.pdf"), centre)

    vdoc.clear_annotations(0)
    vdoc.add_annotation(0, Stamp(solid_rect, "BLOCK", fill_color=(0, 0, 0), opacity=0.2))
    faint = _ink_at(_materialize(vdoc, tmp_path, "faint.pdf"), centre)

    assert opaque < 60          # near-black where the solid stamp sits
    assert faint > opaque + 80  # visibly lighter at 20% opacity


def test_frameless_stamp_draws_only_text(tmp_path):
    """``border_width=0`` with no fill draws no frame — what a watermark needs."""
    framed = render_mark_document(Stamp((0, 0, 200, 60), "X", border_width=3.0))
    bare = render_mark_document(Stamp((0, 0, 200, 60), "X", border_width=0.0))
    try:
        assert len(framed[0].get_drawings()) > 0
        assert len(bare[0].get_drawings()) == 0
    finally:
        framed.close()
        bare.close()


# ---- image stamps (the signature payload) ---------------------------------------


def test_image_stamp_bakes_the_image_in(vdoc, tmp_path, png):
    """A placed image becomes page content — one image on the saved page, none before."""
    assert fitz.open(_materialize(vdoc, tmp_path, "clean.pdf"))[0].get_images() == []
    vdoc.add_annotation(0, ImageStamp(STAMP_RECT, png))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert len(saved[0].get_images()) == 1
        assert list(saved[0].annots()) == []       # content, not an annotation
    finally:
        saved.close()


def test_missing_image_does_not_break_the_save(vdoc, tmp_path):
    """The path is the user's and can move between placing and saving. Losing the image must cost
    the picture, never the document — the save succeeds and the rest of the page is intact."""
    vdoc.add_annotation(0, ImageStamp(STAMP_RECT, str(tmp_path / "gone.png")))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert saved[0].get_images() == []
        assert BODY in saved[0].get_text()
    finally:
        saved.close()


def test_image_opacity_is_carried_in_the_pixels(png):
    """An image has no ``/CA`` to set, so opacity has to reach the alpha channel."""
    art = render_mark_document(ImageStamp((0, 0, 40, 20), png, opacity=0.5))
    try:
        pix = art[0].get_pixmap(alpha=False)
        assert pix.pixel(20, 10)[0] > 100   # a 50%-alpha near-black image renders mid-grey
    finally:
        art.close()


# ---- presets are prefilled custom stamps (Way 2) --------------------------------


def test_preset_is_an_ordinary_stamp(vdoc):
    """A preset yields the same descriptor the custom dialog builds — so it can be edited after
    placing and there is no second code path to keep calibrated."""
    stamp = preset_mark("Approved", STAMP_RECT)
    assert isinstance(stamp, Stamp)
    assert stamp.text == "APPROVED"
    assert stamp.rect == STAMP_RECT


def test_preset_overrides_win():
    assert preset_mark("Approved", STAMP_RECT, angle=15.0).angle == 15.0


def test_unknown_preset_falls_back_to_its_name():
    """A caller can never end up with no stamp at all."""
    assert preset_mark("shipped", STAMP_RECT).text == "SHIPPED"


def test_watermark_preset_is_translucent_diagonal_and_under():
    mark = preset_mark("Confidential", (0, 0, 595, 842), whole_page=True)
    assert mark.text == "CONFIDENTIAL"
    assert mark.under is True
    assert mark.angle == -45.0   # bottom-left to top-right, the near-universal convention
    assert 0.0 < mark.opacity < 0.5
    assert mark.border_width == 0.0


# ---- the shared placement contract (rides the PageRef; moves; scales) -----------


def test_content_marks_ride_the_pageref(vdoc):
    stamp = Stamp(STAMP_RECT, "APPROVED")
    vdoc.add_annotation(0, stamp)
    assert vdoc.page_annotations(0) == (stamp,)
    assert vdoc.dirty is True


def test_content_marks_follow_their_page_through_a_reorder(vdoc):
    stamp = Stamp(STAMP_RECT, "APPROVED")
    vdoc.add_annotation(0, stamp)
    vdoc.move_page(0, 1)
    assert vdoc.page_annotations(1) == (stamp,)
    assert vdoc.page_annotations(0) == ()


@pytest.mark.parametrize("mark", [
    Stamp(STAMP_RECT, "APPROVED"),
    ImageStamp(STAMP_RECT, "sig.png"),
])
def test_content_marks_translate_and_scale(mark):
    """Move and resize come from the existing primitives — a stamp is free-placed geometry, so
    M59.6's group move and M59.7's resize handles work on it without new model code."""
    moved = translate_mark(mark, 10.0, -5.0)
    assert mark_bounds(moved) == (310.0, 395.0, 490.0, 455.0)

    scaled = scale_mark(mark, 2.0, 1.0, 300.0, 400.0)
    assert mark_bounds(scaled) == (300.0, 400.0, 660.0, 460.0)   # stretches, unlike a TextBox


def test_content_marks_are_hashable_for_undo_snapshots():
    """Frozen value objects — the requirement for riding a frozen PageRef and snapshotting."""
    assert len({Stamp(STAMP_RECT, "A"), Stamp(STAMP_RECT, "A"), Stamp(STAMP_RECT, "B")}) == 2


def test_is_content_mark_separates_the_two_paths():
    assert is_content_mark(Stamp(STAMP_RECT, "A")) is True
    assert is_content_mark(ImageStamp(STAMP_RECT, "x.png")) is True
    assert is_content_mark(Highlight(((0, 0, 1, 1),))) is False
    assert is_content_mark(Redaction(((0, 0, 1, 1),))) is False
    assert set(CONTENT_MARK_TYPES) == {Stamp, ImageStamp}


# ---- point of no return ---------------------------------------------------------


def test_has_content_marks_tracks_the_commit_state(vdoc):
    assert vdoc.has_content_marks() is False
    vdoc.add_annotation(0, Highlight(((72, 90, 140, 105),)))
    assert vdoc.has_content_marks() is False      # an overlay is not a commit
    vdoc.add_annotation(1, Stamp(STAMP_RECT, "APPROVED"))
    assert vdoc.has_content_marks() is True


def test_a_baked_stamp_does_not_round_trip(vdoc, tmp_path):
    """Reopening the saved file finds no editable stamp — it is content now. This is exactly why
    the save must reload from the written file: a model copy surviving it would bake a *second*
    stamp on the next save."""
    vdoc.add_annotation(0, Stamp(STAMP_RECT, "APPROVED"))
    out = _materialize(vdoc, tmp_path)

    reopened = VirtualDocument.from_path(out)
    try:
        assert reopened.page_annotations(0) == ()
        assert reopened.has_content_marks() is False
        # …and the stamp really is on the page, just not as an editable mark.
        page = reopened.sources[reopened.ordered[0].source_id][0]
        assert "APPROVED" in page.get_text()
    finally:
        reopened.close()


def test_saving_twice_without_a_reload_would_double_stamp(vdoc, tmp_path):
    """Pins the hazard the commit-on-save exists to prevent: the model still holds the mark, so a
    second materialise from the *same* model draws it again. MainWindow._write_to reloads from the
    written file precisely so this cannot happen through the UI."""
    vdoc.add_annotation(0, Stamp(STAMP_RECT, "APPROVED"))
    first = fitz.open(_materialize(vdoc, tmp_path, "first.pdf"))
    try:
        assert len(first[0].search_for("APPROVED")) == 1
    finally:
        first.close()

    vdoc.reload_from_file(_materialize(vdoc, tmp_path, "second.pdf"))
    assert vdoc.has_content_marks() is False      # the reload is what clears it


# ---- composition with the other passes ------------------------------------------


def test_a_stamp_over_a_redaction_survives_the_destructive_pass(vdoc, tmp_path):
    """Redaction rewrites the content stream, so ordering matters: redact first, then stamp. A
    stamp placed over a redacted box must still be there afterwards."""
    ref = vdoc.ordered[0]
    box = next(tuple(w[:4]) for w in vdoc.sources[ref.source_id][0].get_text("words")
               if w[4] == SECRET)
    vdoc.add_annotation(0, Redaction((box,)))
    vdoc.add_annotation(0, Stamp((box[0], box[1] - 4, box[2] + 60, box[3] + 4), "VOID"))

    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        text = saved[0].get_text()
        assert SECRET not in text          # the redaction still destroyed it
        assert "VOID" in text              # and the stamp survived on top
    finally:
        saved.close()


def test_annotations_still_layer_above_a_stamp(vdoc, tmp_path):
    """Content marks bake below the annotation overlays, which stay editable — so a text box over a
    watermark reads exactly as it does on the page's own ink."""
    from model.page_edits import TextBox

    vdoc.add_annotation(0, preset_mark("Draft", (0, 0, 595, 842), whole_page=True))
    vdoc.add_annotation(0, TextBox((100, 500, 300, 540), "note"))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert len(list(saved[0].annots())) == 1          # only the text box is an annotation
        assert "DRAFT" in saved[0].get_text()             # the watermark is content
    finally:
        saved.close()


def test_render_output_shows_content_marks(vdoc):
    """Print / export / live thumbnails all read ``render_output``, so a stamp shows there before
    it is ever saved (the M61 'renders in preview/print/export' requirement)."""
    vdoc.add_annotation(0, Stamp(STAMP_RECT, "APPROVED"))
    with PyMuPDFEngine().render_output(vdoc) as rendered:
        assert "APPROVED" in rendered[0].get_text()
        assert "APPROVED" not in rendered[1].get_text()


def test_apply_content_marks_ignores_annotation_descriptors(vdoc):
    """The whole page tuple is handed in; only the content marks are drawn here."""
    doc = fitz.open()
    page = doc.new_page()
    try:
        apply_content_marks(page, (Highlight(((0, 0, 10, 10),)), Redaction(((0, 0, 10, 10),))))
        assert page.read_contents() == b""
    finally:
        doc.close()


def test_a_page_range_watermark_is_the_same_descriptor_on_each_page(vdoc, tmp_path):
    """'Watermark pages 1–2' is the model-level loop the UI runs — one descriptor, N pages. No
    page-range state in the model is the reason stamp and watermark are one feature."""
    mark = preset_mark("Copy", (0, 0, 595, 842), whole_page=True)
    for index in range(vdoc.page_count):
        vdoc.add_annotation(index, mark)
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert all("COPY" in saved[i].get_text() for i in range(saved.page_count))
    finally:
        saved.close()
