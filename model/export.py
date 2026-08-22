"""Export — write a *derived* copy of the document in a chosen format (PLAN.md, M31.5 / M36).

Distinct from Save / Save As, which write the **editable** document (annotations stay annotations,
our marks round-trip on reopen — M31). Export writes a locked / derived artifact:

* **Flattened PDF** (M31.5): every annotation and form widget is baked into page **content** via
  PyMuPDF ``Document.bake()`` — the text layer is **preserved** (body text *and* the baked
  annotation text stay real, searchable text; nothing is rasterised), but the marks are now page
  content, not editable annotations, so they can't be moved / removed / re-edited in any tool. It is
  the opt-out counterpart to M31's round-trip: Save As stays editable, Export → PDF locks.
* **Images** (M36): the selected page(s) → PNG / JPEG at a chosen DPI, one file per page.
* **Selected pages as PDF** (M51): the selected page(s) → a new PDF, extracted **object-level**
  through the ordinary materialise path — so unlike the other formats it stays *editable* (a
  Save-like artifact of a page subset): the text layer, form fields, and our round-trippable
  annotations all carry, and the origin bookmarks / internal links whose targets were extracted
  are remapped to the new page numbers (the rest are dropped).
* **Reduced-size PDF** (M52): the whole document with its images **recompressed lossily**
  (downsampled to a target dpi, re-encoded JPEG at a quality) + fonts subset. This is the lossy
  tier only — a normal Save already runs the lossless cleanups
  (:func:`~model.edit_engine.write_options`), which is why the reported "before" is what a plain
  Save would write, not the stale on-disk size: the delta shown is the true cost/benefit of the
  lossy step alone.

Every write here goes through the one option set the Save owns
(:func:`~model.edit_engine.write_options`). They were four copies of a literal, and when M93 added
``use_objstms=1`` it reached exactly one of them — leaving the feature whose whole purpose is a
smaller file returning 146 KB *more* than a plain Save on one document, and reporting a "before"
baseline it never actually wrote (M111).

Every format shares the edits-applied render (:meth:`PyMuPDFEngine.render_output`), so an export
reflects the same page order / rotation / redactions / annotations / fills a Save would write — and
a pending (unsaved) redaction is applied destructively in the *exported* copy without committing it
in the working document (the throwaway render keeps the redaction point-of-no-return tied to Save).

Headless and GUI-free; the ``File ▸ Export`` menu wiring lives in ``main_window``.
"""

from __future__ import annotations

import os

import pymupdf as fitz

from model.edit_engine import GARBAGE_DEDUP, PyMuPDFEngine, write_options
from model.virtual_document import VirtualDocument

_JPEG_EXTS = (".jpg", ".jpeg")


def export_flattened_pdf(vdoc: VirtualDocument, out_path: str) -> None:
    """Write ``vdoc`` to ``out_path`` as a **flattened** PDF.

    Builds the edits-applied output (exactly what a Save would write), then ``Document.bake()``
    turns every annotation and form widget into permanent page content — text-preserving, not
    rasterised — and saves. The result is a locked copy: the marks are page content, no longer
    editable annotations.

    Written with the shared option set at the **deduplicating** level (M111). A Save leaves a copy
    of the origin as packed as it arrived (M110) — but ``bake()`` is not a copy, it is a rewrite
    *this* export performs, and it duplicates streams the way the graft does: every widget of a form
    becomes page content, and a form's widgets share appearance streams. Measured, level 4 is
    removing our own mess rather than tidying the user's file — ``f8949.pdf`` flattens to 46,680 B
    against 81,578, ``ssa-1-bk.pdf`` to 107,676 against 144,709 — which is the same argument that
    keeps it on the graft route. Before M111 this carried its own copy of the option literal and so
    never got M93's ``use_objstms``.
    """
    out = PyMuPDFEngine().render_output(vdoc)
    try:
        out.bake()  # annotations + form widgets → permanent page content (text layer preserved)
        out.save(out_path, **write_options(GARBAGE_DEDUP))
    finally:
        out.close()


def export_selected_pages(vdoc: VirtualDocument, page_indices, out_path: str) -> None:
    """Write the pages at ``page_indices`` (indices into the live order, deduped, document order)
    to ``out_path`` as a new PDF — the object-level extract (M51).

    Materialises a :meth:`VirtualDocument.subset`, so the output is exactly what a Save would
    write for those pages: text layer / forms / annotations carried, rotation + crop + fills
    applied, a *pending* redaction applied destructively **in the extracted copy only** (the
    working document keeps it, still undoable — same side-artifact rule as the other exports),
    and the origin bookmarks + internal links remapped to the extracted page numbers.
    """
    indices = sorted(set(page_indices))
    if not indices:
        return
    PyMuPDFEngine().materialize(vdoc.subset(indices), out_path)


def export_reduced_pdf(
    vdoc: VirtualDocument, out_path: str, dpi: int, jpg_quality: int
) -> tuple[int, int]:
    """Write ``vdoc`` to ``out_path`` with images recompressed **lossily** and fonts subset (M52).

    Builds the edits-applied output (exactly what a Save would write), then downsamples every
    image above ``dpi`` to ``dpi`` and re-encodes it as JPEG at ``jpg_quality`` (PyMuPDF
    ``rewrite_images``), subsets the embedded fonts to the glyphs actually used, and saves with
    the usual lossless cleanups. The removed image detail is **gone from the copy permanently** —
    the working document and its file are untouched (side-artifact rule).

    Returns ``(before, after)`` byte sizes — *actual* values, no estimates: ``before`` is what a
    plain Save of the current document would write (so the delta is the lossy tier's true effect,
    not the lossless cleanup a Save gives anyway), ``after`` is the written file's size.

    **``before`` is measured with the Save's own keywords** (M111), not with a second copy of them.
    It was computed without ``use_objstms`` while a real Save had used it since M93, so it
    overstated the starting size — by 143,143 B on a 7 MB prospectus — and therefore overstated how
    much this feature had saved. A number whose entire job is to be the honest baseline has to be
    measured with the thing it is a baseline for, which is why
    :meth:`PyMuPDFEngine.save_keywords` is public: it carries the encryption a Save writes as well
    as the cleanup options, and both move the size.

    **The write itself uses the deduplicating level whichever route the document took.** A Save
    leaves a copy of the origin as packed as it arrived (M110), but this is the one operation where
    the caller has explicitly asked for a smaller file — and the one that *creates* duplicate
    streams, since re-encoding every image to JPEG at one quality can turn two different images
    into identical ones, which only level 4 merges. So it names
    :data:`~model.edit_engine.GARBAGE_DEDUP` deliberately rather than inheriting the Save's choice.
    """
    engine = PyMuPDFEngine()
    out = engine.render_output(vdoc)
    try:
        before = len(out.tobytes(**engine.save_keywords(vdoc)))
        # threshold must sit strictly above target (a rewrite_images rule); +1 keeps the promise
        # "images *above* the target resolution are downsampled" exact — a page image already at
        # the target is left alone, everything above it comes down to the target.
        out.rewrite_images(
            dpi_threshold=dpi + 1, dpi_target=dpi, quality=jpg_quality, lossy=True, lossless=True
        )
        out.subset_fonts()
        out.save(out_path, **write_options(GARBAGE_DEDUP))
    finally:
        out.close()
    return before, os.path.getsize(out_path)


# How far outside the page a clip may stray before it is an error rather than float noise. A box
# that came from `search` is computed, not typed, so its edge can land a ten-thousandth of a point
# past the page edge; 0.01 pt is sub-pixel at any dpi anything here will render, so the tolerance
# cannot hide a clip that is genuinely in the wrong place.
_CLIP_TOLERANCE = 0.01


def resolve_clip(page: fitz.Page, clip) -> fitz.Rect | None:
    """Validate a ``[x0, y0, x1, y1]`` region against ``page``; ``None`` means the whole page (M99).

    Page points — the space ``search`` reports boxes in and ``redact_regions`` consumes them from,
    which is the composition this exists for.

    **One rectangle, deliberately, even though a ``search`` hit carries a list.** Since #250 a hit
    occupies one box *per line*, so a match wrapping a line break has several; the caller unions
    them (``fitz.Rect`` folds a list in one ``|=``) and gets a region covering the whole match plus
    whatever sits between the lines. That union is right for looking and wrong for deleting, which
    is why ``redact_regions`` takes the boxes separately and this takes one rect: a render showing
    a little extra context is helpful, and a redaction removing a little extra is data loss.

    **A clip that is not wholly on the page is an error, not a clamp**, and the reason is specific
    rather than a general taste for strictness: the bridge's ``render_page`` returns an *image
    block*, so its reply has nowhere to carry a note. PyMuPDF would quietly intersect an overhanging
    clip with the page and hand back a smaller pixmap, and the caller — who sized a layout from the
    clip it asked for — would receive different pixels with nothing to say so. The error is the only
    channel available, so it names the page rect: the caller can correct in one step instead of
    guessing which edge overhung. ``export_images`` returns JSON and could have reported an
    adjustment instead, but two imaging tools that disagreed about what a clip means would be worse
    than one strict rule. Lives here, beside the rasterisation it constrains, so the bridge and the
    app's own Export cannot drift into two validators with two answers.

    **The clip is read in *unrotated* space and returned in *displayed* space** (M99.1, TC-008
    Finding 3), and the split is the whole correctness of this function on a rotated page.
    ``search_for`` reports boxes in the unrotated page — byte-identical coordinates whether the page
    carries ``/Rotate 0`` or ``/Rotate 90`` — and ``redact_regions`` consumes them there. But
    ``page.rect`` is the *displayed* rect, which swaps width and height under a quarter turn, and
    that is also the space ``get_pixmap`` clips in. Validating against ``page.rect`` therefore put
    ``clip`` on the opposite side of the rotation from every box a caller has, and it failed twice
    over: a ``search`` box landed inside the displayed rect and **rendered blank** (measured: 671
    dark pixels unrotated, 0 at ``/Rotate 90``), while a box beyond the displayed width was
    **refused as off-page** although ``search`` had just returned it for that same page. So the
    bounds check runs against ``page.rect * page.derotation_matrix`` — the unrotated rect, the one
    the caller's numbers are in — and the result is mapped through ``page.rotation_matrix`` for the
    rasteriser. Both matrices are the identity on an unrotated page, so nothing changes there.
    """
    if clip is None:
        return None
    try:
        values = [float(v) for v in clip]
    except (TypeError, ValueError):
        raise ValueError(f"clip must be four numbers [x0, y0, x1, y1]; got {clip!r}") from None
    if len(values) != 4:
        raise ValueError(f"clip must be [x0, y0, x1, y1] in page points; got {clip!r}")
    rect = fitz.Rect(*values)
    if rect.x0 >= rect.x1 or rect.y0 >= rect.y1:
        raise ValueError(f"clip {values} is empty or inverted")
    # The unrotated rect — what `search` measures against, not what the reader sees.
    bounds = page.rect * page.derotation_matrix
    if (rect.x0 < bounds.x0 - _CLIP_TOLERANCE or rect.y0 < bounds.y0 - _CLIP_TOLERANCE
            or rect.x1 > bounds.x1 + _CLIP_TOLERANCE or rect.y1 > bounds.y1 + _CLIP_TOLERANCE):
        raise ValueError(
            f"clip {values} lies outside page {page.number + 1}, which is "
            f"[{bounds.x0:g}, {bounds.y0:g}, {bounds.x1:g}, {bounds.y1:g}] in points"
            + (f" (the page is rotated {page.rotation}°; these are the unrotated coordinates "
               "`search` reports boxes in)" if page.rotation else "")
        )
    # Absorb the tolerance: a clip allowed through a hair over the edge must still be a rect
    # `get_pixmap` can render, and the intersection is that clip to within a hundredth of a point.
    # Then across to displayed space, which is where the rasteriser cuts.
    return (rect & bounds) * page.rotation_matrix


def export_page_images(
    vdoc: VirtualDocument,
    page_indices,
    base_path: str,
    dpi: int = 150,
    jpg_quality: int = 90,
    clip=None,
    number_all: bool = False,
) -> list[str]:
    """Export pages of the **edits-applied** output to image files — one file per page (M36).

    ``page_indices`` are indices into the live page order (``ordered[]``), the same indices the
    viewer / thumbnails use. The image **format** comes from ``base_path``'s extension
    (``.png`` / ``.jpg`` / ``.jpeg``). A single page writes ``base_path`` verbatim; with more than
    one, the document page number is appended, zero-padded — ``report.png`` → ``report-01.png`` …
    Returns the written paths in order.

    Rasterised from :meth:`PyMuPDFEngine.render_output` at ``dpi`` (1 pt = dpi/72 px), so each image
    reflects the page order / rotation / annotations / fills / redactions a Save would write — and a
    *pending* redaction exports as removed without committing it (the render copy is a throwaway).

    ``clip`` (M99) narrows every exported page to the same ``[x0, y0, x1, y1]`` region in page
    points. It is validated **per page**, not once: page sizes vary within a document, so a region
    that sits comfortably on page 1 can overhang page 2, and validating only the first would export
    that page silently short.

    ``number_all`` forces the page suffix on even for a single page (M104). It defaults off because
    the **app's** Export writes the filename the user typed into a save dialog, and turning
    ``report.png`` into ``report-1.png`` behind them would be its own small betrayal. The **bridge**
    passes it, because there the filename is derived rather than chosen and the "verbatim when
    single" rule made the scheme non-uniform — and, worse, made two clips of one page collide on one
    name, which is precisely the job ``clip`` was added for (TC-008 Finding 1).
    """
    indices = list(page_indices)
    if not indices:
        return []
    root, ext = os.path.splitext(base_path)
    is_jpeg = ext.lower() in _JPEG_EXTS
    single = len(indices) == 1 and not number_all
    # Padded to the **document's** page count on the bridge path, so two exports from one document
    # into one directory agree: `pages: [1..60]` and `pages: [5, 72, 500]` produced `-01` and `-005`
    # from the same file, and `-005` then sorts before `-01` (TC-011 retest). The app's Export keeps
    # padding to the request, because there the user picked the pages and a width derived from a
    # page count they never mentioned would be the surprising choice — `number_all` is already the
    # bridge-vs-app discriminator for exactly this kind of derived-vs-chosen filename question.
    widest = vdoc.page_count if number_all else max(i + 1 for i in indices)
    pad = len(str(widest))
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)

    out = PyMuPDFEngine().render_output(vdoc)
    written: list[str] = []
    try:
        # Validate the whole set before writing anything: a clip that fails on page 7 of 10 would
        # otherwise leave six files behind from a call that raised.
        rects = {index: resolve_clip(out[index], clip) for index in indices}
        for index in indices:
            target = base_path if single else f"{root}-{index + 1:0{pad}d}{ext}"
            pix = out[index].get_pixmap(matrix=matrix, clip=rects[index], alpha=False)
            if is_jpeg:
                pix.save(target, jpg_quality=jpg_quality)
            else:
                pix.save(target)
            written.append(target)
    finally:
        out.close()
    return written
