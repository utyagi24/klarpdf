"""Find-in-document: ``page.search_for`` highlighting + hit navigation (PLAN.md, Viewer).

:class:`SearchController` collects every hit across the document — each with a **context
snippet** (M47) — paints them, and tracks a "current" hit for next/prev navigation (wrapping),
scrolling each into view. :class:`FindBar` is the small UI (text field + Match case / Whole words
toggles (M75) + prev/next/close + List All) MainWindow shows on Ctrl+F; :class:`SearchResultsPanel`
is the M47 doc-wide hit list
(page + snippet, click-to-jump) that appears only on List All — and is the reviewable-hit-list
surface M64 (search & redact) later extends with checkboxes.

Highlight placement uses the rotation-0 geometry helpers, so highlights are drawn only in an
unrotated view; navigation still works regardless.
"""

from __future__ import annotations

import pymupdf as fitz
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
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

#: Live search-as-you-type is coalesced over this idle gap (ms). Read at call time so tests can
#: set it to 0 for a synchronous search — see :meth:`FindBar._on_text`.
SEARCH_DEBOUNCE_MS = 250


def _boxes_touch(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class _PageText:
    """Every text lookup a page's hits need, extracted **once** and shared by all of them.

    Each of the three per-hit lookups used to re-scan the whole page, which is fine for the handful
    of hits a small document returns and quadratic-feeling on a real one. Measured on the 320-page
    ``spaceX_prospectus.pdf``, where the one-letter query a live search runs first has ~72 000 hits:

    * the snippet walked the full word list **three times per hit** — ~4.4 s document-wide;
    * Match case called ``page.get_textbox`` per hit, and *that* re-extracts the page's text on
      every call (~31 ms each) — ~37 **minutes** document-wide, i.e. the reported hang.

    Both are answered from a per-line index built once: a hit box can only touch words (or chars)
    whose line shares its vertical band, so a lookup scans one line rather than one page. The char
    index behind :meth:`text_under` is built lazily — only Match case needs it.
    """

    def __init__(self, page=None, words: list | None = None) -> None:
        self._page = page
        self.words: list = page.get_text("words") if words is None else words
        self._by_key: dict[tuple, list] = {}
        for i, w in enumerate(self.words):
            self._by_key.setdefault((w[5], w[6]), []).append((i, w))
        self._lines = [(min(w[1] for _, w in ws), max(w[3] for _, w in ws), ws)
                       for ws in self._by_key.values()]
        self._chars: list | None = None

    @staticmethod
    def _band(lines: list, box: tuple) -> list:
        """The lines whose vertical band meets ``box``. A word can only touch the box if its own
        band does, and a line's band covers every word on it — so this narrows without dropping."""
        return [entry for entry in lines if entry[0] < box[3] and box[1] < entry[1]]

    def struck(self, box: tuple) -> list:
        """The page words ``box`` overlaps, in document order (as a full scan would return)."""
        found = [(i, w) for _ly0, _ly1, ws in self._band(self._lines, box)
                 for i, w in ws if _boxes_touch(w[:4], box)]
        found.sort(key=lambda t: t[0])
        return found

    def is_whole_word(self, box: tuple, tol: float = 0.5) -> bool:
        struck = self.struck(box)
        if not struck:
            return True                  # nothing to contradict it (e.g. a hit with no word boxes)
        return struck[0][1][0] >= box[0] - tol and struck[-1][1][2] <= box[2] + tol

    def snippet(self, box: tuple) -> str:
        struck = self.struck(box)
        if not struck:
            return ""
        first = struck[0][1]
        line = [w for _i, w in self._by_key[(first[5], first[6])]]
        matched = [i for i, w in enumerate(line) if _boxes_touch(w[:4], box)]
        lo = max(0, matched[0] - _SNIPPET_WORDS)
        hi = min(len(line), matched[-1] + 1 + _SNIPPET_WORDS)
        text = " ".join(w[4] for w in line[lo:hi])
        return ("… " if lo > 0 else "") + text + (" …" if hi < len(line) else "")

    def _char_lines(self) -> list:
        """Per-line char index ``(y0, y1, [(x0, y0, x1, y1, char), …])``, built on first use."""
        if self._chars is None:
            self._chars = []
            if self._page is not None:
                raw = self._page.get_text("rawdict", flags=fitz.TEXTFLAGS_TEXT)
                for block in raw.get("blocks", []):
                    for line in block.get("lines", []):
                        chars = [(*ch["bbox"], ch["c"])
                                 for span in line.get("spans", []) for ch in span.get("chars", [])]
                        if chars:
                            self._chars.append((min(c[1] for c in chars),
                                                max(c[3] for c in chars), chars))
        return self._chars

    def text_under(self, box: tuple) -> str:
        """The text actually under a hit box — what Match case compares against the term.

        Char-level, taking each char whose **centre** falls inside the box. ``page.get_textbox``
        answered the same question by clipping, which sweeps in whatever else shares the rect's
        band — a hit on "SP" came back as ``'Cla\\nSP'`` — so a genuinely case-matching hit was
        rejected because of its neighbours. This is both the faster answer and the correct one.
        """
        x0, y0, x1, y1 = box
        return "".join(c[4] for _ly0, _ly1, chars in self._band(self._char_lines(), box)
                       for c in chars
                       if x0 <= (c[0] + c[2]) / 2 <= x1 and y0 <= (c[1] + c[3]) / 2 <= y1)


def is_whole_word(words: list, box: tuple, tol: float = 0.5) -> bool:
    """Is the hit at ``box`` a whole word rather than part of a longer one? (M64)

    Geometric rather than textual: a hit is a whole word when the words it touches do not extend
    past it on either side. Searching "Smith" matches inside "Smithsonian", whose word box runs well
    beyond the hit — which is precisely the false positive the review step exists to catch, and this
    toggle to prevent wholesale.
    """
    return _PageText(words=words).is_whole_word(box, tol)


def _snippet_for(words: list, box: tuple) -> str:
    """Context snippet for a hit ``box``: the words of its line, windowed to ±``_SNIPPET_WORDS``
    around the matched span, with ellipses marking a trimmed side. ``words`` is the page's
    ``get_text("words")`` list (w = x0,y0,x1,y1,text,block,line,word)."""
    return _PageText(words=words).snippet(box)


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

        ``whole_word`` decides **what the query is** as well as how it matches (M75.1). Off, the
        query is a list of words and any of them matches on its own — "electric heater" finds every
        *electric* and every *heater*, each still matching inside longer words. On, the query is one
        unit: the whole phrase, and only where neither end sits inside a longer word (see
        :func:`is_whole_word`) — "electric heater" then finds just that phrase. ``case_sensitive``
        compares the text actually under each hit box against the term that found it.

        The filters exist because MuPDF's ``search_for`` is always case-insensitive and always
        matches inside words; it is run once per term, and a multi-term result is re-ordered into
        reading order per page so next/prev still walks the page the way it is read.
        """
        self._query = query or ""
        self._hits = []
        self._idx = -1
        terms = [self._query] if whole_word else self._query.split()
        if terms:
            for page_index in range(self._view._vdoc.page_count):
                ref = self._view._vdoc.ordered[page_index]
                page = self._view._vdoc.sources[ref.source_id][ref.source_page_index]
                found = [(r, term) for term in terms for r in page.search_for(term)]
                if not found:
                    continue
                if len(terms) > 1:   # one term already comes back in reading order
                    found.sort(key=lambda f: (round(f[0].y0, 1), f[0].x0))
                text = _PageText(page)   # one extraction + index serves the page's hits
                seen: set = set()
                for r, term in found:
                    box = (r.x0, r.y0, r.x1, r.y1)
                    key = tuple(round(v, 2) for v in box)
                    if key in seen:
                        continue    # two terms landing on the same text is still one hit
                    seen.add(key)
                    if whole_word and not text.is_whole_word(box):
                        continue
                    if case_sensitive and text.text_under(box).strip() != term:
                        continue
                    self._hits.append((page_index, box, text.snippet(box)))
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
    """Text field + match options + prev/next/List All/close, wired to a view's
    :class:`SearchController`.

    **Match case** and **Whole words** (M75) are M64's existing ``search()`` filters, surfaced on
    the interactive bar at last — same labels as the Find-and-Redact dialog, both off by default.
    Toggling one re-runs the live query in place, so the hits, the count label and a visible results
    panel all follow without retyping. **Whole words** also decides whether a multi-word query is a
    phrase or a list of words (see :meth:`SearchController.search`).

    Previous / Next / List All are disabled while the search has no hits — see
    :meth:`_sync_controls`.

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
        self._case_box = QCheckBox("Match case")
        self._word_box = QCheckBox("Whole words")
        self._word_box.setToolTip("On: match the whole phrase, and only as whole words\n"
                                  "Off: match any of the words, inside longer words too")
        self._prev_btn = QPushButton("Previous")
        self._next_btn = QPushButton("Next")
        self._list_btn = QPushButton("List All")
        self._list_btn.setCheckable(True)
        self._list_btn.setToolTip("List every match with its context; click a row to jump")
        close_btn = QPushButton("✕")
        close_btn.setMaximumWidth(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._case_box)
        layout.addWidget(self._word_box)
        layout.addWidget(self._label)
        layout.addWidget(self._prev_btn)
        layout.addWidget(self._next_btn)
        layout.addWidget(self._list_btn)
        layout.addWidget(close_btn)

        self._pending = QTimer(self)   # the debounce behind live search — see _on_text
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self._run_query)

        self._edit.textChanged.connect(self._on_text)
        self._edit.returnPressed.connect(self._on_next)
        self._case_box.toggled.connect(self._on_options_changed)
        self._word_box.toggled.connect(self._on_options_changed)
        self._prev_btn.clicked.connect(self._on_prev)
        self._next_btn.clicked.connect(self._on_next)
        self._list_btn.toggled.connect(self._on_list_toggled)
        close_btn.clicked.connect(self.hide_bar)
        self._sync_controls()   # nothing searched yet: the three hit verbs start dead
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
            self._run_query()

    def hide_bar(self) -> None:
        self._pending.stop()   # a queued keystroke must not re-populate hits behind a closed bar
        self._view.search.clear()
        self._list_btn.setChecked(False)  # also hides the results panel (toggled handler)
        self.hide()
        self._view.setFocus()

    def find_next(self) -> None:
        self.flush_pending_search()   # Enter mid-debounce searches what was typed, not the last run
        self._view.search.next()
        self._sync_results()
        self._update_label()

    def find_prev(self) -> None:
        self.flush_pending_search()
        self._view.search.prev()
        self._sync_results()
        self._update_label()

    def flush_pending_search(self) -> None:
        """Run a debounced query now, if one is waiting."""
        if self._pending.isActive():
            self._pending.stop()
            self._run_query()

    def _on_text(self, _text: str = "") -> None:
        """A keystroke schedules the search rather than running it (``SEARCH_DEBOUNCE_MS``).

        Every prefix of a query is a throwaway search, and the *first* one — a single letter — is
        the most expensive search the document has: on a 320-page file "s" matches ~72 000 times,
        so typing a five-letter word ran five full-document scans, the worst of them first. Coalescing
        the burst means one scan, for the query the user actually meant. A 0 interval runs
        synchronously, which is what the tests assert against."""
        if SEARCH_DEBOUNCE_MS > 0:
            self._pending.start(SEARCH_DEBOUNCE_MS)
        else:
            self._run_query()

    def _run_query(self) -> None:
        self._view.search.search(self._edit.text(), case_sensitive=self._case_box.isChecked(),
                                 whole_word=self._word_box.isChecked())
        if self.results_panel is not None and self._list_btn.isChecked():
            self.results_panel.refresh()  # a live panel follows the query as it is typed
        self._update_label()

    def _on_options_changed(self) -> None:
        """A match option toggled → re-run the live query under the new filters (M75). The label,
        highlights and a visible results panel all refresh through the ordinary ``_run_query`` path;
        an empty field is a cheap no-op search. Not debounced: a deliberate click has no burst to
        coalesce, so it answers at once."""
        self._pending.stop()
        self._run_query()

    def _on_next(self) -> None:
        self.find_next()

    def _on_prev(self) -> None:
        self.find_prev()

    def _on_list_toggled(self, checked: bool) -> None:
        if self.results_panel is None:
            return
        if checked:
            self.results_panel.refresh()
        self._sync_controls()

    def _sync_controls(self) -> None:
        """Previous / Next / List All act on hits, so with none they are **dead verbs** — disabled
        rather than clickable no-ops, which is the same rule as the outline tab that only exists
        when the document has one. The list panel goes with them: an empty band under the bar says
        nothing the "No results" label doesn't, and it returns (still listing) the moment the query
        matches again."""
        _idx, total = self._view.search.position()
        for button in (self._prev_btn, self._next_btn, self._list_btn):
            button.setEnabled(total > 0)
        if self.results_panel is not None:
            self.results_panel.setVisible(self._list_btn.isChecked() and total > 0)

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
        self._sync_controls()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide_bar()
            return
        super().keyPressEvent(event)
