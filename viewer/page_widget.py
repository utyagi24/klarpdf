"""Editable page counter, two-way bound to a :class:`~viewer.pdf_view.PdfView` (M91.3).

Reads ``10  of 320`` on the reading bar: the number is a field you can type into, the total is a
label. The binding is the one :class:`~viewer.zoom_widget.ZoomWidget` already uses, for the same
reason — **the view is the single source of truth.** Typing a number calls ``view.goto_page`` and the
view's ``currentPageChanged`` drives the displayed value, so the wheel, PgUp/PgDn, Home/End, the
sidebar, the outline and Ctrl+G all keep the indicator in sync without any of them knowing it exists.

**Why it exists at all.** ``sidebar_visible`` defaults to ``False``, so out of the box the app gave a
reader *no* position indication whatsoever — the sidebar's current-thumbnail highlight was the only
one there was, and on a 320-page document that is the difference between reading and guessing.

**The total is pushed, not signalled.** There is no ``pageCountChanged``: insert / delete / undo
change the count without moving the current page, so ``MainWindow._on_doc_changed`` calls
:meth:`show_count` on the way through. Coupling the total to ``currentPageChanged`` would have left
"of 320" on screen after deleting ten pages, which is worse than no total.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QWidget


class PageWidget(QWidget):
    """``[ 10 ] of 320`` — the field jumps, the label counts."""

    def __init__(self, view, parent=None) -> None:
        super().__init__(parent)
        self._view = view
        # **Fixed, or it eats the bar.** A plain ``QWidget`` handed to ``QToolBar.addWidget`` gets a
        # Preferred policy, and the toolbar's layout hands every pixel of slack to whatever will
        # take it: measured, this widget stretched to 627 px in an 1100 px window and pushed the
        # entire zoom cluster off the right-hand end. ``ZoomWidget`` never showed the problem
        # because it sets a fixed width on itself.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        self.field = QLineEdit()
        self.field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # ClickFocus, like the zoom combo: a wheel scroll or a Tab pass over the reading bar must
        # never take the keyboard away from the page — the arrow keys are navigation (M78.2).
        self.field.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.field.setFixedWidth(44)
        self.field.setToolTip("Current page — type a number to jump")
        # A validator keeps letters out as they are typed; `_apply_text` still handles an empty or
        # out-of-range field, because a validator cannot reject "0" without also rejecting the "0"
        # in "10" mid-keystroke.
        self.field.setValidator(QIntValidator(0, 999_999, self))

        self.total = QLabel()
        self.total.setToolTip("Pages in this document")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.field)
        layout.addWidget(self.total)

        self.field.editingFinished.connect(self._apply_text)
        view.currentPageChanged.connect(self.show_page)
        self.show_count()
        self.show_page(view.current_page)

    # ---- the view drives the display --------------------------------------------

    def show_page(self, index: int) -> None:
        """Reflect the view's current page (0-based) in the field as a 1-based number."""
        text = str(index + 1)
        if self.field.text() != text:
            self.field.setText(text)

    def show_count(self) -> None:
        """Re-read the document's page count. Called by ``_on_doc_changed`` — see the module note."""
        self.total.setText(f"of {self._view._vdoc.page_count}")

    # ---- the display drives the view --------------------------------------------

    def _apply_text(self) -> None:
        """Jump to the typed page, clamped; garbage restores the live value.

        ``editingFinished`` fires on Enter **and** on focus-out, which is what makes clicking away
        from a half-typed number harmless rather than a jump to page 1.
        """
        raw = self.field.text().strip()
        try:
            page = int(raw)
        except ValueError:
            self.show_page(self._view.current_page)   # ignore garbage, restore the live value
            return
        count = self._view._vdoc.page_count
        self._view.goto_page(max(1, min(page, count)) - 1)
        # Echo the clamped result: typing 900 into a 320-page document must not leave 900 on screen.
        # goto_page scrolls, which emits currentPageChanged and updates the field — except when the
        # clamped page is the one already current, where nothing moves and nothing is emitted.
        self.show_page(self._view.current_page)
