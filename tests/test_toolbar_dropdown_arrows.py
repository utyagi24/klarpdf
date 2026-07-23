"""Toolbar dropdown-arrow placement (PLAN.md §GUI feature roadmap, M59.13 — R3). Offscreen GUI.

The three menu-carrying toolbar buttons — Markup ▾ and Draw ▾ (``MenuButtonPopup``) and the pen-&-
shapes style swatch (``InstantPopup``) — used to disagree about where the dropdown arrow goes,
because Qt places it per popup mode: MenuButtonPopup centres it in a raised sub-panel, InstantPopup
tucks it into the **bottom-right corner**. Measured, the swatch's arrow sat at **0.745** of the
button height against ~0.45 for the other two, and every arrow was jammed against the icon.

These tests pin the fix by measuring where the arrow is actually **painted**. Qt's own
``subControlRect`` is no use here: it reports the menu *hit area* (identical before and after), not
where the glyph lands — so the check has to look at pixels.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QToolButton

from app import PdfApp
from main_window import MainWindow
from store.settings import Settings

# The arrow lives in the right-hand strip the stylesheet reserves for it.
_STRIP = 14


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    qapp.object_clipboard = []
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    w.resize(1200, 700)
    w.show()
    w.markup_bar.show()  # the dropdown buttons live on the markup bar, hidden at rest (M71)
    app.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _arrow_height_fraction(button) -> float:
    """Where the arrow's ink sits vertically, as a fraction of the button's height (0.5 = dead
    centre). Saturated pixels are skipped, so the style button's colour swatch is never mistaken
    for arrow ink — the arrow is grey, the swatch is not."""
    img = button.grab().toImage()
    w, h = img.width(), img.height()
    bg = img.pixelColor(1, 1)
    total = weighted = 0.0
    for x in range(max(0, w - _STRIP), w):
        for y in range(h):
            c = img.pixelColor(x, y)
            if max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) > 24:
                continue                                   # a colour swatch, not the grey arrow
            ink = abs(c.red() - bg.red()) + abs(c.green() - bg.green()) + abs(c.blue() - bg.blue())
            if ink > 24:
                total += ink
                weighted += ink * (y + 0.5)
    assert total, "no arrow ink found in the button's right-hand strip"
    return weighted / total / h


def _menu_buttons(win):
    return [win._markup_button, win._draw_button, win._markup_style_button]


def test_every_dropdown_arrow_sits_at_the_same_height(win):
    """The uniformity ask: the arrow must not move between buttons. Before the fix the spread was
    0.307 of the button height — the swatch's arrow was in the bottom corner while the split
    buttons' were mid-height."""
    fractions = [_arrow_height_fraction(b) for b in _menu_buttons(win)]
    assert max(fractions) - min(fractions) < 0.02


def test_every_dropdown_arrow_is_vertically_centred(win):
    """…and the shared height is the middle of the button, not a corner. This is the assertion the
    old InstantPopup corner arrow (0.745) fails."""
    for button in _menu_buttons(win):
        assert _arrow_height_fraction(button) == pytest.approx(0.5, abs=0.05)


def test_dropdown_buttons_reserve_room_so_the_arrow_never_touches_the_icon(win):
    """The 'too close and tight' half: a menu button is wider than a plain one by the strip its
    arrow gets, instead of the arrow crowding (or overlapping) the icon."""
    bar = win.markup_bar  # compare within the bar the menu buttons sit on (M71)
    plain = [b for b in bar.findChildren(QToolButton) if b.menu() is None and b.isVisible()]
    assert plain, "expected some menu-less toolbar buttons to compare against"
    widest_plain = max(b.width() for b in plain)
    for button in _menu_buttons(win):
        assert button.width() > widest_plain


def test_the_two_popup_modes_are_both_covered(win):
    """A guard on the premise: the fix has to span both Qt popup modes, since that difference is
    what caused the mismatch. If these ever converge, the stylesheet can be simplified."""
    modes = {b.popupMode() for b in _menu_buttons(win)}
    assert len(modes) == 2
