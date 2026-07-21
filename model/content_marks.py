"""Content-draw engine — stamps, signatures and watermarks (PLAN.md §R4, M61).

**One engine for all three.** A stamp, a placed signature image and a page watermark differ only in
what they draw, where they sit, and whether they go *over* or *under* the page content — so they are
one code path with two payload descriptors, not three features:

* :class:`Stamp` — drawn text in an optional rounded-rect frame (the custom-text generator);
* :class:`ImageStamp` — a placed raster (scanned signature, seal, logo).

A **watermark is not a third type**: it is one of the two with ``under=True``, added to every page in
a range. The range lives in the UI loop, not in the model — which is what keeps "apply this stamp to
pages 3–17" and "watermark the document" the same operation.

**Presets are prefilled custom stamps** (owner call, "Way 2"): :data:`STAMP_PRESETS` holds keyword
overrides for the same :class:`Stamp` the custom dialog builds, so there is no second code path to
keep calibrated and a preset can be tweaked after placing.

**Why content, not annotations.** These marks bake into the page's content stream at materialise
(:func:`apply_content_marks`), unlike the :mod:`model.page_edits` overlays which stay annotations and
round-trip as editable. That is deliberate: a signature or an APPROVED stamp that a recipient can
select and drag off in any PDF editor is not a mark, it is a sticker. The cost is that a content mark
**does not round-trip** — like a redaction it is editable and undoable until Save and permanent
after, so a save that bakes one is a point of no return (see
``VirtualDocument.has_content_marks``). The UI says so plainly.

**How it draws.** Each mark renders into a throwaway one-page PDF at its own natural size, which is
then placed onto the target page with ``show_pdf_page`` — the standard PyMuPDF overlay recipe. That
keeps text **vector and extractable** (a rasterised stamp would blur at zoom and go missing from the
text layer), gives arbitrary rotation for free, and treats text and image payloads identically:
whatever the throwaway page holds is what lands on the target, rotated into the mark's rect.

Geometry is in unrotated page points, the same frame the annotation descriptors use. Every
descriptor is frozen + hashable so it can ride a ``PageRef`` and snapshot for undo/redo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import pymupdf as fitz

# Helvetica-Bold — the stamp face. A base-14 font, so it needs no embedding and renders identically
# in every viewer (PLAN.md §R4: no cross-renderer calibration).
STAMP_FONT = "hebo"

# Auto-fit search bounds, in points. The ceiling is generous enough for a full-page watermark word;
# the floor is the size below which a stamp is unreadable and the placement was a mistake.
_MIN_FONTSIZE = 4.0
_MAX_FONTSIZE = 400.0

# Breathing room between the frame and the text, per side, on top of the border width. Named
# because `natural_size` has to reproduce exactly what `_draw_text` insets, or a "hug the text"
# box would clip the very text it was sized for.
_TEXT_INSET = 2.0


@dataclass(frozen=True)
class Stamp:
    """A text stamp drawn into the page content: the word(s), an optional rounded frame, an angle.

    ``fontsize`` ``0`` (the default) **auto-fits** the text to ``rect`` — the natural behaviour when
    the placement gesture is "drag the box you want it to fill". A non-zero value pins the size, so a
    stamp applied across a page range stays visually identical on pages of differing size.

    ``under`` puts the mark beneath the page content instead of over it — the watermark mode. It
    changes nothing else, which is the point: a watermark is a stamp that the text sits on top of.

    ``angle`` is degrees **counter-clockwise**, the maths convention: ``+45`` reads bottom-left to
    top-right (north-east, the near-universal watermark diagonal) and ``-45`` reads top-left to
    bottom-right. Every consumer converts to its own sense at the edge (see
    :func:`apply_content_marks`); the descriptor itself never carries a renderer's.

    The sign was **backwards until M69.9**: ``-45`` produced the north-east diagonal, i.e. the field
    was clockwise-positive while this docstring already claimed counter-clockwise. Caught by the
    owner asking why north-east was negative — it should not have been, and nothing had shipped, so
    the convention was corrected rather than the documentation bent to fit it.
    """

    rect: tuple[float, float, float, float]
    text: str
    color: tuple[float, float, float] = (0.80, 0.10, 0.10)      # stamp red
    fontsize: float = 0.0                                        # 0 = auto-fit to rect
    border_width: float = 3.0                                    # 0 = no frame
    border_radius: float = 0.2                                   # fraction of the shorter side
    fill_color: tuple[float, float, float] | None = None         # None = transparent interior
    angle: float = 0.0
    opacity: float = 1.0
    under: bool = False

    def bounding_rect(self) -> tuple[float, float, float, float]:
        return self.rect


@dataclass(frozen=True)
class ImageStamp:
    """A raster placed into the page content — the sign-and-return payload (M63's signature).

    ``image_path`` is stored, never the pixels: KlarPDF keeps no hidden copy of a signature, so a
    recent-signatures list is a list of paths the user can see and delete (PLAN.md §M63). A path that
    has since moved renders as nothing rather than failing the save.

    ``white_to_alpha`` drops a light background out of the image (M63), which is what makes a **phone
    photo of a signature on paper** usable without an image editor: a transparent PNG already works
    through its own alpha, but a JPEG snapshot arrives as ink on an opaque white rectangle that would
    blank out whatever it is placed over. ``white_threshold`` is the luminance (0..1) at or above
    which a pixel becomes fully transparent, with a short ramp below it so antialiased strokes keep
    a soft edge instead of turning into jagged bitmap.
    """

    rect: tuple[float, float, float, float]
    image_path: str
    angle: float = 0.0
    opacity: float = 1.0
    under: bool = False
    white_to_alpha: bool = False
    white_threshold: float = 0.85

    def bounding_rect(self) -> tuple[float, float, float, float]:
        return self.rect


# The descriptors this module owns. Everything that must tell a content mark from an annotation
# (materialise ordering, the point-of-no-return check, the placement UI) tests against this.
CONTENT_MARK_TYPES = (Stamp, ImageStamp)


def is_content_mark(mark) -> bool:
    """True for a descriptor this module bakes into page content (vs an annotation overlay)."""
    return isinstance(mark, CONTENT_MARK_TYPES)


# ---- presets (Way 2: prefilled entries of the custom generator) -----------------
#
# Keyword overrides for the same Stamp the custom dialog builds — never a separate code path. A
# preset chosen from the menu is placed and then editable exactly like a hand-made one.

# **One list** (M69.3). There were two — a stamp list and a watermark list — and both contained
# "Draft" and "Confidential": the same word producing a different mark depending on which menu the
# user happened to open, with nothing on screen to explain the difference. A preset is a *word*, so
# it prefills only text + colour; whether the mark is a stamp or a watermark is the visible Place
# choice in the dialog. That is this module's own "Way 2" rule (a preset is a prefill, never a
# separate code path) applied one level up.
MARK_PRESETS: dict[str, dict] = {
    "Approved":     {"text": "APPROVED", "color": (0.05, 0.55, 0.20)},
    "Rejected":     {"text": "REJECTED", "color": (0.80, 0.10, 0.10)},
    "Reviewed":     {"text": "REVIEWED", "color": (0.10, 0.35, 0.75)},
    "Final":        {"text": "FINAL", "color": (0.05, 0.55, 0.20)},
    "Draft":        {"text": "DRAFT", "color": (0.35, 0.35, 0.40)},
    "Confidential": {"text": "CONFIDENTIAL", "color": (0.80, 0.10, 0.10)},
    "Copy":         {"text": "COPY", "color": (0.45, 0.45, 0.50)},
    "Sample":       {"text": "SAMPLE", "color": (0.45, 0.45, 0.50)},
}

# What "over the whole page" means as style: translucent, diagonal, unframed and *under* the
# content. The watermark look, expressed as defaults on the one descriptor rather than as a second
# kind of mark — which is the whole argument for one engine.
WHOLE_PAGE_DEFAULTS: dict = {
    "color": (0.45, 0.45, 0.50),
    "border_width": 0.0,     # a frame around the page edge reads as a border, not a mark
    "angle": 45.0,     # north-east: counter-clockwise positive (M69.9)
    "opacity": 0.18,
    # **Over the content, not under it** (M69.5). `under=True` is a real capability and still works,
    # but it is the wrong *default*: it puts the mark beneath everything the page draws, and most
    # real-world PDFs paint an opaque full-page background — so the mark bakes correctly, lands in
    # the text layer, and is completely invisible. (Reported as "does not save with the document";
    # the text was in fact in the saved file.) At these opacities drawing over the content is what a
    # watermark is supposed to look like anyway: visible, with the page's own text fully legible
    # through it. See PROGRESS.md Open follow-ups for making `under` itself honest.
    "under": False,
}


def preset_mark(name: str, rect: tuple[float, float, float, float],
                whole_page: bool = False, **overrides) -> Stamp:
    """The :class:`Stamp` for preset ``name`` at ``rect``.

    ``whole_page`` layers on :data:`WHOLE_PAGE_DEFAULTS` — the watermark look — under the preset's
    own text and colour. Unknown names fall back to the name itself as the text, so a caller can
    never end up with no mark at all.
    """
    fields = dict(WHOLE_PAGE_DEFAULTS) if whole_page else {}
    fields.update(MARK_PRESETS.get(name, {"text": name.upper()}))
    if whole_page:
        fields["color"] = WHOLE_PAGE_DEFAULTS["color"]   # the preset supplies the word, not the ink
    fields.update(overrides)
    return Stamp(rect=rect, **fields)


# ---- rendering ------------------------------------------------------------------


@lru_cache(maxsize=4096)
def _measure_free_height(width: float, height: float, text: str, fontsize: float) -> float:
    """The cached core of :func:`_free_height`, keyed on plain scalars so it is hashable.

    **Memoised because it is both pure and startlingly expensive.** Each call opens a throwaway
    PDF, embeds the font and lays the text out; :func:`_fit_fontsize` needs fourteen of them for a
    single mark. Repainting a 320-page document watermarked on every page therefore ran ~4500 of
    these — about 8 seconds of the ~9 the viewer spent rebuilding after one edit — to compute the
    *same answer* 320 times over, since the pages are the same size and the mark is the same mark.

    The inputs fully determine the result (the font is the fixed :data:`STAMP_FONT`), so the cache
    can never go stale. It is bounded, so a document of genuinely distinct marks degrades to the
    old cost rather than growing without limit.
    """
    scratch = fitz.open()
    page = scratch.new_page(width=width, height=height)
    try:
        return page.insert_textbox(
            fitz.Rect(0, 0, width, height),
            text, fontsize=fontsize, fontname=STAMP_FONT, align=fitz.TEXT_ALIGN_CENTER,
        )
    finally:
        scratch.close()


def _free_height(box: fitz.Rect, text: str, fontsize: float) -> float:
    """Vertical space ``text`` would leave unused in ``box`` at ``fontsize``; negative = doesn't fit.

    Measured on a **throwaway page**, never the real one. ``insert_textbox`` is the only way to ask
    PyMuPDF this question, and it answers by *drawing* — even at ``render_mode=3`` (invisible) the
    glyphs still land in the content stream and come back out of ``get_text``. Measuring on the page
    we are about to draw on therefore stamps everything twice, once invisibly.
    """
    return _measure_free_height(max(box.width, 1.0), max(box.height, 1.0), text, fontsize)


@lru_cache(maxsize=2048)
def _text_width(text: str, fontsize: float) -> float:
    """Widest authored line of ``text`` at ``fontsize``. Cached alongside
    :func:`_measure_free_height` — the same binary search calls it just as often, and it too walks
    the font's char-width table on every call."""
    return max(fitz.get_text_length(line, fontname=STAMP_FONT, fontsize=fontsize)
               for line in text.split("\n"))


def _fits(box: fitz.Rect, text: str, fontsize: float) -> bool:
    """Does ``text`` fit ``box`` at ``fontsize`` **without wrapping**?

    The no-wrap rule is what keeps a stamp looking like a stamp: left to itself ``insert_textbox``
    will happily satisfy a narrow box by breaking ``DRAFT`` into ``DR`` / ``AFT``, which is never
    what someone dragging a stamp box wants. Width is checked per authored line (an explicit newline
    is honoured), height by measurement.
    """
    return _text_width(text, fontsize) <= box.width and _free_height(box, text, fontsize) >= 0


def _fit_fontsize(box: fitz.Rect, text: str) -> float:
    """The largest size at which ``text`` fits ``box`` on one line per authored line."""
    low, high, best = _MIN_FONTSIZE, _MAX_FONTSIZE, _MIN_FONTSIZE
    for _ in range(14):
        mid = (low + high) / 2.0
        if _fits(box, text, mid):
            best, low = mid, mid
        else:
            high = mid
    return best


def _text_padding(mark: Stamp) -> float:
    """The gap :func:`_draw_text` leaves between the mark's box and its text, per side."""
    return max(mark.border_width, 0.0) + _TEXT_INSET


def art_size(mark) -> tuple[float, float]:
    """The size :func:`render_mark_document` builds ``mark``'s artwork at.

    For an **auto-fit** mark this is the rect: the box was dragged, and the artwork's whole job is
    to fill it. For a **pinned-size** stamp it is :func:`natural_size` instead — the artwork is the
    size the text actually is, and the rect's job becomes holding it (see :func:`placement_size`).
    Decoupling the two is what lets a pinned size survive rotation.
    """
    if isinstance(mark, Stamp) and mark.fontsize:
        return natural_size(mark)
    x0, y0, x1, y1 = mark.rect
    return max(abs(x1 - x0), 1.0), max(abs(y1 - y0), 1.0)


def rotated_extent(width: float, height: float, angle: float) -> tuple[float, float]:
    """The axis-aligned bounding box of a ``width`` × ``height`` box turned by ``angle``."""
    if not angle:
        return width, height
    radians = math.radians(angle)
    cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
    return width * cos + height * sin, width * sin + height * cos


def placement_size(mark) -> tuple[float, float]:
    """The rect a **pinned-size** mark must occupy to render at exactly that size.

    ``show_pdf_page`` fits a mark's *rotated* artwork inside its rect and centres it there, so the
    scale it applies is ``min(rect / rotated-artwork-extent)``. Handing it the rotated extent as the
    rect therefore makes that scale exactly ``1`` — the pinned size is drawn at the pinned size.

    This is what a rotated stamp got wrong: with the rect sized to the *unrotated* text, a 120pt
    stamp at −45° was fitted down to 40pt, and its artwork sat diagonally inside a box shaped for
    horizontal text — so the selection box did not agree with what was drawn, and resizing it looked
    like distortion.
    """
    return rotated_extent(*art_size(mark), mark.angle)


def art_scale(mark) -> float:
    """The factor the artwork is drawn at when placed into ``mark.rect`` (1.0 = its own size).

    ``show_pdf_page`` *fits* — it scales the rotated artwork to the rect in both directions, up as
    readily as down. That is right for an auto-fit mark, whose artwork **is** the rect and whose
    whole contract is "fill the box I dragged". It is wrong for a **pinned size**, where being
    enlarged to fill a roomier box is precisely what "pinned" rules out — so a pinned mark is
    capped at 1.0 and simply sits centred in a rect larger than it needs.

    Shrinking is *not* capped in either case: a mark that would overflow its rect is scaled down
    rather than allowed to spill, which is the same policy :func:`_draw_text` applies to text
    overflowing its box.
    """
    rect_w = max(abs(mark.rect[2] - mark.rect[0]), 1e-6)
    rect_h = max(abs(mark.rect[3] - mark.rect[1]), 1e-6)
    spanned_w, spanned_h = rotated_extent(*art_size(mark), mark.angle)
    scale = min(rect_w / max(spanned_w, 1e-6), rect_h / max(spanned_h, 1e-6))
    if isinstance(mark, Stamp) and mark.fontsize:
        return min(scale, 1.0)
    return scale


def art_target_rect(mark) -> tuple[float, float, float, float]:
    """The sub-rect of ``mark.rect`` the artwork is actually placed into, centred within it.

    Handing ``show_pdf_page`` this instead of the whole rect is what makes :func:`art_scale`'s cap
    real: fitting into a box already the artwork's own size is the identity. For an auto-fit mark it
    is the rect itself (or, when rotated, the centred sub-rect the fit would have produced anyway),
    so nothing about the pre-existing behaviour moves.
    """
    scale = art_scale(mark)
    spanned_w, spanned_h = rotated_extent(*art_size(mark), mark.angle)
    width, height = spanned_w * scale, spanned_h * scale
    center_x = (mark.rect[0] + mark.rect[2]) / 2.0
    center_y = (mark.rect[1] + mark.rect[3]) / 2.0
    return (center_x - width / 2.0, center_y - height / 2.0,
            center_x + width / 2.0, center_y + height / 2.0)


def size_for_page(mark: Stamp, page_width: float, page_height: float) -> float:
    """``mark.fontsize`` reduced — never raised — until :func:`placement_size` fits the page.

    A stamp bigger than the paper is not a stamp: it cannot be centred, cannot be seen whole, and
    on a rotated mark it is the *diagonal* that overflows, which is not something a user can be
    expected to work out from a point size. So the typed size is honoured when it fits and otherwise
    becomes the largest that does — the same policy :func:`_draw_text` already applies to a pinned
    size that overflows its box, rather than a second rule to learn.

    Iterative because :func:`natural_size` is affine, not linear, in the font size (the padding is a
    constant); each pass overshoots slightly and converges in two or three.
    """
    from dataclasses import replace

    size = mark.fontsize
    if not size:
        return size
    for _ in range(8):
        width, height = placement_size(replace(mark, fontsize=size))
        if width <= page_width and height <= page_height:
            break
        size = max(size * min(page_width / width, page_height / height), _MIN_FONTSIZE)
        if size <= _MIN_FONTSIZE:
            break
    return size


def natural_size(mark: Stamp) -> tuple[float, float]:
    """The box ``mark``'s text needs at its **pinned** ``fontsize`` — the "hug the text" size.

    The counterpart to :func:`_fit_fontsize`: that one answers "how big can the text be in this
    box", this one answers "how big must the box be for text this size". It is what lets a stamp
    with an explicit point size be *dropped* rather than dragged — the placement gesture stops
    having to guess a rectangle whose auto-fit happens to land on the size the user asked for.

    Height is **measured, not modelled**: ``insert_textbox`` returns the height it left unused, so
    probing a deliberately over-tall box and subtracting the remainder gives the exact line-height
    PyMuPDF will use, including its own leading. Guessing ``fontsize × 1.2`` would clip descenders
    on some faces and pad on others.
    """
    fontsize = mark.fontsize or _MIN_FONTSIZE
    lines = mark.text.split("\n") or [""]
    # A hair of slack: at *exactly* the measured width, float rounding inside insert_textbox can
    # decide the last glyph does not fit and wrap it, which would corrupt the height measurement.
    width = max(_text_width(mark.text, fontsize), 1.0) + 1.0
    probe = fitz.Rect(0, 0, width, fontsize * (len(lines) + 2) * 2.0)
    used = probe.height - max(_free_height(probe, mark.text, fontsize), 0.0)
    pad = _text_padding(mark)
    return width + 2 * pad, max(used, fontsize) + 2 * pad


def _alpha_channel(pix: fitz.Pixmap) -> bytes:
    """``pix``'s alpha bytes, one per pixel. The strided slice runs in C, so this stays fast on a
    multi-megapixel phone photo where a Python loop would not."""
    return pix.samples[pix.n - 1:: pix.n]


def _with_opacity(pix: fitz.Pixmap, opacity: float) -> fitz.Pixmap:
    """``pix`` with its alpha scaled by ``opacity`` — an image has no ``/CA`` to set, so translucency
    has to be carried in the pixels. An already-transparent PNG keeps its shape: existing alpha is
    scaled, not replaced."""
    if opacity >= 1.0:
        return pix
    if not pix.alpha:
        pix = fitz.Pixmap(pix, 1)                 # add a fully-opaque alpha channel
    table = bytes(min(255, int(value * opacity)) for value in range(256))
    pix.set_alpha(_alpha_channel(pix).translate(table), premultiply=0)  # not premultiplied
    return pix


def _drop_white(pix: fitz.Pixmap, threshold: float) -> fitz.Pixmap:
    """``pix`` with light pixels made transparent — the "phone photo of a signature" fix (M63).

    Luminance comes from MuPDF's own greyscale conversion, which gives exactly one byte per pixel in
    C; the per-pixel decision is then a single 256-entry :meth:`bytes.translate`. Both steps run at
    C speed, which matters: a 12-megapixel photo is a realistic input and a Python loop over it would
    stall the UI for seconds.

    Pixels at or above ``threshold`` go fully transparent; below it a short ramp keeps antialiased
    stroke edges soft rather than jagged. Any alpha the image already carries is **intersected**, not
    replaced, so a transparent PNG never gains opaque pixels here.
    """
    cutoff = max(1, min(255, int(threshold * 255)))
    ramp = max(1, int(0.15 * 255))                       # soft edge below the cutoff
    table = bytes(
        0 if value >= cutoff
        else 255 if value <= cutoff - ramp
        else int(255 * (cutoff - value) / ramp)
        for value in range(256)
    )
    gray = fitz.Pixmap(fitz.csGRAY, pix)                 # luminance per pixel, computed in C
    try:
        # `Pixmap(csGRAY, pix)` *keeps* the source's alpha channel, so an already-transparent PNG
        # yields 2 bytes per pixel, not 1. Take the first channel explicitly (a C-level strided
        # slice) — reading `gray.samples` whole would misalign the mask against the pixels.
        keyed = gray.samples[0:: gray.n].translate(table)
    finally:
        gray = None
    if not pix.alpha:
        pix = fitz.Pixmap(pix, 1)
        pix.set_alpha(keyed, premultiply=0)
        return pix
    existing = _alpha_channel(pix)                       # honour the image's own transparency too
    pix.set_alpha(bytes(min(a, b) for a, b in zip(existing, keyed)), premultiply=0)
    return pix


def render_mark_document(mark) -> fitz.Document:
    """A throwaway one-page PDF holding ``mark``'s artwork at its unrotated :func:`art_size`.

    The single generator behind stamps, signatures and watermarks — the caller places the result with
    ``show_pdf_page``. Exposed (rather than kept private to :func:`apply_content_marks`) because the
    viewer's live placement preview renders exactly this, so what is dragged around on screen is the
    same artwork that bakes at save. The caller owns the document and must close it.
    """
    width, height = art_size(mark)
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    box = fitz.Rect(0, 0, width, height)
    try:
        if isinstance(mark, ImageStamp):
            _draw_image(page, box, mark)
        else:
            _draw_text(page, box, mark)
    except Exception:
        doc.close()
        raise
    return doc


def _draw_image(page: fitz.Page, box: fitz.Rect, mark: ImageStamp) -> None:
    """Draw an :class:`ImageStamp`'s file into ``box``, honouring its own alpha and ``opacity``.

    A missing or unreadable file draws **nothing** rather than raising: the path is the user's (it
    can move between placing the signature and saving), and losing the image must not cost them the
    save. The mark stays in the model, so re-pointing it and saving again works.
    """
    try:
        pix = fitz.Pixmap(mark.image_path)
    except Exception:
        return
    try:
        if pix.colorspace is not None and pix.colorspace.n > 3:   # CMYK → RGB for insert_image
            pix = fitz.Pixmap(fitz.csRGB, pix)
        if mark.white_to_alpha:
            pix = _drop_white(pix, mark.white_threshold)
        page.insert_image(box, pixmap=_with_opacity(pix, mark.opacity), keep_proportion=True)
    finally:
        pix = None


def _draw_text(page: fitz.Page, box: fitz.Rect, mark: Stamp) -> None:
    """Draw a :class:`Stamp`'s frame + text into ``box``."""
    inset = max(mark.border_width, 0.0)
    if mark.border_width > 0 or mark.fill_color is not None:
        shape = page.new_shape()
        frame = box + (inset / 2, inset / 2, -inset / 2, -inset / 2)
        # radius is a fraction of the shorter side; 0 draws square corners.
        shape.draw_rect(frame, radius=mark.border_radius or None)
        shape.finish(
            color=mark.color if mark.border_width > 0 else None,
            fill=mark.fill_color,
            width=mark.border_width,
            stroke_opacity=mark.opacity,
            fill_opacity=mark.opacity,
        )
        shape.commit()

    pad = _text_padding(mark)
    text_box = box + (pad, pad, -pad, -pad)
    if text_box.is_empty or not mark.text:
        return
    # A pinned size that does not fit is shrunk rather than honoured: `insert_textbox` draws
    # *nothing* when the text overflows, and a stamp the user placed but cannot see is a worse
    # outcome than one that is a few points smaller on an unusually small page.
    fontsize = mark.fontsize if mark.fontsize and _fits(text_box, mark.text, mark.fontsize) else 0.0
    fontsize = fontsize or _fit_fontsize(text_box, mark.text)
    # insert_textbox anchors to the top and returns the unused height; shifting the box down by half
    # of it centres the text, which is what a stamp needs (the box was dragged around it).
    free = max(_free_height(text_box, mark.text, fontsize), 0.0)
    page.insert_textbox(
        text_box + (0, free / 2, 0, 0), mark.text, fontsize=fontsize, fontname=STAMP_FONT,
        color=mark.color, align=fitz.TEXT_ALIGN_CENTER,
        fill_opacity=mark.opacity, stroke_opacity=mark.opacity,
    )


def apply_content_marks(page: fitz.Page, marks: tuple) -> None:
    """Bake every content mark in ``marks`` into ``page``'s content stream (materialise + render).

    Runs **after** :func:`model.page_edits.apply_redactions` — a redaction rewrites the page content
    and would otherwise erase a stamp drawn under it — and **before** ``apply_annotations``, so the
    editable annotation overlays keep sitting on top of a baked stamp exactly as they do on the page's
    own content. Non-content descriptors are ignored, so the caller passes the page's whole tuple.

    Each mark is placed with ``show_pdf_page`` into :func:`art_target_rect` — a centred sub-rect of
    the mark's own rect — so the rect stays the promise ("the mark lands here, at any angle") while
    a pinned font size is never scaled up to fill it. ``under`` selects ``overlay=False``, putting
    the mark beneath the existing content (the watermark case).

    ``show_pdf_page``'s ``rotate`` is counter-clockwise-positive, the same sense as
    :class:`Stamp.angle`, so the angle passes through unchanged: ``+45`` gives the north-east
    diagonal in the file exactly as it does on screen.

    Both signs have been wrong here before, in opposite ways, which is why they are spelled out.
    M69.1: the angle was passed straight through while the *descriptor* was clockwise-positive, so
    every rotated mark baked as its own mirror image — a stamp tilting one way on the page and the
    other in the thumbnail. M69.9: the descriptor itself was flipped to be genuinely
    counter-clockwise, which cancelled the negation this had needed.
    """
    for mark in marks:
        if not is_content_mark(mark):
            continue
        rect = fitz.Rect(mark.rect).normalize()
        if rect.is_empty or rect.is_infinite:
            continue
        art = render_mark_document(mark)
        try:
            target = fitz.Rect(art_target_rect(mark)).normalize()
            page.show_pdf_page(target, art, 0, rotate=mark.angle, overlay=not mark.under)
        finally:
            art.close()
