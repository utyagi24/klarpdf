"""Annotations sidebar list (PLAN.md §GUI feature roadmap → R6, M77).

A sidebar tab beside Pages | Outline listing the document's **text markups** — highlights,
underlines, strike-outs and notes — ours (the PageRef descriptors) and foreign (another tool's
annotations, read live so pending deletions / moves are respected), as "p. N · type · snippet"
rows; clicking a row jumps to the mark and selects it (the M47 click-to-jump pattern).

**Markups, not every mark** (M77.1, owner's call after the R6 test pass). A highlight is a
*passage* — the row's snippet is the point of it, and a list of them is a reading of what you
marked. A pen stroke, a shape, a text box, a stamp or a form field is a placed **object**: it has
no passage to read back, its row said only "p. 3 · line", and a page of drawings buried the
markups they were drawn around. Objects are found where they sit, or through the Objects mode
that exists to arrange them. :func:`is_listed` / :func:`is_listed_foreign` are the one definition
of what belongs here, shared with the tab's own existence check in ``MainWindow``.

The tab **exists only while the document has marks it would list** (owner rule: inapplicable
chrome is invisible, not greyed out) — its mounting lives in ``MainWindow._mount_sidebar``, and
``populate()`` is re-run after every edit so the list follows add / remove / undo live.

Foreign annotations come through a ``foreign_provider`` callable (in practice
``AnnotationOverlay.foreign_annotations``) rather than a viewer import, so this panel — like the
outline panel — depends only on the model and the provider seam.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from model.page_edits import Highlight, Strikeout, Underline
from model.page_text import PageText
from model.virtual_document import VirtualDocument
from organize.thumbnail_panel import _SIDEBAR_W  # one default width for all sidebar tabs

_ROLE = Qt.ItemDataRole.UserRole  # row payload: (page_index, mark, bounds)
_SNIPPET_CHARS = 48
# A note is clipped harder than the passage it annotates (M90.3). The snippet is what identifies
# the row — you find the mark by recognising the words — while the note is the *content*, read in
# full from the row's tooltip or by opening it. A long remark must not push the passage off the row.
_NOTE_CHARS = 32
_NOTE_LEAD = " — "   # an em dash sets the remark off from the passage without looking like more of it

# The text markups, ours and by PDF subtype. Squiggly is a wavy underline — a markup we cannot
# draw but can perfectly well list; Text is the sticky note, which is the "notes" of the list's
# remit arriving from another tool ahead of our own. Everything absent from these two is an
# object, deliberately: see the module docstring.
_LISTED = (Highlight, Underline, Strikeout)
_LISTED_FOREIGN_KINDS = frozenset({"Highlight", "Underline", "StrikeOut", "Squiggly", "Text"})


def is_listed(mark) -> bool:
    """Does this descriptor of ours belong in the list? (Bookkeeping descriptors — foreign
    deletions and moves — are not marks at all and fail this with everything else.)"""
    return isinstance(mark, _LISTED)


def is_listed_foreign(annot) -> bool:
    """Does this foreign annotation belong in the list?"""
    return annot.kind_name in _LISTED_FOREIGN_KINDS


def _clip(text: str, limit: int = _SNIPPET_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _union(rects) -> tuple:
    xs0, ys0, xs1, ys1 = zip(*((r[0], r[1], r[2], r[3]) for r in rects))
    return (min(xs0), min(ys0), max(xs1), max(ys1))


class AnnotationsPanel(QListWidget):
    """Flat list of every mark in the document, in page order; click to jump + select."""

    markActivated = Signal(int, object, tuple)  # (page_index, mark | ForeignAnnot, bounds)
    noteRequested = Signal(int, object)         # double-clicked one of ours: write / edit its note

    def __init__(self, vdoc: VirtualDocument, foreign_provider, parent=None) -> None:
        super().__init__(parent)
        self._vdoc = vdoc
        self._foreign = foreign_provider
        self._page_text: dict[int, PageText] = {}   # one index per page, per rebuild
        self.setUniformItemSizes(True)
        self.itemClicked.connect(self._on_item_clicked)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.populate()

    def sizeHint(self) -> QSize:
        return QSize(_SIDEBAR_W, super().sizeHint().height())

    # ---- build ------------------------------------------------------------------

    def populate(self) -> None:
        """(Re)build the rows from the live document. Called after every edit
        (``MainWindow._on_doc_changed``), so the list follows add / remove / undo.

        Every row's snippet is a text lookup on its page, so the page indexes are shared **across
        the whole rebuild** — marks that sit on one page read it once between them, which is the
        difference between 15.7 s and 0.14 s at 200 highlights (M78.8). They are dropped again at
        the end: an index describes a page as it was, and the next rebuild is called precisely
        because something changed."""
        self.clear()
        self._page_text = {}
        try:
            self._populate_rows()
        finally:
            self._page_text = {}

    def _populate_rows(self) -> None:
        for page_index in range(self._vdoc.page_count):
            for mark in self._vdoc.page_annotations(page_index):
                if not is_listed(mark):
                    continue
                self._add_row(page_index, mark, self._describe(page_index, mark),
                              self._bounds(mark), note=getattr(mark, "note", ""))
            for annot in self._foreign(page_index):
                if not is_listed_foreign(annot):
                    continue
                self._add_row(page_index, annot, self._describe_foreign(page_index, annot),
                              annot.rect, note=annot.contents)

    def _add_row(self, page_index: int, mark, label: str, bounds: tuple,
                 note: str = "") -> None:
        item = QListWidgetItem(f"p. {page_index + 1} · {label}")
        item.setData(_ROLE, (page_index, mark, tuple(bounds)))
        # The **full** note is the tooltip when there is one (M90.3): the row clips it to keep the
        # passage readable, and a remark you can only read half of is worse than one you can hover.
        item.setToolTip(note or f"Page {page_index + 1}")
        self.addItem(item)

    def _describe(self, page_index: int, mark) -> str:
        from viewer.annotations import mark_noun  # lazy: one vocabulary, no import-time cycle

        noun = mark_noun(mark)
        # Every listed mark of ours is text-anchored, so the snippet is always the covered text —
        # which is the row's whole value: a highlight row reads back the passage you highlighted.
        snippet = _clip(self._covered_text(page_index, mark.rects))
        row = f"{noun} · {snippet}" if snippet else noun
        # …and since M90.3 the note follows it, because for a *noted* mark the remark is the point
        # of the row. Appended rather than substituted: the passage is what lets you recognise
        # which mark this is, so it stays even when the note is the more interesting half.
        note = _clip(getattr(mark, "note", ""), _NOTE_CHARS)
        return f"{row}{_NOTE_LEAD}{note}" if note else row

    def _describe_foreign(self, page_index: int, annot) -> str:
        """A foreign row, in **the same shape as one of ours**: type · passage — comment (M90.4).

        Before this it read ``type · comment``, which put the *comment* in the slot our own rows
        use for the *passage* — so the same position on the same list meant two different things
        depending on who wrote the mark, and a commented foreign highlight never showed the words
        it covered at all. A sticky note has no passage under it and correctly shows none.
        """
        row = annot.kind_name.lower()
        snippet = _clip(self._covered_text(page_index, (annot.rect,)))
        if snippet:
            row = f"{row} · {snippet}"
        comment = _clip(annot.contents, _NOTE_CHARS)
        return f"{row}{_NOTE_LEAD}{comment}" if comment else row

    def _covered_text(self, page_index: int, rects) -> str:
        """The page text under a text-anchored mark's bars — what a highlight row should read as.

        Read from the page's :class:`PageText` index rather than ``page.get_textbox(rect)``. That
        call was wrong twice: it re-extracted the whole page **per bar** (15.7 s per rebuild at 200
        highlights, and a rebuild follows every edit), and it answers by *clipping*, so it returned
        whatever else shared the bar's band — on a two-column page 567 of 700 single-word
        highlights read back as text the reader never highlighted, e.g. "Following" as "Following
        and Class B" from the next column. The snippet is the row's whole value, so that was not a
        cosmetic fault. See :mod:`model.page_text`."""
        text = self._page_text_for(page_index)
        parts = [" ".join(text.text_under(tuple(r)).split()) for r in rects]
        return " ".join(p for p in parts if p)

    def _page_text_for(self, page_index: int) -> PageText:
        """The page's text index, built once per rebuild and shared by every mark on it."""
        text = self._page_text.get(page_index)
        if text is None:
            ref = self._vdoc.ordered[page_index]
            page = self._vdoc.sources[ref.source_id][ref.source_page_index]
            text = self._page_text[page_index] = PageText(page)
        return text

    @staticmethod
    def _bounds(mark) -> tuple:
        return _union(mark.rects)   # text-anchored: the union of its bars

    # ---- activation -------------------------------------------------------------

    def _on_item_clicked(self, item) -> None:
        page_index, mark, bounds = item.data(_ROLE)
        self.markActivated.emit(page_index, mark, bounds)

    def _on_item_double_clicked(self, item) -> None:
        """Double-click a row of **ours** → write or edit that mark's note (M90.3).

        The window answers this by jumping to the mark and opening the *same on-page popup* the
        glyph and the context menu open — deliberately, rather than growing a second editor inside
        the sidebar. "Editing here and on the page agree" is then true by construction instead of
        by two implementations being kept in step, which is the drift this codebase has been bitten
        by before (preview vs committed mark, M89.6).

        Foreign rows are excluded: another tool's comment is read-only until the mark is adopted
        (M90.4), and offering an editor that refuses to save would be worse than offering none.
        """
        page_index, mark, _bounds = item.data(_ROLE)
        if is_listed(mark):     # ours — a ForeignAnnot is not a descriptor and fails this
            self.noteRequested.emit(page_index, mark)
