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


class PageField(QLineEdit):
    """The number itself — a `QLineEdit` that declines the keys it has no use for (M91.4)."""

    def keyPressEvent(self, event) -> None:
        # **`Space` belongs to the document.** This field is integer-validated, so a space can never
        # be valid input here — but `QLineEdit` accepts the key anyway and the validator quietly
        # drops the character, so the press did *nothing at all*. A reader who had clicked the field
        # once (to read it, or to type a page) then found the most common reading gesture in the app
        # dead for the rest of the session, with no way to tell why. Leaving it unaccepted carries
        # it to `MainWindow.keyPressEvent`, which pages the view — the same rule the sidebar panels
        # follow, and the reason `PgUp`/`PgDn` already worked from here while `Space` did not.
        if event.key() == Qt.Key.Key_Space:
            event.ignore()
            return
        super().keyPressEvent(event)


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

        self.field = PageField()
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
        self.field.returnPressed.connect(self._commit)
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

    def _commit(self) -> None:
        """Enter: jump, then **hand the keyboard back to the page**.

        A page field is a one-shot instruction, not a place to leave the focus. Keeping it meant a
        reader who typed a page number was left with the caret in a 44 px box while looking at the
        document — every reading key from then on either did nothing or belonged to the field. This
        is the same reasoning as :meth:`PageField.keyPressEvent`, one step earlier: the reader's
        attention went back to the page, so the keyboard should follow it.
        """
        self._apply_text()
        self._view.setFocus()

    def _apply_text(self) -> None:
        """Jump to the typed page, clamped; garbage restores the live value.

        **Only when the reader actually typed something** (M91.4). ``editingFinished`` fires on
        Enter *and* on every focus-out with valid contents — Qt does not require the text to have
        changed — so merely clicking the field and clicking away re-applied whatever number it was
        showing. That is not the no-op it looks like: ``goto_page`` re-seats the view on that page's
        **top**, so a reader who clicked the field, scrolled on with the wheel and then clicked back
        onto the page was yanked to the top of the page they started from. Owner report,
        2026-07-30: "the first page flickers but stays at 1" — the flicker was this jump, and every
        `Space` in between was being eaten by the field (see :class:`PageField`).

        ``isModified`` is the exact question to ask: Qt sets it when the *user* edits and clears it
        on ``setText``, which is how :meth:`show_page` marks the value as the view's rather than
        theirs. The zoom box next door has the same ``editingFinished`` wiring and is **not** wrong,
        which is worth recording so this is not "fixed" there too: re-applying a stale zoom is a
        genuine no-op because ``set_zoom`` returns early on an unchanged value, while ``goto_page``
        has no such early-out and must not — jumping to the page you are already on is exactly what
        a reader means by clicking its thumbnail (M91.4).
        """
        if not self.field.isModified():
            return
        raw = self.field.text().strip()
        try:
            page = int(raw)
        except ValueError:
            self.show_page(self._view.current_page)   # ignore garbage, restore the live value
            return
        count = self._view._vdoc.page_count
        self.field.setModified(False)   # applied — a following focus-out must not repeat the jump
        self._view.goto_page(max(1, min(page, count)) - 1)
        # Echo the clamped result: typing 900 into a 320-page document must not leave 900 on screen.
        # goto_page scrolls, which emits currentPageChanged and updates the field — except when the
        # clamped page is the one already current, where nothing moves and nothing is emitted.
        self.show_page(self._view.current_page)
