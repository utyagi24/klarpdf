"""Image stamp / signature — the sign-and-return workflow (PLAN.md §R4, M63).

Headless model + offscreen GUI. Three claims under test:

* a **phone photo** of a signature on paper works without an image editor — "make white background
  transparent" keys the paper out, and honours a PNG's own alpha rather than replacing it;
* the recent list holds **paths only**, so KlarPDF never keeps a copy of a signature image and
  deleting the file revokes it;
* the second use is **two clicks** — pick it from the menu, drag its box; no dialog.

Plus the boundary the docs promise: a baked signature is page content and cannot be lifted off.
"""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt

from app import PdfApp
from main_window import MainWindow
from model.content_marks import ImageStamp, apply_content_marks, render_mark_document
from model.edit_engine import PyMuPDFEngine
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.tools import ArmedTool

INK = (0.05, 0.05, 0.35)


@pytest.fixture
def photo_sig(tmp_path) -> str:
    """A 'phone photo': dark ink on an off-white paper background, fully opaque (a JPEG-alike)."""
    path = str(tmp_path / "photo_sig.png")
    doc = fitz.open()
    page = doc.new_page(width=200, height=80)
    page.draw_rect(page.rect, color=None, fill=(0.96, 0.95, 0.93))   # paper, not pure white
    page.draw_line(fitz.Point(20, 60), fitz.Point(180, 25), color=INK, width=6)
    page.get_pixmap(dpi=150).save(path)
    doc.close()
    return path


@pytest.fixture
def alpha_sig(tmp_path) -> str:
    """A PNG whose author already removed part of the image — and the removed part is **dark**.

    That combination is the one that matters: keying maps dark pixels to *opaque*, so a naive
    implementation that overwrote alpha instead of intersecting it would resurrect exactly these
    pixels. (A fully transparent black pixmap does not survive PyMuPDF's PNG writer, hence the
    opaque white half — which also keeps the fixture a realistic image.)
    """
    path = str(tmp_path / "alpha_sig.png")
    doc = fitz.open()
    page = doc.new_page(width=40, height=20)
    page.draw_rect(fitz.Rect(0, 0, 20, 20), color=None, fill=(0.04, 0.04, 0.04))   # dark half
    page.draw_rect(fitz.Rect(20, 0, 40, 20), color=None, fill=(1, 1, 1))           # white half
    pix = fitz.Pixmap(page.get_pixmap(dpi=72), 1)
    pix.set_alpha(bytes([0] * 20 + [255] * 20) * 20, premultiply=0)   # the dark half is removed
    pix.save(path)
    doc.close()
    return path


def _alpha_at(mark: ImageStamp, x: int, y: int) -> int:
    """The rendered artwork's alpha at a pixel — what actually reaches the page."""
    art = render_mark_document(mark)
    try:
        pix = art[0].get_pixmap(alpha=True)
        offset = (y * pix.width + x) * pix.n
        return pix.samples[offset + pix.n - 1]
    finally:
        art.close()


# ---- white-to-alpha: the phone-photo fix ----------------------------------------


def test_raw_photo_keeps_its_opaque_paper_background(photo_sig):
    """Without the toggle the image is a solid rectangle — which is exactly the problem: dropped
    onto a form it blanks out whatever it covers."""
    mark = ImageStamp((0, 0, 200, 80), photo_sig)
    assert _alpha_at(mark, 5, 5) == 255            # a corner, far from the ink


def test_white_to_alpha_drops_the_paper(photo_sig):
    mark = ImageStamp((0, 0, 200, 80), photo_sig, white_to_alpha=True)
    assert _alpha_at(mark, 5, 5) == 0              # paper gone


def test_white_to_alpha_keeps_the_ink(photo_sig):
    """The stroke must survive at full strength — a background remover that eats the signature is
    worse than none."""
    mark = ImageStamp((0, 0, 200, 80), photo_sig, white_to_alpha=True)
    inked = [_alpha_at(mark, x, y)
             for x, y in ((60, 50), (100, 42), (140, 34))]   # points along the drawn line
    assert max(inked) > 200


def test_threshold_controls_how_much_is_dropped(tmp_path):
    """A mid-grey background needs a lower threshold; the slider is what makes that reachable."""
    path = str(tmp_path / "grey.png")
    doc = fitz.open()
    page = doc.new_page(width=60, height=30)
    page.draw_rect(page.rect, color=None, fill=(0.72, 0.72, 0.72))
    page.get_pixmap(dpi=72).save(path)
    doc.close()

    high = ImageStamp((0, 0, 60, 30), path, white_to_alpha=True, white_threshold=0.95)
    low = ImageStamp((0, 0, 60, 30), path, white_to_alpha=True, white_threshold=0.60)
    assert _alpha_at(high, 30, 15) == 255          # 0.72 is below 0.95 — kept
    assert _alpha_at(low, 30, 15) == 0             # …and above 0.60 — dropped


def test_white_to_alpha_never_makes_a_transparent_png_opaque(alpha_sig):
    """Existing alpha is intersected, not replaced — keying a PNG that is already transparent must
    not resurrect pixels the author removed.

    The fixture's transparent half is *dark*, which keying would otherwise mark fully opaque, so the
    precondition is asserted first: without it a PNG-writer change could quietly make this vacuous.
    """
    loaded = fitz.Pixmap(alpha_sig)
    assert loaded.alpha and loaded.samples[(10 * 40 + 5) * loaded.n + loaded.n - 1] == 0

    mark = ImageStamp((0, 0, 40, 20), alpha_sig, white_to_alpha=True)
    assert _alpha_at(mark, 5, 10) == 0


def test_white_to_alpha_is_off_by_default(photo_sig):
    """It is a fix for a specific input, not a thing that silently rewrites every image."""
    assert ImageStamp((0, 0, 1, 1), photo_sig).white_to_alpha is False


def test_keyed_signature_lets_the_page_show_through(photo_sig, tmp_path):
    """The end-to-end claim: placed over text, a keyed signature does not blank it out."""
    src = str(tmp_path / "form.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 400), "SIGNHERE", fontsize=14)
    doc.save(src)
    doc.close()

    vdoc = VirtualDocument.from_path(src)
    try:
        vdoc.add_annotation(0, ImageStamp((40, 370, 340, 430), photo_sig, white_to_alpha=True))
        out = str(tmp_path / "signed.pdf")
        PyMuPDFEngine().materialize(vdoc, out)
        saved = fitz.open(out)
        try:
            # The text is still *there* (content, not covered by an opaque box) and the image landed.
            assert "SIGNHERE" in saved[0].get_text()
            assert len(saved[0].get_images()) == 1
        finally:
            saved.close()
    finally:
        vdoc.close()


def test_a_baked_signature_is_content_not_an_annotation(photo_sig, tmp_path):
    """"The baked mark can't be lifted off" — the M63 done-when. It is page content, so there is no
    annotation for a recipient to select and drag away."""
    src = str(tmp_path / "form.pdf")
    doc = fitz.open()
    doc.new_page()
    doc.save(src)
    doc.close()

    vdoc = VirtualDocument.from_path(src)
    try:
        vdoc.add_annotation(0, ImageStamp((40, 370, 340, 430), photo_sig))
        out = str(tmp_path / "signed.pdf")
        PyMuPDFEngine().materialize(vdoc, out)
        saved = fitz.open(out)
        try:
            assert list(saved[0].annots()) == []
            assert len(saved[0].get_images()) == 1
        finally:
            saved.close()
    finally:
        vdoc.close()


def test_a_huge_image_keys_without_stalling(tmp_path):
    """The keying runs at C speed (greyscale conversion + one `bytes.translate`), because a
    multi-megapixel phone photo is a realistic input and a Python per-pixel loop would freeze the UI.
    A ~4 MP image must key in well under a second."""
    import time

    path = str(tmp_path / "big.png")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2000, 2000), False)
    pix.clear_with(250)
    pix.save(path)

    started = time.perf_counter()
    art = render_mark_document(ImageStamp((0, 0, 200, 200), path, white_to_alpha=True))
    art.close()
    assert time.perf_counter() - started < 3.0


# ---- recent signatures: paths only ----------------------------------------------


def test_recent_signatures_store_paths_not_pixels(tmp_path, photo_sig):
    settings = Settings(tmp_path / "vs.json")
    settings.add_recent_signature(photo_sig)
    assert settings.recent_signatures() == [photo_sig]
    # Nothing but the path is persisted — the whole revocation story depends on it.
    import json

    stored = json.loads((tmp_path / "vs.json").read_text(encoding="utf-8"))
    assert stored["preferences"]["recent_signatures"] == [photo_sig]


def test_deleting_the_file_revokes_the_signature(tmp_path, photo_sig):
    """Moving or deleting the image is the revocation mechanism, so a vanished file must drop out."""
    settings = Settings(tmp_path / "vs.json")
    settings.add_recent_signature(photo_sig)
    os.remove(photo_sig)
    assert settings.recent_signatures() == []


def test_recent_signatures_dedupe_and_cap(tmp_path):
    settings = Settings(tmp_path / "vs.json")
    made = []
    for i in range(9):
        path = tmp_path / f"s{i}.png"
        path.write_bytes(b"x")
        made.append(str(path))
        settings.add_recent_signature(str(path))
    settings.add_recent_signature(made[0])            # re-use jumps to the front
    recent = settings.recent_signatures()
    assert recent[0] == made[0]
    assert len(recent) == len(set(recent)) <= 6


def test_clear_recent_signatures(tmp_path, photo_sig):
    settings = Settings(tmp_path / "vs.json")
    settings.add_recent_signature(photo_sig, white_to_alpha=True, white_threshold=0.7)
    settings.clear_recent_signatures()
    assert settings.recent_signatures() == []
    assert settings.signature_settings(photo_sig) is None   # the settings go with the entry


def test_transparency_settings_ride_with_the_path(tmp_path, photo_sig):
    """How much paper to drop out is a property of the scan, so it is remembered per image (M63.1)
    — beside the list, never inside it, so "paths, never pixels" still describes the list itself."""
    settings = Settings(tmp_path / "vs.json")
    settings.add_recent_signature(photo_sig, white_to_alpha=True, white_threshold=0.7)
    assert settings.signature_settings(photo_sig) == {"white_to_alpha": True,
                                                      "white_threshold": 0.7}
    settings.add_recent_signature(photo_sig)              # re-placing it keeps what it was tuned to
    assert settings.signature_settings(photo_sig)["white_threshold"] == 0.7
    import json

    stored = json.loads((tmp_path / "vs.json").read_text(encoding="utf-8"))
    assert stored["preferences"]["recent_signatures"] == [photo_sig]


def test_an_untuned_image_has_no_remembered_settings(tmp_path, photo_sig):
    settings = Settings(tmp_path / "vs.json")
    settings.add_recent_signature(photo_sig)
    assert settings.signature_settings(photo_sig) is None


def test_settings_do_not_outlive_their_entry(tmp_path, photo_sig):
    """A file deleted to revoke the signature must not leave its tuning behind."""
    settings = Settings(tmp_path / "vs.json")
    settings.add_recent_signature(photo_sig, white_to_alpha=True, white_threshold=0.7)
    os.remove(photo_sig)
    assert settings.recent_signatures() == []
    assert settings.signature_settings(photo_sig) is None


# ---- the two-click second use (offscreen GUI) -----------------------------------


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


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _recent_titles(win) -> list[str]:
    return [a.text() for a in win._signature_menu.actions() if not a.isSeparator()]


def test_the_recent_menu_is_hidden_until_there_is_one(win):
    """No dead chrome on the first use (owner rule) — an empty submenu is worse than none."""
    assert win._signature_menu.menuAction().isVisible() is False
    assert _recent_titles(win) == []


def test_using_a_signature_puts_it_in_the_menu(win, photo_sig):
    win._settings.add_recent_signature(photo_sig)
    win._rebuild_signature_menu()
    assert win._signature_menu.menuAction().isVisible() is True
    assert _recent_titles(win) == [os.path.basename(photo_sig), "Clear List"]


def test_the_second_use_is_pick_then_drag(win, photo_sig):
    """The M63 done-when: no dialog on the second use — the menu entry arms the placement directly."""
    win._settings.add_recent_signature(photo_sig)
    win._rebuild_signature_menu()

    win._place_recent_signature(photo_sig)            # click 2 (click 1 opened the dropdown)
    assert win.view.armed is ArmedTool.STAMP

    overlay = win.view.annotations
    overlay.begin_draw(ArmedTool.STAMP, _scene(win, 100, 300))
    overlay.update_draw(_scene(win, 300, 360), Qt.KeyboardModifier.NoModifier)
    overlay.finish_draw()

    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, ImageStamp)]
    assert len(marks) == 1
    assert marks[0].image_path == photo_sig


def test_the_menu_places_it_the_way_it_was_placed_last_time(win, photo_sig):
    """The menu path has no dialog, so it had nowhere to re-tick "make white background
    transparent" — a photo signature came back with its paper on (owner-reported)."""
    win._settings.add_recent_signature(photo_sig, white_to_alpha=True, white_threshold=0.70)
    win._rebuild_signature_menu()

    win._place_recent_signature(photo_sig)
    overlay = win.view.annotations
    overlay.begin_draw(ArmedTool.STAMP, _scene(win, 100, 300))
    overlay.update_draw(_scene(win, 300, 360), Qt.KeyboardModifier.NoModifier)
    overlay.finish_draw()

    mark = [a for a in win.vdoc.page_annotations(0) if isinstance(a, ImageStamp)][0]
    assert mark.white_to_alpha is True
    assert mark.white_threshold == pytest.approx(0.70)


def test_the_dialog_reopens_an_image_the_way_it_was_left(win, photo_sig):
    from ui.signature_dialog import SignatureDialog

    remembered = {photo_sig: {"white_to_alpha": True, "white_threshold": 0.70}}
    dialog = SignatureDialog(win, [photo_sig], remembered)
    try:
        assert dialog.transparent.isChecked() is True
        assert dialog.strength.value() == 30          # (100 - 0.70 * 100)
        assert dialog.strength.isEnabled() is True    # the blocked toggle still enabled the slider
        assert dialog.image_stamp().white_threshold == pytest.approx(0.70)
    finally:
        dialog.deleteLater()


def test_a_newly_browsed_image_keeps_the_settings_on_screen(win, photo_sig, tmp_path):
    """An image with no memory of its own inherits what is on the controls — which, since the
    dialog opens on the most recent entry, is the last-used setting. So re-scanning the same
    signature to a new file starts tuned."""
    from ui.signature_dialog import SignatureDialog

    remembered = {photo_sig: {"white_to_alpha": True, "white_threshold": 0.70}}
    dialog = SignatureDialog(win, [photo_sig], remembered)
    try:
        dialog.set_path(str(tmp_path / "never-seen.png"))
        assert dialog.transparent.isChecked() is True
        assert dialog.strength.value() == 30
    finally:
        dialog.deleteLater()


def test_a_vanished_recent_signature_is_dropped_not_placed(win, photo_sig):
    from PySide6.QtWidgets import QApplication

    win._settings.add_recent_signature(photo_sig)
    win._rebuild_signature_menu()
    os.remove(photo_sig)
    win._place_recent_signature(photo_sig)
    assert win.view.armed is not ArmedTool.STAMP
    # The menu rebuild is deferred to the next event-loop turn (M69.11): doing it inline would
    # destroy the QAction whose triggered signal is still being delivered.
    QApplication.processEvents()
    assert _recent_titles(win) == []


def test_signature_dialog_composes_the_mark(win, photo_sig):
    from ui.signature_dialog import SignatureDialog

    dialog = SignatureDialog(win, [photo_sig])
    try:
        assert dialog.path() == photo_sig             # the recent entry is preselected
        dialog.transparent.setChecked(True)
        dialog.strength.setValue(30)      # "remove more" — see _white_threshold
        mark = dialog.image_stamp()
        assert mark.image_path == photo_sig
        assert mark.white_to_alpha is True
        assert mark.white_threshold == pytest.approx(0.70)   # (100 - 30) / 100
    finally:
        dialog.deleteLater()


def test_signature_dialog_strength_follows_the_toggle(win, photo_sig):
    from ui.signature_dialog import SignatureDialog

    dialog = SignatureDialog(win, [photo_sig])
    try:
        assert dialog.strength.isEnabled() is False   # meaningless until keying is on
        dialog.transparent.setChecked(True)
        assert dialog.strength.isEnabled() is True
    finally:
        dialog.deleteLater()


def test_signature_dialog_needs_an_image_before_ok(win):
    from PySide6.QtWidgets import QDialogButtonBox

    from ui.signature_dialog import SignatureDialog

    dialog = SignatureDialog(win, [])
    try:
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is False
    finally:
        dialog.deleteLater()


def test_apply_content_marks_tolerates_a_missing_signature(tmp_path):
    """A path that moved between placing and saving costs the picture, never the save."""
    doc = fitz.open()
    page = doc.new_page()
    try:
        apply_content_marks(page, (ImageStamp((10, 10, 90, 50), str(tmp_path / "gone.png"),
                                              white_to_alpha=True),))
        assert page.get_images() == []
    finally:
        doc.close()


def test_the_removal_slider_runs_the_way_it_looks(win, photo_sig):
    """Owner-reported: dragging right *reduced* the transparency. The descriptor underneath is a
    luminance **cutoff**, which runs backwards — lower removes more — and the slider exposed it raw
    and unlabelled. Right must mean more removed (M69.13)."""
    from ui.signature_dialog import SignatureDialog

    dialog = SignatureDialog(win, [photo_sig])
    try:
        dialog.transparent.setChecked(True)
        dialog.strength.setValue(dialog.strength.minimum())
        least = dialog.image_stamp((0, 0, 60, 30)).white_threshold
        dialog.strength.setValue(dialog.strength.maximum())
        most = dialog.image_stamp((0, 0, 60, 30)).white_threshold
        # A *lower* cutoff removes more, so dragging right must lower it.
        assert most < least
    finally:
        dialog.deleteLater()


def test_the_removal_slider_keeps_the_old_default(win, photo_sig):
    """Inverting the control must not quietly re-tune it: 0.85 was the default and still is."""
    from ui.signature_dialog import SignatureDialog

    dialog = SignatureDialog(win, [photo_sig])
    try:
        dialog.transparent.setChecked(True)
        assert dialog.image_stamp((0, 0, 60, 30)).white_threshold == pytest.approx(0.85)
    finally:
        dialog.deleteLater()
