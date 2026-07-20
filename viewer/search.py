"""Find-in-document: ``page.search_for`` highlighting + hit navigation (PLAN.md, Viewer).

:class:`SearchController` collects every hit across the document — each with a **context
snippet** (M47) — paints them, and tracks a "current" hit for next/prev navigation (wrapping),
scrolling each into view. :class:`FindBar` is the small UI (text field + prev/next/close +
List All) MainWindow shows on Ctrl+F; :class:`SearchResultsPanel` is the M47 doc-wide hit list
(page + snippet, click-to-jump) that appears only on List All — and is the reviewable-hit-list
surface M64 (search & redact) later extends with checkboxes.

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
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

_HIT = QColor(255, 235, 59, 90)        # all matches: translucent yellow
_CURRENT = QColor(255, 138, 0, 150)    # current match: stronger orange
_SNIPPET_WORDS = 4                     # context words kept either side of a match in a snippet


def _boxes_touch(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _struck_words(words: list, box: tuple) -> list:
    """The page words a hit ``box`` overlaps, in reading order."""
    return [w for w in words if _boxes_touch(w[:4], box)]


def is_whole_word(words: list, box: tuple, tol: float = 0.5) -> bool:
    """Is the hit at ``box`` a whole word rather than part of a longer one? (M64)

    Geometric rather than textual: a hit is a whole word when the words it touches do not extend
    past it on either side. Searching "Smith" matches inside "Smithsonian", whose word box runs well
    beyond the hit — which is precisely the false positive the review step exists to catch, and this
    toggle to prevent wholesale.
    """
    struck = _struck_words(words, box)
    if not struck:
        return True                      # nothing to contradict it (e.g. a hit with no word boxes)
    return struck[0][0] >= box[0] - tol and struck[-1][2] <= box[2] + tol


def _snippet_for(words: list, box: tuple) -> str:
    """Context snippet for a hit ``box``: the words of its line, windowed to ±``_SNIPPET_WORDS``
    around the matched span, with ellipses marking a trimmed side. ``words`` is the page's
    ``get_text("words")`` list (w = x0,y0,x1,y1,text,block,line,word)."""
    struck = [w for w in words if _boxes_touch(w[:4], box)]
    if not struck:
        return ""
    block_no, line_no = struck[0][5], struck[0][6]
    line = [w for w in words if w[5] == block_no and w[6] == line_no]
    matched = [i for i, w in enumerate(line) if _boxes_touch(w[:4], box)]
    lo = max(0, matched[0] - _SNIPPET_WORDS)
    hi = min(len(line), matched[-1] + 1 + _SNIPPET_WORDS)
    text = " ".join(w[4] for w in line[lo:hi])
    return ("… " if lo > 0 else "") + text + (" …" if hi < len(line) else "")


class SearchController:
    def __init__(self, view) -> None:
        self._view = view
        self._hits: list[tuple[int, tuple, str]] = []   # (page_index, box_pts, snippet)
        self._idx = -1
        self._items: list[QGraphicsRectItem] = []
        self._query = ""

    def search(self, query: str, case_sensitive: bool = False,
               whole_word: bool = False) -> int:
        """Find all matches for ``query`` and select the first. Returns the hit count.

        MuPDF's ``search_for`` is always case-insensitive and always matches inside words, so the
        two M64 toggles are applied here as filters over its hits: ``case_sensitive`` compares the
        text actually under each hit box, ``whole_word`` checks the hit is not part of a longer word
        (see :func:`is_whole_word`). Both default off, which is exactly today's behaviour.
        """
        self._query = query or ""
        self._hits = []
        self._idx = -1
        if self._query:
            for page_index in range(self._view._vdoc.page_count):
                ref = self._view._vdoc.ordered[page_index]
                page = self._view._vdoc.sources[ref.source_id][ref.source_page_index]
                boxes = page.search_for(self._query)
                words = page.get_text("words") if boxes else []  # one scan serves the page's hits
                for r in boxes:
                    box = (r.x0, r.y0, r.x1, r.y1)
                    if whole_word and not is_whole_word(words, box):
                        continue
                    if case_sensitive and page.get_textbox(r).strip() != self._query:
                        continue
                    self._hits.append((page_index, box, _snippet_for(words, box)))
            if self._hits:
                self._idx = 0
        self.repaint()
        if self._idx >= 0:
            self._reveal()
        return len(self._hits)

    def hits(self) -> list[tuple[int, tuple, str]]:
        """Every hit as ``(page_index, box, snippet)`` in document order (M47 results panel)."""
        return list(self._hits)

    def position(self) -> tuple[int, int]:
        """``(current_index, total)`` — current is -1 when there are no hits."""
        return self._idx, len(self._hits)

    def goto(self, index: int) -> None:
        """Make hit ``index`` current and scroll it into view (M47 click-to-jump)."""
        if 0 <= index < len(self._hits):
            self._idx = index
            self.repaint()
            self._reveal()

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
        page_index, box, _snippet = self._hits[self._idx]
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
        for i, (page_index, box, _snippet) in enumerate(self._hits):
            item = QGraphicsRectItem(self._view.scene_rect_for_box(page_index, box))
            item.setBrush(QBrush(_CURRENT if i == self._idx else _HIT))
            item.setPen(QColor(0, 0, 0, 0))
            item.setZValue(9)
            scene.addItem(item)
            self._items.append(item)


class SearchResultsPanel(QListWidget):
    """The M47 doc-wide hit list: one row per hit — "p. N   …snippet…" — click to jump. Exists in
    the layout but stays hidden until the FindBar's List All toggle shows it (no dead chrome).

    In **checkable** mode (M64) each row gains a checkbox and the list becomes the review step of
    search-&-redact: the snippet is what lets you untick the "Smithsonian" that a search for "Smith"
    turned up. Clicking a row still jumps to the hit, so a doubtful one can be inspected on the page
    before deciding — which is the point of reviewing on the real panel rather than in a bare list.
    """

    _INDEX_ROLE = Qt.ItemDataRole.UserRole  # row payload: the hit's index in the controller

    def __init__(self, view, parent=None, checkable: bool = False) -> None:
        super().__init__(parent)
        self._view = view
        self._checkable = checkable
        self.setUniformItemSizes(True)
        self.setMaximumHeight(180)  # a band under the find bar, never crowding out the page
        self.itemClicked.connect(self._on_item_clicked)
        self.hide()

    def refresh(self) -> None:
        """Rebuild the rows from the controller's current hits and mark the current one."""
        self.clear()
        idx, _total = self._view.search.position()
        for i, (page_index, _box, snippet) in enumerate(self._view.search.hits()):
            item = QListWidgetItem(f"p. {page_index + 1}   {snippet}")
            item.setData(self._INDEX_ROLE, i)
            item.setToolTip(f"Page {page_index + 1}")
            if self._checkable:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)   # opt-out, not opt-in: the user asked
            self.addItem(item)                              # for all of them, then prunes
        if idx >= 0:
            self.setCurrentRow(idx)

    def checked_hits(self) -> list[tuple[int, tuple]]:
        """``(page_index, box)`` for every ticked row — what a redaction would actually cover."""
        hits = self._view.search.hits()
        chosen = []
        for row in range(self.count()):
            item = self.item(row)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            index = item.data(self._INDEX_ROLE)
            if 0 <= index < len(hits):
                page_index, box, _snippet = hits[index]
                chosen.append((page_index, box))
        return chosen

    def set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.count()):
            self.item(row).setCheckState(state)

    def _on_item_clicked(self, item) -> None:
        self._view.search.goto(item.data(self._INDEX_ROLE))


class FindBar(QWidget):
    """Text field + prev/next/List All/close, wired to a view's :class:`SearchController`.

    ``results_panel`` (set by MainWindow) is the :class:`SearchResultsPanel` this bar drives: the
    List All toggle shows/hides it, a re-typed query refreshes it while visible, and closing the
    bar hides it with everything else."""

    def __init__(self, view, parent=None) -> None:
        super().__init__(parent)
        self._view = view
        self.results_panel = None
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Find in document")
        self._label = QLabel("")
        prev_btn = QPushButton("Previous")
        next_btn = QPushButton("Next")
        self._list_btn = QPushButton("List All")
        self._list_btn.setCheckable(True)
        self._list_btn.setToolTip("List every match with its context; click a row to jump")
        close_btn = QPushButton("✕")
        close_btn.setMaximumWidth(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._label)
        layout.addWidget(prev_btn)
        layout.addWidget(next_btn)
        layout.addWidget(self._list_btn)
        layout.addWidget(close_btn)

        self._edit.textChanged.connect(self._on_text)
        self._edit.returnPressed.connect(self._on_next)
        prev_btn.clicked.connect(self._on_prev)
        next_btn.clicked.connect(self._on_next)
        self._list_btn.toggled.connect(self._on_list_toggled)
        close_btn.clicked.connect(self.hide_bar)
        self.hide()

    def show_bar(self) -> None:
        self.show()
        self._edit.setFocus()
        self._edit.selectAll()
        # Closing the bar clears the search (the highlight overlays must go), but the query text
        # survives in the field — so reopening showed the old query with zero hits behind it, a
        # dead state where only retyping revived the search. Re-run the kept query when the
        # controller is empty; a bar that is already live (Ctrl+F while open) keeps its current
        # hit position untouched.
        if self._edit.text() and self._view.search.position()[1] == 0:
            self._on_text(self._edit.text())

    def hide_bar(self) -> None:
        self._view.search.clear()
        self._list_btn.setChecked(False)  # also hides the results panel (toggled handler)
        self.hide()
        self._view.setFocus()

    def find_next(self) -> None:
        self._view.search.next()
        self._sync_results()
        self._update_label()

    def find_prev(self) -> None:
        self._view.search.prev()
        self._sync_results()
        self._update_label()

    def _on_text(self, text: str) -> None:
        self._view.search.search(text)
        if self.results_panel is not None and self.results_panel.isVisible():
            self.results_panel.refresh()  # a live panel follows the query as it is typed
        self._update_label()

    def _on_next(self) -> None:
        self.find_next()

    def _on_prev(self) -> None:
        self.find_prev()

    def _on_list_toggled(self, checked: bool) -> None:
        if self.results_panel is None:
            return
        if checked:
            self.results_panel.refresh()
        self.results_panel.setVisible(checked)

    def _sync_results(self) -> None:
        """Keep the visible panel's current-row marker on the controller's current hit."""
        if self.results_panel is not None and self.results_panel.isVisible():
            idx, _total = self._view.search.position()
            if idx >= 0:
                self.results_panel.setCurrentRow(idx)

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
