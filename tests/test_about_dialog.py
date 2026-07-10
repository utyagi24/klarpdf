"""Help ▸ About / Open-Source Licenses, and the bundled-resource resolver (PLAN.md, G4).

Headless (offscreen, set in conftest). These tests carry an unusual burden: the dialogs read licence
texts that only *move* in the frozen build, which this suite never produces. So rather than only
exercising the source path, we also assert the contract the frozen build depends on — that
``packaging/klarpdf.spec`` lists both licence files in ``datas``, landing them where
``util.resources.resource_root()`` will look. If someone drops a ``datas`` entry, this fails here
instead of silently shipping an installer whose licence dialog is empty.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import PdfApp
from store.settings import Settings
from util.resources import LICENSE_FILES, read_text_resource, resource_path, resource_root
from version import __version__

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "klarpdf.spec"


@pytest.fixture(scope="session")
def qapp():
    # Must be a PdfApp, not a bare QApplication: the QApplication instance is process-wide and the
    # first fixture to build one wins. This module sorts before test_export.py, which needs PdfApp.
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    qapp.page_clipboard = []
    return qapp


# --- the resolver -------------------------------------------------------------------------------


def test_resource_root_is_repo_root_from_source():
    """Running from a checkout, resources resolve next to LICENSE — not inside util/."""
    assert resource_root() == ROOT
    assert resource_path("LICENSE") == ROOT / "LICENSE"


@pytest.mark.parametrize("name", LICENSE_FILES)
def test_bundled_license_files_exist_and_are_readable(name):
    assert resource_path(name).is_file(), f"{name} missing from the repo root"
    text = read_text_resource(name)
    assert len(text) > 500, f"{name} looks truncated ({len(text)} chars)"


def test_license_is_the_agpl_not_a_placeholder():
    """Guard against a LICENSE that got clobbered by a template or a stub."""
    text = read_text_resource("LICENSE")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 19 November 2007" in text
    # Our own notice must sit above the FSF text, not be mistaken for part of it.
    assert text.startswith("KlarPDF"), "project notice should head the file"
    assert "Copyright (C) 2026 KlarPDF contributors" in text


def test_missing_resource_degrades_instead_of_raising():
    """A packaging fault must not crash the dialog — it must say so, visibly."""
    text = read_text_resource("NO_SUCH_FILE_xyz")
    assert "could not be read" in text
    assert "packaging fault" in text


def test_resource_root_follows_meipass_when_frozen(tmp_path, monkeypatch):
    """The frozen branch is the one the suite otherwise never executes.

    PyInstaller sets ``sys._MEIPASS`` to its unpack dir; the resolver must read from there rather
    than from the source tree beside ``util/``. Simulating it is the only way to exercise this
    without building an installer — and a resolver that ignores ``_MEIPASS`` would still pass every
    other test in this file.
    """
    import sys

    (tmp_path / "LICENSE").write_text("frozen-copy-of-the-licence", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resource_root() == tmp_path
    assert resource_path("LICENSE") == tmp_path / "LICENSE"
    assert read_text_resource("LICENSE") == "frozen-copy-of-the-licence"


# --- the contract the frozen build relies on ----------------------------------------------------


@pytest.mark.parametrize("name", LICENSE_FILES)
def test_spec_bundles_license_file_at_bundle_root(name):
    """`datas` must ship each licence text to the bundle root, where resource_root() looks.

    The headless suite cannot run PyInstaller, so this reads the spec as text. It is a weaker check
    than freezing, and deliberately so: it costs nothing and catches the realistic regression (an
    entry deleted or its destination changed) rather than nothing at all.
    """
    spec = SPEC.read_text(encoding="utf-8")
    pattern = rf'ROOT\s*/\s*"{re.escape(name)}"\s*\)\s*,\s*"\."'
    assert re.search(pattern, spec), f"{name} not bundled to bundle root in {SPEC.name}"


# --- the dialogs --------------------------------------------------------------------------------


def test_about_dialog_shows_version_licence_and_no_warranty(qapp):
    from PySide6.QtWidgets import QLabel

    from ui.about import APP_NAME, AboutDialog

    dlg = AboutDialog()
    text = " ".join(label.text() for label in dlg.findChildren(QLabel))
    assert APP_NAME in text
    assert __version__ in text
    # AGPL §15-16: the warranty disclaimer has to be conveyed, not merely shipped in a file.
    assert "NO WARRANTY" in text.upper()
    assert "Affero" in text
    dlg.deleteLater()


def test_about_source_link_points_at_this_exact_build(qapp):
    """AGPL 'corresponding source' means the tag this binary came from, never a moving branch."""
    from ui.about import REPO_URL, SOURCE_URL

    assert SOURCE_URL == f"{REPO_URL}/tree/v{__version__}"
    assert "/tree/main" not in SOURCE_URL


def test_licenses_dialog_has_a_tab_per_bundled_text_with_real_content(qapp):
    from ui.about import LicensesDialog

    dlg = LicensesDialog()
    assert dlg.tabs.count() == len(LICENSE_FILES)
    for i, name in enumerate(LICENSE_FILES):
        assert dlg.tabs.tabText(i) == name
        body = dlg.tabs.widget(i).toPlainText()
        assert len(body) > 500
        assert "could not be read" not in body, f"{name} failed to resolve"
    dlg.deleteLater()


def test_main_window_help_menu_is_wired_to_the_dialogs(app, a_pdf, monkeypatch):
    """The dialogs are worthless if nothing opens them — so drive the real menu, not the source.

    Triggering the actions on a real ``MainWindow`` catches a misnamed slot, an action added to the
    wrong menu, or a dialog that raises on construction. ``exec()`` is stubbed because a modal loop
    would hang the suite; the dialog is still fully constructed first.
    """
    from PySide6.QtWidgets import QMenu

    from main_window import MainWindow

    win = MainWindow(app, a_pdf, app.settings)
    help_menu = next((m for m in win.menuBar().findChildren(QMenu) if m.title() == "&Help"), None)
    assert help_menu is not None, "no Help menu on the menu bar"
    labels = [a.text() for a in help_menu.actions() if not a.isSeparator()]
    assert labels == ["About KlarPDF", "Open-Source Licenses", "View Source"]

    import ui.about as about

    monkeypatch.setattr(about.AboutDialog, "exec", lambda self: None)
    monkeypatch.setattr(about.LicensesDialog, "exec", lambda self: None)
    opened: list[str] = []
    monkeypatch.setattr(about.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))

    for action in help_menu.actions():
        if not action.isSeparator():
            action.trigger()  # must not raise

    # View Source hands exactly one URL to the browser — the tagged source, never a live socket.
    assert opened == [about.SOURCE_URL]
    win.close()
