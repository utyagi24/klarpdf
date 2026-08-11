"""M92.6 — the Pages sidebar rolls continuously under the wheel (`PLAN.md` §M92). Offscreen GUI.

The defect these pin, owner-reported 2026-08-01: *"scrolling on the thumbnails sidebar jumps three
thumbnails at a time"*. Two wrong factors multiplied. Qt sets an ``IconMode`` list's ``singleStep``
to **one whole thumbnail** (measured: 253 px at the default bar width), and Windows' *lines to
scroll at a time* defaults to **3** — so a detent asked for three pages, and Qt's clamp to
``pageStep`` then delivered **one whole viewport, 698 px, 2.76 thumbnails, per click**.

The replacement is ``angleDelta / notch × pitch / 3``, and the four properties below assert it one
at a time: the **distance** is a third of a thumbnail; it is **independent of the Windows setting**
(owner request — ``wheelScrollLines`` is a *lines of text* preference and the sidebar has no text);
it is **continuous**, landing on fractions of a thumbnail rather than only on boundaries (the Edge
behaviour the owner asked for — *"only half or a fraction of it is visible on the top"*); and it is
**invariant across sidebar width**, since the thumbnails scale with the bar.

The last group is the boundary: a tilt wheel and a document too short to scroll must still reach
``super().wheelEvent()`` rather than being swallowed here.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app import PdfApp
from model.virtual_document import VirtualDocument
from organize.thumbnail_panel import _NOTCH_PER_THUMB, _WHEEL_NOTCH, ThumbnailPanel

_BAR_W = 210      # the default sidebar width (_SIDEBAR_W)
_BAR_H = 700


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def deep_pdf(tmp_path):
    """A document deep enough that the sidebar scrolls for many detents."""
    path = str(tmp_path / "deep.pdf")
    doc = fitz.open()
    for i in range(30):
        doc.new_page(width=612, height=792).insert_text((72, 100), f"page {i}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def panel(qapp, deep_pdf):
    p = ThumbnailPanel(VirtualDocument.from_path(deep_pdf))
    p.resize(_BAR_W, _BAR_H)
    p.show()
    qapp.processEvents()
    return p


@pytest.fixture
def wheel_lines():
    """Set Windows' *lines to scroll at a time* for a test, and put it back afterwards."""
    original = QApplication.wheelScrollLines()
    yield QApplication.setWheelScrollLines
    QApplication.setWheelScrollLines(original)


def _wheel(panel, angle_y=-_WHEEL_NOTCH, angle_x=0) -> int:
    """Send one wheel event carrying a raw ``angleDelta`` (eighths of a degree, so a mouse detent
    is ±120 and a hi-res wheel sends fractions of it). Returns the resulting scroll position."""
    pos = QPointF(50, 100)
    event = QWheelEvent(
        pos, panel.viewport().mapToGlobal(pos.toPoint()),
        QPoint(0, 0), QPoint(angle_x, angle_y),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(panel.viewport(), event)
    return panel.verticalScrollBar().value()


# ---- the distance rule ------------------------------------------------------


def test_one_detent_moves_a_third_of_a_thumbnail(panel):
    pitch = panel._row_pitch()
    assert pitch > 0
    assert _wheel(panel) == pytest.approx(pitch / _NOTCH_PER_THUMB, abs=1)


def test_a_detent_is_not_the_old_viewport_jump(panel):
    """The regression guard. Before M92.6 a detent moved ``pageStep`` — the whole viewport."""
    moved = _wheel(panel)
    assert moved < panel.viewport().height() / 2
    assert moved < panel._row_pitch()          # and less than a single thumbnail


def test_wheel_up_scrolls_back(panel):
    _wheel(panel)
    _wheel(panel)
    down = panel.verticalScrollBar().value()
    assert _wheel(panel, angle_y=_WHEEL_NOTCH) == pytest.approx(down / 2, abs=1)


# ---- independent of the Windows setting -------------------------------------


@pytest.mark.parametrize("lines", [1, 3, 10])
def test_detent_ignores_windows_lines_to_scroll(panel, wheel_lines, lines):
    """Owner request: the sidebar must not inherit *lines to scroll at a time*. Setting it to 1 is
    also how the owner confirmed Qt was applying it — the app's behaviour changed with the slider."""
    wheel_lines(lines)
    panel.verticalScrollBar().setValue(0)
    assert _wheel(panel) == pytest.approx(panel._row_pitch() / _NOTCH_PER_THUMB, abs=1)


# ---- continuous, not stepped ------------------------------------------------


def test_successive_detents_land_between_thumbnails(panel):
    """Edge's behaviour, and the point of the milestone: the strip stops at fractions of a
    thumbnail — *"only half or a fraction of it is visible on the top"* — not only on boundaries."""
    pitch = panel._row_pitch()
    panel.verticalScrollBar().setValue(0)
    offsets = [(_wheel(panel) % pitch) / pitch for _ in range(_NOTCH_PER_THUMB * 2)]
    partial = [f for f in offsets if 0.05 < f < 0.95]
    assert len(partial) >= _NOTCH_PER_THUMB, f"never landed mid-thumbnail: {offsets}"


def test_sub_notch_delta_moves_proportionally(panel):
    """A free-spin or hi-res wheel reporting a fraction of a notch moves a matching fraction —
    the step is proportional to the raw delta, not quantised to whole detents."""
    expected = panel._row_pitch() / _NOTCH_PER_THUMB / 2
    assert _wheel(panel, angle_y=-_WHEEL_NOTCH // 2) == pytest.approx(expected, abs=1)


# ---- invariant across sidebar width -----------------------------------------


@pytest.mark.parametrize("width", [150, 210, 276])
def test_step_stays_a_third_of_a_thumbnail_at_every_width(qapp, panel, width):
    """The thumbnails scale with the bar (`_apply_thumb_size`), so a fixed pixel constant would
    drift from a third to a half across the range. Scaling by the pitch is the sidebar's analogue
    of M92.1 scaling the document view's detent by zoom."""
    panel.resize(width, _BAR_H)
    qapp.processEvents()
    panel.verticalScrollBar().setValue(0)
    assert _wheel(panel) / panel._row_pitch() == pytest.approx(1 / _NOTCH_PER_THUMB, abs=0.02)


# ---- the boundary -----------------------------------------------------------


def test_unscrollable_document_leaves_the_event_to_qt(qapp, tmp_path):
    """A document too short to scroll must not have its wheel event swallowed — Qt's path lets an
    unusable event propagate to the parent instead."""
    path = str(tmp_path / "one.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(path)
    doc.close()
    panel = ThumbnailPanel(VirtualDocument.from_path(path))
    panel.resize(_BAR_W, _BAR_H)
    panel.show()
    qapp.processEvents()

    assert panel.verticalScrollBar().maximum() == 0
    assert _wheel(panel) == 0


def test_tilt_wheel_is_left_to_qt(panel):
    """A horizontal wheel carries no vertical angle; it must not be read as a vertical step."""
    assert _wheel(panel, angle_y=0, angle_x=-_WHEEL_NOTCH) == 0
