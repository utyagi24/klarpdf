"""Dynamic theme icons (PLAN.md, M29). Offscreen GUI.

A runtime OS light<->dark switch arrives as ``ApplicationPaletteChange``; ``MainWindow.changeEvent``
clears the tinted-icon cache (``icons.refresh_for_theme``) and re-fetches every toolbar/menu action's
icon, so the monochrome glyphs re-tint to the new theme's text colour without a restart. The
full-colour app/window icon is theme-agnostic and intentionally left as-is.

(The live OS flip is the real-Windows manual check; here we deliver the same event offscreen, where
a manually-set palette is not overridden by the platform's system-theme following.)
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QAction, QColor, QPalette

from app import PdfApp
from store.settings import Settings
from ui import icons


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    original = qapp.palette()
    w = qapp.open_document(a_pdf)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()
    qapp.setPalette(original)        # leave the shared app palette as we found it
    icons.refresh_for_theme()


def _min_glyph_lum(icon):
    """Min luminance of an icon's opaque (glyph) pixels — low on a light theme, high on a dark one."""
    img = icon.pixmap(32, 32).toImage()
    vals = [
        (c.red() + c.green() + c.blue()) // 3
        for y in range(img.height())
        for x in range(img.width())
        if (c := img.pixelColor(x, y)).alpha() > 200
    ]
    return min(vals) if vals else None


def _flip_theme(qapp, win, button_text: str) -> None:
    pal = QPalette(qapp.palette())
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(button_text))
    qapp.setPalette(pal)
    qapp.sendEvent(win, QEvent(QEvent.Type.ApplicationPaletteChange))  # the OS-flip signal


def test_palette_change_retints_toolbar_icons(win, qapp):
    """The full runtime path: an ApplicationPaletteChange re-tints the toolbar glyphs in place."""
    undo = next(a for a in win.findChildren(QAction) if a.property("iconName") == "undo")
    _flip_theme(qapp, win, "#101010")   # light theme -> near-black glyphs
    dark = _min_glyph_lum(undo.icon())
    _flip_theme(qapp, win, "white")     # dark theme -> near-white glyphs
    light = _min_glyph_lum(undo.icon())
    assert dark is not None and light is not None
    assert light > dark + 100           # re-tinted dark -> light, no restart


def test_every_icon_action_has_a_name_so_it_re_tints(win):
    """Every toolbar/menu action that carries an icon must also carry the ``iconName`` property
    ``_retint_icons`` keys off — otherwise it would be left stale on a theme switch."""
    iconful = [a for a in win.findChildren(QAction) if not a.icon().isNull()]
    assert iconful  # sanity: the window has icon-bearing actions
    stale = [a.text() for a in iconful if not a.property("iconName")]
    assert not stale, f"actions with an icon but no iconName (won't re-tint): {stale}"


def test_app_icon_stays_full_colour_across_themes(win):
    """The app/window icon is full-colour and theme-agnostic (not cleared by refresh_for_theme), so
    it reads on both themes without a per-theme variant."""
    icons.refresh_for_theme()  # a theme switch clears action icons…
    img = icons.app_icon().pixmap(64, 64).toImage()  # …but the app icon keeps its colours
    colours = {
        (c.red(), c.green(), c.blue())
        for y in range(img.height())
        for x in range(img.width())
        if (c := img.pixelColor(x, y)).alpha() > 200
    }
    assert len(colours) > 3
