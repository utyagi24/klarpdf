"""Draggable resize handles around a rect — the shared placement component (PLAN.md, M59.7).

Deliberately standalone and geometry-only: it knows how to *draw* handles, *hit-test* them, and
compute the *resized rect* for a drag — but nothing about annotations, undo, or the model. M59.7
drives it for object resize; **M62** (stamp / watermark placement) and **M69** (form-field creation)
are scheduled to reuse the same component for their "drag rect, move, corner-resize until save"
placement mode, which is why it lives here rather than inside the annotation overlay.

Two shapes of handle set:

* :meth:`ResizeHandles.show_box` — the eight-handle box (4 corners + 4 edge midpoints) around a
  rect: corners scale both axes, edges scale one. Used for shapes, ink strokes, and any group.
* :meth:`ResizeHandles.show_points` — handles at arbitrary named points, for geometry a box can't
  express: a **line's two endpoints** (a horizontal line's box is degenerate, so box handles would
  be unusable — you re-aim a line by dragging an end).

Handles are drawn in **scene units** (device pixels), not page points, so they stay a constant
on-screen size at any zoom — the rect they surround is given in unrotated page points. Rotation-0
only, like the other overlays.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem

BOX_HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
_SIZE = 8.0        # on-screen handle square, in scene units (== device px)
_GRAB = 4.0        # extra slack around a handle when hit-testing (fat-finger margin)
MIN_SIZE = 4.0     # a resized box never collapses below this, in page points

_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
}


def box_handle_points(rect: tuple) -> dict:
    """The eight handle anchor points for ``rect`` (unrotated page points)."""
    x0, y0, x1, y1 = rect
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return {
        "nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my),
        "se": (x1, y1), "s": (mx, y1), "sw": (x0, y1), "w": (x0, my),
    }


def cursor_for(handle: str):
    """The resize cursor for a handle name (endpoint handles get a plain cross-hair-ish size-all)."""
    return _CURSORS.get(handle, Qt.CursorShape.SizeAllCursor)


def resized_rect(rect: tuple, handle: str, dx: float, dy: float,
                 keep_aspect: bool = False) -> tuple:
    """``rect`` after dragging ``handle`` by ``(dx, dy)`` page points.

    Edge handles move one edge; corner handles move two. With ``keep_aspect`` (Shift held) a corner
    drag preserves the original proportions, growing from the opposite corner — the same
    Shift-constrains-the-gesture idiom the draw tools use. The result is normalised and never
    collapses below :data:`MIN_SIZE`.
    """
    x0, y0, x1, y1 = rect
    if keep_aspect and handle in ("nw", "ne", "se", "sw"):
        w0, h0 = x1 - x0, y1 - y0
        anchor_x = x1 if "w" in handle else x0        # the corner that stays put
        anchor_y = y1 if "n" in handle else y0
        moved_x = (x0 + dx) if "w" in handle else (x1 + dx)
        moved_y = (y0 + dy) if "n" in handle else (y1 + dy)
        if w0 > 0 and h0 > 0:
            scale = max(abs(moved_x - anchor_x) / w0, abs(moved_y - anchor_y) / h0)
            new_w, new_h = w0 * scale, h0 * scale
        else:
            new_w, new_h = abs(moved_x - anchor_x), abs(moved_y - anchor_y)
        x0, x1 = ((anchor_x - new_w, anchor_x) if "w" in handle
                  else (anchor_x, anchor_x + new_w))
        y0, y1 = ((anchor_y - new_h, anchor_y) if "n" in handle
                  else (anchor_y, anchor_y + new_h))
    else:
        if "n" in handle:
            y0 += dy
        if "s" in handle:
            y1 += dy
        if "w" in handle:
            x0 += dx
        if "e" in handle:
            x1 += dx
    x0, x1 = min(x0, x1), max(x0, x1)                 # a drag past the far edge flips it
    y0, y1 = min(y0, y1), max(y0, y1)
    if x1 - x0 < MIN_SIZE:
        x1 = x0 + MIN_SIZE
    if y1 - y0 < MIN_SIZE:
        y1 = y0 + MIN_SIZE
    return (x0, y0, x1, y1)


class ResizeHandles:
    """The handle items for one selection: draw, hit-test, clear."""

    def __init__(self, view) -> None:
        self._view = view
        self._items: dict = {}     # handle name -> QGraphicsRectItem (positioned in scene units)
        self._page = 0

    @property
    def visible(self) -> bool:
        return bool(self._items)

    @property
    def page(self) -> int:
        return self._page

    def show_box(self, page_index: int, rect: tuple) -> None:
        """Eight handles around ``rect`` (page points)."""
        self._place(page_index, box_handle_points(rect))

    def show_points(self, page_index: int, points: dict) -> None:
        """Handles at arbitrary named page points — e.g. ``{"p0": start, "p1": end}`` for a line."""
        self._place(page_index, points)

    def _place(self, page_index: int, points: dict) -> None:
        self.hide()
        self._page = page_index
        for name, (px, py) in points.items():
            centre = self._view.scene_rect_for_box(page_index, (px, py, px, py)).center()
            item = QGraphicsRectItem(
                QRectF(centre.x() - _SIZE / 2, centre.y() - _SIZE / 2, _SIZE, _SIZE)
            )
            item.setBrush(QBrush(QColor(255, 255, 255)))
            item.setPen(QPen(QColor(0, 120, 215), 1))
            item.setZValue(14)     # above the selection outline (12) and the marquee band (13)
            self._view.scene().addItem(item)
            self._items[name] = item

    def hide(self) -> None:
        scene = self._view.scene()
        for item in self._items.values():
            try:
                if item.scene() is scene:
                    scene.removeItem(item)
            except RuntimeError:
                pass  # already destroyed by scene.clear() during a rebuild (same as the overlay)
        self._items = {}

    def handle_at(self, scene_pt) -> "str | None":
        """The handle name under ``scene_pt`` (with a grab margin), else None."""
        for name, item in self._items.items():
            if item.rect().adjusted(-_GRAB, -_GRAB, _GRAB, _GRAB).contains(scene_pt):
                return name
        return None
