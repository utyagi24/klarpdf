"""Where the marked thumbnail sits in the Pages sidebar. Offscreen GUI + a headless policy check.

The same defect the page view had, reported straight after it (owner, 2026-08-13): the current
thumbnail ended up jammed against the top or the bottom of the strip depending on which way the
reader was travelling, because ``scrollToItem``'s default ``EnsureVisible`` hint scrolls the
**minimum** distance.

``PositionAtCenter`` on its own would trade one annoyance for another — it scrolls *every* time, so
reading down a page would keep tugging the strip while the current thumbnail was plainly in sight.
So both surfaces share one policy (:mod:`util.reveal`): leave it alone when it is already well
inside, centre it otherwise. These tests assert that split, and the ``util`` half is asserted
directly because it is the piece the two callers must not diverge on.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from klarpdf.model.virtual_document import VirtualDocument
from organize.thumbnail_panel import ThumbnailPanel
from klarpdf.util.reveal import is_settled

_BAR_W = 210
_BAR_H = 700


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def deep_pdf(tmp_path):
    path = str(tmp_path / "deep.pdf")
    doc = fitz.open()
    for i in range(40):
        doc.new_page(width=612, height=792).insert_text((72, 100), f"page {i}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


def _panel(qapp, path, height):
    p = ThumbnailPanel(VirtualDocument.from_path(path))
    p.resize(_BAR_W, height)
    p.show()
    qapp.processEvents()
    return p


@pytest.fixture
def panel(qapp, deep_pdf):
    p = _panel(qapp, deep_pdf, _BAR_H)
    yield p
    p.close()


@pytest.fixture
def tall_panel(qapp, deep_pdf):
    """A sidebar deep enough to hold several thumbnails — a maximised window on a big screen.

    At the 700 px default only about 2.7 thumbnails fit, so consecutive pages simply cannot both
    be comfortably on screen and *any* correct policy scrolls between them. The leave-it-alone
    half of the rule only has room to show itself here.
    """
    p = _panel(qapp, deep_pdf, _BAR_H * 2)
    yield p
    p.close()


def _offset(panel, row: int) -> float:
    """How far row ``row`` sits from the middle of the strip, as a fraction of its height.

    0.0 is dead centre, ±0.5 is hard against an edge — a fraction so the assertion means the same
    thing at any sidebar height.
    """
    rect = panel.visualItemRect(panel.item(row))
    port = panel.viewport().rect()
    return (rect.center().y() - port.center().y()) / port.height()


# ---- the shared policy, headless -------------------------------------------------


def test_settled_only_when_clear_of_both_edges():
    assert is_settled(400.0, 440.0, 0.0, 1000.0) is True      # comfortably mid-window
    assert is_settled(10.0, 50.0, 0.0, 1000.0) is False       # hard against the top
    assert is_settled(950.0, 990.0, 0.0, 1000.0) is False     # hard against the bottom


def test_the_band_has_a_pixel_floor_for_short_windows():
    """15% of a 200 px sidebar is 30 px, which would call a thumbnail 31 px from the edge settled.
    The 60 px floor is what stops the policy evaporating as the window shrinks."""
    assert is_settled(35.0, 45.0, 0.0, 200.0) is False
    assert is_settled(100.0, 110.0, 0.0, 200.0) is True


def test_an_item_taller_than_the_band_is_never_settled():
    """No resting position gives it clearance both sides, so it is always centred — which is the
    right answer rather than an edge case to special-case."""
    assert is_settled(0.0, 900.0, 0.0, 1000.0) is False


# ---- the sidebar ----------------------------------------------------------------


def test_a_far_page_is_marked_near_the_middle_of_the_strip(panel):
    """The regression: this used to land hard against an edge, in whichever direction it moved."""
    panel.set_current(30)
    assert abs(_offset(panel, 30)) < 0.25


def test_going_back_lands_in_the_same_part_of_the_strip_as_going_forward(panel):
    """Direction of travel must not decide where the marker sits — that asymmetry was the bug."""
    panel.set_current(30)
    forward = _offset(panel, 30)
    panel.set_current(5)
    backward = _offset(panel, 5)
    assert abs(forward - backward) < 0.2, f"forward {forward:.3f} vs backward {backward:.3f}"


def test_a_neighbouring_page_does_not_tug_the_strip(tall_panel):
    """Reading from page N to N+1 must not move the sidebar while N+1 is already comfortably in
    sight — the reason this is not simply `PositionAtCenter`, which would scroll on every page."""
    tall_panel.set_current(20)
    settled_at = tall_panel.verticalScrollBar().value()
    tall_panel.set_current(21)
    assert tall_panel.verticalScrollBar().value() == settled_at


def test_the_first_and_last_pages_stay_reachable(panel):
    """Centring is clamped by the scroll range, so the ends land as close to centred as the strip
    allows — and must still be on screen, not pushed past an edge."""
    for row in (0, panel.count() - 1):
        panel.set_current(row)
        rect = panel.visualItemRect(panel.item(row))
        port = panel.viewport().rect()
        assert port.top() <= rect.center().y() <= port.bottom()


# ---- #288: a rebuild must not scroll the strip out from under the reader ------
#
# `populate()` runs on every structural edit and did not go through `_reveal_row`: `clear()` drops
# the strip to the top and the `setCurrentRow` that restores the marker scrolls the *minimum*
# distance back, landing it hard against an edge. Same defect as above, different path in.


def test_a_rebuild_keeps_the_reader_where_they_were(tall_panel):
    """The general case, which is not specific to inserting: nothing structural should move the
    strip when the marked row is still comfortably in view."""
    tall_panel.set_current(20)
    before = tall_panel.verticalScrollBar().value()

    tall_panel.populate()

    assert tall_panel.verticalScrollBar().value() == before


def test_a_rebuild_does_not_jam_the_marked_row_against_an_edge(tall_panel):
    """`clear()` resets the offset to 0, so without the restore the marker comes back at whichever
    edge the minimum scroll reached — which is exactly what hid the newly inserted page."""
    tall_panel.set_current(30)

    tall_panel.populate()

    assert abs(_offset(tall_panel, 30)) < 0.25


def test_the_marker_survives_the_rebuild(tall_panel):
    """The property `populate` already had, re-asserted so the scroll fix cannot cost it."""
    tall_panel.set_current(20)
    tall_panel.populate()
    assert tall_panel.currentRow() == 20
