"""Night reading mode (PLAN.md §GUI feature roadmap, M49). Offscreen GUI.

A view-only pixel inversion, independent of the followed OS theme: toggling renders the pages
dark in the viewer while the file, print/export renders, and thumbnails keep their true colours.
Remembered app-wide.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from model.edit_engine import PyMuPDFEngine
from store.settings import Settings


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


@pytest.fixture
def pdf(tmp_path) -> str:
    path = str(tmp_path / "night.pdf")
    doc = fitz.open()
    doc.new_page(width=400, height=600).insert_text((72, 72), "NIGHT", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def _win(app, path):
    w = app.open_document(path)
    app.processEvents()
    return w


def _page_sample(win):
    """A pixel from an empty (paper) area of the rendered page-0 pixmap."""
    win.view.set_zoom(1.0)
    image = win.view._render_pixmap(0).toImage()
    return image.pixelColor(200, 300)


def test_toggle_inverts_the_page_render(app, pdf):
    win = _win(app, pdf)
    assert _page_sample(win).lightness() > 200        # paper: white
    win._a_night.trigger()
    assert win.view.night_mode is True
    assert _page_sample(win).lightness() < 50         # night: the paper renders near-black
    win._a_night.trigger()
    assert _page_sample(win).lightness() > 200        # back to daylight


def test_page_background_placeholder_matches(app, pdf):
    win = _win(app, pdf)
    win._a_night.trigger()
    assert win.view._pages[0]["bg"].brush().color().lightness() < 50
    win._a_night.trigger()
    assert win.view._pages[0]["bg"].brush().color().lightness() > 200


def test_file_and_print_output_stay_daylight(app, pdf):
    win = _win(app, pdf)
    win._a_night.trigger()
    # The save/print/export path renders from the materialised output — no inversion there.
    out = PyMuPDFEngine().render_output(win.vdoc)
    try:
        pm = out[0].get_pixmap()
        r, g, b = pm.pixel(200, 300)
        assert (r, g, b) == (255, 255, 255)
    finally:
        out.close()
    # Thumbnails render their true colours too.
    icon_img = win.thumbs._thumbnail(0).pixmap(64, 96).toImage()
    assert icon_img.pixelColor(32, 48).lightness() > 200


def test_night_mode_is_remembered_app_wide(app, pdf, tmp_path):
    win = _win(app, pdf)
    win._a_night.trigger()
    assert app.settings.get_pref("night_mode") is True
    # open_document dedupes by path — a distinct file gives a genuinely new window.
    other_path = str(tmp_path / "other.pdf")
    doc = fitz.open()
    doc.new_page(width=400, height=600)
    doc.save(other_path)
    doc.close()
    second = _win(app, other_path)
    assert second.view.night_mode is True            # a new window opens dark
    assert second._a_night.isChecked() is True
