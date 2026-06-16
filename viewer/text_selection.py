"""Word-box text selection + clipboard copy (PLAN.md, Viewer — the feature QPdfView lacks).

Selection is built from ``page.get_text("words")`` — each tuple is
``(x0, y0, x1, y1, word, block_no, line_no, word_no)``, so reading order comes straight from the
data (sort by ``(block_no, line_no, word_no)``). Mouse-down hit-tests to an anchor word; drag
hit-tests to a cursor word; the selection is the inclusive range between them in reading order.
Selected word boxes are painted as highlight rects; copy joins the words to the clipboard. The
selected text is exactly the preserved OCR layer.

Rotation 0 only (the geometry helpers it uses are rotation-0); selection is disabled in a rotated
view. Cross-page selection falls out of the global (page, word) ordering and is supported
best-effort — single-page selection is the thoroughly tested path (PLAN.md flags cross-page in
continuous scroll as a follow-up).
"""

from __future__ import annotations

from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import QGraphicsRectItem

_HIGHLIGHT = QColor(0, 120, 215, 80)  # translucent selection blue


class TextSelection:
    def __init__(self, view) -> None:
        self._view = view
        self._words: dict[int, list] = {}   # page_index -> words sorted in reading order
        self._items: list[QGraphicsRectItem] = []
        self._anchor: tuple[int, int] | None = None   # (page_index, word_order_index)
        self._cursor: tuple[int, int] | None = None
        self.active = False

    # ---- word data --------------------------------------------------------------

    def _words_for(self, page_index: int) -> list:
        cached = self._words.get(page_index)
        if cached is None:
            ref = self._view._vdoc.ordered[page_index]
            page = self._view._vdoc.sources[ref.source_id][ref.source_page_index]
            cached = sorted(page.get_text("words"), key=lambda w: (w[5], w[6], w[7]))
            self._words[page_index] = cached
        return cached

    def _hit(self, scene_pt) -> tuple[int, int] | None:
        page_index, local = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return None
        words = self._words_for(page_index)
        if not words:
            return None
        lx, ly = local.x(), local.y()
        for i, w in enumerate(words):
            if w[0] <= lx <= w[2] and w[1] <= ly <= w[3]:
                return (page_index, i)
        # No exact hit: snap to the nearest word, weighting vertical distance so we lock to the
        # correct line before choosing within it.
        best_i, best_d = 0, None
        for i, w in enumerate(words):
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            d = 4 * (cy - ly) ** 2 + (cx - lx) ** 2
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        return (page_index, best_i)

    # ---- mouse-driven lifecycle -------------------------------------------------

    def begin(self, scene_pt) -> bool:
        """Start a selection at ``scene_pt``. Returns True if it consumed the event."""
        if self._view.rotation != 0:
            return False
        hit = self._hit(scene_pt)
        if hit is None:
            self.clear()
            return False
        self.active = True
        self._anchor = self._cursor = hit
        self.repaint()
        return True

    def update_to(self, scene_pt) -> None:
        hit = self._hit(scene_pt)
        if hit is not None:
            self._cursor = hit
            self.repaint()

    def finish(self) -> None:
        self.active = False

    def clear(self) -> None:
        self._anchor = self._cursor = None
        self.active = False
        self._clear_items()

    # ---- selection content ------------------------------------------------------

    def selected_words(self) -> list[tuple[int, int, tuple]]:
        """Return ``(page_index, order_index, word_tuple)`` for the inclusive selection."""
        if self._anchor is None or self._cursor is None:
            return []
        lo, hi = sorted((self._anchor, self._cursor))
        out: list[tuple[int, int, tuple]] = []
        for page_index in range(lo[0], hi[0] + 1):
            words = self._words_for(page_index)
            start = lo[1] if page_index == lo[0] else 0
            end = hi[1] if page_index == hi[0] else len(words) - 1
            for i in range(start, end + 1):
                out.append((page_index, i, words[i]))
        return out

    def selected_text(self) -> str:
        parts: list[str] = []
        last_line = None
        for page_index, _i, w in self.selected_words():
            line_key = (page_index, w[5], w[6])
            if last_line is not None:
                parts.append("\n" if line_key != last_line else " ")
            parts.append(w[4])
            last_line = line_key
        return "".join(parts)

    def copy(self) -> bool:
        """Copy the current selection to the clipboard. Returns False if nothing selected."""
        text = self.selected_text()
        if not text:
            return False
        QGuiApplication.clipboard().setText(text)
        return True

    # ---- highlight painting -----------------------------------------------------

    def _clear_items(self) -> None:
        scene = self._view.scene()
        for item in self._items:
            try:
                if item.scene() is scene:
                    scene.removeItem(item)
            except RuntimeError:
                pass  # already destroyed by scene.clear() during a rebuild
        self._items.clear()

    def repaint(self) -> None:
        """Rebuild highlight rects from the logical selection (also called after zoom/rebuild)."""
        self._clear_items()
        if self._view.rotation != 0:
            return
        scene = self._view.scene()
        brush = QBrush(_HIGHLIGHT)
        for page_index, _i, w in self.selected_words():
            rect = self._view.scene_rect_for_box(page_index, (w[0], w[1], w[2], w[3]))
            item = QGraphicsRectItem(rect)
            item.setBrush(brush)
            item.setPen(QColor(0, 0, 0, 0))
            item.setZValue(10)
            scene.addItem(item)
            self._items.append(item)
