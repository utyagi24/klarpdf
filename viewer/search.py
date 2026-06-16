"""Find-in-document: ``page.search_for`` highlighting + hit navigation (PLAN.md, Viewer).

:class:`SearchController` collects every hit across the document, paints them, and tracks a
"current" hit for next/prev navigation (wrapping), scrolling each into view. :class:`FindBar` is
the small UI (text field + prev/next/close) MainWindow shows on Ctrl+F.

Highlight placement uses the rotation-0 geometry helpers, so highlights are drawn only in an
unrotated view; navigation still works regardless.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

_HIT = QColor(255, 235, 59, 90)        # all matches: translucent yellow
_CURRENT = QColor(255, 138, 0, 150)    # current match: stronger orange


class SearchController:
    def __init__(self, view) -> None:
        self._view = view
        self._hits: list[tuple[int, tuple]] = []   # (page_index, box_pts)
        self._idx = -1
        self._items: list[QGraphicsRectItem] = []
        self._query = ""

    def search(self, query: str) -> int:
        """Find all matches for ``query`` and select the first. Returns the hit count."""
        self._query = query or ""
        self._hits = []
        self._idx = -1
        if self._query:
            for page_index in range(self._view._vdoc.page_count):
                ref = self._view._vdoc.ordered[page_index]
                page = self._view._vdoc.sources[ref.source_id][ref.source_page_index]
                for r in page.search_for(self._query):
                    self._hits.append((page_index, (r.x0, r.y0, r.x1, r.y1)))
            if self._hits:
                self._idx = 0
        self.repaint()
        if self._idx >= 0:
            self._reveal()
        return len(self._hits)

    def position(self) -> tuple[int, int]:
        """``(current_index, total)`` — current is -1 when there are no hits."""
        return self._idx, len(self._hits)

    def next(self) -> None:
        if self._hits:
            self._idx = (self._idx + 1) % len(self._hits)
            self.repaint()
            self._reveal()

    def prev(self) -> None:
        if self._hits:
            self._idx = (self._idx - 1) % len(self._hits)
            self.repaint()
            self._reveal()

    def clear(self) -> None:
        self._query = ""
        self._hits = []
        self._idx = -1
        self._clear_items()

    def _reveal(self) -> None:
        page_index, box = self._hits[self._idx]
        self._view.ensure_box_visible(page_index, box)

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
        self._clear_items()
        if self._view.rotation != 0:
            return
        scene = self._view.scene()
        for i, (page_index, box) in enumerate(self._hits):
            item = QGraphicsRectItem(self._view.scene_rect_for_box(page_index, box))
            item.setBrush(QBrush(_CURRENT if i == self._idx else _HIT))
            item.setPen(QColor(0, 0, 0, 0))
            item.setZValue(9)
            scene.addItem(item)
            self._items.append(item)


class FindBar(QWidget):
    """Text field + prev/next/close, wired to a view's :class:`SearchController`."""

    def __init__(self, view, parent=None) -> None:
        super().__init__(parent)
        self._view = view
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Find in document")
        self._label = QLabel("")
        prev_btn = QPushButton("Previous")
        next_btn = QPushButton("Next")
        close_btn = QPushButton("✕")
        close_btn.setMaximumWidth(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._label)
        layout.addWidget(prev_btn)
        layout.addWidget(next_btn)
        layout.addWidget(close_btn)

        self._edit.textChanged.connect(self._on_text)
        self._edit.returnPressed.connect(self._on_next)
        prev_btn.clicked.connect(self._on_prev)
        next_btn.clicked.connect(self._on_next)
        close_btn.clicked.connect(self.hide_bar)
        self.hide()

    def show_bar(self) -> None:
        self.show()
        self._edit.setFocus()
        self._edit.selectAll()

    def hide_bar(self) -> None:
        self._view.search.clear()
        self.hide()
        self._view.setFocus()

    def find_next(self) -> None:
        self._view.search.next()
        self._update_label()

    def find_prev(self) -> None:
        self._view.search.prev()
        self._update_label()

    def _on_text(self, text: str) -> None:
        self._view.search.search(text)
        self._update_label()

    def _on_next(self) -> None:
        self.find_next()

    def _on_prev(self) -> None:
        self.find_prev()

    def _update_label(self) -> None:
        idx, total = self._view.search.position()
        if total:
            self._label.setText(f"{idx + 1} of {total}")
        else:
            self._label.setText("No results" if self._edit.text() else "")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide_bar()
            return
        super().keyPressEvent(event)
