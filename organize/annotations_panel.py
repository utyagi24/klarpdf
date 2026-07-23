"""Annotations sidebar list (PLAN.md §GUI feature roadmap → R6, M77).

A third sidebar tab beside Pages | Outline listing **every mark in the document** — ours (the
PageRef descriptors) and foreign (another tool's annotations, read live so pending deletions /
moves are respected) — as "p. N · type · snippet" rows; clicking a row jumps to the mark and
selects it (the M47 click-to-jump pattern). The tab **exists only while the document has marks**
(owner rule: inapplicable chrome is invisible, not greyed out) — its mounting lives in
``MainWindow._mount_sidebar``, and ``populate()`` is re-run after every edit so the list follows
add / remove / undo live.

Foreign annotations come through a ``foreign_provider`` callable (in practice
``AnnotationOverlay.foreign_annotations``) rather than a viewer import, so this panel — like the
outline panel — depends only on the model and the provider seam.
"""

from __future__ import annotations

import os

import pymupdf as fitz
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from model.content_marks import ImageStamp, Stamp
from model.foreign_annots import ForeignDeletion, ForeignMove
from model.form_fields import NewField
from model.page_edits import TextBox
from model.virtual_document import VirtualDocument
from organize.thumbnail_panel import _SIDEBAR_W  # one default width for all sidebar tabs

_ROLE = Qt.ItemDataRole.UserRole  # row payload: (page_index, mark, bounds)
_SNIPPET_CHARS = 48


def _clip(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= _SNIPPET_CHARS else text[: _SNIPPET_CHARS - 1] + "…"


def _union(rects) -> tuple:
    xs0, ys0, xs1, ys1 = zip(*((r[0], r[1], r[2], r[3]) for r in rects))
    return (min(xs0), min(ys0), max(xs1), max(ys1))


class AnnotationsPanel(QListWidget):
    """Flat list of every mark in the document, in page order; click to jump + select."""

    markActivated = Signal(int, object, tuple)  # (page_index, mark | ForeignAnnot, bounds)

    def __init__(self, vdoc: VirtualDocument, foreign_provider, parent=None) -> None:
        super().__init__(parent)
        self._vdoc = vdoc
        self._foreign = foreign_provider
        self.setUniformItemSizes(True)
        self.itemClicked.connect(self._on_item_clicked)
        self.populate()

    def sizeHint(self) -> QSize:
        return QSize(_SIDEBAR_W, super().sizeHint().height())

    # ---- build ------------------------------------------------------------------

    def populate(self) -> None:
        """(Re)build the rows from the live document. Called after every edit
        (``MainWindow._on_doc_changed``), so the list follows add / remove / undo."""
        self.clear()
        for page_index in range(self._vdoc.page_count):
            for mark in self._vdoc.page_annotations(page_index):
                if isinstance(mark, (ForeignDeletion, ForeignMove)):
                    continue  # bookkeeping descriptors, not visible marks
                self._add_row(page_index, mark, self._describe(page_index, mark),
                              self._bounds(mark))
            for annot in self._foreign(page_index):
                label = annot.kind_name.lower()
                snippet = _clip(annot.contents)
                self._add_row(page_index, annot,
                              f"{label} · {snippet}" if snippet else label, annot.rect)

    def _add_row(self, page_index: int, mark, label: str, bounds: tuple) -> None:
        item = QListWidgetItem(f"p. {page_index + 1} · {label}")
        item.setData(_ROLE, (page_index, mark, tuple(bounds)))
        item.setToolTip(f"Page {page_index + 1}")
        self.addItem(item)

    def _describe(self, page_index: int, mark) -> str:
        from viewer.annotations import mark_noun  # lazy: one vocabulary, no import-time cycle

        noun = mark_noun(mark)
        snippet = ""
        if isinstance(mark, TextBox):
            snippet = _clip(mark.text)
        elif isinstance(mark, Stamp):
            snippet = _clip(mark.text)
        elif isinstance(mark, ImageStamp):
            snippet = _clip(os.path.basename(mark.image_path))
        elif isinstance(mark, NewField):
            snippet = _clip(mark.name)
        elif hasattr(mark, "rects"):
            snippet = _clip(self._covered_text(page_index, mark.rects))
        return f"{noun} · {snippet}" if snippet else noun

    def _covered_text(self, page_index: int, rects) -> str:
        """The page text under a text-anchored mark's bars — what a highlight row should read as."""
        ref = self._vdoc.ordered[page_index]
        page = self._vdoc.sources[ref.source_id][ref.source_page_index]
        parts = [" ".join(page.get_textbox(fitz.Rect(r)).split()) for r in rects]
        return " ".join(p for p in parts if p)

    @staticmethod
    def _bounds(mark) -> tuple:
        if hasattr(mark, "rects"):
            return _union(mark.rects)
        if hasattr(mark, "bounding_rect"):
            return mark.bounding_rect()
        return tuple(mark.rect)

    # ---- activation -------------------------------------------------------------

    def _on_item_clicked(self, item) -> None:
        page_index, mark, bounds = item.data(_ROLE)
        self.markActivated.emit(page_index, mark, bounds)
