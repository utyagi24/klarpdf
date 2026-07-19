"""Crop pages (PLAN.md §GUI feature roadmap, M48). Model + offscreen GUI.

``crop_override`` rides the PageRef like ``rotation_override`` (absolute rect in unrotated
content coords, snapshots for undo, follows reorder/copy); materialise applies it via
``set_cropbox``. Crop **hides** — the content stays in the file (Redact removes): the honesty
assertion below un-crops a saved file and watches the text come back. The viewer displays the
crop live (geometry, overlay mapping, clip render); Remove Crop restores the full MediaBox,
including one the file arrived with.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from model.edit_commands import CropPagesCommand, ResetCropCommand
from model.edit_engine import PyMuPDFEngine
from model.virtual_document import PageRef, VirtualDocument
from store.settings import Settings
from viewer.tools import ArmedTool

# Content pages are 400x600; the crop keeps the top-left area holding "KEEP" and hides "SECRET".
_PAGE_W, _PAGE_H = 400, 600
_CROP = (50.0, 50.0, 350.0, 300.0)


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    for w in list(qapp._windows.values()):
        w.close()
    qapp._windows.clear()
    yield qapp
    for w in list(qapp._windows.values()):
        w.undo_stack.setClean()
        w.close()
    qapp._windows.clear()


def _build_pdf(path, pages=3, precrop=None):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
        page.insert_text((60, 100), f"KEEP {i}", fontsize=12)       # inside _CROP
        page.insert_text((60, 500), f"SECRET {i}", fontsize=12)     # outside _CROP
        if precrop:
            page.set_cropbox(fitz.Rect(*precrop))
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def pdf(tmp_path):
    return _build_pdf(tmp_path / "crop.pdf")


@pytest.fixture
def vdoc(pdf):
    v = VirtualDocument.from_path(pdf)
    yield v
    v.close()


def _reopen(path):
    return fitz.open(path)


# ---- model ----------------------------------------------------------------------


def test_with_crop_validates_and_rides_positionally(vdoc):
    ref = vdoc.ordered[0]
    assert ref.crop_override is None
    with pytest.raises(ValueError):
        ref.with_crop((10, 10, 10, 200))  # zero width
    # Existing positional construction (cross-window carry) still works, crop last.
    clone = PageRef(ref.source_id, ref.source_page_index, 90, (), _CROP)
    assert clone.crop_override == _CROP and clone.rotation_override == 90


def test_set_crop_clamps_to_the_page(vdoc):
    vdoc.set_crop([0], (-20.0, -20.0, 10_000.0, 10_000.0))
    assert vdoc.ordered[0].crop_override == (0.0, 0.0, float(_PAGE_W), float(_PAGE_H))
    assert vdoc.dirty


def test_crop_command_undo_redo(vdoc):
    cmd = CropPagesCommand(vdoc, [0, 2], _CROP)
    cmd.redo()
    assert vdoc.ordered[0].crop_override == _CROP
    assert vdoc.ordered[1].crop_override is None
    assert vdoc.ordered[2].crop_override == _CROP
    cmd.undo()
    assert all(r.crop_override is None for r in vdoc.ordered)


def test_crop_follows_a_reorder(vdoc):
    vdoc.set_crop([0], _CROP)
    vdoc.move_pages([0], 3)  # cropped page to the end
    assert vdoc.ordered[2].crop_override == _CROP
    assert vdoc.ordered[0].crop_override is None


def test_materialise_applies_the_crop(vdoc, tmp_path):
    out = str(tmp_path / "out.pdf")
    vdoc.set_crop([0], _CROP)
    PyMuPDFEngine().materialize(vdoc, out)
    with _reopen(out) as doc:
        assert (doc[0].rect.width, doc[0].rect.height) == (300.0, 250.0)  # crop dims
        assert "SECRET 0" not in doc[0].get_text()  # hidden from the default (visible) view
        assert doc[1].rect.width == _PAGE_W  # uncropped page untouched


def test_crop_hides_but_does_not_remove(vdoc, tmp_path):
    """The honesty guarantee behind the UI copy: un-crop the *saved* file → the text comes back.
    (Redact is the removal tool; its own tests assert the opposite — the text is gone.)"""
    out = str(tmp_path / "out.pdf")
    vdoc.set_crop([0], _CROP)
    PyMuPDFEngine().materialize(vdoc, out)
    with _reopen(out) as doc:
        doc[0].set_cropbox(doc[0].mediabox)
        assert "SECRET 0" in doc[0].get_text()


def test_reset_crop_restores_the_full_page(vdoc, tmp_path):
    out = str(tmp_path / "out.pdf")
    vdoc.set_crop([0], _CROP)
    vdoc.reset_crop([0])
    PyMuPDFEngine().materialize(vdoc, out)
    with _reopen(out) as doc:
        assert (doc[0].rect.width, doc[0].rect.height) == (float(_PAGE_W), float(_PAGE_H))
        assert "SECRET 0" in doc[0].get_text()


def test_reset_uncrops_a_precropped_source(tmp_path):
    """Remove Crop also un-hides a crop the file *arrived* with — reset reaches the MediaBox."""
    path = _build_pdf(tmp_path / "pre.pdf", pages=1, precrop=(50, 50, 350, 300))
    v = VirtualDocument.from_path(path)
    try:
        assert v.page_is_cropped(0) is True
        assert "SECRET 0" not in v.sources[v.origin_source_id][0].get_text()
        v.reset_crop([0])
        assert v.ordered[0].crop_override == (-50.0, -50.0, 350.0, 550.0)  # MediaBox in content coords
        out = str(tmp_path / "out.pdf")
        PyMuPDFEngine().materialize(v, out)
        with _reopen(out) as doc:
            assert (doc[0].rect.width, doc[0].rect.height) == (float(_PAGE_W), float(_PAGE_H))
            assert "SECRET 0" in doc[0].get_text()
    finally:
        v.close()


def test_crop_survives_save_and_reopen_then_reset(vdoc, tmp_path):
    """Save → reopen → the page still displays cropped (page_is_cropped via the source CropBox);
    Remove Crop in the reopened doc restores the original full page."""
    out = str(tmp_path / "out.pdf")
    vdoc.set_crop([0], _CROP)
    PyMuPDFEngine().materialize(vdoc, out)
    v2 = VirtualDocument.from_path(out)
    try:
        assert v2.ordered[0].crop_override is None  # structured geometry, no override needed
        assert v2.page_is_cropped(0) is True
        v2.reset_crop([0])
        out2 = str(tmp_path / "out2.pdf")
        PyMuPDFEngine().materialize(v2, out2)
        with _reopen(out2) as doc:
            assert "SECRET 0" in doc[0].get_text()
    finally:
        v2.close()


def test_crop_composes_with_rotation(vdoc, tmp_path):
    out = str(tmp_path / "out.pdf")
    vdoc.set_rotation(0, 90)
    vdoc.set_crop([0], _CROP)
    PyMuPDFEngine().materialize(vdoc, out)
    with _reopen(out) as doc:
        # set_cropbox takes unrotated coords; the visible rect swaps with the 90° rotation.
        assert (doc[0].rect.width, doc[0].rect.height) == (250.0, 300.0)
        assert "KEEP 0" in doc[0].get_text()


# ---- viewer ---------------------------------------------------------------------


def _win(app, path):
    w = app.open_document(path)
    app.processEvents()
    return w


def test_viewer_geometry_and_mapping_reflect_the_crop(app, pdf):
    win = _win(app, pdf)
    win.undo_stack.push(CropPagesCommand(win.vdoc, [0], _CROP))
    assert win.view._unrotated_size(0) == (300.0, 250.0)
    # A content box round-trips through the scene mapping unchanged (within float noise).
    box = (60.0, 60.0, 120.0, 100.0)
    back = win.view.local_box_from_scene_rect(0, win.view.scene_rect_for_box(0, box))
    assert all(abs(a - b) < 0.01 for a, b in zip(box, back))
    # A point outside the crop maps outside the displayed page band.
    outside = win.view.scene_rect_for_box(0, (60.0, 480.0, 120.0, 520.0))
    page_band = win.view._pages[0]
    assert outside.top() > page_band["y"] + page_band["h"]


def test_viewer_renders_the_cropped_pixmap(app, pdf):
    win = _win(app, pdf)
    win.undo_stack.push(CropPagesCommand(win.vdoc, [0], _CROP))
    win.view.set_zoom(1.0)  # explicit zoom: the sticky fit re-fits on resize, timing-dependent
    pixmap = win.view._render_pixmap(0)
    assert abs(pixmap.width() - 300) <= 1 and abs(pixmap.height() - 250) <= 1


def test_crop_drag_asks_scope_and_pushes_one_undo_step(app, pdf, monkeypatch):
    win = _win(app, pdf)
    monkeypatch.setattr(win, "_ask_crop_scope", lambda page_index: [0, 1, 2])  # "All Pages"
    win.view.arm(ArmedTool.CROP)
    rect = win.view.scene_rect_for_box(0, _CROP)
    assert win.view.begin_crop_drag(rect.topLeft()) is True
    win.view.update_crop_drag(rect.bottomRight())
    win.view.finish_crop_drag()
    # The scene→content round-trip carries float noise; half a point is far below visibility.
    assert all(
        r.crop_override is not None
        and all(abs(a - b) < 0.5 for a, b in zip(r.crop_override, _CROP))
        for r in win.vdoc.ordered
    )
    win.undo_stack.undo()  # one step reverts the whole scope
    assert all(r.crop_override is None for r in win.vdoc.ordered)


def test_tiny_crop_drag_is_discarded(app, pdf, monkeypatch):
    win = _win(app, pdf)
    asked = []
    monkeypatch.setattr(win, "_ask_crop_scope", lambda page_index: asked.append(page_index) or [0])
    win.view.arm(ArmedTool.CROP)
    pt = win.view.scene_rect_for_box(0, _CROP).topLeft()
    win.view.begin_crop_drag(pt)
    win.view.finish_crop_drag()  # no drag distance
    assert asked == [] and win.vdoc.ordered[0].crop_override is None


def test_escape_cancels_a_crop_drag(app, pdf):
    win = _win(app, pdf)
    win.view.arm(ArmedTool.CROP)
    win.view.begin_crop_drag(win.view.scene_rect_for_box(0, _CROP).center())
    assert win.view.cropping is True
    win.view.disarm()
    assert win.view.cropping is False and win.view.armed is None


def test_remove_crop_action(app, pdf):
    win = _win(app, pdf)
    win.undo_stack.push(CropPagesCommand(win.vdoc, [0], _CROP))
    win.thumbs.setCurrentRow(0)
    win._remove_crop()
    assert win.vdoc.ordered[0].crop_override is None
    assert win.undo_stack.undoText() == "Remove crop"


def test_cropped_page_thumbnail_uses_the_bake(app, pdf):
    win = _win(app, pdf)
    win.undo_stack.push(CropPagesCommand(win.vdoc, [0], _CROP))
    baked = win.thumbs._edited_render()
    assert baked is not None  # crop counts as an edit → thumbnails show the cropped page
    assert (baked[0].rect.width, baked[0].rect.height) == (300.0, 250.0)
    baked.close()


def test_copy_paste_carries_the_crop(app, pdf, tmp_path):
    win = _win(app, pdf)
    win.undo_stack.push(CropPagesCommand(win.vdoc, [0], _CROP))
    win.thumbs.setCurrentRow(0)
    win._copy_pages([0])
    other = _win(app, _build_pdf(tmp_path / "other.pdf", pages=1))
    other._paste_pages(1)
    assert other.vdoc.ordered[1].crop_override == _CROP
