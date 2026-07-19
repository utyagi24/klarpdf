"""Reduce file size (PLAN.md §GUI feature roadmap, M52).

File ▸ Export ▸ Reduced Size PDF… is the *lossy* tier only (a plain Save already runs the
lossless ``garbage=4, deflate, clean``): images are downsampled to a target dpi and re-encoded
JPEG at a quality, fonts are subset. Presets are named by intent but show their **true values**
("Screen — 150 dpi, JPEG 75") — no synthetic "% compression" slider — and Custom exposes exactly
the two real knobs. The report is the **actual** before → after sizes; the original file is
untouched by default, and overwriting it goes through a guard with permanent-quality-loss wording.
"""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QMessageBox

import main_window as mw
from app import PdfApp
from main_window import MainWindow
from model.export import export_reduced_pdf
from model.page_edits import Redaction
from model.virtual_document import VirtualDocument
from store.settings import Settings
from ui.reduce_dialog import PRESETS, ReduceSizeDialog, human_size, preset_label


@pytest.fixture
def image_pdf(tmp_path) -> str:
    """Two text pages; page 0 carries a large high-resolution noise image (incompressible
    losslessly, so the lossy downsample + JPEG re-encode is what shrinks it)."""
    import random

    path = str(tmp_path / "img.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "IMAGE-PAGE keep-this-text", fontsize=12)
    noise = random.Random(7).randbytes(1200 * 1200 * 3)  # deflate-incompressible RGB samples
    pix = fitz.Pixmap(fitz.csRGB, 1200, 1200, noise, False)
    page.insert_image(fitz.Rect(36, 100, 559, 623), pixmap=pix)
    doc.new_page().insert_text((72, 60), "SECOND-PAGE text", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def _text(path, page_index=0) -> str:
    with fitz.open(path) as doc:
        return doc[page_index].get_text("text")


# ---- the model function ------------------------------------------------------


def test_reduce_shrinks_an_image_heavy_file_and_reports_actual_sizes(image_pdf, tmp_path):
    v = VirtualDocument.from_path(image_pdf)
    out = str(tmp_path / "small.pdf")
    before, after = export_reduced_pdf(v, out, dpi=72, jpg_quality=30)
    assert after < before                       # the lossy tier actually shrank it
    assert after == os.path.getsize(out)        # actual written size, no estimate
    with fitz.open(out) as doc:
        assert doc.page_count == 2
        assert doc[0].get_images()              # the image is still there, recompressed
    assert "keep-this-text" in _text(out, 0)    # text layer intact (fonts subset, not dropped)
    assert "SECOND-PAGE" in _text(out, 1)


def test_reduce_is_edits_aware_and_leaves_the_original_untouched(image_pdf, tmp_path):
    original_size = os.path.getsize(image_pdf)
    v = VirtualDocument.from_path(image_pdf)
    v.delete_page(1)
    v.add_annotation(0, Redaction(((60, 50, 540, 70),)))  # covers page 0's text band
    out = str(tmp_path / "small.pdf")
    export_reduced_pdf(v, out, dpi=72, jpg_quality=30)
    with fitz.open(out) as doc:
        assert doc.page_count == 1              # the deleted page is gone from the copy
    assert "keep-this-text" not in _text(out, 0)  # pending redaction applied in the copy…
    assert v.has_redactions() is True             # …but still pending in the working document
    assert os.path.getsize(image_pdf) == original_size  # original untouched by default


# ---- the dialog: true-value presets + the two real knobs ---------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


def test_presets_show_their_true_values(qapp):
    assert [preset_label(*p) for p in PRESETS] == [
        "Screen — 150 dpi, JPEG 75",
        "Print — 300 dpi, JPEG 85",
    ]
    dialog = ReduceSizeDialog()
    assert [b.text() for b in dialog._preset_buttons] == [preset_label(*p) for p in PRESETS]
    assert dialog.chosen() == (150, 75)         # Screen is the default


def test_custom_mode_exposes_the_two_real_knobs(qapp):
    dialog = ReduceSizeDialog()
    assert not dialog._dpi.isEnabled() and not dialog._quality.isEnabled()  # preset → greyed
    dialog._custom.setChecked(True)
    assert dialog._dpi.isEnabled() and dialog._quality.isEnabled()
    dialog._dpi.setValue(96)
    dialog._quality.setValue(50)
    assert dialog.chosen() == (96, 50)
    dialog._preset_buttons[1].setChecked(True)  # back to Print
    assert dialog.chosen() == (300, 85)


def test_human_size_formats():
    assert human_size(900) == "1 KB"
    assert human_size(300 * 1024) == "300 KB"
    assert human_size(int(4.2 * 1024 * 1024)) == "4.2 MB"


# ---- the menu wiring ---------------------------------------------------------


def _accept_dialog_with(monkeypatch, dpi: int, quality: int) -> None:
    import ui.reduce_dialog as rd

    monkeypatch.setattr(rd.ReduceSizeDialog, "exec", lambda self: 1)
    monkeypatch.setattr(rd.ReduceSizeDialog, "chosen", lambda self: (dpi, quality))


def test_reduced_export_menu_writes_file_and_reports_before_after(
    app, image_pdf, tmp_path, monkeypatch
):
    win = MainWindow(app, image_pdf, app.settings)
    target = str(tmp_path / "small.pdf")
    _accept_dialog_with(monkeypatch, 72, 30)
    monkeypatch.setattr(
        mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, ""))
    )
    reports = []
    monkeypatch.setattr(
        mw.QMessageBox, "information",
        staticmethod(lambda parent, title, text, *a, **k: reports.append(text)),
    )
    win._export_reduced_pdf()
    assert os.path.isfile(target)
    assert len(reports) == 1 and reports[0].startswith("Reduced from ")  # actual before → after
    assert win.vdoc.path == image_pdf and win.undo_stack.isClean()       # side artifact


def test_reduced_export_cancelled_at_the_options_dialog_writes_nothing(
    app, image_pdf, tmp_path, monkeypatch
):
    win = MainWindow(app, image_pdf, app.settings)
    import ui.reduce_dialog as rd

    monkeypatch.setattr(rd.ReduceSizeDialog, "exec", lambda self: 0)  # user cancels
    win._export_reduced_pdf()  # must return before the save dialog (conftest would raise on it)
    assert {p.name for p in tmp_path.glob("*.pdf")} == {"img.pdf"}


def test_overwriting_the_original_needs_the_quality_loss_confirm(
    app, image_pdf, monkeypatch
):
    win = MainWindow(app, image_pdf, app.settings)
    original_size = os.path.getsize(image_pdf)
    _accept_dialog_with(monkeypatch, 72, 30)
    monkeypatch.setattr(
        mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (image_pdf, ""))
    )
    prompts = []

    def deny(parent, title, text, *a, **k):
        prompts.append(text)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(mw.QMessageBox, "warning", staticmethod(deny))
    win._export_reduced_pdf()
    assert len(prompts) == 1 and "permanently lost" in prompts[0]  # the honest wording
    assert os.path.getsize(image_pdf) == original_size             # cancel → untouched

    monkeypatch.setattr(
        mw.QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save),
    )
    monkeypatch.setattr(
        mw.QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    win._export_reduced_pdf()
    assert os.path.getsize(image_pdf) < original_size  # confirmed → original replaced, smaller
